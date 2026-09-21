"""The MCP server layer, exercised end to end through the real SDK with an in-process client."""

from __future__ import annotations

import anyio
import pytest

pytest.importorskip("mcp")

from mcp import Client  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402

from app.classification.service import ClassificationService  # noqa: E402
from mcp_adapter import ClassifyDocumentAdapter, load_mcp_config  # noqa: E402
from mcp_adapter.server import AUTH_ERROR_CODE, build_server, main  # noqa: E402

RECORD = "Employee record\nNational ID: 905-37-6209\nDate of birth: 01/27/1960\n"


@pytest.fixture(scope="module")
def adapter():
    cfg, _ = load_mcp_config()
    return ClassifyDocumentAdapter(
        ClassificationService(llm_mode="off"), cfg, caller_id="example-agent"
    )


def run(adapter, fn):
    async def go():
        async with Client(build_server(adapter)) as client:
            return await fn(client)

    return anyio.run(go)


def test_the_server_lists_exactly_one_read_only_tool_with_the_frozen_output_schema(adapter):
    tools = run(adapter, lambda c: c.list_tools()).tools
    assert [t.name for t in tools] == ["classify_document"]
    t = tools[0]
    assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False
    props = t.input_schema["properties"]
    assert set(props) == {"request_id", "document", "options"}  # no metadata / labels / caller
    assert t.output_schema is not None and t.output_schema["title"] == "ClassificationResult"


def test_a_call_returns_the_classification_result_as_structured_content(adapter):
    res = run(
        adapter,
        lambda c: c.call_tool(
            "classify_document",
            {
                "document": {"content": RECORD, "filename": "employee_record.txt"},
                "options": {"mode": "rules"},
            },
        ),
    )
    assert res.is_error is False
    body = res.structured_content
    assert body["status"] in ("ok", "degraded", "review_required")
    assert body["high_risk"]["value"] is True and body["schema_version"] == "1.0"
    assert "905-37-6209" not in res.content[0].text or body["status"] != "rejected"


def test_invalid_input_is_a_rejected_result_not_a_protocol_error(adapter):
    res = run(
        adapter,
        lambda c: c.call_tool(
            "classify_document", {"document": {"content": RECORD}, "metadata": {"a": "b"}}
        ),
    )
    assert res.is_error is False
    assert res.structured_content["status"] == "rejected"


def test_an_unknown_tool_is_a_protocol_error(adapter):
    async def call(client):
        try:
            await client.call_tool("delete_everything", {})
        except MCPError as exc:
            return exc.code
        return None

    assert run(adapter, call) == -32602


def test_the_launcher_refuses_an_unlisted_or_missing_identity(monkeypatch, capsys):
    monkeypatch.delenv("DATAGUARD_MCP_CALLER_ID", raising=False)
    assert main(["--llm-mode", "off"]) == 2
    monkeypatch.setenv("DATAGUARD_MCP_CALLER_ID", "stranger")
    assert main(["--llm-mode", "off"]) == 2
    err = capsys.readouterr().err
    assert "not allowed" in err and "stranger" not in err.split("must name")[-1]


def test_the_auth_error_code_is_a_json_rpc_server_error_code():
    assert -32099 <= AUTH_ERROR_CODE <= -32000
