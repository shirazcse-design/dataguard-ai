"""UC6 observability evidence: telemetry from a real (replayed) run, plus a privacy audit.

Runs every golden question at each level through the PRODUCTION tracer and redactor
(`config/observability/observability.v1.yaml`), then:

* audits the exported spans with UC4's `observability.audit.audit_spans` against every golden
  question and every policy section (any 5 consecutive words of either is a leak), plus the
  auditor's sensitive-value patterns;
* derives the operational metrics a dashboard would show, from span attributes alone:
  request/stage latency, statuses, insufficient-evidence rate, citation-verification failures,
  guardrail events, model tokens, agent tool calls and stop reasons.

REPLAY latencies are local replay timings (no network), except `dg.latency_ms` on model spans,
which is the RECORDED provider latency. The report says so.
"""

from __future__ import annotations

import json
from collections import Counter
from types import SimpleNamespace
from typing import Any

from observability.audit import audit_spans
from observability.config import build_tracer, load_observability_config
from observability.sinks import MemorySink
from observability.types import Span


def run_traced(mode: str, levels: list[str], items: list) -> tuple[list[Span], Any]:
    from app.policy.service import build_copilot

    cfg, _ = load_observability_config()
    sink = MemorySink()
    tracer, _salt = build_tracer(cfg, [sink], deterministic_ids=True)
    copilot = build_copilot(mode, tracer=tracer)
    for level in levels:
        for item in items:
            copilot.answer(item.question, level)
    return sink.spans, copilot


def privacy_audit(spans: list[Span], items: list, corpus: Any) -> dict[str, Any]:
    text = json.dumps([s.model_dump() for s in spans])
    docs = [
        SimpleNamespace(content=i.question, filename="q", doc_id=i.id, gold_evidence_spans=[])
        for i in items
    ] + [
        SimpleNamespace(content=c.body, filename="c", doc_id=c.chunk_id, gold_evidence_spans=[])
        for c in corpus.chunks
    ]
    res = audit_spans(text, docs, window=5)
    return {
        "clean": res.clean,
        "leaks": [list(x) for x in res.leaks],
        "sources_checked": len(docs),
        "windows_checked": res.windows_checked,
        "pattern_checks": res.pattern_checks,
        "spans_audited": len(spans),
    }


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))], 2)


def summarise(spans: list[Span]) -> dict[str, Any]:
    roots = [s for s in spans if s.name == "uc6.request"]
    by_level: dict[str, Any] = {}
    for level in sorted({r.attributes.get("dg.policy.level") for r in roots}):
        rs = [r for r in roots if r.attributes.get("dg.policy.level") == level]
        ids = {r.trace_id for r in rs}
        mine = [s for s in spans if s.trace_id in ids]
        statuses = Counter(r.attributes.get("dg.policy.status") for r in rs)
        gen = [s for s in mine if s.name in ("uc6.generate", "uc6.agent.planner")]
        verify = [s for s in mine if s.name == "uc6.citation_verify"]
        tools = [s for s in mine if s.name == "uc6.tool"]
        agents = [s for s in mine if s.name == "uc6.agent"]
        stage_ms = {
            name: _pct([s.duration_ms for s in mine if s.name == name], 0.5)
            for name in sorted({s.name for s in mine})
        }
        by_level[level] = {
            "requests": len(rs),
            "statuses": dict(sorted(statuses.items())),
            "insufficient_evidence_rate": round(statuses["INSUFFICIENT_EVIDENCE"] / len(rs), 4),
            "review_required_rate": round(
                sum(bool(r.attributes.get("dg.outcome.review_required")) for r in rs) / len(rs), 4
            ),
            "request_ms_p50": _pct([r.duration_ms for r in rs], 0.5),
            "request_ms_p95": _pct([r.duration_ms for r in rs], 0.95),
            "stage_ms_p50": stage_ms,
            "model_calls": len(gen),
            "model_calls_replayed": sum(bool(s.attributes.get("dg.llm.cached")) for s in gen),
            "recorded_model_latency_ms_p50": _pct(
                [s.attributes["dg.latency_ms"] for s in gen if "dg.latency_ms" in s.attributes], 0.5
            ),
            "tokens_in": sum(s.attributes.get("dg.tokens_in", 0) for s in gen),
            "tokens_out": sum(s.attributes.get("dg.tokens_out", 0) for s in gen),
            "claims_unverified": sum(
                s.attributes.get("dg.policy.claims_unverified", 0) for s in verify
            ),
            "fabricated_citations": sum(
                s.attributes.get("dg.policy.fabricated_citations", 0) for s in verify
            ),
            "guardrail_events": dict(
                Counter(t for r in rs for t in r.attributes.get("dg.guardrail.type", []))
            ),
            "tool_calls": dict(Counter(s.attributes.get("dg.agent.tool") for s in tools)),
            "tool_errors": dict(
                Counter(
                    s.attributes["dg.agent.tool_error"]
                    for s in tools
                    if "dg.agent.tool_error" in s.attributes
                )
            ),
            "agent_stop_reasons": dict(
                Counter(s.attributes.get("dg.agent.stopped_reason") for s in agents)
            ),
            "dense_status": dict(
                Counter(
                    s.attributes["dg.policy.dense_status"]
                    for s in mine
                    if s.name in ("uc6.retrieve", "uc6.tool")
                    and "dg.policy.dense_status" in s.attributes
                )
            ),
        }
    return {
        "spans": len(spans),
        "span_names": dict(sorted(Counter(s.name for s in spans).items())),
        "attribute_keys": sorted({k for s in spans for k in s.attributes}),
        "by_level": by_level,
    }


