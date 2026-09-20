"""Core value types of the Rules Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Strength is a categorical tier, NOT a probability (approved decision).
Strength = Literal["definitive", "strong", "weak"]
STRENGTH_RANK: dict[str, int] = {"weak": 1, "strong": 2, "definitive": 3}
Axis = Literal["category", "level"]
EvidenceKind = Literal[
    "pattern_match", "validator_passed", "dictionary_hit", "existing_label", "filename_signal"
]


def stronger(a: str, b: str) -> str:
    return a if STRENGTH_RANK[a] >= STRENGTH_RANK[b] else b


def demote(strength: str) -> str | None:
    """One tier weaker; None means the detection disappears entirely."""
    return {"definitive": "strong", "strong": "weak", "weak": None}[strength]


@dataclass(frozen=True)
class Detection:
    detector_id: str
    detector_version: str
    axis: Axis
    value: str  # category id (axis="category") or level id (axis="level")
    strength: Strength
    kind: EvidenceKind
    start: int
    end: int
    line: int
    masked_excerpt: str  # NEVER the raw sensitive value
    excerpt_hash: str  # sha256 of the raw matched text (correlation without exposure)
    source: Literal["rules", "metadata"] = "rules"
    level_hint: str | None = None  # e.g. government identifiers imply Highly Confidential


@dataclass(frozen=True)
class Suppression:
    """A candidate that a negative context (or validator/dummy-value check) removed."""

    detector_id: str
    reason: str
    start: int
    end: int


@dataclass
class DetectorOutput:
    detections: list[Detection] = field(default_factory=list)
    suppressions: list[Suppression] = field(default_factory=list)


@dataclass
class RulesResult:
    ruleset_version: str
    detections: list[Detection]
    suppressions: list[Suppression]
    # category id -> strongest emitted strength (only categories at/above emit_min_strength)
    categories: dict[str, str]
    # None => the level ABSTAINS (no decisive evidence); "NO RULE MATCH != PUBLIC"
    level: str | None
    level_strength: str | None
    level_sources: list[str]
    no_signal: bool  # no detection of ANY strength
    truncated: bool
    weak_only_categories: list[str]  # categories with weak evidence that did not reach emission
    elapsed_ms: float
    # detectors that raised ("<detector_id>:<ExceptionClass>"); never the message, it may hold text
    detector_errors: list[str] = field(default_factory=list)
