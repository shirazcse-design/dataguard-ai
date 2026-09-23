"""Fairness & Inclusion probe: counterfactual name-swap invariance (docs/uc4/responsible-ai.md)."""

from __future__ import annotations

import pytest

from app.classification.service import ClassificationService
from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents
from evals.classification.fairness_probe import (
    NAME_PAIRS,
    find_named_person,
    probe_document,
    run_probe,
    swap_name,
)
from evals.classification.gold_review import REVIEW_SPLITS
from tests.helpers import mkdoc


# ---- name detection and swapping ---------------------------------------------------------------
def test_find_named_person_locates_the_first_capitalized_pair():
    doc = mkdoc(content="Manager notes: Jane Roe was informed of the policy change.")
    assert find_named_person(doc) == ("Jane", "Roe")


def test_find_named_person_returns_none_without_a_name():
    doc = mkdoc(content="Aggregated statistics show no individual-level data.")
    assert find_named_person(doc) is None


def test_swap_name_replaces_the_full_name_and_lone_mentions_only():
    text = "Jane Roe filed the report. Later, Roe confirmed the details with Jane."
    out = swap_name(text, ("Jane", "Roe"), ("Amara", "Okafor"))
    assert out == "Amara Okafor filed the report. Later, Okafor confirmed the details with Amara."


def test_swap_name_is_whole_word_and_does_not_touch_substrings():
    text = "Janet Roebuck met Jane Roe near Janesville."
    out = swap_name(text, ("Jane", "Roe"), ("Amara", "Okafor"))
    assert "Janet Roebuck" in out and "Janesville" in out and "Amara Okafor" in out


def test_the_name_pairs_are_distinct_and_not_empty():
    assert len(NAME_PAIRS) >= 5
    assert len(set(NAME_PAIRS)) == len(NAME_PAIRS)


# ---- probing a document ------------------------------------------------------------------------
@pytest.fixture(scope="module")
def svc():
    return ClassificationService(llm_mode="off")


def test_probing_a_document_with_no_name_returns_none(svc):
    doc = mkdoc(content="Quarterly revenue rose 4% across all regions this period, per the report.")
    assert probe_document(svc, doc, mode="rules") is None


def test_probing_a_named_document_classifies_every_substitute_and_compares_to_base(svc):
    doc = mkdoc(
        content=(
            "Employee record for Jane Roe. National ID: 905-37-6209. "
            "Jane Roe has worked in the Finance department since 2019."
        )
    )
    result = probe_document(svc, doc, mode="rules")
    assert result is not None
    assert result["original_name"] == "Jane Roe"
    assert result["n_variants"] == len(NAME_PAIRS)  # "Jane Roe" is not itself in the pool
    assert result["base_level"] is not None
    for v in result["changed"]:
        assert v["name"] != result["original_name"]


def test_a_name_identical_to_a_pool_pair_is_not_offered_as_its_own_substitute(svc):
    first, last = NAME_PAIRS[0]
    doc = mkdoc(content=f"Manager notes about {first} {last}, a member of the engineering team.")
    result = probe_document(svc, doc, mode="rules")
    assert result is not None
    assert result["n_variants"] == len(NAME_PAIRS) - 1
    assert all(v["name"] != f"{first} {last}" for v in result["changed"])


# ---- the aggregate run --------------------------------------------------------------------------
def test_run_probe_skips_undetected_documents_and_counts_them(svc):
    named = mkdoc(doc_id="d1", content="Notes on Jane Roe, effective immediately.")
    unnamed = mkdoc(doc_id="d2", content="Aggregated, de-identified statistics only.")
    report = run_probe(svc, [named, unnamed], mode="rules")
    assert report["n_probed"] == 1 and report["n_skipped_no_name_found"] == 1
    assert report["mode"] == "rules"
    assert len(report["name_pairs"]) == len(NAME_PAIRS)


def test_run_probe_reports_invariance_rate_and_never_divides_by_zero():
    class Stub:
        def classify(self, request):
            raise AssertionError("should not be called: no named documents")

    empty_report = run_probe(Stub(), [mkdoc(content="No names appear anywhere in this document.")])
    assert empty_report["n_probed"] == 0 and empty_report["invariance_rate"] is None


@pytest.mark.parametrize("mode", ["rules"])
def test_run_probe_on_real_pii_dev_documents_and_flag_any_finding(svc, mode):
    """A real run over the dev split's PII/PHI-style families. This does not assert 100% invariance
    (a genuine finding would be real information, not a test bug) - it asserts the report is well-formed
    and prints any finding so it is visible in CI logs rather than silently passing either way."""
    docs = [
        d
        for d in load_documents(DEFAULT_DATA_DIR, splits=list(REVIEW_SPLITS))
        if d.split == "dev" and d.tier != "T5"
    ]
    report = run_probe(svc, docs, mode=mode)
    assert report["n_probed"] + report["n_skipped_no_name_found"] == len(docs)
    if report["n_probed"]:
        assert 0.0 <= report["invariance_rate"] <= 1.0
    flagged = [r for r in report["documents"] if r["n_changed"]]
    if flagged:
        print(f"fairness probe: {len(flagged)} document(s) changed level/categories on a name swap")
