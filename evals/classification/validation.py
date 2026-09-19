"""Validate the evaluation harness itself, using classifiers whose correct scores are known.

* Oracle:    must score perfectly on every metric.
* Majority:  the harness output must equal expectations computed by SEPARATE, deliberately naive
             code that reads raw gold labels and the config lists directly (no shared metrics code).
* Random:    seeded; must land within 4.5 binomial standard deviations of the analytic chance rate.
* Robustness: failures are counted (never dropped), deferral views behave, latency is captured,
              runs are reproducible.

Nothing here is a benchmark of an approach; it is evidence that the ruler measures correctly.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from app.classification.config_loader import ConfigBundle
from app.classification.schemas import (
    NO_CONFIDENCE,
    ClassificationRequest,
    ClassificationResult,
    LevelPrediction,
    ReviewDecision,
)

from .baselines import MajorityClassifier, OracleClassifier, RandomClassifier
from .dataset.schema import DatasetDocument
from .evaluate import EvaluationResult, evaluate
from .lock import LockedTestAuthorization

RANDOM_SEED = 20260918
SIGMAS = 4.5
TOL = 1e-9


@dataclass
class Check:
    suite: str
    split: str
    name: str
    passed: bool
    expected: Any
    observed: Any


@dataclass
class HarnessValidation:
    checks: list[Check] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(
        self, suite: str, split: str, name: str, passed: bool, expected: Any, observed: Any
    ) -> None:
        self.checks.append(Check(suite, split, name, bool(passed), expected, observed))

    def eq(self, suite: str, split: str, name: str, expected: Any, observed: Any) -> None:
        if isinstance(expected, float) or isinstance(observed, float):
            ok = (
                expected is None and observed is None
                if expected is None or observed is None
                else abs(expected - observed) <= TOL
            )
        else:
            ok = expected == observed
        self.add(suite, split, name, ok, expected, observed)

    def within(
        self, suite: str, split: str, name: str, expected: float, observed: float | None, sd: float
    ) -> None:
        ok = observed is not None and abs(observed - expected) <= SIGMAS * sd + 1e-12
        self.add(suite, split, name, ok, f"{expected:.4f} +/- {SIGMAS}*{sd:.4f}", observed)


# ---------------------------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------------------------
def _check_oracle(v: HarnessValidation, split: str, res: EvaluationResult, view: str) -> None:
    m = res.metrics["headline"]["metrics"] if view == "headline" else res.metrics["all_tiers"]
    s = f"oracle/{view}"
    lv, ct, hr, cov = m["level"], m["categories"], m["high_risk"], m["coverage"]
    v.eq(s, split, "level accuracy == 1", 1.0, lv["accuracy"])
    v.eq(s, split, "level macro F1 == 1", 1.0, lv["macro"]["f1"])
    v.eq(s, split, "level macro precision == 1", 1.0, lv["macro"]["precision"])
    v.eq(s, split, "level macro recall == 1", 1.0, lv["macro"]["recall"])
    v.eq(s, split, "level micro F1 == 1", 1.0, lv["micro"]["f1"])
    diag = [
        [c if i == j else 0 for j, c in enumerate(row)]
        for i, row in enumerate(lv["confusion_matrix"]["values"])
    ]
    v.eq(s, split, "confusion matrix is purely diagonal", diag, lv["confusion_matrix"]["values"])
    v.eq(
        s,
        split,
        "every supported level has P=R=F1=1",
        True,
        all(
            p["precision"] == p["recall"] == p["f1"] == 1.0
            for p in lv["per_class"].values()
            if p["support"]
        ),
    )
    o = lv["ordinal_errors"]
    v.eq(
        s,
        split,
        "no under/over-classification",
        (0.0, 0.0, 0.0),
        (
            o["under_classification_rate"],
            o["severe_under_classification_rate"],
            o["over_classification_rate"],
        ),
    )
    v.eq(s, split, "category macro F1 == 1", 1.0, ct["macro"]["f1"])
    v.eq(s, split, "category micro F1 == 1", 1.0, ct["micro"]["f1"])
    v.eq(s, split, "category exact-match == 1", 1.0, ct["exact_match_ratio"])
    v.eq(s, split, "no false-positive categories", 0, ct["docs_with_false_positive_category"])
    v.eq(
        s,
        split,
        "every category has FP=FN=0",
        True,
        all(p["fp"] == 0 and p["fn"] == 0 for p in ct["per_label"].values()),
    )
    v.eq(s, split, "high-risk recall == 1", 1.0, hr["recall"])
    v.eq(s, split, "high-risk precision == 1", 1.0, hr["precision"])
    v.eq(s, split, "high-risk FPR == 0", 0.0, hr["false_positive_rate"])
    v.eq(s, split, "high-risk has no FP/FN", (0, 0), (hr["fp"], hr["fn"]))
    v.eq(
        s,
        split,
        "no failures, deferrals or mismatches",
        (0, 0, 0),
        (cov["n_failed"], cov["n_deferred_to_review"], cov["classifier_high_risk_mismatches"]),
    )
    if view == "headline":
        stats = res.metrics["headline"]["confidence_intervals"]["statistics"]
        expected = {k: (0.0 if k.endswith("false_positive_rate") else 1.0) for k in stats}
        collapsed = {
            k: (x["ci_low"], x["ci_high"]) == (expected[k], expected[k]) for k, x in stats.items()
        }
        v.eq(
            s, split, "bootstrap intervals collapse to the perfect value (1; FPR 0)",
            True, all(collapsed.values()),
        )  # fmt: skip


# ---------------------------------------------------------------------------------------------
# Majority: independent expectations
# ---------------------------------------------------------------------------------------------
def expected_constant_prediction(
    docs: list[DatasetDocument], level: str, cats: list[str], bundle: ConfigBundle
) -> dict[str, Any]:
    """Expected metrics for a classifier that predicts the SAME (level, cats) for every document.

    Deliberately naive and independent of `metrics.py`: plain loops over raw gold labels and the
    config lists, using the closed-form F1 = 2TP / (predicted + support).
    """
    n = len(docs)
    levels = [lv.id for lv in sorted(bundle.taxonomy.levels, key=lambda x: x.rank)]
    rank = {lv: i for i, lv in enumerate(levels)}
    hr_levels, hr_cats = set(bundle.high_risk.levels), set(bundle.high_risk.categories)

    def prf(tp, predicted, support):
        return (
            tp / predicted if predicted else None,
            tp / support if support else None,
            2 * tp / (predicted + support) if predicted + support else None,
        )

    out: dict[str, Any] = {"n": n}
    per, supported = {}, []
    for lv in levels:
        support = sum(d.gold_level == lv for d in docs)
        predicted = n if lv == level else 0
        tp = support if lv == level else 0
        per[lv] = prf(tp, predicted, support) + (support,)
        if support:
            supported.append(lv)
    out["level_accuracy"] = per[level][3] / n if n else None
    out["level_macro_f1"] = sum(per[lv][2] for lv in supported) / len(supported)
    out["level_macro_precision"] = sum((per[lv][0] or 0.0) for lv in supported) / len(supported)
    out["level_macro_recall"] = sum(per[lv][1] for lv in supported) / len(supported)
    out["level_micro_f1"] = per[level][3] / n  # TP=support(level), predicted=n, support=n
    out["level_per_class_f1"] = {lv: per[lv][2] for lv in levels}
    out["under"] = sum(rank[d.gold_level] > rank[level] for d in docs) / n
    out["over"] = sum(rank[d.gold_level] < rank[level] for d in docs) / n
    out["severe_under"] = sum(rank[d.gold_level] - rank[level] >= 2 for d in docs) / n

    cat_ids = [c.id for c in bundle.taxonomy.categories]
    cs = set(cats)
    cper, csupported = {}, []
    for c in cat_ids:
        support = sum(c in d.gold_categories for d in docs)
        predicted = n if c in cs else 0
        tp = support if c in cs else 0
        cper[c] = prf(tp, predicted, support)
        if support:
            csupported.append(c)
    out["category_macro_f1"] = sum(cper[c][2] for c in csupported) / len(csupported)
    out["category_macro_recall"] = sum(cper[c][1] for c in csupported) / len(csupported)
    out["category_per_label_recall"] = {c: cper[c][1] for c in cat_ids}
    out["category_exact_match"] = sum(set(d.gold_categories) == cs for d in docs) / n

    pred_hr = level in hr_levels or bool(cs & hr_cats)
    gold_hr = [d.gold_level in hr_levels or bool(set(d.gold_categories) & hr_cats) for d in docs]
    tp = sum(g and pred_hr for g in gold_hr)
    fn = sum(g and not pred_hr for g in gold_hr)
    fp = sum((not g) and pred_hr for g in gold_hr)
    tn = n - tp - fn - fp
    out["hr"] = {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "recall": tp / (tp + fn) if tp + fn else None,
        "precision": tp / (tp + fp) if tp + fp else None,
        "fpr": fp / (fp + tn) if fp + tn else None,
    }
    return out


def _check_majority(
    v: HarnessValidation,
    split: str,
    view: str,
    docs: list[DatasetDocument],
    res: EvaluationResult,
    level: str,
    cats: list[str],
    bundle: ConfigBundle,
) -> None:
    subset = (
        [d for d in docs if d.tier in set(bundle.eval.headline_tiers)]
        if view == "headline"
        else docs
    )
    exp = expected_constant_prediction(subset, level, cats, bundle)
    m = res.metrics["headline"]["metrics"] if view == "headline" else res.metrics["all_tiers"]
    s = f"majority/{view}"
    lv, ct, hr = m["level"], m["categories"], m["high_risk"]
    v.eq(s, split, "documents evaluated", exp["n"], m["coverage"]["n_docs"])
    v.eq(s, split, "level accuracy", exp["level_accuracy"], lv["accuracy"])
    v.eq(s, split, "level macro F1", exp["level_macro_f1"], lv["macro"]["f1"])
    v.eq(s, split, "level macro precision", exp["level_macro_precision"], lv["macro"]["precision"])
    v.eq(s, split, "level macro recall", exp["level_macro_recall"], lv["macro"]["recall"])
    v.eq(s, split, "level micro F1", exp["level_micro_f1"], lv["micro"]["f1"])
    v.eq(
        s,
        split,
        "level per-class F1",
        exp["level_per_class_f1"],
        {k: p["f1"] for k, p in lv["per_class"].items()},
    )
    o = lv["ordinal_errors"]
    v.eq(s, split, "under-classification rate", exp["under"], o["under_classification_rate"])
    v.eq(
        s,
        split,
        "severe under-classification rate",
        exp["severe_under"],
        o["severe_under_classification_rate"],
    )
    v.eq(s, split, "over-classification rate", exp["over"], o["over_classification_rate"])
    v.eq(s, split, "category macro F1", exp["category_macro_f1"], ct["macro"]["f1"])
    v.eq(s, split, "category macro recall", exp["category_macro_recall"], ct["macro"]["recall"])
    v.eq(
        s,
        split,
        "category per-label recall",
        exp["category_per_label_recall"],
        {k: p["recall"] for k, p in ct["per_label"].items()},
    )
    v.eq(s, split, "category exact-match", exp["category_exact_match"], ct["exact_match_ratio"])
    e = exp["hr"]
    v.eq(
        s,
        split,
        "high-risk confusion counts",
        (e["tp"], e["fp"], e["fn"], e["tn"]),
        (hr["tp"], hr["fp"], hr["fn"], hr["tn"]),
    )
    v.eq(s, split, "high-risk recall", e["recall"], hr["recall"])
    v.eq(s, split, "high-risk precision", e["precision"], hr["precision"])
    v.eq(s, split, "high-risk false-positive rate", e["fpr"], hr["false_positive_rate"])


# ---------------------------------------------------------------------------------------------
# Random: analytic chance rates
# ---------------------------------------------------------------------------------------------
def _binom_sd(p: float, n: int) -> float:
    return math.sqrt(p * (1 - p) / n) if n else float("inf")


def _check_random(
    v: HarnessValidation,
    split: str,
    docs: list[DatasetDocument],
    res: EvaluationResult,
    bundle: ConfigBundle,
) -> None:
    m = res.metrics["all_tiers"]
    lv, ct, hr = m["level"], m["categories"], m["high_risk"]
    s, n = "random/all_tiers", m["coverage"]["n_docs"]
    n_levels = len(bundle.taxonomy.levels)
    v.within(
        s,
        split,
        "level accuracy ~ 1/levels",
        1 / n_levels,
        lv["accuracy"],
        _binom_sd(1 / n_levels, n),
    )
    for label, p in lv["per_class"].items():
        if p["support"] >= 10:
            v.within(
                s,
                split,
                f"level recall[{label}] ~ 1/levels",
                1 / n_levels,
                p["recall"],
                _binom_sd(1 / n_levels, p["support"]),
            )
    for label, p in ct["per_label"].items():
        if p["support"] >= 10:
            v.within(
                s,
                split,
                f"category recall[{label}] ~ 0.5",
                0.5,
                p["recall"],
                _binom_sd(0.5, p["support"]),
            )
        negatives = p["fp"] + p["tn"]
        if negatives >= 10:
            v.within(
                s,
                split,
                f"category FPR[{label}] ~ 0.5",
                0.5,
                p["false_positive_rate"],
                _binom_sd(0.5, negatives),
            )
    n_hr_levels, n_hr_cats = len(bundle.high_risk.levels), len(bundle.high_risk.categories)
    q = (
        1 - (1 - n_hr_levels / n_levels) * 0.5**n_hr_cats
    )  # P(predicted high-risk), independent of gold
    positives, negatives = hr["tp"] + hr["fn"], hr["fp"] + hr["tn"]
    v.within(
        s, split, "high-risk recall ~ P(pred high-risk)", q, hr["recall"], _binom_sd(q, positives)
    )
    v.within(
        s,
        split,
        "high-risk FPR ~ P(pred high-risk)",
        q,
        hr["false_positive_rate"],
        _binom_sd(q, negatives),
    )
    predicted_pos = hr["tp"] + hr["fp"]
    v.within(
        s,
        split,
        "high-risk precision ~ prevalence",
        hr["prevalence"],
        hr["precision"],
        _binom_sd(hr["prevalence"], predicted_pos),
    )


# ---------------------------------------------------------------------------------------------
# Robustness classifiers (test support only)
# ---------------------------------------------------------------------------------------------
class _Raising:
    name, version = "raising", "1.0"

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        raise RuntimeError("synthetic failure")

    def params(self) -> dict[str, Any]:
        return {}


class _Sleeping:
    name, version = "sleeping", "1.0"

    def __init__(self, inner: OracleClassifier, seconds: float) -> None:
        self._inner, self._seconds = inner, seconds

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        time.sleep(self._seconds)
        return self._inner.classify(request)

    def params(self) -> dict[str, Any]:
        return {"sleep_seconds": self._seconds}


class _Lying:
    """Labels every document INTERNAL/no-category but CLAIMS high_risk=True (a contradiction)."""

    name, version = "lying", "1.0"

    def __init__(self, policy: Any) -> None:
        self._policy = policy

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        from .baselines import _result

        res = _result(request, self._policy, "INTERNAL", [], "lying@1.0")
        claimed = res.high_risk.model_copy(update={"value": True})
        return res.model_copy(update={"high_risk": claimed})

    def params(self) -> dict[str, Any]:
        return {}


class _Deferring:
    """Always defers to review, offering the gold label as the provisional one."""

    name, version = "deferring", "1.0"

    def __init__(self, gold: dict[str, str]) -> None:
        self._gold = gold

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        return ClassificationResult(
            request_id=request.request_id,
            content_hash=request.document.content_hash(),
            status="review_required",
            level=LevelPrediction(
                value=self._gold[request.document.document_id or ""],
                confidence=NO_CONFIDENCE,
                decided_by="baseline",
            ),
            review=ReviewDecision(required=True, reason_codes=["LOW_CONFIDENCE"], provisional=True),
        )

    def params(self) -> dict[str, Any]:
        return {}


def _check_robustness(
    v: HarnessValidation,
    docs: list[DatasetDocument],
    bundle: ConfigBundle,
    manifest: dict[str, Any],
    ev: Any,
) -> None:
    split = "all"
    sample = sorted(docs, key=lambda d: d.doc_id)[:40]
    n = len(sample)

    failing = ev(_Raising(), sample, bundle, manifest)
    cov = failing.metrics["all_tiers"]["coverage"]
    v.eq(
        "robustness",
        split,
        "exceptions: every document still has a record",
        n,
        len(failing.records),
    )
    v.eq("robustness", split, "exceptions: all counted as failures", n, cov["n_failed"])
    v.eq(
        "robustness",
        split,
        "exceptions: failure reason recorded",
        {"exception:RuntimeError": n},
        cov["failure_reasons"],
    )
    v.eq(
        "robustness",
        split,
        "exceptions: scored as misses (level accuracy 0)",
        0.0,
        failing.metrics["all_tiers"]["level"]["accuracy"],
    )
    v.eq(
        "robustness",
        split,
        "exceptions: no-prediction column filled",
        n,
        failing.metrics["all_tiers"]["level"]["n_no_prediction"],
    )

    oracle = OracleClassifier.from_docs(sample, bundle.policy)
    slow = ev(_Sleeping(oracle, 0.003), sample, bundle, manifest)
    wall = slow.metrics["latency"]["wall_clock"]
    v.add("robustness", split, "latency: captured for every document", wall["n"] == n, n, wall["n"])
    v.add(
        "robustness",
        split,
        "latency: p50 reflects the injected 3 ms",
        wall["p50_ms"] >= 2.9,
        ">= 2.9 ms",
        round(wall["p50_ms"], 3),
    )
    v.add(
        "robustness",
        split,
        "latency: p95 >= p50",
        wall["p95_ms"] >= wall["p50_ms"],
        ">=",
        round(wall["p95_ms"], 3),
    )

    fast = ev(oracle, sample, bundle, manifest)
    v.eq(
        "robustness",
        split,
        "latency does not change the metrics fingerprint",
        fast.fingerprint,
        slow.fingerprint,
    )
    again = ev(OracleClassifier.from_docs(sample, bundle.policy), sample, bundle, manifest)
    v.eq(
        "robustness",
        split,
        "identical runs give identical fingerprints",
        fast.fingerprint,
        again.fingerprint,
    )
    rand_a = ev(RandomClassifier(1, bundle.policy), sample, bundle, manifest)
    rand_b = ev(RandomClassifier(2, bundle.policy), sample, bundle, manifest)
    v.add(
        "robustness",
        split,
        "different random seeds give different fingerprints",
        rand_a.fingerprint != rand_b.fingerprint,
        "!=",
        "differ" if rand_a.fingerprint != rand_b.fingerprint else "equal",
    )

    liar = ev(_Lying(bundle.policy), sample, bundle, manifest)
    liar_cov = liar.metrics["all_tiers"]["coverage"]
    liar_hr = liar.metrics["all_tiers"]["high_risk"]
    v.eq(
        "robustness", split, "lying high_risk: every mismatch is flagged",
        n, liar_cov["classifier_high_risk_mismatches"],
    )  # fmt: skip
    v.eq(
        "robustness", split, "lying high_risk: harness re-derives it (no predicted positives)",
        0, liar_hr["tp"] + liar_hr["fp"],
    )  # fmt: skip

    deferring = ev(_Deferring({d.doc_id: d.gold_level for d in sample}), sample, bundle, manifest)
    dv = deferring.metrics["headline"]["deferral_views"]
    head_n = deferring.metrics["headline"]["metrics"]["coverage"]["n_docs"]
    v.eq(
        "robustness",
        split,
        "deferral: every headline document reported as deferred",
        head_n,
        dv["n_deferred"],
    )
    v.eq(
        "robustness",
        split,
        "deferral: perfect-reviewer view level macro F1 (hypothetical) == 1",
        1.0,
        dv["deferred_resolved_by_perfect_reviewer_HYPOTHETICAL"]["level_macro_f1"],
    )
    v.eq(
        "robustness",
        split,
        "deferral: deferred-as-errors view scores zero",
        0.0,
        dv["deferred_as_errors"]["level_accuracy"],
    )
    v.eq(
        "robustness",
        split,
        "deferral: auto-only view has no documents",
        0,
        dv["auto_only"]["n_docs"],
    )
    prov = deferring.metrics["headline"]["metrics"]["level"]["accuracy"]
    v.eq(
        "robustness", split, "deferral: primary view scores the provisional (gold) label", 1.0, prov
    )


# ---------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------
def validate_harness(
    docs_by_split: dict[str, list[DatasetDocument]],
    bundle: ConfigBundle,
    dataset_manifest: dict[str, Any],
    random_seed: int = RANDOM_SEED,
    locked_test_authorization: LockedTestAuthorization | None = None,
) -> HarnessValidation:
    """Validate the harness. `docs_by_split` may include the locked test split only with a
    `locked_test_authorization` (evaluate() enforces this)."""
    ev = partial(evaluate, locked_test_authorization=locked_test_authorization)
    v = HarnessValidation()
    policy = bundle.policy
    train_docs = docs_by_split["train"]
    majority = MajorityClassifier.from_docs(train_docs, policy)
    maj_level, maj_cats = majority.params()["level"], majority.params()["categories"]

    for split, docs in docs_by_split.items():
        runs = {
            "oracle": ev(OracleClassifier.from_docs(docs, policy), docs, bundle, dataset_manifest),
            "majority": ev(majority, docs, bundle, dataset_manifest),
            "random": ev(RandomClassifier(random_seed, policy), docs, bundle, dataset_manifest),
        }
        for name, res in runs.items():
            h = res.metrics["headline"]["metrics"]
            v.runs.append(
                {
                    "baseline": name,
                    "split": split,
                    "run_id": res.run_id,
                    "fingerprint": res.fingerprint,
                    "n_headline_docs": h["coverage"]["n_docs"],
                    "level_macro_f1": h["level"]["macro"]["f1"],
                    "category_macro_f1": h["categories"]["macro"]["f1"],
                    "high_risk_recall": h["high_risk"]["recall"],
                    "high_risk_precision": h["high_risk"]["precision"],
                }
            )
        for view in ("headline", "all_tiers"):
            _check_oracle(v, split, runs["oracle"], view)
            _check_majority(v, split, view, docs, runs["majority"], maj_level, maj_cats, bundle)
        _check_random(v, split, docs, runs["random"], bundle)
        v.eq(
            "random",
            split,
            "same seed reproduces identical predictions",
            runs["random"].fingerprint,
            ev(RandomClassifier(random_seed, policy), docs, bundle, dataset_manifest).fingerprint,
        )
    all_docs = [d for docs in docs_by_split.values() for d in docs]
    _check_robustness(v, all_docs, bundle, dataset_manifest, ev)
    return v


# ---------------------------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------------------------
def _fmt(x: Any) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.4f}"
    if isinstance(x, dict):
        return "{" + ", ".join(f"{k}: {_fmt(val)}" for k, val in x.items()) + "}"
    if isinstance(x, (list, tuple)):
        return "[" + ", ".join(_fmt(i) for i in x) + "]"
    return str(x)


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(_fmt(c).replace("|", "\\|") for c in r) + " |" for r in rows]
    return "\n".join(out)


def render_validation_report(
    v: HarnessValidation, dataset_manifest: dict[str, Any], env: dict[str, Any]
) -> str:
    passed = sum(c.passed for c in v.checks)
    L: list[str] = []
    add = L.append
    add("# Evaluation-harness validation results")
    add("")
    add(
        "> Generated by `dataguard-uc4 eval validate-harness`. Every number below comes from an "
        "executed run; nothing is typed by hand."
    )
    add("")
    add(f"**Result: {'PASS' if v.ok else 'FAIL'}** - {passed} of {len(v.checks)} checks passed.")
    add("")
    add(
        f"* dataset: `{dataset_manifest['dataset_id']}` v{dataset_manifest['dataset_version']}, "
        f"sha256 `{dataset_manifest['dataset_sha256']}`"
    )
    add(f"* git commit: `{env.get('commit')}` (dirty: {env.get('dirty')})")
    add(
        f"* python {env.get('python')}, numpy {env.get('numpy')}, scikit-learn {env.get('scikit_learn')}"
    )
    add(f"* dataset labels: {dataset_manifest['label_status']}")
    add(
        f"* splits validated: {', '.join(env.get('splits', []))} "
        "(the locked test split is excluded unless explicitly authorised)"
    )
    add(
        f"* random-baseline seed: {RANDOM_SEED}; tolerance for chance-rate checks: {SIGMAS} binomial standard deviations"
    )
    add("")
    add("## What is validated")
    add("")
    add(
        _md_table(
            ["suite", "claim", "how it is checked"],
            [
                [
                    "oracle",
                    "returning the gold labels scores perfectly on every metric",
                    "level/category/high-risk metrics equal exactly 1 (0 for error rates); confusion matrix "
                    "purely diagonal; bootstrap intervals collapse to [1, 1]",
                ],
                [
                    "majority",
                    "a constant prediction scores exactly what closed-form arithmetic says",
                    "harness output compared with expectations computed by separate naive code from raw gold "
                    "labels and the config lists (shares no metrics code)",
                ],
                [
                    "random",
                    "a seeded random classifier scores at the analytic chance rate",
                    "level accuracy and recall, per-category recall and false-positive rate, and high-risk "
                    "recall/FPR/precision within the stated tolerance of their closed-form expectation",
                ],
                [
                    "robustness",
                    "failures are never dropped; deferral views behave; latency is captured; "
                    "runs are reproducible; a classifier that misreports high_risk is caught",
                    "exceptions, deferrals, sleeping and lying classifiers; fingerprint equality",
                ],
            ],
        )
    )
    add("")
    add("## Baseline runs")
    add("")
    add("Headline = tiers T1-T4. These are sanity classifiers, not approaches to be compared.")
    add("")
    add(
        _md_table(
            [
                "baseline",
                "split",
                "headline docs",
                "level macro-F1",
                "category macro-F1",
                "high-risk recall",
                "high-risk precision",
                "run id",
            ],
            [
                [
                    r["baseline"],
                    r["split"],
                    r["n_headline_docs"],
                    r["level_macro_f1"],
                    r["category_macro_f1"],
                    r["high_risk_recall"],
                    r["high_risk_precision"],
                    f"`{r['run_id']}`",
                ]
                for r in v.runs
            ],
        )
    )
    add("")
    add("## Checks by suite and split")
    add("")
    groups: dict[tuple[str, str], list[Check]] = {}
    for c in v.checks:
        groups.setdefault((c.suite, c.split), []).append(c)
    add(
        _md_table(
            ["suite", "split", "checks", "passed"],
            [[s, sp, len(cs), sum(c.passed for c in cs)] for (s, sp), cs in groups.items()],
        )
    )
    add("")
    failures = [c for c in v.checks if not c.passed]
    add("## Failures")
    add("")
    if failures:
        add(
            _md_table(
                ["suite", "split", "check", "expected", "observed"],
                [[c.suite, c.split, c.name, c.expected, c.observed] for c in failures],
            )
        )
    else:
        add("None.")
    add("")
    shown = "test" if "test" in env.get("splits", []) else "dev"
    add(f"## Expected vs observed on the {shown} split")
    add("")
    add(
        "Majority baseline (headline view) and random baseline (all tiers). The 'expected' column for "
        "the majority baseline comes from independent arithmetic; for random it is the analytic chance "
        "rate with its tolerance."
    )
    add("")
    show = [
        c
        for c in v.checks
        if c.split == shown and c.suite in ("majority/headline", "random/all_tiers")
    ]
    add(
        _md_table(
            ["suite", "check", "expected", "observed", "pass"],
            [[c.suite, c.name, c.expected, c.observed, "yes" if c.passed else "NO"] for c in show],
        )
    )
    add("")
    add("## Limits of this validation")
    add("")
    add(
        "* It shows the harness computes the metrics correctly on known inputs; it says nothing about "
        "any real classifier's quality."
    )
    add(
        "* Chance-rate checks are statistical (deterministic given the seed) with a 4.5-sigma tolerance."
    )
    add(
        "* Correct handling of unsupported labels, undefined values and empty inputs is covered by unit "
        "tests with hand-computed expectations rather than by these dataset-level runs."
    )
    return "\n".join(L) + "\n"


def validation_to_json(v: HarnessValidation) -> dict[str, Any]:
    return {
        "ok": v.ok,
        "n_checks": len(v.checks),
        "n_passed": sum(c.passed for c in v.checks),
        "runs": v.runs,
        "checks": [
            {"suite": c.suite, "split": c.split, "name": c.name, "passed": c.passed,
             "expected": c.expected, "observed": c.observed}
            for c in v.checks
        ],
    }  # fmt: skip
