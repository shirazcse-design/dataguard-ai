"""UC5 pipeline: case packet -> ONE bounded agent investigates -> the harness gathers authoritative
facts itself -> correlation + timeline -> report validation -> severity -> human review -> report.

uc5.incident_case
  > uc5.case_initialization    the MINIMAL packet (case id, alias, trigger, window, file handles)
  > uc5.agent                  planner turns > uc5.tool (read-only, bound to this case); UC4/UC6 spans
  > uc5.authoritative_facts    the harness's own evidence, independent of the agent's tool choices
  > uc5.evidence_correlation   deterministic links, conflicts and gaps
  > uc5.timeline_build         the ordered, precision-preserving timeline
  > uc5.report_validation      every claim resolved to evidence; unsupported claims excluded
  > uc5.deterministic_severity the rubric floor; the agent can raise, never lower
  > uc5.human_review           review status and reasons
  > uc5.final_report           nothing is executed
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.agent.bounded import AgentRun, BoundedAgent
from app.agent.types import AgentTurn, ParsedToolCall
from observability import span

from . import harness
from .correlate import correlate, timeline
from .schemas import CasePacket, Decision, Incident, IncidentReport
from .services import IncidentServices, Ledger, alias, pseudonym
from .tools import CaseState, build_tools
from .validate import WITHHELD, output_filter, validate

REPO = Path(__file__).resolve().parents[2]
AGENT_CONFIG = (
    REPO / "config" / "incident" / "agent.v2.yaml"
)  # v2 after live run 1; v1 kept (frozen)


def load_agent_config(path: Path = AGENT_CONFIG) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def initial_packet(inc: Incident, svc: IncidentServices) -> CasePacket:
    """What the agent starts from: the minimum to begin a safe investigation. No evidence, no labels."""
    lo, hi = svc.window(inc)
    return CasePacket(case_id=inc.case_id, subject=alias(inc.user_id), trigger_type=inc.trigger.type,
                      trigger_time=inc.trigger.time, trigger_summary=inc.trigger.summary, window_start=lo, window_end=hi,
                      case_files=[{"handle": f.handle, "resource_id": f.resource_id} for f in inc.files])  # fmt: skip


@dataclass
class CaseRun:
    incident: Incident
    packet: CasePacket
    run: AgentRun | None
    report: IncidentReport | None
    validation: dict[str, Any]
    facts: harness.Facts
    decision: Decision
    state: CaseState
    ms: float = 0.0
    spans: list[str] = field(default_factory=list)

    def totals(self) -> dict[str, Any]:
        r = self.run
        return {"model_calls": r.model_calls if r else 0, "tool_calls": r.tool_calls if r else 0,
                "tokens_in": r.tokens_in if r else 0, "tokens_out": r.tokens_out if r else 0,
                "turns": r.turns if r else 0, "retries": r.repairs if r else 0, "cost_usd": "NOT_ESTIMATED"}  # fmt: skip

    def final_report(self) -> dict[str, Any]:
        """The analyst's auditable report: the harness decision, the full deterministic timeline and
        correlations, and the agent's VALIDATED claims (unsupported ones are shown as excluded)."""
        r, d, f = self.report, self.decision, self.facts
        retrieved = self.state.ledger.returned
        return {"case_id": self.incident.case_id, "subject": alias(self.incident.user_id),
                "severity": d.severity, "review_status": d.review_status, "incident_status": d.incident_status,
                "decision": d.model_dump(),
                "executive_summary": r.executive_summary if r else None,
                "claims": self.validation["claims"],
                "timeline": [{**e.model_dump(), "retrieved_by_agent": e.evidence_id in retrieved} for e in f.timeline],
                "correlations": [c.model_dump() for c in f.correlations],
                "evidence_gaps": [c.summary for c in f.correlations if c.kind == "gap"] + (r.evidence_gaps if r else []),
                "conflicting_evidence": [c.summary for c in f.correlations if c.kind == "conflict"],
                "recommended_next_steps": r.recommended_next_steps if r else [],
                "affected_files": r.affected_files if r else [],
                "evidence": {k: v.model_dump(exclude={"data"}) for k, v in sorted(f.ledger.items.items())},
                "remediation": "NONE_EXECUTED (recommendations only; an analyst owns every action)"}  # fmt: skip


