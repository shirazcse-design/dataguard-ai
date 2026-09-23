"""`dataguard-uc4 eval fairness-probe` CLI wiring."""

from __future__ import annotations

import json

from app.classification.cli import main


def test_the_locked_test_split_is_refused(capsys):
    assert main(["eval", "fairness-probe", "--split", "test"]) == 2
    assert "not probed" in capsys.readouterr().err


def test_hybrid_mode_with_replay_is_refused_as_a_cache_artifact(capsys):
    """A name-swapped document has a different input hash, so every variant would miss the replay
    cache and escalate to review regardless of the name - a misleading, mechanical false finding,
    not a real one. This combination must be refused, not silently run."""
    assert main(["eval", "fairness-probe", "--mode", "hybrid", "--llm-mode", "replay"]) == 2
    err = capsys.readouterr().err
    assert "cache" in err and "record" in err and "foundry" in err
    assert main(["eval", "fairness-probe", "--mode", "llm", "--llm-mode", "replay"]) == 2


def test_a_real_run_on_dev_prints_a_well_formed_report_and_writes_the_out_file(tmp_path, capsys):
    out = tmp_path / "fairness.json"
    code = main(["eval", "fairness-probe", "--split", "dev", "--mode", "rules", "--out", str(out)])
    report = json.loads(out.read_text())
    assert report["mode"] == "rules"
    assert report["n_probed"] + report["n_skipped_no_name_found"] > 0
    assert json.loads(capsys.readouterr().out) == report
    # exit code reflects whether any document actually changed on a name swap alone
    flagged = sum(1 for d in report["documents"] if d["n_changed"])
    assert code == (1 if flagged else 0)
