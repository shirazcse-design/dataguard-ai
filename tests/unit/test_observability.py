"""Tracer, redaction, sinks, derived metrics, privacy audit and the OpenTelemetry bridge."""

from __future__ import annotations

import json
import threading

import pytest

from app.classification.config_loader import ConfigError
from observability import (
    JsonlSink,
    MemorySink,
    OtelSink,
    Redactor,
    SeededIds,
    Span,
    SpanEvent,
    Tracer,
    audit_spans,
    load_observability_config,
    pseudonymize,
    read_jsonl,
    span,
    summarize,
    tracing_active,
)
from observability.audit import strings_of
from observability.config import build_tracer
from observability.redaction import is_forbidden, salt_from_env
from tests.helpers import mkdoc

ALLOWED = {"dg.request_id", "dg.reason", "dg.stage", "dg.latency_ms", "dg.outcome.categories", "dg.caller",
           "dg.content_hash", "dg.llm.served_model", "dg.guardrail.type", "dg.guardrail.trigger", "dg.escalations", "dg.error.type"}  # fmt: skip


def tracer(sinks=None, allowed=None, clock=None):
    ticks = iter(range(1_000, 10**9, 1_000))
    return Tracer(
        sinks or [MemorySink()],
        Redactor(allowed or ALLOWED, 60),
        ids=SeededIds(),
        clock=clock or (lambda: next(ticks)),
    )


# ---- redaction --------------------------------------------------------------------------------
def test_unknown_keys_are_dropped_and_counted_deny_by_default():
    clean, dropped = Redactor(ALLOWED).scrub({"dg.reason": "ok", "secret": "x", "dg.other": 1})
    assert clean == {"dg.reason": "ok"} and dropped == 2


@pytest.mark.parametrize(
    "key",
    [
        "dg.content",
        "dg.quote",
        "dg.x.excerpt",
        "dg.rationale",
        "dg.filename",
        "dg.prompt",
        "dg.metadata",
        "dg.document",
    ],
)
def test_content_bearing_keys_can_never_be_allowed(key):
    assert is_forbidden(key)
    with pytest.raises(ValueError):
        Redactor({key})


@pytest.mark.parametrize(
    "key",
    [
        "dg.content_hash",
        "dg.content_bytes",
        "dg.document_id",
        "dg.llm.prompt_version",
        "dg.request_id",
    ],
)
def test_ids_hashes_counts_and_versions_are_not_mistaken_for_content(key):
    assert not is_forbidden(key)


def test_identifier_keys_are_validated_by_shape_not_masked():
    r = Redactor(ALLOWED)
    ok, _ = r.scrub(
        {
            "dg.request_id": "req-uc4-019796ef4a",
            "dg.caller": "p_" + "a" * 16,
            "dg.content_hash": "b" * 64,
        }
    )
    assert ok["dg.request_id"] == "req-uc4-019796ef4a" and ok["dg.content_hash"] == "b" * 64
    bad, dropped = r.scrub(
        {
            "dg.request_id": "a sentence with the patient's name",
            "dg.caller": "jane.roe",
            "dg.content_hash": "not-a-hash",
        }
    )
    assert bad == {} and dropped == 3  # an id that is not id-shaped is dropped, never exported


def test_safe_codes_pass_but_secretish_and_free_text_values_are_masked():
    r = Redactor(ALLOWED, 60)
    out, _ = r.scrub({
        "dg.llm.served_model": "gpt-5-mini-2025-08-07", "dg.reason": "llm_review:EVIDENCE_UNVERIFIED",
        "dg.guardrail.type": "prompt_injection_suspected",
    })  # fmt: skip
    assert (
        out["dg.llm.served_model"] == "gpt-5-mini-2025-08-07"
        and out["dg.guardrail.type"] == "prompt_injection_suspected"
    )
    hostile, _ = r.scrub(
        {
            "dg.guardrail.trigger": "SSN 905-37-6209 and jane.roe@corp.example",
            "dg.reason": "sk-abcdefghijklmnopqrstuvwx",
        }
    )
    assert "905-37-6209" not in json.dumps(hostile) and "jane.roe@corp.example" not in json.dumps(
        hostile
    )
    assert "abcdefghijklmnopqrstuvwx" not in hostile["dg.reason"]


