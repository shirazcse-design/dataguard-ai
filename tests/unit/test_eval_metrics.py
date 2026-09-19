"""Metrics verified against hand-computed values and scikit-learn."""

from __future__ import annotations

import pytest
from sklearn.metrics import f1_score, precision_recall_fscore_support

from evals.classification.metrics import (
    category_metrics,
    compact,
    compute_metrics,
    coverage,
    high_risk_metrics,
    level_metrics,
    round_floats,
)
from evals.classification.records import NO_PREDICTION
from tests.helpers import CATS, LEVELS, mkrec

P, I, C, H = LEVELS  # noqa: E741


# ---- level: worked by hand ------------------------------------------------------------------
# gold = P P I I C C H H H H
# pred = P I I I C H H H -  P     ('-' = no prediction)
GOLD = [P, P, I, I, C, C, H, H, H, H]
PRED = [P, I, I, I, C, H, H, H, None, P]


def test_level_confusion_matrix_by_hand():
    m = level_metrics(GOLD, PRED, LEVELS)
    assert m["confusion_matrix"]["cols_predicted"] == [*LEVELS, NO_PREDICTION]
    assert m["confusion_matrix"]["values"] == [
        [1, 1, 0, 0, 0],  # gold PUBLIC
        [0, 2, 0, 0, 0],  # gold INTERNAL
        [0, 0, 1, 1, 0],  # gold CONFIDENTIAL
        [1, 0, 0, 2, 1],  # gold HIGHLY_CONFIDENTIAL (one predicted PUBLIC, one no-prediction)
    ]


def test_level_per_class_by_hand():
    pc = level_metrics(GOLD, PRED, LEVELS)["per_class"]
    assert (pc[P]["precision"], pc[P]["recall"], pc[P]["f1"]) == (0.5, 0.5, 0.5)
    assert pc[I]["precision"] == pytest.approx(2 / 3) and pc[I]["recall"] == 1.0
    assert pc[I]["f1"] == pytest.approx(0.8)
    assert (
        pc[C]["precision"] == 1.0 and pc[C]["recall"] == 0.5 and pc[C]["f1"] == pytest.approx(2 / 3)
    )
    assert pc[H]["precision"] == pytest.approx(2 / 3) and pc[H]["recall"] == 0.5
    assert pc[H]["f1"] == pytest.approx(4 / 7)
    assert [pc[lv]["support"] for lv in LEVELS] == [2, 2, 2, 4]
    assert [pc[lv]["predicted"] for lv in LEVELS] == [2, 3, 1, 3]


def test_level_macro_micro_accuracy_by_hand():
    m = level_metrics(GOLD, PRED, LEVELS)
    assert m["macro"]["f1"] == pytest.approx((0.5 + 0.8 + 2 / 3 + 4 / 7) / 4)
    assert m["macro"]["precision"] == pytest.approx((0.5 + 2 / 3 + 1 + 2 / 3) / 4)
    assert m["macro"]["recall"] == pytest.approx(0.625)
    assert m["macro"]["n_labels"] == 4
    assert m["micro"]["precision"] == pytest.approx(
        6 / 9
    )  # the no-prediction is not a false positive
    assert m["micro"]["recall"] == pytest.approx(0.6)
    assert m["micro"]["f1"] == pytest.approx(12 / 19)
    assert m["accuracy"] == pytest.approx(0.6) and m["n_no_prediction"] == 1 and m["n"] == 10


def test_level_ordinal_errors_by_hand():
    o = level_metrics(GOLD, PRED, LEVELS)["ordinal_errors"]
    assert o["under_classification_rate"] == pytest.approx(0.2)  # H->none, H->P
    assert o["severe_under_classification_rate"] == pytest.approx(0.2)
    assert o["over_classification_rate"] == pytest.approx(0.2)  # P->I, C->H


def test_level_macro_matches_scikit_learn_when_all_labels_supported():
    m = level_metrics(GOLD, PRED, LEVELS)
    y_pred = [p or NO_PREDICTION for p in PRED]
    p, r, f, _ = precision_recall_fscore_support(
        GOLD, y_pred, labels=LEVELS, average="macro", zero_division=0
    )
    assert (m["macro"]["precision"], m["macro"]["recall"], m["macro"]["f1"]) == pytest.approx(
        (p, r, f)
    )
    assert m["micro"]["f1"] == pytest.approx(f1_score(GOLD, y_pred, labels=LEVELS, average="micro"))