class IncidentInvestigator:
    def __init__(self, svc: IncidentServices, planner: Any, *, mode: str = "offline", backend: str = "offline",
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
                            output_model=IncidentReport, backend=self.backend, max_chars=c["max_chars"],
                            span_name="uc5.agent", planner_span="uc5.agent.planner", tool_span="uc5.tool",
                            output_filter=output_filter, withheld=WITHHELD, finish_on_budget=c["finish_on_budget"])  # fmt: skip

    def investigate(self, incident: dict[str, Any]) -> CaseRun:
        t0 = time.perf_counter()
        inc = Incident.model_validate(incident)
        root_cm = self.tracer.trace if self.tracer is not None else span
        with root_cm("uc5.incident_case", dg__ic__case_id=inc.case_id, dg__ic__subject=pseudonym(inc.user_id),
                     dg__ic__trigger_type=inc.trigger.type) as root:  # fmt: skip
            with span("uc5.case_initialization") as s:
                packet = initial_packet(inc, self.svc)
                s.set(
                    dg__ic__files=len(inc.files), dg__ic__prompt_version=self.cfg["prompt_version"]
                )
            state = CaseState(self.svc, inc, Ledger(inc.case_id))
            run = self.agent(state).run(packet.model_dump())
            rep = run.output if isinstance(run.output, IncidentReport) else None
            with span("uc5.authoritative_facts") as s:
                facts = harness.compute_facts(self.svc, inc)
                s.set(dg__ic__failures=facts.failures, dg__ic__data_level=facts.level, dg__ic__anomaly_band=facts.anomaly_band,
                      dg__ic__transfers=len(facts.transfers), dg__ic__evidence_count=len(facts.ledger.items),
                      dg__ic__injection_flagged=facts.injection_flagged)  # fmt: skip
            with span("uc5.evidence_correlation") as s:
                facts.correlations = correlate(list(facts.ledger.items.values()))
                s.set(dg__ic__correlations=len(facts.correlations),
                      dg__ic__conflicts=[c.rule for c in facts.correlations if c.kind == "conflict"],
                      dg__ic__gaps=[c.rule for c in facts.correlations if c.kind == "gap"])  # fmt: skip
            with span("uc5.timeline_build") as s:
                facts.timeline = timeline(list(facts.ledger.items.values()))
                s.set(dg__ic__timeline_events=len(facts.timeline),
                      dg__ic__order_uncertain=sum(1 for e in facts.timeline if e.order_uncertain_with))  # fmt: skip
            with span("uc5.report_validation") as s:
                val = validate(rep, state.ledger)
                s.set(dg__ic__claims=val["total"], dg__ic__unsupported_claims=val["unsupported"],
                      dg__ic__withheld_claims=val["withheld"], dg__ic__claim_types=sorted(val["by_type"]))  # fmt: skip
            with span("uc5.deterministic_severity") as s:
                decision = harness.decide(facts, rep, validation=val, review_requests=state.review_requests,
                                          refusals=state.refusals, agent_failed=rep is None, rubric=self.rubric)  # fmt: skip
                s.set(dg__ic__deterministic_severity=decision.deterministic_severity, dg__ic__severity=decision.severity,
                      dg__ic__rule=decision.rule, dg__ic__agent_effect=decision.agent_effect,
                      dg__ic__rubric_version=decision.rubric_version, dg__ic__potential_sev1=decision.potential_sev1)  # fmt: skip
            with span("uc5.human_review") as s:
                s.set(
                    dg__ic__review_status=decision.review_status,
                    dg__ic__review_reasons=decision.review_reasons,
                )
            with span("uc5.final_report") as s:
                s.set(dg__ic__severity=decision.severity, dg__ic__agent_recommendation=decision.agent_recommendation,
                      dg__ic__remediation="NONE_EXECUTED")  # fmt: skip
            root.set(dg__ic__severity=decision.severity, dg__ic__review_status=decision.review_status,
                     dg__ic__model_calls=run.model_calls, dg__ic__tool_calls=run.tool_calls,
                     dg__ic__refusals=len(state.refusals))  # fmt: skip
        return CaseRun(
            inc, packet, run, rep, val, facts, decision, state, ms=(time.perf_counter() - t0) * 1000
        )


