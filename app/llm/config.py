"""LLM configuration schema and loader (config/llm/llm.v1.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from app.classification.config_loader import ConfigError, default_config_dir, read_yaml
from app.classification.policy import TaxonomyPolicy
from app.classification.schemas.common import SEMVER_RE, StrictModel

LLM_FILE = "llm/llm.v1.yaml"
TIER_NAMES = ("small", "mid", "large")


class PromptConfig(StrictModel):
    version: str
    file: str
    fewshot_file: str


class InputConfig(StrictModel):
    max_input_chars: int = Field(ge=500)
    include_filename: bool
    include_existing_labels: bool
    include_metadata: bool


class GenerationConfig(StrictModel):
    temperature: float = Field(ge=0.0, le=2.0)
    max_output_tokens: int = Field(ge=50)
    timeout_s: float = Field(gt=0)
    schema_repair_retries: int = Field(ge=0, le=3)


class RetryConfig(StrictModel):
    max_attempts: int = Field(ge=1, le=6)
    base_delay_s: float = Field(ge=0)
    max_delay_s: float = Field(ge=0)
    jitter: float = Field(ge=0, le=1)


class EvidenceConfig(StrictModel):
    max_quotes: int = Field(ge=1, le=20)
    max_quote_chars: int = Field(ge=20)
    excerpt_chars: int = Field(ge=20, le=500)


class Price(StrictModel):
    input_per_1k_usd: float | None = Field(default=None, ge=0)
    output_per_1k_usd: float | None = Field(default=None, ge=0)
    retrieved_on: str | None = None

    @model_validator(mode="after")
    def _all_or_nothing(self) -> Price:
        given = [self.input_per_1k_usd, self.output_per_1k_usd]
        if any(v is not None for v in given) and (
            any(v is None for v in given) or not self.retrieved_on
        ):
            raise ValueError(
                "a price needs input, output and a retrieved_on date (prices are never assumed)"
            )
        return self

    @property
    def configured(self) -> bool:
        return self.input_per_1k_usd is not None


class TierConfig(StrictModel):
    alias: str
    deployment_env: str
    price: Price
    api: Literal["chat_completions", "responses"] = "chat_completions"
    # Cache identity of the recorded benchmark for this tier (a deployment NAME, not a secret).
    replay_model_id: str | None = None
    # False when the deployment rejects a temperature parameter (some reasoning models do).
    send_temperature: bool = True


class FoundryConfig(StrictModel):
    endpoint_env: str
    api_version_env: str | None = None  # only needed if url_template contains {api_version}
    url_template: str
    responses_url_template: str = "{endpoint}/openai/v1/responses"
    auth: Literal["api_key", "entra"]
    api_key_env: str
    entra_scope: str | None = None
    max_tokens_param: str
    json_schema_response_format: bool

    @model_validator(mode="after")
    def _entra_scope(self) -> FoundryConfig:
        if self.auth == "entra" and not self.entra_scope:
            raise ValueError("auth 'entra' requires an explicit entra_scope (not assumed)")
        return self


class CacheConfig(StrictModel):
    dir: str


class LLMConfig(StrictModel):
    llm_version: str
    taxonomy_version: str
    seed: int
    prompt: PromptConfig
    input: InputConfig
    generation: GenerationConfig
    retry: RetryConfig
    evidence: EvidenceConfig
    tiers: dict[str, TierConfig]
    foundry: FoundryConfig
    cache: CacheConfig

    @field_validator("llm_version", "taxonomy_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError(f"must be a semantic version, got {v!r}")
        return v

    @field_validator("tiers")
    @classmethod
    def _tiers(cls, v: dict[str, TierConfig]) -> dict[str, TierConfig]:
        if set(v) != set(TIER_NAMES):
            raise ValueError(f"tiers must be exactly {list(TIER_NAMES)}")
        return v


def load_llm_config(
    policy: TaxonomyPolicy, config_dir: Path | str | None = None
) -> tuple[LLMConfig, str]:
    base = Path(config_dir) if config_dir is not None else default_config_dir()
    path = base / LLM_FILE
    data, digest = read_yaml(path)
    try:
        cfg = LLMConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc
    if cfg.taxonomy_version != policy.taxonomy_version:
        raise ConfigError(
            f"{path}: written against taxonomy {cfg.taxonomy_version} but loaded taxonomy is "
            f"{policy.taxonomy_version}"
        )
    return cfg, digest
