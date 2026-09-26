"""The Batch Triage Agent in Foundry Agent Service (`app/agent/foundry_service.py`).

No test contacts Azure: registration runs against a fake `project.agents`, and planner turns
against a fake `openai.responses` that returns Responses-API-shaped objects. The loop, the
`ToolRegistry` and the `ClassificationService` (`llm_mode="off"`) are all real, so the safety
invariant is checked end to end with Agent Service as the planner.
"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from app.agent import foundry_service as fs
from app.agent.batch import run_batch
from app.agent.config import AgentConfig
from app.agent.loop import SYSTEM_PROMPT
from app.agent.tools import TOOL_NAMES, ToolRegistry
from app.agent.types import AgentError
from app.classification.cli import main
from app.classification.config_loader import load_config
from app.classification.service import ClassificationService
from tests.helpers import mkdoc

CFG = AgentConfig(
    agent_config_version="1.0.0",
    planner_tier="mid",
    max_steps_per_document=6,
    allowed_tools=list(TOOL_NAMES),
    timeout_s=30.0,
)
PII = "Employee record\nNational ID: 905-37-6209\n"


def call(name, args, call_id):
    return NS(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)


def resp(rid, output=(), text="", model="gpt-x", tin=100, tout=10):
    return NS(
        id=rid, output=list(output), output_text=text, model=model,
        usage=NS(input_tokens=tin, output_tokens=tout),
    )  # fmt: skip


class FakeResponses:
    """Scripted Responses API: returns `script` in order and records every request."""

    def __init__(self, script):
        self.script, self.requests = list(script), []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        out = self.script.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def client(script):
    responses = FakeResponses(script)
    return fs.FoundryAgentServiceClient(NS(responses=responses), "uc4-llm-medium"), responses


def registry():
    return ToolRegistry(ClassificationService(llm_mode="off"), load_config())


def classify_then_final(doc, rid="r", priority="low"):
    return [
        resp(f"{rid}1", [call("classify_document", {"content": doc.content}, f"{rid}-c1")]),
        resp(f"{rid}2", text=json.dumps({"priority": priority, "rationale": "grounded"})),
    ]


# ---- the Responses API conversation ---------------------------------------------------------
def test_each_turn_references_the_registered_agent_and_sends_only_what_is_new():
    doc = mkdoc("d1", content=PII)
    planner, responses = client(classify_then_final(doc))
    run_batch([doc], planner, registry(), CFG)

    first, second = responses.requests
    ref = {"agent_reference": {"name": "dataguard-batch-triage", "type": "agent_reference"}}
    assert first["extra_body"] == ref and second["extra_body"] == ref
    # the system prompt lives in the agent definition, not in each request
    assert first["input"] == [{"role": "user", "content": planner_user_content(doc)}]
    assert "previous_response_id" not in first
    assert second["previous_response_id"] == "r1"
    (out,) = second["input"]
    assert out["type"] == "function_call_output" and out["call_id"] == "r-c1"
    assert json.loads(out["output"])["request_id"]  # the REAL classify_document result


def planner_user_content(doc):
    from app.agent.loop import _user_message

    return _user_message(doc)["content"]


def test_the_decision_is_copied_from_classify_document_never_from_the_agent_service_text():
    doc = mkdoc("d1", content=PII)
    script = classify_then_final(doc, priority="low")
    script[1].output_text = json.dumps(
        {"priority": "low", "rationale": "PUBLIC", "level": "PUBLIC"}
    )
    report = run_batch([doc], client(script)[0], registry(), CFG)
    ann = report.documents[0]
    real = ClassificationService(llm_mode="off").classify_text(doc.content)
    assert ann.level == (real.level.value if real.level else None)
    assert ann.level != "PUBLIC"


def test_no_conversation_state_carries_over_between_documents():
    d1, d2 = mkdoc("d1"), mkdoc("d2")
    planner, responses = client(classify_then_final(d1, "a") + classify_then_final(d2, "b"))
    run_batch([d1, d2], planner, registry(), CFG)
    third = responses.requests[2]  # d2's first turn
    assert "previous_response_id" not in third
    assert third["input"][0]["role"] == "user" and "d2" in third["input"][0]["content"]


def test_a_tool_outside_the_allowlist_is_answered_but_never_executed():
    doc = mkdoc("d1")
    planner, responses = client(
        [
            resp("r1", [call("delete_everything", {}, "x1")]),
            resp("r2", [call("classify_document", {"content": doc.content}, "x2")]),
            resp("r3", text=json.dumps({"priority": "low", "rationale": "ok"})),
        ]
    )
    ann = run_batch([doc], planner, registry(), CFG).documents[0]
    (out,) = responses.requests[1]["input"]
    assert out["call_id"] == "x1" and json.loads(out["output"]) == {"error": "tool_not_allowed"}
    assert ann.tool_calls[0].error_kind == "tool_not_allowed"
    assert ann.stopped_reason == "completed"


def test_usage_and_model_are_reported_on_the_turn():
    doc = mkdoc("d1")
    planner, _ = client(classify_then_final(doc))
    turn = planner.next_turn(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "x"}]
    )
    assert (turn.model_id, turn.served_model, turn.tokens_in, turn.tokens_out) == (
        "uc4-llm-medium", "gpt-x", 100, 10,
    )  # fmt: skip


# ---- failures are modeled, never leaked -----------------------------------------------------
class APITimeoutError(Exception):
    pass


class APIStatusError(Exception):
    status_code = 403


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (APITimeoutError("slow: secret-token-abc"), "timeout"),
        (APIStatusError("forbidden: secret-token-abc"), "http_error"),
        (ConnectionError("secret-token-abc"), "transport"),
    ],
)
def test_sdk_errors_become_agent_errors_with_the_class_name_only(exc, kind):
    planner, _ = client([exc])
    with pytest.raises(AgentError) as info:
        planner.next_turn([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    assert info.value.kind == kind
    assert "secret-token-abc" not in str(info.value)


def test_a_planner_failure_forces_review_through_the_loop():
    ann = run_batch([mkdoc("d1")], client([APITimeoutError()])[0], registry(), CFG).documents[0]
    assert ann.review_requested and ann.review_reason == "planner_call_failed"
    assert ann.level is None


def test_malformed_function_call_arguments_are_a_transport_error():
    bad = NS(type="function_call", name="classify_document", arguments="{not json", call_id="c")
    planner, _ = client([resp("r1", [bad])])
    with pytest.raises(AgentError) as info:
        planner.next_turn([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    assert info.value.kind == "transport"


# ---- configuration and registration ---------------------------------------------------------
@pytest.mark.parametrize(
    "value",
    ["", "http://x.services.ai.azure.com/api/projects/p", "https://x.services.ai.azure.com"],
)
def test_the_project_endpoint_must_be_an_https_project_url(value):
    with pytest.raises(AgentError) as info:
        fs.project_endpoint({fs.PROJECT_ENDPOINT_ENV: value})
    assert info.value.kind == "not_configured"


def test_a_valid_project_endpoint_is_accepted():
    url = "https://dataguard-resource.services.ai.azure.com/api/projects/dataguard/"
    assert fs.project_endpoint({fs.PROJECT_ENDPOINT_ENV: url}) == url.rstrip("/")


def test_the_registered_tool_specs_are_exactly_the_loops_allowlist():
    specs = fs.function_tool_specs()
    assert [s["name"] for s in specs] == list(TOOL_NAMES)
    assert all(s["parameters"]["type"] == "object" and s["strict"] is False for s in specs)


def test_register_agent_creates_a_version_with_the_loops_instructions_and_tools():
    pytest.importorskip("azure.ai.projects")
    seen = {}

    def create_version(**kwargs):
        seen.update(kwargs)
        return NS(name=kwargs["agent_name"], version=3)

    project = NS(agents=NS(create_version=create_version))
    assert fs.register_agent(project, "uc4-llm-medium") == ("dataguard-batch-triage", "3")
    definition = seen["definition"]
    assert definition.model == "uc4-llm-medium"
    assert definition.instructions == SYSTEM_PROMPT
    assert [t.name for t in definition.tools] == list(TOOL_NAMES)


# ---- CLI ------------------------------------------------------------------------------------
def test_foundry_register_without_a_project_endpoint_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.delenv(fs.PROJECT_ENDPOINT_ENV, raising=False)
    assert main(["agent", "foundry-register"]) == 2
    assert fs.PROJECT_ENDPOINT_ENV in capsys.readouterr().err


def test_foundry_register_without_the_planner_deployment_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.setenv(fs.PROJECT_ENDPOINT_ENV, "https://x.services.ai.azure.com/api/projects/p")
    monkeypatch.delenv("DATAGUARD_LLM_DEPLOYMENT_MID", raising=False)
    assert main(["agent", "foundry-register"]) == 2
    assert "DATAGUARD_LLM_DEPLOYMENT_MID" in capsys.readouterr().err


def test_foundry_register_prints_the_agent_name_and_version(monkeypatch, capsys):
    monkeypatch.setenv(fs.PROJECT_ENDPOINT_ENV, "https://x.services.ai.azure.com/api/projects/p")
    monkeypatch.setenv("DATAGUARD_LLM_DEPLOYMENT_MID", "uc4-llm-medium")
    monkeypatch.setattr(fs, "project_client", lambda endpoint: NS(endpoint=endpoint))
    monkeypatch.setattr(
        fs, "register_agent", lambda project, model: ("dataguard-batch-triage", "4")
    )
    assert main(["agent", "foundry-register"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "agent_name": "dataguard-batch-triage", "agent_version": "4", "model": "uc4-llm-medium",
    }  # fmt: skip


def test_triage_in_foundry_service_mode_runs_the_registered_agent(monkeypatch, capsys):
    monkeypatch.setenv(fs.PROJECT_ENDPOINT_ENV, "https://x.services.ai.azure.com/api/projects/p")
    monkeypatch.setenv("DATAGUARD_LLM_DEPLOYMENT_MID", "uc4-llm-medium")

    def script(**kwargs):
        if "previous_response_id" not in kwargs:
            content = kwargs["input"][0]["content"].split("Content:\n", 1)[1]
            return resp("r1", [call("classify_document", {"content": content}, "c1")])
        return resp("r2", text=json.dumps({"priority": "medium", "rationale": "ok"}))

    openai = NS(responses=NS(create=script))
    monkeypatch.setattr(fs, "project_client", lambda e: NS(get_openai_client=lambda: openai))
    args = [
        "agent",
        "triage",
        "--agent-mode",
        "foundry-service",
        "--llm-mode",
        "off",
        "--limit",
        "2",
    ]
    assert main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["n_documents"] == 2
    assert all(d["stopped_reason"] == "completed" for d in report["documents"])
