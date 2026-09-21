"""The lenient level view: a level inside the gold's acceptable alternatives counts as correct.

It is reported BESIDE the strict headline, relaxes levels only (never categories or high-risk), and
never changes a strict number.
"""

from __future__ import annotations

import pytest

from app.classification.config_loader import load_config
from evals.classification.evaluate import _lenient_level, build_metrics
from tests.helpers import mkrec


@pytest.fixture(scope="module")
def bundle():
    return load_config()


def recs():
    out = []
    for i in range(
        6
    ):  # ambiguous family: gold CONFIDENTIAL, alternatives INTERNAL; model says INTERNAL
        out.append(
            mkrec(
                "CONFIDENTIAL",
                "INTERNAL",
                group="amb",
                doc_id=f"a{i}",
                gold_alternative_levels=["INTERNAL"],
            )
        )
    for i in range(6):  # clean family, correct
        out.append(mkrec("INTERNAL", "INTERNAL", group="ok", doc_id=f"o{i}"))
    for i in range(3):  # wrong and NOT an alternative: stays wrong under the lenient view
        out.append(
            mkrec(
                "CONFIDENTIAL",
                "PUBLIC",
                group="bad",
                doc_id=f"b{i}",
                gold_alternative_levels=["INTERNAL"],
            )
        )
    return out


def test_only_predictions_inside_the_alternatives_are_relaxed():
    lenient = _lenient_level(recs())
    assert [r.pred_level for r in lenient if r.family_id == "amb"] == ["CONFIDENTIAL"] * 6
    assert [r.pred_level for r in lenient if r.family_id == "bad"] == ["PUBLIC"] * 3
    assert [r.pred_level for r in lenient if r.family_id == "ok"] == ["INTERNAL"] * 6


def test_a_missing_prediction_is_never_relaxed_and_a_correct_one_is_untouched():
    rs = [
        mkrec("CONFIDENTIAL", None, group="x", doc_id="m", gold_alternative_levels=["INTERNAL"]),
        mkrec(
            "CONFIDENTIAL",
            "CONFIDENTIAL",
            group="x",
            doc_id="c",
            gold_alternative_levels=["INTERNAL"],
        ),
    ]
    out = _lenient_level(rs)
    assert out[0].pred_level is None and out[1].pred_level == "CONFIDENTIAL"


def test_categories_and_high_risk_are_never_relaxed():
    r = mkrec(
        "HIGHLY_CONFIDENTIAL", "CONFIDENTIAL", gold_cats=["PII"], pred_cats=[], hr=(True, False),
        gold_alternative_levels=["CONFIDENTIAL"],
    )  # fmt: skip
    (out,) = _lenient_level([r])
    assert out.pred_level == "HIGHLY_CONFIDENTIAL"
    assert out.pred_categories == [] and out.pred_high_risk is False and out.gold_high_risk is True


def test_the_view_is_reported_beside_an_unchanged_strict_headline(bundle):
    view = build_metrics(recs(), bundle)["headline"]["lenient_level_view"]
    strict = build_metrics(recs(), bundle)["headline"]["metrics"]["level"]
    assert view["n_relaxed"] == 6 and view["n_headline"] == 15
    assert strict["accuracy"] == pytest.approx(6 / 15)
    assert view["level_accuracy"] == pytest.approx(12 / 15)
    assert view["level_macro_f1"] > strict["macro"]["f1"]
    assert view["level_macro_f1_interval"]["point"] == pytest.approx(view["level_macro_f1"])


def test_without_alternatives_the_lenient_view_equals_the_strict_one(bundle):
    rs = [mkrec("INTERNAL", "PUBLIC", group=f"g{i}", doc_id=f"d{i}") for i in range(4)]
    view = build_metrics(rs, bundle)["headline"]["lenient_level_view"]
    assert view["n_relaxed"] == 0
    assert (
        view["level_accuracy"]
        == build_metrics(rs, bundle)["headline"]["metrics"]["level"]["accuracy"]
    )