def render_markdown(report: dict[str, Any]) -> str:
    audit = report["privacy_audit"]
    summ = report["telemetry"]
    lines = [
        "# UC6 observability evidence (generated)",
        "",
        "Generated by `dataguard-policy obs report`. Do not edit by hand.",
        "",
        f"* Run mode: **{report['mode'].upper()}** - every golden question at levels "
        f"{', '.join(report['levels'])}, through the PRODUCTION tracer and redactor "
        "(`config/observability/observability.v1.yaml`).",
        "* Stage and request timings are LOCAL REPLAY timings (no network). The recorded provider "
        "latency is reported separately as `recorded_model_latency_ms_p50` (single-shot generation "
        "only; agent planner turns are recorded without latency).",
        "",
        "## Privacy audit",
        "",
        f"* Result: **{'CLEAN' if audit['clean'] else 'LEAKS FOUND'}** - {audit['spans_audited']} "
        f"spans audited against {audit['sources_checked']} sources (36 questions + every policy "
        f"section), {audit['windows_checked']} five-word windows, {audit['pattern_checks']} "
        "sensitive-value patterns (UC4's `observability.audit.audit_spans`).",
    ]
    if audit["leaks"]:
        lines.append(f"* Leaks: {audit['leaks']}")
    lines += [
        f"* Exported attribute keys ({len(summ['attribute_keys'])}): "
        + ", ".join(f"`{k}`" for k in summ["attribute_keys"]),
        "",
        "## Telemetry by level",
        "",
    ]
    keys = [
        "requests", "statuses", "insufficient_evidence_rate", "review_required_rate",
        "request_ms_p50", "request_ms_p95", "model_calls", "model_calls_replayed",
        "recorded_model_latency_ms_p50", "tokens_in", "tokens_out", "claims_unverified",
        "fabricated_citations", "guardrail_events", "dense_status", "tool_calls", "tool_errors",
        "agent_stop_reasons", "stage_ms_p50",
    ]  # fmt: skip
    levels = list(summ["by_level"])
    lines.append("| Signal | " + " | ".join(levels) + " |")
    lines.append("|---|" + "---|" * len(levels))
    for k in keys:
        cells = [str(summ["by_level"][lv].get(k, "-")).replace("|", "/") for lv in levels]
        lines.append(f"| {k} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Span names",
        "",
        ", ".join(f"`{k}` x{v}" for k, v in summ["span_names"].items()),
    ]
    return "\n".join(lines) + "\n"
