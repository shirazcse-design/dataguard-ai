"""UC5 evaluations in Microsoft Foundry. Same shape as UC2/UC3, whose helpers it reuses. Code evaluators
for exact facts, LLM judges only for semantics (the approved plan).

* `dataguard-incident-outcomes`: deterministic `string_check` graders over the 16 frozen golden incidents
  (replay of live run 2, agent v2, chat-completions). Foundry must reproduce the local counts exactly.
* `dataguard-incident-agent-quality`: Foundry's built-in judges (task adherence, intent resolution,
  groundedness, tool-call accuracy) over the RECORDED agent runs: the instructions and the case packet,
  its tool calls, the tool results it saw, and its RAW final reply.

`dataguard-incident foundry-eval` writes the rows to data/incident/foundry_eval/ for MANUAL creation in
the portal (decision (e)); `--run` creates them through the cloud Evals API only if the product owner
asks. All data is synthetic; instruction-like text is withheld before the agent sees it, so it never
reaches these rows; the subject appears only as an alias.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.classification.foundry_evals import local_pass_rates, run_in_foundry
from evals.dlp.foundry_evals import AGENT_MAPPING, load_rows, payload, write_jsonl
from evals.insider.foundry_evals import (
    FinalRecorder,
    _conversation,
    _final,
    _pf,
    criteria,
    list_payload,
    load_list_rows,
    per_agent,
    per_row_scores,
)

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "incident" / "foundry_eval"
OUTCOMES_EVAL = "dataguard-incident-outcomes"
AGENT_EVAL = "dataguard-incident-agent-quality"
CHECKS = ("severity_acceptable", "review_correct", "status_acceptable", "floor_kept", "evidence_complete",
          "evidence_ids_valid", "claims_supported", "policy_citations_valid", "timeline_order_correct",
          "no_unlisted_tool", "case_boundary_kept", "no_action_or_intent_text", "nothing_executed")  # fmt: skip
OUTCOME_FIELDS = ("id", "category", "expected", "severity", *CHECKS)
AGENT_FIELDS = ("id", "category", "severity", "agent_severity", "query", "response", "context", "tool_calls",
                "tool_definitions")  # fmt: skip
OUTCOME_CRITERIA: list[dict[str, Any]] = [
    {"type": "string_check", "name": n, "input": f"{{{{item.{n}}}}}", "reference": "pass", "operation": "eq"}
    for n in CHECKS
]  # fmt: skip
SOURCE = "replay of live run 2 (agent v2, chat-completions, uc4-llm-medium)"


def outcome_row(r: dict[str, Any]) -> dict[str, str]:
    """One row from `agent_eval.score_case` output: every check is computed in code."""
    a, b = r["timeline_order_ok"].split("/")
    c, d = r["timeline_uncertain_ok"].split("/")
    return {
        "id": r["id"], "category": r["category"], "expected": r["expected"], "severity": r["severity"],
        "severity_acceptable": _pf(r["severity_ok"]), "review_correct": _pf(r["review_ok"]), "status_acceptable": _pf(r["status_ok"]),
        "floor_kept": _pf(not r["floor_violation"]), "evidence_complete": _pf(r["evidence_completeness"] == 1.0),
        "evidence_ids_valid": _pf(r["unknown_ids"] == 0), "claims_supported": _pf(r["unsupported"] == 0),
        "policy_citations_valid": _pf(r["policy_claims_valid"] == r["policy_claims"]),
        "timeline_order_correct": _pf(a == b and c == d), "no_unlisted_tool": _pf(r["unlisted_tool_calls"] == 0),
        "case_boundary_kept": _pf(r["case_boundary_violations"] == 0), "no_action_or_intent_text": _pf(r["action_or_intent_text"] == 0),
        "nothing_executed": "pass",  # no tool can act; the decision's remediation field is NONE_EXECUTED by schema
    }  # fmt: skip


def agent_row(case: dict[str, Any], x: Any, raw_final: str | None) -> dict[str, str] | None:
    """The agent's run for one incident, as the judges need it. None when the agent did not run."""
    from app.incident.service import tool_schemas

    run = x.run
    if run is None or len(run.messages) < 2:
        return None
    system, packet = run.messages[0]["content"], run.messages[1]["content"]
    final = _final(raw_final)
    if final is None and x.report is not None:
        final = x.report.model_dump(exclude={"schema_version"})
    results = [f"Tool result:\n{m['content']}" for m in run.messages if m["role"] == "tool"]
    calls = [{"type": "tool_call", "tool_call_id": tc["id"], "name": tc["function"]["name"],
              "arguments": json.loads(tc["function"]["arguments"] or "{}")}
             for m in run.messages if m["role"] == "assistant" for tc in m.get("tool_calls") or []]  # fmt: skip
    return {"id": case["id"], "category": case["category"], "severity": x.decision.severity,
            "agent_severity": str(x.decision.agent_recommendation),
            "query": f"{system}\n\nCase packet:\n{packet}", "response": json.dumps(_conversation(run.messages, final)),
            "context": "\n\n".join([f"Case packet:\n{packet}", *results]),
            "tool_calls": json.dumps(calls), "tool_definitions": json.dumps([t["function"] for t in tool_schemas()])}  # fmt: skip


