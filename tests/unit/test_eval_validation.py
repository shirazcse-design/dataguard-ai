"""The harness-validation suite: it must pass on the real harness AND be able to fail."""

from __future__ import annotations

import json

import pytest

from app.classification.cli import main
from evals.classification import metrics as metrics_module
from evals.classification.dataset.build import load_documents, load_manifest
from evals.classification.dataset.schema import SPLIT_NAMES
from evals.classification.validation import (
    HarnessValidation,
    expected_constant_prediction,
    render_validation_report,
    validate_harness,
    validation_to_json,
)
from tests.helpers import mkdoc

P, I, C, H = "PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_CONFIDENTIAL"  # noqa: E741


@pytest.fixture(scope="module")
def by_split():
    docs = load_documents()
    return {s: [d for d in docs if d.split == s] for s in SPLIT_NAMES}


@pytest.fixture(scope="module")
def real_validation(bundle, by_split):
    return validate_harness(by_split, bundle, load_manifest())


def test_the_harness_passes_its_own_validation_on_the_real_dataset(real_validation):
    failures = [c for c in real_validation.checks if not c.passed]
    assert failures == [], [
        (c.suite, c.split, c.name, c.expected, c.observed) for c in failures[:5]
    ]
    assert real_validation.ok and len(real_validation.checks) > 300


def test_validation_covers_every_suite_on_every_split(real_validation):
    suites = {c.suite.split("/")[0] for c in real_validation.checks}
    assert suites == {"oracle", "majority", "random", "robustness"}
    for split in SPLIT_NAMES:
        for suite in (
            "oracle/headline",
            "oracle/all_tiers",
            "majority/headline",
            "random/all_tiers",
        ):
            assert any(c.suite == suite and c.split == split for c in real_validation.checks), (
                suite,
                split,
            )
    assert {(r["baseline"], r["split"]) for r in real_validation.runs} == {
        (b, s) for b in ("oracle", "majority", "random") for s in SPLIT_NAMES
    }


def test_oracle_runs_are_perfect_in_the_recorded_summaries(real_validation):
    for r in real_validation.runs:
        if r["baseline"] == "oracle":
            assert r["level_macro_f1"] == r["category_macro_f1"] == r["high_risk_recall"] == 1.0


def test_validation_can_fail_a_broken_metric_is_detected(bundle, by_split, monkeypatch):
    """Sabotage the F1 formula: the validation suite must notice."""
    real = metrics_module._prf

    def broken(tp, predicted, support):
        out = real(tp, predicted, support)
        out["f1"] = None if out["f1"] is None else out["f1"] * 0.9
        return out

    monkeypatch.setattr(metrics_module, "_prf", broken)
    result = validate_harness(
        {"train": by_split["train"], "test": by_split["test"]}, bundle, load_manifest()
    )
    assert not result.ok and any("F1" in c.name for c in result.checks if not c.passed)


def test_expected_constant_prediction_matches_hand_arithmetic(bundle):
    """Hand-worked: gold levels [P, I, H, H]; categories [], [], [PHI], [PHI, PII]; predict H + [PHI]."""
    docs = [
        mkdoc("a", level=P), mkdoc("b", level=I),
        mkdoc("c", level=H, cats=["PHI"]), mkdoc("d", level=H, cats=["PHI", "PII"]),
    ]  # fmt: skip
    e = expected_constant_prediction(docs, H, ["PHI"], bundle)
    assert e["level_accuracy"] == 0.5 and e["level_micro_f1"] == 0.5
    assert e["level_macro_f1"] == pytest.approx((0 + 0 + 4 / 6) / 3)  # CONFIDENTIAL has no support
    assert e["level_macro_precision"] == pytest.approx((0 + 0 + 0.5) / 3)
    assert e["level_macro_recall"] == pytest.approx(1 / 3)
    assert (e["under"], e["severe_under"], e["over"]) == (0.0, 0.0, 0.5)
    assert e["category_macro_f1"] == pytest.approx((4 / 6 + 0) / 2)  # PHI and PII are supported
    assert e["category_macro_recall"] == pytest.approx(0.5)
    assert e["category_exact_match"] == 0.25
    assert e["hr"] == {
        "tp": 2,
        "fp": 2,
        "fn": 0,
        "tn": 0,
        "recall": 1.0,
        "precision": 0.5,
        "fpr": 1.0,
    }


def test_expected_high_risk_uses_the_config_lists_directly(bundle):
    docs = [mkdoc("a", level=I, cats=[]), mkdoc("b", level=I, cats=["SOURCE_CODE"], group="g")]
    assert expected_constant_prediction(docs, I, [], bundle)["hr"]["tp"] == 0
    e = expected_constant_prediction(docs, I, ["PII"], bundle)  # predicting a high-risk category
    assert e["hr"]["fp"] == 2 and e["hr"]["fpr"] == 1.0


