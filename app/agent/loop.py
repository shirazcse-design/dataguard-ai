"""The Batch Triage Agent's per-document loop (`docs/uc4/agent-plan.md`).

Guardrails enforced HERE, structurally, not left to the prompt:
* **Tool allowlist**: any tool name not in `config.allowed_tools` is rejected before `ToolRegistry`
  ever sees it, and counts as a failed step.
* **Step budget**: `max_steps_per_document`; exceeding it stops the document with
  `stopped_reason="step_budget_exceeded"` and forces a review recommendation.
* **Repeated tool failure**: two consecutive failed tool calls stop the document with
  `stopped_reason="tool_failure"` and force a review recommendation.
* **The core safety invariant**: `level`/`categories`/`high_risk`/`status` on the returned
  `DocumentAnnotation` are copied ONLY from the last successful `classify_document` tool result -
  never from the agent's own text. If `classify_document` was never successfully called, the
  document is forced to review with `review_reason="classify_document_not_called"` rather than
  emitting an unset decision as if it were real.
* **The agent can only ADD review, never suppress one**: `review_requested` is the OR of the
  agent's own `request_human_review` call and `classify_document`'s own `status ==
  "review_required"` - the agent cannot make a document classify_document already flagged look
  clean by simply not calling the review tool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from evals.classification.dataset.schema import DatasetDocument

from .schemas import DocumentAnnotation, ToolCallRecord
from .tools import ToolRegistry
from .types import AgentError, AgentLLMClient

SYSTEM_PROMPT = (
    "You triage one document for a data-security team. Call classify_document exactly once with "
    "the document's content. You may then call lookup_taxonomy_definition to ground your rationale "
    "in the real definition, and request_human_review if you believe this document needs a human "
    "look beyond what classify_document already returned. You do not decide the sensitivity level "
    "or categories yourself - classify_document's result is final and is not yours to change. "
    "When you are done, reply with a final JSON object only: "
    '{"priority": "low"|"medium"|"high", "rationale": "one sentence grounded in the tool results"}.'
)


@dataclass
class _State:
    tool_calls: list[ToolCallRecord]
    consecutive_failures: int = 0
    last_classify_result: dict | None = None
    review_requested_by_agent: bool = False
    review_reason: str | None = None


def _user_message(doc: DatasetDocument) -> dict[str, str]:
    return {
        "role": "user",
        "content": (
            f"Filename: {doc.filename}\n\nContent:\n{doc.content}\n\nTriage this document now."
        ),
    }


def _tool_result_message(call_id: str, result_data: dict) -> dict[str, str]:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result_data)}


def _parse_final(text: str) -> tuple[str, str]:
    """`(priority, rationale)`, defaulting to a conservative "medium"/verbatim-text fallback if the
    final turn was not the requested JSON shape - a malformed final answer must never look like a
    confident "low priority" result."""
    try:
        obj = json.loads(text)
        priority = obj.get("priority")
        rationale = obj.get("rationale", "")
        if priority in ("low", "medium", "high") and isinstance(rationale, str):
            return priority, rationale[:500]
    except (ValueError, TypeError, AttributeError):
        pass
    return "medium", (text or "")[:500]


def run_document(
    doc: DatasetDocument,
    client: AgentLLMClient,
    registry: ToolRegistry,
    *,
    allowed_tools: list[str],
    max_steps: int,
) -> DocumentAnnotation:
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        _user_message(doc),
    ]
    state = _State(tool_calls=[])

    for step in range(1, max_steps + 1):
        try:
            turn = client.next_turn(messages)
        except AgentError:
            return _stopped(doc, state, "tool_failure", "planner_call_failed")

        if not turn.tool_calls:
            priority, rationale = _parse_final(turn.final_text or "")
            return _finalize(doc, state, priority, rationale, "completed")

        messages.append(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                    }
                    for c in turn.tool_calls
                ],
            }
        )
        for call in turn.tool_calls:
            if call.name not in allowed_tools:
                messages.append(_tool_result_message(call.id, {"error": "tool_not_allowed"}))
                state.tool_calls.append(
                    ToolCallRecord(
                        step=step, tool=call.name, ok=False, error_kind="tool_not_allowed"
                    )
                )
                state.consecutive_failures += 1
                continue
            result = registry.call(call.name, call.arguments)
            messages.append(
                _tool_result_message(
                    call.id, result.data if result.ok else {"error": result.error_kind}
                )
            )
            state.tool_calls.append(
                ToolCallRecord(
                    step=step, tool=call.name, ok=result.ok, error_kind=result.error_kind
                )
            )
            if result.ok:
                state.consecutive_failures = 0
                if call.name == "classify_document":
                    state.last_classify_result = result.data
                elif call.name == "request_human_review":
                    state.review_requested_by_agent = True
                    state.review_reason = result.data.get("reason")
            else:
                state.consecutive_failures += 1

            if state.consecutive_failures >= 2:
                return _stopped(doc, state, "tool_failure", "repeated_tool_failure")

    return _stopped(doc, state, "step_budget_exceeded", "step_budget_exceeded")


def _finalize(
    doc: DatasetDocument, state: _State, priority: str, rationale: str, stopped_reason: str
) -> DocumentAnnotation:
    cr = state.last_classify_result
    if cr is None:
        return _stopped(doc, state, "tool_failure", "classify_document_not_called")
    review_from_tool = cr.get("status") == "review_required"
    return DocumentAnnotation(
        doc_id=doc.doc_id,
        request_id=cr.get("request_id", doc.doc_id),
        level=(cr.get("level") or {}).get("value"),
        categories=[c["id"] for c in cr.get("categories", [])],
        high_risk=bool((cr.get("high_risk") or {}).get("value")),
        status=cr.get("status", "error"),
        review_requested=state.review_requested_by_agent or review_from_tool,
        review_reason=state.review_reason if state.review_requested_by_agent else None,
        priority=priority,  # type: ignore[arg-type]
        rationale=rationale,
        tool_calls=state.tool_calls,
        stopped_reason=stopped_reason,  # type: ignore[arg-type]
    )


def _stopped(
    doc: DatasetDocument, state: _State, stopped_reason: str, reason: str
) -> DocumentAnnotation:
    """A fail-safe exit: always forces a review recommendation, and never fabricates a level from
    thin air. If classify_document was already called successfully, its result is still honoured
    (the agent's failure afterwards must not hide a real decision); otherwise the document has no
    level and is marked for review, exactly as the classifier's own review_required path works."""
    cr = state.last_classify_result
    return DocumentAnnotation(
        doc_id=doc.doc_id,
        request_id=(cr or {}).get("request_id", doc.doc_id),
        level=((cr or {}).get("level") or {}).get("value"),
        categories=[c["id"] for c in (cr or {}).get("categories", [])],
        high_risk=bool(((cr or {}).get("high_risk") or {}).get("value")),
        status=(cr or {}).get("status", "error"),
        review_requested=True,
        review_reason=reason,
        priority="high",
        rationale="",
        tool_calls=state.tool_calls,
        stopped_reason=stopped_reason,  # type: ignore[arg-type]
    )