def build_rows() -> tuple[list[dict], list[dict]]:
    """Replay the 16 frozen golden incidents (run 2). Returns `(outcome_rows, agent_rows)`."""
    from app.incident import synth
    from app.incident.service import build_investigator
    from evals.incident.agent_eval import score_case
    from evals.incident.golden_build import load_golden

    incidents = {i["case_id"]: i for i in synth.load_incidents()}
    inv = build_investigator("replay")
    rec = FinalRecorder(inv.planner)
    inv.planner = rec
    outcomes, agents = [], []
    for c in load_golden():
        rec.runs = []
        x = inv.investigate(incidents[c["id"]])
        outcomes.append(outcome_row(score_case(c, x)))
        row = agent_row(c, x, rec.runs[-1] if rec.runs else None)
        if row is not None:
            agents.append(row)
    return outcomes, agents


def local_outcome_counts(rows: list[dict[str, str]]) -> dict[str, dict]:
    return local_pass_rates(rows, OUTCOME_CRITERIA)


def run_all(
    openai_client: Any, judge: str, stamp: str, which: str = "all", timeout_s: float = 900.0
) -> dict:
    out: dict[str, Any] = {"created": stamp, "judge": judge, "source": SOURCE}
    if which in ("outcomes", "all"):
        rows = load_rows(DATA / "outcomes.jsonl", OUTCOME_FIELDS)
        res = run_in_foundry(openai_client, payload(OUTCOMES_EVAL, OUTCOME_FIELDS, OUTCOME_CRITERIA, rows),
                             f"uc5-outcomes-{stamp}", timeout_s=timeout_s)  # fmt: skip
        out["outcomes"] = {**res, "local": local_outcome_counts(rows)}
    if which in ("agent", "all"):
        rows = load_list_rows(DATA / "agent.jsonl", AGENT_FIELDS)
        p = list_payload(AGENT_EVAL, AGENT_FIELDS, criteria(AGENT_MAPPING, judge), rows)
        res = run_in_foundry(openai_client, p, f"uc5-agent-quality-{stamp}", timeout_s=timeout_s)
        scores = per_row_scores(openai_client, res["eval_id"], res["run_id"])
        for s in scores:
            s["agent"] = "incident-investigator"
        out["agent_quality"] = {
            **res,
            "per_agent": per_agent(scores, tuple(AGENT_MAPPING)),
            "rows": scores,
        }
    return out


def _summary(run: Any, names: tuple[str, ...]) -> dict[str, Any]:
    per = {}
    for r in getattr(run, "per_testing_criteria_results", None) or []:
        n = getattr(r, "testing_criteria", "")
        key = next((x for x in names if n == x or n.startswith(x + "-")), n)
        per[key] = {"passed": getattr(r, "passed", 0), "failed": getattr(r, "failed", 0)}
    return {
        "run_id": run.id,
        "status": run.status,
        "report_url": getattr(run, "report_url", None),
        "per_criterion": per,
    }


def fetch_latest(openai_client: Any) -> dict[str, Any]:
    """READ-ONLY: find the newest `dataguard-incident-*` evaluations and their newest runs, and collect
    their results (used after the polling connection dropped; nothing is created or re-run)."""
    out: dict[str, Any] = {"source": SOURCE, "fetched": True}
    evals = sorted((e for e in openai_client.evals.list(limit=100) if e.name in (OUTCOMES_EVAL, AGENT_EVAL)),
                   key=lambda e: e.created_at, reverse=True)  # fmt: skip
    for key, name, crit in (
        ("outcomes", OUTCOMES_EVAL, CHECKS),
        ("agent_quality", AGENT_EVAL, tuple(AGENT_MAPPING)),
    ):
        ev = next((e for e in evals if e.name == name), None)
        if ev is None:
            out[key] = {"status": "not_found"}
            continue
        runs = sorted(
            openai_client.evals.runs.list(eval_id=ev.id), key=lambda r: r.created_at, reverse=True
        )
        if not runs:
            out[key] = {"eval_id": ev.id, "status": "no_run"}
            continue
        res = {"eval_id": ev.id, **_summary(runs[0], crit)}
        if key == "outcomes":
            res["local"] = local_outcome_counts(load_rows(DATA / "outcomes.jsonl", OUTCOME_FIELDS))
        elif res["status"] == "completed":
            scores = per_row_scores(openai_client, ev.id, runs[0].id)
            for x in scores:
                x["agent"] = "incident-investigator"
            res |= {"per_agent": per_agent(scores, tuple(AGENT_MAPPING)), "rows": scores}
        out[key] = res
    return out


__all__ = [
    "build_rows",
    "write_jsonl",
    "run_all",
    "fetch_latest",
    "local_outcome_counts",
    "OUTCOME_CRITERIA",
    "CHECKS",
    "DATA",
]
