"""UC2 investigation pipeline and the three architectures of the experiment.

  A. single  - one agent with the union of the read-only tools
  B. lean    - an orchestrator that reads behaviour itself + the Investigation Agent
  C. full    - Orchestrator + Behavior Agent + Investigation Agent + Risk Agent (the UC2 design)

Every architecture shares the same cases, model, authoritative services and deterministic harness;
only who reasons, with what context, differs. Delegation is a tool call that DataGuard executes by
running the next agent with its own fresh context, budget and least-privilege tools; agents never
see each other's conversations, and only structured findings travel between them.

Spans: uc2.case > uc2.anomaly, uc2.orchestrator (> uc2.behavior_agent, uc2.identity, uc2.data
(UC4 nested), uc2.policy (UC6 nested), uc2.investigation_agent, uc2.risk_agent),
uc2.deterministic_risk, uc2.hitl.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from app.agent.types import AgentTurn, ParsedToolCall
from observability import span

from . import harness
from .agents import AgentRun, BoundedAgent, Tool, params
from .case import (
    EVENT_TYPES,
    CaseContext,
    behavior_tools,
    data_policy_tools,
    identity_tools,
    investigation_tools,
)
from .schemas import (
    SCHEMA_VERSION,
    BehaviorFinding,
    CasePacket,
    Decision,
    InvestigationFinding,
    RiskRecommendation,
)

REPO = Path(__file__).resolve().parents[2]
ARCHITECTURES = ("single", "lean", "full")


class OrchestratorIncomplete(BaseModel):
    status: str
    gaps: list[str] = []
    reason: str = ""


class SingleReport(RiskRecommendation):
    timeline: list[dict[str, Any]] = []
    observed_facts: list[dict[str, Any]] = []


class LeanReport(RiskRecommendation):
    behavior_summary: str = ""


def pseudonym(user_id: str) -> str:
    """A stable pseudonymous subject id for telemetry: a salted hash, never the user id itself.
    The salt comes from DATAGUARD_TELEMETRY_SALT (a fixed demo salt for the synthetic data)."""
    import hashlib
    import os

    salt = os.environ.get("DATAGUARD_TELEMETRY_SALT", "dataguard-uc2-demo")
    return "subj-" + hashlib.sha256(f"{salt}|{user_id}".encode()).hexdigest()[:12]


def load_agent_config() -> dict[str, Any]:
    return yaml.safe_load((REPO / "config" / "insider" / "agents.v1.yaml").read_text("utf-8"))


@dataclass
class Services:
    data: Any  # services.InsiderData (holds the fitted detector)
    behavior: Any
    identity: Any
    logs: Any
    approvals: Any
    data_uc4: Any
    policy: Any
    copilot: Any
    dlp_cfg: Any

    @property
    def data_svc(self) -> Any:
        return self.data_uc4


@dataclass
class InvestigationResult:
    case_id: str
    architecture: str
    mode: str
    packet: CasePacket
    decision: Decision
    ctx: CaseContext
    runs: list[AgentRun] = field(default_factory=list)
    recommendation: Any = None
    ms: float = 0.0

    def totals(self) -> dict[str, Any]:
        return {
            "agents_run": len(self.runs),
            "model_calls": sum(r.model_calls for r in self.runs),
            "tokens_in": sum(r.tokens_in for r in self.runs),
            "tokens_out": sum(r.tokens_out for r in self.runs),
            "tool_calls": sum(r.tool_calls for r in self.runs),
            "delegations": sum(self.ctx.delegations.values()),
            "repeat_calls": self.ctx.store.repeats,
            "unsupported_conclusions": sum(r.unsupported_conclusions for r in self.runs),
            "agent_failures": sum(0 if r.ok else 1 for r in self.runs),
            "cost_usd": "NOT_ESTIMATED",
        }


class InsiderInvestigator:
    def __init__(self, svc: Services, planners: dict[str, Any], *, architecture: str = "full",
                 mode: str = "offline", backend: str = "offline", tracer: Any = None) -> None:  # fmt: skip
        if architecture not in ARCHITECTURES:
            raise ValueError(f"architecture must be one of {ARCHITECTURES}")
        self.svc, self.planners, self.arch = svc, planners, architecture
        self.mode, self.backend, self.tracer = mode, backend, tracer
        self.cfg = load_agent_config()
        self.rubric = harness.load_rubric()
        self.prompts = {
            k: (REPO / v["prompt_file"]).read_text("utf-8").strip()
            for k, v in self.cfg["agents"].items()
        }

    # -- agent factory ---------------------------------------------------------------------------
    def _agent(
        self, role: str, tools: list[Tool], output: type[BaseModel] | None, span_name: str
    ) -> BoundedAgent:
        a = self.cfg["agents"][role]
        return BoundedAgent(a["name"], self.planners[role], self.prompts[role], tools,
                            max_turns=a["max_turns"], max_tool_calls=a["max_tool_calls"],
                            max_failures=a["max_consecutive_tool_failures"], output_model=output,
                            backend=self.backend, max_chars=self.cfg["max_text_chars"], span_name=span_name)  # fmt: skip

    def _bump(self, ctx: CaseContext, kind: str, limit: int) -> bool:
        if ctx.delegations.get(kind, 0) >= limit:
            return False
        ctx.delegations[kind] = ctx.delegations.get(kind, 0) + 1
        return True

    def _run_behavior(
        self, ctx: CaseContext, focus: str
    ) -> tuple[bool, dict[str, Any], str | None]:
        if not self._bump(
            ctx, "behavior", self.cfg["agents"]["orchestrator"]["max_delegations"]["behavior"]
        ):
            return False, {"error": "delegation_budget_exhausted"}, "delegation_budget_exhausted"
        agent = self._agent("behavior", behavior_tools(ctx), BehaviorFinding, "uc2.behavior_agent")
        run = agent.run({"schema_version": SCHEMA_VERSION, "case_id": ctx.case["id"], "subject": ctx.subject,
                         "date": ctx.date, "focus": (focus or "")[:200],
                         "anomaly": {"score": ctx.anomaly.anomaly_score, "band": ctx.anomaly.anomaly_band}})  # fmt: skip
        ctx.agent_runs.append(run)
        if run.output is None:
            return (
                False,
                {"error": "agent_failed", "stopped_reason": run.stopped_reason},
                "agent_failed",
            )
        f: BehaviorFinding = run.output  # type: ignore[assignment]
        # The model result is authoritative: a finding that changed it is corrected and reported.
        if (
            f.anomaly_band != ctx.anomaly.anomaly_band
            or abs(f.anomaly_score - ctx.anomaly.anomaly_score) > 1e-3
        ):
            ctx.guardrail_events.append({"type": "anomaly_score_modified", "source": "behavior_agent",
                                         "action": "restored_authoritative_value"})  # fmt: skip
            f = f.model_copy(
                update={
                    "anomaly_band": ctx.anomaly.anomaly_band,
                    "anomaly_score": ctx.anomaly.anomaly_score,
                }
            )
        ctx.behavior = f
        return True, f.model_dump(), None

    def _run_investigation(
        self, ctx: CaseContext, args: dict[str, Any], role_budget: str
    ) -> tuple[bool, dict[str, Any], str | None]:
        q = args.get("question")
        if not isinstance(q, str) or not 5 <= len(q) <= 300:
            return (
                False,
                {"error": "invalid_arguments", "detail": "question must be 5-300 characters"},
                "invalid_arguments",
            )
        focus = args.get("focus_event_types") or []
        if not isinstance(focus, list) or any(t not in EVENT_TYPES for t in focus):
            return (
                False,
                {
                    "error": "invalid_arguments",
                    "detail": "focus_event_types must come from the event list",
                },
                "invalid_arguments",
            )
        if not self._bump(
            ctx,
            "investigation",
            self.cfg["agents"][role_budget]["max_delegations"]["investigation"],
        ):
            return False, {"error": "delegation_budget_exhausted"}, "delegation_budget_exhausted"
        lo, hi = ctx.window()
        agent = self._agent("investigation", investigation_tools(ctx) + identity_tools(ctx), InvestigationFinding,
                            "uc2.investigation_agent")  # fmt: skip
        run = agent.run({"schema_version": SCHEMA_VERSION, "case_id": ctx.case["id"], "subject": ctx.subject,
                         "date": ctx.date, "question": q, "focus_event_types": focus, "allowed_window": [lo, hi],
                         "files_available": len(ctx.case.get("file_refs", [])),
                         "anomaly": {"band": ctx.anomaly.anomaly_band,
                                     "signals": [s["feature"] for s in ctx.anomaly.contributing_signals]}})  # fmt: skip
        ctx.agent_runs.append(run)
        if run.output is None:
            return (
                False,
                {"error": "agent_failed", "stopped_reason": run.stopped_reason},
                "agent_failed",
            )
        ctx.investigations.append(run.output)  # type: ignore[arg-type]
        return True, run.output.model_dump(), None

    def bundle(self, ctx: CaseContext) -> dict[str, Any]:
        """What the Risk Agent sees: structured findings only (no raw logs, no document text)."""
        an = ctx.anomaly
        return {
            "schema_version": SCHEMA_VERSION, "case_id": ctx.case["id"], "date": ctx.date,
            "anomaly": {"evidence_id": "AS", "score": an.anomaly_score, "band": an.anomaly_band,
                        "signals": [{k: s[k] for k in ("feature", "observed", "baseline_median", "ratio_to_baseline")}
                                    for s in an.contributing_signals]},
            "identity": ctx.identity.model_dump(exclude={"schema_version"}) if ctx.identity else {"available": False},
            "data_sensitivity": ctx.classification.model_dump(exclude={"schema_version"}) if ctx.classification else None,
            "policy": [r.model_dump(exclude={"schema_version"}) for r in ctx.policies.values()],
            "behavior_finding": ctx.behavior.model_dump(exclude={"schema_version"}) if ctx.behavior else None,
            "investigation_findings": [i.model_dump(exclude={"schema_version"}) for i in ctx.investigations],
            "verified_approvals": [{"evidence_id": "APR", "ref": a["ref"], "type": a["type"]} for a in ctx.approvals],
            "capability_failures": list(ctx.failures),
        }  # fmt: skip

    def _run_risk(self, ctx: CaseContext) -> tuple[bool, dict[str, Any], str | None]:
        if ctx.behavior is None and not ctx.investigations:
            return (
                False,
                {
                    "error": "precondition_failed",
                    "detail": "delegate behaviour or investigation first",
                },
                "precondition_failed",
            )
        if not self._bump(ctx, "risk", 1):
            return False, {"error": "delegation_budget_exhausted"}, "delegation_budget_exhausted"
        agent = self._agent("risk", [], RiskRecommendation, "uc2.risk_agent")
        run = agent.run(self.bundle(ctx))
        ctx.agent_runs.append(run)
        if run.output is None:
            return (
                False,
                {"error": "agent_failed", "stopped_reason": run.stopped_reason},
                "agent_failed",
            )
        ctx.risk = run.output  # type: ignore[assignment]
        return True, ctx.risk.model_dump(), None

    # -- architecture tool sets ------------------------------------------------------------------
    def _orchestrator_tools(self, ctx: CaseContext) -> list[Tool]:
        return [
            Tool("delegate_behavior", "Run the Behavior Agent on this case. Returns a BehaviorFinding.",
                 params({"focus": {"type": "string", "description": "Optional focus, at most 200 characters."}}),
                 lambda a: self._run_behavior(ctx, a.get("focus") or "")),
            Tool("delegate_investigation", "Run the Investigation Agent with a specific question. Returns an InvestigationFinding.",
                 params({"question": {"type": "string"}, "focus_event_types": {"type": "array", "items": {"type": "string", "enum": EVENT_TYPES}}}, ["question"]),
                 lambda a: self._run_investigation(ctx, a, "orchestrator")),
            Tool("get_identity_context", "Deterministic identity and access context for this case's user.",
                 params({}), lambda a: ctx.get_identity()),
            *data_policy_tools(ctx),
            Tool("request_risk_assessment", "Run the Risk Agent on the structured findings gathered so far. Ends the investigation.",
                 params({}), lambda a: self._run_risk(ctx), terminal=True),
        ]  # fmt: skip

    def _lean_tools(self, ctx: CaseContext) -> list[Tool]:
        b = {t.name: t for t in behavior_tools(ctx)}
        return [
            b["get_behavior_profile"], b["get_activity_series"], identity_tools(ctx)[0], *data_policy_tools(ctx),
            Tool("delegate_investigation", "Run the Investigation Agent with a specific question.",
                 params({"question": {"type": "string"}, "focus_event_types": {"type": "array", "items": {"type": "string", "enum": EVENT_TYPES}}}, ["question"]),
                 lambda a: self._run_investigation(ctx, a, "lean_orchestrator")),
        ]  # fmt: skip

    def _single_tools(self, ctx: CaseContext) -> list[Tool]:
        inv = [
            t
            for t in investigation_tools(ctx)
            if t.name not in ("classify_case_files", "request_human_review")
        ]
        return [*behavior_tools(ctx), *identity_tools(ctx), *inv, *data_policy_tools(ctx)]

    # -- run ---------------------------------------------------------------------------------------
    def investigate(self, case: dict[str, Any]) -> InvestigationResult:
        from app.policy.agent import PolicyTools
        from app.policy.pipeline import Run

        t0 = time.perf_counter()
        ctx = CaseContext(case=case, svc=self.svc)
        cp = self.svc.copilot
        ctx.policy_tool = PolicyTools(cp, cp.cfg.levels["agentic"], Run(f"uc2-{case['id']}", "agentic", cp.retriever.embedding_model_id), cp.cfg.agent)  # fmt: skip
        root_cm = (
            self.tracer.trace if self.tracer is not None else span
        )  # activates the shared tracer
        with root_cm(
            "uc2.case",
            dg__ir__case_id=case["id"],
            dg__ir__architecture=self.arch,
            dg__ir__trigger=case["trigger"],
            dg__ir__subject=pseudonym(case["user_id"]),
        ) as root:
            with span("uc2.anomaly") as s:
                ctx.anomaly = self.svc.behavior.anomaly(case["user_id"], case["date"], ctx.store)
                s.set(dg__ir__anomaly_band=ctx.anomaly.anomaly_band, dg__ir__anomaly_score=ctx.anomaly.anomaly_score,
                      dg__ir__model_version=ctx.anomaly.model_version, dg__ir__n_signals=len(ctx.anomaly.contributing_signals))  # fmt: skip
            packet = CasePacket(case_id=case["id"], subject=case["user_id"], date=case["date"], trigger=case["trigger"],
                                anomaly=ctx.anomaly, file_ref_count=len(case.get("file_refs", [])),
                                simulated_faults=[f for f in case.get("faults", []) if f != "policy_unavailable"])  # fmt: skip
            rec: Any = None
            if self.arch == "full":
                orch = self._agent(
                    "orchestrator",
                    self._orchestrator_tools(ctx),
                    OrchestratorIncomplete,
                    "uc2.orchestrator",
                )
                run = orch.run(packet.model_dump())
                ctx.agent_runs.insert(0, run)
                if (
                    ctx.risk is None
                    and not run.stopped_reason.startswith("terminal:")
                    and (ctx.behavior or ctx.investigations)
                ):
                    # Graceful stop: the orchestrator ran out of budget (or stopped early) after
                    # specialist findings exist, so DataGuard runs the Risk Agent on what was
                    # gathered and records that the investigation stopped early.
                    ctx.early_stop = run.stopped_reason
                    self._run_risk(ctx)
                rec = ctx.risk
            elif self.arch == "lean":
                lean = self._agent(
                    "lean_orchestrator", self._lean_tools(ctx), LeanReport, "uc2.orchestrator"
                )
                run = lean.run(packet.model_dump())
                ctx.agent_runs.insert(0, run)
                rec = run.output
            else:
                single = self._agent(
                    "single", self._single_tools(ctx), SingleReport, "uc2.single_agent"
                )
                run = single.run(packet.model_dump())
                ctx.agent_runs.append(run)
                rec = run.output
            unsupported = sum(r.unsupported_conclusions for r in ctx.agent_runs)
            if unsupported:
                ctx.guardrail_events.append(
                    {
                        "type": "unsupported_conclusion",
                        "source": "agent_output",
                        "action": "withheld",
                    }
                )
            with span("uc2.deterministic_risk") as s:
                decision = harness.decide(ctx, rec, self.rubric, unsupported=unsupported)
                s.set(dg__ir__outcome=decision.outcome, dg__ir__score=decision.score, dg__ir__floor=decision.floor,
                      dg__ir__reason_codes=decision.reason_codes[:20])  # fmt: skip
            with span("uc2.hitl") as s:
                s.set(dg__ir__analyst_review_required=decision.analyst_review_required)
            root.set(dg__ir__outcome=decision.outcome, dg__ir__band=ctx.anomaly.anomaly_band,
                     dg__ir__agents_run=len(ctx.agent_runs), dg__ir__delegations=sum(ctx.delegations.values()),
                     dg__ir__early_stop=(ctx.early_stop or "none").split(":")[0],
                     dg__ir__simulated_faults=list(case.get("faults", [])),
                     dg__guardrail__type=sorted({e["type"] for e in ctx.guardrail_events}))  # fmt: skip
        return InvestigationResult(case["id"], self.arch, self.mode, packet, decision, ctx, list(ctx.agent_runs), rec,
                                   (time.perf_counter() - t0) * 1000)  # fmt: skip


# -- offline planners (deterministic stand-ins; NOT models; labelled `offline-planner`) ---------------
def _tool_results(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    calls = {}
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            for c in m["tool_calls"]:
                calls[c["id"]] = c["function"]["name"]
    out = []
    for m in messages:
        if m["role"] == "tool":
            out.append((calls.get(m["tool_call_id"], "?"), json.loads(m["content"])))
    return out


def _call(name: str, n: int, **args: Any) -> AgentTurn:
    return AgentTurn(tool_calls=[ParsedToolCall(f"o{n}", name, args)])


def _final(obj: dict[str, Any]) -> AgentTurn:
    return AgentTurn(final_text=json.dumps(obj))


def offline_outcome(
    band: str, max_level: str | None, external: bool, conflict: bool, failed: bool
) -> str:
    if failed or conflict:
        return "HUMAN_REVIEW"
    if band == "HIGH_ANOMALY" and max_level == "HIGHLY_CONFIDENTIAL" and external:
        return "ESCALATE"
    return "MONITOR" if band == "NORMAL" else "INVESTIGATE"


class OfflinePlanner:
    """One deterministic script per role. It reads only its own messages, like a real planner."""

    name = "offline-planner"
    model_id = None

    def __init__(self, role: str) -> None:
        self.role = role

    def next_turn(self, messages: list[dict[str, Any]]) -> AgentTurn:
        payload = json.loads(messages[1]["content"])
        done = _tool_results(messages)
        names = [n for n, _ in done]
        n = len(done) + 1
        return getattr(self, f"_{self.role}")(payload, done, names, n)

    def _behavior(self, payload, done, names, n):
        if "get_behavior_profile" not in names:
            return _call("get_behavior_profile", n)
        if "get_activity_series" not in names:
            return _call("get_activity_series", n, days=14)
        prof = next(r for k, r in done if k == "get_behavior_profile")
        series_ok = any(k == "get_activity_series" and "error" not in r for k, r in done)
        sig = [c["feature"] for c in prof.get("contributing_signals", [])]
        return _final({
            "behavior_summary": f"Band {prof['anomaly_band']} against the user's own {prof['day_type']} baseline.",
            "anomaly_score": prof["anomaly_score"], "anomaly_band": prof["anomaly_band"],
            "baseline_comparison": [{"feature": f, "today": prof["today"][f], "baseline": prof["baseline_median"][f]} for f in sig],
            "contributing_signals": sig,
            "temporal_observations": [{"text": "Recent daily activity reviewed.", "evidence_id": "SERIES", "claim_type": "OBSERVED_FACT"}] if series_ok else [],
            "evidence_ids": ["AS"] + (["SERIES"] if series_ok else []), "confidence": "high" if series_ok else "medium",
            "gaps": [] if series_ok else ["activity_series"],
        })  # fmt: skip

    def _investigation(self, payload, done, names, n):
        if "search_security_logs" not in names:
            lo, hi = payload["allowed_window"]
            return _call(
                "search_security_logs",
                n,
                start_date=payload["date"],
                end_date=payload["date"],
                limit=50,
            )
        logs = next(r for k, r in done if k == "search_security_logs")
        events = logs.get("events", [])
        refs = [e["ref"] for e in events if e.get("ref")]
        checked = [r for k, r in done if k == "check_approval"]
        if len(checked) < len(refs):
            return _call("check_approval", n, ref=refs[len(checked)])
        conflicts = [{"text": f"Referenced approval {r} is not verified for this user and date.", "evidence_ids": ["APR"]}
                     for r, c in zip(refs, checked, strict=False) if not c.get("verified")]  # fmt: skip
        return _final({
            "timeline": [{"time": e["time"], "event": e["type"], "evidence_id": e["id"]} for e in events],
            "observed_facts": [{"text": f"{e['type']} recorded", "evidence_id": e["id"], "claim_type": "OBSERVED_FACT"} for e in events[:8]],
            "data_findings": [], "policy_findings": [], "correlated_events": [], "conflicting_evidence": conflicts,
            "missing_evidence": [] if "error" not in logs else ["security_logs"],
            "evidence_ids": [e["id"] for e in events], "confidence": "medium" if events else "low",
        })  # fmt: skip

    def _risk(self, payload, done, names, n):
        an = payload["anomaly"]
        ds = payload.get("data_sensitivity") or {}
        conflict = any(
            i.get("conflicting_evidence") for i in payload.get("investigation_findings", [])
        )
        external = any(p.get("topic") == "external_transfer" for p in payload.get("policy", []))
        out = offline_outcome(
            an["band"],
            ds.get("max_level"),
            external,
            conflict,
            bool(payload.get("capability_failures")),
        )
        return _final({"recommended_outcome": out, "reason_codes": [f"band:{an['band']}"], "rationale": "offline heuristic",
                       "evidence_ids": ["AS"], "confidence": "low", "recommended_human_action": "review the case"})  # fmt: skip

    def _orchestrator(self, payload, done, names, n):
        band = payload["anomaly"]["anomaly_band"]
        plan = ["delegate_behavior", "get_identity_context"]
        if band != "NORMAL":
            if payload["file_ref_count"]:
                plan.append("check_data_sensitivity")
            plan += ["delegate_investigation", "check_policy"]
        plan.append("request_risk_assessment")
        for step in plan:
            if step == "check_policy":
                tried = [r for k, r in done if k == "check_policy"]
                if not tried:
                    return _call("check_policy", n, topic="external_transfer")
                if len(tried) == 1 and tried[0].get("error") == "no_external_transfer":
                    return _call("check_policy", n, topic="access_beyond_role")
                continue
            if step not in names:
                if step == "delegate_investigation":
                    return _call(
                        step,
                        n,
                        question="Reconstruct the case-day timeline and verify any referenced approvals.",
                    )
                return _call(step, n)
        return _final(
            {"status": "incomplete", "gaps": ["risk_assessment"], "reason": "plan exhausted"}
        )

    def _lean_orchestrator(self, payload, done, names, n):
        band = payload["anomaly"]["anomaly_band"]
        for step in ("get_behavior_profile", "get_access_context"):
            if step not in names:
                return _call(step, n)
        if band != "NORMAL":
            if payload["file_ref_count"] and "check_data_sensitivity" not in names:
                return _call("check_data_sensitivity", n)
            if "delegate_investigation" not in names:
                return _call(
                    "delegate_investigation",
                    n,
                    question="Reconstruct the case-day timeline and verify any referenced approvals.",
                )
            tried = [r for k, r in done if k == "check_policy"]
            if not tried:
                return _call("check_policy", n, topic="external_transfer")
            if len(tried) == 1 and tried[0].get("error") == "no_external_transfer":
                return _call("check_policy", n, topic="access_beyond_role")
        ds = next((r for k, r in done if k == "check_data_sensitivity"), {})
        inv = next((r for k, r in done if k == "delegate_investigation"), {})
        external = any(
            k == "check_policy" and r.get("topic") == "external_transfer" for k, r in done
        )
        failed = any(
            "error" in r and r.get("error") not in ("no_external_transfer",) for _, r in done
        )
        out = offline_outcome(
            band, ds.get("max_level"), external, bool(inv.get("conflicting_evidence")), failed
        )
        return _final({"recommended_outcome": out, "reason_codes": [f"band:{band}"], "rationale": "offline heuristic",
                       "evidence_ids": ["AS"], "confidence": "low", "recommended_human_action": "review the case",
                       "behavior_summary": f"band {band}"})  # fmt: skip

    def _single(self, payload, done, names, n):
        band = payload["anomaly"]["anomaly_band"]
        steps = ["get_behavior_profile", "get_access_context"]
        if band != "NORMAL":
            steps.append("search_security_logs")
            if payload["file_ref_count"]:
                steps.append("check_data_sensitivity")
        for step in steps:
            if step not in names:
                if step == "search_security_logs":
                    return _call(
                        step, n, start_date=payload["date"], end_date=payload["date"], limit=50
                    )
                return _call(step, n)
        logs = next((r for k, r in done if k == "search_security_logs"), {"events": []})
        refs = [e["ref"] for e in logs.get("events", []) if e.get("ref")]
        checked = [r for k, r in done if k == "check_approval"]
        if len(checked) < len(refs):
            return _call("check_approval", n, ref=refs[len(checked)])
        if band != "NORMAL":
            tried = [r for k, r in done if k == "check_policy"]
            if not tried:
                return _call("check_policy", n, topic="external_transfer")
            if len(tried) == 1 and tried[0].get("error") == "no_external_transfer":
                return _call("check_policy", n, topic="access_beyond_role")
        ds = next((r for k, r in done if k == "check_data_sensitivity"), {})
        external = any(
            k == "check_policy" and r.get("topic") == "external_transfer" for k, r in done
        )
        conflict = any(not c.get("verified") for c in checked)
        failed = any(
            "error" in r and r.get("error") not in ("no_external_transfer",) for _, r in done
        )
        out = offline_outcome(band, ds.get("max_level"), external, conflict, failed)
        return _final({"recommended_outcome": out, "reason_codes": [f"band:{band}"], "rationale": "offline heuristic",
                       "evidence_ids": ["AS"], "confidence": "low", "recommended_human_action": "review the case",
                       "timeline": [{"time": e["time"], "event": e["type"], "evidence_id": e["id"]} for e in logs.get("events", [])]})  # fmt: skip


def offline_planners() -> dict[str, Any]:
    return {
        r: OfflinePlanner(r)
        for r in (
            "orchestrator",
            "behavior",
            "investigation",
            "risk",
            "single",
            "lean_orchestrator",
        )
    }
