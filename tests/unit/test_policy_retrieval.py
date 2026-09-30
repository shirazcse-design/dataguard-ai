"""UC6 retrieval: BM25, vector store, query processing, fusion, metadata filter, rerank, and
explicit degradation when the dense leg is unavailable."""

from __future__ import annotations

import numpy as np
import pytest

from app.policy.config import LevelConfig, load_policy_config
from app.policy.corpus import load_corpus
from app.policy.embeddings import EmbeddingError, HashingEmbedder
from app.policy.query import process_query
from app.policy.rerank import rerank_components
from app.policy.retrieval import Retriever
from app.policy.sparse import BM25Index
from app.policy.store import LocalVectorStore
from app.policy.text import terms


@pytest.fixture(scope="module")
def setup():
    cfg, _ = load_policy_config()
    corpus = load_corpus(cfg.corpus.dir, cfg.chunking)
    return cfg, corpus, Retriever(corpus, cfg, HashingEmbedder())


def _level(**kw):
    base = dict(
        query_processing=False, retrieval="hybrid", metadata_filter=False, rerank=False, top_k=5
    )
    return LevelConfig(**{**base, **kw})


def test_terms_drop_stopwords_and_stem_plurals():
    assert terms("The policies for USB drives") == ["policy", "usb", "drive"]


def test_bm25_prefers_the_document_with_the_rare_query_term():
    idx = BM25Index(
        ["a", "b", "c"], ["usb removable media", "email policy", "email email"], k1=1.2, b=0.75
    )
    s = idx.scores("usb")
    assert s[0] > 0 and s[1] == 0 and s[2] == 0


def test_vector_store_cosine_order_and_allow_list():
    store = LocalVectorStore()
    store.add(["x", "y"], np.array([[1.0, 0.0], [0.0, 1.0]]))
    assert [i for i, _ in store.search(np.array([0.9, 0.1]), 2)] == ["x", "y"]
    assert [i for i, _ in store.search(np.array([0.9, 0.1]), 2, allowed={"y"})] == ["y"]
    with pytest.raises(ValueError):
        store.add(["x"], np.array([[1.0, 0.0]]))


def test_query_processing_appends_and_never_drops_user_words():
    pq = process_query("Can I put files on Dropbox?", {"dropbox": ["personal cloud storage"]}, True)
    assert pq.text.startswith("Can I put files on Dropbox?")
    assert pq.expansions == ["dropbox"] and pq.added_terms == ["personal cloud storage"]
    off = process_query("Can I put files on Dropbox?", {"dropbox": ["x"]}, False)
    assert off.text == "Can I put files on Dropbox?" and off.added_terms == []


def test_query_expansion_matches_whole_phrases_only():
    assert (
        process_query("graphics", {"phi": ["protected health information"]}, True).added_terms == []
    )


def test_metadata_filter_excludes_drafts_but_keeps_superseded(setup):
    cfg, corpus, r = setup
    res = r.retrieve(
        "vendor customer data security assessment", _level(metadata_filter=True), top_k=74
    )
    statuses = {corpus.by_id[h.chunk_id].status for h in res.hits}
    assert "draft" not in statuses
    res2 = r.retrieve(
        "retention customer financial records", _level(metadata_filter=True), top_k=10
    )
    assert any(h.status == "superseded" for h in res2.hits)
    assert res.trace.excluded_by_filter == 3


def test_naive_level_can_retrieve_the_unapproved_draft(setup):
    cfg, corpus, r = setup
    res = r.retrieve(
        "Can a vendor receive customer data before its security assessment?",
        cfg.levels["naive"],
        top_k=74,
    )
    assert any(h.status == "draft" for h in res.hits)


def test_trace_lists_exactly_the_stages_that_ran(setup):
    cfg, _, r = setup
    assert r.retrieve("usb", cfg.levels["naive"]).trace.stages == ["dense"]
    adv = r.retrieve("usb", cfg.levels["advanced"]).trace.stages
    assert adv == ["query_processing", "metadata_filter", "sparse", "dense", "fusion", "rerank"]
    assert r.retrieve("usb", _level(retrieval="sparse")).trace.stages == ["sparse"]


def test_rerank_components_are_reported_and_superseded_is_penalised(setup):
    cfg, corpus, _ = setup
    new = rerank_components(
        "customer financial records", corpus.by_id["POL-RET@2.0#2.1"], 1.0, cfg.rerank
    )
    old = rerank_components(
        "customer financial records", corpus.by_id["POL-RET@1.0#2.1"], 1.0, cfg.rerank
    )
    assert set(new) == {"coverage", "heading", "fused_norm", "penalty", "score"}
    assert old["penalty"] == cfg.rerank.superseded_penalty and new["score"] > old["score"]


def test_rerank_orders_by_rerank_score(setup):
    cfg, _, r = setup
    hits = r.retrieve("How often must API keys be rotated?", cfg.levels["advanced"]).hits
    scores = [h.rerank["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert hits[0].chunk_id == "POL-SEC@1.1#4"


class _Failing:
    model_id = "broken"

    def __init__(self, fail_on_query_only: bool):
        self.fail_on_query_only = fail_on_query_only
        self.inner = HashingEmbedder()

    def embed(self, texts):
        if not self.fail_on_query_only or len(texts) == 1:
            raise EmbeddingError("replay_miss")
        return self.inner.embed(texts)


def test_hybrid_degrades_to_sparse_and_says_so(setup):
    cfg, corpus, _ = setup
    r = Retriever(corpus, cfg, _Failing(fail_on_query_only=True))
    res = r.retrieve("usb drive", cfg.levels["advanced"])
    assert res.trace.dense_status == "replay_miss"
    assert "dense" not in res.trace.stages and "fusion" not in res.trace.stages
    assert res.hits  # sparse still answered


def test_dense_only_returns_nothing_rather_than_switching_method(setup):
    cfg, corpus, _ = setup
    r = Retriever(corpus, cfg, _Failing(fail_on_query_only=False))
    assert r.index_status == "replay_miss" and r.embedding_model_id is None
    res = r.retrieve("usb drive", cfg.levels["naive"])
    assert res.hits == [] and res.trace.dense_status == "replay_miss"


def test_retrieval_trace_never_contains_the_question(setup):
    cfg, _, r = setup
    q = "Can Zanzibar-7741 upload files to a personal Dropbox?"
    trace = r.retrieve(q, cfg.levels["advanced"]).trace.model_dump_json()
    assert "Zanzibar" not in trace and "7741" not in trace
