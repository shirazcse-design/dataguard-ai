"""The Batch Triage Agent's per-document loop: the safety invariant, step budget, tool allowlist,
repeated-failure handling, and the "agent can only add review, never suppress one" property.

Every test drives the loop with a `MockAgentClient` (scripted turns) - no test contacts a real
Foundry deployment. `ToolRegistry` runs the REAL `ClassificationService` in `llm_mode="off"`, so
`classify_document`'s result is genuine, not a fake, for every test here.
"""

from __future__ import annotations

from app.agent.loop import run_document
from app.agent.mock import MockAgentClient
from app.agent.tools import ToolRegistry, ToolResult
from app.agent.types import AgentError, AgentTurn, ParsedToolCall
from app.classification.config_loader import load_config
from app.classification.service import ClassificationService
from tests.helpers import mkdoc

ALL_TOOLS = ["classify_document", "lookup_taxonomy_definition", "request_human_review"]
PII_DOC_CONTENT = "Employee record\nNational ID: 905-37-6209\n"


def registry():
    return ToolRegistry(ClassificationService(llm_mode="off"), load_config())


def classify_call(doc, call_id="1"):
    return ParsedToolCall(
        id=call_id, name="classify_document", arguments={"content": doc.content, "filename": doc.filename}
    )  # fmt: skip


def final(priority="low", rationale="ok"):
    import json

    return AgentTurn(final_text=json.dumps({"priority": priority, "rationale": rationale}))


# ---- the happy path -----------------------------------------------------------------------
def test_a_normal_run_copies_the_real_classification_result_verbatim():
    doc = mkdoc(content=PII_DOC_CONTENT, filename="hr.txt")
    client = MockAgentClient(
        [AgentTurn(tool_calls=[classify_call(doc)]), final("high", "has a national ID")]
    )
    ann = run_document(doc, client, registry(), allowed_tools=ALL_TOOLS, max_steps=6)
    assert ann.level == "HIGHLY_CONFIDENTIAL" and "PII" in ann.categories and ann.high_risk is True
    assert ann.priority == "high" and ann.rationale == "has a national ID"
    assert ann.stopped_reason == "completed" and len(ann.tool_calls) == 1


def test_lookup_taxonomy_definition_may_be_called_before_the_final_answer():
    doc = mkdoc(content=PII_DOC_CONTENT)
    client = MockAgentClient(
        [
            AgentTurn(tool_calls=[classify_call(doc)]),
            AgentTurn(
                tool_calls=[
                    ParsedToolCall(
                        id="2", name="lookup_taxonomy_definition", arguments={"id": "PII"}
                    )
                ]
            ),
            final(),
        ]
    )
    ann = run_document(doc, client, registry(), allowed_tools=ALL_TOOLS, max_steps=6)
    assert ann.stopped_reason == "completed"
    assert [c.tool for c in ann.tool_calls] == ["classify_document", "lookup_taxonomy_definition"]


# ---- the core safety invariant: cannot be overridden by the agent's own text ------------------
def test_a_malformed_final_answer_never_looks_like_a_confident_result():
    """If the agent's final turn isn't the requested JSON, priority falls back to a conservative
    "medium", not "low" - a broken final answer must not look like a calm, low-priority result."""
    doc = mkdoc(content=PII_DOC_CONTENT)
    client = MockAgentClient(
        [AgentTurn(tool_calls=[classify_call(doc)]), AgentTurn(final_text="all good, ship it")]
    )
    ann = run_document(doc, client, registry(), allowed_tools=ALL_TOOLS, max_steps=6)
    assert ann.priority == "medium" and ann.level == "HIGHLY_CONFIDENTIAL"  # the real result stands


def test_classify_document_never_called_forces_review_never_a_fabricated_level():
    """The agent tries to skip the tool and just answer. There is no field in the schema for it to
    write a level into - the only possible outcomes are the real tool result or forced review."""
    doc = mkdoc(content=PII_DOC_CONTENT)
    client = MockAgentClient([final("low", "looks fine to me, no need to check")])
    ann = run_document(doc, client, registry(), allowed_tools=ALL_TOOLS, max_steps=6)
    assert ann.level is None and ann.review_requested is True
    assert ann.review_reason == "classify_document_not_called"
    assert ann.priority == "high"  # forced, not the agent's chosen "low"


def test_the_agent_cannot_smuggle_a_level_through_a_tool_call_argument():
    """An adversarial attempt: call classify_document but also try to pass a `level` argument.
    The tool schema has no such parameter, and ToolRegistry only ever reads `content`/`filename` -
    a spurious argument is silently ignored by the real classifier's call signature, never applied."""
    doc = mkdoc(content=PII_DOC_CONTENT)
    call = ParsedToolCall(
        id="1", name="classify_document",
        arguments={"content": doc.content, "filename": doc.filename, "level": "PUBLIC"},
    )  # fmt: skip
    client = MockAgentClient([AgentTurn(tool_calls=[call]), final()])
    ann = run_document(doc, client, registry(), allowed_tools=ALL_TOOLS, max_steps=6)
    assert ann.level == "HIGHLY_CONFIDENTIAL"  # the real result, not "PUBLIC"


