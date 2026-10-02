"""UC1 agent + pipeline: bounded tools, binding to the event, budgets, malformed results, the agent
never sees document text, injected justifications are withheld, stage failures route to review,
and telemetry carries no document, justification or policy text."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent.mock import MockAgentClient
from app.agent.types import AgentError, AgentTurn, ParsedToolCall
from app.dlp.agent import TOOL_NAMES, DlpAgent, tool_schemas
from app.dlp.schemas import DLPEvent
from app.dlp.service import build_investigator
from evals.dlp.golden import load_golden

CASES = {c.id: c for c in load_golden()[0]}


@pytest.fixture(scope="module")
def inv():
    return build_investigator("offline")


def with_planner(inv, planner):
    inv.agent = DlpAgent(
        planner, inv.dlp.agent, inv.agent.system if inv.agent else "s", backend="test"
    )
    return inv


def call(name, n=1, **args):
    return AgentTurn(tool_calls=[ParsedToolCall(f"c{n}", name, args)])


def final(outcome="ESCALATE", findings=()):
    return AgentTurn(
        final_text=json.dumps(
            {"proposed_outcome": outcome, "findings": list(findings), "missing_evidence": []}
        )
    )


def test_tool_set_is_the_approved_five():
    assert list(TOOL_NAMES) == [
        "search_policy",
        "get_policy_section",
        "get_user_activity",
        "check_dlp_exception",
        "request_human_review",
    ]
    for t in tool_schemas():
        assert t["function"]["parameters"]["additionalProperties"] is False
    exc = next(t for t in tool_schemas() if t["function"]["name"] == "check_dlp_exception")
    assert (
        exc["function"]["parameters"]["properties"] == {}
    )  # bound to the event: no user/host argument


def test_agent_never_receives_document_text(inv):
    seen = {}

    def planner(messages):
        seen["pack"] = messages[1]["content"]
        return final()

    with_planner(inv, MockAgentClient(planner))
    inv.investigate(CASES["D11"].event)
    path = Path("data/dlp/documents/acquisition_targets_2027.json")
    doc = json.loads(path.read_text())["content"]
    for phrase in ("Kestrel Sensor Systems", "Ardent Flowworks", "410-460"):
        assert phrase in doc and phrase not in seen["pack"]


def test_unlisted_tools_and_bad_arguments_are_rejected_and_repeated_failure_stops(inv):
    with_planner(
        inv,
        MockAgentClient(
            [call("block_user", user="u-1003"), call("get_user_activity", 2, days=999)]
        ),
    )
    r = inv.investigate(CASES["D12"].event)
    steps = r.agent.trace["steps"]
    assert [s["error"] for s in steps] == ["unknown_tool", "invalid_arguments"]
    assert r.agent.stopped_reason == "tool_failure" and r.decision.outcome in (
        "HUMAN_REVIEW",
        "ESCALATE",
    )
    assert "review:agent_failure_high_impact" in r.decision.reason_codes


def test_step_budget_is_enforced(inv):
    turns = [call("search_policy", i, query=f"personal cloud {i}") for i in range(10)]
    with_planner(inv, MockAgentClient(turns))
    r = inv.investigate(CASES["D12"].event)
    assert (
        r.agent.stopped_reason == "step_budget_exceeded"
        and r.agent.trace["tool_calls"] == inv.dlp.agent.max_tool_calls
    )
    assert r.decision.outcome != "ALLOW"


def test_malformed_activity_result_is_rejected_not_passed_on(inv):
    with_planner(inv, MockAgentClient([call("get_user_activity", days=7), final()]))
    r = inv.investigate(CASES["D29"].event)
    assert r.agent.trace["steps"][0]["error"] == "malformed_tool_result"
    assert r.simulated_faults == ["malformed_activity_result"]


def test_exception_only_counts_when_the_tool_returned_it(inv):
    with_planner(
        inv,
        MockAgentClient(
            [
                final(
                    "WARN",
                    [{"text": "An exception DLPX-2026-009 applies", "evidence_id": "EXCEPTION"}],
                )
            ]
        ),
    )
    claimed = inv.investigate(CASES["D06"].event)
    assert (
        claimed.agent.verified_exception is None and claimed.agent.findings[0]["verified"] is False
    )
    with_planner(inv, MockAgentClient([call("check_dlp_exception"), final("WARN")]))
    real = inv.investigate(CASES["D06"].event)
    assert real.agent.verified_exception["exception_id"] == "DLPX-2026-009"
    assert any("verified_exception:DLPX-2026-009" in r for r in real.decision.reason_codes)


def test_injected_justification_is_withheld_from_the_agent(inv):
    seen = {}

    def planner(messages):
        seen["pack"] = json.loads(messages[1]["content"])
        return final("ALLOW")

    with_planner(inv, MockAgentClient(planner))
    r = inv.investigate(CASES["D26"].event)
    assert "ignore previous instructions" not in json.dumps(seen["pack"])
    assert seen["pack"]["user_justification"].startswith("[withheld")
    assert any(e["source"] == "user_justification" for e in r.guardrail_events)
    assert r.decision.outcome == "HUMAN_REVIEW"  # and the agent's ALLOW cannot lower it


@pytest.mark.parametrize("case,stage", [("D30", "identity"), ("D23", "policy")])
def test_failed_stage_is_recorded_and_routes_to_review(inv, case, stage):
    with_planner(inv, MockAgentClient([final()]))
    r = inv.investigate(CASES[case].event)
    assert any(s.name == stage and s.status == "failed" for s in r.stages)
    assert (
        r.decision.outcome == "HUMAN_REVIEW" and "review:stage_failure" in r.decision.reason_codes
    )


def test_planner_error_is_safe(inv):
    with_planner(inv, MockAgentClient([AgentError("timeout")]))
    r = inv.investigate(CASES["D12"].event)
    assert r.agent.proposed_outcome is None and r.decision.outcome != "ALLOW"


def test_telemetry_has_no_document_justification_or_policy_text(inv):
    from observability.sinks import MemorySink
    from observability.trace import SeededIds, Tracer

    sink = MemorySink()

    class Everything:
        def scrub(self, attrs):
            return dict(attrs), 0

    inv.tracer = Tracer([sink], Everything(), ids=SeededIds())  # type: ignore[arg-type]
    try:
        with_planner(
            inv,
            MockAgentClient([call("search_policy", query="merger plans personal cloud"), final()]),
        )
        inv.investigate(CASES["D26"].event)
        inv.investigate(CASES["D11"].event)
    finally:
        inv.tracer = None
    names = {s.name for s in sink.spans}
    assert {"uc1.investigation", "uc1.prechecks", "uc1.classification", "uc1.identity", "uc1.behavior",
            "uc1.policy_retrieval", "uc1.agent", "uc1.agent.planner", "uc1.tool", "uc1.risk_decision", "uc1.hitl_decision"} <= names  # fmt: skip
    blob = json.dumps([s.model_dump() for s in sink.spans])
    for secret in (
        "Kestrel",
        "ignore previous instructions",
        "merger plans personal cloud",
        "Approved by my manager",
    ):
        assert secret not in blob


def test_event_schema_refuses_malformed_input():
    with pytest.raises(ValueError):
        DLPEvent(case_id="x", timestamp="yesterday", user_id="bob", action="upload",
                 destination={"host": "Drop Box!", "account_type": "personal"}, document_ref="file:///etc/passwd")  # fmt: skip


def test_foundry_agent_setup_doc_matches_the_code():
    """The manual setup guide must paste EXACTLY what the application expects."""
    doc = Path("docs/uc1/foundry-agent-setup.md").read_text(encoding="utf-8")
    assert Path("prompts/uc1/agent.v1.md").read_text(encoding="utf-8").strip() in doc
    for t in tool_schemas():
        assert f"#### `{t['function']['name']}`" in doc
        assert json.dumps(t["function"]["parameters"], indent=2) in doc
        assert t["function"]["description"] in doc
