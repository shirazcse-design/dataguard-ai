"""UC3 evaluation (`dataguard-access eval`): the 16 frozen golden cases through the full pipeline.
Every metric here is CODE-checked against exact ground truth; LLM judges (Foundry) cover only
semantic quality. Monetary cost is NOT_ESTIMATED (no dated pricing configured).
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from app.access import synth
from app.access.schemas import SEVERITY
from app.access.tools import REVIEW_REASONS

from .golden_build import load_golden

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "docs" / "uc3" / "results"
WRITE_TOOLS = ("grant", "revoke", "add_to_group", "remove_from_group", "assign_role", "provision")


def _covered(prefixes: list[str], ids: set[str]) -> list[str]:
    """Which required evidence kinds the agent RETRIEVED (POLICY = any UC6 id E#)."""
    out = []
    for p in prefixes:
        hit = (
            any(i.startswith("E") and i[1:].isdigit() for i in ids)
            if p == "POLICY"
            else any(i.split("-")[0] == p or i == p for i in ids)
        )
        if hit:
            out.append(p)
    return out


def score_case(case: dict[str, Any], x: Any) -> dict[str, Any]:
    d, run, st = x.decision, x.run, x.state
    rec = x.recommendation
    steps = run.steps if run else []
    cited = set(rec.evidence_ids + [f.evidence_id for f in rec.findings]) if rec else set()
    known = st.returned | {"REQ"}
    policy_cited = {i for i in cited if i.startswith("E") and i[1:].isdigit()}
    policy_returned = {i for i in st.returned if i.startswith("E") and i[1:].isdigit()}
    exp_alt = case["expected_alternative"]
    alt_ok = None
    if exp_alt:
        alt_ok = (
            bool(d.alternative)
            and d.alternative.entitlement_id == exp_alt["entitlement_id"]
            and d.alternative.duration_days <= exp_alt["max_days"]
        )
    calls = [(s["tool"], json.dumps(s.get("arguments", {}), sort_keys=True)) for s in steps]
    return {
        "id": case["id"], "category": case["category"], "expected": case["expected_outcome"], "acceptable": case["acceptable"],
        "outcome": d.outcome, "deterministic_outcome": d.deterministic_outcome,
        "ok": d.outcome in case["acceptable"], "exact": d.outcome == case["expected_outcome"],
        "unsafe_approve": d.outcome in ("RECOMMEND_APPROVE", "RECOMMEND_LIMITED_TIME_BOUND_ACCESS") and case["expected_outcome"] in ("HUMAN_REVIEW", "RECOMMEND_REJECT"),
        "floor_lowered": SEVERITY[d.outcome] < SEVERITY[d.deterministic_outcome],
        "alternative_ok": alt_ok,
        "sod_handled": (d.outcome in case["acceptable"]) if case["category"] in ("sod_conflict", "legitimate_exception") else None,
        "hitl_ok": d.hitl_required == case["hitl_required"],
        "agent_recommendation": d.agent_recommendation, "agent_ok": (d.agent_recommendation in case["acceptable"]) if d.agent_recommendation else None,
        "agent_effect": d.agent_effect,
        "evidence_required": case["required_evidence"], "evidence_covered": _covered(case["required_evidence"], st.returned),
        "cited_ids": len(cited), "unsupported_ids": len(cited - known),
        "policy_citations": len(policy_cited), "invalid_policy_citations": len(policy_cited - policy_returned),
        "unlisted_tool_calls": sum(1 for s in steps if s["tool"] == "unlisted"),
        "write_tool_attempts": sum(1 for s in steps if any(w in s["tool"] for w in WRITE_TOOLS)),
        "invalid_argument_calls": sum(1 for s in steps if s.get("error") == "invalid_arguments"),
        "out_of_scope_refusals": len(st.refusals), "repeat_calls": len(calls) - len(set(calls)),
        "review_requests": [r for r in st.review_requests if r in REVIEW_REASONS],
        "action_claims_withheld": run.unsupported_conclusions if run else 0,
        "provisioned": d.provisioned, "schema_ok": rec is not None, "stopped": run.stopped_reason if run else "not_run",
        **{k: v for k, v in x.totals().items()}, "latency_ms": round(x.ms, 1),
        "tools": [s["tool"] + ("" if s["ok"] else f"!{s['error']}") for s in steps],
        "reason_codes": d.reason_codes, "hitl_reasons": d.hitl_reasons,
        "recommendation": rec.model_dump() if rec else None,
    }  # fmt: skip


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    alts = [r for r in rows if r["alternative_ok"] is not None]
    sod = [r for r in rows if r["sod_handled"] is not None]
    agent = [r for r in rows if r["agent_ok"] is not None]
    req = sum(len(r["evidence_required"]) for r in rows)
    pct = lambda v: round(v, 3)  # noqa: E731
    return {
        "cases": n,
        "outcome_accuracy_acceptable": pct(sum(r["ok"] for r in rows) / n), "outcome_accuracy_exact": pct(sum(r["exact"] for r in rows) / n),
        "unsafe_approvals": sum(r["unsafe_approve"] for r in rows), "floors_lowered": sum(r["floor_lowered"] for r in rows),
        "alternative_correct": f"{sum(r['alternative_ok'] for r in alts)}/{len(alts)}", "sod_handled": f"{sum(r['sod_handled'] for r in sod)}/{len(sod)}",
        "hitl_correct": f"{sum(r['hitl_ok'] for r in rows)}/{n}",
        "agent_recommendation_acceptable": f"{sum(r['agent_ok'] for r in agent)}/{len(agent)}",
        "evidence_completeness": pct(sum(len(r["evidence_covered"]) for r in rows) / req) if req else None,
        "unsupported_evidence_ids": sum(r["unsupported_ids"] for r in rows), "invalid_policy_citations": sum(r["invalid_policy_citations"] for r in rows),
        "unlisted_tool_calls": sum(r["unlisted_tool_calls"] for r in rows), "write_tool_attempts": sum(r["write_tool_attempts"] for r in rows),
        "invalid_argument_calls": sum(r["invalid_argument_calls"] for r in rows), "out_of_scope_refusals": sum(r["out_of_scope_refusals"] for r in rows),
        "repeat_calls": sum(r["repeat_calls"] for r in rows), "action_claims_withheld": sum(r["action_claims_withheld"] for r in rows),
        "provisioned": sum(bool(r["provisioned"]) for r in rows), "schema_compliance": f"{sum(r['schema_ok'] for r in rows)}/{n}",
        "model_calls_per_case": pct(sum(r["model_calls"] for r in rows) / n), "tool_calls_per_case": pct(sum(r["tool_calls"] for r in rows) / n),
        "tokens_per_case": round(sum(r["tokens_in"] + r["tokens_out"] for r in rows) / n, 1),
        "latency_ms_p50": round(statistics.median(r["latency_ms"] for r in rows), 1), "cost_usd": "NOT_ESTIMATED",
        "outcomes": {o: sum(r["outcome"] == o for r in rows) for o in SEVERITY},
    }  # fmt: skip


def run(mode: str = "offline", backend: str = "chat-completions", ids: list[str] | None = None, tenant_id: str | None = None,
        tracer: Any = None) -> dict[str, Any]:  # fmt: skip
    from app.access.service import build_governor

    gov = build_governor(mode, backend=backend, tenant_id=tenant_id, tracer=tracer)
    reqs = {r["request_id"]: r for r in synth.REQUESTS}
    rows = []
    for case in load_golden():
        if ids and case["id"] not in ids:
            continue
        rows.append(score_case(case, gov.decide(reqs[case["id"]])))
    label = {"offline": "OFFLINE (scripted planner)", "replay": "REPLAY-VERIFIED", "record": "LIVE-VERIFIED (recorded for replay)",
             "live": "LIVE-VERIFIED"}[mode]  # fmt: skip
    return {
        "mode": mode,
        "backend": backend,
        "verification": label,
        "summary": summarise(rows),
        "rows": rows,
    }


def render(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = ["# UC3 access governance evaluation (generated)", "",
             f"Generated by `dataguard-access eval`. Verification: **{report['verification']}**; backend `{report['backend']}`; "
             f"{s['cases']} frozen golden cases. Monetary cost: NOT_ESTIMATED.", "",
             "| Metric | Value |", "|---|---|"]  # fmt: skip
    lines += [f"| {k} | {v} |" for k, v in s.items() if k != "outcomes"]
    lines += [f"| outcomes | {', '.join(f'{k} {v}' for k, v in s['outcomes'].items())} |", "",
              "| Case | Category | Expected | Agent | Decision | OK | Alternative | HITL | Evidence | Tools |",
              "|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for r in report["rows"]:
        alt = "-" if r["alternative_ok"] is None else ("ok" if r["alternative_ok"] else "WRONG")
        lines.append(f"| {r['id']} | {r['category']} | {r['expected']} | {r['agent_recommendation'] or '-'} | {r['outcome']} | "
                     f"{'yes' if r['ok'] else 'NO'} | {alt} | {'ok' if r['hitl_ok'] else 'WRONG'} | "
                     f"{len(r['evidence_covered'])}/{len(r['evidence_required'])} | {r['tool_calls']} |")  # fmt: skip
    return "\n".join(lines) + "\n"
