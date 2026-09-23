"""Tracing, redaction, sinks, derived metrics and the privacy audit (architecture section 14).

Tracing is a no-op unless a trace is active; export is deny-by-default (see `redaction`).
"""

from .audit import AuditResult, audit_spans
from .config import ObservabilityConfig, build_tracer, load_observability_config
from .dashboard import render_dashboard
from .instrument import TracedClassifier, outcome_attrs, request_attrs
from .metrics import summarize
from .redaction import Redactor, pseudonymize
from .sinks import (
    JsonlSink,
    MemorySink,
    OtelSink,
    azure_monitor_sink,
    flush_azure_monitor,
    read_jsonl,
)
from .trace import RandomIds, SeededIds, Tracer, current_span, span, tracing_active
from .types import Span, SpanEvent

__all__ = [
    "AuditResult",
    "JsonlSink",
    "MemorySink",
    "ObservabilityConfig",
    "OtelSink",
    "RandomIds",
    "Redactor",
    "SeededIds",
    "Span",
    "SpanEvent",
    "TracedClassifier",
    "Tracer",
    "audit_spans",
    "azure_monitor_sink",
    "build_tracer",
    "current_span",
    "flush_azure_monitor",
    "load_observability_config",
    "outcome_attrs",
    "pseudonymize",
    "read_jsonl",
    "render_dashboard",
    "request_attrs",
    "span",
    "summarize",
    "tracing_active",
]
