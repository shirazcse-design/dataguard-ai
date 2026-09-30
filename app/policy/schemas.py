"""The Policy Copilot's answer contract (`PolicyAnswer`) and its parts.

Statuses:
* ANSWERED               - at least one claim whose citation was verified against retrieved text
* INSUFFICIENT_EVIDENCE  - a valid, desirable outcome: the corpus does not support an answer
* CONFLICT_REVIEW        - sources disagree and metadata cannot decide which is authoritative
* BLOCKED                - the input guard refused the question (size or prompt injection)
* UNAVAILABLE            - retrieval or generation failed; never presented as an answer

Review reasons are a fixed vocabulary (safe to trace and to show).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.classification.schemas.common import StrictModel

from .retrieval import RetrievalTrace

AnswerStatus = Literal[
    "ANSWERED", "INSUFFICIENT_EVIDENCE", "CONFLICT_REVIEW", "BLOCKED", "UNAVAILABLE"
]  # fmt: skip
ReviewReason = Literal[
    "insufficient_evidence", "no_verified_claims", "policy_conflict", "conflict_unverified",
    "unverified_claims_removed", "guardrail_injection", "input_rejected", "evidence_injection",
    "generation_unavailable", "agent_requested", "step_budget_exceeded", "tool_failure",
]  # fmt: skip
DropReason = Literal[
    "fabricated_evidence_id", "quote_not_in_evidence", "number_not_in_evidence",
    "superseded_version", "over_claim_limit",
]  # fmt: skip


class Claim(StrictModel):
    text: str
    evidence_id: str  # the label the model cited (E1..En)
    chunk_id: str | None  # None when the label did not exist (a fabricated citation)
    citation: str | None
    quote: str
    verified: bool
    drop_reason: DropReason | None = None


class Evidence(StrictModel):
    evidence_id: str
    chunk_id: str
    citation: str
    policy_id: str
    title: str
    version: str
    effective_date: str
    status: str
    section: str
    heading: str
    policy_owner: str
    body: str  # shown in the UI; NEVER exported to telemetry
    rank: int
    score: float
    cited: bool = False
    flagged_injection: bool = False


class Conflict(StrictModel):
    kind: Literal["version", "cross_policy"]
    chunk_ids: list[str]
    citations: list[str]
    resolution: Literal["resolved_by_metadata", "human_review"]
    authoritative: str | None = None  # citation of the winning source, when metadata decides
    note: str


class Review(StrictModel):
    required: bool = False
    reasons: list[ReviewReason] = Field(default_factory=list)

    def add(self, reason: ReviewReason) -> None:
        self.required = True
        if reason not in self.reasons:
            self.reasons.append(reason)


class GuardrailEvent(StrictModel):
    type: str
    trigger: str  # rule ids / fixed vocabulary only
    action: str
    chunk_id: str | None = None


class Stage(StrictModel):
    name: str
    status: Literal["ok", "skipped", "blocked", "failed", "short_circuit"]
    detail: dict[str, Any] = Field(default_factory=dict)  # counts, ids, scores; never text
    ms: float | None = None


class LLMCall(StrictModel):
    model_id: str
    served_model: str | None
    cached: bool
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: float
    attempts: int = 1


class PolicyAnswer(StrictModel):
    request_id: str
    level: str
    mode: Literal["replay", "live", "offline"]
    status: AnswerStatus
    claims: list[Claim]
    dropped_claims: list[Claim] = Field(default_factory=list)
    citations: list[str]  # unique, verified citations in claim order
    evidence: list[Evidence]
    conflicts: list[Conflict] = Field(default_factory=list)
    conflict_note: str | None = None  # model text; shown only with a verified conflict
    evidence_status: Literal["grounded", "partially_grounded", "ungrounded", "none"]
    top_evidence_score: float | None = None
    review: Review
    guardrail_events: list[GuardrailEvent] = Field(default_factory=list)
    stages: list[Stage]
    retrieval: RetrievalTrace | None = None
    llm: LLMCall | None = None
    embedding_model_id: str | None = None
    agent: dict[str, Any] | None = None  # the agent trace (Agentic RAG only)
    latency_ms: float = 0.0

    @property
    def answer_text(self) -> str:
        return " ".join(c.text for c in self.claims)
