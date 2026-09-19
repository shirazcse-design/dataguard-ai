"""ClassificationResult schema.  [architecture section 3]

Design rules encoded here:
* `high_risk` is DERIVED from the high-risk config after classification; it is never predicted
  directly [DEC-3].
* The classification path always returns a valid result with a `status`; failures are values, not
  exceptions, and never silently default to a low sensitivity [architecture section 19].
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import SCHEMA_VERSION, StrictModel
from .confidence import Confidence
from .evidence import Axis, Evidence

Status = Literal["ok", "degraded", "review_required", "rejected", "error"]
DecidedBy = Literal["rules", "ml", "llm", "fusion", "baseline"]
ReviewReasonCode = Literal[
    "LOW_CONFIDENCE",
    "DETECTOR_CONFLICT",
    "LLM_UNAVAILABLE",
    "EVIDENCE_UNVERIFIED",
    "INJECTION_DOWNGRADE_ATTEMPT",
    "TRUNCATED_LOW_CONF",
    "EXTRACTION_FAILURE",
    "BUDGET_EXHAUSTED",
    "LEVEL_CATEGORY_INCONSISTENT",
]


class LevelPrediction(StrictModel):
    value: str
    confidence: Confidence
    decided_by: DecidedBy


class CategoryPrediction(StrictModel):
    id: str
    confidence: Confidence
    decided_by: DecidedBy
    evidence_ids: list[str] = Field(default_factory=list)


class HighRiskReason(StrictModel):
    axis: Axis
    value: str


class HighRisk(StrictModel):
    value: bool
    reasons: list[HighRiskReason] = Field(default_factory=list)
    config_version: str


class ReviewDecision(StrictModel):
    required: bool = False
    reason_codes: list[ReviewReasonCode] = Field(default_factory=list)
    provisional: bool = False
    priority: int | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ReviewDecision:
        if self.required and not self.reason_codes:
            raise ValueError("review.required=True needs at least one reason code")
        if not self.required and (self.reason_codes or self.provisional):
            raise ValueError("reason codes / provisional only allowed when review.required=True")
        return self


class Routing(StrictModel):
    stages_run: list[str] = Field(default_factory=list)
    stop_reason: str | None = None
    escalations: int = Field(default=0, ge=0)
    short_circuited: bool = False
    # True when the approach found NO decisive signal for the level: the level shown is then a
    # configured default (standalone benchmarks) or a provisional value, not a positive finding.
    abstained: bool = False


class Versions(StrictModel):
    taxonomy: str | None = None
    high_risk_config: str | None = None
    ruleset: str | None = None
    ml_model: str | None = None
    prompt: str | None = None
    llm_deployment: str | None = None
    router_config: str | None = None
    classifier: str | None = None  # name@version of the producing classifier


class Telemetry(StrictModel):
    latency_ms: dict[str, float] = Field(default_factory=dict)
    tokens: dict[str, int] = Field(default_factory=dict)
    est_cost_usd: float | None = None


class GuardrailEvent(StrictModel):
    type: str
    trigger: str
    action: str
    detail: str | None = None


class ClassificationResult(StrictModel):
    schema_version: str = SCHEMA_VERSION
    request_id: str
    document_id: str | None = None
    content_hash: str
    status: Status
    level: LevelPrediction | None = None
    categories: list[CategoryPrediction] = Field(default_factory=list)
    high_risk: HighRisk | None = None
    review: ReviewDecision = Field(default_factory=ReviewDecision)
    evidence: list[Evidence] = Field(default_factory=list)
    routing: Routing = Field(default_factory=Routing)
    versions: Versions = Field(default_factory=Versions)
    telemetry: Telemetry = Field(default_factory=Telemetry)
    guardrail_events: list[GuardrailEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> ClassificationResult:
        if self.status in ("ok", "degraded"):
            if self.level is None:
                raise ValueError(f"status {self.status!r} requires a level")
            if self.high_risk is None:
                raise ValueError(f"status {self.status!r} requires a derived high_risk block")
        if self.status == "review_required" and not self.review.required:
            raise ValueError("status 'review_required' requires review.required=True")
        if self.review.required and self.status in ("ok", "degraded"):
            raise ValueError("review.required=True is incompatible with status ok/degraded")
        cat_ids = [c.id for c in self.categories]
        if len(set(cat_ids)) != len(cat_ids):
            raise ValueError("duplicate category predictions")
        ev_ids = [e.evidence_id for e in self.evidence]
        if len(set(ev_ids)) != len(ev_ids):
            raise ValueError("duplicate evidence ids")
        known = set(ev_ids)
        for c in self.categories:
            missing = [e for e in c.evidence_ids if e not in known]
            if missing:
                raise ValueError(f"category {c.id} references unknown evidence ids {missing}")
        return self
