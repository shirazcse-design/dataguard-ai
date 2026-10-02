"""The deterministic risk / response harness: the ONLY owner of the final response.

score = points(level, high-risk category, destination, verified policy effect, behaviour band,
identity) + verified-exception discount -> band (ALLOW / WARN / ESCALATE) -> floors for verified
prohibitions -> HUMAN_REVIEW triggers. Every point and every rule is a reason code.

The agent is an INPUT, never the decision: its proposal can only raise the outcome to
HUMAN_REVIEW (agent_disagreement_up / agent_requested_review); a lower proposal is ignored and
logged (agent_disagreement_down_ignored). A discount for a DLP exception applies only to a record
returned by the `check_dlp_exception` tool (verified by code), never to the agent's own words.
"""

from __future__ import annotations

from typing import Any

from .config import MappingConfig, RiskRubric
from .schemas import (
    AgentResult,
    Behavior,
    Classification,
    Decision,
    Identity,
    PolicyContext,
)

RANK = {"ALLOW": 0, "WARN": 1, "ESCALATE": 2, "HUMAN_REVIEW": 3}
RISK_LEVEL = {"ALLOW": "LOW", "WARN": "MEDIUM", "ESCALATE": "HIGH"}


def decide(
    *,
    rubric: RiskRubric,
    mapping: MappingConfig,
    destination: str,
    level: str | None,
    classification: Classification | None,
    identity: Identity | None,
    behavior: Behavior | None,
    policy: PolicyContext | None,
    agent: AgentResult | None,
    stage_failures: list[str],
    injection: bool,
) -> Decision:
    p = rubric.points
    factors: list[dict[str, Any]] = []
    reasons: list[str] = []

    exposed = p["destination"][destination] > 0
    gated = rubric.gate_data_points_on_exposure and not exposed

    def add(factor: str, value: Any, pts: int) -> None:
        if gated and factor in ("level", "high_risk_category", "behavior", "identity"):
            pts = 0  # v1.1.0: no exposure, so data/person points do not create DLP risk
        factors.append({"factor": factor, "value": value, "points": pts})
        if pts:
            reasons.append(f"{factor}:{value}:{pts:+d}")

    add("level", level or "unknown", p["level"].get(level, 0) if level else 0)
    if classification and classification.high_risk:
        add(
            "high_risk_category",
            ",".join(classification.categories) or "yes",
            p["high_risk_category"],
        )
    add("destination", destination, p["destination"][destination])
    effect = policy.effect if policy else "unknown"
    add("policy_effect", effect, p["policy_effect"][effect])
    if behavior:
        add("behavior", behavior.band, p["behavior"][behavior.band])
    if identity:
        if identity.employment_type == "contractor":
            add("identity", "contractor", p["identity"]["contractor"])
        if identity.privilege_level == "privileged":
            add("identity", "privileged", p["identity"]["privileged"])
        if identity.employment_status == "notice_period":
            add("identity", "notice_period", p["identity"]["notice_period"])
    exception = agent.verified_exception if agent else None
    if exception:
        add("verified_exception", exception.get("exception_id"), p["verified_exception"])

    if gated:
        reasons.append("gated:no_destination_exposure")
    score = sum(f["points"] for f in factors)
    band = next(b.outcome for b in rubric.bands if score >= b.min)
    rank_lvl = mapping.rank(level)
    for f in rubric.floors:
        hit = (
            f.when == "prohibited_and_level_at_least"
            and effect == "prohibited"
            and rank_lvl >= mapping.rank(f.level)
        ) or (
            f.when == "destination_is"
            and destination == f.destination
            and rank_lvl >= mapping.rank(f.level)
        )
        if not hit:
            continue
        floor = f.exception_floor if exception else f.outcome
        if RANK[floor] > RANK[band]:
            band = floor
            reasons.append(f"floor:{f.when}:{f.level}->{floor}")
    outcome = band

    triggers: list[str] = []
    if (
        classification is None
        or not classification.ok
        or classification.review_required
        or not level
    ):
        triggers.append("classification_uncertain")
    if policy and policy.conflict:
        triggers.append("policy_conflict")
    if (
        policy is not None
        and policy.status in ("INSUFFICIENT_EVIDENCE", "UNAVAILABLE")
        and rank_lvl >= mapping.rank("CONFIDENTIAL")
        and destination != "approved_corporate"
    ):
        triggers.append("policy_insufficient_high_impact")
    if stage_failures:
        triggers.append("stage_failure")
    proposal = agent.proposed_outcome if agent else None
    if proposal and proposal != "HUMAN_REVIEW" and RANK[proposal] > RANK[band]:
        triggers.append("agent_disagreement_up")
    if proposal and RANK.get(proposal, 0) < RANK[band]:
        reasons.append(f"agent_disagreement_down_ignored:{proposal}")
    if agent and (agent.review_requested or proposal == "HUMAN_REVIEW"):
        triggers.append("agent_requested_review")
    if agent and agent.stopped_reason not in ("final_answer", "not_run") and band != "ALLOW":
        triggers.append("agent_failure_high_impact")
    if injection and rank_lvl >= mapping.rank("CONFIDENTIAL"):
        triggers.append("injection_with_sensitive_data")
    triggers = [t for t in dict.fromkeys(triggers) if t in rubric.human_review_triggers]
    if triggers:
        outcome = "HUMAN_REVIEW"
        reasons += [f"review:{t}" for t in triggers]
    if stage_failures:
        reasons += [f"stage_failed:{s}" for s in stage_failures]

    human = outcome == "HUMAN_REVIEW" or (
        outcome == "ESCALATE" and rubric.escalate_requires_human_approval
    )
    action = None
    if band in ("WARN", "ESCALATE") or outcome == "HUMAN_REVIEW":
        action = rubric.simulated_actions.get(destination)
        if destination == "approved_corporate":
            action = None
    return Decision(
        rubric_version=rubric.rubric_version,
        mapping_version=mapping.mapping_version,
        score=score,
        band=band,
        outcome=outcome,
        risk_level=RISK_LEVEL[band] if outcome != "HUMAN_REVIEW" or level else "UNDETERMINED",
        contributing_factors=factors,
        reason_codes=reasons,
        human_review_required=human,
        simulated_action=action,
        agent_proposal=proposal,
    )
