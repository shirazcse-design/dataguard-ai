"""CLI smoke tests (Phase 0: config validation only)."""

from __future__ import annotations

import json

from app.classification.cli import main


def test_config_validate_ok(capsys):
    assert main(["config", "validate"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and len(out["categories"]) == 8


def test_config_validate_reports_invalid_config(config_copy, capsys):
    (config_copy / "taxonomy/taxonomy.v1.yaml").write_text("not: [valid")
    assert main(["config", "validate", "--config-dir", str(config_copy)]) == 2
    assert "CONFIG INVALID" in capsys.readouterr().err
