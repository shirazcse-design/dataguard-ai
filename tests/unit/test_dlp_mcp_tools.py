"""UC1 tools on the existing MCP server: opt-in, read-only, typed, per-tool caller allow-list,
and the UC4 `classify_document` contract unchanged."""

from __future__ import annotations

import anyio
import pytest

pytest.importorskip("mcp")

from mcp import Client  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402

from app.classification.service import ClassificationService  # noqa: E402
from app.dlp.mcp_tools import DlpMcpTools, ToolInputError, ToolRefused  # noqa: E402
from app.policy.service import build_copilot  # noqa: E402
from mcp_adapter import ClassifyDocumentAdapter, load_mcp_config  # noqa: E402
from mcp_adapter.server import AUTH_ERROR_CODE, build_server  # noqa: E402

CALLER = "dlp-investigation-agent"


@pytest.fixture(scope="module")
def copilot():
    return build_copilot("offline")


@pytest.fixture(scope="module")
def adapter():
    cfg, _ = load_mcp_config()
    return ClassifyDocumentAdapter(ClassificationService(llm_mode="off"), cfg, caller_id=CALLER)


def tools_for(caller, copilot):
    return DlpMcpTools(caller, copilot_factory=lambda: copilot)


def run(adapter, extra, fn):
    async def go():
        async with Client(build_server(adapter, extra_tools=extra)) as client:
            return await fn(client)

    return anyio.run(go)


def test_without_the_flag_the_server_is_exactly_uc4(adapter):
    tools = run(adapter, None, lambda c: c.list_tools()).tools
    assert [t.name for t in tools] == ["classify_document"]


def test_with_dlp_tools_the_server_lists_four_read_only_tools_and_no_action_tool(adapter, copilot):
    tools = run(adapter, tools_for(CALLER, copilot), lambda c: c.list_tools()).tools
    names = [t.name for t in tools]
    assert names == ["classify_document", "search_policy", "get_user_profile", "get_user_activity"]
    assert "simulate_block_action" not in names and "calculate_risk_score" not in names
    for t in tools:
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False
    assert tools[0].output_schema["title"] == "ClassificationResult"  # UC4 contract intact


def test_get_user_profile_and_activity_return_synthetic_context(adapter, copilot):
    extra = tools_for(CALLER, copilot)
    prof = run(adapter, extra, lambda c: c.call_tool("get_user_profile", {"user_id": "u-1003"}))
    assert prof.is_error is False
    assert prof.structured_content["profile"]["privilege_level"] == "privileged"
    act = run(
        adapter, extra, lambda c: c.call_tool("get_user_activity", {"user_id": "u-1003", "days": 7})
    )
    assert act.structured_content["status"] == "ok" and act.structured_content["window_days"] == 7
    missing = run(adapter, extra, lambda c: c.call_tool("get_user_profile", {"user_id": "u-9999"}))
    assert missing.structured_content == {"status": "not_found", "error": "unknown_user"}


def test_search_policy_returns_cited_sections(copilot):
    out = tools_for(CALLER, copilot).call(
        "search_policy", {"query": "uploading confidential data to personal cloud storage"}
    )
    assert out["status"] == "ok" and out["results"]
    assert all("citation" in r and "evidence_id" in r for r in out["results"])


def test_inputs_are_typed_and_errors_never_echo_values(copilot):
    t = tools_for(CALLER, copilot)
    for name, args in [
        ("get_user_profile", {"user_id": "'; DROP TABLE users"}),
        ("get_user_activity", {"user_id": "u-1003", "days": 365}),
        ("get_user_profile", {"user_id": "u-1003", "caller": "admin"}),
        ("search_policy", {"query": "x"}),
    ]:
        with pytest.raises(ToolInputError) as exc:
            t.call(name, args)
        assert "DROP" not in str(exc.value) and "admin" not in str(exc.value)


def test_a_caller_not_on_the_tool_allow_list_is_refused(adapter, copilot):
    t = DlpMcpTools(
        "example-agent", copilot_factory=lambda: copilot, allow_list={"get_user_profile": []}
    )
    with pytest.raises(ToolRefused):
        t.call("get_user_profile", {"user_id": "u-1003"})

    async def go(c):
        with pytest.raises(MCPError) as exc:
            await c.call_tool("get_user_profile", {"user_id": "u-1003"})
        return exc.value

    err = run(adapter, t, go)
    assert err.error.code == AUTH_ERROR_CODE


def test_unknown_tool_is_still_rejected(adapter, copilot):
    async def go(c):
        with pytest.raises(MCPError) as exc:
            await c.call_tool("simulate_block_action", {})
        return exc.value

    assert run(adapter, tools_for(CALLER, copilot), go).error.code == -32602


def test_allow_list_cannot_name_a_tool_that_does_not_exist(copilot):
    with pytest.raises(ValueError):
        DlpMcpTools(
            CALLER, copilot_factory=lambda: copilot, allow_list={"simulate_block_action": []}
        )
