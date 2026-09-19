"""Dataset specification models and loader.

The specification is data (YAML) under `data/synthetic/uc4/spec/`:

* `dataset_spec.yaml`  - seed, sizes, thresholds, generator version
* `pools.yaml`         - global vocabulary pools
* `families/*.yaml`    - one entry per content scenario ("family")
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator, model_validator

from app.classification.schemas.common import ID_RE, SEMVER_RE, StrictModel

from .schema import SPLIT_NAMES, TIERS, Tier

DEFAULT_SPEC_DIR = Path(__file__).resolve().parents[3] / "data" / "synthetic" / "uc4" / "spec"
_FAMILY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ExistingLabelSpec(StrictModel):
    scheme: str
    value: str  # template; may contain «a|b» choices
    p: float = Field(default=1.0, gt=0, le=1.0)  # probability the label is present


class FamilySpec(StrictModel):
    family_id: str
    description: str
    tier: Tier
    format: str
    gold_level: str
    gold_categories: list[str] = Field(default_factory=list)
    n_docs: int = Field(ge=1)
    filenames: list[str] = Field(min_length=1)
    bodies: list[str] = Field(min_length=1)
    existing_labels: list[ExistingLabelSpec] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
    pools: dict[str, list[str]] = Field(default_factory=dict)
    ambiguity: bool = False
    acceptable_alternative_levels: list[str] = Field(default_factory=list)
    decoy_for: list[str] = Field(default_factory=list)
    adversarial_type: str | None = None
    notes: str = ""
    group_id: str | None = None  # families sharing a group_id are always placed in one split

    @field_validator("family_id")
    @classmethod
    def _family_id(cls, v: str) -> str:
        if not _FAMILY_ID_RE.match(v):
            raise ValueError(f"family_id must be lower_snake_case, got {v!r}")
        return v

    @field_validator("gold_level")
    @classmethod
    def _level(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError(f"gold_level must be an UPPER_SNAKE id, got {v!r}")
        return v

    @property
    def group(self) -> str:
        return self.group_id or self.family_id


class SplitSizeSpec(StrictModel):
    train: float
    calibration: float
    dev: float
    test: float

    @model_validator(mode="after")
    def _sums_to_one(self) -> SplitSizeSpec:
        total = self.train + self.calibration + self.dev + self.test
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"split fractions must sum to 1, got {total}")
        return self

    def as_dict(self) -> dict[str, float]:
        return {n: getattr(self, n) for n in SPLIT_NAMES}


class NearDuplicateSpec(StrictModel):
    shingle_size: int = Field(ge=1)
    cross_split_max_jaccard: float = Field(gt=0, le=1)


class SplitSearchSpec(StrictModel):
    restarts: int = Field(ge=1)
    iterations: int = Field(ge=1)


class DatasetSpec(StrictModel):
    dataset_id: str
    dataset_version: str
    generator_version: str
    generator_name: str
    seed: int
    taxonomy_version: str
    target_total_docs: int = Field(ge=1)
    split_fractions: SplitSizeSpec
    min_positives_per_label: dict[str, int]
    near_duplicate: NearDuplicateSpec
    split_search: SplitSearchSpec
    tier_descriptions: dict[str, str]

    @field_validator("dataset_version", "generator_version", "taxonomy_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError(f"must be a semantic version, got {v!r}")
        return v

    @model_validator(mode="after")
    def _keys(self) -> DatasetSpec:
        if set(self.min_positives_per_label) != set(SPLIT_NAMES):
            raise ValueError(f"min_positives_per_label must have keys {list(SPLIT_NAMES)}")
        if set(self.tier_descriptions) != set(TIERS):
            raise ValueError(f"tier_descriptions must cover exactly {list(TIERS)}")
        return self


class LoadedSpec(StrictModel):
    dataset: DatasetSpec
    pools: dict[str, list[str]]
    families: list[FamilySpec]
    spec_hash: str


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_spec(spec_dir: Path | str | None = None) -> LoadedSpec:
    base = Path(spec_dir) if spec_dir is not None else DEFAULT_SPEC_DIR
    hasher = hashlib.sha256()

    def read(path: Path) -> Any:
        hasher.update(path.relative_to(base).as_posix().encode())
        hasher.update(path.read_bytes())
        return _load_yaml(path)

    dataset = DatasetSpec.model_validate(read(base / "dataset_spec.yaml"))
    pools_raw = read(base / "pools.yaml")
    if not isinstance(pools_raw, dict):
        raise ValueError("pools.yaml must be a mapping of pool name -> list")
    pools = {str(k): [str(x) for x in v] for k, v in pools_raw.items()}

    families: list[FamilySpec] = []
    for path in sorted((base / "families").glob("*.yaml")):
        raw = read(path)
        for entry in raw.get("families", []):
            families.append(FamilySpec.model_validate(entry))
    ids = [f.family_id for f in families]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate family_id(s): {dupes}")
    return LoadedSpec(dataset=dataset, pools=pools, families=families, spec_hash=hasher.hexdigest())
