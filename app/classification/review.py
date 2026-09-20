"""Human-review decision: reason codes, provisional label and priority (architecture section 13)."""

from __future__ import annotations

from .schemas import ReviewDecision

# Order in which codes are listed when several apply (most actionable first).
_ORDER = [
    "INJECTION_DOWNGRADE_ATTEMPT",
    "DETECTOR_CONFLICT",
    "EVIDENCE_UNVERIFIED",
    "BUDGET_EXHAUSTED",
    "LLM_UNAVAILABLE",
    "TRUNCATED_LOW_CONF",
    "LOW_CONFIDENCE",
    "LEVEL_CATEGORY_INCONSISTENT",
    "EXTRACTION_FAILURE",
]


def build_review(
    codes: list[str], *, provisional: bool, provisional_high_risk: bool
) -> ReviewDecision:
    """No codes means no review. With codes, `provisional` says a best-effort label accompanies it
    and the priority puts provisional high-risk documents first (1 before 2)."""
    unique = sorted(set(codes), key=lambda c: _ORDER.index(c) if c in _ORDER else len(_ORDER))
    if not unique:
        return ReviewDecision()
    return ReviewDecision(
        required=True,
        reason_codes=unique,  # type: ignore[arg-type]
        provisional=provisional,
        priority=1 if (provisional and provisional_high_risk) else 2,
    )
