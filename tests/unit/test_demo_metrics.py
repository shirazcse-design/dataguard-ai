"""The demo dashboard's evaluation numbers come from approved, COMMITTED artifacts only
(`app/demo/metrics.py`) - never computed, never typed into the page.

Each parsed value is cross-checked against the raw text of its source document, so an edit to a
report that the loader misreads fails here rather than on screen.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.demo import metrics as m

REPO = Path(__file__).resolve().parents[2]


def src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_headline_rows_match_the_completion_report_text_exactly():
    text = src(m.COMPLETION_REPORT)
    rows = {r["split"]: r for r in m.headline()}
    assert list(rows) == ["locked test", "calibration", "dev"]
    for r in rows.values():
        strict = r["strict_level_f1"]
        # the exact interval string appears in the report row for that split
        line = next(line for line in text.splitlines() if line.startswith(f"| {r['split']} ("))
        assert f"{strict['value']:.3f} [{strict['lo']:.3f}, {strict['hi']:.3f}]" in line
        assert f"{r['category_f1']['value']:.3f}" in line
    locked = rows["locked test"]
    assert locked["high_risk_recall"] == {"value": 1.0, "numerator": 111, "denominator": 111}


def test_the_values_the_dashboard_spec_quotes_are_the_canonical_ones():
    rows = {r["split"]: r for r in m.headline()}
    assert rows["locked test"]["strict_level_f1"] == {"value": 0.884, "lo": 0.758, "hi": 0.98}
    assert rows["locked test"]["lenient_level_f1"] == {"value": 1.0, "lo": 1.0, "hi": 1.0}
    assert rows["locked test"]["category_f1"]["value"] == 0.997
    assert rows["calibration"]["strict_level_f1"] == {"value": 0.859, "lo": 0.685, "hi": 1.0}
    assert rows["dev"]["strict_level_f1"] == {"value": 0.87, "lo": 0.631, "hi": 1.0}


def test_locked_test_confusion_matrix_is_consistent_with_its_own_per_level_support():
    d = m.locked_test_detail()
    cm = d["confusion_matrix"]["rows"]
    support = {r["level"]: r["support"] for r in d["per_level"]}
    for level, row in cm.items():
        assert sum(row.values()) == support[level]
    assert cm["CONFIDENTIAL"]["INTERNAL"] == 19
    assert d["run_id"] == "hybrid-test-20260921T071856Z-789a620e"
    hr = d["high_risk"]
    assert (hr["TP"], hr["FN"]) == (111, 0)
    assert len(d["per_category"]) == 8


def test_the_gate_threshold_comes_from_config_not_the_page():
    assert m.gates()["level_macro_f1"] == 0.85


def test_status_is_derived_from_the_completion_report_and_stays_honest():
    st = m.status()
    assert st["implemented"] and st["evaluated"]
    assert st["human_review"] == "one reviewer"
    assert st["second_independent_review"] == "pending"
    assert st["independent_validation"] == "pending"
    assert st["strict_gate_met"] is False and st["locked_test_strict_lower_bound"] == 0.758
    assert st["lenient_gate_met"] is True
    closed = [i["item"] for i in st["open_items"] if i["closed"]]
    assert any("5 dev errors" in i for i in closed)  # A39
    assert any("second independent human review" in i for i in (
        x["item"] for x in st["open_items"] if not x["closed"]))  # fmt: skip


def test_agent_eval_task_completion_is_the_recorded_helpful_rate_and_none_stays_none():
    a = m.agent_eval()
    assert a["task_completion"] == 1.0 and a["safety_invariant_compliance"] == 1.0
    assert a["hhh"]["honest"] is None and a["apf"]["reliability"] is None  # not computed != 0


def test_foundry_evals_are_read_from_the_responsible_ai_record():
    f = m.foundry_evals()
    assert {r["criterion"]: (r["passed"], r["total"]) for r in f["classifier"]} == {
        "level_exact": (102, 107), "level_acceptable": (102, 107), "no_missed_high_risk": (107, 107),
    }  # fmt: skip
    assert all((r["passed"], r["total"]) == (107, 107) for r in f["agent"])
    assert f["matched_local"] is True


def _copy_sources(tmp_path: Path) -> Path:
    for rel in (m.COMPLETION_REPORT, m.LOCKED_TEST_REPORT, m.AGENT_EVAL, m.GATES, m.RESPONSIBLE_AI):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, tmp_path / rel)
    return tmp_path


def test_a_changed_results_header_is_an_error_not_a_guess(tmp_path):
    root = _copy_sources(tmp_path)
    p = root / m.COMPLETION_REPORT
    p.write_text(
        p.read_text().replace("| strict level F1 |", "| level F1 (strict) |"), encoding="utf-8"
    )
    with pytest.raises(m.ArtifactError, match="header changed"):
        m.headline(root)


def test_a_missing_artifact_is_an_error(tmp_path):
    root = _copy_sources(tmp_path)
    (root / m.AGENT_EVAL).unlink()
    with pytest.raises(m.ArtifactError, match="missing"):
        m.agent_eval(root)


def test_a_malformed_interval_is_an_error(tmp_path):
    root = _copy_sources(tmp_path)
    p = root / m.COMPLETION_REPORT
    p.write_text(
        p.read_text().replace("0.884 [0.758, 0.980]", "0.884 (0.758-0.980)"), encoding="utf-8"
    )
    with pytest.raises(m.ArtifactError, match="value \\[lo, hi\\]"):
        m.headline(root)


def test_a_reopened_item_turns_the_status_back_to_open(tmp_path):
    root = _copy_sources(tmp_path)
    p = root / m.COMPLETION_REPORT
    text = p.read_text()
    assert "~~The **5 dev errors**" in text
    p.write_text(text.replace("~~The **5 dev errors**", "The **5 dev errors**"), encoding="utf-8")
    items = {i["item"][:14]: i["closed"] for i in m.status(root)["open_items"]}
    assert items["The 5 dev erro"] is False


def test_a_closed_items_closure_note_is_kept_separate_from_its_text():
    items = {i["item"]: i for i in m.status()["open_items"]}
    dev = next(v for k, v in items.items() if k.startswith("The 5 dev errors"))
    assert dev["closed"] and dev["note"] == "Closed 2026-09-26 (A39)"
    assert "Closed" not in dev["item"]
    assert items["A second independent human review of the gold labels"]["note"] is None
