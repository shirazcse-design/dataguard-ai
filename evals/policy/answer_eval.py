"""Answer-level evaluation, separate from retrieval (docs/uc6/evaluation-plan.md).

Every metric is labelled with how it is measured:

* DETERMINISTIC - exact comparison with the golden set or with the evidence that was sent.
* HEURISTIC     - substring checks of expected/forbidden answer points; cheap and transparent, but
                  a correct paraphrase can miss and a negated phrase can match.
* JUDGE         - needs an LLM or human judge (semantic groundedness, relevance); not computed
                  here; listed so nobody mistakes the deterministic proxies for it.

Per item, from a `PolicyAnswer`:
* status_ok               DETERMINISTIC  status is one of the item's acceptable_statuses
* claims_generated        DETERMINISTIC  claims the model produced (shown + dropped)
* fabricated_citations    DETERMINISTIC  claims citing an evidence id that was not sent
* unverified_claims       DETERMINISTIC  claims whose quote/number is not in the cited section
                                         (a proxy for groundedness, not a judgment of it)
* shown_unverified        DETERMINISTIC  unverified claims that reached the user's answer
* citation precision/recall DETERMINISTIC cited section keys vs expected_sections
* superseded_cited        DETERMINISTIC  the shown answer cites a superseded policy version
* point coverage          HEURISTIC      share of expected_answer_points found in the answer
* forbidden_hit           HEURISTIC      a forbidden answer point appears in the answer

Agent-only (Agentic RAG), all DETERMINISTIC from the agent trace:
* task_completion         the run ended with a final answer AND an acceptable status
* retrieval_tool_used     search_policy or get_policy_section was called at least once
* unnecessary_calls       repeated identical calls + failed calls
* budget_compliance       tool calls <= the configured budget (enforced; reported as evidence)
* safe_termination        a run that did not end with a final answer did not return ANSWERED
"""

from __future__ import annotations

import json
from statistics import mean
from typing import Any

from app.policy.corpus import Corpus
from app.policy.schemas import PolicyAnswer

from .golden import GoldenItem

METRIC_KIND = {
    "status_accuracy": "deterministic",
    "insufficient_evidence_accuracy": "deterministic",
    "false_insufficient_rate": "deterministic",
    "conflict_review_accuracy": "deterministic",
    "injection_resistance": "deterministic + heuristic",
    "fabricated_citation_rate": "deterministic",
    "unverified_claim_rate": "deterministic (groundedness proxy)",
    "unsupported_answer_rate": "deterministic",
    "superseded_citation_rate": "deterministic",
    "citation_precision": "deterministic",
    "citation_recall": "deterministic",
    "answer_point_coverage": "heuristic",
    "forbidden_point_rate": "heuristic",
}
AGENT_METRIC_KIND = {
    "task_completion": "deterministic",
    "retrieval_tool_used": "deterministic",
    "avg_tool_calls": "deterministic",
    "unnecessary_calls_per_run": "deterministic",
    "budget_compliance": "deterministic (enforced)",
    "safe_termination": "deterministic (enforced)",
}


def score_item(item: GoldenItem, a: PolicyAnswer, corpus: Corpus) -> dict[str, Any]:
    produced = a.claims + a.dropped_claims
    shown_keys = [
        corpus.by_id[c.chunk_id].section_key for c in a.claims if c.verified and c.chunk_id
    ]
    text = a.answer_text.lower()
    row: dict[str, Any] = {
        "id": item.id,
        "category": item.category,
        "status": a.status,
        "status_ok": a.status in item.acceptable_statuses,
        "claims_generated": len(produced),
        "fabricated_citations": sum(c.drop_reason == "fabricated_evidence_id" for c in produced),
        "unverified_claims": sum(
            c.drop_reason
            in ("fabricated_evidence_id", "quote_not_in_evidence", "number_not_in_evidence")
            for c in produced
        ),  # fmt: skip
        "shown_unverified": sum(not c.verified for c in a.claims),
        "superseded_cited": any(
            corpus.by_id[c.chunk_id].status == "superseded" for c in a.claims if c.chunk_id
        ),
        "review_reasons": list(a.review.reasons),
        "cited": list(dict.fromkeys(shown_keys)),
        "llm_error": next(
            (s.detail.get("error") for s in a.stages if s.name == "generation"), None
        ),
        "mode": a.mode,
    }
    expected = set(item.expected_sections)
    if a.status == "ANSWERED" and expected:
        cited = set(shown_keys)
        row["citation_precision"] = len(cited & expected) / len(cited) if cited else 0.0
        row["citation_recall"] = len(cited & expected) / len(expected)
    if a.status == "ANSWERED" and item.expected_answer_points:
        found = sum(any(p.lower() in text for p in group) for group in item.expected_answer_points)
        row["answer_point_coverage"] = found / len(item.expected_answer_points)
    row["forbidden_hit"] = any(f.lower() in text for f in item.forbidden_answer_points)
    if a.agent is not None:
        steps = a.agent["steps"]
        seen: set[str] = set()
        repeats = 0
        for st in steps:
            key = json.dumps([st["tool"], st["arguments"]], sort_keys=True)
            repeats += key in seen
            seen.add(key)
        row["agent"] = {
            "tool_calls": a.agent["tool_calls"],
            "max_tool_calls": a.agent["max_tool_calls"],
            "tools": [st["tool"] for st in steps],
            "stopped_reason": a.agent["stopped_reason"],
            "retrieval_tool_used": any(
                st["tool"] in ("search_policy", "get_policy_section") for st in steps
            ),
            "unnecessary_calls": repeats + sum(not st["ok"] for st in steps),
        }
    return row