def test_check_helpers():
    v = HarnessValidation()
    v.eq("s", "t", "float ok", 0.5, 0.5 + 1e-12)
    v.eq("s", "t", "float bad", 0.5, 0.6)
    v.eq("s", "t", "none ok", None, None)
    v.eq("s", "t", "none vs value", None, 0.0)
    v.eq("s", "t", "exact", {"a": 1}, {"a": 1})
    v.within("s", "t", "inside", 0.5, 0.52, 0.02)
    v.within("s", "t", "outside", 0.5, 0.9, 0.02)
    v.within("s", "t", "missing", 0.5, None, 0.02)
    assert [c.passed for c in v.checks] == [True, False, True, False, True, True, False, False]
    assert not v.ok


def test_report_and_json_render_from_the_results(real_validation):
    env = {"commit": "abc", "dirty": False, "python": "3.12", "numpy": "x", "scikit_learn": "y"}
    text = render_validation_report(real_validation, load_manifest(), env)
    n = len(real_validation.checks)
    assert f"**Result: PASS** - {n} of {n} checks passed." in text
    assert "## Failures\n\nNone." in text and "Limits of this validation" in text
    assert all(r["run_id"] in text for r in real_validation.runs)
    data = validation_to_json(real_validation)
    assert (
        data["ok"] and data["n_checks"] == n and data["n_passed"] == n and len(data["runs"]) == 12
    )


def test_report_lists_failures():
    v = HarnessValidation()
    v.eq("oracle/headline", "test", "level accuracy == 1", 1.0, 0.9)
    text = render_validation_report(v, load_manifest(), {})
    assert "**Result: FAIL**" in text and "level accuracy == 1" in text


# ---- CLI ------------------------------------------------------------------------------------
def test_cli_eval_run_writes_artifacts_and_prints_the_run_id(tmp_path, capsys):
    assert (
        main(
            ["eval", "run", "--classifier", "oracle", "--split", "dev", "--runs-dir", str(tmp_path)]
        )
        == 0
    )
    out = capsys.readouterr().out
    run_id = next(line.split(": ")[1] for line in out.splitlines() if line.startswith("run_id"))
    assert (tmp_path / run_id / "report.md").exists() and "level macro-F1" in out and "1.000" in out


@pytest.mark.parametrize("clf", ["majority", "random"])
def test_cli_eval_run_other_baselines(tmp_path, capsys, clf):
    assert (
        main(
            [
                "eval",
                "run",
                "--classifier",
                clf,
                "--split",
                "dev,calibration",
                "--seed",
                "3",
                "--runs-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert clf in capsys.readouterr().out
    manifest = json.loads(next(tmp_path.glob("*/run_manifest.json")).read_text())
    assert (
        manifest["dataset"]["splits_evaluated"] == ["calibration", "dev"]
        and "func" not in manifest["cli_args"]
    )


def test_cli_warns_when_touching_the_locked_test_split(tmp_path, capsys):
    assert (
        main(
            [
                "eval",
                "run",
                "--classifier",
                "oracle",
                "--split",
                "test",
                "--runs-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert "locked test split" in capsys.readouterr().err


def test_cli_rejects_unknown_split(tmp_path, capsys):
    assert (
        main(
            [
                "eval",
                "run",
                "--classifier",
                "oracle",
                "--split",
                "dev,nonsense",
                "--runs-dir",
                str(tmp_path),
            ]
        )
        == 2
    )
    assert "unknown split" in capsys.readouterr().err and list(tmp_path.iterdir()) == []


def test_cli_validate_harness_writes_results_and_exits_zero(tmp_path, capsys):
    assert main(["eval", "validate-harness", "--out-dir", str(tmp_path)]) == 0
    assert "checks passed" in capsys.readouterr().out
    assert "**Result: PASS**" in (tmp_path / "harness-validation.md").read_text()
    assert json.loads((tmp_path / "harness-validation.json").read_text())["ok"] is True


def test_cli_validate_harness_exits_nonzero_when_the_harness_is_broken(
    tmp_path, capsys, monkeypatch
):
    real = metrics_module._prf

    def broken(tp, predicted, support):
        out = real(tp, predicted, support)
        out["recall"] = None if out["recall"] is None else out["recall"] * 0.5
        return out

    monkeypatch.setattr(metrics_module, "_prf", broken)
    assert main(["eval", "validate-harness", "--out-dir", str(tmp_path)]) == 4
    assert "FAIL" in capsys.readouterr().err
    assert "**Result: FAIL**" in (tmp_path / "harness-validation.md").read_text()
