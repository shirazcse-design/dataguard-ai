"""Generate the LLM classifier status/results document.

Nothing is typed by hand. Two kinds of content:

* **Model-independent, measurable now:** the local injection guard (detection on T5, false positives
  on T1-T4), prompt sizes, truncation, few-shot leakage checks.
* **Model results, only for a tier whose recorded responses are COMPLETE for the dev split.** A tier
  with no (or partial) recorded run is reported as NOT RUN / INCOMPLETE. No number is ever shown for
  a model that was not run.

Development splits only; the locked test split is never read.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from app.classification.config_loader import ConfigBundle
from app.llm.classifier import LLMClassifier
from guardrails.injection import InjectionScanner

from .dataset.schema import DatasetDocument
from .evaluate import EvaluationResult, evaluate
from .records import PredictionRecord
from .reporting import _ci, _f, _table

TIERS = ("small", "mid", "large")
BUCKETS = ("low", "medium", "high")
RUN_COMMAND = (
    "DATAGUARD_FOUNDRY_ENDPOINT=... DATAGUARD_FOUNDRY_API_VERSION=... DATAGUARD_FOUNDRY_API_KEY=... "
    "DATAGUARD_LLM_DEPLOYMENT_SMALL=<your deployment> dataguard-uc4 eval run --classifier llm "
    "--llm-tier small --llm-mode record --split dev"
)


def _guard_stats(scanner: InjectionScanner, docs: list[DatasetDocument]) -> dict[str, Any]:
    t5 = [d for d in docs if d.tier == "T5"]
    benign = [d for d in docs if d.tier != "T5"]
    hits5 = {d.doc_id: [f.rule_id for f in scanner.scan(d.content)] for d in t5}
    hitsb = {d.doc_id: [f.rule_id for f in scanner.scan(d.content)] for d in benign}
    by_type: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for d in t5:
        by_type[d.adversarial_type or "?"][1] += 1
        by_type[d.adversarial_type or "?"][0] += bool(hits5[d.doc_id])
    fp_families = Counter(d.family_id for d in benign if hitsb[d.doc_id])
    rules = Counter(r for v in (*hits5.values(), *hitsb.values()) for r in v)
    return {
        "n_t5": len(t5),
        "t5_flagged": sum(bool(v) for v in hits5.values()),
        "n_benign": len(benign),
        "benign_flagged": sum(bool(v) for v in hitsb.values()),
        "by_type": {k: tuple(v) for k, v in sorted(by_type.items())},
        "fp_families": dict(fp_families),
        "missed_t5_families": sorted({d.family_id for d in t5 if not hits5[d.doc_id]}),
        "rules": dict(rules),
    }


def _rate(a: int, b: int) -> str:
    return "n/a" if not b else f"{a / b:.3f} ({a}/{b})"


def _median(xs: list[int]) -> Any:
    s = sorted(xs)
    return s[len(s) // 2] if s else None


def _tier_status(res: EvaluationResult) -> tuple[str, int]:
    n = len(res.records)
    miss = sum("replay_miss" in (r.failure or "") for r in res.records)
    if miss == 0:
        return "RUN (complete)", miss
    if miss == n:
        return "NOT RUN (no recorded responses)", miss
    return f"INCOMPLETE ({n - miss}/{n} recorded)", miss


def _reliability(records: list[PredictionRecord]) -> list[list[Any]]:
    """Measured reliability of the model's VERBALIZED confidence (it is not a probability)."""
    rows: list[list[Any]] = []
    for b in BUCKETS:
        lv = [r for r in records if r.has_prediction and r.level_confidence == b]
        ok = sum(r.pred_level == r.gold_level for r in lv)
        under = sum(
            r.pred_level is not None
            and r.pred_level != r.gold_level
            and _rank(r.pred_level) < _rank(r.gold_level)
            for r in lv
        )
        pc = [
            (c in r.gold_categories)
            for r in records
            if r.has_prediction and r.category_confidence == b
            for c in r.pred_categories
        ]
        rows.append(
            [
                b,
                len(lv),
                _rate(ok, len(lv)),
                _rate(under, len(lv)),
                len(pc),
                _rate(sum(pc), len(pc)),
            ]
        )
    return rows


_ORDER = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_CONFIDENTIAL"]


def _rank(level: str) -> int:
    return _ORDER.index(level)


