"""ML configuration schema and loader (config/ml/ml.v1.yaml)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, ValidationError, field_validator, model_validator

from app.classification.config_loader import ConfigError, default_config_dir, read_yaml
from app.classification.policy import TaxonomyPolicy
from app.classification.schemas.common import SEMVER_RE, StrictModel

ML_FILE = "ml/ml.v1.yaml"


class FeatureConfig(StrictModel):
    max_chars: int = Field(ge=1000)
    sublinear_tf: bool
    word_ngram_range: tuple[int, int]
    word_min_df: int = Field(ge=1)
    word_max_features: int = Field(ge=100)
    char_ngram_range: tuple[int, int]
    char_min_df: int = Field(ge=1)
    char_max_features: int = Field(ge=100)
    filename_block: bool


class HeadConfig(StrictModel):
    C: float = Field(gt=0)
    class_weight: str | None = "balanced"
    max_iter: int = Field(ge=100)

    @field_validator("class_weight")
    @classmethod
    def _cw(cls, v: str | None) -> str | None:
        if v not in (None, "balanced"):
            raise ValueError("class_weight must be null or 'balanced'")
        return v


class CalibrationConfig(StrictModel):
    method: str = "sigmoid"
    min_positives: int = Field(ge=2)
    min_negatives: int = Field(ge=2)

    @field_validator("method")
    @classmethod
    def _m(cls, v: str) -> str:
        if v != "sigmoid":
            raise ValueError("only sigmoid (Platt) calibration is implemented")
        return v


class DecisionConfig(StrictModel):
    category_threshold: float = Field(gt=0, lt=1)
    category_thresholds: dict[str, float] = Field(default_factory=dict)


class EvidenceConfig(StrictModel):
    top_features: int = Field(ge=1, le=10)
    max_token_chars: int = Field(ge=3)


class SelectionConfig(StrictModel):
    c_grid: list[float] = Field(min_length=1)
    cv_folds: int = Field(ge=2)


class MLConfig(StrictModel):
    ml_version: str
    taxonomy_version: str
    seed: int
    features: FeatureConfig
    level_head: HeadConfig
    category_head: HeadConfig
    calibration: CalibrationConfig
    decision: DecisionConfig
    evidence: EvidenceConfig
    selection: SelectionConfig

    @field_validator("ml_version", "taxonomy_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError(f"must be a semantic version, got {v!r}")
        return v

    @model_validator(mode="after")
    def _grid_positive(self) -> MLConfig:
        if any(c <= 0 for c in self.selection.c_grid):
            raise ValueError("c_grid values must be positive")
        return self

    def threshold_for(self, category: str) -> float:
        return self.decision.category_thresholds.get(category, self.decision.category_threshold)


def load_ml_config(
    policy: TaxonomyPolicy, config_dir: Path | str | None = None
) -> tuple[MLConfig, str]:
    base = Path(config_dir) if config_dir is not None else default_config_dir()
    path = base / ML_FILE
    data, digest = read_yaml(path)
    try:
        cfg = MLConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc
    if cfg.taxonomy_version != policy.taxonomy_version:
        raise ConfigError(
            f"{path}: written against taxonomy {cfg.taxonomy_version} but loaded taxonomy is "
            f"{policy.taxonomy_version}"
        )
    unknown = sorted(set(cfg.decision.category_thresholds) - set(policy.category_ids))
    if unknown:
        raise ConfigError(f"{path}: thresholds for unknown categories {unknown}")
    if any(not 0 < t < 1 for t in cfg.decision.category_thresholds.values()):
        raise ConfigError(f"{path}: category thresholds must be within (0, 1)")
    return cfg, digest
