"""FoundryAgentClient: the tool-calling chat-completions adapter for the Batch Triage Agent,
against a LOCAL fake server only - mirrors `test_llm_clients.py`'s Foundry adapter tests. No test
here contacts a real Foundry deployment."""

from __future__ import annotations

import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.agent.foundry_agent import FoundryAgentClient, build_agent_client
from app.agent.tools import tool_schemas
from app.agent.types import AgentError
from app.llm.config import FoundryConfig


def fcfg(**over) -> FoundryConfig:
    base = dict(
        endpoint_env="EP", api_version_env=None,
        url_template="{endpoint}/openai/v1/chat/completions",
        auth="api_key", api_key_env="KEY", entra_scope=None,
        max_tokens_param="max_completion_tokens", json_schema_response_format=True,
    )  # fmt: skip
    base.update(over)
    return FoundryConfig(**base)


def env(url, **extra):
    return {"EP": url, "KEY": "sekrit-key-123", **extra}


MESSAGES = [{"role": "system", "content": "sys"}, {"role": "user", "content": "triage this"}]


class _Handler(BaseHTTPRequestHandler):
    behaviour = {"status": 200, "body": None, "headers": {}}
    seen: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        _Handler.seen.append(
            {
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": json.loads(self.rfile.read(length)),
            }
        )
        b = _Handler.behaviour
        payload = (
            b["body"] if b["body"] is not None else {"choices": [{"message": {"content": "{}"}}]}
        )
        self.send_response(b["status"])
        for k, v in b["headers"].items():
            self.send_header(k, v)
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *a):
        pass


@pytest.fixture()
def server():
    _Handler.seen = []
    _Handler.behaviour = {"status": 200, "body": None, "headers": {}}
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_port}", _Handler
    httpd.shutdown()
    httpd.server_close()


def client_for(url, **cfg_over):
    return FoundryAgentClient(fcfg(**cfg_over), "my-deployment", tools=tool_schemas(), env=env(url))


# ---- request shape --------------------------------------------------------------------------
def test_request_shape_carries_tools_and_tool_choice_auto(server):
    url, h = server
    client_for(url).next_turn(MESSAGES)
    body = h.seen[0]["body"]
    assert body["model"] == "my-deployment" and body["messages"] == MESSAGES
    assert body["tool_choice"] == "auto"
    assert {t["function"]["name"] for t in body["tools"]} == {
        "classify_document",
        "lookup_taxonomy_definition",
        "request_human_review",
    }
    assert body["max_completion_tokens"] == 2000
    assert h.seen[0]["headers"]["api-key"] == "sekrit-key-123"


# ---- response parsing -------------------------------------------------------------------------
def test_a_tool_calling_turn_is_parsed_into_parsed_tool_calls(server):
    url, h = server
    h.behaviour = {
        "status": 200, "headers": {},
        "body": {"choices": [{"message": {"tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "classify_document", "arguments": '{"content": "hello"}'}},
        ]}}]},
    }  # fmt: skip
    turn = client_for(url).next_turn(MESSAGES)
    assert turn.final_text is None
    assert len(turn.tool_calls) == 1
    call = turn.tool_calls[0]
    assert call.id == "call_1" and call.name == "classify_document"
    assert call.arguments == {"content": "hello"}


def test_a_tool_call_with_no_arguments_parses_to_an_empty_dict(server):
    url, h = server
    h.behaviour = {
        "status": 200, "headers": {},
        "body": {"choices": [{"message": {"tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "request_human_review"}},
        ]}}]},
    }  # fmt: skip
    turn = client_for(url).next_turn(MESSAGES)
    assert turn.tool_calls[0].arguments == {}


def test_a_final_text_turn_has_no_tool_calls(server):
    url, h = server
    h.behaviour = {
        "status": 200, "headers": {},
        "body": {"choices": [{"message": {"content": '{"priority": "low", "rationale": "fine"}'}}]},
    }  # fmt: skip
    turn = client_for(url).next_turn(MESSAGES)
    assert turn.tool_calls == [] and turn.final_text == '{"priority": "low", "rationale": "fine"}'