def test_values_are_truncated_lists_are_scrubbed_and_objects_dropped():
    r = Redactor(ALLOWED, 30)
    out, dropped = r.scrub(
        {
            "dg.reason": "word " * 50,
            "dg.outcome.categories": ["PHI", "PII"],
            "dg.stage": {"a": 1},
            "dg.latency_ms": 3.5,
        }
    )
    assert (
        len(out["dg.reason"]) <= 30
        and out["dg.outcome.categories"] == ["PHI", "PII"]
        and out["dg.latency_ms"] == 3.5
    )
    assert "dg.stage" not in out and dropped == 1


def test_pseudonyms_are_stable_salted_and_not_the_original():
    a, b = pseudonymize("alice@corp", b"salt1"), pseudonymize("alice@corp", b"salt1")
    assert (
        a == b
        and a != pseudonymize("alice@corp", b"salt2")
        and "alice" not in a
        and a.startswith("p_")
    )
    assert salt_from_env("X", {"X": "s"}) == b"s" and len(salt_from_env("X", {})) == 16


# ---- configuration ----------------------------------------------------------------------------
def test_real_observability_config_loads_and_every_key_is_safe():
    cfg, sha = load_observability_config()
    assert len(sha) == 64 and all(
        k.startswith("dg.") and not is_forbidden(k) for k in cfg.allowed_attributes
    )


@pytest.mark.parametrize(
    "old, new",
    [
        ("  - dg.stage\n", "  - dg.quote\n"),
        ("  - dg.stage\n", "  - stage\n"),
        ("  - dg.stage\n", "  - dg.stage\n  - dg.stage\n"),
        ("observability_version: 1.0.0", "observability_version: one"),
    ],
)
def test_unsafe_observability_config_is_refused(config_copy, old, new):
    p = config_copy / "observability/observability.v1.yaml"
    p.write_text(p.read_text().replace(old, new, 1))
    with pytest.raises(ConfigError):
        load_observability_config(config_copy)


# ---- tracer -----------------------------------------------------------------------------------
def test_tracing_is_a_noop_outside_a_trace():
    assert not tracing_active()
    with span("x", dg__stage="s") as h:
        h.set(dg__reason="r")
        h.event("e")
        h.fail("Boom")  # all no-ops, nothing raised


def test_a_trace_has_a_root_children_valid_ids_and_ordered_times():
    sink = MemorySink()
    t = tracer([sink])
    with t.trace("classify", dg__request_id="r1"):
        with span("S1.rules", dg__stage="rules") as s1:
            s1.set(dg__latency_ms=1.5)
        with span("S3.llm.mid", dg__stage="llm:mid"), span("llm.call"):
            pass
    spans = {s.name: s for s in sink.spans}
    assert set(spans) == {"classify", "S1.rules", "S3.llm.mid", "llm.call"}
    root = spans["classify"]
    assert root.parent_span_id is None and spans["S1.rules"].parent_span_id == root.span_id
    assert spans["llm.call"].parent_span_id == spans["S3.llm.mid"].span_id
    assert (
        len({s.trace_id for s in sink.spans}) == 1
        and len(root.trace_id) == 32
        and len(root.span_id) == 16
    )
    for s in sink.spans:
        assert s.end_ns >= s.start_ns and root.start_ns <= s.start_ns and s.end_ns <= root.end_ns
    assert spans["S1.rules"].attributes["dg.latency_ms"] == 1.5 and not tracing_active()


def test_an_exception_marks_the_span_failed_records_only_the_class_and_propagates():
    sink = MemorySink()
    t = tracer([sink])
    with pytest.raises(RuntimeError), t.trace("classify"), span("S1.rules"):
        raise RuntimeError("the patient Jane Roe has diabetes")
    blob = "".join(s.model_dump_json() for s in sink.spans)
    assert "Jane Roe" not in blob and "diabetes" not in blob
    by = {s.name: s for s in sink.spans}
    assert (
        by["S1.rules"].status == "error"
        and by["S1.rules"].attributes["dg.error.type"] == "RuntimeError"
    )
    assert by["classify"].status == "error"  # the trace is still flushed


def test_events_are_scrubbed_like_attributes():
    sink = MemorySink()
    with tracer([sink]).trace("classify") as root:
        root.event(
            "guardrail",
            dg__guardrail__type="prompt_injection_suspected",
            dg__guardrail__trigger="SSN 905-37-6209",
            secret="x",
        )
    ev = sink.spans[0].events[0]
    assert (
        ev.name == "guardrail"
        and "905-37-6209" not in json.dumps(ev.model_dump())
        and "secret" not in ev.attributes
    )
    assert sink.spans[0].dropped_attributes == 1


def test_a_nested_trace_joins_the_active_trace():
    sink = MemorySink()
    t = tracer([sink])
    with t.trace("outer"), t.trace("inner"):
        pass
    assert {s.name for s in sink.spans} == {"outer", "inner"} and len(
        {s.trace_id for s in sink.spans}
    ) == 1


