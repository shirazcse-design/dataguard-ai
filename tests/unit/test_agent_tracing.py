"""Batch Triage Agent tracing: one trace per document (`agent.document` > `agent.planner` /
`agent.tool` > the classifier's own `classify` tree), fixed-vocabulary attributes only, and the
OpenTelemetry GenAI translation Foundry's Tracing view renders.

Every test uses a scripted or offline planner and the REAL `ClassificationService` in
`llm_mode="off"` with the REAL observability allow-list - no test contacts Foundry or Azure Monitor.
"""

from __future__ import annotations

import json

import pytest

import observability
from app.agent.batch import run_batch, trace_review_reason
from app.agent.config import AgentConfig
from app.agent.loop import tool_label
from app.agent.mock import MockAgentClient
from app.agent.offline_policy import offline_policy
from app.agent.schemas import DocumentAnnotation
from app.agent.tools import ToolRegistry
from app.agent.types import AgentError, AgentTurn, ParsedToolCall
from app.classification.cli import main
from app.classification.config_loader import load_config
from app.classification.service import ClassificationService
from observability import MemorySink, OtelSink, audit_spans
from tests.helpers import mkdoc

CFG = AgentConfig(
    agent_config_version="1.0.0",
    planner_tier="mid",
    max_steps_per_document=6,
    allowed_tools=["classify_document", "lookup_taxonomy_definition", "request_human_review"],
    timeout_s=30.0,
)
PII = "Employee record\nNational ID: 905-37-6209\n"


def traced(tmp_path, *extra):
    sink = MemorySink()
    svc = ClassificationService(
        llm_mode="off", trace_path=tmp_path / "spans.jsonl", extra_sinks=[sink, *extra]
    )
    return svc, ToolRegistry(svc, load_config()), sink


def by_name(spans, name):
    return [s for s in spans if s.name == name]


def final(priority="low", rationale="ok", **meta):
    return AgentTurn(final_text=json.dumps({"priority": priority, "rationale": rationale}), **meta)


def classify(doc, call_id="1"):
    return ParsedToolCall(
        id=call_id, name="classify_document", arguments={"content": doc.content, "filename": doc.filename}
    )  # fmt: skip


# ---- trace shape ------------------------------------------------------------------------------
def test_each_document_is_one_trace_with_the_classifier_nested_under_its_tool_span(tmp_path):
    svc, reg, sink = traced(tmp_path)
    docs = [mkdoc("d1", content=PII), mkdoc("d2")]
    run_batch(docs, MockAgentClient(offline_policy), reg, CFG, tracer=svc.tracer)

    roots = by_name(sink.spans, "agent.document")
    assert len(roots) == 2 and len({r.trace_id for r in roots}) == 2
    for root in roots:
        same = [s for s in sink.spans if s.trace_id == root.trace_id]
        assert root.parent_span_id is None
        assert root.attributes["dg.agent.name"] == "dataguard-batch-triage"
        assert root.attributes["dg.agent.planner"] == "mock"
        assert root.attributes["dg.agent.stopped_reason"] == "completed"
        assert by_name(same, "agent.planner")
        tool = next(
            s for s in by_name(same, "agent.tool") if s.attributes["dg.agent.tool"] == "classify_document"
        )  # fmt: skip
        assert tool.attributes["dg.agent.tool_ok"] is True
        classify_span = by_name(same, "classify")[0]
        assert classify_span.parent_span_id == tool.span_id  # joined the agent's trace
    assert {r.attributes["dg.document_id"] for r in roots} == {"d1", "d2"}


def test_without_a_tracer_no_spans_are_recorded_and_the_report_is_unchanged(tmp_path):
    svc, reg, sink = traced(tmp_path)
    docs = [mkdoc("d1", content=PII)]
    plain = run_batch(docs, MockAgentClient(offline_policy), reg, CFG)
    assert not by_name(sink.spans, "agent.document")
    with_trace = run_batch(docs, MockAgentClient(offline_policy), reg, CFG, tracer=svc.tracer)
    strip = lambda r: [d.model_dump(exclude={"request_id"}) for d in r.documents]  # noqa: E731
    assert strip(plain) == strip(with_trace)  # tracing never changes a decision


def test_a_failing_planner_turn_is_an_error_span_with_the_error_kind(tmp_path):
    svc, reg, sink = traced(tmp_path)
    client = MockAgentClient([AgentError("timeout", "slow")])
    run_batch([mkdoc("d1")], client, reg, CFG, tracer=svc.tracer)
    planner = by_name(sink.spans, "agent.planner")[0]
    assert planner.status == "error"
    assert planner.attributes["dg.llm.error_kind"] == "timeout"
    root = by_name(sink.spans, "agent.document")[0]
    assert root.attributes["dg.agent.review_reason"] == "planner_call_failed"


