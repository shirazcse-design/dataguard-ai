"""Cluster bootstrap: correctness, determinism and the reason it clusters by family."""

from __future__ import annotations

import math

import pytest

from evals.classification.bootstrap import bootstrap_intervals, point_estimates
from evals.classification.metrics import compute_metrics
from tests.helpers import CATS, LEVELS, mkrec

P, I, C, H = LEVELS  # noqa: E741


def _mixed_records():
    """A small but non-trivial record set spread over several groups."""
    recs = []
    spec = [
        ("a", H, H, ["PHI"], ["PHI"], (True, True)), ("a", H, H, ["PHI"], ["PHI", "PII"], (True, True)),
        ("b", C, I, ["SOURCE_CODE"], [], (False, False)), ("b", C, C, ["SOURCE_CODE"], ["SOURCE_CODE"], (False, False)),
        ("c", I, I, [], [], (False, False)), ("c", I, C, [], ["SOURCE_CODE"], (False, False)),
        ("d", P, P, [], [], (False, False)), ("d", P, H, [], ["PII"], (False, True)),
        ("e", H, P, ["TRADE_SECRET"], [], (True, False)), ("e", H, H, ["TRADE_SECRET"], ["TRADE_SECRET"], (True, True)),
        ("f", C, C, ["INTELLECTUAL_PROPERTY"], ["INTELLECTUAL_PROPERTY"], (False, False)),
    ]  # fmt: skip
    for k, (g, gl, pl, gc, pc, hr) in enumerate(spec):
        recs.append(mkrec(gl, pl, gc, pc, group=g, doc_id=f"x{k}", hr=hr))
    return recs


def test_fast_point_estimates_equal_the_scikit_learn_based_metrics_exactly():
    recs = _mixed_records()
    fast = point_estimates(recs, LEVELS, CATS)
    slow = compute_metrics(recs, LEVELS, CATS)
    assert fast["level_macro_f1"] == pytest.approx(slow["level"]["macro"]["f1"], abs=1e-12)
    assert fast["category_macro_f1"] == pytest.approx(slow["categories"]["macro"]["f1"], abs=1e-12)
    assert fast["high_risk_recall"] == pytest.approx(slow["high_risk"]["recall"], abs=1e-12)
    assert fast["high_risk_precision"] == pytest.approx(slow["high_risk"]["precision"], abs=1e-12)


def test_fast_and_slow_agree_when_predictions_are_missing():
    recs = _mixed_records()
    recs[0] = mkrec(
        H, None, ["PHI"], [], group="a", doc_id="x0", hr=(True, False), failure="exception:X"
    )
    fast = point_estimates(recs, LEVELS, CATS)
    slow = compute_metrics(recs, LEVELS, CATS)
    assert fast["level_macro_f1"] == pytest.approx(slow["level"]["macro"]["f1"], abs=1e-12)
    assert fast["category_macro_f1"] == pytest.approx(slow["categories"]["macro"]["f1"], abs=1e-12)


def _ci(recs, **kw):
    args = dict(n_resamples=300, confidence_level=0.95, seed=7, unit="group")
    args.update(kw)
    return bootstrap_intervals(recs, LEVELS, CATS, **args)


def test_intervals_are_deterministic_and_seed_sensitive():
    recs = _mixed_records()
    assert _ci(recs) == _ci(recs)
    assert _ci(recs)["statistics"] != _ci(recs, seed=8)["statistics"]


def test_point_estimate_is_reported_and_intervals_are_ordered():
    out = _ci(_mixed_records())
    assert out["unit"] == "group" and out["n_units"] == 6 and out["n_resamples"] == 300
    for name, s in out["statistics"].items():
        assert s["ci_low"] <= s["ci_high"], name
        assert s["point"] is not None


def test_perfect_predictions_have_a_degenerate_interval_at_one():
    recs = [
        mkrec(H, H, ["PHI"], ["PHI"], group=g, doc_id=f"p{k}", hr=(True, True))
        for k, g in enumerate("abcd")
    ]
    s = _ci(recs)["statistics"]
    assert (s["level_macro_f1"]["ci_low"], s["level_macro_f1"]["ci_high"]) == (1.0, 1.0)
    assert s["high_risk_recall"]["ci_low"] == 1.0


def test_clustering_widens_the_interval_when_documents_within_a_group_are_correlated():
    """Whole families are either right or wrong, so document-level resampling is overconfident."""
    recs = []
    for g in range(12):
        right = g % 2 == 0
        for d in range(8):
            recs.append(mkrec(H, H if right else P, ["PHI"], ["PHI"] if right else [], group=f"g{g}",
                              doc_id=f"g{g}d{d}", hr=(True, right)))  # fmt: skip
    by_group = _ci(recs, unit="group")["statistics"]["high_risk_recall"]
    by_doc = _ci(recs, unit="document")["statistics"]["high_risk_recall"]
    width = lambda s: s["ci_high"] - s["ci_low"]  # noqa: E731
    assert width(by_group) > 1.5 * width(by_doc)


def test_undefined_resamples_are_counted_not_hidden():
    # Group "pos" holds all the high-risk positives; group "neg" none. Resampling only "neg"
    # leaves recall undefined, and that must be reported.
    recs = [
        mkrec(H, H, group="pos", doc_id="p1", hr=(True, True)),
        mkrec(P, P, group="neg", doc_id="n1", hr=(False, False)),
    ]
    s = _ci(recs, n_resamples=200)["statistics"]["high_risk_recall"]
    assert s["n_undefined_resamples"] > 0
    assert s["ci_low"] == 1.0  # defined resamples all have recall 1


def test_invalid_unit_rejected():
    with pytest.raises(ValueError, match="unit"):
        _ci(_mixed_records(), unit="family")


def test_interval_contains_point_for_a_well_behaved_sample():
    recs = _mixed_records() * 1
    s = _ci(recs, n_resamples=500)["statistics"]
    for name in ("level_macro_f1", "high_risk_recall"):
        assert s[name]["ci_low"] - 0.2 <= s[name]["point"] <= s[name]["ci_high"] + 0.2
    assert not math.isnan(s["category_macro_f1"]["point"])
