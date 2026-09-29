"""Builds the files YOU upload to Foundry Evaluations (docs/uc6/foundry-evals-setup.md).

Nothing here talks to Foundry: it writes local JSONL files from a REPLAY run of the golden set.
Every value is synthetic (the Harbourline corpus and the golden questions), so it is safe to upload.

* `answers.jsonl`  - one row per (item, level) with a shown answer: `query`, `response`, `context`
  (the text of the evidence the answer could cite), `ground_truth` (the item's expected answer
  points, a rough reference, not a written model answer), plus ids and local results so Foundry
  scores can be joined back to local ones. For model-judged evaluators (groundedness, relevance,
  retrieval).
* `outcomes.jsonl` - one row per (item, level) for ALL 108 runs, with `expected_status`,
  `actual_status` and precomputed pass/fail fields, for deterministic string-check graders that
  should reproduce the local numbers exactly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .answer_eval import score_item


def _context(answer) -> str:
    sent = [e for e in answer.evidence if not e.flagged_injection]
    return "\n\n".join(f"[{e.citation}] {e.heading}: {e.body}" for e in sent)


def build_rows(copilot, items, levels: list[str]) -> tuple[list[dict], list[dict]]:
    answers: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    for level in levels:
        for item in items:
            a = copilot.answer(item.question, level)
            r = score_item(item, a, copilot.corpus)
            outcomes.append(
                {
                    "id": f"{item.id}-{level}",
                    "item_id": item.id,
                    "level": level,
                    "category": item.category,
                    "mode": a.mode,
                    "expected_status": item.expected_status,
                    "actual_status": a.status,
                    "status_ok": "pass" if r["status_ok"] else "fail",
                    "no_forbidden_phrase": "fail" if r["forbidden_hit"] else "pass",
                    "no_unverified_shown": "fail" if r["shown_unverified"] else "pass",
                    "no_fabricated_citation": "fail" if r["fabricated_citations"] else "pass",
                }
            )
            if a.status != "ANSWERED" or not a.claims:
                continue
            answers.append(
                {
                    "id": f"{item.id}-{level}",
                    "item_id": item.id,
                    "level": level,
                    "category": item.category,
                    "query": item.question,
                    "response": " ".join(
                        f"{c.text} [{c.citation}]" if c.citation else c.text for c in a.claims
                    ),
                    "context": _context(a),
                    "ground_truth": "; ".join(" / ".join(g) for g in item.expected_answer_points),
                    "local_citation_precision": r.get("citation_precision"),
                    "local_point_coverage": r.get("answer_point_coverage"),
                    "local_all_claims_verified": all(c.verified for c in a.claims),
                }
            )
    return answers, outcomes


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
        encoding="utf-8",
    )
