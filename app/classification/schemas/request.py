"""ClassificationRequest schema.  [architecture section 3]

v0.1 classifies PRE-EXTRACTED TEXT only [approved decision 5]; binary parsing is out of scope.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import SCHEMA_VERSION, StrictModel, sha256_text


class ExistingLabel(StrictModel):
    scheme: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=200)


class Document(StrictModel):
    document_id: str | None = Field(default=None, max_length=200)
    content: str  # extracted text (bounded by the input guard, not by the schema)
    filename: str = Field(max_length=255)
    extension: str = Field(max_length=32)
    # Informational. Recomputed from `content` whenever it can be encoded, so a caller cannot lie.
    size_bytes: int | None = Field(default=None, ge=0)
    existing_labels: list[ExistingLabel] = Field(default_factory=list, max_length=50)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=50)

    @model_validator(mode="after")
    def _fill_size(self) -> Document:
        try:
            self.size_bytes = len(self.content.encode("utf-8"))
        except (
            UnicodeEncodeError
        ):  # undecodable text: keep the caller's figure, the guard rejects it
            self.size_bytes = self.size_bytes or 0
        return self

    def content_hash(self) -> str:
        return sha256_text(self.content)


class Budget(StrictModel):
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_latency_ms: int | None = Field(default=None, ge=1)


class Options(StrictModel):
    # Single-approach modes exist so the harness can benchmark each approach in isolation.
    mode: Literal["hybrid", "rules", "ml", "llm"] = "hybrid"
    max_llm_tier: Literal["none", "small", "mid", "large"] = "large"
    budget: Budget = Field(default_factory=Budget)
    include_evidence: bool = True


class Caller(StrictModel):
    caller_id: str = Field(default="local", min_length=1, max_length=200)
    purpose: str = Field(default="classification", min_length=1, max_length=200)


class ClassificationRequest(StrictModel):
    schema_version: str = SCHEMA_VERSION
    request_id: str = Field(min_length=1, max_length=200)
    document: Document
    options: Options = Field(default_factory=Options)
    caller: Caller = Field(default_factory=Caller)
