"""`dataguard-uc4 guardrails azure-check` / `second-opinion` CLI wiring."""

from __future__ import annotations

import json

from app.classification.cli import main


def test_azure_check_without_credentials_is_a_usage_error_not_a_traceback(monkeypatch, capsys):
    monkeypatch.delenv("DATAGUARD_CONTENT_SAFETY_ENDPOINT", raising=False)
    monkeypatch.delenv("DATAGUARD_CONTENT_SAFETY_API_KEY", raising=False)
    assert main(["guardrails", "azure-check"]) == 2
    err = capsys.readouterr().err
    assert "DATAGUARD_CONTENT_SAFETY_ENDPOINT" in err


def test_azure_check_never_prints_the_api_key(monkeypatch, capsys):
    monkeypatch.setenv(
        "DATAGUARD_CONTENT_SAFETY_ENDPOINT", "https://127.0.0.1:1"
    )  # nothing listens
    monkeypatch.setenv("DATAGUARD_CONTENT_SAFETY_API_KEY", "super-secret-key-value")
    main(["guardrails", "azure-check"])
    out, err = capsys.readouterr()
    assert "super-secret-key-value" not in out and "super-secret-key-value" not in err


def test_second_opinion_refuses_the_locked_test_split(capsys):
    assert main(["guardrails", "second-opinion", "--split", "test"]) == 2
    assert "not used" in capsys.readouterr().err


def test_second_opinion_runs_in_custom_only_mode_without_credentials(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DATAGUARD_CONTENT_SAFETY_ENDPOINT", raising=False)
    monkeypatch.delenv("DATAGUARD_CONTENT_SAFETY_API_KEY", raising=False)
    out = tmp_path / "second_opinion.json"
    code = main(
        [
            "guardrails", "second-opinion", "--split", "dev", "--llm-mode", "replay",
            "--max-claims", "5", "--out", str(out),
        ]
    )  # fmt: skip
    assert code == 0
    report = json.loads(out.read_text())
    assert report["second_opinion_available"] is False
    assert report["injection"]["agreement_rate"] is None
    assert all(r["azure_flagged"] is None for r in report["injection"]["rows"])
    assert report["groundedness"]["n_claims"] <= 5
    assert all(r["azure_grounded"] is None for r in report["groundedness"]["rows"])
    assert json.loads(capsys.readouterr().out) == report
