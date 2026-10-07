"""UC3 pipeline: request -> precheck -> ONE bounded agent -> deterministic harness -> HITL -> outcome.

uc3.access_request
  > uc3.precheck              schema, injection scan, authoritative FACTS (identity, graph, UC4,
                              UC6, least privilege, SoD), computed independently of the agent
  > uc3.agent                 planner turns > uc3.tool (read-only, bound to this request)
  > uc3.authorization_harness the deterministic outcome; the agent can raise, never lower
  > uc3.hitl                  is a person's approval required, and why
  > uc3.outcome               recommendation vs decision (nothing is provisioned)
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.agent.bounded import AgentRun, BoundedAgent
from app.agent.types import AgentTurn, ParsedToolCall
from observability import span

from . import harness
from .schemas import (
    AccessDecisionContext,
    AccessRecommendation,
    AccessRequest,
    ContextItem,
    Decision,
)
from .services import AccessServices, Facts, alias, compute_facts, pseudonym
from .tools import CaseState, build_tools

REPO = Path(__file__).resolve().parents[2]
AGENT_CONFIG = REPO / "config" / "access" / "agent.v2.yaml"  # v2 after live run 1; v1 kept (frozen)
WITHHELD_JUSTIFICATION = "[withheld: instruction-like text detected in the justification]"
# The agent claims to have changed access: withheld from the analyst and counted (no tool can act).
ACTION_CLAIM = re.compile(
    r"\b(i|we)\s+(have\s+)?(granted|provisioned|added|removed|revoked|assigned)\b"
    r"|\baccess\s+(has\s+been|was|is\s+now)\s+(granted|provisioned|given)\b",
    re.IGNORECASE,
)


def load_agent_config(path: Path = AGENT_CONFIG) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def action_claim(text: str) -> bool:
    return bool(ACTION_CLAIM.search(text or ""))


@dataclass
class GovernanceRun:
    request: dict[str, Any]
    facts: Facts
    context: AccessDecisionContext
    run: AgentRun | None
    recommendation: AccessRecommendation | None
    decision: Decision
    state: CaseState
    ms: float = 0.0
    spans: list[str] = field(default_factory=list)

    def totals(self) -> dict[str, Any]:
        r = self.run
        return {"model_calls": r.model_calls if r else 0, "tool_calls": r.tool_calls if r else 0,
                "tokens_in": r.tokens_in if r else 0, "tokens_out": r.tokens_out if r else 0,
                "turns": r.turns if r else 0, "cost_usd": "NOT_ESTIMATED"}  # fmt: skip


def initial_context(facts: Facts, resource_id: str) -> AccessDecisionContext:
    """What the agent starts from: the request slice only. Everything else it must fetch with a tool,
    so the evidence it cites is evidence it actually retrieved (context engineering, not dumping)."""
    req, ident = facts.request, facts.identity or {}
    flagged = facts.justification_flagged
    summary = f"{req['entitlement_id']} for {req['duration_days']} days, purpose {req.get('purpose_category') or 'unspecified'}"
    if req.get("project_id"):
        summary += f", project {req['project_id']}"
    return AccessDecisionContext(
        request_id=req["request_id"], subject=alias(req["user_id"]), role_id=ident.get("role_id"),
        department=ident.get("department"), requested_entitlement=req["entitlement_id"], requested_resource=resource_id,
        purpose_category=req.get("purpose_category"), duration_days=req["duration_days"], project_id=req.get("project_id"),
        justification=WITHHELD_JUSTIFICATION if flagged else (req.get("justification") or ""), justification_flagged=flagged,
        items=[ContextItem(evidence_id="REQ", claim_type="OBSERVED_FACT", source="access_request", summary=summary)],
        missing_evidence=[],
    )  # fmt: skip


class AccessGovernor:
    def __init__(self, svc: AccessServices, planner: Any, *, mode: str = "offline", backend: str = "offline",
                 tracer: Any = None) -> None:  # fmt: skip
        self.svc, self.planner, self.mode, self.backend, self.tracer = (
            svc,
            planner,
            mode,
            backend,
            tracer,
        )
        self.cfg = load_agent_config()
        self.prompt = (REPO / self.cfg["prompt_file"]).read_text("utf-8").strip()
        self.rubric = harness.load_rubric()

    def agent(self, state: CaseState) -> BoundedAgent:
        c = self.cfg
        return BoundedAgent(c["name"], self.planner, self.prompt, build_tools(state), max_turns=c["max_turns"],
                            max_tool_calls=c["max_tool_calls"], max_failures=c["max_consecutive_tool_failures"],
                            output_model=AccessRecommendation, backend=self.backend, max_chars=c["max_chars"],
                            span_name="uc3.agent", planner_span="uc3.agent.planner", tool_span="uc3.tool",
                            output_filter=action_claim, withheld="[withheld: claimed an access change]",
                            finish_on_budget=c.get("finish_on_budget", False))  # fmt: skip

    def decide(self, request: dict[str, Any]) -> GovernanceRun:
        t0 = time.perf_counter()
        req = AccessRequest.model_validate(request).model_dump()
        root_cm = self.tracer.trace if self.tracer is not None else span
        with root_cm("uc3.access_request", dg__ag__request_id=req["request_id"], dg__ag__subject=pseudonym(req["user_id"]),
                     dg__ag__entitlement=req["entitlement_id"]) as root:  # fmt: skip
            with span("uc3.precheck") as s:
                facts = compute_facts(self.svc, req)
                rid = self.svc.graph.resource_of(req["entitlement_id"])
                s.set(dg__ag__failures=facts.failures, dg__ag__justification_flagged=facts.justification_flagged,
                      dg__ag__sensitivity=(facts.sensitivity or {}).get("level"),
                      dg__ag__policy_sections=[p["citation"] for p in facts.policy], dg__ag__policy_missing=facts.policy_missing,
                      dg__ag__signals=facts.governance.codes() if facts.governance else [])  # fmt: skip
            ctx = initial_context(facts, rid)
            state = CaseState(
                self.svc, facts, policy=self.svc.policy_tools(f"uc3-{req['request_id']}")
            )
            run = self.agent(state).run(ctx.model_dump())
            rec = run.output if isinstance(run.output, AccessRecommendation) else None
            with span("uc3.authorization_harness") as s:
                decision = harness.decide(facts, self.svc.graph, rec, agent_failed=rec is None,
                                          action_claims=run.unsupported_conclusions, rubric=self.rubric)  # fmt: skip
                s.set(dg__ag__deterministic_outcome=decision.deterministic_outcome, dg__ag__outcome=decision.outcome,
                      dg__ag__reason_codes=decision.reason_codes[:20], dg__ag__agent_effect=decision.agent_effect,
                      dg__ag__rubric_version=decision.rubric_version)  # fmt: skip
            with span("uc3.hitl") as s:
                s.set(
                    dg__ag__hitl_required=decision.hitl_required,
                    dg__ag__hitl_reasons=decision.hitl_reasons,
                )
            with span("uc3.outcome") as s:
                s.set(dg__ag__agent_recommendation=decision.agent_recommendation, dg__ag__outcome=decision.outcome,
                      dg__ag__provisioned=False)  # fmt: skip
            root.set(dg__ag__outcome=decision.outcome, dg__ag__hitl_required=decision.hitl_required,
                     dg__ag__model_calls=run.model_calls, dg__ag__tool_calls=run.tool_calls)  # fmt: skip
        return GovernanceRun(
            req, facts, ctx, run, rec, decision, state, ms=(time.perf_counter() - t0) * 1000
        )


# -- offline planner: a deterministic script that reads only its own messages, like a real one ---------
def _tool_results(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    calls = {
        c["id"]: c["function"]["name"]
        for m in messages
        if m["role"] == "assistant" and m.get("tool_calls")
        for c in m["tool_calls"]
    }
    return [
        (calls.get(m["tool_call_id"], "?"), json.loads(m["content"]))
        for m in messages
        if m["role"] == "tool"
    ]


class OfflinePlanner:
    """Gathers the core evidence, then recommends from the deterministic results it was shown. Used
    by tests and the offline evaluation; the real agent runs in replay or live mode."""

    name = "offline-planner"
    model_id = None
    STEPS = (
        "get_user_profile",
        "get_current_entitlements",
        "classify_resource",
        "evaluate_least_privilege",
        "check_sod",
    )

    def next_turn(self, messages: list[dict[str, Any]]) -> AgentTurn:
        ctx = json.loads(messages[1]["content"])
        done = _tool_results(messages)
        names = [n for n, _ in done]
        for i, step in enumerate(self.STEPS):
            if step not in names:
                args = (
                    {"resource_id": ctx["requested_resource"]}
                    if step == "classify_resource"
                    else {}
                )
                return AgentTurn(tool_calls=[ParsedToolCall(f"o{i}", step, args)])
        if "get_policy_section" not in names:
            return AgentTurn(
                tool_calls=[
                    ParsedToolCall(
                        "o9", "get_policy_section", {"policy_id": "POL-ACC", "section": "2"}
                    )
                ]
            )
        res = {n: r for n, r in done}
        ids = sorted({i for _, r in done for i in r.get("evidence_ids", [])})
        lp, sod = res.get("evaluate_least_privilege", {}), res.get("check_sod", {})
        codes = [s["code"] for s in lp.get("signals", [])] if "error" not in lp else []
        alt = lp.get("alternative") if "error" not in lp else None
        if any("error" in r for _, r in done) or ctx["justification_flagged"]:
            outcome = "HUMAN_REVIEW"
        elif sod.get("conflicts") or "EXISTING_ACCESS_SUFFICIENT" in codes:
            outcome = "RECOMMEND_REJECT"
        elif alt:
            outcome = "RECOMMEND_LIMITED_TIME_BOUND_ACCESS"
        else:
            outcome = "RECOMMEND_APPROVE"
        out = {"recommended_outcome": outcome, "confidence": "medium",
               "alternative": {"entitlement_id": alt["entitlement_id"], "duration_days": round(alt["duration_hours"] / 24, 3)} if alt and outcome.endswith("ACCESS") else None,
               "findings": [{"text": f"Deterministic finding: {c}.", "evidence_id": f"LP-{ctx['requested_entitlement']}"} for c in codes[:4]],
               "rationale": "offline script: recommendation follows the deterministic findings it retrieved",
               "missing_evidence": [], "human_review_reasons": [], "evidence_ids": ids}  # fmt: skip
        return AgentTurn(final_text=json.dumps(out))