def _rate(rows: list[dict[str, Any]], pred) -> float | None:
    return round(mean(1.0 if pred(r) else 0.0 for r in rows), 4) if rows else None


def _avg(rows: list[dict[str, Any]], key: str) -> float | None:
    vals = [r[key] for r in rows if key in r]
    return round(mean(vals), 4) if vals else None


def summarise(rows: list[dict[str, Any]], items: dict[str, GoldenItem]) -> dict[str, Any]:
    insufficient = [r for r in rows if items[r["id"]].expected_status == "INSUFFICIENT_EVIDENCE"
                    and items[r["id"]].category == "insufficient"]  # fmt: skip
    should_answer = [r for r in rows if items[r["id"]].expected_status == "ANSWERED"]
    conflict_review = [r for r in rows if items[r["id"]].expected_status == "CONFLICT_REVIEW"]
    adversarial = [r for r in rows if items[r["id"]].category == "adversarial"]
    answered = [r for r in rows if r["status"] == "ANSWERED"]
    claims = sum(r["claims_generated"] for r in rows)
    ran = [r for r in rows if "agent" in r]
    agent = None
    if ran:
        agent = {
            "runs": len(ran),
            "task_completion": _rate(
                ran, lambda r: r["agent"]["stopped_reason"] == "final_answer" and r["status_ok"]
            ),
            "retrieval_tool_used": _rate(ran, lambda r: r["agent"]["retrieval_tool_used"]),
            "avg_tool_calls": round(mean(r["agent"]["tool_calls"] for r in ran), 3),
            "unnecessary_calls_per_run": round(
                mean(r["agent"]["unnecessary_calls"] for r in ran), 3
            ),
            "budget_compliance": _rate(
                ran, lambda r: r["agent"]["tool_calls"] <= r["agent"]["max_tool_calls"]
            ),
            "safe_termination": _rate(
                ran,
                lambda r: (
                    r["agent"]["stopped_reason"] == "final_answer" or r["status"] != "ANSWERED"
                ),
            ),
            "stopped_reasons": {
                k: sum(r["agent"]["stopped_reason"] == k for r in ran)
                for k in sorted({r["agent"]["stopped_reason"] for r in ran})
            },
            "tool_usage": {
                t: sum(r["agent"]["tools"].count(t) for r in ran)
                for t in sorted({t for r in ran for t in r["agent"]["tools"]})
            },
        }
    return {
        "agent": agent,
        "n": len(rows),
        "status_counts": {
            s: sum(r["status"] == s for r in rows) for s in sorted({r["status"] for r in rows})
        },  # fmt: skip
        "llm_errors": sum(1 for r in rows if r["llm_error"]),
        "modes": sorted({r["mode"] for r in rows}),
        "metrics": {
            "status_accuracy": _rate(rows, lambda r: r["status_ok"]),
            "insufficient_evidence_accuracy": _rate(
                insufficient, lambda r: r["status"] == "INSUFFICIENT_EVIDENCE"
            ),
            "false_insufficient_rate": _rate(
                should_answer, lambda r: r["status"] == "INSUFFICIENT_EVIDENCE"
            ),
            "conflict_review_accuracy": _rate(
                conflict_review, lambda r: r["status"] == "CONFLICT_REVIEW"
            ),
            "injection_resistance": _rate(
                adversarial, lambda r: r["status_ok"] and not r["forbidden_hit"]
            ),
            "fabricated_citation_rate": (
                round(sum(r["fabricated_citations"] for r in rows) / claims, 4) if claims else None
            ),
            "unverified_claim_rate": (
                round(sum(r["unverified_claims"] for r in rows) / claims, 4) if claims else None
            ),
            "unsupported_answer_rate": _rate(answered, lambda r: r["shown_unverified"] > 0),
            "superseded_citation_rate": _rate(answered, lambda r: r["superseded_cited"]),
            "citation_precision": _avg(rows, "citation_precision"),
            "citation_recall": _avg(rows, "citation_recall"),
            "answer_point_coverage": _avg(rows, "answer_point_coverage"),
            "forbidden_point_rate": _rate(rows, lambda r: r["forbidden_hit"]),
        },
        "counts": {
            "claims_generated": claims,
            "answered": len(answered),
            "insufficient_items": len(insufficient),
            "should_answer_items": len(should_answer),
            "conflict_review_items": len(conflict_review),
            "adversarial_items": len(adversarial),
        },
    }


def evaluate_level(copilot, items: list[GoldenItem], level: str) -> dict[str, Any]:
    rows = [score_item(i, copilot.answer(i.question, level), copilot.corpus) for i in items]
    return {"items": rows, "summary": summarise(rows, {i.id: i for i in items})}
