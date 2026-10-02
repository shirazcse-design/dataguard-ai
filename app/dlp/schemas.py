"""UC1 contracts: the DLP event in, the Investigation out. Every stage result is a typed object, so
the harness decides from fields, never from model text."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.classification.schemas.common import StrictModel

Outcome = Literal["ALLOW", "WARN", "ESCALATE", "HUMAN_REVIEW"]
Fault = Literal[
    "semantic_tier_unavailable",
    "policy_unavailable",
    "identity_unavailable",
    "activity_tool_error",
    "malformed_activity_result",
]


class Destination(StrictModel):
    host: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9.-]+$")
    account_type: Literal["personal", "corporate", "external"]


class DLPEvent(StrictModel):
    """A synthetic DLP alert. `document_ref` is `uc4:<doc_id>` (a UC4 dev-split document) or
    `uc1:<name>` (data/dlp/documents). `simulate_fault` injects a failure for testing ONLY and is
    always reported as SIMULATED."""

    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    timestamp: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")  # local time of the action
    user_id: str = Field(pattern=r"^u-\d{4}$")
    action: Literal["upload", "email", "share", "paste", "post"]
    destination: Destination
    document_ref: str = Field(pattern=r"^(uc4|uc1):[A-Za-z0-9_-]{1,80}$")
    existing_label: str | None = Field(default=None, max_length=40)
    user_justification: str | None = Field(default=None, max_length=2000)
    simulate_fault: list[Fault] = Field(default_factory=list)


class Stage(StrictModel):
    name: str
    status: Literal["ok", "failed", "skipped", "blocked"]
    ms: float | None = None
    detail: dict[str, Any] = Field(default_factory=dict)  # ids, counts, codes; never document text


class Prechecks(StrictModel):
    destination_class: str
    destination_known_to_user: bool | None
    existing_label: str | None
    pattern_categories: dict[str, str]  # UC4 rules engine: category -> strongest strength
    pattern_level: str | None
    findings: list[str]  # fixed codes, e.g. "explicit_label:CONFIDENTIAL", "pattern:PII:strong"


class Classification(StrictModel):
    ok: bool
    status: str
    level: str | None = None
    confidence: str | None = None  # UC4 confidence bucket / strength
    decided_by: str | None = None
    categories: list[str] = Field(default_factory=list)
    high_risk: bool = False
    review_required: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    injection_flagged: bool = False
    stages_run: list[str] = Field(default_factory=list)
    llm_cached: bool | None = None


class Identity(StrictModel):
    user_id: str
    role: str
    department: str
    employment_type: str
    employment_status: str
    manager: str
    privilege_level: str
    region: str
    business_unit: str


class Behavior(StrictModel):
    band: Literal["NORMAL", "ELEVATED", "UNUSUAL"]
    signals: list[str]
    features: dict[str, float | int | bool]
    provider: str  # which implementation produced it (UC2 will plug in here)


class PolicyContext(StrictModel):
    question: str
    status: str  # UC6 PolicyAnswer status, or "UNAVAILABLE"
    mode: str | None = None
    citations: list[str] = Field(default_factory=list)  # verified, human-readable
    cited_sections: list[str] = Field(default_factory=list)  # verified section keys
    claims: list[dict[str, Any]] = Field(default_factory=list)  # {text, citation} verified only
    effects: list[dict[str, str]] = Field(default_factory=list)  # {section, effect}
    effect: Literal["prohibited", "requires_approval", "allowed", "unknown"] = "unknown"
    conflict: bool = False
    conflict_reason: str | None = None
    review_reasons: list[str] = Field(default_factory=list)


class AgentResult(StrictModel):
    proposed_outcome: Outcome | None = None
    findings: list[dict[str, Any]] = Field(default_factory=list)  # {text, evidence_id, verified}
    missing_evidence: list[str] = Field(default_factory=list)
    verified_exception: dict[str, Any] | None = None  # ONLY from a check_dlp_exception tool result
    review_requested: list[str] = Field(default_factory=list)
    stopped_reason: str = "not_run"
    trace: dict[str, Any] = Field(default_factory=dict)


class Decision(StrictModel):
    rubric_version: str
    mapping_version: str
    score: int
    band: Literal["ALLOW", "WARN", "ESCALATE"]
    outcome: Outcome
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "UNDETERMINED"]
    contributing_factors: list[dict[str, Any]]  # {factor, value, points}
    reason_codes: list[str]
    human_review_required: bool
    simulated_action: str | None = None  # ALWAYS simulated; never executed
    agent_proposal: Outcome | None = None


class Investigation(StrictModel):
    case_id: str
    mode: Literal["replay", "live", "offline"]
    event: DLPEvent
    simulated_faults: list[str]
    stages: list[Stage]
    prechecks: Prechecks | None = None
    classification: Classification | None = None
    identity: Identity | None = None
    behavior: Behavior | None = None
    policy: PolicyContext | None = None
    agent: AgentResult | None = None
    decision: Decision
    guardrail_events: list[dict[str, str]] = Field(default_factory=list)
    latency_ms: float = 0.0
