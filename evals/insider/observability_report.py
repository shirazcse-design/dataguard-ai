"""UC2 observability evidence: all 36 golden cases replayed (full 4-agent system, recorded live
turns) through the PRODUCTION tracer and redactor, a privacy audit of the exported spans, and a
telemetry summary that answers the operating questions (why anomalous, which agents and tools ran,
where latency went, what failed, why it stopped, what the outcome was).
"""

from __future__ import annotations

import json
from collections import Counter
from types import SimpleNamespace
from typing import Any

from evals.insider import golden_build as G
from evals.policy.observability_report import _pct
from observability.audit import audit_spans
from observability.config import build_tracer, load_observability_config
from observability.sinks import MemorySink, _genai_attrs


def run_traced(
    mode: str = "replay", architecture: str = "full"
) -> tuple[list[Any], list[dict], Any]:
    from app.insider.service import build_investigator
    from evals.insider.agent_eval import load_golden

    cfg, _ = load_observability_config()
    sink = MemorySink()
    tracer, _ = build_tracer(cfg, [sink], deterministic_ids=True)
    inv = build_investigator(mode, architecture=architecture, tracer=tracer)
    cases = load_golden()
    for c in cases:
        inv.investigate(c)
    return sink.spans, cases, inv


def privacy_audit(spans: list[Any], cases: list[dict], inv: Any) -> dict[str, Any]:
    from ml.insider import synth

    blob = json.dumps([s.model_dump() for s in spans], default=str)
    sources = []
    for c in cases:
        for ref in c.get("file_refs", []):
            d = inv.svc.data_uc4.documents.get(ref)
            sources.append(
                SimpleNamespace(
                    content=d.content, filename=d.filename, doc_id=ref, gold_evidence_spans=[]
                )
            )
    for ch in inv.svc.copilot.corpus.chunks:
        sources.append(
            SimpleNamespace(
                content=ch.body, filename="", doc_id=ch.chunk_id, gold_evidence_spans=[]
            )
        )
    res = audit_spans(blob, sources)
    leaks = [list(x) for x in res.leaks]
    needles: dict[str, list[str]] = {
        "user_id": sorted({c["user_id"] for c in cases}),
        "repository": sorted({r for v in synth.EVENT_DETAILS.values() for r in v.get("new_repositories", [])}),
        "destination_host": sorted({v["upload"]["host"] for v in synth.EVENT_DETAILS.values() if "upload" in v}),
        "untrusted_text": [G.INJECTION[:40], G.POISON[:40], G.GUILT_LURE[:40], G.ACTION_LURE[:40], G.LOOP_LURE[:40],
                           "Preparing the conference demo"],
        "canary": [G.CANARY],
    }  # fmt: skip
    checks = 0
    for kind, values in needles.items():
        for v in values:
            checks += 1
            if v in blob:
                leaks.append([kind, v[:30]])
    return {"clean": not leaks, "spans_audited": len(spans), "sources_checked": len(sources),
            "windows_checked": res.windows_checked, "pattern_checks": res.pattern_checks,
            "identifier_checks": checks, "leaks": leaks}  # fmt: skip


def summarise(spans: list[Any]) -> dict[str, Any]:
    roots = [s for s in spans if s.name == "uc2.case"]
    agents = [
        s
        for s in spans
        if s.name
        in ("uc2.orchestrator", "uc2.behavior_agent", "uc2.investigation_agent", "uc2.risk_agent")
    ]
    tools = [s for s in spans if s.name == "uc2.tool"]
    planners = [s for s in spans if s.name == "uc2.agent.planner"]
    names = sorted({s.name for s in spans})

    def count(vals):
        return dict(sorted(Counter(vals).items()))

    return {
        "spans": len(spans), "cases": len(roots),
        "span_names": count(s.name for s in spans),
        "attribute_keys": sorted({k for s in spans for k in s.attributes}),
        "outcomes": count(r.attributes.get("dg.ir.outcome") for r in roots),
        "anomaly_bands": count(r.attributes.get("dg.ir.band") for r in roots),
        "review_reasons": count(c for r in roots for c in r.attributes.get("dg.ir.reason_codes", []) if c.startswith("review:")),
        "agent_stop_reasons": count(f"{s.attributes.get('dg.agent.name')}:{s.attributes.get('dg.agent.stopped_reason')}" for s in agents),
        "tool_calls": count(s.attributes.get("dg.agent.tool") for s in tools),
        "tool_errors": count(s.attributes["dg.agent.tool_error"] for s in tools if "dg.agent.tool_error" in s.attributes),
        "planner_turns": len(planners), "planner_turns_replayed": sum(bool(s.attributes.get("dg.llm.cached")) for s in planners),
        "tokens_in": sum(s.attributes.get("dg.tokens_in", 0) for s in planners),
        "tokens_out": sum(s.attributes.get("dg.tokens_out", 0) for s in planners),
        "guardrail_events": count(t for r in roots for t in r.attributes.get("dg.guardrail.type", [])),
        "genai_operations": count(_genai_attrs(s.name, s.attributes).get("gen_ai.operation.name") for s in spans
                                  if _genai_attrs(s.name, s.attributes)),
        "case_ms_p50": _pct([r.duration_ms for r in roots], 0.5),
        "stage_ms_p50": {n: _pct([s.duration_ms for s in spans if s.name == n], 0.5) for n in names if n.startswith("uc2.")},
    }  # fmt: skip


def render(report: dict[str, Any]) -> str:
    a, t = report["privacy_audit"], report["telemetry"]
    lines = ["# UC2 observability evidence (generated)", "",
             "Generated by `dataguard-insider obs report`: all 36 golden cases, full 4-agent system, REPLAYED from the recorded live run "
             "(run 3), through the PRODUCTION tracer and redactor. Timings are local replay timings.", "",
             "## Privacy audit", "",
             f"* Result: **{'CLEAN' if a['clean'] else 'LEAKS FOUND'}**. {a['spans_audited']} spans against {a['sources_checked']} sources "
             f"(case documents and every policy section): {a['windows_checked']} word windows, {a['pattern_checks']} sensitive-value patterns, "
             f"{a['identifier_checks']} user-id / repository / destination / untrusted-text / canary checks."]  # fmt: skip
    if a["leaks"]:
        lines.append(f"* Leaks: {a['leaks']}")
    lines += [f"* Exported attribute keys ({len(t['attribute_keys'])}): " + ", ".join(f"`{k}`" for k in t["attribute_keys"]), "",
              "## Telemetry", "", "| Signal | Value |", "|---|---|"]  # fmt: skip
    for k in ("cases", "outcomes", "anomaly_bands", "review_reasons", "agent_stop_reasons", "tool_calls", "tool_errors",
              "planner_turns", "planner_turns_replayed", "tokens_in", "tokens_out", "guardrail_events", "genai_operations",
              "case_ms_p50", "stage_ms_p50"):  # fmt: skip
        lines.append(f"| {k} | {str(t[k]).replace('|', '/')} |")
    lines += ["", "## Span names", "", ", ".join(f"`{k}` x{v}" for k, v in t["span_names"].items())]
    return "\n".join(lines) + "\n"
