"""Stage acceptance and conflict tests for the hybrid router (pure functions, no I/O).

Nothing here consults the gold labels; every decision uses only a stage's own result.
"""

from __future__ import annotations

from dataclasses import dataclass

from .policy import TaxonomyPolicy
from .routing_config import BUCKET_RANK, STRENGTH_RANK, LLMStage, MLStage, RulesStage
from .schemas import ClassificationResult


@dataclass(frozen=True)
class Acceptance:
    accepted: bool
    reason: str  # short machine-readable cause, recorded in the result


def rules_sufficient(result: ClassificationResult | None, cfg: RulesStage) -> Acceptance:
    """Rules are sufficient when the level is decisive at >= the configured strength.

    A Rules abstention is a signal to escalate. It is never a finding that the document is Public.
    """
    if result is None or result.level is None:
        return Acceptance(False, "rules_unavailable")
    if result.status == "degraded":
        return Acceptance(False, "rules_degraded")  # a broken detector: evidence may be incomplete
    if result.routing.abstained:
        return Acceptance(False, "rules_abstained")
    conf = result.level.confidence
    if conf.kind != "rule_strength" or conf.raw not in STRENGTH_RANK:
        return Acceptance(False, "rules_no_strength")
    if STRENGTH_RANK[str(conf.raw)] < STRENGTH_RANK[cfg.min_level_strength]:
        return Acceptance(False, "rules_below_min_strength")
    return Acceptance(True, "rules_sufficient")


def ml_accepted(result: ClassificationResult | None, cfg: MLStage) -> Acceptance:
    """ML is accepted only with CALIBRATED scores that are reliable on ALL axes."""
    if result is None or result.level is None or result.scores is None:
        return Acceptance(False, "ml_unavailable")
    sc = result.scores
    if not sc.calibrated or sc.level is None or sc.categories is None:
        return Acceptance(False, "ml_uncalibrated")
    if max(sc.level.values()) < cfg.tau:
        return Acceptance(False, "ml_level_below_tau")
    if any(max(p, 1.0 - p) < cfg.tau for p in sc.categories.values()):
        return Acceptance(False, "ml_category_below_tau")
    return Acceptance(True, "ml_reliable")


def llm_accepted(result: ClassificationResult | None, cfg: LLMStage) -> Acceptance:
    """An LLM result is accepted when it is usable, sufficiently confident, verified and decisive.

    The confidence is the model's verbalized bucket; it is compared as an ordinal, never treated
    as a probability.
    """
    if result is None or result.level is None:
        return Acceptance(False, "llm_no_level")
    if result.status != "ok" or result.review.required:
        codes = ",".join(result.review.reason_codes) or result.status
        return Acceptance(False, f"llm_review:{codes}")
    if result.routing.abstained and not cfg.accept_abstention:
        return Acceptance(False, "llm_insufficient_information")
    floor = BUCKET_RANK[cfg.min_confidence]
    buckets = [result.level.confidence.raw] + [c.confidence.raw for c in result.categories]
    if any(b not in BUCKET_RANK or BUCKET_RANK[str(b)] < floor for b in buckets):
        return Acceptance(False, "llm_low_confidence")
    return Acceptance(True, "llm_accepted")


def has_conflict(
    a: ClassificationResult, b: ClassificationResult, policy: TaxonomyPolicy, level_rank_gap: int
) -> bool:
    """Two results conflict if they disagree on high-risk, or on the level by >= `level_rank_gap`.

    (`level_rank_gap=2` means "by MORE than one rank", the architecture's definition.)
    """
    if a.level is None or b.level is None:
        return False
    if (a.high_risk and b.high_risk) and a.high_risk.value != b.high_risk.value:
        return True
    gap = abs(policy.level_rank(a.level.value) - policy.level_rank(b.level.value))
    return gap >= level_rank_gap