def test_a_failing_sink_never_breaks_the_traced_operation():
    class Broken:
        def write(self, spans):
            raise OSError("disk full")

    good = MemorySink()
    with tracer([Broken(), good]).trace("classify"):
        pass
    assert len(good.spans) == 1  # the other sink still received it


def test_concurrent_traces_do_not_mix_spans():
    sink = MemorySink()
    t = tracer([sink], clock=lambda: 1)

    def work(i):
        with t.trace("classify", dg__request_id=f"req-{i}"), span("S1.rules", dg__stage=f"s{i}"):
            pass

    threads = [threading.Thread(target=work, args=(i,)) for i in range(20)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    by_trace: dict[str, list[Span]] = {}
    for s in sink.spans:
        by_trace.setdefault(s.trace_id, []).append(s)
    assert len(by_trace) == 20 and all(len(v) == 2 for v in by_trace.values())
    for spans in by_trace.values():
        rid = next(s.attributes["dg.request_id"] for s in spans if s.name == "classify")
        assert (
            next(s.attributes["dg.stage"] for s in spans if s.name == "S1.rules")
            == "s" + rid.split("-")[1]
        )


def test_seeded_ids_are_deterministic_and_random_ids_differ():
    a, b = SeededIds(), SeededIds()
    assert a.trace_id() == b.trace_id() and a.span_id() == b.span_id()
    from observability import RandomIds

    r = RandomIds()
    assert r.trace_id() != r.trace_id() and len(r.trace_id()) == 32


def test_build_tracer_uses_the_real_allow_list():
    cfg, _ = load_observability_config()
    sink = MemorySink()
    t, salt = build_tracer(cfg, [sink], deterministic_ids=True)
    with t.trace("classify", dg__request_id="r1", content="RAW TEXT", dg__stage="x"):
        pass
    assert (
        "RAW TEXT" not in sink.spans[0].model_dump_json()
        and sink.spans[0].dropped_attributes == 1
        and len(salt) >= 1
    )


# ---- sinks ------------------------------------------------------------------------------------
def test_jsonl_sink_round_trips_and_keeps_concurrent_traces_intact(tmp_path):
    path = tmp_path / "deep" / "spans.jsonl"
    t = tracer([JsonlSink(path)], clock=lambda: 5)

    def work(i):
        with t.trace("classify", dg__request_id=f"req-{i}"), span("S1.rules"):
            pass

    threads = [threading.Thread(target=work, args=(i,)) for i in range(30)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    spans = read_jsonl(path)
    assert len(spans) == 60 and all(json.loads(x) for x in path.read_text().splitlines())
    assert len({s.trace_id for s in spans}) == 30


# ---- derived metrics --------------------------------------------------------------------------
def mkspan(name, parent="p" * 16, **attrs):
    ev = attrs.pop("events", [])
    return Span(trace_id="1" * 32, span_id="2" * 16, parent_span_id=None if name == "classify" else parent, name=name,
                start_ns=0, end_ns=2_000_000, attributes=attrs, events=ev, status=attrs.pop("status", "ok") if False else "ok")  # fmt: skip


def test_summarize_derives_every_metric_from_spans():
    roots = [mkspan("classify", **{"dg.stop_reason": "llm:mid_accepted", "dg.outcome.status": "ok", "dg.outcome.review_required": False, "dg.escalations": 0}),
             mkspan("classify", **{"dg.stop_reason": "no_stage_accepted", "dg.outcome.status": "review_required", "dg.outcome.review_required": True, "dg.escalations": 2}),
             mkspan("classify", **{"dg.stop_reason": "llm:mid_accepted", "dg.outcome.status": "degraded", "dg.outcome.review_required": False, "dg.escalations": 1})]  # fmt: skip
    calls = [mkspan("llm.call", **{"dg.tokens_in": 100, "dg.tokens_out": 10, "dg.llm.schema_invalid": True, "dg.est_cost_usd": 0.5}),
             mkspan("llm.call", **{"dg.tokens_in": 200, "dg.tokens_out": 20, "dg.llm.schema_invalid": False, "dg.est_cost_usd": 0.5}),
             mkspan("llm.call", **{"dg.tokens_in": 100, "dg.tokens_out": 10, "dg.llm.schema_invalid": False, "dg.est_cost_usd": 0.5})]  # fmt: skip
    stages = [
        mkspan("S1.rules", **{"dg.latency_ms": 1.0}),
        mkspan("S1.rules", **{"dg.latency_ms": 3.0}),
        mkspan("S3.llm.mid"),
    ]
    stages[2] = stages[2].model_copy(
        update={"attributes": {"dg.llm.evidence_total": 4, "dg.llm.evidence_verified": 3}}
    )
    guard = Span(trace_id="1" * 32, span_id="3" * 16, parent_span_id="p" * 16, name="S0.guardrails", start_ns=0, end_ns=1,
                 events=[SpanEvent(name="guardrail", ts_ns=1, attributes={"dg.guardrail.type": "prompt_injection_suspected"}),
                         SpanEvent(name="retry", ts_ns=2)])  # fmt: skip
    m = summarize([*roots, *calls, *stages, guard])
    assert m["n_traces"] == 3 and m["route_distribution"] == {
        "llm:mid_accepted": 2,
        "no_stage_accepted": 1,
    }
    assert m["review_rate"] == pytest.approx(1 / 3) and m["status_distribution"]["degraded"] == 1
    assert (
        m["stage_latency"]["S1.rules"]["p50_ms"] == pytest.approx(2.0)
        and m["stage_latency"]["S1.rules"]["n"] == 2
    )
    assert m["stage_latency"]["S1.rules"]["p95_ms"] == pytest.approx(
        2.9
    )  # linear percentile of [1, 3]
    assert m["stage_latency"]["S3.llm.mid"]["p50_ms"] == pytest.approx(
        2.0
    )  # falls back to wall-clock duration
    assert (
        m["llm_calls"] == 3
        and m["tokens_per_trace"] == pytest.approx(440 / 3)
        and m["est_cost_usd_per_trace"] == pytest.approx(0.5)
    )
    assert m["schema_validation_failures"] == 1 and m["schema_failure_rate"] == pytest.approx(1 / 3)
    assert m["evidence_verification_failure_rate"] == pytest.approx(0.25)
    assert m["fallbacks"] == {
        "escalations": 3,
        "degraded_results": 1,
        "failed_stage_spans": 0,
        "retries": 1,
    }
    assert m["guardrail_triggers"] == {"prompt_injection_suspected": 1}


def test_summarize_of_nothing_is_safe():
    m = summarize([])
    assert (
        m["n_traces"] == 0
        and m["review_rate"] is None
        and m["stage_latency"] == {}
        and m["est_cost_usd_per_trace"] is None
    )


# ---- privacy audit ----------------------------------------------------------------------------
DOC = mkdoc("d1", content="The quarterly restructuring plan for the battery unit will be announced to the board on Friday morning.",
            filename="Layoff_Planning_Notes.docx")  # fmt: skip


def dump(**attrs):
    return Span(
        trace_id="1" * 32,
        span_id="2" * 16,
        name="classify",
        start_ns=1_700_000_000_000_000_000,
        end_ns=1_700_000_000_000_000_500,
        attributes=attrs,
    ).model_dump_json()


def test_audit_passes_a_clean_dump_and_counts_what_it_checked():
    r = audit_spans(dump(**{"dg.reason": "llm:mid_accepted", "dg.content_hash": "a" * 64}), [DOC])
    assert r.clean and r.windows_checked > 5 and r.filenames_checked == 1 and r.pattern_checks == 5


def test_audit_catches_document_text_however_it_is_cased_or_spaced():
    r = audit_spans(dump(**{"dg.reason": "PLAN for THE   battery unit will be announced"}), [DOC])
    assert ("content_window", "d1") in r.leaks


def test_audit_catches_evidence_spans_filenames_and_sensitive_patterns():
    from evals.classification.dataset.schema import GoldEvidenceSpan

    doc = DOC.model_copy(
        update={
            "gold_evidence_spans": [
                GoldEvidenceSpan(
                    label="MA_CORP_STRATEGY",
                    char_start=4,
                    char_end=40,
                    text="quarterly restructuring plan for",
                )
            ]
        }
    )
    assert ("evidence_span", "d1") in audit_spans(
        dump(a="Quarterly restructuring plan for"), [doc]
    ).leaks
    assert ("filename", "d1") in audit_spans(dump(a="Layoff_Planning_Notes"), [DOC]).leaks
    for value, name in [
        ("905-37-6209", "ssn_like"),
        ("4111 1111 1111 1111", "card_like"),
        ("jane.roe@corp.example", "email"),
        ("dgsk_live_abc", "secret_prefix"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private_key"),
    ]:
        assert ("pattern", name) in audit_spans(dump(a=value), [DOC]).leaks, name


def test_audit_does_not_confuse_ids_hashes_and_timestamps_with_card_numbers():
    line = dump(**{"dg.content_hash": "1234567890123456" * 4, "dg.request_id": "req-4111"})
    assert audit_spans(
        line, [DOC]
    ).clean  # long hex/digit ids and 19-digit timestamps are not cards


def test_audit_scans_unparseable_lines_whole_and_reads_keys_and_nested_values():
    assert not audit_spans("garbage 905-37-6209 garbage", [DOC]).clean
    nested = json.dumps(
        {"events": [{"attributes": {"x": "plan for the battery unit will be announced"}}]}
    )
    assert not audit_spans(nested, [DOC]).clean
    assert "keyname" in strings_of(json.dumps({"keyname": 1}))


# ---- OpenTelemetry bridge ---------------------------------------------------------------------
def test_otel_bridge_reemits_spans_with_parents_attributes_events_and_errors():
    pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    t = tracer([OtelSink(provider)])
    with pytest.raises(ValueError), t.trace("classify", dg__request_id="r1") as root:
        root.event("guardrail", dg__guardrail__type="prompt_injection_suspected")
        with span("S1.rules", dg__stage="rules"):
            pass
        with span("S3.llm.mid") as s3:
            s3.set(dg__llm__served_model="gpt-x")
            raise ValueError("boom with text")
    done = {s.name: s for s in exporter.get_finished_spans()}
    assert set(done) == {"classify", "S1.rules", "S3.llm.mid"}
    assert done["S1.rules"].parent.span_id == done["classify"].context.span_id
    assert done["S3.llm.mid"].parent.span_id == done["classify"].context.span_id
    assert (
        done["classify"].attributes["dg.request_id"] == "r1"
        and "dg.trace_id" in done["classify"].attributes
    )
    assert done["S3.llm.mid"].attributes["dg.llm.served_model"] == "gpt-x"
    assert (
        done["S3.llm.mid"].status.status_code.name == "ERROR"
        and done["S1.rules"].status.status_code.name != "ERROR"
    )
    assert [e.name for e in done["classify"].events] == ["guardrail"]
    assert "boom with text" not in str([s.attributes for s in done.values()])
    assert done["classify"].end_time >= done["S1.rules"].end_time


def test_azure_monitor_glue_needs_the_optional_extra(monkeypatch):
    """Forces the absent-package path regardless of whether the extra happens to be installed here,
    so this is deterministic in every environment. The glue itself is verified against a real
    Application Insights resource (2026-09-21; docs/uc4/observability-engine.md), never in a unit
    test: a real call would contact a live service and start a background export thread."""
    import sys

    from observability.sinks import azure_monitor_sink

    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", None)
    with pytest.raises(ImportError):
        azure_monitor_sink("InstrumentationKey=00000000-0000-0000-0000-000000000000")


def test_flush_azure_monitor_is_safe_with_no_provider_configured():
    from observability.sinks import flush_azure_monitor

    assert flush_azure_monitor() is True  # the default global provider has no force_flush


def test_flush_azure_monitor_calls_the_configured_providers_force_flush(monkeypatch):
    from opentelemetry import trace

    from observability.sinks import flush_azure_monitor

    calls = []

    class FakeProvider:
        def force_flush(self, timeout_millis):
            calls.append(timeout_millis)
            return True

    monkeypatch.setattr(trace, "get_tracer_provider", lambda: FakeProvider())
    assert flush_azure_monitor(5_000) is True
    assert calls == [5_000]


def test_traced_classifier_exports_a_pseudonymous_caller_never_the_raw_id():
    from app.classification.schemas import (
        Caller,
        ClassificationRequest,
        ClassificationResult,
        Document,
    )
    from observability import TracedClassifier

    class Stub:
        name, version = "stub", "0"

        def params(self):
            return {}

        def classify(self, req):
            return ClassificationResult(
                request_id=req.request_id,
                content_hash=req.document.content_hash(),
                status="rejected",
            )

    sink = MemorySink()
    cfg, _ = load_observability_config()
    tr, salt = build_tracer(cfg, [sink], deterministic_ids=True)
    doc = Document(content="hello there", filename="a.txt", extension="txt")
    req = ClassificationRequest(
        request_id="r1", document=doc, caller=Caller(caller_id="alice-analyst")
    )
    TracedClassifier(Stub(), tr, salt).classify(req)
    root = sink.spans[0]
    assert (
        root.attributes["dg.caller"] == pseudonymize("alice-analyst", salt)
        and "alice" not in root.model_dump_json()
    )
