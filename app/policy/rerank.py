"""Deterministic reranker.

    score = w_coverage * |Q ∩ chunk terms| / |Q|
          + w_heading  * |Q ∩ heading terms| / |heading terms|
          + w_fused    * fused_score / max fused_score
          - superseded_penalty (if the chunk is from a superseded policy version)

Q is the set of content terms of the (processed) query. Every component is shown in the retrieval
trace, so a reranking decision can always be explained by numbers a reader can recompute. It is a
lexical reranker: it rewards chunks that address the query's own words, which the fused hybrid
score alone does not guarantee. A cross-encoder/LLM reranker is a deliberate non-goal of the MVP.
"""

from __future__ import annotations

from .config import RerankConfig
from .corpus import Chunk
from .text import terms


def rerank_components(
    query_text: str, chunk: Chunk, fused_norm: float, cfg: RerankConfig
) -> dict[str, float]:
    q = set(terms(query_text))
    body = set(terms(chunk.index_text))
    heading = set(terms(chunk.heading))
    coverage = len(q & body) / len(q) if q else 0.0
    head = len(q & heading) / len(heading) if heading else 0.0
    penalty = cfg.superseded_penalty if chunk.status == "superseded" else 0.0
    score = cfg.w_coverage * coverage + cfg.w_heading * head + cfg.w_fused * fused_norm - penalty
    return {
        "coverage": round(coverage, 4),
        "heading": round(head, 4),
        "fused_norm": round(fused_norm, 4),
        "penalty": penalty,
        "score": round(max(score, 0.0), 4),
    }
