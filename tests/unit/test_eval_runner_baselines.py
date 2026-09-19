"""Runner failure accounting and the three sanity baselines."""

from __future__ import annotations

import time

import pytest

from app.classification.schemas import (
    NO_CONFIDENCE,
    ClassificationResult,
    HighRisk,
    LevelPrediction,
    ReviewDecision,
    Telemetry,
)
from evals.classification.baselines import MajorityClassifier, OracleClassifier, RandomClassifier
from evals.classification.runner import run_classifier
from tests.helpers import LEVELS, mkdoc

P, I, C, H = LEVELS  # noqa: E741


@pytest.fixture()
def policy(bundle):
    return bundle.policy


def docs3():
    return [
        mkdoc("d1", level=H, cats=["PHI"], group="g1", tier="T2"),
        mkdoc("d2", level=I, group="g2"),
        mkdoc("d3", level=C, cats=["SOURCE_CODE"], group="g3", tier="T3", ambiguity_flag=True,
              annotation_notes="n", acceptable_alternative_levels=[I]),
    ]  # fmt: skip


class Scripted:
    """A classifier whose behaviour per doc is scripted by a function."""

    name, version = "scripted", "0"

    def __init__(self, fn):
        self.fn = fn

    def classify(self, request):
        return self.fn(request)

    def params(self):
        return {}


def ok(request, level=I, cats=(), policy=None, **overrides):
    from evals.classification.baselines import _result

    res = _result(request, policy, level, cats, "scripted@0")
    return res.model_copy(update=overrides) if overrides else res


# ---- oracle / majority / random -------------------------------------------------------------
def test_oracle_returns_gold_for_every_document(policy):
    docs = docs3()
    recs = run_classifier(OracleClassifier.from_docs(docs, policy), docs, policy)
    assert [(r.pred_level, r.pred_categories) for r in recs] == [
        (d.gold_level, sorted(d.gold_categories)) for d in sorted(docs, key=lambda d: d.doc_id)
    ]
    assert all(r.status == "ok" and r.has_prediction and not r.failed for r in recs)


def test_oracle_on_unknown_document_is_recorded_as_a_failure_not_a_crash(policy):
    oracle = OracleClassifier.from_docs(docs3()[:1], policy)
    recs = run_classifier(oracle, docs3(), policy)
    assert sum(r.failed for r in recs) == 2 and {r.failure for r in recs if r.failed} == {
        "exception:KeyError"
    }


def test_majority_picks_most_common_level_and_breaks_ties_upward(policy):
    train = [mkdoc(f"t{i}", level=lv) for i, lv in enumerate([I, I, C, C, P])]
    assert MajorityClassifier.from_docs(train, policy).params()["level"] == C  # tie I/C -> higher
    train = [mkdoc(f"t{i}", level=lv) for i, lv in enumerate([I, I, I, C, H])]
    assert MajorityClassifier.from_docs(train, policy).params()["level"] == I


def test_majority_categories_are_those_with_prevalence_at_least_half(policy):
    train = [
        mkdoc(f"t{i}", level=H, cats=["PHI"] if i < 2 else []) for i in range(4)
    ]  # PHI prevalence 0.5
    assert MajorityClassifier.from_docs(train, policy).params()["categories"] == ["PHI"]
    train = [mkdoc(f"t{i}", level=H, cats=["PHI"] if i < 1 else []) for i in range(4)]  # 0.25
    assert MajorityClassifier.from_docs(train, policy).params()["categories"] == []
    with pytest.raises(ValueError):
        MajorityClassifier.from_docs([], policy)


def test_majority_predicts_the_same_thing_for_every_document(policy):
    docs = docs3()
    recs = run_classifier(MajorityClassifier(I, [], policy), docs, policy)
    assert {(r.pred_level, tuple(r.pred_categories)) for r in recs} == {(I, ())}


def test_random_is_reproducible_per_document_and_independent_of_order(policy):
    docs = docs3()
    a = run_classifier(RandomClassifier(5, policy), docs, policy)
    b = run_classifier(RandomClassifier(5, policy), list(reversed(docs)), policy)
    assert [(r.doc_id, r.pred_level, r.pred_categories) for r in a] == [
        (r.doc_id, r.pred_level, r.pred_categories) for r in b
    ]
    many = [mkdoc(f"m{i}") for i in range(60)]
    x = run_classifier(RandomClassifier(1, policy), many, policy)
    y = run_classifier(RandomClassifier(2, policy), many, policy)
    assert [r.pred_level for r in x] != [r.pred_level for r in y]


def test_random_covers_the_label_space(policy):
    many = [mkdoc(f"m{i}") for i in range(400)]
    recs = run_classifier(RandomClassifier(3, policy), many, policy)
    assert {r.pred_level for r in recs} == set(LEVELS)
    assert {c for r in recs for c in r.pred_categories} == set(policy.category_ids)


def test_baseline_results_are_valid_contract_objects(policy):
    doc = docs3()[0]
    res = OracleClassifier.from_docs([doc], policy).classify(doc.to_request())
    assert isinstance(res, ClassificationResult) and res.level.confidence == NO_CONFIDENCE
    assert res.versions.classifier == "oracle@1.0" and res.high_risk.value is True


# ---- runner: every document is accounted for ------------------------------------------------
def test_one_record_per_document_sorted_by_id_with_gold_and_slice_fields(policy):
    docs = docs3()
    recs = run_classifier(MajorityClassifier(I, [], policy), docs, policy)
    assert [r.doc_id for r in recs] == ["d1", "d2", "d3"]
    r1, _, r3 = recs
    assert (r1.gold_level, r1.gold_categories, r1.gold_high_risk, r1.tier) == (
        H,
        ["PHI"],
        True,
        "T2",
    )
    assert r3.ambiguity_flag is True and r3.gold_high_risk is False


def test_exception_is_recorded_without_leaking_the_message(policy):
    secret = "SSN 912-34-5678 must never be logged"

    def boom(request):
        raise ValueError(secret)

    recs = run_classifier(Scripted(boom), docs3(), policy)
    assert len(recs) == 3 and all(r.status == "error" and r.failed for r in recs)
    assert {r.failure for r in recs} == {"exception:ValueError"}
    assert secret not in "".join(r.model_dump_json() for r in recs)


def test_result_for_the_wrong_request_or_content_is_a_contract_violation(policy):
    def wrong_id(request):
        return ok(request, policy=policy, request_id="someone-else")

    def wrong_hash(request):
        return ok(request, policy=policy, content_hash="0" * 64)

    assert {r.failure for r in run_classifier(Scripted(wrong_id), docs3(), policy)} == {
        "contract_violation:request_id"
    }
    assert {r.failure for r in run_classifier(Scripted(wrong_hash), docs3(), policy)} == {
        "contract_violation:content_hash"
    }


def test_labels_outside_the_taxonomy_are_not_usable_predictions(policy):
    def bad_level(request):
        return ok(request, policy=policy).model_copy(
            update={
                "level": LevelPrediction(
                    value="TOP_SECRET", confidence=NO_CONFIDENCE, decided_by="baseline"
                )
            }
        )

    recs = run_classifier(Scripted(bad_level), docs3(), policy)
    assert all(r.failure == "invalid_labels" and not r.has_prediction for r in recs)


def test_floor_violating_predictions_are_measured_not_rejected(policy):
    """A classifier that outputs INTERNAL + PHI is *wrong* (measured) but is still a prediction."""
    recs = run_classifier(Scripted(lambda rq: ok(rq, I, ["PHI"], policy)), docs3(), policy)
    assert all(
        r.has_prediction and r.pred_level == I and r.pred_categories == ["PHI"] for r in recs
    )
    assert all(r.pred_high_risk for r in recs)  # PHI is a configured high-risk category


@pytest.mark.parametrize("status", ["rejected", "error"])
def test_rejected_and_error_results_have_no_prediction(policy, status):
    def bad(request):
        return ClassificationResult(
            request_id=request.request_id,
            content_hash=request.document.content_hash(),
            status=status,
        )

    recs = run_classifier(Scripted(bad), docs3(), policy)
    assert all(r.failed and r.status == status and r.failure == f"no_label:{status}" for r in recs)


def test_review_required_with_a_provisional_label_is_scored_and_marked_deferred(policy):
    def deferred(request):
        return ClassificationResult(
            request_id=request.request_id, content_hash=request.document.content_hash(), status="review_required",
            level=LevelPrediction(value=C, confidence=NO_CONFIDENCE, decided_by="baseline"),
            review=ReviewDecision(required=True, reason_codes=["LOW_CONFIDENCE"], provisional=True),
        )  # fmt: skip

    recs = run_classifier(Scripted(deferred), docs3(), policy)
    assert all(
        r.deferred and r.has_prediction and r.review_required and r.pred_level == C for r in recs
    )

    def deferred_no_label(request):
        return ClassificationResult(
            request_id=request.request_id, content_hash=request.document.content_hash(), status="review_required",
            review=ReviewDecision(required=True, reason_codes=["LLM_UNAVAILABLE"]),
        )  # fmt: skip

    assert all(
        r.failed and r.deferred
        for r in run_classifier(Scripted(deferred_no_label), docs3(), policy)
    )


def test_high_risk_is_rederived_and_a_lying_classifier_is_flagged(policy):
    def liar(request):
        res = ok(request, I, [], policy)  # INTERNAL, no categories -> not high risk
        return res.model_copy(update={"high_risk": HighRisk(value=True, config_version="9.9.9")})

    recs = run_classifier(Scripted(liar), docs3(), policy)
    assert all(r.classifier_high_risk_mismatch and r.pred_high_risk is False for r in recs)


def test_latency_is_measured_by_the_harness_and_reported_latency_passes_through(policy):
    def slow(request):
        time.sleep(0.01)
        return ok(
            request,
            policy=policy,
            telemetry=Telemetry(latency_ms={"total": 3.5}, est_cost_usd=0.01),
        )

    recs = run_classifier(Scripted(slow), docs3(), policy)
    assert all(r.latency_ms >= 9.0 for r in recs)
    assert all(r.reported_latency_ms == 3.5 and r.est_cost_usd == 0.01 for r in recs)


def test_non_classifier_is_rejected(policy):
    with pytest.raises(TypeError):
        run_classifier(object(), docs3(), policy)
