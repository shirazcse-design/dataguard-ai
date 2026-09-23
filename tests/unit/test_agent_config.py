"""Batch Triage Agent configuration: `config/agent/agent.v1.yaml` loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.config import ALLOWED_TOOL_NAMES, load_agent_config
from app.classification.config_loader import ConfigError


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"test setup: {old!r} not found in {path.name}"
    path.write_text(text.replace(old, new, 1))


def test_real_agent_config_loads():
    cfg, digest = load_agent_config()
    assert cfg.agent_config_version == "1.0.0"
    assert cfg.planner_tier in ("small", "mid", "large")
    assert 1 <= cfg.max_steps_per_document <= 20
    assert set(cfg.allowed_tools) <= ALLOWED_TOOL_NAMES
    assert cfg.timeout_s > 0
    assert len(digest) == 64  # sha256 hex


def test_an_unimplemented_tool_name_is_rejected(config_copy):
    _edit(
        config_copy / "agent" / "agent.v1.yaml",
        "allowed_tools: [classify_document, lookup_taxonomy_definition, request_human_review]",
        "allowed_tools: [classify_document, delete_everything]",
    )
    with pytest.raises(ConfigError, match="unimplemented tool"):
        load_agent_config(config_copy)


def test_a_duplicate_allowed_tool_is_rejected(config_copy):
    _edit(
        config_copy / "agent" / "agent.v1.yaml",
        "allowed_tools: [classify_document, lookup_taxonomy_definition, request_human_review]",
        "allowed_tools: [classify_document, classify_document]",
    )
    with pytest.raises(ConfigError, match="duplicate"):
        load_agent_config(config_copy)


def test_a_non_semver_version_is_rejected(config_copy):
    _edit(
        config_copy / "agent" / "agent.v1.yaml",
        "agent_config_version: 1.0.0",
        "agent_config_version: v1",
    )
    with pytest.raises(ConfigError, match="semantic version"):
        load_agent_config(config_copy)


def test_max_steps_out_of_range_is_rejected_by_the_schema(config_copy):
    _edit(
        config_copy / "agent" / "agent.v1.yaml",
        "max_steps_per_document: 6",
        "max_steps_per_document: 0",
    )
    with pytest.raises(ConfigError, match="schema validation failed"):
        load_agent_config(config_copy)


def test_an_unknown_planner_tier_is_rejected_by_the_schema(config_copy):
    _edit(config_copy / "agent" / "agent.v1.yaml", "planner_tier: mid", "planner_tier: huge")
    with pytest.raises(ConfigError, match="schema validation failed"):
        load_agent_config(config_copy)


def test_a_missing_config_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_agent_config(tmp_path)
