"""Retrieval evaluation, measured separately from answer generation (docs/uc6/evaluation-plan.md).

All metrics here are DETERMINISTIC: they compare retrieved section keys with the golden set's
`expected_sections`. Per item with expected evidence:

* Recall@K    = relevant sections retrieved in the top K / relevant sections
* Precision@K = relevant hits in the top K / K (so a one-section question caps P@5 at 0.20)
* HitRate@K   = 1 if any relevant section is in the top K (the PRD's "retrieval hit rate")
* MRR         = 1 / rank of the first relevant hit (0 if none in the top 10)

Plus two retrieval-safety measures over ALL items: how often a `draft` (unapproved) chunk reaches
the top K, and, for conflict items, whether every conflicting section is surfaced in the top K.

The variants form an ablation ladder: naive (dense only) -> sparse only -> hybrid -> hybrid + query
processing -> advanced (hybrid + query processing + metadata filter + rerank). Agentic RAG calls the
Advanced retriever through `search_policy`; its multi-step behaviour is evaluated separately.
"""

from __future__ import annotations

from statistics import mean
from typing import Any

from app.policy.config import LevelConfig, PolicyConfig
from app.policy.retrieval import Retriever

from .golden import GoldenItem

KS = (3, 5, 10)


def variants(cfg: PolicyConfig) -> dict[str, LevelConfig]:
    naive = cfg.levels["naive"]
    advanced = cfg.levels["advanced"]
    base = {"query_processing": False, "metadata_filter": False, "rerank": False}
    return {
        "naive (dense)": naive,
        "sparse (BM25)": LevelConfig(**{**base, "retrieval": "sparse", "top_k": naive.top_k}),
        "hybrid (RRF)": LevelConfig(**{**base, "retrieval": "hybrid", "top_k": naive.top_k}),
        "hybrid + query processing": LevelConfig(
            **{**base, "query_processing": True, "retrieval": "hybrid", "top_k": naive.top_k}
        ),
        "advanced": advanced,
    }


def _item_metrics(ranked_keys: list[str], relevant: set[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for k in KS:
        top = ranked_keys[:k]
        found = relevant & set(top)
        out[f"recall@{k}"] = len(found) / len(relevant)
        out[f"precision@{k}"] = sum(1 for key in top if key in relevant) / k
        out[f"hit@{k}"] = 1.0 if found else 0.0
    first = next((i for i, key in enumerate(ranked_keys[:10], start=1) if key in relevant), None)
    out["mrr"] = 1.0 / first if first else 0.0
    return out


def evaluate_variant(
    retriever: Retriever, items: list[GoldenItem], level: LevelConfig
) -> dict[str, Any]:
    per_item: list[dict[str, Any]] = []
    for item in items:
        result = retriever.retrieve(item.question, level, top_k=max(KS))
        chunks = [retriever.corpus.by_id[h.chunk_id] for h in result.hits]
        keys = list(dict.fromkeys(c.section_key for c in chunks))  # parts count once
        row: dict[str, Any] = {
            "id": item.id,
            "category": item.category,
            "retrieved": keys[: level.top_k],
            "dense_status": result.trace.dense_status,
            "draft_in_top_k": any(c.status == "draft" for c in chunks[: level.top_k]),
            "top1_evidence_score": result.hits[0].evidence_score if result.hits else 0.0,
        }
        if item.expected_sections:
            row.update(_item_metrics(keys, set(item.expected_sections)))
        if item.conflict_sections:
            wanted = set(item.conflict_sections) | set(item.expected_sections)
            row["conflict_surfaced"] = wanted <= set(keys[: level.top_k])
        per_item.append(row)
    return {"level": level.model_dump(), "items": per_item, "summary": summarise(per_item)}


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if "mrr" in r]
    metrics = [f"{m}@{k}" for m in ("recall", "precision", "hit") for k in KS] + ["mrr"]
    summary: dict[str, Any] = {
        "n_scored": len(scored),
        "overall": {m: round(mean(r[m] for r in scored), 4) for m in metrics} if scored else {},
        "by_category": {},
        "draft_in_top_k_rate": round(mean(r["draft_in_top_k"] for r in rows), 4),
        "dense_unavailable": sum(
            1 for r in rows if r["dense_status"] not in ("ok", "not_requested")
        ),
    }
    for cat in sorted({r["category"] for r in scored}):
        sub = [r for r in scored if r["category"] == cat]
        summary["by_category"][cat] = {
            "n": len(sub),
            "recall@5": round(mean(r["recall@5"] for r in sub), 4),
            "mrr": round(mean(r["mrr"] for r in sub), 4),
        }
    conflicts = [r for r in rows if "conflict_surfaced" in r]
    if conflicts:
        rate = mean(r["conflict_surfaced"] for r in conflicts)
        summary["conflict_surfaced_rate"] = round(rate, 4)
    return summary
