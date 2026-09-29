"""The retriever: one set of components, configured per RAG level.

    question -> [query processing] -> sparse (BM25) leg + dense (embedding) leg
             -> [metadata filter] -> Reciprocal Rank Fusion -> [deterministic rerank] -> top-k

Naive / Advanced / Agentic differ only by their `LevelConfig` (config/policy/policy.v1.yaml); the
agent calls this same retriever through its `search_policy` tool.

Degradation is explicit: if the dense leg cannot run (no embedding deployment configured, or a
replay miss for an unrecorded question), a hybrid retrieval continues on the sparse leg alone and
the trace says so (`dense_status`). A dense-only retrieval returns nothing rather than quietly
switching method. The stages the trace lists are exactly the stages that ran.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import Field

from app.classification.schemas.common import StrictModel

from .config import LevelConfig, PolicyConfig
from .corpus import Corpus
from .embeddings import Embedder, EmbeddingError
from .query import process_query
from .rerank import rerank_components
from .sparse import BM25Index
from .store import LocalVectorStore, VectorStore


class Hit(StrictModel):
    chunk_id: str
    citation: str
    status: str
    rank: int = Field(ge=1)
    sparse_rank: int | None = None
    sparse_score: float | None = None
    dense_rank: int | None = None
    dense_score: float | None = None
    fused_score: float
    rerank: dict[str, float] | None = None

    @property
    def evidence_score(self) -> float:
        """What the evidence gate thresholds: the rerank score when reranking ran, else the
        cosine similarity (dense) or nothing comparable (sparse-only BM25 is unbounded)."""
        if self.rerank is not None:
            return self.rerank["score"]
        return self.dense_score if self.dense_score is not None else 0.0


class RetrievalTrace(StrictModel):
    """Safe to show and to trace: ids, counts, scores, configured expansion phrases, timings.
    Never the question text or chunk text."""

    level: dict[str, Any]
    stages: list[str]
    expansions: list[str] = Field(default_factory=list)
    added_terms: list[str] = Field(default_factory=list)
    excluded_by_filter: int = 0
    dense_status: str  # ok | not_requested | <error kind>
    sparse_candidates: list[str] = Field(default_factory=list)
    dense_candidates: list[str] = Field(default_factory=list)
    fused_candidates: int = 0
    latency_ms: dict[str, float] = Field(default_factory=dict)


class RetrievalResult(StrictModel):
    query_text: str  # the processed query (kept in memory for generation; never exported)
    hits: list[Hit]
    trace: RetrievalTrace


class Retriever:
    def __init__(
        self,
        corpus: Corpus,
        cfg: PolicyConfig,
        embedder: Embedder | None,
        *,
        store: VectorStore | None = None,
    ) -> None:
        self.corpus = corpus
        self.cfg = cfg
        self.embedder = embedder
        ids = [c.chunk_id for c in corpus.chunks]
        self.bm25 = BM25Index(
            ids,
            [c.index_text for c in corpus.chunks],
            k1=cfg.sparse.bm25_k1,
            b=cfg.sparse.bm25_b,
        )
        self.store: VectorStore | None = None
        self.index_status = "not_configured"
        if embedder is not None:
            try:
                vectors = embedder.embed([c.index_text for c in corpus.chunks])
            except EmbeddingError as err:
                self.index_status = err.kind
            else:
                self.store = store if store is not None else LocalVectorStore()
                self.store.add(ids, vectors)
                self.index_status = "ok"
        self.eligible = {
            c.chunk_id for c in corpus.chunks if c.status in cfg.corpus.eligible_statuses
        }

    @property
    def embedding_model_id(self) -> str | None:
        return getattr(self.embedder, "model_id", None) if self.store is not None else None

    def _dense(self, text: str, allowed: set[str] | None, n: int) -> tuple[list, str]:
        if self.store is None:
            return [], self.index_status
        try:
            qvec = self.embedder.embed([text])[0]  # type: ignore[union-attr]
        except EmbeddingError as err:
            return [], err.kind
        return self.store.search(qvec, n, allowed), "ok"

    def retrieve(
        self, question: str, level: LevelConfig, top_k: int | None = None
    ) -> RetrievalResult:
        k = top_k or level.top_k
        n = self.cfg.hybrid.candidates_per_leg
        timings: dict[str, float] = {}
        stages: list[str] = []

        t = time.perf_counter()
        pq = process_query(question, self.cfg.query.expansions, level.query_processing)
        if level.query_processing:
            stages.append("query_processing")
            timings["query_processing"] = _ms(t)

        allowed = self.eligible if level.metadata_filter else None
        excluded = len(self.corpus.chunks) - len(self.eligible) if allowed is not None else 0
        if allowed is not None:
            stages.append("metadata_filter")

        sparse: list[tuple[str, float]] = []
        if level.retrieval in ("sparse", "hybrid"):
            t = time.perf_counter()
            scores = self.bm25.scores(pq.text)
            ranked = sorted(
                (
                    (cid, s)
                    for cid, s in zip(self.bm25.ids, scores, strict=True)
                    if s > 0 and (allowed is None or cid in allowed)
                ),
                key=lambda x: (-x[1], x[0]),
            )
            sparse = ranked[:n]
            stages.append("sparse")
            timings["sparse"] = _ms(t)

        dense: list[tuple[str, float]] = []
        dense_status = "not_requested"
        if level.retrieval in ("dense", "hybrid"):
            t = time.perf_counter()
            dense, dense_status = self._dense(pq.text, allowed, n)
            if dense_status == "ok":
                stages.append("dense")
            timings["dense"] = _ms(t)

        # Reciprocal Rank Fusion over the legs that actually produced candidates.
        t = time.perf_counter()
        rrf: dict[str, float] = {}
        info: dict[str, dict[str, Any]] = {}
        for leg, results in (("sparse", sparse), ("dense", dense)):
            for rank, (cid, score) in enumerate(results, start=1):
                rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (self.cfg.hybrid.rrf_k + rank)
                info.setdefault(cid, {})[f"{leg}_rank"] = rank
                info[cid][f"{leg}_score"] = round(score, 4)
        if sparse and dense:
            stages.append("fusion")
        timings["fusion"] = _ms(t)
        order = sorted(rrf, key=lambda cid: (-rrf[cid], cid))
        top = rrf[order[0]] if order else 1.0

        rerank: dict[str, dict[str, float]] = {}
        if level.rerank and order:
            t = time.perf_counter()
            for cid in order:
                rerank[cid] = rerank_components(
                    pq.text, self.corpus.by_id[cid], rrf[cid] / top, self.cfg.rerank
                )
            order = sorted(order, key=lambda cid: (-rerank[cid]["score"], -rrf[cid], cid))
            stages.append("rerank")
            timings["rerank"] = _ms(t)

        hits = [
            Hit(
                chunk_id=cid,
                citation=self.corpus.by_id[cid].citation,
                status=self.corpus.by_id[cid].status,
                rank=i,
                fused_score=round(rrf[cid], 6),
                rerank=rerank.get(cid),
                **info[cid],
            )
            for i, cid in enumerate(order[:k], start=1)
        ]
        trace = RetrievalTrace(
            level=level.model_dump(),
            stages=stages,
            expansions=pq.expansions,
            added_terms=pq.added_terms,
            excluded_by_filter=excluded,
            dense_status=dense_status,
            sparse_candidates=[cid for cid, _ in sparse],
            dense_candidates=[cid for cid, _ in dense],
            fused_candidates=len(rrf),
            latency_ms=timings,
        )
        return RetrievalResult(query_text=pq.text, hits=hits, trace=trace)


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)
