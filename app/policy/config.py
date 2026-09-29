"""UC6 configuration schema and loader (config/policy/policy.v1.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from app.classification.config_loader import ConfigError, default_config_dir, read_yaml
from app.classification.schemas.common import SEMVER_RE, StrictModel

POLICY_FILE = "policy/policy.v1.yaml"
LEVELS = ("naive", "advanced", "agentic")
Retrieval = Literal["dense", "sparse", "hybrid"]


class CorpusConfig(StrictModel):
    dir: str
    eligible_statuses: list[str] = Field(min_length=1)


class ChunkingConfig(StrictModel):
    max_words: int = Field(gt=20)
    overlap_words: int = Field(ge=0)
    contextual_header: bool

    @model_validator(mode="after")
    def _overlap(self) -> ChunkingConfig:
        if self.overlap_words >= self.max_words:
            raise ValueError("overlap_words must be smaller than max_words")
        return self


class SparseConfig(StrictModel):
    bm25_k1: float = Field(gt=0)
    bm25_b: float = Field(ge=0, le=1)


class EmbeddingConfig(StrictModel):
    deployment_env: str
    replay_model_id: str
    url_template: str
    dimensions: int | None = Field(default=None, gt=0)
    batch_size: int = Field(gt=0)
    timeout_s: float = Field(gt=0)
    cache_dir: str


class HybridConfig(StrictModel):
    rrf_k: int = Field(gt=0)
    candidates_per_leg: int = Field(gt=0)


class RerankConfig(StrictModel):
    w_coverage: float = Field(ge=0)
    w_heading: float = Field(ge=0)
    w_fused: float = Field(ge=0)
    superseded_penalty: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _weights(self) -> RerankConfig:
        total = self.w_coverage + self.w_heading + self.w_fused
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"rerank weights must sum to 1.0, got {total}")
        return self


class QueryConfig(StrictModel):
    expansions: dict[str, list[str]]


class LevelConfig(StrictModel):
    query_processing: bool
    retrieval: Retrieval
    metadata_filter: bool
    rerank: bool
    top_k: int = Field(gt=0)


class PolicyConfig(StrictModel):
    policy_version: str
    corpus: CorpusConfig
    chunking: ChunkingConfig
    sparse: SparseConfig
    embedding: EmbeddingConfig
    hybrid: HybridConfig
    rerank: RerankConfig
    query: QueryConfig
    levels: dict[str, LevelConfig]

    @field_validator("policy_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError(f"must be a semantic version, got {v!r}")
        return v

    @field_validator("levels")
    @classmethod
    def _levels(cls, v: dict[str, LevelConfig]) -> dict[str, LevelConfig]:
        if set(v) != set(LEVELS):
            raise ValueError(f"levels must be exactly {list(LEVELS)}")
        return v


def load_policy_config(config_dir: Path | str | None = None) -> tuple[PolicyConfig, str]:
    base = Path(config_dir) if config_dir is not None else default_config_dir()
    path = base / POLICY_FILE
    data, digest = read_yaml(path)
    try:
        return PolicyConfig.model_validate(data), digest
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc
