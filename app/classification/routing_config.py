"""Routing configuration schema and loader (routing.v1.yaml, gates.v1.yaml under config/).

A *variant* is `base` with a set of overrides applied (deep merge), then re-validated, so a typo in
an override fails loudly instead of being ignored.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from .config_loader import ConfigError, default_config_dir, read_yaml
from .policy import TaxonomyPolicy
from .schemas.common import SEMVER_RE, StrictModel

ROUTING_FILE = "routing/routing.v1.yaml"
GATES_FILE = "eval/gates.v1.yaml"
TIERS = ("small", "mid", "large")
BUCKET_RANK = {"low": 0, "medium": 1, "high": 2}
STRENGTH_RANK = {"weak": 0, "strong": 1, "definitive": 2}


class RulesStage(StrictModel):
    enabled: bool
    short_circuit: bool
    min_level_strength: Literal["strong", "definitive"]


class MLStage(StrictModel):
    enabled: bool
    short_circuit: bool
    tau: float = Field(ge=0.5, le=1.0)


class LLMStage(StrictModel):
    tier_order: list[str]
    min_confidence: Literal["low", "medium", "high"]
    accept_abstention: bool

    @field_validator("tier_order")
    @classmethod
    def _tiers(cls, v: list[str]) -> list[str]:
        if any(t not in TIERS for t in v):
            raise ValueError(f"tiers must come from {list(TIERS)}")
        if len(set(v)) != len(v):
            raise ValueError("a tier may appear at most once")
        return v


class Budget(StrictModel):
    max_llm_calls: int = Field(ge=0, le=10)


class ConflictCfg(StrictModel):
    level_rank_gap: int = Field(ge=1, le=3)


class FusionCfg(StrictModel):
    rules_floor: bool
    category_floors: bool


class InjectionCfg(StrictModel):
    restrict_downgrade: bool


class VariantConfig(StrictModel):
    rules: RulesStage
    ml: MLStage
    llm: LLMStage
    budget: Budget
    conflict: ConflictCfg
    fusion: FusionCfg
    injection: InjectionCfg

    @model_validator(mode="after")
    def _something_decides(self) -> VariantConfig:
        if not (self.rules.enabled or self.ml.enabled or self.llm.tier_order):
            raise ValueError("at least one stage (rules, ml or an LLM tier) must be enabled")
        return self

    def tiers_used(self) -> list[str]:
        return list(self.llm.tier_order)


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class RoutingConfig(StrictModel):
    routing_version: str
    taxonomy_version: str
    default_variant: str
    base: VariantConfig
    variants: dict[str, dict[str, Any]]

    @field_validator("routing_version", "taxonomy_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError(f"must be a semantic version, got {v!r}")
        return v

    @model_validator(mode="after")
    def _variants_resolve(self) -> RoutingConfig:
        if self.default_variant not in self.variants:
            raise ValueError(f"default_variant {self.default_variant!r} is not a defined variant")
        base = self.base.model_dump()
        for name in self.variants:
            try:
                VariantConfig.model_validate(_merge(base, self.variants[name]))
            except ValidationError as exc:
                raise ValueError(f"variant {name!r} is invalid: {exc}") from exc
        return self

    def variant(self, name: str) -> VariantConfig:
        if name not in self.variants:
            raise KeyError(f"unknown hybrid variant {name!r}; choose from {sorted(self.variants)}")
        return VariantConfig.model_validate(_merge(self.base.model_dump(), self.variants[name]))


class Gates(StrictModel):
    level_macro_f1: float = Field(gt=0, le=1)
    category_macro_f1: float = Field(gt=0, le=1)
    high_risk_recall: float = Field(gt=0, le=1)


class GatesConfig(StrictModel):
    gates_version: str
    gates: Gates
    tool_call_limit_s: float = Field(gt=0)


def load_routing_config(
    policy: TaxonomyPolicy, config_dir: Path | str | None = None
) -> tuple[RoutingConfig, str]:
    base = Path(config_dir) if config_dir is not None else default_config_dir()
    path = base / ROUTING_FILE
    data, digest = read_yaml(path)
    try:
        cfg = RoutingConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc
    if cfg.taxonomy_version != policy.taxonomy_version:
        raise ConfigError(
            f"{path}: written against taxonomy {cfg.taxonomy_version} but loaded taxonomy is "
            f"{policy.taxonomy_version}"
        )
    return cfg, digest


def load_gates(config_dir: Path | str | None = None) -> tuple[GatesConfig, str]:
    base = Path(config_dir) if config_dir is not None else default_config_dir()
    path = base / GATES_FILE
    data, digest = read_yaml(path)
    try:
        return GatesConfig.model_validate(data), digest
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc
