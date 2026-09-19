"""Abstention accounting and hard-negative (T4) metrics."""

from __future__ import annotations

import pytest

from app.classification.schemas import NO_CONFIDENCE, LevelPrediction, Routing
from evals.classification.baselines import _result
from evals.classification.metrics import coverage, hard_negative_metrics
from evals.classification.runner import run_classifier
from tests.helpers import mkdoc, mkrec

H, I = "HIGHLY_CONFIDENTIAL", "INTERNAL"  # noqa: E741


def t4(fid, decoy, pred_level, pred_cats=(), gold_level=I, hr=(False, False), **kw):
    return mkrec(gold_level, pred_level, [], pred_cats, group=fid, doc_id=f"{fid}-{len(str(kw))}",
                 hr=hr, tier="T4", decoy_for=decoy, **kw)  # fmt: skip


def test_hard_negative_metrics_by_hand():
    recs = [
        t4("fam_a", ["PII"], I, ["PII"], hr=(False, True)),  # predicts the decoy category
        t4("fam_b", ["FINANCIAL_PCI"], I),  # correctly quiet
        t4("fam_c", [H], H, hr=(False, True), gold_level=I),  # predicts the decoy LEVEL
        t4(
            "fam_d", ["SOURCE_CODE"], None, failure="exception:X"
        ),  # no prediction: not a false positive
        mkrec(H, H, ["PHI"], ["PHI"], group="other", tier="T1", hr=(True, True)),  # not T4: ignored
    ]
    m = hard_negative_metrics(recs)
    assert (m["n_docs"], m["n_families"]) == (4, 4)
    assert m["decoy_hit_rate"] == 0.5  # fam_a (category) and fam_c (level)
    assert m["any_false_positive_category_rate"] == 0.25  # only fam_a predicted a category
    assert m["high_risk_false_positive_rate"] == 0.5  # fam_a and fam_c predicted high-risk
    assert m["families_with_decoy_hits"] == {"fam_a": 1, "fam_c": 1}
    assert m["families_with_any_false_positive"] == {"fam_a": 1, "fam_c": 1}


def test_hard_negative_metrics_with_no_t4_documents():
    m = hard_negative_metrics([mkrec(I, I, tier="T1")])
    assert m["n_docs"] == 0 and m["decoy_hit_rate"] is None and m["families_with_decoy_hits"] == {}


def test_abstention_is_counted_in_coverage():
    recs = [mkrec(I, I, group="a", abstained=True), mkrec(I, I, group="b", abstained=True),
            mkrec(H, H, group="c"), mkrec(H, H, group="d")]  # fmt: skip
    c = coverage(recs)
    assert c["n_abstained"] == 2 and c["abstention_rate"] == 0.5


def test_abstention_rate_is_undefined_for_no_documents():
    assert coverage([])["abstention_rate"] is None


class _Abstainer:
    name, version = "abstainer", "0"

    def __init__(self, policy):
        self.policy = policy

    def classify(self, request):
        res = _result(request, self.policy, I, [], "abstainer@0")
        return res.model_copy(update={"routing": Routing(abstained=True, stop_reason="no_signal")})

    def params(self):
        return {}


def test_runner_carries_abstention_and_decoy_metadata_into_records(bundle):
    docs = [
        mkdoc("h1", level=I, group="g1", tier="T4", split="dev", decoy_for=["PII"]),
        mkdoc("h2", level=I, group="g2", tier="T1", split="dev"),
    ]
    recs = run_classifier(_Abstainer(bundle.policy), docs, bundle.policy)
    assert all(r.abstained for r in recs)
    assert {r.doc_id: r.decoy_for for r in recs} == {"h1": ["PII"], "h2": []}


def test_routing_abstained_defaults_to_false_and_validates():
    assert Routing().abstained is False and Routing(abstained=True).abstained is True
    with pytest.raises(ValueError):
        Routing(abstained="maybe-not")


def test_level_prediction_confidence_none_is_valid_for_defaulted_levels():
    """An abstaining approach reports its default level with confidence kind 'none'."""
    assert (
        LevelPrediction(value=I, confidence=NO_CONFIDENCE, decided_by="rules").confidence.kind
        == "none"
    )
