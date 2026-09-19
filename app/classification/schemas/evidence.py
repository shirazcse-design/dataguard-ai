"""Evidence schema.  [architecture section 5; PRD 4.12, 8.4, 14.1]

`provenance` carries the PRD grounding contract (observed evidence vs model inference) and is
validated against the evidence `type` so the two cannot disagree.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import StrictModel

EvidenceSource = Literal["rules", "ml", "llm", "metadata"]
EvidenceType = Literal[
    "pattern_match",
    "validator_passed",
    "dictionary_hit",
    "existing_label",
    "filename_signal",
    "feature_attribution",
    "llm_excerpt",
    "llm_rationale",
]
EvidenceStrength = Literal["definitive", "strong", "weak", "n/a"]
Provenance = Literal["observed", "inferred"]
Axis = Literal["level", "category"]

# Types that are direct observations of the document text/metadata.
_OBSERVED_TYPES = {
    "pattern_match",
    "validator_passed",
    "dictionary_hit",
    "existing_label",
    "filename_signal",
}
# Types that are model inference, never observed fact.
_INFERRED_TYPES = {"feature_attribution", "llm_rationale"}


class Supports(StrictModel):
    axis: Axis
    value: str


class Locator(StrictModel):
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> Locator:
        if self.char_end < self.char_start:
            raise ValueError("char_end must be >= char_start")
        return self


class DetectorRef(StrictModel):
    id: str
    version: str


class Evidence(StrictModel):
    evidence_id: str
    source: EvidenceSource
    supports: Supports
    type: EvidenceType
    locator: Locator | None = None
    # Truncated, and ALWAYS masked for PCI/PII/secrets by the producer. Never raw sensitive text.
    excerpt: str | None = Field(default=None, max_length=500)
    excerpt_hash: str | None = None
    detector: DetectorRef | None = None
    strength: EvidenceStrength = "n/a"
    weight: float | None = None  # ML signed contribution
    verified: bool = False  # LLM excerpts: confirmed to be a substring of the input
    provenance: Provenance

    @model_validator(mode="after")
    def _provenance_matches_type(self) -> Evidence:
        t = self.type
        if t in _OBSERVED_TYPES and self.provenance != "observed":
            raise ValueError(f"evidence type {t!r} must have provenance 'observed'")
        if t in _INFERRED_TYPES and self.provenance != "inferred":
            raise ValueError(f"evidence type {t!r} must have provenance 'inferred'")
        if t == "llm_excerpt":
            expected = "observed" if self.verified else "inferred"
            if self.provenance != expected:
                raise ValueError(
                    "llm_excerpt is 'observed' only when verified against the input; "
                    "otherwise it is 'inferred'"
                )
        if self.verified and t != "llm_excerpt":
            raise ValueError("`verified` applies only to llm_excerpt evidence")
        return self
