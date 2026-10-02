"""UC1 telemetry through the REAL redactor and allow-list, across all 30 golden cases (which include
every simulated fault): the dg.dlp.* fields survive, nothing identifying or textual leaks, and uc1
spans map onto the OpenTelemetry GenAI conventions the same way UC4/UC6 spans do."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agent.mock import MockAgentClient
from app.agent.types import AgentError, AgentTurn, ParsedToolCall
from app.dlp.agent import DlpAgent
from app.dlp.integration import DocumentStore
from app.dlp.service import build_investigator
from evals.dlp.golden import load_golden
from observability.audit import audit_spans
from observability.config import build_tracer, load_observability_config
from observability.sinks import MemorySink, _genai_attrs

CASES = load_golden()[0]


@pytest.fixture(scope="module")
def traced():
    cfg, _ = load_observability_config()
    sink = MemorySink()
    tracer, _ = build_tracer(cfg, [sink], deterministic_ids=True)
    inv = build_investigator("offline", tracer=tracer)
    results = {c.id: inv.investigate(c.event) for c in CASES}
    return sink.spans, results


def _blob(spans):
    return json.dumps([s.model_dump() for s in spans], default=str)


def test_root_spans_carry_the_dlp_decision_fields(traced):
    spans, results = traced
    roots = [s for s in spans if s.name == "uc1.investigation"]
    assert len(roots) == len(CASES)
    for r in roots:
        a = r.attributes
        assert a["dg.dlp.outcome"] == results[a["dg.dlp.case_id"]].decision.outcome
        assert {"dg.dlp.band", "dg.dlp.score", "dg.dlp.reason_codes"} <= set(a)
    faulted = [r for r in roots if r.attributes.get("dg.dlp.simulated_faults")]
    assert len(faulted) == sum(1 for c in CASES if c.event.simulate_fault)


def test_every_exported_key_is_allow_listed(traced):
    cfg, _ = load_observability_config()
    allowed = set(cfg.allowed_attributes)
    for s in traced[0]:
        assert set(s.attributes) <= allowed, (s.name, set(s.attributes) - allowed)


def test_no_document_text_filename_identity_host_or_justification_leaks(traced):
    spans, _ = traced
    store = DocumentStore()
    docs = []
    for c in CASES:
        d = store.get(c.event.document_ref)
        docs.append(SimpleNamespace(content=d.content, filename=d.filename,
                                    doc_id=c.id, gold_evidence_spans=[]))  # fmt: skip
    res = audit_spans(_blob(spans), docs)
    assert res.leaks == [], res.leaks
    blob = _blob(spans)
    for c in CASES:
        e = c.event
        assert e.user_id not in blob and e.destination.host not in blob
        if e.user_justification:
            assert e.user_justification[:40] not in blob


def test_failure_injection_reports_codes_only(traced):
    spans, results = traced
    blob = _blob(spans)
    for fault in ("semantic_tier_unavailable", "policy_unavailable", "identity_unavailable",
                  "activity_tool_error", "malformed_activity_result"):  # fmt: skip
        assert fault in blob
    assert results["D30"].decision.outcome == "HUMAN_REVIEW"
    assert "review:stage_failure" in results["D30"].decision.reason_codes


def test_a_failing_planner_exports_an_error_kind_not_a_message():
    cfg, _ = load_observability_config()
    sink = MemorySink()
    tracer, _ = build_tracer(cfg, [sink], deterministic_ids=True)
    inv = build_investigator("offline", tracer=tracer)
    boom = AgentError("http_error", "upstream said: Kestrel Sensor Systems u-1003 dropbox.com")
    inv.agent = DlpAgent(MockAgentClient([boom]), inv.dlp.agent, "s", backend="test")
    flagship = next(c for c in CASES if c.id == "D11")
    out = inv.investigate(flagship.event)
    assert out.decision.outcome in ("HUMAN_REVIEW", "ESCALATE")
    blob = _blob(sink.spans)
    assert "Kestrel" not in blob and "upstream said" not in blob and "u-1003" not in blob


def test_uc1_spans_map_to_genai_conventions():
    assert _genai_attrs("uc1.agent", {"dg.agent.name": "dataguard-dlp-investigator"}) == {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "dataguard-dlp-investigator",
    }
    tool = _genai_attrs("uc1.tool", {"dg.agent.tool": "check_dlp_exception"})
    assert tool["gen_ai.operation.name"] == "execute_tool"
    assert tool["gen_ai.tool.name"] == "check_dlp_exception"
    live = {"dg.llm.model_id": "uc4-llm-medium", "dg.llm.cached": False, "dg.tokens_in": 10}
    assert _genai_attrs("uc1.agent.planner", live)["gen_ai.operation.name"] == "chat"
    assert _genai_attrs("uc1.agent.planner", {**live, "dg.llm.cached": True}) == {}
    assert _genai_attrs("uc1.agent.planner", {}) == {}  # the offline planner is not a model call


def test_tool_call_spans_never_carry_arguments():
    cfg, _ = load_observability_config()
    sink = MemorySink()
    tracer, _ = build_tracer(cfg, [sink], deterministic_ids=True)
    inv = build_investigator("offline", tracer=tracer)
    turns = [
        AgentTurn(
            tool_calls=[
                ParsedToolCall("c1", "search_policy", {"query": "acquisition shortlist Kestrel"})
            ]
        ),  # fmt: skip
        AgentTurn(
            final_text=json.dumps(
                {"proposed_outcome": "ESCALATE", "findings": [], "missing_evidence": []}
            )
        ),  # fmt: skip
    ]
    inv.agent = DlpAgent(MockAgentClient(turns), inv.dlp.agent, "s", backend="test")
    inv.investigate(next(c for c in CASES if c.id == "D11").event)
    tools = [s for s in sink.spans if s.name == "uc1.tool"]
    assert tools and all(s.attributes["dg.agent.tool"] == "search_policy" for s in tools)
    assert "Kestrel" not in _blob(sink.spans)


def test_obs_report_privacy_audit_is_clean_and_counts_every_case():
    from evals.dlp.observability_report import privacy_audit, run_traced, summarise

    spans, inv = run_traced("offline", CASES)
    audit = privacy_audit(spans, CASES, inv)
    assert audit["clean"], audit["leaks"]
    assert audit["identifier_checks"] >= 2 * len(CASES)
    t = summarise(spans)
    assert t["investigations"] == len(CASES)
    assert sum(t["outcomes"].values()) == len(CASES)


def test_azure_monitor_sink_exports_every_span_by_default(monkeypatch):
    """The exporter's default rate-limited sampler dropped spans mid-trace in the live check."""
    pytest.importorskip("azure.monitor.opentelemetry")
    import azure.monitor.opentelemetry as amo

    from observability.sinks import azure_monitor_sink

    seen = {}
    monkeypatch.delenv("OTEL_TRACES_SAMPLER", raising=False)
    monkeypatch.setattr(amo, "configure_azure_monitor",
                        lambda **kw: seen.update(sampler=__import__("os").environ.get("OTEL_TRACES_SAMPLER")))  # fmt: skip
    azure_monitor_sink("InstrumentationKey=00000000-0000-0000-0000-000000000000")
    assert seen["sampler"] == "always_on"
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "traceidratio")  # an explicit operator choice wins
    azure_monitor_sink("InstrumentationKey=00000000-0000-0000-0000-000000000000")
    assert seen["sampler"] == "traceidratio"
