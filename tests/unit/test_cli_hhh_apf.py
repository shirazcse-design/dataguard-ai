"""`dataguard-uc4 eval hhh-apf` CLI wiring."""

from __future__ import annotations

import json

from app.classification.cli import main


def test_the_locked_test_split_is_refused(capsys):
    assert main(["eval", "hhh-apf", "--split", "test"]) == 2
    assert "not used" in capsys.readouterr().err


def test_a_replayed_run_produces_a_well_formed_report_without_a_trace_file(tmp_path, capsys):
    out = tmp_path / "hhh_apf.json"
    code = main(["eval", "hhh-apf", "--split", "dev", "--llm-mode", "replay", "--out", str(out)])
    assert code == 0
    report = json.loads(out.read_text())
    assert report["splits"] == ["dev"] and report["n_documents"] > 0
    assert report["spans_supplied"] is False
    assert report["hhh"]["helpful"]["score"] is not None
    assert report["hhh"]["honest"]["score"] is None  # no trace file supplied
    assert report["apf"]["dimensions"]["effectiveness"]["score"] is not None
    assert json.loads(capsys.readouterr().out) == report


def test_supplying_a_trace_file_fills_in_the_remaining_sub_scores(tmp_path):
    spans = tmp_path / "spans.jsonl"
    assert main(["eval", "run", "--classifier", "hybrid", "--split", "dev", "--llm-mode", "replay",
                 "--trace-out", str(spans)]) == 0  # fmt: skip
    out = tmp_path / "hhh_apf.json"
    assert main(["eval", "hhh-apf", "--split", "dev", "--llm-mode", "replay",
                 "--spans", str(spans), "--out", str(out)]) == 0  # fmt: skip
    report = json.loads(out.read_text())
    assert report["spans_supplied"] is True
    assert report["hhh"]["honest"]["score"] is not None
    assert report["apf"]["dimensions"]["efficiency"]["score"] is not None
    assert report["apf"]["dimensions"]["reliability"]["score"] is not None
    assert report["apf"]["composite_note"] is None  # every dimension present