def _tier_section(
    tier: str, clf: LLMClassifier, res: EvaluationResult, rules_res, ml_res, policy, threshold
) -> list[str]:
    L: list[str] = []
    add = L.append
    p = clf.params()
    hm = res.metrics["headline"]["metrics"]
    st = res.metrics["headline"]["confidence_intervals"]["statistics"]
    recs = res.records
    n = len(recs)
    add(f"## Tier `{tier}`: model `{p['model_id']}` ({p['client']} adapter)")
    add("")
    add(
        f"Held-out dev, T1-T4: {hm['coverage']['n_docs']} documents from "
        f"**{hm['coverage']['n_groups']} independent families** (family-level bootstrap)."
    )
    add("")
    rows = [
        [
            f"LLM {tier}",
            _ci(st["level_macro_f1"]),
            _ci(st["category_macro_f1"]),
            _f(hm["high_risk"]["precision"]),
            _ci(st["high_risk_recall"]),
            _f(hm["high_risk"]["f1"]),
            _f(hm["high_risk"]["false_positive_rate"]),
        ]
    ]
    for name, other in (("Rules 1.0.3 (frozen)", rules_res), ("ML", ml_res)):
        if other is None:
            continue
        o = other.metrics["headline"]
        os_, om = o["confidence_intervals"]["statistics"], o["metrics"]
        rows.append(
            [
                name,
                _ci(os_["level_macro_f1"]),
                _ci(os_["category_macro_f1"]),
                _f(om["high_risk"]["precision"]),
                _ci(os_["high_risk_recall"]),
                _f(om["high_risk"]["f1"]),
                _f(om["high_risk"]["false_positive_rate"]),
            ]
        )
    add(
        _table(
            [
                "approach",
                "level macro-F1",
                "category macro-F1",
                "HR precision",
                "HR recall",
                "HR F1",
                "HR FPR",
            ],
            rows,
        )
    )
    add("")
    add(
        "High-risk recall is not read alone: see precision and FPR. No operating point has been "
        "chosen. Rules were developed against dev; the LLM prompt was not tuned on dev."
    )
    add("")
    inv = sum("llm_output_invalid" in (r.failure or "") for r in recs)
    prov = sum("llm_error" in (r.failure or "") for r in recs)
    quotes = sum(r.evidence_total for r in recs)
    verified = sum(r.evidence_verified for r in recs)
    with_q = [r for r in recs if r.evidence_total]
    add("### Output validity and evidence")
    add("")
    add(
        _table(
            ["measure", "value"],
            [
                ["documents", n],
                ["schema-valid rate (after the repair retry)", _rate(n - inv - prov, n)],
                ["provider failures (no response)", _rate(prov, n)],
                ["evidence quotes verified against the input", _rate(verified, quotes)],
                ["documents with >= 1 quote", _rate(len(with_q), n)],
                ["documents whose quotes ALL verified", _rate(sum(r.evidence_verified == r.evidence_total for r in with_q), len(with_q))],
                ["abstained (insufficient_information)", _rate(sum(r.abstained for r in recs), n)],
                ["review required", _rate(sum(r.review_required or r.deferred for r in recs), n)],
                ["guardrail: injection suspected", _rate(sum("prompt_injection_suspected" in r.guardrail_types for r in recs), n)],
            ],
        )
    )  # fmt: skip
    add("")
    add("### Reliability of the model's verbalized confidence (measured on dev)")
    add("")
    add(
        "The buckets are the model's own words, never calibrated probabilities. This table is the "
        "only statement about how far to trust them."
    )
    add("")
    add(
        _table(
            ["bucket", "docs (level)", "level accuracy", "under-classified", "predicted categories", "category precision"],
            _reliability(recs),
        )
    )  # fmt: skip
    add("")
    add("### Per category F1 (dev, T1-T4)")
    add("")
    per = hm["categories"]["per_label"]
    cols = ["category", "support", f"LLM {tier} F1", "flag"]
    body = [[c, per[c]["support"], per[c]["f1"], "SMALL_SAMPLE" if per[c]["support"] < threshold else ""] for c in policy.category_ids]  # fmt: skip
    for name, other in (("Rules F1", rules_res), ("ML F1", ml_res)):
        if other is not None:
            cols.append(name)
            op = other.metrics["headline"]["metrics"]["categories"]["per_label"]
            for row, c in zip(body, policy.category_ids, strict=True):
                row.append(op[c]["f1"])
    add(_table(cols, body))
    add("")
    add("### By tier")
    add("")
    tiers = res.metrics["slices"]["tier"]
    add(
        _table(
            ["tier", "docs", "level macro-F1", "category macro-F1", "HR recall", "HR FPR"],
            [
                [t, v["n_docs"], v["level_macro_f1"], v["category_macro_f1"], v["high_risk_recall"], v["high_risk_false_positive_rate"]]
                for t, v in tiers.items()
            ],
        )
    )  # fmt: skip
    add("")
    add("T5 (adversarial) is a slice and is excluded from the headline.")
    add("")
    lat = res.metrics["latency"]["classifier_reported"]
    ti = sum(r.tokens_in or 0 for r in recs)
    to = sum(r.tokens_out or 0 for r in recs)
    cost = res.metrics["cost"]["est_cost_usd_total"]
    add("### Latency, tokens, cost")
    add("")
    add(
        f"Reported latency P50 {_f(lat.get('p50_ms'))} ms, P95 {_f(lat.get('p95_ms'))} ms over "
        f"{lat.get('n', 0)} documents; **for replayed runs this is the latency recorded when the responses were "
        f"captured**. Tokens: {ti} prompt + {to} completion. Estimated cost: "
        + (f"${cost:.4f}" if cost is not None else "not estimated (no price configured)")
        + "."
    )
    add("")
    return L