def test_planner_model_and_token_metadata_is_recorded_when_the_client_reports_it(tmp_path):
    svc, reg, sink = traced(tmp_path)
    doc = mkdoc("d1")
    meta = dict(model_id="uc4-llm-medium", served_model="gpt-x", tokens_in=900, tokens_out=40)
    client = MockAgentClient([AgentTurn(tool_calls=[classify(doc)], **meta), final(**meta)])
    run_batch([doc], client, reg, CFG, tracer=svc.tracer)
    planners = by_name(sink.spans, "agent.planner")
    assert [p.attributes["dg.agent.turn"] for p in planners] == ["tool_calls", "final"]
    assert all(p.attributes["dg.llm.model_id"] == "uc4-llm-medium" for p in planners)
    assert planners[0].attributes["dg.tokens_in"] == 900


# ---- privacy: only fixed-vocabulary values ever leave the process -----------------------------
def test_tool_arguments_rationale_and_free_text_review_reasons_never_reach_a_span(tmp_path):
    svc, reg, sink = traced(tmp_path)
    doc = mkdoc("d1", content=PII)
    client = MockAgentClient(
        [
            AgentTurn(
                tool_calls=[
                    classify(doc),
                    ParsedToolCall(id="2", name="exfiltrate_905-37-6209", arguments={}),
                    ParsedToolCall(
                        id="3",
                        name="request_human_review",
                        arguments={"reason": "SECRET-REASON national id 905-37-6209"},
                    ),
                ]
            ),
            final("high", "SECRET-RATIONALE quoting 905-37-6209"),
        ]
    )
    run_batch([doc], client, reg, CFG, tracer=svc.tracer)
    blob = json.dumps([s.model_dump() for s in sink.spans])
    for leaked in ("905-37-6209", "SECRET-REASON", "SECRET-RATIONALE", "exfiltrate", "National ID"):
        assert leaked not in blob
    tools = [s.attributes["dg.agent.tool"] for s in by_name(sink.spans, "agent.tool")]
    assert "unlisted" in tools
    root = by_name(sink.spans, "agent.document")[0]
    assert root.attributes["dg.agent.review_reason"] == "agent_requested"


def test_the_privacy_audit_passes_on_a_traced_agent_run_over_the_real_dev_split(tmp_path):
    """The same gate CI runs on the classifier's own spans, applied to the agent's."""
    from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents

    docs = load_documents(DEFAULT_DATA_DIR, splits=["dev"])
    svc, reg, _sink = traced(tmp_path)
    run_batch(docs, MockAgentClient(offline_policy), reg, CFG, tracer=svc.tracer)
    res = audit_spans((tmp_path / "spans.jsonl").read_text(encoding="utf-8"), docs)
    assert res.clean, res.leaks[:5]


def test_tool_label_only_passes_real_tool_names():
    assert tool_label("classify_document") == "classify_document"
    assert tool_label("rm -rf /") == "unlisted"


@pytest.mark.parametrize(
    ("review_requested", "reason", "expected"),
    [
        (False, None, "none"),
        (True, "step_budget_exceeded", "step_budget_exceeded"),
        (True, "anything the planner wrote", "agent_requested"),
        (True, None, "classifier_flagged"),
    ],
)
def test_trace_review_reason_is_a_fixed_vocabulary(review_requested, reason, expected):
    ann = DocumentAnnotation(
        doc_id="d1", request_id="d1", status="ok", review_requested=review_requested,
        review_reason=reason, priority="low", tool_calls=[], stopped_reason="completed",
    )  # fmt: skip
    assert trace_review_reason(ann) == expected


# ---- GenAI semantic conventions (Foundry's Tracing view) --------------------------------------
def _otel():
    pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, OtelSink(provider)


