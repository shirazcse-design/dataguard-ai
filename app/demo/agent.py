"""Agent Triage for the demo: the REAL Batch Triage Agent (`app/agent/`), one document per request.

Nothing here plans, calls tools or decides. `AgentSession.run` calls the existing `run_batch` on one
curated dev document, through the same traced `ClassificationService` the Classify page uses, and
returns the agent's own `DocumentAnnotation` plus a view built from that run's redacted spans:

* the planner turns and tool calls, in order (`agent.planner` / `agent.tool` spans);
* the safety checks, each computed from evidence in the trace - above all the never-downgrade
  invariant: the agent's level must equal what `classify_document` itself returned (the `classify`
  span's `dg.outcome.level`). A violation is reported as a violation, never hidden or smoothed.

Planners: REPLAY uses the offline deterministic planner (`offline_policy`) - it is NOT a model and
is labelled as such. LIVE uses the agent registered in Foundry Agent Service (Entra ID sign-in; the
optional tenant comes from DATAGUARD_ENTRA_TENANT_ID). A planner that cannot start is reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.batch import run_batch, trace_review_reason
from app.agent.config import load_agent_config
from app.agent.loop import SYSTEM_PROMPT
from app.agent.mock import MockAgentClient
from app.agent.offline_policy import offline_policy
from app.agent.tools import ToolRegistry
from observability import Span

from . import examples
from .classify import ClassifySession, DemoError


@dataclass(frozen=True)
class BatchDoc:
    key: str
    doc_id: str
    title: str
    why: str


# REAL dev-split documents (the recorded LLM responses cover them, so REPLAY is faithful).
AGENT_BATCH: tuple[BatchDoc, ...] = (
    BatchDoc("public", "uc4-2154faf7ae", "Public careers FAQ", "nothing sensitive"),
    BatchDoc("pii-notes", "uc4-288d0c258b", "Manager's private notes", "PII makes it high-risk"),
    BatchDoc("healthcare", "uc4-19c5bd1307", "Prescription record", "PHI"),
    BatchDoc("ci-token", "uc4-312ecaca55", "CI pipeline config with a token", "credentials"),
    BatchDoc(
        "injection-exfil", "uc4-1b97dfb0c8", "Process card with an embedded instruction",
        "adversarial: a prompt injection inside the document",
    ),
    BatchDoc(
        "injection-upgrade", "uc4-4b4b541179", "Offsite notes that ask to be over-labelled",
        "adversarial: text that tries to talk the classifier into a higher label",
    ),
)  # fmt: skip
BATCH = {d.key: d for d in AGENT_BATCH}

PLANNERS = {
    "replay": "Offline deterministic planner (no model): REPLAY / LOCAL DEMO",
    "live": "Foundry Agent Service: dataguard-batch-triage (LIVE)",
}


def agent_view(annotation: dict[str, Any], spans: list[Span]) -> dict[str, Any]:
    """Steps and safety checks for one document, from its own trace. Pure; unit-tested."""
    root = next((s for s in spans if s.name == "agent.document"), None)
    classify = next((s for s in spans if s.name == "classify"), None)
    tool_spans = [s for s in spans if s.name == "agent.tool"]
    steps: list[dict[str, Any]] = []
    for s in spans:
        a = s.attributes
        if s.name == "agent.planner":
            steps.append(
                {
                    "kind": "planner",
                    "step": a.get("dg.agent.step"),
                    "turn": a.get("dg.agent.turn"),
                    "tool_calls": a.get("dg.agent.n_tool_calls"),
                    "model": a.get("dg.llm.model_id"),
                    "tokens": {"in": a.get("dg.tokens_in"), "out": a.get("dg.tokens_out")},
                    "status": s.status,
                    "error": a.get("dg.llm.error_kind"),
                    "duration_ms": (s.end_ns - s.start_ns) / 1e6,
                }
            )
        elif s.name == "agent.tool":
            steps.append(
                {
                    "kind": "tool",
                    "step": a.get("dg.agent.step"),
                    "tool": a.get("dg.agent.tool"),
                    "ok": a.get("dg.agent.tool_ok"),
                    "error": a.get("dg.agent.tool_error"),
                    "args_rebound": a.get("dg.agent.args_rebound"),
                    "duration_ms": (s.end_ns - s.start_ns) / 1e6,
                }
            )
    classifier_level = (classify.attributes.get("dg.outcome.level") or None) if classify else None
    agent_level = annotation.get("level") or None
    classify_called = any(
        s.attributes.get("dg.agent.tool") == "classify_document"
        and s.attributes.get("dg.agent.tool_ok")
        for s in tool_spans
    )
    guard_events = [
        {k.replace("dg.guardrail.", ""): v for k, v in e.attributes.items()}
        for s in spans
        for e in s.events
        if e.name == "guardrail"
    ]
    checks = [
        {
            "check": "Decision copied from classify_document (never-downgrade invariant)",
            "holds": classify_called and agent_level == classifier_level,
            "detail": f"agent level {agent_level or 'none'}; classify_document returned "
            f"{classifier_level or 'none'}"
            + ("" if classify_called else " (classify_document was never called successfully)"),
        },
        {
            "check": "classify_document classified the original document",
            "holds": not any(s.attributes.get("dg.agent.args_rebound") for s in tool_spans),
            "detail": "the loop binds the tool to the document under triage; planner arguments "
            "are ignored",
        },
        {
            "check": "Only allow-listed tools were executed",
            "holds": all(s.attributes.get("dg.agent.tool") != "unlisted" for s in tool_spans),
            "detail": ", ".join(str(s.attributes.get("dg.agent.tool")) for s in tool_spans)
            or "none",
        },
        {
            "check": "The run stopped inside its step budget",
            "holds": annotation.get("stopped_reason") != "step_budget_exceeded",
            "detail": annotation.get("stopped_reason"),
        },
    ]
    return {
        "annotation": annotation,
        "steps": steps,
        "checks": checks,
        "invariant_violated": not checks[0]["holds"],
        "guardrail_events": guard_events,
        "review_reason_recorded": root.attributes.get("dg.agent.review_reason") if root else None,
        "duration_ms": (root.end_ns - root.start_ns) / 1e6 if root else None,
        "spans": [
            {
                "name": s.name,
                "status": s.status,
                "duration_ms": (s.end_ns - s.start_ns) / 1e6,
                "attributes": dict(s.attributes),
            }
            for s in spans
        ],  # fmt: skip
    }


class AgentSession:
    def __init__(self, mode: str, classify: ClassifySession, env: dict[str, str]) -> None:
        self.mode = "live" if mode == "live" else "replay"
        self.classify = classify
        self.env = env
        self.cfg, _ = load_agent_config()
        self._client: Any = None
        self.error: str | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "planner": PLANNERS[self.mode],
            "planner_is_model": self.mode == "live",
            "goal": SYSTEM_PROMPT,
            "allowed_tools": list(self.cfg.allowed_tools),
            "max_steps_per_document": self.cfg.max_steps_per_document,
            "agent_config_version": self.cfg.agent_config_version,
            "batch": [
                {
                    "key": d.key,
                    "doc_id": d.doc_id,
                    "title": d.title,
                    "why": d.why,
                    "family_id": examples._dev_docs()[d.doc_id].family_id,
                }
                for d in AGENT_BATCH
            ],  # fmt: skip
        }

    def _planner(self) -> Any:
        if self._client is not None:
            return self._client
        if self.mode == "replay":
            self._client = MockAgentClient(offline_policy)
            return self._client
        try:
            from app.agent import foundry_service as fs

            deployment = self.env.get(f"DATAGUARD_LLM_DEPLOYMENT_{self.cfg.planner_tier.upper()}")
            if not deployment:
                raise DemoError("the planner deployment variable is not set", status=503)
            project = fs.project_client(
                fs.project_endpoint(self.env), tenant_id=self.env.get("DATAGUARD_ENTRA_TENANT_ID")
            )
            self._client = fs.FoundryAgentServiceClient(project.get_openai_client(), deployment)
        except DemoError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported, never replaced by the offline planner
            raise DemoError(
                f"the live planner did not start: {type(exc).__name__}", status=503
            ) from None
        return self._client

    def run(self, key: str) -> dict[str, Any]:
        doc_def = BATCH.get(key)
        if doc_def is None:
            raise DemoError("unknown batch document")
        svc = self.classify.service
        if svc is None:
            raise DemoError(f"the classifier did not start: {self.classify.error}", status=503)
        client = self._planner()
        doc = examples._dev_docs()[doc_def.doc_id]
        with self.classify._lock:  # one run at a time; the spans collected are this run's
            before = {s.span_id for s in self.classify._sink.spans}
            report = run_batch(
                [doc], client, ToolRegistry(svc, svc.bundle), self.cfg, tracer=svc.tracer
            )
            spans = [s for s in self.classify._sink.spans if s.span_id not in before]
            ids = {s.span_id for s in spans}
            self.classify._sink.spans[:] = [
                s for s in self.classify._sink.spans if s.span_id not in ids
            ]
        annotation = report.documents[0].model_dump(mode="json")
        view = agent_view(annotation, sorted(spans, key=lambda s: s.start_ns))
        view["doc"] = {"key": key, "title": doc_def.title, "why": doc_def.why}
        view["planner"] = PLANNERS[self.mode]
        view["review_reason_fixed"] = trace_review_reason(report.documents[0])
        self.classify.recent.append({"request_id": f"agent-{key}", "spans": view["spans"]})
        return view
