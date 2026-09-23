"""Batch Triage Agent schemas (docs/uc4/agent-plan.md).

The core safety invariant lives here: `DocumentAnnotation.level`, `.categories` and `.high_risk`
are populated FROM the `classify_document` tool's own result, never from the agent's free text —
the agent cannot express a different sensitivity decision even if it tried, because there is no
field for it to write one into. This is a schema-level control, not a hope about model behaviour.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.classification.schemas.common import StrictModel

StopReason = Literal["completed", "step_budget_exceeded", "tool_failure"]
Priority = Literal["low", "medium", "high"]


class ToolCallRecord(StrictModel):
    """One tool call the agent made. Never carries document text or raw arguments - only a short,
    length-capped summary, matching the observability spans' redaction discipline."""

    step: int = Field(ge=1)
    tool: str
    ok: bool
    error_kind: str | None = None


class DocumentAnnotation(StrictModel):
    doc_id: str
    request_id: str
    # Copied verbatim from the classify_document tool result - see the module docstring.
    level: str | None = None
    categories: list[str] = Field(default_factory=list)
    high_risk: bool = False
    status: str
    review_requested: bool = False
    review_reason: str | None = Field(default=None, max_length=200)
    priority: Priority = "low"
    rationale: str = Field(default="", max_length=500)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    stopped_reason: StopReason = "completed"


class BatchTriageReport(StrictModel):
    agent_config_version: str
    n_documents: int
    documents: list[DocumentAnnotation] = Field(default_factory=list)
