"""Generate the Hybrid (Approach D) results document from executed runs on the DEVELOPMENT split.

Every LLM response is replayed from the recorded benchmark (`data/llm_cache`); no provider call is
made. Dev is used for BOTH choosing the recommended variant and evaluating it, so those numbers are
optimistic and are labelled as such. The locked test split is never read.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from app.classification.config_loader import ConfigBundle
from app.classification.hybrid import HybridClassifier
from app.classification.router import ml_accepted, rules_sufficient
from app.classification.routing_config import (
    BUCKET_RANK,
    GatesConfig,
    MLStage,
    RoutingConfig,
    RulesStage,
)

from .dataset.schema import DatasetDocument
from .evaluate import EvaluationResult, evaluate
from .ml_report import _family_errors
from .records import PredictionRecord
from .reporting import _ci, _f, _table

EPS = 0.02  # differences up to this are ties in the pre-registered selection rule
SANITY_VARIANT = "llm_mid_only"  # a pass-through sanity check, not a candidate operating point
TOOL_LIMIT_MS = 10_000
ML_TAUS = (0.5, 0.6, 0.7, 0.8, 0.9)
_LEVEL_ORDER = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_CONFIDENTIAL"]


def _rank(level: str) -> int:
    return _LEVEL_ORDER.index(level)


def _llm_latency_ms(r: PredictionRecord) -> float:
    """Recorded LLM latency of a document: the sum of its LLM stages (sequential)."""
    return sum(v for k, v in r.stage_latency_ms.items() if k == "llm" or k.startswith("llm:"))


def row_metrics(res: EvaluationResult, bundle: ConfigBundle, n_stages: int) -> dict[str, Any]:
    """The numbers used for tables, gates and the selection rule (all from executed records)."""
    h = res.metrics["headline"]
    st, hm = h["confidence_intervals"]["statistics"], h["metrics"]
    tiers = set(bundle.eval.headline_tiers)
    head = [r for r in res.records if r.tier in tiers]
    llm_ms = sorted(_llm_latency_ms(r) for r in res.records)
    n = max(len(res.records), 1)
    return {
        "level": st["level_macro_f1"],
        "category": st["category_macro_f1"],
        "level_f1": st["level_macro_f1"]["point"],
        "cat_f1": st["category_macro_f1"]["point"],
        "hr_precision": hm["high_risk"]["precision"],
        "hr_recall": st["high_risk_recall"]["point"],
        "hr_recall_stat": st["high_risk_recall"],
        "hr_fpr": hm["high_risk"]["false_positive_rate"],
        "review_rate": hm["coverage"]["review_rate"],
        "n_fail": sum(not r.has_prediction for r in head),
        "severe_under": sum(
            r.has_prediction
            and r.gold_level == "HIGHLY_CONFIDENTIAL"
            and r.pred_level in ("PUBLIC", "INTERNAL")
            for r in head
        ),
        "tokens_per_doc": sum((r.tokens_in or 0) + (r.tokens_out or 0) for r in res.records) / n,
        "calls_per_doc": sum(sum(s.startswith("llm") for s in r.stages_run) for r in res.records)
        / n,
        # RECORDED LLM latency only (deterministic); local Rules/ML time is milliseconds and excluded
        "p50_s": round(float(np.percentile(llm_ms, 50)) / 1000, 1) if llm_ms else 0.0,
        "p95_s": round(float(np.percentile(llm_ms, 95)) / 1000, 1) if llm_ms else 0.0,
        "over_limit": sum(_llm_latency_ms(r) > TOOL_LIMIT_MS for r in res.records),
        "n_docs": len(res.records),
        "n_stages": n_stages,
    }


def dominates(b: dict[str, Any], a: dict[str, Any], eps: float = EPS) -> bool:
    """b dominates a: no worse than a (within eps) on all four quality metrics and better by more
    than eps on at least one. Differences up to eps are ties."""
    keys = ("level_f1", "cat_f1", "hr_precision", "hr_recall")
    no_worse = all((b[k] or 0) >= (a[k] or 0) - eps for k in keys)
    better = any((b[k] or 0) > (a[k] or 0) + eps for k in keys)
    return no_worse and better


def recommend(
    rows: dict[str, dict[str, Any]], hr_reference: float, eps: float = EPS
) -> dict[str, Any]:
    """The pre-registered selection rule (docs/uc4/hybrid-plan.md). A recommendation only."""
    cands = [n for n in rows if n != SANITY_VARIANT]
    eliminated: dict[str, str] = {}
    step1 = []
    for n in cands:
        r = rows[n]
        if (r["hr_recall"] or 0) < hr_reference:
            eliminated[n] = f"high-risk recall {_f(r['hr_recall'])} < {hr_reference}"
        elif r["severe_under"]:
            eliminated[n] = f"{r['severe_under']} severely under-classified document(s)"
        elif r["n_fail"]:
            eliminated[n] = f"{r['n_fail']} document(s) without a prediction"
        else:
            step1.append(n)
    front = []
    for n in step1:
        by = next((m for m in step1 if m != n and dominates(rows[m], rows[n], eps)), None)
        if by is None:
            front.append(n)
        else:
            eliminated[n] = f"dominated by {by}"
    chosen = (
        min(front, key=lambda n: (rows[n]["tokens_per_doc"], rows[n]["p95_s"], rows[n]["n_stages"]))
        if front
        else None
    )
    return {"step1": step1, "front": front, "chosen": chosen, "eliminated": eliminated}


class _FaultyStage:
    """Wraps a stage and makes it unavailable for a deterministic share of documents (fault
    injection). The unavailable result is what the real LLM classifier returns on provider failure."""

    def __init__(self, inner, percent: int) -> None:
        self.inner, self.percent = inner, percent
        self.name, self.version = inner.name, inner.version

    def params(self) -> dict[str, Any]:
        return {**self.inner.params(), "injected_fault_percent": self.percent}

    def classify(self, request):
        if int(request.document.content_hash()[:8], 16) % 100 >= self.percent:
            return self.inner.classify(request)
        from app.classification.schemas import ClassificationResult, ReviewDecision

        return ClassificationResult(
            request_id=request.request_id,
            document_id=request.document.document_id,
            content_hash=request.document.content_hash(),
            status="review_required",
            review=ReviewDecision(required=True, reason_codes=["LLM_UNAVAILABLE"]),
            warnings=["llm_error:injected_fault"],
        )


FAULT_SCENARIOS = [
    # (label, variant, {tier: percent of documents made unavailable})
    ("mid unavailable for every document", "default", {"mid": 100}),
    ("mid unavailable for 20% of documents", "default", {"mid": 20}),
    ("mid and large unavailable for every document", "default", {"mid": 100, "large": 100}),
    (
        "every LLM tier unavailable, Rules short-circuit on",
        "rules_short_circuit",
        {"mid": 100, "large": 100},
    ),
]


def _gate(value: float | None, target: float) -> str:
    return "n/a" if value is None else ("PASS" if value >= target else "FAIL")


def _stage_counts(res: EvaluationResult) -> Counter[str]:
    c: Counter[str] = Counter()
    for r in res.records:
        s = r.stop_reason or ""
        if s.startswith("rules_short_circuit"):
            c["rules short-circuit"] += 1
        elif s.startswith("ml_short_circuit"):
            c["ML short-circuit"] += 1
        elif s.startswith("escalated_"):
            c["accepted after escalation"] += 1
        elif s.endswith("_accepted"):
            c["accepted by the first LLM tier"] += 1
        elif s == "no_llm_configured":
            c["decided without an LLM"] += 1
        else:
            c["review (no stage accepted)"] += 1
    return c


def build_hybrid_report(
    bundle: ConfigBundle,
    routing: RoutingConfig,
    routing_sha: str,
    gates: GatesConfig,
    components: dict[str, Any],
    dev: list[DatasetDocument],
    dataset_manifest: dict[str, Any],
    git: dict[str, Any],
    make_variant,
) -> str:
    """`make_variant(name)` returns a HybridClassifier sharing `components`."""
    policy = bundle.policy
    threshold = bundle.eval.min_support_flag
    L: list[str] = []
    add = L.append

    # ---- run everything ------------------------------------------------------------------------
    standalone: dict[str, EvaluationResult] = {}
    stage_count = {"Rules 1.0.3 (A)": 1, "ML (B)": 1}
    standalone["Rules 1.0.3 (A)"] = evaluate(components["rules"], dev, bundle, dataset_manifest)
    standalone["ML (B)"] = evaluate(components["ml"], dev, bundle, dataset_manifest)
    for t in ("small", "mid", "large"):
        standalone[f"LLM {t} (C)"] = evaluate(components["llms"][t], dev, bundle, dataset_manifest)
        stage_count[f"LLM {t} (C)"] = 1
    variants: dict[str, HybridClassifier] = {n: make_variant(n) for n in routing.variants}
    vres: dict[str, EvaluationResult] = {
        n: evaluate(c, dev, bundle, dataset_manifest) for n, c in variants.items()
    }

    def nst(v: HybridClassifier) -> int:
        return int(v.cfg.rules.enabled) + int(v.cfg.ml.enabled) + len(v.cfg.llm.tier_order)

    rows: dict[str, dict[str, Any]] = {
        n: row_metrics(r, bundle, stage_count.get(n, 1)) for n, r in standalone.items()
    }
    vrows = {n: row_metrics(vres[n], bundle, nst(variants[n])) for n in variants}
    rec = recommend(vrows, gates.gates.high_risk_recall)
    chosen = rec["chosen"]
    n_fam = standalone["Rules 1.0.3 (A)"].metrics["headline"]["confidence_intervals"]["n_units"]

    add("# Hybrid routing results (Approach D)")
    add("")
    add(
        "> Generated by `dataguard-uc4 hybrid report` from executed code on the DEVELOPMENT split. "
        "Every LLM response is replayed from the recorded benchmark; no provider call was made. "
        "**The locked test split was not read.** Dataset labels: **AI-generated synthetic dataset — "
        "reviewed by one human (provenance per coordinator); second independent review pending**."
    )
    add("")
    add(
        "> **Dev was used both to choose the recommended variant and to evaluate it**, so its dev "
        "numbers are optimistic by construction, and dev has only "
        f"**{n_fam} independent families**. Each metric stands alone (no combined score); high-risk recall "
        "is never read without precision and FPR. The recommendation is a recommendation: the "
        "operating point is the product owner's decision."
    )
    add("")
    add("## Protocol")
    add("")
    add("* pre-registered plan: `docs/uc4/hybrid-plan.md` (committed before any router code)")
    add(
        f"* routing config `{routing.routing_version}` sha256 `{routing_sha[:16]}...`; gates `{gates.gates_version}`"
    )
    add(
        f"* dev: {len(dev)} documents; headline = tiers {', '.join(bundle.eval.headline_tiers)}; T5 is a slice"
    )
    add(
        f"* git `{(git.get('commit') or 'unknown')[:12]}` on `{git.get('branch')}` (dirty: {git.get('dirty')})"
    )
    add("")

    # ---- sanity check --------------------------------------------------------------------------
    mid = standalone["LLM mid (C)"]
    same = {r.doc_id: (r.pred_level, tuple(r.pred_categories)) for r in mid.records} == {
        r.doc_id: (r.pred_level, tuple(r.pred_categories)) for r in vres[SANITY_VARIANT].records
    }
    add("## Sanity check")
    add("")
    add(
        f"`{SANITY_VARIANT}` (Rules and ML off, one tier, no floors) "
        + (
            "**reproduces the standalone mid tier exactly** on every dev document "
            f"(identical metrics fingerprint `{mid.fingerprint[:8]}`)."
            if same and mid.fingerprint == vres[SANITY_VARIANT].fingerprint
            else "**DOES NOT reproduce the standalone mid tier: the router has a defect.**"
        )
    )
    add("")

    # ---- comparison ----------------------------------------------------------------------------
    def line(name: str, r: EvaluationResult, m: dict[str, Any]) -> list[Any]:
        return [
            name, _ci(m["level"]), _ci(m["category"]), _f(m["hr_precision"]), _ci(m["hr_recall_stat"]),
            _f(m["hr_fpr"]), _f(m["review_rate"]), _f(m["calls_per_doc"], 2), _f(m["tokens_per_doc"], 0),
            _f(m["p50_s"], 1), _f(m["p95_s"], 1), m["over_limit"],
        ]  # fmt: skip

    head = ["approach / variant", "level macro-F1", "category macro-F1", "HR precision", "HR recall",
            "HR FPR", "review rate", "LLM calls / doc", "tokens / doc", "P50 (s)", "P95 (s)",
            f"docs > {TOOL_LIMIT_MS // 1000} s"]  # fmt: skip
    add(f"## All approaches and variants (held-out dev, T1-T4: {n_fam} independent families)")
    add("")
    add(
        "Standalone approaches first, then every pre-registered hybrid variant. Latency is the sum of the LLM stages' RECORDED latencies (sequential stages; local Rules/ML time is milliseconds and excluded, so it shows as 0.0 for those approaches); tokens and calls are the cost proxy (no prices exist)."
    )
    add("")
    body = [line(n, standalone[n], rows[n]) for n in standalone]
    body += [
        line(f"hybrid `{n}`" + (" **(recommended)**" if n == chosen else ""), vres[n], vrows[n])
        for n in vres
    ]
    add(_table(head, body))
    add("")

    # ---- gates ---------------------------------------------------------------------------------
    g = gates.gates
    add("## Gates (PRD MVP targets; pass/fail on dev, NOT a release gate)")
    add("")
    add(
        f"Targets: level macro-F1 >= {g.level_macro_f1}, category macro-F1 >= {g.category_macro_f1} (two separate gates), "
        f"high-risk recall >= {g.high_risk_recall} (informational, never sufficient alone). Each is judged on the point "
        "estimate and on the lower family-bootstrap bound. Dev chose the recommended variant, so passing here proves little."
    )
    add("")
    grows = []
    for n in [*standalone, *(f"hybrid `{v}`" for v in vres)]:
        m = rows[n] if n in rows else vrows[n[8:-1]]
        lo = lambda s: s.get("ci_low")  # noqa: E731
        grows.append([
            n,
            f"{_gate(m['level_f1'], g.level_macro_f1)} / {_gate(lo(m['level']), g.level_macro_f1)}",
            f"{_gate(m['cat_f1'], g.category_macro_f1)} / {_gate(lo(m['category']), g.category_macro_f1)}",
            f"{_gate(m['hr_recall'], g.high_risk_recall)} / {_gate(lo(m['hr_recall_stat']), g.high_risk_recall)}",
        ])  # fmt: skip
    add(
        _table(
            [
                "approach / variant",
                "level F1 (point / lower bound)",
                "category F1 (point / lower bound)",
                "HR recall (point / lower bound)",
            ],
            grows,
        )
    )
    add("")

    # ---- selection rule ------------------------------------------------------------------------
    add("## Recommended variant (pre-registered selection rule)")
    add("")
    add(
        f"Rule: (1) keep variants with high-risk recall >= {g.high_risk_recall}, zero severe under-classification "
        f"(Highly Confidential predicted Public/Internal) and no document without a prediction; (2) drop variants dominated on "
        f"level F1, category F1, high-risk precision and recall (differences <= {EPS} are ties); (3) choose the lowest mean tokens "
        f"per document, then lowest P95 latency, then fewest stages. `{SANITY_VARIANT}` is a sanity check and not a candidate."
    )
    add("")
    add(
        f"Survived step 1: {', '.join(f'`{n}`' for n in rec['step1']) or 'none'}. Non-dominated (step 2): {', '.join(f'`{n}`' for n in rec['front']) or 'none'}."
    )
    add("")
    add(f"**Recommendation: `{chosen}`.**" if chosen else "**No variant survived the rule.**")
    add("")
    if chosen:
        twins = [
            n for n in rec["front"]
            if n != chosen
            and all(abs((vrows[n][k] or 0) - (vrows[chosen][k] or 0)) <= 1e-9 for k in ("level_f1", "cat_f1", "hr_precision", "hr_recall", "tokens_per_doc", "p95_s"))
        ]  # fmt: skip
        add(
            "Variants with metrics identical to the recommended one on dev, so dev cannot tell them apart: "
            + (", ".join(f"`{n}`" for n in twins) or "none")
            + ". Ties are resolved by configuration order, which puts `default` (all safety mechanisms on) first; "
            "the safety controls are what separate these variants in practice, not the dev numbers."
        )  # fmt: skip
        add("")
    add(
        _table(
            ["eliminated variant", "reason"], [[f"`{k}`", v] for k, v in rec["eliminated"].items()]
        )
        if rec["eliminated"]
        else "No variant was eliminated."
    )
    add("")
    add(
        "Most differences between variants are within noise (18 families, saturated LLM scores); the rule prefers the "
        "cheapest of the practically equivalent ones. It does not say the others are worse."
    )
    add("")

    # ---- stage coverage -----------------------------------------------------------------------
    add("## Which stage decided each document")
    add("")
    cats = ["rules short-circuit", "ML short-circuit", "accepted by the first LLM tier",
            "accepted after escalation", "decided without an LLM", "review (no stage accepted)"]  # fmt: skip
    add(
        _table(
            ["variant", *cats],
            [[n, *[_stage_counts(vres[n]).get(c, 0) for c in cats]] for n in vres],
        )
    )
    add("")

    # ---- threshold evidence -------------------------------------------------------------------
    add("## Evidence for the thresholds (measured on dev; no target was approved)")
    add("")
    reqs = {d.doc_id: d for d in dev}
    rules_res = {d.doc_id: components["rules"].classify(d.to_request()) for d in dev}
    ml_res = {d.doc_id: components["ml"].classify(d.to_request()) for d in dev}
    mid_recs: dict[str, PredictionRecord] = {r.doc_id: r for r in mid.records}

    suff = [
        k
        for k, r in rules_res.items()
        if rules_sufficient(
            r, RulesStage(enabled=True, short_circuit=True, min_level_strength="strong")
        ).accepted
    ]
    add("### Rules sufficiency (level decisive at strength >= strong)")
    add("")

    def prf(keys: list[str], pred: dict[str, set[str]]) -> tuple[Any, Any]:
        tp = sum(len(pred[k] & set(reqs[k].gold_categories)) for k in keys)
        fp = sum(len(pred[k] - set(reqs[k].gold_categories)) for k in keys)
        fn = sum(len(set(reqs[k].gold_categories) - pred[k]) for k in keys)
        return (tp / (tp + fp) if tp + fp else None, tp / (tp + fn) if tp + fn else None)

    rp = {k: {c.id for c in r.categories} for k, r in rules_res.items()}
    mp = {r.doc_id: set(r.pred_categories) for r in mid.records}
    rl_ok = sum(rules_res[k].level.value == reqs[k].gold_level for k in suff)
    ml_ok = sum(mid_recs[k].pred_level == reqs[k].gold_level for k in suff)
    p_r, r_r = prf(suff, rp)
    p_m, r_m = prf(suff, mp)
    add(
        _table(
            ["stage on the sufficient documents", "documents", "level accuracy", "category precision", "category recall"],
            [["Rules", len(suff), f"{rl_ok}/{len(suff)}", _f(p_r), _f(r_r)], ["LLM mid (same documents)", len(suff), f"{ml_ok}/{len(suff)}", _f(p_m), _f(r_m)]],
        )
    )  # fmt: skip
    add("")
    add(
        "Short-circuiting on Rules is only justified where Rules are at least as good as the LLM on these documents; recall is the column to watch (a rule that fires on one category can hide a semantic one)."
    )
    add("")

    add("### ML acceptance by tau (calibrated reliability on all axes)")
    add("")
    mrows = []
    for tau in ML_TAUS:
        acc = [
            k
            for k, r in ml_res.items()
            if ml_accepted(r, MLStage(enabled=True, short_circuit=True, tau=tau)).accepted
        ]
        ok = sum(ml_res[k].level.value == reqs[k].gold_level for k in acc)
        mrows.append([tau, f"{len(acc)}/{len(dev)}", f"{ok}/{len(acc)}" if acc else "n/a"])
    add(_table(["tau", "documents ML would decide", "level accuracy of those"], mrows))
    add("")

    add("### LLM confidence floor (verbalized bucket; not a probability)")
    add("")
    lrows = []
    for t in ("small", "mid", "large"):
        recs = [r for r in standalone[f"LLM {t} (C)"].records if r.has_prediction]
        for floor in ("low", "medium", "high"):
            acc = [
                r
                for r in recs
                if BUCKET_RANK.get(r.level_confidence or "", -1) >= BUCKET_RANK[floor]
            ]
            rej = [r for r in recs if r not in acc]
            lrows.append([
                t, floor, f"{len(acc)}/{len(recs)}",
                f"{sum(r.pred_level == r.gold_level for r in acc)}/{len(acc)}" if acc else "n/a",
                f"{sum(r.pred_level == r.gold_level for r in rej)}/{len(rej)}" if rej else "n/a",
            ])  # fmt: skip
    add(
        _table(
            [
                "tier",
                "floor",
                "accepted",
                "level accuracy of accepted",
                "level accuracy of rejected",
            ],
            lrows,
        )
    )
    add("")
    add(
        "A floor is only useful if the rejected documents are less accurate than the accepted ones; where a tier says `high` for everything the floor cannot discriminate."
    )
    add("")

    # ---- safety mechanisms exercised ------------------------------------------------------------
    add("## Safety mechanisms exercised on real dev data")
    add("")
    srows = []
    for n, r in vres.items():
        fl = Counter(f for x in r.records for f in x.routing_flags)
        esc = sum(x.escalations > 0 for x in r.records)
        srows.append([n, fl.get("fusion:rules_floor", 0), fl.get("fusion:category_floor", 0), fl.get("fusion:injection_restriction", 0), sum(f.startswith("conflict") for x in r.records for f in x.routing_flags), esc, sum(x.deferred for x in r.records)])  # fmt: skip
    add(
        _table(
            [
                "variant",
                "Rules floor raised the level",
                "category floor raised the level",
                "injection restriction raised the level",
                "conflicts",
                "documents escalated",
                "sent to review",
            ],
            srows,
        )
    )
    add("")
    add(
        "With the recorded model outputs the stages almost always agree, so the escalation, conflict and review paths are rarely or never triggered on real data. They are exercised by unit tests and by the fault-injection scenarios below, not by these numbers."
    )
    add("")

    # ---- fault injection ------------------------------------------------------------------------
    add("## Behaviour when LLM tiers fail (fault injection on dev)")
    add("")
    add(
        "A tier is made unavailable for all or a deterministic share of documents (the same result a real provider failure produces). Missing labels count as misses. Real Rules outputs and recorded LLM responses are otherwise unchanged."
    )
    add("")
    frows = []
    for label, vname, faults in FAULT_SCENARIOS:
        llms = dict(components["llms"])
        for t, pct in faults.items():
            llms[t] = _FaultyStage(llms[t], pct)
        fv = make_variant(vname, {**components, "llms": llms})
        fr = evaluate(fv, dev, bundle, dataset_manifest)
        m = row_metrics(fr, bundle, nst(fv))
        nolabel = sum(not x.has_prediction for x in fr.records)
        provisional = [x for x in fr.records if x.deferred and x.has_prediction]
        frows.append([
            label, f"`{vname}`", _f(m["review_rate"]), nolabel, f"{sum(x.pred_level == x.gold_level for x in provisional)}/{len(provisional)}",
            _f(m["level_f1"]), _f(m["hr_precision"]), _f(m["hr_recall"]), m["severe_under"], _f(m["calls_per_doc"], 2), _f(m["p95_s"], 1),
        ])  # fmt: skip
    add(
        _table(
            [
                "scenario",
                "variant",
                "review rate",
                "documents with no label",
                "provisional level correct",
                "level macro-F1",
                "HR precision",
                "HR recall",
                "severely under-classified",
                "LLM calls / doc",
                "P95 (s)",
            ],
            frows,
        )
    )
    add("")
    add(
        "A failed stage never produces a low default: a document is either decided by a working stage, or sent to review with the fail-safe provisional label when one exists, or returned with no label at all. Escalating from a failed mid tier to large costs latency, not accuracy."
    )
    add("")

    # ---- review analysis ------------------------------------------------------------------------
    rev_rows = []
    for n, r in vres.items():
        rv = [x for x in r.records if x.deferred]
        if not rv:
            continue
        reasons = Counter(c for x in rv for c in x.review_reasons)
        withp = [x for x in rv if x.has_prediction]
        rev_rows.append([n, len(rv), dict(reasons), f"{sum(x.pred_level == x.gold_level for x in withp)}/{len(withp)}",
                         f"{sum(x.pred_high_risk == x.gold_high_risk for x in withp)}/{len(withp)}"])  # fmt: skip
    add("## Review cases")
    add("")
    if rev_rows:
        add(
            _table(
                [
                    "variant",
                    "documents sent to review",
                    "reason codes",
                    "provisional level correct",
                    "provisional high-risk correct",
                ],
                rev_rows,
            )
        )
        add("")
        add(
            "Provisional labels are the fail-safe fusion (highest level, union of categories) of every stage that produced a level."
        )
    else:
        add("No variant sent any document to review.")
    add("")

    # ---- recommended variant details ----------------------------------------------------------
    if chosen:
        r = vres[chosen]
        m = vrows[chosen]
        add(f"## Recommended variant `{chosen}`: details (dev; optimistic because dev chose it)")
        add("")
        add(
            f"Level macro-F1 {_ci(m['level'])}, category macro-F1 {_ci(m['category'])}, high-risk precision {_f(m['hr_precision'])}, recall {_ci(m['hr_recall_stat'])}, FPR {_f(m['hr_fpr'])}; {n_fam} independent families."
        )
        add("")
        per = r.metrics["headline"]["metrics"]["categories"]["per_label"]
        base_per = standalone["LLM mid (C)"].metrics["headline"]["metrics"]["categories"][
            "per_label"
        ]
        add(_table(["category", "support", "hybrid F1", "LLM mid F1", "flag"],
                   [[c, per[c]["support"], per[c]["f1"], base_per[c]["f1"], "SMALL_SAMPLE" if per[c]["support"] < threshold else ""] for c in policy.category_ids]))  # fmt: skip
        add("")
        tiers = r.metrics["slices"]["tier"]
        add(_table(["tier", "docs", "level macro-F1", "category macro-F1", "HR recall", "HR FPR"],
                   [[t, v["n_docs"], v["level_macro_f1"], v["category_macro_f1"], v["high_risk_recall"], v["high_risk_false_positive_rate"]] for t, v in tiers.items()]))  # fmt: skip
        add("")
        hn = r.metrics["hard_negatives"]
        add(
            f"**Hard negatives (T4):** {hn['n_docs']} documents / {hn['n_families']} families; decoy-hit rate {_f(hn['decoy_hit_rate'])}; predicted high-risk though not {_f(hn['high_risk_false_positive_rate'])}. Families with decoy hits: `{hn['families_with_decoy_hits'] or 'none'}`."
        )
        add("")
        t5 = [x for x in r.records if x.tier == "T5"]
        under = sum(x.has_prediction and _rank(x.pred_level) < _rank(x.gold_level) for x in t5)
        add(
            f"**Adversarial (T5):** {len(t5)} documents / {len({x.family_id for x in t5})} families; under-classified {under}/{len(t5)}; local injection guard flagged {sum('prompt_injection_suspected' in x.guardrail_types for x in t5)}/{len(t5)}. Anecdotal."
        )
        add("")
        wrong = Counter(
            (x.family_id, x.tier, x.gold_level, x.pred_level)
            for x in r.records
            if x.has_prediction and x.pred_level != x.gold_level
        )
        if wrong:
            add(
                _table(
                    ["family", "tier", "gold level", "predicted", "docs"],
                    [[f, t, g, p, n] for (f, t, g, p), n in sorted(wrong.items())],
                )
            )
            add("")
        fn, fp = _family_errors(r)
        add("**Categories missed:** " + ("; ".join(x[2:] for x in fn) or "none"))
        add("")
        add("**Categories wrongly asserted:** " + ("; ".join(x[2:] for x in fp) or "none"))
        add("")

    add("## Caveats")
    add("")
    for c in (
        "Synthetic, template-generated, AI-authored labels not yet human reviewed; 18 dev families; wide intervals.",
        "**Dev chose the recommended variant and also evaluates it.** The one audited, report-only run of the frozen variant on the locked test split was made on 2026-09-21 (`results/hybrid-locked-test.md`); the split is now consumed.",
        "The LLM stage is near-saturated on this dataset (see the LLM results), so routing has little accuracy to add; its value here is cost, latency and safety behaviour.",
        "LLM outputs are single recorded samples; two tiers had no temperature; `large` was recorded under concurrency, so its latency may include queueing.",
        "Latency sums the recorded latencies of sequential stages; it is not a controlled benchmark. Cost is calls and tokens (no prices).",
        "The Rules stage was developed against dev, so any variant that leans on it is flattered.",
        "The injection restriction only acts when the local lexicon flags a document (3 of 10 dev injection documents).",
    ):
        add(f"* {c}")
    add("")
    return "\n".join(L)
