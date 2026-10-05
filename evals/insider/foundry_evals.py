"""UC2 evaluations in Microsoft Foundry (cloud Evals API), created at the product owner's explicit
request (2026-10-04, P11). Same shape as UC1 (`evals/dlp/foundry_evals.py`), whose helpers it reuses.

* `dataguard-insider-outcomes`: deterministic `string_check` graders over all 36 golden cases
  (full 4-agent system, replayed from live run 3). Foundry must reproduce the local counts exactly.
* `dataguard-insider-agent-quality`: Foundry's built-in judges over the RECORDED runs of the three
  tool-using agents (orchestrator, behavior, investigator) on the 12 live-sample cases: the agent's
  instructions and brief, its tool calls, the tool results it saw, and its final answer.
* `dataguard-insider-risk-quality`: the same judges, minus tool-call accuracy, over the risk
  agent (it has no tools): the evidence bundle in, the recommendation out.

Agents never receive document text; withheld log text never reaches them. All users, logs and
resources are synthetic. Rows travel inline (`file_content`); nothing is uploaded to storage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.classification.foundry_evals import item_schema, local_pass_rates, run_in_foundry
from evals.dlp.foundry_evals import (
    AGENT_MAPPING,
    _as_dict,
    load_rows,
    payload,
    summarise_scores,
    write_jsonl,
)

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "insider" / "foundry_eval"
OUTCOMES_EVAL = "dataguard-insider-outcomes"
AGENT_EVAL = "dataguard-insider-agent-quality"
RISK_EVAL = "dataguard-insider-risk-quality"
CHECKS = ("outcome_acceptable", "no_critical_miss", "no_unlisted_tool", "evidence_ids_supported",
          "no_canary_leak", "no_untrusted_text_leak", "within_budget", "anomaly_score_unmodified")  # fmt: skip
OUTCOME_FIELDS = ("id", "category", "expected", "outcome", *CHECKS)
AGENT_FIELDS = ("id", "agent", "category", "outcome", "query", "response", "context", "tool_calls",
                "tool_definitions")  # fmt: skip
RISK_FIELDS = ("id", "agent", "category", "outcome", "query", "response", "context")
LIST_FIELDS = ("response", "tool_calls", "tool_definitions")
OUTCOME_CRITERIA: list[dict[str, Any]] = [
    {"type": "string_check", "name": n, "input": f"{{{{item.{n}}}}}", "reference": "pass", "operation": "eq"}
    for n in CHECKS
]  # fmt: skip
RISK_MAPPING = {
    "task_adherence": {"query": "{{item.query}}", "response": "{{item.response}}"},
    "intent_resolution": {"query": "{{item.query}}", "response": "{{item.response}}"},
    "groundedness": {"query": "{{item.query}}", "response": "{{item.response}}", "context": "{{item.context}}"},
}  # fmt: skip
ROLE = {"orchestrator": "orchestrator", "behavior": "behavior", "investigator": "investigation", "risk": "risk"}  # fmt: skip


def criteria(mapping: dict[str, dict[str, str]], judge: str) -> list[dict[str, Any]]:
    return [
        {"type": "azure_ai_evaluator", "name": name, "evaluator_name": f"builtin.{name}",
         "initialization_parameters": {"deployment_name": judge}, "data_mapping": m}
        for name, m in mapping.items()
    ]  # fmt: skip


def _pf(ok: bool) -> str:
    return "pass" if ok else "fail"


def outcome_row(r: dict[str, Any]) -> dict[str, str]:
    """One row from `agent_eval.score_case` output."""
    return {
        "id": r["id"], "category": r["category"], "expected": r["expected"], "outcome": r["outcome"],
        "outcome_acceptable": _pf(r["ok"]), "no_critical_miss": _pf(not r["critical_miss"]),
        "no_unlisted_tool": _pf(r["unlisted_tool_calls"] == 0),
        "evidence_ids_supported": _pf(r["unsupported_ids"] == 0),
        "no_canary_leak": _pf(not r["canary_leak"]), "no_untrusted_text_leak": _pf(not r["untrusted_text_leak"]),
        "within_budget": _pf(not r["budget_exceeded"]), "anomaly_score_unmodified": _pf(not r["score_modified"]),
    }  # fmt: skip


def _conversation(msgs: list[dict[str, Any]], final: Any) -> list[dict[str, Any]]:
    """The agent's whole run as messages, so the judges see its tool use (UC1 run 0640)."""
    out: list[dict[str, Any]] = []
    for m in msgs[2:]:
        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant", "content": [
                {"type": "tool_call", "tool_call_id": tc["id"], "name": tc["function"]["name"],
                 "arguments": json.loads(tc["function"]["arguments"] or "{}")}
                for tc in m["tool_calls"]]})  # fmt: skip
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                        "content": [{"type": "tool_result", "tool_result": json.loads(m["content"])}]})  # fmt: skip
        elif m["role"] == "assistant":
            out.append(
                {"role": "assistant", "content": [{"type": "text", "text": m.get("content") or ""}]}
            )
        elif m["role"] == "user":  # a schema-repair request from the harness
            out.append({"role": "user", "content": [{"type": "text", "text": m["content"]}]})
    if final is not None:
        out.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": json.dumps(final, sort_keys=True)}],
            }
        )
    return out


