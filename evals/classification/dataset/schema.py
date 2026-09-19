"""Dataset record schema (extends PRD Appendix B evaluation dataset fields for classification)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.classification.schemas import ClassificationRequest, Document, ExistingLabel
from app.classification.schemas.common import StrictModel, sha256_text

Tier = Literal["T1", "T2", "T3", "T4", "T5"]
SplitName = Literal["train", "calibration", "dev", "test"]
SPLIT_NAMES: tuple[SplitName, ...] = ("train", "calibration", "dev", "test")
TIERS: tuple[Tier, ...] = ("T1", "T2", "T3", "T4", "T5")
AnnotationStatus = Literal["unreviewed", "human_reviewed"]


class GoldEvidenceSpan(StrictModel):
    label: str  # taxonomy id (category id, or level id) the span supports
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    text: str

    @model_validator(mode="after")
    def _ordered(self) -> GoldEvidenceSpan:
        if self.char_end <= self.char_start:
            raise ValueError("evidence span must be non-empty")
        return self


class DatasetDocument(StrictModel):
    """One labeled synthetic document. Only `document`-derived fields are ever shown to a
    classifier; everything else (tier, family, gold labels, ...) is ground truth for evaluation."""

    doc_id: str
    split: SplitName
    tier: Tier
    family_id: str
    group_id: str
    generator: str
    format: str

    # --- classifier-visible inputs ---
    filename: str
    extension: str
    content: str
    existing_labels: list[ExistingLabel] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

    # --- ground truth ---
    gold_level: str
    gold_categories: list[str] = Field(default_factory=list)
    gold_evidence_spans: list[GoldEvidenceSpan] = Field(default_factory=list)
    ambiguity_flag: bool = False
    acceptable_alternative_levels: list[str] = Field(default_factory=list)
    decoy_for: list[str] = Field(default_factory=list)
    adversarial_type: str | None = None
    annotation_notes: str = ""
    annotation_status: AnnotationStatus = "unreviewed"
    taxonomy_version: str
    content_hash: str

    @model_validator(mode="after")
    def _hash_matches(self) -> DatasetDocument:
        if self.content_hash != sha256_text(self.content):
            raise ValueError("content_hash does not match content")
        return self

    def to_request(self, request_id: str | None = None) -> ClassificationRequest:
        """Build the classifier-visible request (ground truth and bookkeeping are excluded)."""
        return ClassificationRequest(
            request_id=request_id or f"req-{self.doc_id}",
            document=Document(
                document_id=self.doc_id,
                content=self.content,
                filename=self.filename,
                extension=self.extension,
                existing_labels=list(self.existing_labels),
                metadata=dict(self.metadata),
            ),
        )
