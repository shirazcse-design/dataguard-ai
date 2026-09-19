"""Load and validate the version-controlled UC4 configuration.

Fails fast: any schema or cross-file inconsistency raises `ConfigError` with the offending file
named, so a bad taxonomy can never be used partially [architecture section 19].
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .policy import TaxonomyPolicy
from .schemas import EvalConfig, HighRiskConfig, TaxonomyConfig

TAXONOMY_FILE = "taxonomy/taxonomy.v1.yaml"
HIGH_RISK_FILE = "taxonomy/high_risk.v1.yaml"
EVAL_FILE = "eval/eval.v1.yaml"

CONFIG_DIR_ENV = "DATAGUARD_CONFIG_DIR"


class ConfigError(Exception):
    """Raised when configuration is missing, malformed or inconsistent."""


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys (PyYAML silently keeps the last one)."""


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False):
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def default_config_dir() -> Path:
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override)
    # app/classification/config_loader.py -> repo root is three levels up.
    return Path(__file__).resolve().parents[2] / "config"


@dataclass(frozen=True)
class ConfigBundle:
    """Validated configuration plus provenance hashes for run manifests."""

    taxonomy: TaxonomyConfig
    high_risk: HighRiskConfig
    eval: EvalConfig
    config_dir: Path
    file_hashes: dict[str, str] = field(default_factory=dict)  # relative path -> sha256 of bytes

    @property
    def policy(self) -> TaxonomyPolicy:
        return TaxonomyPolicy(self.taxonomy, self.high_risk)

    def versions(self) -> dict[str, str]:
        return {
            "taxonomy": self.taxonomy.taxonomy_version,
            "high_risk": self.high_risk.high_risk_version,
            "eval": self.eval.eval_config_version,
        }


def _read_yaml(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    try:
        data = yaml.load(raw, Loader=_StrictLoader)  # noqa: S506 - _StrictLoader is a SafeLoader
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return data, hashlib.sha256(raw).hexdigest()


def _validate(model_cls, data: dict[str, Any], path: Path):
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc


def load_config(config_dir: Path | str | None = None) -> ConfigBundle:
    """Load, validate and cross-check the taxonomy, high-risk and eval configuration."""
    base = Path(config_dir) if config_dir is not None else default_config_dir()

    tax_path, hr_path, ev_path = base / TAXONOMY_FILE, base / HIGH_RISK_FILE, base / EVAL_FILE
    tax_data, tax_hash = _read_yaml(tax_path)
    hr_data, hr_hash = _read_yaml(hr_path)
    ev_data, ev_hash = _read_yaml(ev_path)

    taxonomy = _validate(TaxonomyConfig, tax_data, tax_path)
    high_risk = _validate(HighRiskConfig, hr_data, hr_path)
    eval_cfg = _validate(EvalConfig, ev_data, ev_path)

    if high_risk.taxonomy_version != taxonomy.taxonomy_version:
        raise ConfigError(
            f"{hr_path}: written against taxonomy {high_risk.taxonomy_version} but loaded "
            f"taxonomy is {taxonomy.taxonomy_version}"
        )
    level_ids = {lv.id for lv in taxonomy.levels}
    category_ids = {c.id for c in taxonomy.categories}
    unknown_cats = sorted(set(high_risk.categories) - category_ids)
    if unknown_cats:
        raise ConfigError(f"{hr_path}: unknown categories {unknown_cats}")
    unknown_levels = sorted(set(high_risk.levels) - level_ids)
    if unknown_levels:
        raise ConfigError(f"{hr_path}: unknown levels {unknown_levels}")

    return ConfigBundle(
        taxonomy=taxonomy,
        high_risk=high_risk,
        eval=eval_cfg,
        config_dir=base,
        file_hashes={TAXONOMY_FILE: tax_hash, HIGH_RISK_FILE: hr_hash, EVAL_FILE: ev_hash},
    )
