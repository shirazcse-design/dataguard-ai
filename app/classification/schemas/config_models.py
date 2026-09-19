"""Schemas for the version-controlled configuration files (taxonomy, high-risk, eval).

These validate the *shape* of each file. Cross-file consistency (e.g. high-risk categories exist
in the taxonomy) is checked by `app.classification.config_loader`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import ID_RE, SEMVER_RE, StrictModel


def _check_semver(value: str) -> str:
    if not SEMVER_RE.match(value):
        raise ValueError(f"must be a semantic version like 1.0.0, got {value!r}")
    return value


class LevelDef(StrictModel):
    id: str
    rank: int = Field(ge=0)
    name: str
    description: str

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError(f"id must be UPPER_SNAKE_CASE, got {v!r}")
        return v


class CategoryDef(StrictModel):
    id: str
    name: str
    description: str
    level_floor: str
    positive_examples: list[str] = Field(default_factory=list)
    counter_examples: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError(f"id must be UPPER_SNAKE_CASE, got {v!r}")
        return v


class TaxonomyConstraints(StrictModel):
    level_cardinality: Literal["exactly_one"] = "exactly_one"
    category_cardinality: Literal["zero_or_more"] = "zero_or_more"
    enforce_level_floors: bool = True


class TaxonomyConfig(StrictModel):
    taxonomy_version: str
    policy_defaults_notice: str
    levels: list[LevelDef] = Field(min_length=2)
    categories: list[CategoryDef] = Field(min_length=1)
    constraints: TaxonomyConstraints

    @field_validator("taxonomy_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        return _check_semver(v)

    @model_validator(mode="after")
    def _cross_checks(self) -> TaxonomyConfig:
        level_ids = [lv.id for lv in self.levels]
        if len(set(level_ids)) != len(level_ids):
            raise ValueError("duplicate level ids")
        ranks = sorted(lv.rank for lv in self.levels)
        if ranks != list(range(len(ranks))):
            raise ValueError(f"level ranks must be contiguous from 0, got {ranks}")
        cat_ids = [c.id for c in self.categories]
        if len(set(cat_ids)) != len(cat_ids):
            raise ValueError("duplicate category ids")
        overlap = set(level_ids) & set(cat_ids)
        if overlap:
            raise ValueError(f"ids used as both level and category: {sorted(overlap)}")
        for c in self.categories:
            if c.level_floor not in level_ids:
                raise ValueError(f"category {c.id}: level_floor {c.level_floor!r} is not a level")
        return self


class HighRiskConfig(StrictModel):
    high_risk_version: str
    taxonomy_version: str
    categories: list[str]
    levels: list[str]
    combine: Literal["any"] = "any"

    @field_validator("high_risk_version", "taxonomy_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        return _check_semver(v)

    @model_validator(mode="after")
    def _non_empty(self) -> HighRiskConfig:
        if not self.categories and not self.levels:
            raise ValueError("high-risk config must list at least one category or level")
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("duplicate high-risk categories")
        if len(set(self.levels)) != len(self.levels):
            raise ValueError("duplicate high-risk levels")
        return self


class BootstrapConfig(StrictModel):
    n_resamples: int = Field(ge=1)
    confidence_level: float = Field(gt=0, lt=1)
    seed: int
    # Resample scenario families ("group") because documents within a family are correlated.
    unit: Literal["group", "document"] = "group"


class EvalConfig(StrictModel):
    eval_config_version: str
    headline_tiers: list[str] = Field(min_length=1)
    slice_fields: list[str]
    bootstrap: BootstrapConfig
    min_support_flag: int = Field(ge=1)
    calibration_bins: int = Field(default=5, ge=2)
    # Informational reference values shown in reports. They are NOT pass/fail gates.
    reference_targets: dict[str, float] = Field(default_factory=dict)
    latency_percentiles: list[int]

    @field_validator("eval_config_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        return _check_semver(v)

    @field_validator("latency_percentiles")
    @classmethod
    def _pct(cls, v: list[int]) -> list[int]:
        if any(not 0 < p <= 100 for p in v):
            raise ValueError("latency percentiles must be in (0, 100]")
        return v
