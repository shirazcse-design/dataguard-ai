"""Run the Batch Triage Agent's per-document loop over a whole batch (`docs/uc4/agent-plan.md`).

One independent `run_document` call per input document - no cross-document memory, so one
document's content can never leak into another's triage through shared agent context (the same
per-request isolation `ClassificationService` already guarantees, kept true one level up). Every
input document is guaranteed to appear in the output report, even if its own loop fails outright,
so "task completion" is checked by construction here, not just measured afterwards.

With a `tracer`, each document is one trace rooted at an `agent.document` span; the loop's
`agent.planner`/`agent.tool` spans and `classify_document`'s own `classify` span tree nest inside
it. The root carries the outcome as fixed-vocabulary values only: the agent's free-text review
reason and rationale are never recorded (see `trace_review_reason`).
"""

from __future__ import annotations

from collections.abc import Iterable

from evals.classification.dataset.schema import DatasetDocument
from observability import Tracer

from .config import AGENT_NAME, AgentConfig
from .loop import run_document
from .schemas import BatchTriageReport, DocumentAnnotation, ToolCallRecord
from .tools import ToolRegistry
from .types import AgentError, AgentLLMClient

# Review reasons the loop itself sets (a fixed vocabulary). Anything else came from the planner's
# own request_human_review call, i.e. model-written text, and is recorded only as "agent_requested".
_LOOP_REVIEW_REASONS = frozenset(
    {
        "planner_call_failed",
        "repeated_tool_failure",
        "step_budget_exceeded",
        "classify_document_not_called",
        "agent_run_failed",
    }
)


def trace_review_reason(ann: DocumentAnnotation) -> str:
    if not ann.review_requested:
        return "none"
    if ann.review_reason in _LOOP_REVIEW_REASONS:
        return ann.review_reason
    return "agent_requested" if ann.review_reason else "classifier_flagged"


def run_batch(
    docs: Iterable[DatasetDocument],
    client: AgentLLMClient,
    registry: ToolRegistry,
    cfg: AgentConfig,
    *,
    tracer: Tracer | None = None,
) -> BatchTriageReport:
    docs = list(docs)
    annotations: list[DocumentAnnotation] = []
    for doc in docs:
        if tracer is None:
            ann = _run_one(doc, client, registry, cfg)
        else:
            with tracer.trace(
                "agent.document",
                dg__agent__name=AGENT_NAME,
                dg__agent__config_version=cfg.agent_config_version,
                dg__agent__planner=getattr(client, "name", "unknown"),
                dg__document_id=doc.doc_id,
            ) as root:
                ann = _run_one(doc, client, registry, cfg)
                root.set(
                    dg__agent__stopped_reason=ann.stopped_reason,
                    dg__agent__priority=ann.priority,
                    dg__agent__tool_calls=len(ann.tool_calls),
                    dg__agent__review_requested=ann.review_requested,
                    dg__agent__review_reason=trace_review_reason(ann),
                    dg__outcome__status=ann.status,
                    dg__outcome__level=ann.level or "",
                )
                if ann.review_reason == "agent_run_failed":
                    root.fail("agent_run_failed")
        annotations.append(ann)
    return BatchTriageReport(
        agent_config_version=cfg.agent_config_version,
        n_documents=len(annotations),
        documents=annotations,
    )


def _run_one(
    doc: DatasetDocument, client: AgentLLMClient, registry: ToolRegistry, cfg: AgentConfig
) -> DocumentAnnotation:
    try:
        return run_document(
            doc,
            client,
            registry,
            allowed_tools=cfg.allowed_tools,
            max_steps=cfg.max_steps_per_document,
        )
    except AgentError:
        # A failure the loop itself did not catch (e.g. a bug, not a modeled AgentError from
        # the client) must still not drop the document - fail safe to review, never silently.
        return DocumentAnnotation(
            doc_id=doc.doc_id,
            request_id=doc.doc_id,
            status="error",
            review_requested=True,
            review_reason="agent_run_failed",
            priority="high",
            tool_calls=[
                ToolCallRecord(step=1, tool="run_document", ok=False, error_kind="unhandled")
            ],
            stopped_reason="tool_failure",
        )
