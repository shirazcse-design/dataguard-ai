"""UC1 evaluations in Microsoft Foundry (cloud Evals API), created at the product owner's explicit
request (2026-10-02). Reuses UC4's `run_in_foundry` / `local_pass_rates` unchanged.

* `dataguard-dlp-outcomes`: deterministic `string_check` graders over the 30 golden cases
  (`data/dlp/foundry_eval/outcomes.jsonl`). Foundry must reproduce the local counts exactly.
* `dataguard-dlp-agent-quality`: Foundry's built-in AI-assisted agent evaluators over the agent's
  RECORDED investigations (`agent.jsonl`): what it was told (instructions + evidence pack), the
  tool calls it made, the tool results it saw, and its final proposal. The evidence pack never
  holds document text; the rows are synthetic (fictional company, users and policies).

Rows travel inline (`file_content`); no dataset is uploaded to project storage.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

from evals.classification.foundry_evals import item_schema, local_pass_rates, run_in_foundry

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "dlp" / "foundry_eval"
OUTCOMES_EVAL = "dataguard-dlp-outcomes"
AGENT_EVAL = "dataguard-dlp-agent-quality"
CHECKS = ("outcome_acceptable", "no_critical_false_negative", "no_unlisted_tool",
          "required_tools_used", "safe_termination", "findings_cite_known_evidence")  # fmt: skip
OUTCOME_FIELDS = ("id", "category", "expected", "outcome", *CHECKS)
AGENT_FIELDS = ("id", "category", "outcome", "query", "response", "context", "tool_calls",
                "tool_definitions")  # fmt: skip
OUTCOME_CRITERIA: list[dict[str, Any]] = [
    {"type": "string_check", "name": n, "input": f"{{{{item.{n}}}}}", "reference": "pass",
     "operation": "eq"}
    for n in CHECKS
]  # fmt: skip
AGENT_MAPPING = {
    "task_adherence": {"query": "{{item.query}}", "response": "{{item.response}}",
                       "tool_definitions": "{{item.tool_definitions}}"},
    "intent_resolution": {"query": "{{item.query}}", "response": "{{item.response}}",
                          "tool_definitions": "{{item.tool_definitions}}"},
    "groundedness": {"query": "{{item.query}}", "response": "{{item.response}}",
                     "context": "{{item.context}}",
                     "tool_definitions": "{{item.tool_definitions}}"},
    "tool_call_accuracy": {"query": "{{item.query}}", "response": "{{item.response}}",
                           "tool_calls": "{{item.tool_calls}}",
                           "tool_definitions": "{{item.tool_definitions}}"},
}  # fmt: skip
# Foundry validates that every field an evaluator supports is mapped when the row has it (the first
# run, 2026-10-02 06:04, failed EvalValidationFailed / MissingRequiredDataMapping for three).


def agent_criteria(judge: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "azure_ai_evaluator",
            "name": name,
            "evaluator_name": f"builtin.{name}",
            "initialization_parameters": {"deployment_name": judge},
            "data_mapping": mapping,
        }
        for name, mapping in AGENT_MAPPING.items()
    ]


class _Recorder:
    """Wraps a planner and keeps the last message list it was given (the full conversation)."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.messages = inner, []

    def next_turn(self, messages: list[dict[str, Any]]) -> Any:
        self.messages = [dict(m) for m in messages]
        turn = self.inner.next_turn(messages)
        self.last_final = turn.final_text
        return turn

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def _pf(ok: bool) -> str:
    return "pass" if ok else "fail"


def build_rows(mode: str = "replay") -> tuple[list[dict], list[dict], str]:
    """Replay the golden set and return `(outcome_rows, agent_rows, golden_sha)`."""
    from app.dlp.agent import tool_schemas
    from app.dlp.service import build_investigator
    from evals.dlp.golden import load_golden
    from evals.dlp.metrics import score_case

    cases, sha = load_golden()
    inv = build_investigator(mode)
    recorder = _Recorder(inv.agent.planner)
    inv.agent.planner = recorder
    tool_defs = json.dumps([t["function"] for t in tool_schemas()])
    outcomes, agent_rows = [], []
    for c in cases:
        recorder.messages, recorder.last_final = [], None
        result = inv.investigate(c.event)
        r = score_case(c, result)
        stopped_early = r["agent_stopped"] not in ("final_answer", "not_run")
        outcomes.append({
            "id": c.id, "category": c.category, "expected": c.expected_outcome,
            "outcome": r["outcome"],
            "outcome_acceptable": _pf(r["ok"]),
            "no_critical_false_negative": _pf(not r["critical_false_negative"]),
            "no_unlisted_tool": _pf(r["unlisted_tool_calls"] == 0),
            "required_tools_used": _pf(not r["required_tools_missing"]),
            "safe_termination": _pf(not (stopped_early and r["outcome"] == "ALLOW")),
            "findings_cite_known_evidence": _pf(r["unsupported_findings"] == 0),
        })  # fmt: skip
        msgs = recorder.messages
        if not msgs or recorder.last_final is None:
            continue  # the agent did not run or did not answer; covered by the outcome checks
        system = next((m["content"] for m in msgs if m["role"] == "system"), "")
        pack = next((m["content"] for m in msgs if m["role"] == "user"), "")
        calls = [
            {"type": "tool_call", "tool_call_id": tc["id"], "name": tc["function"]["name"],
             "arguments": json.loads(tc["function"]["arguments"] or "{}")}
            for m in msgs if m["role"] == "assistant" for tc in m.get("tool_calls") or []
        ]  # fmt: skip
        results = [f"Tool result:\n{m['content']}" for m in msgs if m["role"] == "tool"]
        conversation = []  # the agent's whole run, so the judges see its tool use (run 0640 fix)
        for m in msgs[2:]:
            if m["role"] == "assistant":
                conversation.append({"role": "assistant", "content": [
                    {"type": "tool_call", "tool_call_id": tc["id"], "name": tc["function"]["name"],
                     "arguments": json.loads(tc["function"]["arguments"] or "{}")}
                    for tc in m.get("tool_calls") or []]})  # fmt: skip
            elif m["role"] == "tool":
                result = {"type": "tool_result", "tool_result": json.loads(m["content"])}
                conversation.append(
                    {"role": "tool", "tool_call_id": m["tool_call_id"], "content": [result]}
                )
        conversation.append(
            {"role": "assistant", "content": [{"type": "text", "text": recorder.last_final}]}
        )
        agent_rows.append({
            "id": c.id, "category": c.category, "outcome": r["outcome"],
            "query": f"{system}\n\nEvidence pack:\n{pack}",
            "response": json.dumps(conversation),
            "context": "\n\n".join([f"Evidence pack:\n{pack}", *results]),
            "tool_calls": json.dumps(calls),
            "tool_definitions": tool_defs,
        })  # fmt: skip
    return outcomes, agent_rows, sha


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def load_rows(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows.append({f: str(r.get(f, "")) for f in fields})
    return rows


def payload(name: str, fields: tuple[str, ...], criteria: list[dict], rows: list[dict]) -> dict:
    return {
        "name": name,
        "data_source_config": {"type": "custom", "item_schema": item_schema(fields)},
        "testing_criteria": criteria,
        "rows": rows,
    }


# The evaluators require real lists (run 0608). `response` is the agent's message list, so that
# task adherence sees the tool calls, not only the final JSON (run 0640).
LIST_FIELDS = ("response", "tool_calls", "tool_definitions")


def load_agent_rows(path: Path) -> list[dict[str, Any]]:
    """Agent rows with `tool_calls` / `tool_definitions` as lists, everything else as strings."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows.append({f: json.loads(r[f]) if f in LIST_FIELDS else str(r.get(f, ""))
                         for f in AGENT_FIELDS})  # fmt: skip
    return rows


def agent_payload(criteria: list[dict], rows: list[dict]) -> dict:
    schema = item_schema(AGENT_FIELDS)
    for f in LIST_FIELDS:
        schema["properties"][f] = {"type": "array", "items": {"type": "object"}}
    return {
        "name": AGENT_EVAL,
        "data_source_config": {"type": "custom", "item_schema": schema},
        "testing_criteria": criteria,
        "rows": rows,
    }


def _as_dict(obj: Any) -> Any:
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def per_row_scores(openai_client: Any, eval_id: str, run_id: str) -> list[dict[str, Any]]:
    out = []
    for item in openai_client.evals.runs.output_items.list(run_id=run_id, eval_id=eval_id):
        d = _as_dict(item)
        src = d.get("datasource_item") or d.get("data_source_item") or {}
        row: dict[str, Any] = {k: src.get(k) for k in ("id", "category", "outcome")}
        for res in d.get("results") or []:
            name = str(res.get("name", ""))
            key = next((n for n in AGENT_MAPPING if name == n or name.startswith(n + "-")), name)
            row[key] = res.get("score")
            row[f"{key}_passed"] = res.get("passed")
            sample = res.get("sample") or {}
            err = sample.get("error") if isinstance(sample, dict) else None
            if err:
                row[f"{key}_error"] = (err.get("code") if isinstance(err, dict) else str(err))[:80]
        out.append(row)
    return out


def summarise_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for name in AGENT_MAPPING:
        scores = [r[name] for r in rows if isinstance(r.get(name), int | float)]
        out[name] = {
            "mean": round(mean(scores), 3) if scores else None,
            "n_scored": len(scores),
            "passed": sum(1 for r in rows if r.get(f"{name}_passed") is True),
            "failed": sum(1 for r in rows if r.get(f"{name}_passed") is False),
            "errors": sum(1 for r in rows if r.get(f"{name}_error")),
            "low_rows": sorted(r["id"] for r in rows if r.get(f"{name}_passed") is False),
        }
    return out


def local_outcome_counts(rows: list[dict[str, str]]) -> dict[str, dict]:
    return local_pass_rates(rows, OUTCOME_CRITERIA)


__all__ = ["run_in_foundry", "build_rows", "write_jsonl", "load_rows", "payload", "agent_criteria",
           "per_row_scores", "summarise_scores", "local_outcome_counts", "OUTCOME_CRITERIA",
           "OUTCOME_FIELDS", "AGENT_FIELDS", "OUTCOMES_EVAL", "AGENT_EVAL", "DATA"]  # fmt: skip