def test_level_unsupported_label_is_reported_and_excluded_from_macro():
    gold = [P, P, I, I]
    pred = [P, C, I, C]  # CONFIDENTIAL is predicted but never gold
    m = level_metrics(gold, pred, LEVELS)
    assert m["unsupported_labels"] == [C, H]
    assert m["macro"]["n_labels"] == 2
    assert m["per_class"][C]["support"] == 0 and m["per_class"][C]["recall"] is None
    assert m["per_class"][C]["predicted"] == 2 and m["per_class"][C]["precision"] == 0.0
    assert m["per_class"][H]["f1"] is None  # nothing gold, nothing predicted: undefined, not 0
    # PUBLIC: tp=1, predicted=1, support=2 -> f1 = 2/3.  INTERNAL: tp=1, predicted=1, support=2 -> 2/3.
    assert m["macro"]["f1"] == pytest.approx(2 / 3)


def test_level_all_predictions_missing_is_worst_case_not_skipped():
    m = level_metrics([P, I, H], [None, None, None], LEVELS)
    assert m["accuracy"] == 0.0 and m["n_no_prediction"] == 3 and m["macro"]["f1"] == 0.0
    assert m["ordinal_errors"]["severe_under_classification_rate"] == 1.0


def test_empty_input_is_all_undefined_not_a_crash():
    m = level_metrics([], [], LEVELS)
    assert m["n"] == 0 and m["accuracy"] is None and m["macro"]["f1"] is None
    c = category_metrics([], [], CATS)
    assert c["n"] == 0 and c["macro"]["f1"] is None and c["exact_match_ratio"] is None
    h = high_risk_metrics([], [])
    assert h["recall"] is None and h["accuracy"] is None
    assert compute_metrics([], LEVELS, CATS)["coverage"]["n_docs"] == 0


# ---- categories: worked by hand -------------------------------------------------------------
CG = [["PII"], ["PII", "PHI"], [], ["FINANCIAL_PCI"], ["PHI"]]
CP = [["PII"], ["PII"], ["FINANCIAL_PCI"], ["FINANCIAL_PCI", "PII"], []]


def test_category_per_label_by_hand():
    m = category_metrics(CG, CP, CATS)
    pii, phi, fin = (m["per_label"][c] for c in ("PII", "PHI", "FINANCIAL_PCI"))
    assert (pii["tp"], pii["fp"], pii["fn"], pii["tn"]) == (2, 1, 0, 2)
    assert (
        pii["precision"] == pytest.approx(2 / 3)
        and pii["recall"] == 1.0
        and pii["f1"] == pytest.approx(0.8)
    )
    assert (phi["tp"], phi["fp"], phi["fn"], phi["tn"]) == (0, 0, 2, 3)
    assert phi["precision"] is None and phi["recall"] == 0.0 and phi["f1"] == 0.0
    assert (fin["tp"], fin["fp"], fin["fn"], fin["tn"]) == (1, 1, 0, 3)
    assert fin["precision"] == 0.5 and fin["recall"] == 1.0 and fin["f1"] == pytest.approx(2 / 3)
    assert [pii["false_positive_rate"], phi["false_positive_rate"], fin["false_positive_rate"]] == [
        pytest.approx(1 / 3), 0.0, 0.25,
    ]  # fmt: skip


def test_category_macro_micro_and_document_level_stats_by_hand():
    m = category_metrics(CG, CP, CATS)
    assert m["macro"]["n_labels"] == 3  # only PII, PHI, FINANCIAL_PCI have gold support
    assert m["macro"]["f1"] == pytest.approx((0.8 + 0.0 + 2 / 3) / 3)
    assert m["macro"]["precision"] == pytest.approx((2 / 3 + 0.0 + 0.5) / 3)  # undefined -> 0
    assert m["macro"]["recall"] == pytest.approx(2 / 3)
    assert m["micro"] == {"precision": 0.6, "recall": 0.6, "f1": pytest.approx(0.6)}
    assert m["exact_match_ratio"] == pytest.approx(0.2)
    assert m["mean_gold_cardinality"] == 1.0 and m["mean_predicted_cardinality"] == 1.0
    assert m["docs_with_false_positive_category"] == 2
    assert set(m["unsupported_labels"]) == set(CATS) - {"PII", "PHI", "FINANCIAL_PCI"}