def test_an_assistant_message_with_neither_content_nor_tool_calls_is_an_empty_final_text(server):
    url, h = server
    h.behaviour = {"status": 200, "headers": {}, "body": {"choices": [{"message": {}}]}}
    turn = client_for(url).next_turn(MESSAGES)
    assert turn.tool_calls == [] and turn.final_text == ""


@pytest.mark.parametrize(
    "body",
    [
        {"nope": 1},
        {"choices": []},
        {"choices": [{"message": {"tool_calls": [{"id": "x"}]}}]},  # missing "function"
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "x",
                                "function": {"name": "classify_document", "arguments": "not json"},
                            }
                        ]
                    }
                }
            ]
        },  # fmt: skip
    ],
)
def test_unexpected_or_malformed_response_shapes_raise_transport_errors(server, body):
    url, h = server
    h.behaviour = {"status": 200, "headers": {}, "body": body}
    with pytest.raises(AgentError) as exc:
        client_for(url).next_turn(MESSAGES)
    assert exc.value.kind == "transport"


# ---- HTTP / transport failure mapping --------------------------------------------------------
def test_an_http_error_status_is_reported_with_the_status_code(server):
    url, h = server
    h.behaviour = {"status": 500, "headers": {}, "body": {"error": "boom"}}
    with pytest.raises(AgentError) as exc:
        client_for(url).next_turn(MESSAGES)
    assert exc.value.kind == "http_error" and "500" in str(exc.value)


def test_a_connection_failure_is_a_transport_or_timeout_error():
    with pytest.raises(AgentError) as exc:
        client_for("http://127.0.0.1:1").next_turn(MESSAGES)
    assert exc.value.kind in ("transport", "timeout")


# ---- configuration and safety guards ----------------------------------------------------------
def test_a_missing_endpoint_env_var_fails_clearly(server):
    url, _ = server
    e = {"KEY": "sekrit-key-123"}  # EP missing
    with pytest.raises(AgentError) as exc:
        FoundryAgentClient(fcfg(), "d", tools=tool_schemas(), env=e).next_turn(MESSAGES)
    assert exc.value.kind == "not_configured" and "EP" in str(exc.value)


def test_a_missing_api_key_env_var_fails_clearly(server):
    url, _ = server
    e = {"EP": url}  # KEY missing
    with pytest.raises(AgentError) as exc:
        FoundryAgentClient(fcfg(), "d", tools=tool_schemas(), env=e).next_turn(MESSAGES)
    assert exc.value.kind == "not_configured" and "KEY" in str(exc.value)


def test_entra_auth_is_not_supported_by_the_agent_client(server):
    url, _ = server
    cfg = fcfg(auth="entra", entra_scope="scope-under-test")
    with pytest.raises(AgentError) as exc:
        FoundryAgentClient(cfg, "d", tools=tool_schemas(), env=env(url)).next_turn(MESSAGES)
    assert exc.value.kind == "not_configured" and "api_key" in str(exc.value)


def test_credentials_are_refused_over_plain_http_to_a_remote_host():
    called = []
    client = FoundryAgentClient(
        fcfg(), "d", tools=tool_schemas(), env=env("http://example.com"),
        opener=lambda *a, **k: called.append(1),
    )  # fmt: skip
    with pytest.raises(AgentError) as exc:
        client.next_turn(MESSAGES)
    assert exc.value.kind == "not_configured" and not called


def test_a_project_endpoint_is_reduced_to_the_resource_host(server):
    url, h = server
    client_for(url + "/api/projects/my-project").next_turn(MESSAGES)
    assert h.seen[0]["path"] == "/openai/v1/chat/completions"


# ---- build_agent_client -----------------------------------------------------------------------
def test_build_agent_client_rejects_the_large_planner_tier():
    with pytest.raises(AgentError) as exc:
        build_agent_client(fcfg(), "large", "d", tool_schemas())
    assert exc.value.kind == "not_configured"


@pytest.mark.parametrize("tier", ["small", "mid"])
def test_build_agent_client_accepts_small_and_mid_tiers(tier):
    client = build_agent_client(fcfg(), tier, "d", tool_schemas())
    assert isinstance(client, FoundryAgentClient) and client.deployment == "d"
