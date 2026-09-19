"""Cluster bootstrap confidence intervals.

Documents in a scenario family are variations of one template, so they are highly correlated and
the effective sample size is the number of families (groups). The default therefore resamples
GROUPS with replacement; `unit="document"` is available for comparison and is expected to give
narrower (overconfident) intervals.

The per-resample statistics are computed with a fast, independent numpy implementation; a test
asserts that on the full sample they equal the scikit-learn-based `metrics` module exactly.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from .dataset.rng import DetRandom
from .records import PredictionRecord


class _Arrays:
    def __init__(self, records: Sequence[PredictionRecord], level_ids: list[str], cats: list[str]):
        li = {lv: i for i, lv in enumerate(level_ids)}
        ci = {c: i for i, c in enumerate(cats)}
        n = len(records)
        self.n_levels, self.n_cats = len(level_ids), len(cats)
        self.g_level = np.array([li[r.gold_level] for r in records], dtype=int)
        self.p_level = np.array(
            [li[r.pred_level] if r.has_prediction and r.pred_level else -1 for r in records],
            dtype=int,
        )
        self.g_cat = np.zeros((n, len(cats)), dtype=bool)
        self.p_cat = np.zeros((n, len(cats)), dtype=bool)
        for k, r in enumerate(records):
            for c in r.gold_categories:
                self.g_cat[k, ci[c]] = True
            if r.has_prediction:
                for c in r.pred_categories:
                    self.p_cat[k, ci[c]] = True
        self.g_hr = np.array([r.gold_high_risk for r in records], dtype=bool)
        self.p_hr = np.array([r.pred_high_risk and r.has_prediction for r in records], dtype=bool)
        groups: dict[str, list[int]] = {}
        for k, r in enumerate(records):
            groups.setdefault(r.group_id, []).append(k)
        self.group_index = [np.array(v, dtype=int) for _, v in sorted(groups.items())]


def _macro_f1(tp: np.ndarray, predicted: np.ndarray, support: np.ndarray) -> float:
    mask = support > 0
    if not mask.any():
        return float("nan")
    return float(np.mean(2 * tp[mask] / (predicted[mask] + support[mask])))


def _stats(a: _Arrays, ix: np.ndarray) -> dict[str, float]:
    g, p = a.g_level[ix], a.p_level[ix]
    lv = np.arange(a.n_levels)
    tp = np.array([np.sum((g == k) & (p == k)) for k in lv])
    level_f1 = _macro_f1(
        tp, np.array([np.sum(p == k) for k in lv]), np.array([np.sum(g == k) for k in lv])
    )

    G, P = a.g_cat[ix], a.p_cat[ix]
    cat_f1 = _macro_f1((G & P).sum(0), P.sum(0), G.sum(0))

    gh, ph = a.g_hr[ix], a.p_hr[ix]
    tp_h, fn_h, fp_h = np.sum(gh & ph), np.sum(gh & ~ph), np.sum(~gh & ph)
    return {
        "level_macro_f1": level_f1,
        "category_macro_f1": cat_f1,
        "high_risk_recall": float(tp_h / (tp_h + fn_h)) if tp_h + fn_h else float("nan"),
        "high_risk_precision": float(tp_h / (tp_h + fp_h)) if tp_h + fp_h else float("nan"),
    }


def point_estimates(records, level_ids, category_ids) -> dict[str, float]:
    """The same statistics on the full sample (used to cross-check `metrics`)."""
    a = _Arrays(records, level_ids, category_ids)
    return _stats(a, np.arange(len(records)))


def bootstrap_intervals(
    records: Sequence[PredictionRecord],
    level_ids: list[str],
    category_ids: list[str],
    *,
    n_resamples: int,
    confidence_level: float,
    seed: int,
    unit: str = "group",
) -> dict[str, Any]:
    if unit not in ("group", "document"):
        raise ValueError("unit must be 'group' or 'document'")
    a = _Arrays(records, level_ids, category_ids)
    n_docs = len(records)
    units = len(a.group_index) if unit == "group" else n_docs
    point = _stats(a, np.arange(n_docs))
    draws: dict[str, list[float]] = {k: [] for k in point}
    undefined = dict.fromkeys(point, 0)
    for i in range(n_resamples):
        rng = DetRandom(seed, "bootstrap", unit, i)
        picks = [rng.randint(0, units - 1) for _ in range(units)]
        ix = (
            np.concatenate([a.group_index[k] for k in picks])
            if unit == "group"
            else np.array(picks)
        )
        stats = _stats(a, ix)
        for k, v in stats.items():
            if np.isnan(v):
                undefined[k] += 1
            else:
                draws[k].append(v)
    alpha = (1 - confidence_level) / 2 * 100
    out: dict[str, Any] = {}
    for k, values in draws.items():
        if values:
            low, high = np.percentile(values, [alpha, 100 - alpha])
        else:
            low = high = float("nan")
        out[k] = {
            "point": None if np.isnan(point[k]) else point[k],
            "ci_low": None if np.isnan(low) else float(low),
            "ci_high": None if np.isnan(high) else float(high),
            "n_undefined_resamples": undefined[k],
        }
    return {
        "unit": unit,
        "n_units": units,
        "n_resamples": n_resamples,
        "confidence_level": confidence_level,
        "seed": seed,
        "method": "percentile bootstrap",
        "statistics": out,
    }
