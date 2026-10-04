"""UC2 tools on the existing MCP server: opt-in, read-only, typed, allow-listed, windowed; multiple
registries coexist with UC1's; duplicate names refused; classify_document unchanged."""

from __future__ import annotations

import anyio
import pytest

pytest.importorskip("mcp")

from mcp import Client  # noqa: E402

from app.classification.service import ClassificationService  # noqa: E402
from app.dlp.mcp_tools import DlpMcpTools, ToolInputError, ToolRefused  # noqa: E402
from app.insider.mcp_tools import InsiderMcpTools  # noqa: E402
from app.insider.service import build_services  # noqa: E402
from mcp_adapter import ClassifyDocumentAdapter, load_mcp_config  # noqa: E402
from mcp_adapter.server import build_server  # noqa: E402

CALLER = "insider-risk-agent"


@pytest.fixture(scope="module")
def svc():
    return build_services("offline")


@pytest.fixture(scope="module")
def adapter():
    cfg, _ = load_mcp_config()
    return ClassifyDocumentAdapter(ClassificationService(llm_mode="off"), cfg, caller_id=CALLER)


def listed(adapter, extra):
    async def go():
        async with Client(build_server(adapter, extra_tools=extra)) as c:
            return [t.name for t in (await c.list_tools()).tools]

    return anyio.run(go)


def test_uc4_contract_unchanged_without_flags_and_registries_combine(adapter, svc):
    assert listed(adapter, None) == ["classify_document"]
    both = listed(adapter, [DlpMcpTools(CALLER, copilot_factory=lambda: svc.copilot, allow_list={}),
                            InsiderMcpTools(CALLER, services=svc)])  # fmt: skip
    assert (
        both[0] == "classify_document"
        and "search_security_logs" in both
        and "search_policy" in both
    )


def test_duplicate_tool_names_are_refused(adapter, svc):
    with pytest.raises(ValueError, match="duplicate"):
        build_server(
            adapter,
            extra_tools=[
                InsiderMcpTools(CALLER, services=svc),
                InsiderMcpTools(CALLER, services=svc),
            ],
        )


def test_tools_are_typed_windowed_and_allow_listed(svc):
    t = InsiderMcpTools(CALLER, services=svc)
    out = t.call("get_behavior_profile", {"user_id": "u-2043", "date": "2026-08-25"})
    assert out["anomaly_band"] == "HIGH_ANOMALY" and "not evidence of intent" in out["note"]
    with pytest.raises(ToolInputError):
        t.call(
            "search_security_logs",
            {"user_id": "u-2043", "start_date": "2026-06-01", "end_date": "2026-08-25"},
        )
    with pytest.raises(ToolInputError):
        t.call("get_permissions", {"user_id": "u-1003"})  # UC1 ids are not UC2 subjects
    with pytest.raises(ToolRefused):
        InsiderMcpTools("example-agent", services=svc).call(
            "get_permissions", {"user_id": "u-2043"}
        )
    assert all(x.name.startswith(("get_", "search_")) for x in t.tools)
