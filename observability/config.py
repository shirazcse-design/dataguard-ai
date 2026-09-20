"""Observability configuration (config/observability/observability.v1.yaml)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydantic import Field, ValidationError, field_validator

from app.classification.config_loader import ConfigError, default_config_dir, read_yaml
from app.classification.schemas.common import SEMVER_RE, StrictModel

from .redaction import Redactor, is_forbidden, salt_from_env
from .trace import RandomIds, SeededIds, Sink, Tracer

OBS_FILE = "observability/observability.v1.yaml"


class ObservabilityConfig(StrictModel):
    observability_version: str
    service_name: str
    max_value_chars: int = Field(ge=20, le=500)
    pseudonym_salt_env: str
    allowed_attributes: list[str] = Field(min_length=1)

    @field_validator("observability_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError("observability_version must be a semantic version")
        return v

    @field_validator("allowed_attributes")
    @classmethod
    def _safe(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate allowed attribute")
        bad = [k for k in v if is_forbidden(k) or not k.startswith("dg.")]
        if bad:
            raise ValueError(f"unsafe or non-dg.* attribute keys in the allow-list: {bad}")
        return v


def load_observability_config(
    config_dir: Path | str | None = None,
) -> tuple[ObservabilityConfig, str]:
    base = Path(config_dir) if config_dir is not None else default_config_dir()
    path = base / OBS_FILE
    data, digest = read_yaml(path)
    try:
        return ObservabilityConfig.model_validate(data), digest
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc


def build_tracer(
    cfg: ObservabilityConfig,
    sinks: list[Sink],
    *,
    deterministic_ids: bool = False,
) -> tuple[Tracer, bytes]:
    """A tracer plus the pseudonymisation salt to use for caller ids."""
    tracer = Tracer(
        sinks,
        Redactor(cfg.allowed_attributes, cfg.max_value_chars),
        ids=SeededIds() if deterministic_ids else RandomIds(),
    )
    return tracer, salt_from_env(cfg.pseudonym_salt_env)


def salt_for(cfg: ObservabilityConfig, environ: Mapping[str, str] | None = None) -> bytes:
    return salt_from_env(cfg.pseudonym_salt_env, environ)
