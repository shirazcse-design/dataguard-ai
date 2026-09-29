"""UC6 answer-level metrics: arithmetic and labelling, on constructed PolicyAnswers."""

from __future__ import annotations

from app.policy.config import load_policy_config
from app.policy.corpus import load_corpus
from app.policy.schemas import Claim, PolicyAnswer, Review
from evals.policy.answer_eval import METRIC_KIND, score_item, summarise
from evals.policy.golden import load_golden
from evals.policy.report import ANSWER_ROWS, render_answers_markdown

CFG, _ = load_policy_config()
CORPUS = load_corpus(CFG.corpus.dir, CFG.chunking)
ITEMS = {i.id: i for i in load_golden()[0]}


def _claim(chunk_id, verified=True, reason=None, text="Uploading is prohibited."):
    c = CORPUS.by_id[chunk_id] if chunk_id else None
    return Claim(text=text, evidence_id="E1", chunk_id=chunk_id, citation=c.citation if c else None,
                 quote="q", verified=verified, drop_reason=reason)  # fmt: skip


def _answer(status, claims=(), dropped=()):
    return PolicyAnswer(request_id="r", level="advanced", mode="replay", status=status, claims=list(claims),
                        dropped_claims=list(dropped), citations=[], evidence=[], evidence_status="none",
                        review=Review(), stages=[])  # fmt: skip


def test_grounded_answer_scores():
    r = score_item(
        ITEMS["S01"],
        _answer("ANSWERED", [_claim("POL-DLP@2.1#4.2"), _claim("POL-ACC@2.0#6")]),
        CORPUS,
    )
    assert r["status_ok"] and r["citation_precision"] == 0.5 and r["citation_recall"] == 1 / 3
    assert r["answer_point_coverage"] == 1.0 and not r["forbidden_hit"]


def test_fabricated_and_unverified_are_counted_even_when_dropped():
    r = score_item(ITEMS["S01"], _answer("ANSWERED", [_claim("POL-DLP@2.1#4.2")],
                                         [_claim(None, False, "fabricated_evidence_id"),
                                          _claim("POL-DLP@2.1#4.2", False, "quote_not_in_evidence")]), CORPUS)  # fmt: skip
    assert (
        r["claims_generated"] == 3
        and r["fabricated_citations"] == 1
        and r["unverified_claims"] == 2
    )
    assert r["shown_unverified"] == 0


def test_summary_rates():
    rows = [
        score_item(ITEMS["I01"], _answer("INSUFFICIENT_EVIDENCE"), CORPUS),
        score_item(
            ITEMS["I02"],
            _answer("ANSWERED", [_claim("POL-RET@2.0#2.3", False, "quote_not_in_evidence")]),
            CORPUS,
        ),
        score_item(ITEMS["C01"], _answer("CONFLICT_REVIEW"), CORPUS),
        score_item(
            ITEMS["C02"], _answer("ANSWERED", [_claim("POL-RET@1.0#2.1", text="10 years")]), CORPUS
        ),
    ]
    m = summarise(rows, ITEMS)["metrics"]
    assert m["insufficient_evidence_accuracy"] == 0.5
    assert m["conflict_review_accuracy"] == 1.0
    assert m["unsupported_answer_rate"] == 0.5  # I02 shows an unverified claim
    assert m["superseded_citation_rate"] == 0.5  # C02 cites the superseded version


def test_every_reported_metric_is_labelled():
    assert {k for k, _ in ANSWER_ROWS} == set(METRIC_KIND)
    rows = [score_item(ITEMS["S01"], _answer("ANSWERED", [_claim("POL-DLP@2.1#4.2")]), CORPUS)]
    rep = {"provenance": {"mode": "offline", "generator": "offline-extractive", "embedding_model_id": "x",
                          "golden_items": 1, "golden_sha256": "0" * 64, "corpus_fingerprint": "0" * 64,
                          "config_sha256": "0" * 64, "prompt_version": "p"},
           "levels": {"advanced": {"items": rows, "summary": summarise(rows, ITEMS)}}}  # fmt: skip
    md = render_answers_markdown(rep)
    assert "not answer-quality results" in md and "JUDGE" in md
