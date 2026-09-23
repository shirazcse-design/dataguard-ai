"""`dataguard-uc4 obs azure-check`: the wiring to Azure Monitor, tested with an injected fake sink.

No test contacts a real Azure resource: the CLI's lazy `from observability import azure_monitor_sink`
is patched at the `observability` module before each call, so `configure_azure_monitor` (which starts a
background exporter thread against a live endpoint) is never reached from a test.
"""

from __future__ import annotations

import json

import observability
from app.classification.cli import main


def test_a_missing_connection_string_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.delenv("DATAGUARD_AZURE_MONITOR_CONNECTION_STRING", raising=False)
    assert main(["obs", "azure-check"]) == 2
    err = capsys.readouterr().err
    assert "DATAGUARD_AZURE_MONITOR_CONNECTION_STRING is not set" in err


def test_a_missing_extra_is_reported_as_a_usage_error_not_a_traceback(monkeypatch, capsys):
    monkeypatch.setenv("DATAGUARD_AZURE_MONITOR_CONNECTION_STRING", "super-secret-value")

    def boom(_connection_string):
        raise ImportError("no azure package")

    monkeypatch.setattr(observability, "azure_monitor_sink", boom)
    assert main(["obs", "azure-check"]) == 2
    err = capsys.readouterr().err
    assert "azure-monitor" in err and "pip install" in err
    assert "super-secret-value" not in err


def test_a_successful_check_sends_spans_through_the_sink_and_never_prints_the_connection_string(
    monkeypatch, tmp_path, capsys
):
    secret = "InstrumentationKey=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee;super-secret-suffix"
    monkeypatch.setenv("DATAGUARD_AZURE_MONITOR_CONNECTION_STRING", secret)
    fake_sink = observability.MemorySink()
    seen_conn = []

    def fake_azure_monitor_sink(connection_string):
        seen_conn.append(connection_string)
        return fake_sink

    monkeypatch.setattr(observability, "azure_monitor_sink", fake_azure_monitor_sink)
    monkeypatch.setattr(observability, "flush_azure_monitor", lambda *a, **k: True)

    trace_out = tmp_path / "spans.jsonl"
    assert main(["obs", "azure-check", "--trace-out", str(trace_out)]) == 0

    out, err = capsys.readouterr()
    assert secret not in out and secret not in err  # never printed, even though we hold it locally
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["flushed"] is True
    assert payload["request_id"].startswith("az-verify-")
    assert payload["local_spans"] == str(trace_out)

    # the real connection string DID reach the sink factory (the wiring is exercised end to end)...
    assert seen_conn == [secret]
    # ...but nothing derived from it, and no document text, reached the sink's own captured spans
    assert fake_sink.spans and any(s.name == "classify" for s in fake_sink.spans)
    blob = json.dumps([s.model_dump() for s in fake_sink.spans])
    assert secret not in blob and "cafeteria" not in blob.lower()

    assert trace_out.exists()  # the local JSONL copy was also written


def test_a_failed_result_status_is_a_non_zero_exit(monkeypatch, tmp_path):
    monkeypatch.setenv("DATAGUARD_AZURE_MONITOR_CONNECTION_STRING", "fake")
    monkeypatch.setattr(observability, "azure_monitor_sink", lambda c: observability.MemorySink())
    monkeypatch.setattr(observability, "flush_azure_monitor", lambda *a, **k: True)

    import app.classification.service as service_mod

    class FailResult:
        status = "error"

    class FailSvc:
        def __init__(self, *a, **k):
            pass

        def classify_text(self, *a, **k):
            return FailResult()

    monkeypatch.setattr(service_mod, "ClassificationService", FailSvc)
    assert main(["obs", "azure-check", "--trace-out", str(tmp_path / "s.jsonl")]) == 4
