"""UC1 configuration (config/dlp/*.yaml): destinations + agent limits, the UC4->UC6 mapping
contract, and the risk rubric. Each file is validated strictly and its hash is reported."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from app.classification.config_loader import ConfigError, default_config_dir, read_yaml
from app.classification.schemas.common import SEMVER_RE, StrictModel

LEVELS = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_CONFIDENTIAL")
DESTINATIONS = (
    "approved_corporate",
    "personal_cloud",
    "personal_email",
    "partner_external",
    "generative_ai",
    "restricted",
    "unknown_external",
)
OUTCOMES = ("ALLOW", "WARN", "ESCALATE", "HUMAN_REVIEW")
Effect = Literal["prohibited", "requires_approval", "allowed"]


def _semver(v: str) -> str:
    if not SEMVER_RE.match(v):
        raise ValueError(f"must be a semantic version, got {v!r}")
    return v


class DestinationClass(StrictModel):
    description: str
    hosts: list[str]


class BehaviorConfig(StrictModel):
    business_hours: dict[str, int]
    external_upload_count_7d: int = Field(gt=0)
    download_volume_ratio: float = Field(gt=1)
    sensitive_file_access_7d: int = Field(gt=0)
    bands: dict[Literal["ELEVATED", "UNUSUAL"], int]


class AgentConfig(StrictModel):
    name: str = Field(pattern=r"^[a-z0-9-]+$")
    prompt_version: str
    prompt_file: str
    allowed_tools: list[str]
    max_tool_calls: int = Field(gt=0, le=20)
    max_turns: int = Field(gt=1, le=30)
    max_consecutive_tool_failures: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    max_justification_chars: int = Field(gt=0)


class DlpConfig(StrictModel):
    dlp_version: str
    destinations: dict[str, DestinationClass]
    corporate_tenants: list[str]
    behavior: BehaviorConfig
    policy_question: str
    agent: AgentConfig

    _v = field_validator("dlp_version")(classmethod(lambda cls, v: _semver(v)))

    @model_validator(mode="after")
    def _known(self) -> DlpConfig:
        unknown = set(self.destinations) - set(DESTINATIONS)
        if unknown:
            raise ValueError(f"unknown destination classes {sorted(unknown)}")
        hosts = [h for d in self.destinations.values() for h in d.hosts]
        if len(hosts) != len(set(hosts)):
            raise ValueError("a host is listed under two destination classes")
        return self


class LevelTerm(StrictModel):
    rank: int
    policy_term: str


class CategoryTerm(StrictModel):
    phrase: str
    min_level: str


class DestinationPhrase(StrictModel):
    action: str
    phrase: str


class PolicyEffect(StrictModel):
    section: str = Field(pattern=r"^POL-[A-Z0-9-]+@\d+\.\d+#[\d.]+$")
    destinations: list[str]
    min_level: str
    max_level: str | None = None
    effect: Effect


class MappingConfig(StrictModel):
    mapping_version: str
    levels: dict[str, LevelTerm]
    categories: dict[str, CategoryTerm]
    destination_phrases: dict[str, DestinationPhrase]
    policy_effects: list[PolicyEffect]

    _v = field_validator("mapping_version")(classmethod(lambda cls, v: _semver(v)))

    @model_validator(mode="after")
    def _complete(self) -> MappingConfig:
        if set(self.levels) != set(LEVELS):
            raise ValueError(f"levels must map exactly {list(LEVELS)}")
        if set(self.destination_phrases) != set(DESTINATIONS):
            raise ValueError(f"destination_phrases must cover exactly {list(DESTINATIONS)}")
        for e in self.policy_effects:
            if set(e.destinations) - set(DESTINATIONS) or e.min_level not in LEVELS:
                raise ValueError(f"bad policy effect {e.section}")
            if e.max_level and e.max_level not in LEVELS:
                raise ValueError(f"bad max_level in {e.section}")
        for c in self.categories.values():
            if c.min_level not in LEVELS:
                raise ValueError(f"bad category min_level {c.min_level}")
        return self

    def rank(self, level: str | None) -> int:
        return self.levels[level].rank if level in self.levels else -1


class Band(StrictModel):
    min: int
    outcome: Literal["ALLOW", "WARN", "ESCALATE"]


class Floor(StrictModel):
    when: Literal["prohibited_and_level_at_least", "destination_is"]
    level: str
    outcome: Literal["WARN", "ESCALATE"]
    exception_floor: Literal["ALLOW", "WARN", "ESCALATE"]
    destination: str | None = None


class RiskRubric(StrictModel):
    rubric_version: str
    points: dict[str, dict[str, int] | int]
    bands: list[Band] = Field(min_length=1)
    floors: list[Floor]
    human_review_triggers: list[str]
    escalate_requires_human_approval: bool
    gate_data_points_on_exposure: bool = False
    simulated_actions: dict[str, str]

    _v = field_validator("rubric_version")(classmethod(lambda cls, v: _semver(v)))


def _load(name: str, model, config_dir: Path | str | None):
    path = (Path(config_dir) if config_dir else default_config_dir()) / "dlp" / name
    data, digest = read_yaml(path)
    try:
        return model.model_validate(data), digest
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc


def load_dlp_configs(config_dir: Path | str | None = None):
    """`(dlp, mapping, rubric, hashes)` - every file validated; hashes keyed by file name."""
    dlp, h1 = _load("dlp.v1.yaml", DlpConfig, config_dir)
    mapping, h2 = _load("mapping.v1.yaml", MappingConfig, config_dir)
    rubric, h3 = _load("risk.v1.yaml", RiskRubric, config_dir)
    return dlp, mapping, rubric, {"dlp.v1.yaml": h1, "mapping.v1.yaml": h2, "risk.v1.yaml": h3}