def test_agent_spans_carry_invoke_agent_execute_tool_and_chat_genai_attributes(tmp_path):
    exporter, otel = _otel()
    svc, reg, _ = traced(tmp_path, otel)
    doc = mkdoc("d1")
    meta = dict(model_id="uc4-llm-medium", tokens_in=900, tokens_out=40)
    client = MockAgentClient([AgentTurn(tool_calls=[classify(doc)], **meta), final(**meta)])
    client.name = "foundry"
    run_batch([doc], client, reg, CFG, tracer=svc.tracer)
    spans = exporter.get_finished_spans()

    root = next(s for s in spans if s.name == "agent.document").attributes
    assert root["gen_ai.operation.name"] == "invoke_agent"
    assert root["gen_ai.agent.name"] == "dataguard-batch-triage"
    assert root["gen_ai.provider.name"] == "azure.ai.openai"
    tool = next(s for s in spans if s.name == "agent.tool").attributes
    assert tool["gen_ai.operation.name"] == "execute_tool"
    assert tool["gen_ai.tool.name"] == "classify_document"
    planner = next(s for s in spans if s.name == "agent.planner").attributes
    assert planner["gen_ai.operation.name"] == "chat"
    assert planner["gen_ai.request.model"] == "uc4-llm-medium"
    assert planner["gen_ai.usage.input_tokens"] == 900


def test_the_offline_planner_is_not_labelled_as_a_model_call(tmp_path):
    exporter, otel = _otel()
    svc, reg, _ = traced(tmp_path, otel)
    run_batch([mkdoc("d1")], MockAgentClient(offline_policy), reg, CFG, tracer=svc.tracer)
    spans = exporter.get_finished_spans()
    planner = next(s for s in spans if s.name == "agent.planner").attributes
    assert "gen_ai.operation.name" not in planner
    root = next(s for s in spans if s.name == "agent.document").attributes
    assert root["gen_ai.operation.name"] == "invoke_agent"
    assert "gen_ai.provider.name" not in root  # nothing was sent to a provider


# ---- CLI --------------------------------------------------------------------------------------
def test_agent_triage_azure_monitor_without_a_connection_string_is_a_usage_error(
    monkeypatch, capsys
):
    monkeypatch.delenv("DATAGUARD_AZURE_MONITOR_CONNECTION_STRING", raising=False)
    assert main(["agent", "triage", "--llm-mode", "off", "--limit", "1", "--azure-monitor"]) == 2
    assert "DATAGUARD_AZURE_MONITOR_CONNECTION_STRING is not set" in capsys.readouterr().err


def test_agent_triage_azure_monitor_sends_agent_traces_through_the_sink(monkeypatch, capsys):
    secret = "InstrumentationKey=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee;super-secret-suffix"
    monkeypatch.setenv("DATAGUARD_AZURE_MONITOR_CONNECTION_STRING", secret)
    fake = MemorySink()
    monkeypatch.setattr(observability, "azure_monitor_sink", lambda c: fake)
    flushed = []
    monkeypatch.setattr(
        observability, "flush_azure_monitor", lambda *a, **k: flushed.append(1) or True
    )
    args = ["agent", "triage", "--llm-mode", "off", "--limit", "2", "--azure-monitor"]
    assert main(args) == 0
    out, err = capsys.readouterr()
    assert secret not in out and secret not in err
    assert len(by_name(fake.spans, "agent.document")) == 2
    assert flushed == [1]


def test_a_rebound_classify_call_is_flagged_on_its_tool_span(tmp_path):
    svc, reg, sink = traced(tmp_path)
    doc = mkdoc("d1", content=PII)
    altered = ParsedToolCall(id="1", name="classify_document", arguments={"content": "retyped"})
    exact = classify(doc, "2")
    for call_, expected in ((altered, True), (exact, False)):
        sink.spans.clear()
        run_batch(
            [doc],
            MockAgentClient([AgentTurn(tool_calls=[call_]), final()]),
            reg,
            CFG,
            tracer=svc.tracer,
        )
        tool = by_name(sink.spans, "agent.tool")[0]
        assert tool.attributes["dg.agent.args_rebound"] is expected


def test_a_replayed_llm_call_is_not_labelled_as_a_model_call_or_counted_as_usage():
    """D9.35: seen live in Foundry - a replayed classifier response rendered as a Chat span with its
    recorded tokens, claiming a call (and usage) that never happened."""
    from observability.sinks import _genai_attrs

    live = {"dg.llm.model_id": "uc4-llm-medium", "dg.tokens_in": 10, "dg.tokens_out": 2}
    assert _genai_attrs("llm.call", live)["gen_ai.operation.name"] == "chat"
    assert _genai_attrs("llm.call", {**live, "dg.llm.cached": True}) == {}
    assert (
        _genai_attrs("llm.call", {**live, "dg.llm.cached": False})["gen_ai.usage.input_tokens"]
        == 10
    )


def test_the_trace_agent_name_is_the_registered_foundry_agent_name():
    from app.agent import foundry_service
    from app.agent.config import AGENT_NAME

    assert foundry_service.AGENT_NAME == AGENT_NAME == "dataguard-batch-triage"
