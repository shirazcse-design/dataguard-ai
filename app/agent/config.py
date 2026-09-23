"""Batch Triage Agent configuration (config/agent/agent.v1.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from app.classification.config_loader import ConfigError, default_config_dir, read_yaml
from app.classification.schemas.common import SEMVER_RE, StrictModel

AGENT_FILE = "agent/agent.v1.yaml"
ALLOWED_TOOL_NAMES = {"classify_document", "lookup_taxonomy_definition", "request_human_review"}


class AgentConfig(StrictModel):
    agent_config_version: str
    planner_tier: Literal["small", "mid", "large"]
    max_steps_per_document: int = Field(ge=1, le=20)
    allowed_tools: list[str] = Field(min_length=1)
    timeout_s: float = Field(gt=0)

    def _validate(self) -> None:
        if not SEMVER_RE.match(self.agent_config_version):
            raise ValueError("agent_config_version must be a semantic version")
        unknown = set(self.allowed_tools) - ALLOWED_TOOL_NAMES
        if unknown:
            raise ValueError(f"allowed_tools names an unimplemented tool: {sorted(unknown)}")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("allowed_tools has a duplicate")


def load_agent_config(config_dir: Path | str | None = None) -> tuple[AgentConfig, str]:
    base = Path(config_dir) if config_dir is not None else default_config_dir()
    path = base / AGENT_FILE
    data, digest = read_yaml(path)
    try:
        cfg = AgentConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc
    try:
        cfg._validate()
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return cfg, digest
