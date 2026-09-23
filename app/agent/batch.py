"""Run the Batch Triage Agent's per-document loop over a whole batch (`docs/uc4/agent-plan.md`).

One independent `run_document` call per input document - no cross-document memory, so one
document's content can never leak into another's triage through shared agent context (the same
per-request isolation `ClassificationService` already guarantees, kept true one level up). Every
input document is guaranteed to appear in the output report, even if its own loop fails outright,
so "task completion" is checked by construction here, not just measured afterwards.
"""

from __future__ import annotations

from collections.abc import Iterable

from evals.classification.dataset.schema import DatasetDocument

from .config import AgentConfig
from .loop import run_document
from .schemas import BatchTriageReport, DocumentAnnotation, ToolCallRecord
from .tools import ToolRegistry
from .types import AgentError, AgentLLMClient


def run_batch(
    docs: Iterable[DatasetDocument],
    client: AgentLLMClient,
    registry: ToolRegistry,
    cfg: AgentConfig,
) -> BatchTriageReport:
    docs = list(docs)
    annotations: list[DocumentAnnotation] = []
    for doc in docs:
        try:
            ann = run_document(
                doc,
                client,
                registry,
                allowed_tools=cfg.allowed_tools,
                max_steps=cfg.max_steps_per_document,
            )
        except AgentError:
            # A failure the loop itself did not catch (e.g. a bug, not a modeled AgentError from
            # the client) must still not drop the document - fail safe to review, never silently.
            ann = DocumentAnnotation(
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
        annotations.append(ann)
    return BatchTriageReport(
        agent_config_version=cfg.agent_config_version,
        n_documents=len(annotations),
        documents=annotations,
    )
