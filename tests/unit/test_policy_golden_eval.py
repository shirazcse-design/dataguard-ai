"""UC6 golden set integrity and retrieval-metric arithmetic."""

from __future__ import annotations

from collections import Counter

import pytest

from app.policy.config import load_policy_config
from app.policy.corpus import load_corpus
from app.policy.embeddings import HashingEmbedder
from app.policy.retrieval import Retriever
from evals.policy.golden import GoldenItem, check_against_corpus, load_golden
from evals.policy.report import render_markdown
from evals.policy.retrieval_eval import _item_metrics, evaluate_variant, variants


def test_golden_set_is_valid_and_matches_the_corpus():
    items, sha = load_golden()
    cfg, _ = load_policy_config()
    assert check_against_corpus(items, load_corpus(cfg.corpus.dir, cfg.chunking)) == []
    assert len(items) == 36 and len(sha) == 64
    cats = Counter(i.category for i in items)
    assert cats["insufficient"] >= 5 and cats["conflict"] >= 3 and cats["adversarial"] >= 3
    assert any(i.expected_status == "CONFLICT_REVIEW" for i in items)


def test_inconsistent_golden_item_is_rejected():
    items, _ = load_golden()
    bad = items[0].model_dump() | {"answerable": False}
    with pytest.raises(ValueError):
        GoldenItem.model_validate(bad)


def test_metric_arithmetic():
    m = _item_metrics(["a", "x", "b", "y", "z", "c"], {"a", "b", "c"})
    assert m["recall@3"] == pytest.approx(2 / 3) and m["precision@3"] == pytest.approx(2 / 3)
    assert m["recall@5"] == pytest.approx(2 / 3) and m["precision@5"] == pytest.approx(0.4)
    assert m["recall@10"] == 1.0 and m["hit@3"] == 1.0 and m["mrr"] == 1.0
    assert _item_metrics(["x", "a"], {"a"})["mrr"] == 0.5
    assert _item_metrics(["x"], {"a"})["mrr"] == 0.0


def test_ablation_runs_offline_and_the_report_flags_the_offline_embedder():
    cfg, _ = load_policy_config()
    corpus = load_corpus(cfg.corpus.dir, cfg.chunking)
    items, sha = load_golden()
    r = Retriever(corpus, cfg, HashingEmbedder())
    vs = variants(cfg)
    assert list(vs) == [
        "naive (dense)",
        "sparse (BM25)",
        "hybrid (RRF)",
        "hybrid + query processing",
        "advanced",
    ]
    report = {
        "provenance": {
            "embed_mode": "offline",
            "embedding_model_id": "offline-hash",
            "golden_items": 36,
            "scored_items": 28,
            "golden_sha256": sha,
            "corpus_fingerprint": corpus.fingerprint(),
            "chunks": len(corpus.chunks),
            "config_sha256": "0" * 64,
        },
        "variants": {n: evaluate_variant(r, items, lv) for n, lv in vs.items()},
    }
    adv = report["variants"]["advanced"]["summary"]
    assert adv["n_scored"] == 28 and adv["draft_in_top_k_rate"] == 0.0
    md = render_markdown(report)
    assert "NOT a semantic model" in md and "OFFLINE" in md
