"""`dataguard-uc4 agent triage`/`agent eval` CLI wiring. `--agent-mode mock` (the default) needs no
Foundry credentials, so these run against the real classifier and the real config, offline."""

from __future__ import annotations

import json

from app.classification.cli import main


def test_the_locked_test_split_is_refused(capsys):
    assert main(["agent", "triage", "--split", "test"]) == 2
    assert "not used" in capsys.readouterr().err


def test_triage_produces_a_report_covering_every_requested_document(tmp_path, capsys):
    out = tmp_path / "report.json"
    code = main(
        [
            "agent",
            "triage",
            "--split",
            "dev",
            "--limit",
            "3",
            "--llm-mode",
            "off",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    report = json.loads(out.read_text())
    assert report["n_documents"] == 3
    assert len(report["documents"]) == 3
    assert json.loads(capsys.readouterr().out) == report
    for doc in report["documents"]:
        # the safety invariant, visible from the outside: no level without an "ok"/"degraded"
        # status, and every document was actually looked at by classify_document.
        assert any(c["tool"] == "classify_document" and c["ok"] for c in doc["tool_calls"])


def test_eval_computes_task_completion_and_hhh_apf_from_a_real_run(tmp_path, capsys):
    out = tmp_path / "eval.json"
    code = main(
        ["agent", "eval", "--split", "dev", "--limit", "5", "--llm-mode", "off", "--out", str(out)]
    )
    assert code == 0
    result = json.loads(out.read_text())
    assert result["n_documents"] == 5
    assert result["hhh"]["helpful"] == 1.0  # every input document was reported
    assert result["hhh"]["harmless"] == 1.0
    assert result["safety_invariant_compliance"]["rate"] == 1.0
    assert result["apf"]["composite"] is not None
    assert json.loads(capsys.readouterr().out) == result


def test_foundry_agent_mode_without_a_deployment_env_var_fails_clearly(capsys, monkeypatch):
    monkeypatch.delenv("DATAGUARD_LLM_DEPLOYMENT_MID", raising=False)
    code = main(
        [
            "agent",
            "triage",
            "--agent-mode",
            "foundry",
            "--split",
            "dev",
            "--limit",
            "1",
            "--llm-mode",
            "off",
        ]
    )
    assert code == 2
    assert "DATAGUARD_LLM_DEPLOYMENT_MID" in capsys.readouterr().err
