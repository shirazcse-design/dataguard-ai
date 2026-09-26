"""Span sinks: JSONL (local default), in-memory (tests), optional OpenTelemetry SDK bridge.

The bridge and the Azure Monitor glue are OPTIONAL and lazily imported. The bridge is tested with
the SDK's in-memory exporter. The Azure Monitor glue ran successfully against a real Application
Insights resource (`dataguard-uc4-appinsights`, 2026-09-22, via `dataguard-uc4 obs azure-check`):
the SDK reported no error and `force_flush` completed without timing out on two separate runs.
Portal-side confirmation (Transaction search) has not been completed; see
`docs/uc4/observability-engine.md` for the exact status.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .types import Span


class MemorySink:
    def __init__(self) -> None:
        self.spans: list[Span] = []
        self._lock = threading.Lock()

    def write(self, spans: list[Span]) -> None:
        with self._lock:
            self.spans.extend(spans)


class JsonlSink:
    """Appends one JSON object per span; a lock keeps concurrent traces from interleaving."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, spans: list[Span]) -> None:
        blob = "".join(s.model_dump_json() + "\n" for s in spans)
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(blob)


def read_jsonl(path: Path | str) -> list[Span]:
    out: list[Span] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(Span.model_validate_json(line))
    return out


# Span names that carry an LLM call's own dg.* attributes, for _genai_attrs below. Kept as a set
# (not a single literal) so a future span shape - e.g. the Batch Triage Agent's own planner call -
# can opt in by adding its name here, without changing the translation logic itself.
_LLM_CALL_SPANS = frozenset({"llm.call"})


def _genai_attrs(span_name: str, attrs: dict[str, Any]) -> dict[str, Any]:
    """Derive OpenTelemetry GenAI semantic-convention attributes (`gen_ai.*`, `error.type`) from our
    own already-redacted `dg.*` attributes, for spans that represent one LLM call.

    This is export-time-only: it does not touch the Redactor or its `dg.*`-only allow-list (PRD 19),
    so the deny-by-default privacy invariant is unchanged - these are additional, DERIVED views of
    values already cleared for export, not new data. It exists so Foundry's Trace view (and any
    other consumer built on the OpenTelemetry GenAI conventions) can render the same span, since
    that convention is what such tools key off, not our own `dg.*` namespace.
    """
    if span_name not in _LLM_CALL_SPANS:
        return {}
    out: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "azure.ai.openai",
    }
    if attrs.get("dg.llm.model_id"):
        out["gen_ai.request.model"] = attrs["dg.llm.model_id"]
    err = attrs.get("dg.llm.error_kind") or attrs.get("dg.error.type")
    if not err:
        # A response.model only makes sense once a response actually came back; a failed call
        # never received one, so it must not fall back to the request model as if it had.
        served = attrs.get("dg.llm.served_model") or attrs.get("dg.llm.model_id")
        if served:
            out["gen_ai.response.model"] = served
    if "dg.tokens_in" in attrs:
        out["gen_ai.usage.input_tokens"] = attrs["dg.tokens_in"]
    if "dg.tokens_out" in attrs:
        out["gen_ai.usage.output_tokens"] = attrs["dg.tokens_out"]
    if err:
        out["error.type"] = err
    return out


class OtelSink:
    """Re-emits our spans through an OpenTelemetry TracerProvider (SDK ids are assigned by the SDK;
    our ids are kept as `dg.trace_id` / `dg.span_id` attributes). Requires the `otel` extra."""

    def __init__(self, tracer_provider: Any, service_name: str = "dataguard-uc4") -> None:
        try:
            from opentelemetry import trace  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on the optional extra
            raise RuntimeError("OtelSink needs the 'otel' extra (opentelemetry-sdk)") from exc
        self._tracer = tracer_provider.get_tracer(service_name)

    def write(self, spans: list[Span]) -> None:
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode

        made: dict[str, Any] = {}
        for s in sorted(spans, key=lambda x: (x.start_ns, x.parent_span_id is not None)):
            parent = made.get(s.parent_span_id) if s.parent_span_id else None
            ctx = trace.set_span_in_context(parent) if parent is not None else None
            attrs = {
                **s.attributes,
                **_genai_attrs(s.name, s.attributes),
                "dg.trace_id": s.trace_id,
                "dg.span_id": s.span_id,
            }
            otel = self._tracer.start_span(
                s.name, context=ctx, start_time=s.start_ns, attributes=attrs
            )
            for ev in s.events:
                otel.add_event(ev.name, ev.attributes, timestamp=ev.ts_ns)
            if s.status == "error":
                otel.set_status(Status(StatusCode.ERROR))
            made[s.span_id] = otel
        for s in sorted(spans, key=lambda x: -x.end_ns):  # children end before parents
            made[s.span_id].end(end_time=s.end_ns)


def azure_monitor_sink(connection_string: str) -> OtelSink:
    """Configure Azure Monitor and return a bridge to its tracer provider. Requires the
    `azure-monitor` extra (`pip install 'dataguard-ai[azure-monitor]'`); raises `ImportError` if it
    is not installed. The caller must supply the connection string from the environment or a secret
    store, never a literal in code, a config file or a log line.
    """
    from azure.monitor.opentelemetry import (
        configure_azure_monitor,  # type: ignore[import-not-found]
    )
    from opentelemetry import trace

    configure_azure_monitor(connection_string=connection_string)
    return OtelSink(trace.get_tracer_provider())


def flush_azure_monitor(timeout_millis: int = 30_000) -> bool:
    """Force the Azure Monitor exporter to send now rather than on its periodic timer. Returns
    whether the flush completed within the timeout. Only meaningful after `azure_monitor_sink`."""
    from opentelemetry import trace

    provider = trace.get_tracer_provider()
    force_flush = getattr(provider, "force_flush", None)
    return bool(force_flush(timeout_millis)) if force_flush else True
