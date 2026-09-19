"""Calibration metrics, threshold sweeps, scores plumbing and the in-sample warning."""

from __future__ import annotations

import pytest

from app.classification.schemas import Scores
from evals.classification.baselines import OracleClassifier
from evals.classification.calibration import calibration_metrics, threshold_sweep
from evals.classification.evaluate import evaluate
from evals.classification.reporting import render_run_report
from tests.helpers import CATS, LEVELS, mkdoc, mkrec

P, I, C, H = LEVELS  # noqa: E741


def rec(gold, pred, probs, *, cat_probs=None, gold_cats=(), **kw):
    return mkrec(gold, pred, gold_cats, [c for c, p in (cat_probs or {}).items() if p >= 0.5],
                 level_probs=probs, category_probs=cat_probs, scores_calibrated=True, **kw)  # fmt: skip


def test_scores_schema_rules():
    assert Scores(level={"A": 0.5}, calibrated=True, calibration_ref="m1").calibrated
    with pytest.raises(ValueError, match="calibration_ref"):
        Scores(calibrated=True)
    with pytest.raises(ValueError, match="within"):
        Scores(level={"A": 1.5})


def test_level_ece_and_brier_by_hand():
    # 4 documents, 2 bins ([0,.5), [.5,1]).  Confidences .9 .9 .6 .4 ; correct 1 1 0 1
    recs = [
        rec(H, H, {P: 0.0, I: 0.0, C: 0.1, H: 0.9}, group="a", doc_id="1"),
        rec(H, H, {P: 0.0, I: 0.0, C: 0.1, H: 0.9}, group="b", doc_id="2"),
        rec(C, H, {P: 0.0, I: 0.0, C: 0.4, H: 0.6}, group="c", doc_id="3"),
        rec(I, I, {P: 0.3, I: 0.4, C: 0.3, H: 0.0}, group="d", doc_id="4"),
    ]
    m = calibration_metrics(recs, LEVELS, CATS, 2)
    lv = m["level"]
    # bin [0,.5): conf .4 acc 1 (n=1); bin [.5,1]: conf (.9+.9+.6)/3=.8 acc 2/3 (n=3)
    assert [b["n"] for b in lv["bins"]] == [1, 3]
    assert (
        lv["bins"][0]["mean_confidence"] == pytest.approx(0.4) and lv["bins"][0]["accuracy"] == 1.0
    )
    assert lv["bins"][1]["mean_confidence"] == pytest.approx(0.8) and lv["bins"][1][
        "accuracy"
    ] == pytest.approx(2 / 3)
    assert lv["ece"] == pytest.approx(1 / 4 * abs(1 - 0.4) + 3 / 4 * abs(2 / 3 - 0.8))
    brier1 = 0.1**2 + 0.1**2  # docs 1-2: H .9 vs 1, C .1 vs 0
    brier3 = (0.4 - 1) ** 2 + 0.6**2  # doc 3: gold C, C .4 and H .6
    brier4 = 0.3**2 + (0.4 - 1) ** 2 + 0.3**2  # doc 4: gold I
    assert m["calibrated_claim"] is True
    assert lv["brier_multiclass"] == pytest.approx((2 * brier1 + brier3 + brier4) / 4)


def test_category_calibration_and_perfect_case():
    recs = [
        rec(
            H,
            H,
            {H: 1.0},
            cat_probs={c: (1.0 if c == "PHI" else 0.0) for c in CATS},
            gold_cats=["PHI"],
            group="a",
            doc_id="1",
        ),
        rec(I, I, {I: 1.0}, cat_probs={c: 0.0 for c in CATS}, group="b", doc_id="2"),
    ]
    m = calibration_metrics(recs, LEVELS, CATS, 5)
    assert (
        m["level"]["ece"] == 0.0
        and m["categories"]["ece"] == 0.0
        and m["categories"]["brier"] == 0.0
    )
    assert m["categories"]["per_category"]["PHI"]["n_positive"] == 1


def test_no_scores_means_no_calibration_block():
    assert calibration_metrics([mkrec(I, I)], LEVELS, CATS, 5) is None


def test_uncalibrated_scores_do_not_claim_calibration():
    r = mkrec(
        I,
        I,
        level_probs={I: 0.9, C: 0.1},
        category_probs={c: 0.1 for c in CATS},
        scores_calibrated=False,
    )
    assert calibration_metrics([r], LEVELS, CATS, 5)["calibrated_claim"] is False


def test_threshold_sweep_by_hand(bundle):
    # PHI probabilities .9 / .4 / .2 with gold PHI, PHI, none ; level INTERNAL for all
    def r(i, p, gold):
        return rec(I, I, {I: 1.0}, cat_probs={c: (p if c == "PHI" else 0.0) for c in CATS},
                   gold_cats=gold, group=f"g{i}", doc_id=f"d{i}", hr=(bool(gold), False))  # fmt: skip

    recs = [r(1, 0.9, ["PHI"]), r(2, 0.4, ["PHI"]), r(3, 0.2, [])]
    rows = {x["threshold"]: x for x in threshold_sweep(recs, bundle.policy, [0.3, 0.5])}
    assert (
        rows[0.3]["high_risk_recall"] == 1.0 and rows[0.3]["high_risk_false_positive_rate"] == 0.0
    )
    assert rows[0.5]["high_risk_recall"] == 0.5 and rows[0.5]["high_risk_precision"] == 1.0
    assert rows[0.5]["category_micro_recall"] == 0.5


def test_in_sample_warning_appears_when_evaluating_fitted_splits(bundle):
    class Fitted(OracleClassifier):
        def params(self):
            return {"fit_splits": ["dev"], "calibration_splits": ["calibration"]}

    docs = [mkdoc("a", level=I, split="dev")]
    manifest = {"dataset_id": "t", "dataset_version": "1", "dataset_sha256": "a" * 64, "spec_hash": "b" * 64,
                "label_status": "AI-generated synthetic dataset — pending human gold-label review"}  # fmt: skip
    res = evaluate(Fitted.from_docs(docs, bundle.policy), docs, bundle, manifest)
    text = render_run_report(res)
    assert "IN-SAMPLE WARNING" in text and "fitted on" in text
    clean = evaluate(OracleClassifier.from_docs(docs, bundle.policy), docs, bundle, manifest)
    assert "IN-SAMPLE WARNING" not in render_run_report(clean)
