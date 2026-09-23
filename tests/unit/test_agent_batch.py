"""`run_batch` (every input document appears in the output, no cross-document memory) and the
deterministic `offline_policy` planner used by `agent triage --agent-mode mock`."""

from __future__ import annotations

from app.agent.batch import run_batch
from app.agent.config import AgentConfig
from app.agent.loop import run_document
from app.agent.mock import MockAgentClient
from app.agent.offline_policy import offline_policy
from app.agent.tools import ToolRegistry
from app.classification.config_loader import load_config
from app.classification.service import ClassificationService
from tests.helpers import mkdoc

CFG = AgentConfig(
    agent_config_version="1.0.0",
    planner_tier="mid",
    max_steps_per_document=6,
    allowed_tools=["classify_document", "lookup_taxonomy_definition", "request_human_review"],
    timeout_s=30.0,
)


def registry():
    return ToolRegistry(ClassificationService(llm_mode="off"), load_config())


# ---- offline_policy ---------------------------------------------------------------------------
def test_offline_policy_flags_a_pii_document_as_high_priority():
    doc = mkdoc(content="Employee record\nNational ID: 905-37-6209\n", filename="hr.txt")
    ann = run_document(
        doc, MockAgentClient(offline_policy), registry(), allowed_tools=list(CFG.allowed_tools), max_steps=6
    )  # fmt: skip
    assert ann.level == "HIGHLY_CONFIDENTIAL" and ann.priority == "high"
    assert ann.stopped_reason == "completed"
    assert [c.tool for c in ann.tool_calls] == ["classify_document"]


def test_offline_policy_gives_an_undecided_document_medium_not_high_priority():
    """Under `llm_mode="off"` rules alone never confidently decide a low-sensitivity document (no
    LLM to confirm it), so it comes back `review_required` with no high_risk - offline_policy adds
    its own review request but must not escalate an undecided document to "high" the way it does
    for a real high-risk finding."""
    doc = mkdoc(content="A completely ordinary internal memo about the office move.")
    ann = run_document(
        doc, MockAgentClient(offline_policy), registry(), allowed_tools=list(CFG.allowed_tools), max_steps=6
    )  # fmt: skip
    assert ann.status == "review_required" and ann.high_risk is False
    assert ann.priority == "medium" and ann.review_requested is True


def test_offline_policy_preserves_the_original_filename_in_its_tool_call():
    """Regression: the tool call must carry the SAME filename the loop's user message used, or a
    `--llm-mode replay` run would build a different classification prompt than the one recorded -
    a silent cache-miss dressed up as a finding, the same class of bug the fairness probe hit
    earlier in this project (word-for-word: a changed input hash looks like a real result change
    but is a cache artifact)."""
    from app.agent.loop import _user_message

    doc = mkdoc(content="Some content.", filename="notes/README_sdk.md")
    messages = [{"role": "system", "content": "sys"}, _user_message(doc)]
    turn = offline_policy(messages)
    assert turn.tool_calls[0].arguments == {
        "content": "Some content.",
        "filename": "notes/README_sdk.md",
    }


# ---- run_batch ----------------------------------------------------------------------------------
def test_run_batch_covers_every_input_document_exactly_once():
    docs = [
        mkdoc(content="Employee record\nNational ID: 905-37-6209\n", doc_id="d1"),
        mkdoc(content="A completely ordinary internal memo.", doc_id="d2"),
        mkdoc(content="Another plain memo about parking.", doc_id="d3"),
    ]
    report = run_batch(docs, MockAgentClient(offline_policy), registry(), CFG)
    assert report.n_documents == 3
    assert [d.doc_id for d in report.documents] == ["d1", "d2", "d3"]
    assert report.agent_config_version == "1.0.0"


def test_run_batch_never_reuses_context_across_documents():
    """Each document gets its own fresh MockAgentClient turn count - the offline policy never sees
    another document's tool results, so a highly-sensitive doc cannot bleed into a benign one."""
    docs = [
        mkdoc(content="Employee record\nNational ID: 905-37-6209\n", doc_id="d1"),
        mkdoc(content="A completely ordinary internal memo.", doc_id="d2"),
    ]
    report = run_batch(docs, MockAgentClient(offline_policy), registry(), CFG)
    by_id = {d.doc_id: d for d in report.documents}
    assert by_id["d1"].level == "HIGHLY_CONFIDENTIAL"
    assert by_id["d2"].level != "HIGHLY_CONFIDENTIAL"


def test_run_batch_fails_safe_to_review_if_run_document_itself_raises(monkeypatch):
    import app.agent.batch as batch_mod

    def boom(*a, **k):
        from app.agent.types import AgentError

        raise AgentError("timeout", "simulated")

    monkeypatch.setattr(batch_mod, "run_document", boom)
    docs = [mkdoc(content="Employee record\nNational ID: 905-37-6209\n", doc_id="d1")]
    report = run_batch(docs, MockAgentClient(offline_policy), registry(), CFG)
    assert report.n_documents == 1
    ann = report.documents[0]
    assert ann.doc_id == "d1" and ann.review_requested is True
    assert ann.review_reason == "agent_run_failed" and ann.stopped_reason == "tool_failure"
