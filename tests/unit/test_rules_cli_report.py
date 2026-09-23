"""Rules CLI commands and the generated baseline report."""

from __future__ import annotations

import json

import pytest

from app.classification.cli import main
from evals.classification.dataset.build import load_documents, load_manifest
from evals.classification.lock import DEVELOPMENT_SPLITS


def test_eval_run_supports_the_rules_classifier(tmp_path, capsys):
    assert (
        main(
            ["eval", "run", "--classifier", "rules", "--split", "dev", "--runs-dir", str(tmp_path)]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "high-risk precision" in out and "independent families" in out
    manifest = json.loads(next(tmp_path.glob("*/run_manifest.json")).read_text())
    assert manifest["classifier"]["name"] == "rules"
    assert manifest["classifier"]["params"]["standalone_default_level"] == "INTERNAL"
    assert manifest["dataset"]["evaluated_locked_test_split"] is False
    report = next(tmp_path.glob("*/report.md")).read_text()
    assert "abstained" in report and "Hard negatives (T4)" in report


def test_the_locked_test_split_is_refused_for_rules_too(tmp_path, capsys):
    assert (
        main(
            ["eval", "run", "--classifier", "rules", "--split", "test", "--runs-dir", str(tmp_path)]
        )
        == 2
    )
    assert "locked" in capsys.readouterr().err and list(tmp_path.iterdir()) == []


def test_rules_analyze_refuses_test_and_writes_a_report(tmp_path, capsys):
    assert main(["rules", "analyze", "--split", "test"]) == 2
    assert "train, calibration and dev only" in capsys.readouterr().err
    out = tmp_path / "analysis.md"
    assert main(["rules", "analyze", "--split", "dev", "--out", str(out)]) == 0
    text = out.read_text()
    assert "False negatives" in text and "Hard-negative decoy hits" in text


@pytest.fixture(scope="module")
def baseline_report(tmp_path_factory):
    out = tmp_path_factory.mktemp("rules") / "rules-baseline.md"
    assert main(["rules", "report", "--out", str(out)]) == 0
    return out.read_text()


def test_baseline_report_states_its_provenance_and_the_core_principle(baseline_report):
    t = baseline_report
    assert "second independent review pending" in t and "not independently human-validated" in t
    assert "The locked test split was not read" in t
    assert "No rule match does not mean Public" in t and "Rules never output `PUBLIC`" in t
    assert "development-contaminated" in t and "rules-changelog.md" in t
    assert "independent families" in t


def test_baseline_report_contains_every_requested_section(baseline_report):
    t = baseline_report
    for heading in (
        "## Summary by split", "### Sensitivity level", "### Data categories", "### High-risk",
        "## Abstention and rules coverage", "## Latency", "## Performance by difficulty tier",
        "## Hard negatives (T4)", "## Error analysis",
    ):  # fmt: skip
        assert heading in t, heading
    for needle in ("Confusion matrix", "Under-classification rate", "abstention rate", "rules coverage",
                   "decoy-hit", "p50", "p95", "T1", "T5", "SMALL_SAMPLE"):  # fmt: skip
        assert needle in t, needle
    assert "informational, not a gate" in t and "no operating point has been chosen" in t


def test_baseline_report_reads_only_development_documents(baseline_report):
    n_dev = len(load_documents(splits=list(DEVELOPMENT_SPLITS)))
    assert f"{n_dev} documents" in baseline_report or f"/{n_dev} (" in baseline_report
    total_test = load_manifest()["files"]["test"]["n_docs"]
    full = str(n_dev + total_test)
    # a bare substring match on a small number can coincidentally match inside an unrelated hex
    # string (a commit sha, a hash prefix); require it to appear as a document COUNT, not anywhere.
    assert f"{full} documents" not in baseline_report and f"/{full} (" not in baseline_report
