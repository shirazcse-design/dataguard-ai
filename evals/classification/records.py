"""Per-document prediction records: the harness's unit of accounting.

A record exists for EVERY evaluated document, including failures; nothing is ever dropped.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.classification.schemas.common import StrictModel

NO_PREDICTION = "NO_PREDICTION"  # sentinel column in the level confusion matrix

RecordStatus = Literal["ok", "degraded", "review_required", "rejected", "error"]


class PredictionRecord(StrictModel):
    # identity / slicing fields (copied from the dataset document; never shown to the classifier)
    doc_id: str
    group_id: str
    family_id: str
    split: str
    tier: str
    format: str
    generator: str
    ambiguity_flag: bool
    decoy_for: list[str] = Field(default_factory=list)  # T4: what this hard negative resembles

    # ground truth (from the dataset + the configured high-risk definition)
    gold_level: str
    gold_categories: list[str]
    gold_high_risk: bool

    # what the classifier returned
    status: RecordStatus
    failure: str | None = None  # why there is no usable prediction (exception CLASS only, no text)
    has_prediction: bool
    pred_level: str | None = None
    pred_categories: list[str] = Field(default_factory=list)
    pred_high_risk: bool = False  # ALWAYS re-derived by the harness from the configured definition
    review_required: bool = False
    abstained: bool = False  # the approach reported no decisive level signal
    level_probs: dict[str, float] | None = None
    category_probs: dict[str, float] | None = None
    scores_calibrated: bool = False
    classifier_high_risk_mismatch: bool = False  # classifier's own high_risk disagreed with ours

    # LLM-specific accounting (empty/zero for other approaches)
    level_confidence: str | None = None  # verbalized bucket; never a calibrated probability
    category_confidence: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    evidence_total: int = 0  # llm_excerpt quotes returned
    evidence_verified: int = 0  # of those, found in the text that was sent
    guardrail_types: list[str] = Field(default_factory=list)

    # routing accounting (hybrid; empty for single-stage approaches)
    stop_reason: str | None = None
    stages_run: list[str] = Field(default_factory=list)
    escalations: int = 0
    short_circuited: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    routing_flags: list[str] = Field(default_factory=list)  # fusion:* / conflict:* / budget:* trace

    stage_latency_ms: dict[str, float] = Field(default_factory=dict)  # per-stage, as reported

    # cost / time
    latency_ms: float  # wall-clock around classify(), measured by the harness
    reported_latency_ms: float | None = None  # what the classifier says it spent
    est_cost_usd: float | None = None

    @property
    def deferred(self) -> bool:
        return self.status == "review_required"

    @property
    def failed(self) -> bool:
        return not self.has_prediction
