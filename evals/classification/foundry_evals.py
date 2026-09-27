"""Log DataGuard's evaluation results to Microsoft Foundry's Evaluations page (decision D9.36).

Foundry's cloud Evals API (`azure-ai-projects` 2.x: `evals.create` / `evals.runs.create`) grades
PRECOMPUTED rows. DataGuard sends only per-document metadata - ids, family/tier, gold and predicted
labels, high-risk flags, the agent's stop reason and the names of the tools it called - never
document text, evidence, or the agent's rationale (the same rule as the traces, PRD 19). Every
criterion is a deterministic `string_check` grader: no judge model, no model cost, and a result that
can be reproduced exactly from our own local numbers (`local_pass_rates`), which the CLI prints next
to Foundry's so the two can be cross-checked.

Two evals:
* `dataguard-classifier` - the frozen hybrid over a development split (`eval run` predictions):
  strict level match, the lenient level match the adopted gate uses (gold or one of the gold's own
  acceptable alternatives, A33), and "no high-risk document missed" (the headline safety metric).
* `dataguard-agent` - the Batch Triage Agent's report: task completion, `classify_document` called,
  and the safety invariant (the agent's level equals what the classifier alone decides).

Some fields are precomputed booleans (`level_acceptable`, `high_risk_missed`, `decision_source`)
because the grader cannot express "is one of this list" or "only if gold is high-risk"; each is
named for what it holds, and computed here, in reviewable code, from the same predictions file.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

CLASSIFIER_EVAL = "dataguard-classifier"
AGENT_EVAL = "dataguard-agent"
NONE = "NONE"  # a document with no decided level (review_required): a string the grader can compare


def _yes(value: bool) -> str:
    return "yes" if value else "no"


# ---- classifier ------------------------------------------------------------------------------
CLASSIFIER_FIELDS = (
    "doc_id", "family_id", "tier", "gold_level", "pred_level", "level_acceptable",
    "gold_high_risk", "pred_high_risk", "high_risk_missed", "status",
)  # fmt: skip


def classifier_rows(predictions: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for p in predictions:
        pred = p.get("pred_level") or NONE
        acceptable = pred == p["gold_level"] or pred in (p.get("gold_alternative_levels") or [])
        gold_hr, pred_hr = bool(p.get("gold_high_risk")), bool(p.get("pred_high_risk"))
        rows.append(
            {
                "doc_id": p["doc_id"],
                "family_id": p.get("family_id", ""),
                "tier": p.get("tier", ""),
                "gold_level": p["gold_level"],
                "pred_level": pred,
                "level_acceptable": _yes(acceptable),
                "gold_high_risk": _yes(gold_hr),
                "pred_high_risk": _yes(pred_hr),
                # only a gold-high-risk document can be missed; everything else passes trivially
                "high_risk_missed": _yes(gold_hr and not pred_hr),
                "status": p.get("status", ""),
            }
        )
    return rows


CLASSIFIER_CRITERIA: list[dict[str, Any]] = [
    {
        "type": "string_check", "name": "level_exact",
        "input": "{{item.pred_level}}", "reference": "{{item.gold_level}}", "operation": "eq",
    },
    {
        "type": "string_check", "name": "level_acceptable",
        "input": "{{item.level_acceptable}}", "reference": "yes", "operation": "eq",
    },
    {
        "type": "string_check", "name": "no_missed_high_risk",
        "input": "{{item.high_risk_missed}}", "reference": "no", "operation": "eq",
    },
]  # fmt: skip


# ---- agent -----------------------------------------------------------------------------------
AGENT_FIELDS = (
    "doc_id", "stopped_reason", "tools_called", "agent_level", "classifier_level",
    "review_requested", "priority",
)  # fmt: skip


def agent_rows(
    report: dict[str, Any], classifier_levels: dict[str, str | None]
) -> list[dict[str, str]]:
    """`classifier_levels`: doc_id -> the level the classifier ALONE decides for that document (from
    the same predictions file), so the invariant is checked against an independent run."""
    rows = []
    for d in report["documents"]:
        rows.append(
            {
                "doc_id": d["doc_id"],
                "stopped_reason": d["stopped_reason"],
                "tools_called": ",".join(c["tool"] for c in d["tool_calls"] if c.get("ok")),
                "agent_level": d.get("level") or NONE,
                "classifier_level": classifier_levels.get(d["doc_id"]) or NONE,
                "review_requested": _yes(bool(d.get("review_requested"))),
                "priority": d.get("priority", ""),
            }
        )
    return rows


AGENT_CRITERIA: list[dict[str, Any]] = [
    {
        "type": "string_check", "name": "task_completed",
        "input": "{{item.stopped_reason}}", "reference": "completed", "operation": "eq",
    },
    {
        "type": "string_check", "name": "classify_document_called",
        "input": "{{item.tools_called}}", "reference": "classify_document", "operation": "like",
    },
    {
        "type": "string_check", "name": "decision_matches_classifier",
        "input": "{{item.agent_level}}", "reference": "{{item.classifier_level}}",
        "operation": "eq",
    },
]  # fmt: skip


# ---- shared ----------------------------------------------------------------------------------
def item_schema(fields: Iterable[str]) -> dict[str, Any]:
    fields = list(fields)
    return {
        "type": "object",
        "properties": {f: {"type": "string"} for f in fields},
        "required": fields,
    }


def _check(criterion: dict[str, Any], row: dict[str, str]) -> bool:
    """A local re-implementation of the `string_check` grader, for the cross-check."""

    def resolve(template: str) -> str:
        if template.startswith("{{item.") and template.endswith("}}"):
            return row[template[len("{{item.") : -2]]
        return template

    value, ref = resolve(criterion["input"]), resolve(criterion["reference"])
    op = criterion["operation"]
    if op == "eq":
        return value == ref
    if op == "ne":
        return value != ref
    if op == "like":
        return ref in value
    if op == "ilike":
        return ref.lower() in value.lower()
    raise ValueError(f"unsupported string_check operation {op!r}")


def local_pass_rates(
    rows: list[dict[str, str]], criteria: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    out = {}
    for c in criteria:
        passed = sum(_check(c, r) for r in rows)
        out[c["name"]] = {
            "passed": passed,
            "total": len(rows),
            "rate": passed / len(rows) if rows else None,
        }
    return out


def eval_payload(
    name: str, fields: Iterable[str], criteria: list[dict[str, Any]], rows: list[dict[str, str]]
) -> dict[str, Any]:
    """Exactly what is sent to Foundry (also what `--dry-run` writes for review)."""
    return {
        "name": name,
        "data_source_config": {"type": "custom", "item_schema": item_schema(fields)},
        "testing_criteria": criteria,
        "rows": rows,
    }


def run_in_foundry(
    openai_client: Any, payload: dict[str, Any], run_name: str, *, poll_s: float = 5.0,
    timeout_s: float = 600.0, sleep=time.sleep,
) -> dict[str, Any]:  # fmt: skip
    """Create the eval and one run over the inline rows, wait for it, and return a summary. The
    rows travel inline (`file_content`), so no dataset is uploaded to project storage."""
    ev = openai_client.evals.create(
        name=payload["name"],
        data_source_config=payload["data_source_config"],
        testing_criteria=payload["testing_criteria"],
    )
    run = openai_client.evals.runs.create(
        eval_id=ev.id,
        name=run_name,
        data_source={
            "type": "jsonl",
            "source": {"type": "file_content", "content": [{"item": r} for r in payload["rows"]]},
        },
    )
    waited = 0.0
    while run.status not in ("completed", "failed", "canceled") and waited < timeout_s:
        sleep(poll_s)
        waited += poll_s
        run = openai_client.evals.runs.retrieve(run_id=run.id, eval_id=ev.id)
    per_criterion = {}
    for r in getattr(run, "per_testing_criteria_results", None) or []:
        name = getattr(r, "testing_criteria", "")
        passed, failed = getattr(r, "passed", 0), getattr(r, "failed", 0)
        per_criterion[_criterion_name(name, payload)] = {"passed": passed, "failed": failed}
    return {
        "eval_id": ev.id,
        "run_id": run.id,
        "status": run.status,
        "report_url": getattr(run, "report_url", None),
        "per_criterion": per_criterion,
    }


def _criterion_name(reported: str, payload: dict[str, Any]) -> str:
    """Foundry may report a criterion as `<name>` or `<name>-<suffix>`; map it back to ours."""
    for c in payload["testing_criteria"]:
        if reported == c["name"] or reported.startswith(c["name"] + "-"):
            return c["name"]
    return reported
