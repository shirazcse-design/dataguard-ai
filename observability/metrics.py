"""Derived metrics from spans (architecture section 14): stage latency P50/P95, route distribution,
review rate, tokens and cost per document, schema-validation failures, evidence-verification
failure rate, fallback counts and guardrail triggers. Pure functions over `Span` records."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np

from .types import Span

ROOT = "classify"


def _pct(values: list[float], q: float) -> float | None:
    return float(np.percentile(values, q)) if values else None


def _latency_ms(s: Span) -> float:
    """Reported (recorded) stage latency when present, else the span's own wall-clock duration."""
    v = s.attributes.get("dg.latency_ms")
    return float(v) if isinstance(v, int | float) else s.duration_ms


def summarize(spans: list[Span]) -> dict[str, Any]:
    roots = [s for s in spans if s.name == ROOT]
    n = len(roots)
    by_stage: dict[str, list[Span]] = defaultdict(list)
    for s in spans:
        if s.name != ROOT:
            by_stage[s.name].append(s)

    def rate(a: float, b: float) -> float | None:
        return a / b if b else None

    stage = {}
    for name, ss in sorted(by_stage.items()):
        lat = [_latency_ms(s) for s in ss]
        stage[name] = {
            "n": len(ss),
            "p50_ms": _pct(lat, 50),
            "p95_ms": _pct(lat, 95),
            "errors": sum(s.status == "error" for s in ss),
        }
    route = Counter(str(r.attributes.get("dg.stop_reason", "")) for r in roots)
    status = Counter(str(r.attributes.get("dg.outcome.status", "")) for r in roots)
    llm_calls = [s for s in spans if s.name == "llm.call"]
    tok = sum(
        int(s.attributes.get("dg.tokens_in", 0)) + int(s.attributes.get("dg.tokens_out", 0))
        for s in llm_calls
    )
    cost = [
        float(s.attributes["dg.est_cost_usd"])
        for s in llm_calls
        if "dg.est_cost_usd" in s.attributes
    ]
    ev_total = sum(int(s.attributes.get("dg.llm.evidence_total", 0)) for s in spans)
    ev_ok = sum(int(s.attributes.get("dg.llm.evidence_verified", 0)) for s in spans)
    guard = Counter(
        str(e.attributes.get("dg.guardrail.type", ""))
        for s in spans
        for e in s.events
        if e.name == "guardrail"
    )
    retries = sum(1 for s in spans for e in s.events if e.name == "retry")
    return {
        "n_traces": n,
        "n_spans": len(spans),
        "stage_latency": stage,
        "route_distribution": dict(route),
        "status_distribution": dict(status),
        "review_rate": rate(
            sum(bool(r.attributes.get("dg.outcome.review_required")) for r in roots), n
        ),
        "llm_calls": len(llm_calls),
        "llm_calls_per_trace": rate(len(llm_calls), n),
        "tokens_per_trace": rate(tok, n),
        "est_cost_usd_per_trace": rate(sum(cost), n) if cost else None,
        "schema_validation_failures": sum(
            bool(s.attributes.get("dg.llm.schema_invalid")) for s in llm_calls
        ),
        "schema_failure_rate": rate(
            sum(bool(s.attributes.get("dg.llm.schema_invalid")) for s in llm_calls), len(llm_calls)
        ),
        "evidence_quotes": ev_total,
        "evidence_verification_failure_rate": rate(ev_total - ev_ok, ev_total),
        "fallbacks": {
            "escalations": sum(int(r.attributes.get("dg.escalations", 0)) for r in roots),
            "degraded_results": status.get("degraded", 0),
            "failed_stage_spans": sum(s.status == "error" for s in spans if s.name != ROOT),
            "retries": retries,
        },
        "guardrail_triggers": dict(guard),
        "dropped_attributes": sum(s.dropped_attributes for s in spans),
    }