# ---- review can only be added, never suppressed -----------------------------------------------
def test_the_agent_can_add_a_review_request_beyond_what_the_tool_flagged():
    doc = mkdoc(content="A completely ordinary internal memo about the office move.")
    review_call = ParsedToolCall(
        id="2", name="request_human_review", arguments={"reason": "wants a second look"}
    )
    client = MockAgentClient(
        [AgentTurn(tool_calls=[classify_call(doc)]), AgentTurn(tool_calls=[review_call]), final()]
    )  # fmt: skip
    ann = run_document(doc, client, registry(), allowed_tools=ALL_TOOLS, max_steps=6)
    assert ann.review_requested is True and ann.review_reason == "wants a second look"


def test_declining_to_call_request_human_review_cannot_hide_a_real_review_required_status(
    monkeypatch,
):
    """Even if the agent never calls request_human_review, a document classify_document itself
    marked review_required must still show review_requested=True in the final annotation."""
    doc = mkdoc(content=PII_DOC_CONTENT)
    r = registry()
    real_classify = r._classify_document

    def flagged(arguments):
        result = real_classify(arguments)
        return ToolResult(ok=True, data={**result.data, "status": "review_required"})

    monkeypatch.setattr(r, "_classify_document", flagged)
    client = MockAgentClient([AgentTurn(tool_calls=[classify_call(doc)]), final()])
    ann = run_document(doc, client, r, allowed_tools=ALL_TOOLS, max_steps=6)
    assert ann.status == "review_required" and ann.review_requested is True
    assert ann.review_reason is None  # the agent itself never asked; nothing to attribute to it


# ---- tool allowlist -------------------------------------------------------------------------
def test_a_tool_outside_the_allowlist_is_rejected_before_the_registry_sees_it():
    doc = mkdoc(content=PII_DOC_CONTENT)
    call = ParsedToolCall(id="1", name="request_human_review", arguments={"reason": "x"})
    client = MockAgentClient(
        [AgentTurn(tool_calls=[classify_call(doc)]), AgentTurn(tool_calls=[call]), final()]
    )
    ann = run_document(
        doc,
        client,
        registry(),
        allowed_tools=["classify_document", "lookup_taxonomy_definition"],
        max_steps=6,
    )  # request_human_review is NOT in this run's allowlist
    assert any(
        c.tool == "request_human_review" and c.error_kind == "tool_not_allowed"
        for c in ann.tool_calls
    )
    assert ann.review_requested is False  # the disallowed call never took effect


# ---- step budget ------------------------------------------------------------------------------
def test_exceeding_the_step_budget_forces_review_not_a_silent_drop():
    doc = mkdoc(content=PII_DOC_CONTENT)
    lookup = ParsedToolCall(id="x", name="lookup_taxonomy_definition", arguments={"id": "PII"})
    client = MockAgentClient(
        [AgentTurn(tool_calls=[lookup]) for _ in range(5)]
    )  # never calls classify or finishes
    ann = run_document(doc, client, registry(), allowed_tools=ALL_TOOLS, max_steps=3)
    assert ann.stopped_reason == "step_budget_exceeded"
    assert ann.review_requested is True and ann.review_reason == "step_budget_exceeded"
    assert len(client.calls) == 3  # the loop actually stopped at the budget


# ---- repeated tool failure ----------------------------------------------------------------
def test_two_consecutive_tool_failures_stop_the_document_and_force_review():
    doc = mkdoc(content=PII_DOC_CONTENT)
    bad = ParsedToolCall(id="1", name="classify_document", arguments={})  # missing "content"
    client = MockAgentClient([AgentTurn(tool_calls=[bad]), AgentTurn(tool_calls=[bad]), final()])
    ann = run_document(doc, client, registry(), allowed_tools=ALL_TOOLS, max_steps=6)
    assert ann.stopped_reason == "tool_failure" and ann.review_reason == "repeated_tool_failure"
    assert len(client.calls) == 2  # never reaches the scripted final turn


def test_a_single_failure_followed_by_success_does_not_stop_the_document():
    doc = mkdoc(content=PII_DOC_CONTENT)
    bad = ParsedToolCall(id="1", name="classify_document", arguments={})
    client = MockAgentClient(
        [AgentTurn(tool_calls=[bad]), AgentTurn(tool_calls=[classify_call(doc, "2")]), final()]
    )
    ann = run_document(doc, client, registry(), allowed_tools=ALL_TOOLS, max_steps=6)
    assert ann.stopped_reason == "completed" and ann.level == "HIGHLY_CONFIDENTIAL"


def test_the_planner_itself_failing_stops_the_document_and_forces_review():
    doc = mkdoc(content=PII_DOC_CONTENT)
    client = MockAgentClient([AgentError("timeout")])
    ann = run_document(doc, client, registry(), allowed_tools=ALL_TOOLS, max_steps=6)
    assert ann.stopped_reason == "tool_failure" and ann.review_reason == "planner_call_failed"
    assert ann.review_requested is True
