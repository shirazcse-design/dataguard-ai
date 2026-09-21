"""The MCP adapter core (no SDK): deny-by-default, input subset, caps, size limit, unchanged result."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.classification.config_loader import ConfigError
from app.classification.schemas import ClassificationRequest
from app.classification.service import ClassificationService
from mcp_adapter import ClassifyDocumentAdapter, McpAuthorizationError, load_mcp_config
from mcp_adapter.config import McpConfig
from observability import audit_spans, read_jsonl

RECORD = "Employee record\nNational ID: 905-37-6209\nDate of birth: 01/27/1960\n"
ROOT = Path(__file__).resolve().parents[2]


def cfg(**over) -> McpConfig:
    base = {
        "mcp_version": "1.0.0",
        "tool_name": "classify_document",
        "max_content_bytes": 100_000,
        "default_include_evidence": False,
        "callers": {
            "agent-a": {
                "max_llm_tier": "mid",
                "max_cost_usd": 0.05,
                "max_latency_ms": 10000,
                "allow_evidence": True,
            },
            "agent-noev": {"max_llm_tier": "none", "allow_evidence": False},
        },
    }
    base.update(over)
    return McpConfig.model_validate(base)


class SpyService:
    """Records the request the adapter builds; stands in for the service."""

    max_document_bytes = 5_000_000

    def __init__(self):
        self.seen: list[ClassificationRequest] = []
        self.real = ClassificationService(llm_mode="off")

    def classify(self, req):
        self.seen.append(req)
        return self.real.classify(req)

    def reject(self, rid, reason):
        return self.real.reject(rid, reason)


@pytest.fixture(scope="module")
def svc():
    return ClassificationService(llm_mode="off")


def args(content=RECORD, **over):
    body = {"document": {"content": content, "filename": "employee_record.txt"}}
    body.update(over)
    return body


# ---- authorization ------------------------------------------------------------------------
def test_an_unlisted_caller_is_refused_before_any_classification(svc):
    with pytest.raises(McpAuthorizationError):
        ClassifyDocumentAdapter(svc, cfg(), caller_id="stranger")


def test_an_empty_allowlist_denies_everyone(svc):
    with pytest.raises(McpAuthorizationError):
        ClassifyDocumentAdapter(svc, cfg(callers={}), caller_id="agent-a")


def test_the_shipped_config_loads_and_lists_the_placeholder_agent():
    c, _ = load_mcp_config()
    assert c.tool_name == "classify_document" and "example-agent" in c.callers


def test_the_content_limit_may_not_exceed_the_service_limit(svc):
    with pytest.raises(ConfigError):
        ClassifyDocumentAdapter(
            svc, cfg(max_content_bytes=svc.max_document_bytes + 1), caller_id="agent-a"
        )


# ---- the result is the service result, unchanged --------------------------------------------
def test_the_tool_result_equals_the_service_result_for_the_same_request(svc):
    ad = ClassifyDocumentAdapter(svc, cfg(), caller_id="agent-a")
    got = ad.classify_document(args(request_id="r-1", options={"mode": "rules"}))
    want = svc.classify(
        {
            "request_id": "r-1",
            "document": {"content": RECORD, "filename": "employee_record.txt", "extension": "txt"},
            "options": {
                "mode": "rules",
                "max_llm_tier": "mid",
                "budget": {"max_cost_usd": 0.05, "max_latency_ms": 10000},
                "include_evidence": False,
            },
            "caller": {"caller_id": "agent-a", "purpose": "mcp:classify_document"},
        }
    ).model_dump(mode="json")
    got.pop("telemetry"), want.pop("telemetry")
    assert got == want
    assert got["status"] in ("ok", "degraded", "review_required")


def test_the_output_validates_against_the_frozen_result_schema(svc):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "docs/uc4/schema/classification-result.v1.json").read_text())
    ad = ClassifyDocumentAdapter(svc, cfg(), caller_id="agent-a")
    for a in (args(request_id="ok-1"), args(content=""), {"document": {}}):
        jsonschema.validate(ad.classify_document(a), schema)


# ---- input subset ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "extra",
    [
        {"metadata": {"source_system": "Public developer portal"}},
        {"existing_labels": [{"scheme": "x", "value": "PUBLIC"}]},
        {"caller": {"caller_id": "someone-else"}},
        {"schema_version": "1.0"},
    ],
)
def test_spoofable_fields_are_rejected_not_ignored(svc, extra):
    ad = ClassifyDocumentAdapter(svc, cfg(), caller_id="agent-a")
    out = ad.classify_document({**args(), **extra})
    assert out["status"] == "rejected" and out["level"] is None
    assert out["routing"]["stop_reason"].startswith("input_rejected:tool_input_invalid:")


def test_a_document_id_is_rejected_because_resolution_does_not_exist(svc):
    ad = ClassifyDocumentAdapter(svc, cfg(), caller_id="agent-a")
    a = args()
    a["document"]["document_id"] = "doc-1"
    out = ad.classify_document(a)
    assert out["status"] == "rejected"
    assert "document.document_id" in out["routing"]["stop_reason"]


def test_a_rejection_never_echoes_the_offending_value_or_the_text(svc):
    ad = ClassifyDocumentAdapter(svc, cfg(), caller_id="agent-a")
    secret = "SECRET-VALUE-4242"
    out = ad.classify_document({**args(content=secret * 3), "metadata": {"k": secret}})
    assert secret not in json.dumps(out)


def test_a_hostile_extra_key_name_is_sanitised_and_truncated(svc):
    ad = ClassifyDocumentAdapter(svc, cfg(), caller_id="agent-a")
    out = ad.classify_document({**args(), "x" * 500 + "\n<b>": 1})
    reason = out["routing"]["stop_reason"]
    assert len(reason) < 120 and "\n" not in reason and "<" not in reason


def test_a_hostile_request_id_is_replaced(svc):
    ad = ClassifyDocumentAdapter(svc, cfg(), caller_id="agent-a")
    out = ad.classify_document(args(request_id="bad id\n" * 40))
    assert out["request_id"].startswith("mcp-")


def test_non_dict_arguments_become_a_rejected_result(svc):
    ad = ClassifyDocumentAdapter(svc, cfg(), caller_id="agent-a")
    assert ad.classify_document(["not", "a", "dict"])["status"] == "rejected"  # type: ignore[arg-type]


# ---- size limit -------------------------------------------------------------------------------
def test_oversize_content_is_rejected_without_classification(svc):
    ad = ClassifyDocumentAdapter(svc, cfg(max_content_bytes=1000), caller_id="agent-a")
    out = ad.classify_document(args(content="a" * 1001))
    assert out["status"] == "rejected"
    assert out["routing"]["stop_reason"] == "input_rejected:content_too_large"
    assert "aaaa" not in json.dumps(out)


def test_the_size_limit_counts_bytes_not_characters(svc):
    ad = ClassifyDocumentAdapter(svc, cfg(max_content_bytes=1000), caller_id="agent-a")
    out = ad.classify_document(args(content="é" * 600))  # 600 chars, 1200 bytes
    assert out["routing"]["stop_reason"] == "input_rejected:content_too_large"


def test_undecodable_text_is_rejected(svc):
    ad = ClassifyDocumentAdapter(svc, cfg(), caller_id="agent-a")
    out = ad.classify_document(args(content="ok \ud800 lone surrogate"))
    assert out["status"] == "rejected"


# ---- per-caller caps --------------------------------------------------------------------------
def test_a_request_can_lower_but_never_raise_the_caller_caps():
    spy = SpyService()
    ad = ClassifyDocumentAdapter(spy, cfg(), caller_id="agent-a")
    ad.classify_document(
        args(
            options={
                "mode": "rules",
                "max_llm_tier": "large",
                "max_cost_usd": 5.0,
                "max_latency_ms": 60000,
            }
        )
    )
    o = spy.seen[-1].options
    assert (o.max_llm_tier, o.budget.max_cost_usd, o.budget.max_latency_ms) == ("mid", 0.05, 10000)
    ad.classify_document(
        args(
            options={
                "mode": "rules",
                "max_llm_tier": "small",
                "max_cost_usd": 0.01,
                "max_latency_ms": 500,
            }
        )
    )
    o = spy.seen[-1].options
    assert (o.max_llm_tier, o.budget.max_cost_usd, o.budget.max_latency_ms) == ("small", 0.01, 500)


def test_defaults_come_from_the_caller_policy():
    spy = SpyService()
    ClassifyDocumentAdapter(spy, cfg(), caller_id="agent-a").classify_document(
        args(options={"mode": "rules"})
    )
    o = spy.seen[-1].options
    assert o.max_llm_tier == "mid" and o.budget.max_latency_ms == 10000


def test_the_caller_is_the_connection_identity_and_the_purpose_is_fixed():
    spy = SpyService()
    ClassifyDocumentAdapter(spy, cfg(), caller_id="agent-a").classify_document(
        args(options={"mode": "rules"})
    )
    c = spy.seen[-1].caller
    assert (c.caller_id, c.purpose) == ("agent-a", "mcp:classify_document")


def test_evidence_is_off_by_default_and_only_on_when_requested_and_permitted():
    spy = SpyService()
    a = ClassifyDocumentAdapter(spy, cfg(), caller_id="agent-a")
    a.classify_document(args(options={"mode": "rules"}))
    assert spy.seen[-1].options.include_evidence is False
    a.classify_document(args(options={"mode": "rules", "include_evidence": True}))
    assert spy.seen[-1].options.include_evidence is True
    b = ClassifyDocumentAdapter(spy, cfg(), caller_id="agent-noev")
    b.classify_document(args(options={"mode": "rules", "include_evidence": True}))
    assert spy.seen[-1].options.include_evidence is False  # policy forbids it


def test_a_high_risk_document_is_flagged_high_risk_through_the_tool(svc):
    out = ClassifyDocumentAdapter(svc, cfg(), caller_id="agent-a").classify_document(
        args(options={"mode": "rules"})
    )
    assert out["high_risk"]["value"] is True


# ---- tracing ------------------------------------------------------------------------------------
def test_tool_calls_are_traced_with_a_pseudonymised_caller_and_no_document_text(tmp_path):
    trace = tmp_path / "spans.jsonl"
    traced = ClassificationService(llm_mode="off", trace_path=trace)
    ad = ClassifyDocumentAdapter(traced, cfg(), caller_id="agent-a")
    ad.classify_document(args(options={"mode": "rules"}))
    ad.classify_document(args(content="a" * 200_000))  # rejected: too large
    text = trace.read_text()
    roots = [s for s in read_jsonl(trace) if s.name == "classify"]
    assert len(roots) == 2
    assert "agent-a" not in text and "905-37-6209" not in text
    assert all(s.attributes.get("dg.caller") for s in roots if "dg.caller" in s.attributes)

    class Doc:
        content, filename, doc_id, gold_evidence_spans = RECORD, "employee_record.txt", "x", []

    assert audit_spans(text, [Doc()]).clean
