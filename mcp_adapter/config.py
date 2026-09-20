"""Configuration for the MCP adapter (`config/mcp/mcp.v1.yaml`)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator

from app.classification.config_loader import ConfigError, default_config_dir, read_yaml
from app.classification.schemas.common import SEMVER_RE, StrictModel

MCP_FILE = "mcp/mcp.v1.yaml"
TIER_RANK = {"none": 0, "small": 1, "mid": 2, "large": 3}


class CallerPolicy(StrictModel):
    """What one allowlisted agent may ask for. Requests can only lower these caps."""

    max_llm_tier: Literal["none", "small", "mid", "large"]
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_latency_ms: int | None = Field(default=None, ge=1)
    allow_evidence: bool = False


class McpConfig(StrictModel):
    mcp_version: str
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    max_content_bytes: int = Field(ge=1000)
    default_include_evidence: bool = False
    callers: dict[str, CallerPolicy] = Field(default_factory=dict)

    @field_validator("mcp_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError(f"must be a semantic version, got {v!r}")
        return v

    @field_validator("callers")
    @classmethod
    def _caller_ids(cls, v: dict[str, CallerPolicy]) -> dict[str, CallerPolicy]:
        for cid in v:
            if not (1 <= len(cid) <= 200):
                raise ValueError("caller ids must be 1-200 characters")
        return v


def load_mcp_config(config_dir: Path | str | None = None) -> tuple[McpConfig, str]:
    base = Path(config_dir) if config_dir is not None else default_config_dir()
    path = base / MCP_FILE
    data, digest = read_yaml(path)
    try:
        return McpConfig.model_validate(data), digest
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc
