"""Create UC6 evaluations in Microsoft Foundry (cloud Evals API), at the product owner's explicit
request (2026-09-29). Reuses UC4's `run_in_foundry` / `local_pass_rates` unchanged.

* `dataguard-policy-outcomes`: four deterministic `string_check` graders over the 108 outcome rows
  (`data/uc6/foundry_eval/outcomes.jsonl`). Foundry must reproduce the local counts exactly.
* `dataguard-policy-quality`: Foundry's built-in AI-assisted evaluators (groundedness, relevance,
  retrieval) over the 76 answered rows (`answers.jsonl`), with a judge deployment. These rows carry
  the SYNTHETIC questions and policy text; nothing else leaves the process.

Rows travel inline (`file_content`); no dataset is uploaded to project storage.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

from evals.classification.foundry_evals import item_schema, local_pass_rates, run_in_foundry

OUTCOMES_EVAL = "dataguard-policy-outcomes"
QUALITY_EVAL = "dataguard-policy-quality"
OUTCOME_FIELDS = ("id", "item_id", "level", "category", "mode", "expected_status",
                  "actual_status", "status_ok", "no_forbidden_phrase", "no_unverified_shown",
                  "no_fabricated_citation")  # fmt: skip
QUALITY_FIELDS = ("id", "item_id", "level", "category", "query", "response", "context",
                  "ground_truth")  # fmt: skip
OUTCOME_CRITERIA: list[dict[str, Any]] = [
    {"type": "string_check", "name": name, "input": f"{{{{item.{name}}}}}", "reference": "pass",
     "operation": "eq"}
    for name in ("status_ok", "no_forbidden_phrase", "no_unverified_shown", "no_fabricated_citation")
]  # fmt: skip
QUALITY_MAPPING = {
    "groundedness": {"query": "{{item.query}}", "response": "{{item.response}}",
                     "context": "{{item.context}}"},
    "relevance": {"query": "{{item.query}}", "response": "{{item.response}}"},
    "retrieval": {"query": "{{item.query}}", "context": "{{item.context}}"},
}  # fmt: skip


def quality_criteria(judge: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "azure_ai_evaluator",
            "name": name,
            "evaluator_name": f"builtin.{name}",
            "initialization_parameters": {"deployment_name": judge},
            "data_mapping": mapping,
        }
        for name, mapping in QUALITY_MAPPING.items()
    ]


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


def _as_dict(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, list):
        return [_as_dict(x) for x in obj]
    return obj


def per_row_scores(openai_client: Any, eval_id: str, run_id: str) -> list[dict[str, Any]]:
    """Per-row judge scores: `{id, level, category, <criterion>: score, <criterion>_passed}`."""
    out = []
    for item in openai_client.evals.runs.output_items.list(run_id=run_id, eval_id=eval_id):
        d = _as_dict(item)
        src = d.get("datasource_item") or d.get("data_source_item") or {}
        row = {k: src.get(k) for k in ("id", "item_id", "level", "category")}
        for res in d.get("results") or []:
            name = str(res.get("name", ""))
            key = next((n for n in QUALITY_MAPPING if name == n or name.startswith(n + "-")), name)
            row[key] = res.get("score")
            row[f"{key}_passed"] = res.get("passed")
        out.append(row)
    return out


def summarise_scores(rows: list[dict[str, Any]], local: dict[str, dict]) -> dict[str, Any]:
    """Mean judge score per criterion and level, plus the judge-vs-proxy disagreements."""
    summary: dict[str, Any] = {}
    for level in sorted({r["level"] for r in rows if r.get("level")}):
        sub = [r for r in rows if r.get("level") == level]
        summary[level] = {
            name: {
                "mean": round(mean(s), 3) if (s := [r[name] for r in sub if isinstance(r.get(name), int | float)]) else None,
                "n_scored": len(s),
                "passed": sum(1 for r in sub if r.get(f"{name}_passed") is True),
            }
            for name in QUALITY_MAPPING
        }  # fmt: skip
    disagreements = [
        {"id": r["id"], "groundedness": r.get("groundedness")}
        for r in rows
        if local.get(r.get("id"), {}).get("local_all_claims_verified")
        and isinstance(r.get("groundedness"), int | float)
        and r["groundedness"] < 4
    ]
    return {"by_level": summary, "verified_but_judged_ungrounded": disagreements}


def local_outcome_counts(rows: list[dict[str, str]]) -> dict[str, dict]:
    return local_pass_rates(rows, OUTCOME_CRITERIA)


__all__ = ["run_in_foundry", "payload", "load_rows", "per_row_scores", "summarise_scores",
           "local_outcome_counts", "quality_criteria", "OUTCOME_CRITERIA", "OUTCOME_FIELDS",
           "QUALITY_FIELDS", "OUTCOMES_EVAL", "QUALITY_EVAL"]  # fmt: skip
