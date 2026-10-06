"""UC3 deterministic authorization harness (config/access/rubric.v1.yaml). AUTHORITATIVE.

It decides from the precheck FACTS only (app/access/services.py): identity, graph, UC4, UC6 and the
least-privilege / SoD engine. The agent's recommendation is considered last, and only to RAISE a
concern; it never lowers the deterministic outcome. Nothing here provisions anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .schemas import SEVERITY, AccessRecommendation, Alternative, Decision
from .services import Facts

REPO = Path(__file__).resolve().parents[2]
RUBRIC = REPO / "config" / "access" / "rubric.v1.yaml"


def load_rubric(path: Path = RUBRIC) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _deterministic(f: Facts, graph: Any) -> tuple[str, str, Alternative | None, list[str]]:
    """(outcome, rule_id, alternative, extra reason codes) from the facts alone."""
    if f.failures:
        return (
            "HUMAN_REVIEW",
            "required_capability_failed",
            None,
            [f"failed:{x}" for x in f.failures],
        )
    gov = f.governance
    assert gov is not None
    codes = [f"signal:{c}" for c in gov.codes()]
    has = gov.has
    alt = None
    if gov.alternative:
        alt = Alternative(
            entitlement_id=gov.alternative["entitlement_id"],
            duration_days=round(gov.alternative["duration_hours"] / 24, 3),
        )
    if has("SOD_CONFLICT"):
        return ("HUMAN_REVIEW", "sod_conflict_with_exception", None, codes) if has("EXCEPTION_APPROVED") \
            else ("RECOMMEND_REJECT", "sod_conflict", None, codes)  # fmt: skip
    if has("BUSINESS_PURPOSE_MISSING"):
        return "HUMAN_REVIEW", "business_purpose_missing", None, codes
    if has("EXISTING_ACCESS_SUFFICIENT"):
        return "RECOMMEND_REJECT", "existing_access_sufficient", None, codes
    if has("PROJECT_NOT_ASSIGNED"):
        return "HUMAN_REVIEW", "conflicting_evidence", None, codes
    if f.policy_missing:
        return (
            "HUMAN_REVIEW",
            "policy_evidence_missing",
            None,
            codes + [f"missing:{m}" for m in f.policy_missing],
        )
    if has("PRIVILEGED_ACCESS"):
        return (
            "HUMAN_REVIEW",
            "privileged_access",
            alt,
            codes,
        )  # the alternative (8-hour JIT) is a suggestion
    if has("STALE_EXISTING_ENTITLEMENT"):
        return "HUMAN_REVIEW", "stale_access_review", None, codes
    if alt is not None:
        return "RECOMMEND_LIMITED_TIME_BOUND_ACCESS", "least_privilege_alternative", alt, codes
    ent = graph.entitlement(f.request["entitlement_id"])
    if has("HIGH_SENSITIVITY_RESOURCE") and ent["privilege"] != "read":
        return "HUMAN_REVIEW", "high_sensitivity_write", None, codes
    max_hours = (f.requirements or {}).get("max_hours")
    if max_hours is not None:
        days = min(
            f.request["duration_days"],
            gov.allowed_hours / 24 if gov.allowed_hours else max_hours / 24,
        )
        return "RECOMMEND_LIMITED_TIME_BOUND_ACCESS", "policy_time_bound", \
            Alternative(entitlement_id=f.request["entitlement_id"], duration_days=round(days, 3)), codes  # fmt: skip
    return "RECOMMEND_APPROVE", "approve", None, codes


def decide(f: Facts, graph: Any, rec: AccessRecommendation | None, *, agent_failed: bool = False,
           action_claims: int = 0, rubric: dict[str, Any] | None = None) -> Decision:  # fmt: skip
    rubric = rubric or load_rubric()
    det, rule, alt, codes = _deterministic(f, graph)
    reasons = [f"rule:{rule}", *codes]
    outcome, effect = det, "not_run"
    hitl_reasons: list[str] = []
    if rec is None:
        if agent_failed:
            effect = "agent_failed"
            if SEVERITY[det] < SEVERITY[rubric["agent"]["failure_outcome_floor"]]:
                outcome = rubric["agent"]["failure_outcome_floor"]
                hitl_reasons.append("agent_failed")
    else:
        diff = SEVERITY[rec.recommended_outcome] - SEVERITY[det]
        if diff == 0:
            effect = "agreed"
        elif (
            diff > 0
        ):  # stricter: a raised concern goes to a person (the agent cannot reject alone)
            effect = "raised_adopted"
            if SEVERITY[outcome] < SEVERITY["HUMAN_REVIEW"]:
                outcome = "HUMAN_REVIEW"
            hitl_reasons.append("agent_raised_concern")
        elif diff == -1:
            effect = "lower_ignored"
        else:
            effect = "disagreement_logged"
            hitl_reasons.append("agent_disagreement")
    if action_claims and SEVERITY[outcome] < SEVERITY[rubric["agent"]["action_claim_floor"]]:
        outcome = rubric["agent"]["action_claim_floor"]
        hitl_reasons.append("agent_claimed_action")
    if f.justification_flagged:
        reasons.append("guardrail:justification_withheld")
        if outcome == "RECOMMEND_APPROVE":
            outcome = "HUMAN_REVIEW"
        hitl_reasons.append("instruction_like_justification")
    if outcome in ("HUMAN_REVIEW", "RECOMMEND_REJECT"):
        alt = alt if outcome == "HUMAN_REVIEW" else None
    level = (f.sensitivity or {}).get("level")
    ent = graph.entitlement(f.request["entitlement_id"])
    free = rubric["hitl_free"]
    hitl_required = not (outcome == free["outcome"] and level in free["levels"] and ent["privilege"] == free["privilege"]
                         and not ent["privileged"])  # fmt: skip
    if hitl_required and not hitl_reasons:
        hitl_reasons.append(
            f"rule:{rule}" if outcome != "RECOMMEND_APPROVE" else "data_owner_approval"
        )
    return Decision(rubric_version=rubric["rubric_version"], outcome=outcome, deterministic_outcome=det, alternative=alt,
                    reason_codes=reasons, hitl_required=hitl_required, hitl_reasons=hitl_reasons,
                    agent_recommendation=rec.recommended_outcome if rec else None, agent_effect=effect)  # fmt: skip
