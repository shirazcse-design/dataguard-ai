"""The generated ML baseline report."""

from __future__ import annotations

import pytest

from app.classification.cli import main


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("ml") / "ml-baseline.md"
    assert main(["ml", "report", "--out", str(out)]) == 0
    return out.read_text()


def test_report_states_provenance_protocol_and_the_lock(report):
    assert (
        "second independent review pending" in report
        and "not independently human-validated" in report
    )
    assert "The locked test split was not read" in report and "ml-plan.md" in report
    assert (
        "fitted on **train**" in report
        and "calibrated on **calibration**" in report
        and "evaluated on dev" in report
    )
    assert (
        "independent families" in report
        and "no operating point has been chosen" in report.lower()
        or "No operating point" in report
    )


def test_report_contains_every_required_section(report):
    for heading in (
        "## Headline: held-out dev split", "## Why the numbers look this way: template memorisation",
        "### Sensitivity level", "### Data categories", "### High-risk", "## Calibration (held-out dev)",
        "## ML vs Rules on the same dev documents", "## Complementarity", "## Threshold sweep on dev",
        "## Ablation: no filename block", "## In-sample and calibration-split numbers (NOT held-out)",
        "## Latency", "## Hard negatives", "## Error analysis",
    ):  # fmt: skip
        assert heading in report, heading
    for needle in (
        "Confusion",
        "ECE",
        "Brier",
        "SMALL_SAMPLE",
        "random kfold leaky",
        "grouped kfold honest",
        "in sample",
    ):
        assert needle.lower() in report.lower(), needle


def test_report_warns_about_misleading_readings(report):
    assert "Read with care" in report and "level-driven" in report
    assert "optimistic by construction" in report
    assert "not like-for-like" in report or "flatters Rules" in report


def test_report_compares_ml_with_frozen_rules(report):
    assert "Rules 1.0.3 (frozen)" in report and "ML (this model)" in report
