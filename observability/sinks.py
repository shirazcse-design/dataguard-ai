"""Span sinks: JSONL (local default), in-memory (tests), optional OpenTelemetry SDK bridge.

The bridge and the Azure Monitor glue are OPTIONAL and lazily imported. The bridge is tested with
the SDK's in-memory exporter. The Azure Monitor glue ran successfully against a real Application
Insights resource (`dataguard-uc4-appinsights`, 2026-09-22, via `dataguard-uc4 obs azure-check`):
the SDK reported no error and `force_flush` completed without timing out on two separate runs.
Portal-confirmed on 2026-09-26: an `obs azure-check --llm` trace was opened in Microsoft Foundry's
Tracing view (decision D9.31); see `docs/uc4/observability-engine.md` for the exact status.
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


# Span names that carry an LLM call's own dg.* attributes, for _genai_attrs below: the classifier's
# LLM stage and the Batch Triage Agent's planner turn.
# UC6 (Data Security Policy Copilot) spans map onto the same conventions: `uc6.generate` and
# `uc6.agent.planner` are model calls, `uc6.agent` an agent invocation, `uc6.tool` a tool call.
# UC1 (Agentic DLP) follows the same shape: `uc1.agent.planner` / `uc1.agent` / `uc1.tool`.
# UC2 (Insider Risk): one span per agent (`uc2.orchestrator`, `uc2.behavior_agent`,
# `uc2.investigation_agent`, `uc2.risk_agent`, `uc2.single_agent`), `uc2.agent.planner`, `uc2.tool`.
_PLANNER_SPANS = frozenset(
    {
        "agent.planner",
        "uc6.agent.planner",
        "uc1.agent.planner",
        "uc2.agent.planner",
        "uc3.agent.planner",
    }
)
_LLM_CALL_SPANS = frozenset({"llm.call", "uc6.generate", *_PLANNER_SPANS})
_AGENT_SPANS = frozenset({
    "agent.document", "uc6.agent", "uc1.agent", "uc2.orchestrator", "uc2.behavior_agent",
    "uc2.investigation_agent", "uc2.risk_agent", "uc2.single_agent", "uc3.agent",
})  # fmt: skip
_TOOL_SPANS = frozenset({"agent.tool", "uc6.tool", "uc1.tool", "uc2.tool", "uc3.tool"})
_PROVIDER = "azure.ai.openai"


def _genai_attrs(span_name: str, attrs: dict[str, Any]) -> dict[str, Any]:
    """Derive OpenTelemetry GenAI semantic-convention attributes (`gen_ai.*`, `error.type`) from our
    own already-redacted `dg.*` attributes: one LLM call (`llm.call`, `agent.planner`) becomes a
    `chat` span, the agent's per-document root an `invoke_agent` span, and its tool calls
    `execute_tool` spans.

    This is export-time-only: it does not touch the Redactor or its `dg.*`-only allow-list (PRD 19),
    so the deny-by-default privacy invariant is unchanged - these are additional, DERIVED views of
    values already cleared for export, not new data. It exists so Foundry's Trace view (and any
    other consumer built on the OpenTelemetry GenAI conventions) can render the same span, since
    that convention is what such tools key off, not our own `dg.*` namespace.
    """
    if span_name in _AGENT_SPANS:
        return _genai_agent_attrs(attrs)
    if span_name in _TOOL_SPANS:
        return _genai_tool_attrs(attrs)
    if span_name not in _LLM_CALL_SPANS:
        return {}
    if span_name in _PLANNER_SPANS and not attrs.get("dg.llm.model_id"):
        # The offline planner is not a model call; labelling it "chat" would misstate what ran.
        return {}
    if attrs.get("dg.llm.cached") is True:
        # A replayed response: no model was called, so no "chat" span and no token usage - the
        # recorded tokens would otherwise inflate Foundry's usage and cost views (D9.35).
        return {}
    out: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": _PROVIDER,
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


def _genai_agent_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"gen_ai.operation.name": "invoke_agent"}
    if attrs.get("dg.agent.name"):
        out["gen_ai.agent.name"] = attrs["dg.agent.name"]
    if attrs.get("dg.agent.planner") in ("foundry", "foundry-agent-service"):
        out["gen_ai.provider.name"] = _PROVIDER
    if attrs.get("dg.error.type"):
        out["error.type"] = attrs["dg.error.type"]
    return out


def _genai_tool_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.type": "function"}
    if attrs.get("dg.agent.tool"):
        out["gen_ai.tool.name"] = attrs["dg.agent.tool"]
    if attrs.get("dg.error.type"):
        out["error.type"] = attrs["dg.error.type"]
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
    import os

    from azure.monitor.opentelemetry import (
        configure_azure_monitor,  # type: ignore[import-not-found]
    )
    from opentelemetry import trace

    # azure-monitor-opentelemetry >= 1.8 defaults to a rate-limited sampler (5 traces/s). This
    # sink re-emits a whole request's spans in one burst, so that default dropped spans from the
    # middle of traces (found in the UC1 live check, 2026-10-01). Export every span unless the
    # operator chose a sampler explicitly. Sampling changes completeness only, never content.
    os.environ.setdefault("OTEL_TRACES_SAMPLER", "always_on")
    configure_azure_monitor(connection_string=connection_string)
    return OtelSink(trace.get_tracer_provider())


def flush_azure_monitor(timeout_millis: int = 30_000) -> bool:
    """Force the Azure Monitor exporter to send now rather than on its periodic timer. Returns
    whether the flush completed within the timeout. Only meaningful after `azure_monitor_sink`."""
    from opentelemetry import trace

    provider = trace.get_tracer_provider()
    force_flush = getattr(provider, "force_flush", None)
    return bool(force_flush(timeout_millis)) if force_flush else True