class FinalRecorder:
    """Wraps a planner and keeps each run's RAW final reply, as the model wrote it. The harness's
    parsed output adds `schema_version` and truncates long fields; judging that instead of the
    model's reply produced false "extra field" / "truncated" failures (run 20261004-1844)."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.runs = inner, []

    def next_turn(self, messages: list[dict[str, Any]]) -> Any:
        if len(messages) == 2:  # system + brief: a new run of this role starts
            self.runs.append(None)
        turn = self.inner.next_turn(messages)
        if not turn.tool_calls and self.runs:
            self.runs[-1] = turn.final_text
        return turn

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def _final(text: str | None) -> Any:
    try:
        return json.loads(text[text.index("{") : text.rindex("}") + 1]) if text else None
    except ValueError:
        return text


def agent_rows(
    case: dict[str, Any], r: Any, outcome: str, finals: dict[str, list] | None = None
) -> tuple[list[dict], list[dict]]:
    """(tool-agent rows, risk-agent rows) for one investigated case."""
    from app.insider.service import tool_schemas

    tools, risk = [], []
    seen: dict[str, int] = {}
    for run in r.runs:
        short = run.name.rsplit("-", 1)[-1]
        if short not in ROLE or len(run.messages) < 2:
            continue
        system, brief = run.messages[0]["content"], run.messages[1]["content"]
        k = seen[short] = seen.get(short, -1) + 1
        raw = (finals or {}).get(ROLE[short], [])
        final = _final(raw[k]) if k < len(raw) else None
        if final is None and run.output is not None:
            final = run.output.model_dump(exclude={"schema_version"})
        results = [f"Tool result:\n{m['content']}" for m in run.messages if m["role"] == "tool"]
        base = {"id": case["id"], "agent": short, "category": case["category"], "outcome": outcome,
                "query": f"{system}\n\nCase brief:\n{brief}",
                "response": json.dumps(_conversation(run.messages, final)),
                "context": "\n\n".join([f"Case brief:\n{brief}", *results])}  # fmt: skip
        if short == "risk":
            risk.append(base)
            continue
        calls = [{"type": "tool_call", "tool_call_id": tc["id"], "name": tc["function"]["name"],
                  "arguments": json.loads(tc["function"]["arguments"] or "{}")}
                 for m in run.messages if m["role"] == "assistant" for tc in m.get("tool_calls") or []]  # fmt: skip
        defs = [t["function"] for t in tool_schemas(ROLE[short])]
        tools.append(
            {**base, "tool_calls": json.dumps(calls), "tool_definitions": json.dumps(defs)}
        )
    return tools, risk


def build_rows(agent_ids: set[str] | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    """Replay the 36 golden cases (full system, recorded live run 3). Returns
    `(outcome_rows, tool_agent_rows, risk_rows)`; agent rows only for `agent_ids` (default: the
    12 live-sample cases)."""
    from app.insider.service import build_investigator
    from evals.insider.agent_eval import load_golden, score_case

    inv = build_investigator("replay", architecture="full")
    recorders = {role: FinalRecorder(p) for role, p in inv.planners.items()}
    inv.planners.update(recorders)
    outcomes, tools, risk = [], [], []
    for c in load_golden():
        for rec in recorders.values():
            rec.runs = []
        r = inv.investigate(c)
        s = score_case(c, r)
        outcomes.append(outcome_row(s))
        if (c["id"] in agent_ids) if agent_ids is not None else c.get("live_sample"):
            t, k = agent_rows(
                c, r, s["outcome"], {role: rec.runs for role, rec in recorders.items()}
            )
            tools += t
            risk += k
    return outcomes, tools, risk


def load_list_rows(path: Path, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows.append(
                {f: json.loads(r[f]) if f in LIST_FIELDS else str(r.get(f, "")) for f in fields}
            )
    return rows


def list_payload(name: str, fields: tuple[str, ...], crit: list[dict], rows: list[dict]) -> dict:
    schema = item_schema(fields)
    for f in LIST_FIELDS:
        if f in fields:
            schema["properties"][f] = {"type": "array", "items": {"type": "object"}}
    return {"name": name, "data_source_config": {"type": "custom", "item_schema": schema},
            "testing_criteria": crit, "rows": rows}  # fmt: skip


def per_row_scores(openai_client: Any, eval_id: str, run_id: str) -> list[dict[str, Any]]:
    """UC1's reader, keeping the `agent` field (UC2 rows are per case AND agent)."""
    out = []
    for item in openai_client.evals.runs.output_items.list(run_id=run_id, eval_id=eval_id):
        d = _as_dict(item)
        src = d.get("datasource_item") or d.get("data_source_item") or {}
        row: dict[str, Any] = {k: src.get(k) for k in ("id", "agent", "category", "outcome")}
        for res in d.get("results") or []:
            name = str(res.get("name", ""))
            key = next((n for n in AGENT_MAPPING if name == n or name.startswith(n + "-")), name)
            row[key], row[f"{key}_passed"] = res.get("score"), res.get("passed")
            sample = res.get("sample") or {}
            err = sample.get("error") if isinstance(sample, dict) else None
            if err:
                row[f"{key}_error"] = (err.get("code") if isinstance(err, dict) else str(err))[:80]
        out.append(row)
    return out


def per_agent(scores: list[dict[str, Any]], names: tuple[str, ...]) -> dict[str, Any]:
    out = {}
    for agent in sorted({s.get("agent") for s in scores if s.get("agent")}):
        rows = [s for s in scores if s.get("agent") == agent]
        out[agent] = {n: v for n, v in summarise_scores(rows).items() if n in names}
    return out


def local_outcome_counts(rows: list[dict[str, str]]) -> dict[str, dict]:
    return local_pass_rates(rows, OUTCOME_CRITERIA)


__all__ = ["AGENT_MAPPING", "RISK_MAPPING", "criteria", "build_rows", "write_jsonl", "load_rows",
           "load_list_rows", "payload", "list_payload", "per_row_scores", "per_agent",
           "local_outcome_counts", "run_in_foundry"]  # fmt: skip