def test_category_macro_matches_scikit_learn_on_supported_labels():
    m = category_metrics(CG, CP, CATS)
    from sklearn.preprocessing import MultiLabelBinarizer

    mlb = MultiLabelBinarizer(classes=CATS)
    yt, yp = mlb.fit_transform(CG), mlb.transform(CP)
    idx = [CATS.index(c) for c in ("PII", "PHI", "FINANCIAL_PCI")]
    _, _, f, _ = precision_recall_fscore_support(
        yt[:, idx], yp[:, idx], average="macro", zero_division=0
    )
    assert m["macro"]["f1"] == pytest.approx(f)


def test_category_missing_prediction_counts_as_false_negatives():
    m = category_metrics([["PII"], ["PHI"]], [[], []], CATS)
    assert m["per_label"]["PII"]["fn"] == 1 and m["macro"]["recall"] == 0.0


# ---- high risk ------------------------------------------------------------------------------
def test_high_risk_by_hand():
    gold = [True, True, True, False, False, False, False, True]
    pred = [True, False, True, True, False, False, False, False]
    m = high_risk_metrics(gold, pred)
    assert (m["tp"], m["fn"], m["fp"], m["tn"]) == (2, 2, 1, 3)
    assert m["recall"] == 0.5 and m["precision"] == pytest.approx(2 / 3)
    assert m["f1"] == pytest.approx(4 / 7) and m["false_positive_rate"] == 0.25
    assert m["prevalence"] == 0.5 and m["accuracy"] == pytest.approx(5 / 8)


def test_high_risk_undefined_values_are_none_not_zero():
    m = high_risk_metrics([False, False], [False, False])
    assert m["recall"] is None and m["precision"] is None and m["f1"] is None
    assert m["false_positive_rate"] == 0.0 and m["prevalence"] == 0.0
    assert high_risk_metrics([True], [False])["recall"] == 0.0
    assert high_risk_metrics([False], [True])["false_positive_rate"] == 1.0


# ---- record-level helpers -------------------------------------------------------------------
def test_compute_metrics_treats_failures_as_misses_and_reports_coverage():
    records = [
        mkrec(H, H, ["PHI"], ["PHI"], hr=(True, True), group="a"),
        mkrec(H, None, ["PHI"], [], hr=(True, False), group="b", failure="exception:ValueError"),
    ]
    m = compute_metrics(records, LEVELS, CATS)
    assert m["coverage"]["n_failed"] == 1 and m["coverage"]["failure_reasons"] == {
        "exception:ValueError": 1
    }
    assert m["level"]["n_no_prediction"] == 1 and m["level"]["accuracy"] == 0.5
    assert m["categories"]["per_label"]["PHI"]["fn"] == 1
    assert m["high_risk"]["recall"] == 0.5


def test_coverage_counts_deferrals_and_autos():
    records = [
        mkrec(I, I, group="a"),
        mkrec(I, I, group="b", status="review_required", review_required=True),
        mkrec(I, None, group="c", status="rejected", failure="no_label:rejected"),
    ]
    c = coverage(records)
    assert (
        c["n_docs"],
        c["n_groups"],
        c["n_auto_decided"],
        c["n_deferred_to_review"],
        c["n_failed"],
    ) == (3, 3, 1, 1, 1)


def test_compact_extracts_headline_numbers_without_a_composite_score():
    m = compute_metrics([mkrec(P, P), mkrec(H, H, hr=(True, True), group="b")], LEVELS, CATS)
    c = compact(m)
    assert c["level_macro_f1"] == 1.0 and c["high_risk_recall"] == 1.0
    assert not any("composite" in k or "overall" in k or "score" in k for k in c)


def test_round_floats_and_nan_handling():
    assert round_floats({"a": 0.1 + 0.2, "b": [1.0000000000000002], "c": float("nan")}) == {
        "a": 0.3, "b": [1.0], "c": None,
    }  # fmt: skip
