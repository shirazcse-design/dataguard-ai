"""Rules configuration schema and loader (config/rules/rules.v1.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, field_validator, model_validator

from app.classification.config_loader import ConfigError, default_config_dir, read_yaml
from app.classification.policy import TaxonomyPolicy
from app.classification.schemas.common import SEMVER_RE, StrictModel

RULES_FILE = "rules/rules.v1.yaml"

REQUIRED_LEXICONS = {
    "pii_id_keywords",
    "passport_keywords",
    "dob_keywords",
    "pii_field_labels",
    "card_keywords",
    "routing_keywords",
    "account_keywords",
    "ssn_negative_keywords",
    "icd_context_keywords",
    "clinical_terms",
    "financial_results_terms",
    "nonpublic_terms",
    "published_terms",
    "mna_transaction_terms",
    "mna_secrecy_terms",
    "mna_markers",
    "mna_announced_terms",
    "ip_markers",
    "ip_negative_terms",
    "ip_novelty_terms",
    "ts_markers",
    "ts_negative_terms",
    "ts_secrecy_terms",
    "secret_key_names",
    "secret_key_names_exact",
    "placeholder_values",
    "env_lookups",
}


class EmitConfig(StrictModel):
    default: str = "strong"
    overrides: dict[str, str] = Field(default_factory=dict)


class KnownDummy(StrictModel):
    ssn: list[str]
    pan: list[str]
    aws_access_key: list[str]


class ContextConfig(StrictModel):
    window_chars: int = Field(ge=1)
    header_context_chars: int = Field(ge=1)
    placeholder_terms: list[str]
    test_terms: list[str]
    documentation_terms: list[str]
    filename_negative_tokens: list[str]
    known_dummy: KnownDummy


class ExistingLabelsConfig(StrictModel):
    banner_max_line_chars: int = Field(ge=1)
    banner_lines: int = Field(ge=1)
    banner_min_marking_fraction: float = Field(gt=0, le=1)
    levels: dict[str, list[str]]


class CodeConfig(StrictModel):
    code_extensions: list[str]
    code_filenames: list[str]
    min_code_lines: int = Field(ge=1)
    min_density: float = Field(gt=0, le=1)
    oss_license_terms: list[str]
    proprietary_terms: list[str]


class RulesConfig(StrictModel):
    ruleset_version: str
    taxonomy_version: str
    standalone_default_level: str
    max_content_chars: int = Field(ge=1000)
    emit_min_strength: EmitConfig
    context: ContextConfig
    existing_labels: ExistingLabelsConfig
    lexicons: dict[str, list[str]]
    code: CodeConfig

    @field_validator("ruleset_version", "taxonomy_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError(f"must be a semantic version, got {v!r}")
        return v

    @model_validator(mode="after")
    def _lexicons_complete(self) -> RulesConfig:
        missing = REQUIRED_LEXICONS - set(self.lexicons)
        if missing:
            raise ValueError(f"missing lexicons: {sorted(missing)}")
        empty = [k for k, v in self.lexicons.items() if not v]
        if empty:
            raise ValueError(f"empty lexicons: {sorted(empty)}")
        return self

    def emit_min(self, category: str) -> str:
        return self.emit_min_strength.overrides.get(category, self.emit_min_strength.default)


def load_rules_config(
    policy: TaxonomyPolicy, config_dir: Path | str | None = None
) -> tuple[RulesConfig, str]:
    """Load and cross-check the rules config. Returns (config, sha256 of the file)."""
    base = Path(config_dir) if config_dir is not None else default_config_dir()
    path = base / RULES_FILE
    data, digest = read_yaml(path)
    try:
        cfg = RulesConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc
    if cfg.taxonomy_version != policy.taxonomy_version:
        raise ConfigError(
            f"{path}: written against taxonomy {cfg.taxonomy_version} but loaded taxonomy is "
            f"{policy.taxonomy_version}"
        )
    if cfg.standalone_default_level not in policy.level_ids:
        raise ConfigError(
            f"{path}: unknown standalone_default_level {cfg.standalone_default_level!r}"
        )
    for level in cfg.existing_labels.levels:
        if level not in policy.level_ids:
            raise ConfigError(f"{path}: existing_labels references unknown level {level!r}")
    strengths = {"weak", "strong", "definitive"}
    for s in [cfg.emit_min_strength.default, *cfg.emit_min_strength.overrides.values()]:
        if s not in strengths:
            raise ConfigError(f"{path}: unknown strength {s!r}")
    for cat in cfg.emit_min_strength.overrides:
        if cat not in policy.category_ids:
            raise ConfigError(f"{path}: emit_min_strength override for unknown category {cat!r}")
    return cfg, digest


def config_summary(cfg: RulesConfig) -> dict[str, Any]:
    return {
        "ruleset_version": cfg.ruleset_version,
        "standalone_default_level": cfg.standalone_default_level,
        "emit_min_strength": cfg.emit_min_strength.default,
        "max_content_chars": cfg.max_content_chars,
    }
