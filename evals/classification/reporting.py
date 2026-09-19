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


def render_run_report(result: EvaluationResult) -> str:
    m, man = result.metrics, result.manifest
    head = m["headline"]
    hm = head["metrics"]
    ci = head["confidence_intervals"]
    stats = ci["statistics"]
    L: list[str] = []
    add = L.append

    add(f"# Evaluation run `{man['run_id']}`")
    add("")
    add(
        "> Every number below was computed by this run from the classifier's actual outputs. "
        "There is no combined headline score; each metric stands alone."
    )
    add("")
    add("## Provenance")
    add("")
    ds = man["dataset"]
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
        + ("  **(includes the locked test split)**" if ds["evaluated_locked_test_split"] else "")
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
            ["documents", "with prediction", "failed", "deferred to review", "auto-decided"],
            [
                [
                    cov["n_docs"],
                    cov["n_with_prediction"],
                    cov["n_failed"],
                    cov["n_deferred_to_review"],
                    cov["n_auto_decided"],
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

    add(
        f"## Headline metrics (tiers {', '.join(head['tiers'])}; "
        f"{hm['coverage']['n_docs']} documents, "
        f"{hm['coverage']['n_groups']} families)"
    )
    add("")
    add(
        f"Point estimate with {int(ci['confidence_level'] * 100)}% percentile interval from "
        f"{ci['n_resamples']} bootstrap resamples of **{ci['unit']}s** "
        f"({ci['n_units']} units, seed "
        f"{ci['seed']}). Families are resampled because documents in a family are correlated."
    )
    add("")
    hr = hm["high_risk"]
    add(
        _table(
            ["metric", "value [CI]"],
            [
                ["Sensitivity level: macro-F1", _ci(stats["level_macro_f1"])],
                ["Data categories: macro-F1", _ci(stats["category_macro_f1"])],
                ["High-risk: recall", _ci(stats["high_risk_recall"])],
                ["High-risk: precision", _ci(stats["high_risk_precision"])],
                ["High-risk: false-positive rate", _f(hr["false_positive_rate"])],
            ],
        )
    )
    add("")
    add(f"Macro convention: {hm['level']['macro_convention']}.")
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
            ["level", "precision", "recall", "F1", "support", "predicted"],
            [
                [k, v["precision"], v["recall"], v["f1"], v["support"], v["predicted"]]
                for k, v in lv["per_class"].items()
            ],
        )
    )
    add("")
    o = lv["ordinal_errors"]
    add(
        f"macro P/R/F1: {_f(lv['macro']['precision'])} / {_f(lv['macro']['recall'])} / "
        f"{_f(lv['macro']['f1'])}; micro F1 {_f(lv['micro']['f1'])}; "
        f"accuracy {_f(lv['accuracy'])}. "
        f"Under-classification {_f(o['under_classification_rate'])} "
        f"(severe {_f(o['severe_under_classification_rate'])}), over-classification "
        f"{_f(o['over_classification_rate'])}."
    )
    add("")

    add("### Data categories")
    add("")
    cat = hm["categories"]
    add(
        _table(
            ["category", "precision", "recall", "F1", "support", "TP", "FP", "FN", "TN"],
            [
                [
                    k,
                    v["precision"],
                    v["recall"],
                    v["f1"],
                    v["support"],
                    v["tp"],
                    v["fp"],
                    v["fn"],
                    v["tn"],
                ]
                for k, v in cat["per_label"].items()
            ],
        )
    )
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
                [
                    hr["tp"],
                    hr["fp"],
                    hr["fn"],
                    hr["tn"],
                    hr["precision"],
                    hr["recall"],
                    hr["f1"],
                    hr["false_positive_rate"],
                    hr["prevalence"],
                ]
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
            "documents on "
            "their provisional label. Alternative views:"
        )
        add("")
        add(
            _table(
                ["view", "level macro-F1", "category macro-F1", "high-risk recall", "documents"],
                [
                    [
                        name,
                        v["level_macro_f1"],
                        v["category_macro_f1"],
                        v["high_risk_recall"],
                        v["n_docs"],
                    ]
                    for name, v in dv.items()
                    if isinstance(v, dict)
                ],
            )
        )
        add("")
        add("The perfect-reviewer row is HYPOTHETICAL and is not a measured result.")
    add("")

    add("## Slices")
    add("")
    add(
        "Computed over all tiers (so T5 appears here). `small` marks slices with fewer documents "
        "than the small-sample threshold; macro-F1 covers only labels with support in the slice."
    )
    for field, values in m["slices"].items():
        add("")
        add(f"**by {field}**")
        add("")
        add(
            _table(
                [
                    field,
                    "docs",
                    "families",
                    "level macro-F1",
                    "category macro-F1",
                    "high-risk recall",
                    "high-risk FPR",
                    "small",
                ],
                [
                    [
                        v_name,
                        v["n_docs"],
                        v["n_groups"],
                        v["level_macro_f1"],
                        v["category_macro_f1"],
                        v["high_risk_recall"],
                        v["high_risk_false_positive_rate"],
                        "yes" if v["small_sample"] else "",
                    ]
                    for v_name, v in values.items()
                ],
            )
        )
    add("")
    sm = head["small_sample_labels"]
    add("## Small-sample caveats")
    add("")
    add(
        f"Headline labels with fewer than {sm['min_support_flag']} gold positives: "
        f"levels {sm['levels'] or 'none'}, categories {sm['categories'] or 'none'}."
    )
    add("")
    lat = m["latency"]["wall_clock"]
    add("## Latency (wall clock per document; not part of the reproducible fingerprint)")
    add("")
    if lat["n"]:
        add(
            _table(
                [
                    "n",
                    "mean ms",
                    *[k.replace("_ms", " ms") for k in lat if k.startswith("p")],
                    "max ms",
                    "total ms",
                ],
                [
                    [
                        lat["n"],
                        lat["mean_ms"],
                        *[lat[k] for k in lat if k.startswith("p")],
                        lat["max_ms"],
                        lat["total_ms"],
                    ]
                ],
            )
        )
    add("")
    add("## Caveats")
    add("")
    add(
        "* The dataset is synthetic, AI-authored and not human-reviewed; "
        "see docs/uc4/dataset-spec.md."
    )
    add(
        "* Documents in a family are variations of one template; treat families as the sample size."
    )
    add(
        "* Precision and accuracy depend on this dataset's class balance, "
        "not production base rates."
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
