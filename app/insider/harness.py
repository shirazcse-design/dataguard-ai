"""The deterministic risk harness (`config/insider/risk.v1.yaml`): AUTHORITATIVE for the bounded
outcome. The Risk Agent (or, in the baselines, the single agent / lean orchestrator) recommends;
this decides.

Order: points -> band -> floors -> agent recommendation (raise adopted, lower ignored or, if 2+
levels lower, human review) -> human-review triggers. Every point and rule is a reason code.

Inputs are authoritative facts collected during the investigation: the Isolation Forest band and
signals, UC4 sensitivity, the case-day external transfer from the log service, UC6 verified
effects, approvals the register verified, identity access context. Deliberately NOT inputs:
demographics, HR data, employment status, notice period.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .case import CaseContext
from .schemas import SEVERITY, Decision

REPO = Path(__file__).resolve().parents[2]
ORDER = ["MONITOR", "INVESTIGATE", "ESCALATE"]


def load_rubric() -> dict[str, Any]:
    return yaml.safe_load((REPO / "config" / "insider" / "risk.v1.yaml").read_text("utf-8"))


def _max(a: str, b: str) -> str:
    return a if SEVERITY[a] >= SEVERITY[b] else b


def decide(
    ctx: CaseContext, recommendation: Any, rubric: dict[str, Any], *, unsupported: int = 0
) -> Decision:
    p = rubric["points"]
    an = ctx.anomaly
    band = an.anomaly_band
    factors: list[dict[str, Any]] = []
    codes: list[str] = []

    def add(factor: str, value: Any, pts: int) -> None:
        factors.append({"factor": factor, "value": value, "points": pts})
        codes.append(f"{factor}:{value}:{pts:+d}")

    add("anomaly_band", band, p["anomaly_band"][band])
    n_sig = (
        min(len(an.contributing_signals), p["max_contributing_signals"]) if band != "NORMAL" else 0
    )
    if n_sig:
        add("contributing_signals", n_sig, n_sig * p["contributing_signal"])
    cls = ctx.classification
    xfer = ctx.transfer()
    external = bool(
        xfer and xfer.get("present") and xfer["destination_class"] != "approved_corporate"
    )
    if cls and cls.max_level:
        if external or not within_role(ctx):
            add("data_level", cls.max_level, p["data_level"][cls.max_level])
            if cls.high_risk_categories:
                add(
                    "high_risk_category",
                    "+".join(cls.high_risk_categories),
                    p["high_risk_category"],
                )
        else:
            codes.append("gated:data_within_role_no_external_transfer")
    if xfer and xfer.get("present"):
        dc = xfer["destination_class"]
        key = "personal" if dc in ("personal_cloud", "personal_email", "generative_ai") else dc
        add("external_transfer", dc, p["external_transfer"].get(key, 15))
    effects = [r.effect for r in ctx.policies.values()]
    if effects:
        worst = max(
            effects,
            key=lambda e: ["unknown", "allowed", "requires_approval", "prohibited"].index(e),
        )
        if p["policy_effect"][worst]:
            add("policy_effect", worst, p["policy_effect"][worst])
    if ctx.identity and ctx.identity.privilege_level == "privileged" and band != "NORMAL":
        add("privileged_access", "privileged", p["privileged_access"])
    if ctx.approvals:
        add("verified_approval", ctx.approvals[0]["ref"], p["verified_approval"])
    score = sum(f["points"] for f in factors)
    scored = next(b["outcome"] for b in rubric["bands"] if score >= b["min"])

    risky_dest = (
        "personal_cloud",
        "personal_email",
        "generative_ai",
        "unknown_external",
        "restricted",
    )
    personal = bool(xfer and xfer.get("present") and xfer["destination_class"] in risky_dest)
    if ctx.approvals and not personal and SEVERITY[scored] > SEVERITY["INVESTIGATE"]:
        scored = "INVESTIGATE"
        codes.append("ceiling:verified_approval")
    floor = "MONITOR"
    if band == "HIGH_ANOMALY":
        floor = "INVESTIGATE"
        if cls and cls.max_level == "HIGHLY_CONFIDENTIAL" and xfer and xfer.get("present") \
                and xfer["destination_class"] != "approved_corporate":  # fmt: skip
            floor = "ESCALATE"
    if SEVERITY[floor] > SEVERITY[scored]:
        codes.append(f"floor:{floor}")
    bounded = _max(scored, floor)
    outcome: str = bounded

    if ctx.early_stop:
        codes.append(f"orchestrator_stopped_early:{ctx.early_stop.split(':')[0]}:risk_assessed")
    rec = getattr(recommendation, "recommended_outcome", None)
    triggers: list[str] = []
    if rec == "HUMAN_REVIEW":
        triggers.append("agent_disagreement")
        codes.append("agent_recommended_human_review")
    elif rec in SEVERITY:
        diff = SEVERITY[rec] - SEVERITY[bounded]
        if diff > 0:
            outcome = rec
            codes.append(f"agent_raised:{rec}")
        elif diff == -1:
            codes.append(f"agent_lower_ignored:{rec}")
        elif diff <= -2:
            triggers.append("agent_disagreement")
            codes.append(f"agent_lower_material:{rec}")

    elevated = band != "NORMAL"
    required = {"identity", "logs", "data", "behavior_series", "behavior", "policy"}
    if any(f in required for f in ctx.failures):
        triggers.append("required_capability_failed")
    if elevated:
        missing = []
        if not ctx.logs_seen and "logs" not in ctx.failures:
            missing.append("timeline")
        if ctx.case.get("file_refs") and cls is None and "data" not in ctx.failures:
            missing.append("data_sensitivity")
        if band == "HIGH_ANOMALY" and not ctx.policies and "policy" not in ctx.failures:
            missing.append("policy")
        if missing:
            triggers.append("required_evidence_missing")
            codes += [f"missing:{m}" for m in missing]
        if cls is not None and cls.uncertain:
            triggers.append("data_uncertain")
        cited, uncited = cited_conflicts(ctx)
        if cited:
            triggers.append("specialist_conflict")
        if uncited:
            codes.append(f"conflicts_uncited_logged:{uncited}")
        if recommendation is None:
            triggers.append("agent_failure")
    if any(r.conflict for r in ctx.policies.values()):
        triggers.append("policy_conflict")
    for r in ctx.policies.values():
        if r.conflict_note == "uc6_conflict_not_material_at_level":
            codes.append(f"policy:{r.topic}:uc6_conflict_not_material_at_level")
    if band == "HIGH_ANOMALY" and ctx.policies and all(r.insufficient for r in ctx.policies.values()) \
            and "prohibited" not in effects:  # fmt: skip
        triggers.append("policy_insufficient_material")
    if unsupported:
        triggers.append("unsupported_conclusion")
    triggers = list(dict.fromkeys(triggers))
    if triggers:
        outcome = "HUMAN_REVIEW"
        codes += [f"review:{t}" for t in triggers]

    return Decision(
        rubric_version=rubric["rubric_version"], score=score, band=scored, floor=floor, outcome=outcome,
        reason_codes=codes, contributing_factors=factors, analyst_review_required=outcome != "MONITOR",
        agent_recommendation=rec, summary=summary(ctx, outcome, xfer),
    )  # fmt: skip


def cited_conflicts(ctx: CaseContext) -> tuple[int, int]:
    """Fix 4 (approved 2026-10-04): an investigator-reported conflict forces review only when it
    cites at least one evidence id that exists in this case. Uncited conflicts are logged."""
    known = ctx.known_ids()
    cited = uncited = 0
    for inv in ctx.investigations:
        for c in inv.conflicting_evidence:
            ids = c.get("evidence_ids") or ([c["evidence_id"]] if c.get("evidence_id") else [])
            if any(isinstance(i, str) and i in known for i in ids):
                cited += 1
            else:
                uncited += 1
    return cited, uncited


def within_role(ctx: CaseContext) -> bool:
    """Rubric 1.2.0: are all case files inside the role's expected data classes? Unknown identity
    (not fetched or failed) = False, so the data points count (the conservative direction)."""
    if ctx.identity is None or ctx.classification is None:
        return False
    expected = set(ctx.identity.expected_data_classes) | {"PUBLIC", "INTERNAL"}
    for f in ctx.classification.files:
        if f.categories and not set(f.categories) <= expected:
            return False
        if not f.categories and (f.level or "UNKNOWN") not in expected:
            return False
    return True


def summary(ctx: CaseContext, outcome: str, xfer: dict[str, Any] | None) -> list[dict[str, str]]:
    """Fixed templates, each labelled with its claim type. Never a statement about intent."""
    an = ctx.anomaly
    out = [{"claim_type": "INFERRED_ANOMALY", "evidence_id": "AS",
            "text": f"Isolation Forest scored this user-day {an.anomaly_score} ({an.anomaly_band}) "
                    "relative to the user's own history. An anomaly is not evidence of intent."}]  # fmt: skip
    for s in an.contributing_signals[:3]:
        ratio = (
            f"{s['ratio_to_baseline']}x baseline"
            if s.get("ratio_to_baseline")
            else f"baseline {s['baseline_median']}"
        )
        out.append({"claim_type": "OBSERVED_FACT", "evidence_id": "AS",
                    "text": f"{s['feature'].replace('_', ' ')}: {s['observed']} ({ratio})."})  # fmt: skip
    if ctx.classification and ctx.classification.max_level:
        out.append({"claim_type": "OBSERVED_FACT", "evidence_id": ctx.classification.files[0].evidence_id,
                    "text": f"UC4 classified the most sensitive case file as {ctx.classification.max_level}."})  # fmt: skip
    if xfer and xfer.get("present"):
        out.append({"claim_type": "OBSERVED_FACT", "evidence_id": "XFER",
                    "text": f"External transfer to a {xfer['destination_class'].replace('_', ' ')} destination "
                            f"({xfer['volume_mb']} MB) on the case date."})  # fmt: skip
    for r in ctx.policies.values():
        if r.claims:
            out.append({"claim_type": "POLICY_REQUIREMENT", "evidence_id": r.claims[0]["evidence_id"],
                        "text": f"Verified policy ({r.claims[0]['citation']}): {r.claims[0]['text']}"})  # fmt: skip
    lead = {
        "MONITOR": "No investigation is warranted on the current evidence; monitoring continues.",
        "INVESTIGATE": "The behaviour is anomalous and warrants a standard analyst investigation.",
        "ESCALATE": "Behaviour is materially anomalous and the correlated evidence warrants priority analyst investigation.",
        "HUMAN_REVIEW": "The automated assessment is not reliable enough on its own; an analyst must review the case.",
    }[outcome]  # fmt: skip
    out.append({"claim_type": "AGENT_INTERPRETATION", "evidence_id": "DECISION", "text": lead})
    return out
