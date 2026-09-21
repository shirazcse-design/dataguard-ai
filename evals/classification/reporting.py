"""Write run artifacts and render the human-readable report.

Every number in a report is read from the run's metrics; nothing is typed by hand, and the report
states the run id and dataset hash it came from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .evaluate import EvaluationResult

DEFAULT_RUNS_DIR = Path(__file__).resolve().parent / "runs"
# Append-only audit trail of every authorised read of the locked test split. It is a TRACKED file on
# purpose: each entry appears in `git diff`, which discourages repeated peeking.
DEFAULT_ACCESS_LOG = (
    Path(__file__).resolve().parents[2] / "data/synthetic/uc4/locked_test_access.jsonl"
)


def _f(x: Any, digits: int = 3) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{digits}f}"
    return str(x)


def _ci(stat: dict[str, Any]) -> str:
    if stat["point"] is None:
        return "n/a"
    return f"{_f(stat['point'])} [{_f(stat['ci_low'])}, {_f(stat['ci_high'])}]"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(_f(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _flag(support: int, threshold: int) -> str:
    return "SMALL_SAMPLE" if support < threshold else ""


def render_run_report(result: EvaluationResult) -> str:
    m, man = result.metrics, result.manifest
    head = m["headline"]
    hm = head["metrics"]
    ci = head["confidence_intervals"]
    stats = ci["statistics"]
    ds = man["dataset"]
    threshold = man["eval_settings"]["min_support_flag"]
    L: list[str] = []
    add = L.append

    add(f"# Evaluation run `{man['run_id']}`")
    add("")
    if ds["evaluated_locked_test_split"]:
        auth = ds["locked_test_authorization"] or {}
        add(
            "> **LOCKED TEST SPLIT EVALUATED - REPORT-ONLY.** Authorised via "
            f"`{auth.get('mechanism')}` at {auth.get('authorized_at')}. These results must not be "
            "used to tune rules, thresholds, prompts or models."
        )
        add("")
    add(
        "> Every number below was computed by this run from the classifier's actual outputs. "
        "There is no combined headline score; each metric stands alone."
    )
    add("")
    params = man["classifier"]["params"]
    fit = set(params.get("fit_splits", [])) & set(ds["splits_evaluated"])
    calib = set(params.get("calibration_splits", [])) & set(ds["splits_evaluated"])
    if fit or calib:
        add(
            "> **IN-SAMPLE WARNING.** This run evaluates data the classifier was "
            + (f"**fitted on** ({', '.join(sorted(fit))}) " if fit else "")
            + (
                f"and/or **used to fit its calibrators** ({', '.join(sorted(calib))}) "
                if calib
                else ""
            )
            + "so these numbers are optimistic and are NOT held-out performance."
        )
        add("")
    if params.get("few_shot_doc_ids") and "train" in ds["splits_evaluated"]:
        add(
            f"> **FEW-SHOT NOTE.** {len(params['few_shot_doc_ids'])} `train` documents are shown to "
            "the model as prompt examples, so `train` results include examples it has seen and are "
            "not held-out. Read `dev`."
        )
        add("")
    add("## Provenance")
    add("")
    add(f"* **dataset labels: {ds['label_status']}** (not independently human-validated)")
    add(
        f"* classifier: `{man['classifier']['name']}` v{man['classifier']['version']} "
        f"params `{json.dumps(man['classifier']['params'], sort_keys=True)}`"
    )
    add(
        f"* dataset: `{ds['dataset_id']}` v{ds['dataset_version']} "
        f"sha256 `{ds['dataset_sha256'][:16]}...`"
    )
    add(
        f"* splits evaluated: {', '.join(ds['splits_evaluated'])} "
        f"({ds['n_documents']} documents, {ds['n_groups']} families)"
    )
    add(
        f"* git: `{(man['git']['commit'] or 'unknown')[:12]}` on `{man['git']['branch']}` "
        f"(dirty: {man['git']['dirty']})"
    )
    add(f"* config versions: {man['config']['versions']}")
    add(f"* metrics fingerprint: `{result.fingerprint}`")
    add("")
    add("## Coverage and failures")
    add("")
    cov = m["all_tiers"]["coverage"]
    add(
        _table(
            [
                "documents",
                "with prediction",
                "failed",
                "review required",
                "review rate",
                "auto-decided",
                "abstained (rate)",
            ],
            [
                [
                    cov["n_docs"],
                    cov["n_with_prediction"],
                    cov["n_failed"],
                    cov["n_review_required"],
                    cov["review_rate"],
                    cov["n_auto_decided"],
                    f"{cov['n_abstained']} ({_f(cov['abstention_rate'])})",
                ]
            ],
        )
    )
    if cov["failure_reasons"]:
        add("")
        add(f"Failure reasons: `{cov['failure_reasons']}`. Failed documents are counted as misses.")
    if cov["classifier_high_risk_mismatches"]:
        add("")
        add(
            f"**{cov['classifier_high_risk_mismatches']} results carried a `high_risk` value that "
            "disagreed with the configured definition; the harness used its own derivation.**"
        )
    add("")

    n_docs, n_fam = hm["coverage"]["n_docs"], hm["coverage"]["n_groups"]
    add(
        f"## Headline metrics (tiers {', '.join(head['tiers'])}; {n_docs} documents, {n_fam} families)"
    )
    add("")
    add(
        f"Intervals are {int(ci['confidence_level'] * 100)}% percentile bootstrap intervals from "
        f"{ci['n_resamples']} resamples (seed {ci['seed']}) of **{ci['unit']}s**. "
        f"**They rest on {ci['n_units']} independent families** ({n_docs} documents), because "
        "documents within a family are variations of one template and are correlated. Read the "
        "intervals, not just the point estimates."
    )
    add("")
    hr = hm["high_risk"]
    add(
        _table(
            ["metric", "value [95% CI]"],
            [
                ["Sensitivity level: macro-F1", _ci(stats["level_macro_f1"])],
                ["Data categories: macro-F1", _ci(stats["category_macro_f1"])],
                ["High-risk: recall", _ci(stats["high_risk_recall"])],
                ["High-risk: precision", _ci(stats["high_risk_precision"])],
                ["High-risk: F1", _ci(stats["high_risk_f1"])],
                ["High-risk: false-positive rate", _ci(stats["high_risk_false_positive_rate"])],
                ["Review rate (headline documents)", _f(hm["coverage"]["review_rate"])],
            ],
        )
    )
    add("")
    ref = man["eval_settings"].get("reference_targets", {}).get("high_risk_recall")
    if ref is not None:
        got = stats["high_risk_recall"]["point"]
        verdict = "n/a" if got is None else ("at or above" if got >= ref else "below")
        add(
            f"Initial safety-oriented reference: high-risk recall >= {ref:.2f}; observed "
            f"{_f(got)} ({verdict} the reference). **This is informational, not a pass/fail gate:** "
            "recall alone does not determine success, and no minimum precision or false-positive "
            "threshold has been chosen yet. That operating point will be selected on the "
            "development set; never tune against the locked test split."
        )
        add("")
    add(f"Macro convention: {hm['level']['macro_convention']}.")
    add("")

    sm = head["small_sample_labels"]
    add(
        f"## Sample-size warnings (SMALL_SAMPLE: fewer than {sm['min_support_flag']} gold positives)"
    )
    add("")
    if sm["levels"] or sm["categories"]:
        rows = [["level", k, v, "SMALL_SAMPLE"] for k, v in sm["levels"].items()]
        rows += [["category", k, v, "SMALL_SAMPLE"] for k, v in sm["categories"].items()]
        add(_table(["axis", "label", "gold positives (headline subset)", "warning"], rows))
    else:
        add("None: every label has enough positives in the headline subset.")
    add("")
    add("Per-label counts are preserved in the `support` columns of the tables below.")
    add("")

    cal = head.get("calibration")
    if cal:
        add("### Calibration (probabilities reported by the classifier)")
        add("")
        claim = "claims calibration" if cal["calibrated_claim"] else "does NOT claim calibration"
        add(
            f"The classifier {claim}. Reliability is measured on this evaluation subset with "
            f"{man['eval_settings'].get('calibration_bins', 5)} equal-width bins; with so few "
            "documents per bin these numbers are indicative only."
        )
        add("")
        lv_c = cal["level"]
        add(
            f"**Level (top-label confidence):** ECE {_f(lv_c['ece'])}, multiclass Brier {_f(lv_c['brier_multiclass'])}, n={lv_c['n']}."
        )
        add("")
        add(
            _table(
                ["confidence bin", "n", "mean confidence", "accuracy"],
                [[b["bin"], b["n"], b["mean_confidence"], b["accuracy"]] for b in lv_c["bins"]],
            )
        )
        add("")
        cc = cal["categories"]
        if cc:
            add(
                f"**Categories (all document-category pairs):** ECE {_f(cc['ece'])}, Brier {_f(cc['brier'])}, n={cc['n']}."
            )
            add("")
            add(
                _table(
                    ["probability bin", "n", "mean probability", "observed rate"],
                    [[b["bin"], b["n"], b["mean_confidence"], b["accuracy"]] for b in cc["bins"]],
                )
            )
            add("")
            add(
                _table(
                    ["category", "gold positives", "mean probability", "prevalence", "Brier"],
                    [
                        [k, v["n_positive"], v["mean_probability"], v["prevalence"], v["brier"]]
                        for k, v in cc["per_category"].items()
                    ],
                )
            )
            add("")
    add("### Sensitivity level")
    add("")
    lv = hm["level"]
    cm = lv["confusion_matrix"]
    add("Confusion matrix (rows = gold, columns = predicted):")
    add("")
    add(
        _table(
            ["gold \\ predicted", *cm["cols_predicted"]],
            [[g, *row] for g, row in zip(cm["rows_gold"], cm["values"], strict=True)],
        )
    )
    add("")
    add(
        _table(
            ["level", "precision", "recall", "F1", "support", "predicted", "warning"],
            [
                [k, v["precision"], v["recall"], v["f1"], v["support"], v["predicted"],
                 _flag(v["support"], threshold)]
                for k, v in lv["per_class"].items()
            ],
        )
    )  # fmt: skip
    add("")
    o = lv["ordinal_errors"]
    add(
        f"macro P/R/F1: {_f(lv['macro']['precision'])} / {_f(lv['macro']['recall'])} / "
        f"{_f(lv['macro']['f1'])}; micro F1 {_f(lv['micro']['f1'])}; "
        f"accuracy {_f(lv['accuracy'])}. Under-classification "
        f"{_f(o['under_classification_rate'])} (severe {_f(o['severe_under_classification_rate'])}), "
        f"over-classification {_f(o['over_classification_rate'])}."
    )
    add("")

    add("### Data categories")
    add("")
    cat = hm["categories"]
    add(
        _table(
            ["category", "precision", "recall", "F1", "support", "TP", "FP", "FN", "TN", "warning"],
            [
                [k, v["precision"], v["recall"], v["f1"], v["support"], v["tp"], v["fp"], v["fn"],
                 v["tn"], _flag(v["support"], threshold)]
                for k, v in cat["per_label"].items()
            ],
        )
    )  # fmt: skip
    add("")
    add(
        f"macro P/R/F1: {_f(cat['macro']['precision'])} / {_f(cat['macro']['recall'])} / "
        f"{_f(cat['macro']['f1'])}; micro F1 {_f(cat['micro']['f1'])}; exact-match "
        f"{_f(cat['exact_match_ratio'])}."
    )
    add("")

    add("### High-risk (derived from the configured definition)")
    add("")
    add(
        _table(
            ["TP", "FP", "FN", "TN", "precision", "recall", "F1", "FPR", "prevalence"],
            [
                [hr["tp"], hr["fp"], hr["fn"], hr["tn"], hr["precision"], hr["recall"], hr["f1"],
                 hr["false_positive_rate"], hr["prevalence"]]
            ],
        )
    )  # fmt: skip
    add("")

    lenient = head.get("lenient_level_view")
    if lenient and lenient["n_relaxed"]:
        add("### Lenient level view (within the gold's acceptable alternatives)")
        add("")
        add(
            f"{lenient['note']} {lenient['n_relaxed']} of {lenient['n_headline']} headline documents change from "
            "incorrect to correct under it."
        )
        add("")
        add(
            _table(
                ["view", "level accuracy", "level macro-F1 [95% CI]"],
                [
                    ["strict (headline)", hm["level"]["accuracy"], _ci(stats["level_macro_f1"])],
                    ["lenient", lenient["level_accuracy"], _ci(lenient["level_macro_f1_interval"])],
                ],
            )
        )
        add("")
    dv = head["deferral_views"]
    add("### Deferrals to human review")
    add("")
    if dv["n_deferred"] == 0:
        add("No documents were deferred; all scoring views are identical.")
    else:
        add(
            f"{dv['n_deferred']} documents were deferred. Primary metrics score deferred "
            "documents on their provisional label. Alternative views:"
        )
        add("")
        add(
            _table(
                ["view", "level macro-F1", "category macro-F1", "high-risk recall", "documents"],
                [
                    [name, v["level_macro_f1"], v["category_macro_f1"], v["high_risk_recall"],
                     v["n_docs"]]
                    for name, v in dv.items()
                    if isinstance(v, dict)
                ],
            )
        )  # fmt: skip
        add("")
        add("The perfect-reviewer row is HYPOTHETICAL and is not a measured result.")
    add("")

    hn = m["hard_negatives"]
    add("## Hard negatives (T4)")
    add("")
    if hn["n_docs"]:
        add(
            f"{hn['n_docs']} hard-negative documents in {hn['n_families']} families. **Decoy-hit rate "
            f"{_f(hn['decoy_hit_rate'])}** (a decoy hit = the prediction contains the category or level "
            "the document merely resembles); any false-positive category "
            f"{_f(hn['any_false_positive_category_rate'])}; predicted high-risk although not "
            f"{_f(hn['high_risk_false_positive_rate'])}."
        )
        if hn["families_with_decoy_hits"]:
            add("")
            add(f"Families with decoy hits (documents): `{hn['families_with_decoy_hits']}`")
    else:
        add("No T4 documents in this run.")
    add("")
    add("## Slices")
    add("")
    add(
        "Computed over all tiers (so T5 appears here). Macro-F1 covers only labels with support in "
        "the slice; slices with fewer documents than the threshold are marked SMALL_SAMPLE."
    )
    for field, values in m["slices"].items():
        add("")
        add(f"**by {field}**")
        add("")
        add(
            _table(
                [field, "docs", "families", "level macro-F1", "category macro-F1",
                 "high-risk recall", "high-risk precision", "high-risk FPR", "warning"],
                [
                    [name, v["n_docs"], v["n_groups"], v["level_macro_f1"], v["category_macro_f1"],
                     v["high_risk_recall"], v["high_risk_precision"],
                     v["high_risk_false_positive_rate"], "SMALL_SAMPLE" if v["small_sample"] else ""]
                    for name, v in values.items()
                ],
            )
        )  # fmt: skip
    add("")
    lat = m["latency"]["wall_clock"]
    add("## Latency (wall clock per document; not part of the reproducible fingerprint)")
    add("")
    if lat["n"]:
        pcols = [k for k in lat if k.startswith("p")]
        add(
            _table(
                ["n", "mean ms", *[k.replace("_ms", " ms") for k in pcols], "max ms", "total ms"],
                [
                    [
                        lat["n"],
                        lat["mean_ms"],
                        *[lat[k] for k in pcols],
                        lat["max_ms"],
                        lat["total_ms"],
                    ]
                ],
            )
        )
    add("")
    add("## Caveats")
    add("")
    add(f"* Dataset labels: {ds['label_status']}. Nothing here is human-validated.")
    add(
        "* Documents in a family are variations of one template; treat families as the sample size."
    )
    add(
        "* Precision and accuracy depend on this dataset's class balance, not production base rates."
    )
    return "\n".join(L) + "\n"


def write_run(result: EvaluationResult, runs_dir: Path | str | None = None) -> Path:
    """Write run artifacts. Everything is serialised BEFORE the directory is created, so a
    serialisation error can never leave a partial or empty run directory behind."""
    base = Path(runs_dir) if runs_dir is not None else DEFAULT_RUNS_DIR
    files = {
        "run_manifest.json": json.dumps(result.manifest, indent=2, sort_keys=True) + "\n",
        "metrics.json": json.dumps(result.metrics, indent=2, sort_keys=True) + "\n",
        "predictions.jsonl": "".join(
            json.dumps(r.model_dump(), sort_keys=True, ensure_ascii=False) + "\n"
            for r in result.records
        ),
        "report.md": render_run_report(result),
    }
    out = base / result.run_id
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    return out


def append_access_log(result: EvaluationResult, log_path: Path | str | None = None) -> Path:
    """Append one audit entry for a run that read the locked test split."""
    access = result.manifest.get("locked_test_access")
    if access is None:
        raise ValueError("run did not touch the locked test split; nothing to log")
    path = Path(log_path) if log_path is not None else DEFAULT_ACCESS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_id": result.run_id,
        "timestamp": access["timestamp"],
        "git_commit": access["git_commit"],
        "git_dirty": access["git_dirty"],
        "dataset_sha256": access["dataset_sha256"],
        "config_versions": access["config_versions"],
        "classifier": access["classifier"],
        "authorization": access["authorization"],
        "metrics_fingerprint": result.fingerprint,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return path