def build_llm_report(
    bundle: ConfigBundle,
    probe: LLMClassifier,
    tiers: dict[str, LLMClassifier | None],
    docs_by_split: dict[str, list[DatasetDocument]],
    dataset_manifest: dict[str, Any],
    git: dict[str, Any],
    *,
    rules_ml_factory=None,
) -> str:
    """`tiers[t]` is a replay-backed classifier or None (no model id supplied for that tier).
    `rules_ml_factory()` returns (rules_result_or_None, ml_result_or_None) for dev comparison."""
    policy = bundle.policy
    threshold = bundle.eval.min_support_flag
    dev = docs_by_split["dev"]
    p = probe.params()
    scanner = probe.scanner
    L: list[str] = []
    add = L.append

    results: dict[str, EvaluationResult | None] = {}
    status: dict[str, str] = {}
    for t in TIERS:
        clf = tiers.get(t)
        if clf is None:
            results[t], status[t] = None, "NOT RUN (no model configured for this tier)"
            continue
        res = evaluate(clf, dev, bundle, dataset_manifest)
        s, _ = _tier_status(res)
        results[t], status[t] = (res if s.startswith("RUN") else None), s

    add("# LLM classifier status and results (Approach C)")
    add("")
    add(
        "> Generated by `dataguard-uc4 llm report` from executed code on the DEVELOPMENT splits. "
        "**The locked test split was not read.** Dataset labels: **AI-generated synthetic dataset "
        "— pending human gold-label review**."
    )
    add("")
    ran = [t for t in TIERS if results[t] is not None]
    if not ran:
        add(
            "> **NO LLM BENCHMARK HAS BEEN RUN.** No model endpoint, deployment names or recorded "
            "responses exist for any tier, so there are **no LLM accuracy, calibration, latency or cost "
            "numbers** in this document, and none should be inferred. Only the model-independent "
            "measurements below (prompt audit and injection guard) are real results."
        )
        add("")
    add("## Tier status")
    add("")
    add(_table(["tier", "status"], [[t, status[t]] for t in TIERS]))
    add("")
    add(
        "The Foundry adapter is **unverified against a real endpoint** (tested only against a local "
        "fake server). The PRD requires at least three model configurations to be benchmarked; that "
        "remains blocked on access and deployment names supplied by the product owner."
    )
    add("")
    add("To run a tier once access exists (records a replayable cache; then re-run this report):")
    add("")
    add(f"    {RUN_COMMAND}")
    add("")
    add("## Provenance and protocol")
    add("")
    add("* pre-registered plan: `docs/uc4/llm-plan.md` (committed before any LLM code)")
    add(
        f"* prompt `{p['prompt_version']}` sha256 `{p['prompt_sha256'][:16]}...`; llm config sha256 `{p['llm_config_sha256'][:16]}...`"
    )
    add(
        f"* {len(p['few_shot_doc_ids'])} few-shot examples, all from `train`; temperature {p['temperature']}; max output tokens {p['max_output_tokens']}"
    )
    add(
        f"* injection guardrail `{p['guardrail_version']}` sha256 `{p['guardrail_config_sha256'][:16]}...`"
    )
    add(
        f"* git `{(git.get('commit') or 'unknown')[:12]}` on `{git.get('branch')}` (dirty: {git.get('dirty')})"
    )
    add("* embedded labels and metadata are NOT shown to the model; the filename is")
    add("")

    # ---- prompt audit --------------------------------------------------------------------------
    add("## Prompt audit (no model involved)")
    add("")
    builder = probe.builder
    fs_ids = set(p["few_shot_doc_ids"])
    train = {d.doc_id: d for d in docs_by_split["train"]}
    fs_fams = {train[i].family_id for i in fs_ids if i in train}
    other_fams = {d.family_id for s in ("calibration", "dev") for d in docs_by_split[s]}
    sizes = []
    trunc = 0
    for d in dev:
        b = builder.build(d.to_request().document)
        sizes.append(len(b.user))
        trunc += b.truncated
    add(
        _table(
            ["check", "result"],
            [
                ["few-shot examples", len(fs_ids)],
                ["all few-shot examples are train documents", all(i in train for i in fs_ids)],
                ["few-shot families also present in calibration/dev", len(fs_fams & other_fams)],
                ["system prompt characters (fixed part)", len(builder.system_prompt)],
                ["dev user-message characters: median / max", f"{_median(sizes)} / {max(sizes)}"],
                ["dev documents truncated at max_input_chars", _rate(trunc, len(dev))],
            ],
        )
    )
    add("")
    add(
        "Characters, not tokens: token counts depend on the tokenizer of the deployed model and are "
        "recorded only from real responses."
    )
    add("")

    # ---- injection guard -----------------------------------------------------------------------
    add("## Local injection guard (lexicon; no model involved)")
    add("")
    add(
        "The lexicon (`config/guardrails/injection.v1.yaml`) was developed on **train** only. `dev` "
        "and `calibration` are held out. A hit only records a guardrail event; it never blocks or "
        "lowers a level."
    )
    add("")
    rows = []
    for split in ("train", "calibration", "dev"):
        g = _guard_stats(scanner, docs_by_split[split])
        rows.append(
            [
                split + (" (developed on)" if split == "train" else " (held out)"),
                _rate(g["t5_flagged"], g["n_t5"]),
                _rate(g["benign_flagged"], g["n_benign"]),
            ]
        )
    add(
        _table(
            [
                "split",
                "T5 documents flagged (detection)",
                "T1-T4 documents flagged (false positives)",
            ],
            rows,
        )
    )
    add("")
    gd = _guard_stats(scanner, dev)
    add(
        "Dev detection by attack type: "
        + ", ".join(f"`{k}` {v[0]}/{v[1]}" for k, v in gd["by_type"].items())
        + f". Dev false-positive families: {gd['fp_families'] or 'none'}. "
        f"Dev T5 families missed: {gd['missed_t5_families'] or 'none'}."
    )
    add("")
    add(
        "A lexicon detects known phrasing only: paraphrased, encoded or novel attacks will be "
        "missed, and only 5 T5 families exist in train and 3-4 in dev, so these rates are anecdotal."
    )
    add("")

    # ---- per-tier results ----------------------------------------------------------------------
    if ran:
        rules_res, ml_res = rules_ml_factory() if rules_ml_factory else (None, None)
        for t in ran:
            L.extend(_tier_section(t, tiers[t], results[t], rules_res, ml_res, policy, threshold))
    add("## Caveats")
    add("")
    add(
        "* Synthetic, template-generated, AI-authored labels not yet human reviewed; dev has 18 families."
    )
    add("* The Foundry adapter is unverified against a real service; no model names are assumed.")
    add(
        "* Replayed runs use RECORDED responses and latency; a changed prompt, few-shot set or schema invalidates the cache key by design."
    )
    add("* The comparison with Rules flatters Rules (developed against dev).")
    add(
        "* Hybrid routing, injection 'raise-not-lower' fusion, thresholds and the locked-test report are Phase 6 and have not been done."
    )
    add("")
    return "\n".join(L)
