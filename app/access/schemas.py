"""UC3 schemas. Every statement shown to the agent or the analyst carries a claim type, so the
dashboard can show where it came from:

  OBSERVED_FACT                 from an authoritative store (identity, graph, usage, register)
  DETERMINISTIC_CONTROL_RESULT  from the least-privilege / SoD engine or a UC4 classification
  POLICY_REQUIREMENT            a POL-ACC section retrieved from UC6 and citation-checked
  AGENT_INFERENCE               the agent's reading of the evidence (never authoritative)
  AGENT_RECOMMENDATION          the agent's proposed outcome (the harness decides)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "uc3.v1"
Outcome = Literal[
    "RECOMMEND_APPROVE", "RECOMMEND_LIMITED_TIME_BOUND_ACCESS", "HUMAN_REVIEW", "RECOMMEND_REJECT"
]
SEVERITY = {
    "RECOMMEND_APPROVE": 0,
    "RECOMMEND_LIMITED_TIME_BOUND_ACCESS": 1,
    "HUMAN_REVIEW": 2,
    "RECOMMEND_REJECT": 3,
}
ClaimType = Literal[
    "OBSERVED_FACT",
    "DETERMINISTIC_CONTROL_RESULT",
    "POLICY_REQUIREMENT",
    "AGENT_INFERENCE",
    "AGENT_RECOMMENDATION",
]
Fault = Literal["identity_unavailable", "uc4_unavailable", "policy_unavailable"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccessRequest(Strict):
    request_id: str = Field(pattern=r"^AR-\d{3}$")
    user_id: str = Field(pattern=r"^u-\d{4}$")
    entitlement_id: str = Field(min_length=3, max_length=60)
    purpose_category: str | None = None
    duration_days: int = Field(ge=1, le=365)
    project_id: str | None = None
    justification: str = Field(default="", max_length=1000)  # UNTRUSTED free text
    simulate_fault: Fault | None = None


class ContextItem(Strict):
    """One evidence item: an id the agent may cite, what it is and where it came from."""

    evidence_id: str
    claim_type: ClaimType
    source: str
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)


class AccessDecisionContext(Strict):
    """The ONLY context the agent ever sees: the request, a pseudonymous subject, and typed evidence
    items. No other user's data, no raw documents, no free text that failed the injection scan."""

    schema_version: str = SCHEMA_VERSION
    request_id: str
    subject: str  # a pseudonym, never the user id
    role_id: str | None
    department: str | None  # present because POL-ACC routes approvals by it; never scored
    requested_entitlement: str
    requested_resource: str
    purpose_category: str | None
    duration_days: int
    project_id: str | None
    justification: str  # the requester's text, or a withheld marker
    justification_flagged: bool
    items: list[ContextItem]
    missing_evidence: list[str]

    def ids(self) -> set[str]:
        return {i.evidence_id for i in self.items}


class Finding(Strict):
    text: str = Field(max_length=400)
    evidence_id: str
    claim_type: Literal["AGENT_INFERENCE"] = "AGENT_INFERENCE"


class Alternative(Strict):
    entitlement_id: str
    duration_days: float = Field(gt=0)


class AccessRecommendation(Strict):
    """The agent's structured output. Everything here is AGENT_INFERENCE / AGENT_RECOMMENDATION."""

    recommended_outcome: Outcome
    alternative: Alternative | None = None
    confidence: Literal["low", "medium", "high"]
    findings: list[Finding] = Field(default_factory=list, max_length=12)
    rationale: str = Field(max_length=800)
    missing_evidence: list[str] = Field(default_factory=list)
    human_review_reasons: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class Decision(Strict):
    """The deterministic harness's result: AUTHORITATIVE."""

    schema_version: str = SCHEMA_VERSION
    rubric_version: str
    outcome: Outcome
    deterministic_outcome: Outcome  # before the agent's view is considered
    alternative: Alternative | None
    reason_codes: list[str]
    hitl_required: bool
    hitl_reasons: list[str]
    agent_recommendation: Outcome | None
    agent_effect: Literal[
        "agreed",
        "raised_adopted",
        "lower_ignored",
        "disagreement_logged",
        "agent_failed",
        "not_run",
    ]
    provisioned: Literal[False] = False  # no grant exists anywhere in UC3


class AnalystDecision(Strict):
    request_id: str
    action: Literal["APPROVE", "REJECT", "MODIFY"]
    modified: Alternative | None = None
    note: str = Field(default="", max_length=500)
    simulated: Literal[True] = True
    not_gold_adjudication: Literal[True] = True
