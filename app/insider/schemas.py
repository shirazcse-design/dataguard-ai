"""Typed contracts between every UC2 component. Each carries `schema_version`; every claim carries
an evidence id and a claim type, so a reader can tell an OBSERVED FACT from an INFERRED ANOMALY,
a POLICY REQUIREMENT or an AGENT INTERPRETATION.

Agents exchange these structures only: never conversation histories, raw logs or document text.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "uc2.v1"
Band = Literal["NORMAL", "ELEVATED", "HIGH_ANOMALY"]
Outcome = Literal["MONITOR", "INVESTIGATE", "ESCALATE", "HUMAN_REVIEW"]
ClaimType = Literal[
    "OBSERVED_FACT", "INFERRED_ANOMALY", "POLICY_REQUIREMENT", "AGENT_INTERPRETATION"
]
OUTCOMES: tuple[str, ...] = ("MONITOR", "INVESTIGATE", "ESCALATE", "HUMAN_REVIEW")
SEVERITY = {"MONITOR": 0, "INVESTIGATE": 1, "ESCALATE": 2}
Fault = Literal[
    "logs_unavailable",
    "identity_unavailable",
    "semantic_tier_unavailable",
    "malformed_activity_series",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceItem(Strict):
    id: str  # A1.. anomaly signal, IDN identity, D1.. UC4, P1.. UC6, L1.. log events, APR approval
    claim_type: ClaimType
    source: str  # the capability or tool that produced it
    summary: str  # a short, fixed-vocabulary statement (no raw log text, no document text)


class AnomalyResult(Strict):
    schema_version: str = SCHEMA_VERSION
    model_id: str
    model_version: str
    model_fingerprint: str
    anomaly_score: float
    anomaly_band: Band
    thresholds: dict[str, float]
    contributing_signals: list[dict[str, Any]]
    evidence_ids: list[str]


class CasePacket(Strict):
    """What the orchestrator starts from: the alert, the authoritative anomaly result and ids."""

    schema_version: str = SCHEMA_VERSION
    case_id: str
    subject: str  # the synthetic user id (pseudonymous; no name exists in UC2 data)
    date: str
    trigger: Literal["anomaly_alert", "referral"]
    anomaly: AnomalyResult
    file_ref_count: int
    simulated_faults: list[Fault] = Field(default_factory=list)


class IdentityContext(Strict):
    schema_version: str = SCHEMA_VERSION
    available: bool
    role: str | None = None
    role_family: str | None = None
    department: str | None = None
    privilege_level: str | None = None
    work_hours: list[int] | None = None
    expected_repositories: list[str] = Field(default_factory=list)
    expected_data_classes: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class FileSensitivity(Strict):
    file_ref: str
    evidence_id: str
    ok: bool
    level: str | None
    categories: list[str]
    confidence: str | None
    review_required: bool
    injection_flagged: bool


class ClassificationSummary(Strict):
    schema_version: str = SCHEMA_VERSION
    files: list[FileSensitivity]
    max_level: str | None
    high_risk_categories: list[str]
    uncertain: bool  # any file without a level / review_required


class VerifiedPolicyResult(Strict):
    schema_version: str = SCHEMA_VERSION
    topic: str
    question: str
    status: str  # UC6 status, or UNAVAILABLE / NOT_RUN
    claims: list[dict[str, Any]]  # {evidence_id, text, citation} - verified claims only
    effect: Literal["prohibited", "requires_approval", "allowed", "unknown"] = "unknown"
    conflict: bool = False
    insufficient: bool = False
    conflict_note: str | None = None  # e.g. "uc6_conflict_not_material_at_level"


class BehaviorFinding(Strict):
    schema_version: str = SCHEMA_VERSION
    behavior_summary: str
    anomaly_score: float
    anomaly_band: Band
    baseline_comparison: list[dict[str, Any]]
    contributing_signals: list[str]
    temporal_observations: list[dict[str, Any]]  # {text, evidence_id, claim_type}
    evidence_ids: list[str]
    confidence: Literal["low", "medium", "high"]
    gaps: list[str] = Field(default_factory=list)
    recommended_follow_up: list[str] = Field(default_factory=list)


class InvestigationFinding(Strict):
    schema_version: str = SCHEMA_VERSION
    timeline: list[dict[str, Any]]  # {time, event, evidence_id}
    observed_facts: list[dict[str, Any]]  # {text, evidence_id, claim_type}
    data_findings: list[dict[str, Any]]
    policy_findings: list[dict[str, Any]]
    correlated_events: list[dict[str, Any]]
    conflicting_evidence: list[dict[str, Any]]
    missing_evidence: list[str]
    evidence_ids: list[str]
    confidence: Literal["low", "medium", "high"]
    recommended_follow_up: list[str] = Field(default_factory=list)


class RiskRecommendation(Strict):
    schema_version: str = SCHEMA_VERSION
    recommended_outcome: Outcome
    reason_codes: list[str]
    rationale: str
    evidence_ids: list[str]
    confidence: Literal["low", "medium", "high"]
    uncertainty: list[str] = Field(default_factory=list)
    conflicting_findings: list[str] = Field(default_factory=list)
    recommended_human_action: str


class Decision(Strict):
    schema_version: str = SCHEMA_VERSION
    rubric_version: str
    score: int
    band: Literal["MONITOR", "INVESTIGATE", "ESCALATE"]
    floor: Literal["MONITOR", "INVESTIGATE", "ESCALATE"]
    outcome: Outcome
    reason_codes: list[str]
    contributing_factors: list[dict[str, Any]]
    analyst_review_required: bool
    agent_recommendation: str | None = None
    summary: list[
        dict[str, str]
    ]  # {claim_type, text, evidence_id} - fixed templates, no guilt words


class AnalystDecision(Strict):
    schema_version: str = SCHEMA_VERSION
    case_id: str
    action: Literal["agree", "disagree_lower", "disagree_higher", "needs_more_information"]
    note: str = Field(default="", max_length=500)
    simulated: bool = True
    not_gold_adjudication: bool = True