# -- offline planner: a deterministic script that reads only its own messages, like a real one ---------
def _results(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    calls = {c["id"]: (c["function"]["name"], json.loads(c["function"]["arguments"] or "{}"))
             for m in messages if m["role"] == "assistant" and m.get("tool_calls") for c in m["tool_calls"]}  # fmt: skip
    return [
        (*calls.get(m["tool_call_id"], ("?", {})), json.loads(m["content"]))
        for m in messages
        if m["role"] == "tool"
    ]


class OfflinePlanner:
    """A scripted investigation used by tests and the offline evaluation: gather, build the timeline,
    close gaps once, then report from the deterministic results it was shown. The real agent runs in
    replay or live mode."""

    name = "offline-planner"
    model_id = None

    def next_turn(self, messages: list[dict[str, Any]]) -> AgentTurn:  # noqa: C901
        pkt = json.loads(messages[1]["content"])
        done = _results(messages)
        names = [n for n, _, _ in done]
        res = {n: r for n, _, r in done if "error" not in r}
        if not done:
            return AgentTurn(tool_calls=[ParsedToolCall("o1", "search_security_logs", {}), ParsedToolCall("o2", "get_dlp_findings", {}),
                                         ParsedToolCall("o3", "classify_files", {}), ParsedToolCall("o4", "get_identity_context", {}),
                                         ParsedToolCall("o5", "get_behavior_findings", {})])  # fmt: skip
        if "get_access_context" not in names:
            calls = [ParsedToolCall(f"a{i}", "get_access_context", {"resource_id": r})
                     for i, r in enumerate(sorted({f["resource_id"] for f in pkt["case_files"]}))]  # fmt: skip
            classes = sorted(
                {
                    a["destination_class"]["class"]
                    for a in res.get("get_dlp_findings", {}).get("alerts", [])
                }
            )
            nonapp = [c for c in classes if c not in ("approved_corporate",)]
            if nonapp and "classify_files" in res:
                calls.append(
                    ParsedToolCall(
                        "p1",
                        "search_policy",
                        {"topic": "external_transfer", "destination_class": nonapp[0]},
                    )
                )
            calls.append(ParsedToolCall("p2", "search_policy", {"topic": "incident_procedure"}))
            refs = sorted(
                {
                    e["ref"]
                    for e in res.get("search_security_logs", {}).get("events", [])
                    if e.get("ref")
                }
            )
            calls += [
                ParsedToolCall(f"r{i}", "check_approval", {"ref": r}) for i, r in enumerate(refs)
            ]
            return AgentTurn(tool_calls=calls)
        if "build_timeline" not in names:
            return AgentTurn(tool_calls=[ParsedToolCall("t1", "build_timeline", {})])
        cor = res.get("build_timeline", {}).get("correlations", [])
        files = res.get("classify_files", {}).get("files", [])
        levels = [f["level"] for f in files if f.get("level")]
        alerts = res.get("get_dlp_findings", {}).get("alerts", [])
        personal = [
            a for a in alerts if a["destination_class"]["class"] not in ("approved_corporate",)
        ]
        sev = (
            "HIGH"
            if personal and "HIGHLY_CONFIDENTIAL" in levels
            else "MEDIUM"
            if personal
            else "LOW"
        )
        claims = []
        for a in alerts:
            claims.append({"topic": "dlp", "claim_type": "OBSERVED_FACT", "text": f"DLP alert at {a['time'][11:]} to {a['destination']}.",
                           "evidence_ids": [a["evidence_id"]]})  # fmt: skip
            claims.append({"topic": "dlp", "claim_type": "DETERMINISTIC_FINDING",
                           "text": f"The destination is classified {a['destination_class']['class']}.",
                           "evidence_ids": [a["destination_class"]["evidence_id"]]})  # fmt: skip
        for f in files:
            claims.append({"topic": "data", "claim_type": "DETERMINISTIC_FINDING" if f.get("level") else "UNKNOWN_OR_GAP",
                           "text": f"{f['handle']} is {f.get('level') or 'not classified'}.", "evidence_ids": [f["evidence_id"]]})  # fmt: skip
        b = res.get("get_behavior_findings")
        if b:
            claims.append({"topic": "behavior", "claim_type": "DETERMINISTIC_FINDING",
                           "text": f"The day's anomaly band is {b['anomaly_band']}.", "evidence_ids": ["UC2-ANOM"]})  # fmt: skip
        for pol in (r for n, _, r in done if n == "search_policy" and "error" not in r):
            for c in pol.get("claims", [])[:2]:
                claims.append({"topic": "policy", "claim_type": "POLICY_REQUIREMENT", "text": f"Policy {c['citation']} applies.",
                               "evidence_ids": [c["evidence_id"]]})  # fmt: skip
        for c in cor:
            claims.append({"topic": "correlation", "claim_type": c["claim_type"], "text": c["summary"][:390],
                           "evidence_ids": [c["correlation_id"]]})  # fmt: skip
        errs = [n for n, _, r in done if "error" in r]
        ids = sorted({i for _, _, r in done for i in r.get("evidence_ids", [])})
        out = {"case_id": pkt["case_id"], "executive_summary": "Offline scripted investigation of the case's evidence.",
               "incident_status": "INSUFFICIENT_EVIDENCE" if errs else ("POTENTIAL_INCIDENT" if sev != "LOW" else "NO_INCIDENT_INDICATED"),
               "severity_recommendation": sev, "claims": claims[:30],
               "evidence_gaps": [c["summary"] for c in cor if c["kind"] == "gap"][:10],
               "conflicting_evidence": [c["summary"] for c in cor if c["kind"] == "conflict"][:6],
               "affected_files": [f["handle"] for f in files], "recommended_next_steps": ["Analyst to review the timeline and gaps."],
               "confidence": "medium", "human_review_requested": bool(errs) or sev == "HIGH", "missing_evidence": errs,
               "evidence_ids": ids}  # fmt: skip
        return AgentTurn(final_text=json.dumps(out))
