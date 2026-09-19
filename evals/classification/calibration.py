"""Calibration metrics (ECE, Brier, reliability tables) and threshold sweeps.

Applies to any classifier whose results carry `scores`. Confidence kinds are never mixed: these
metrics describe the probabilities a classifier reports, and say whether they claim calibration.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from app.classification.policy import TaxonomyPolicy

from .metrics import category_metrics, high_risk_metrics
from .records import PredictionRecord


def _reliability(conf: np.ndarray, correct: np.ndarray, n_bins: int) -> dict[str, Any]:
    """Equal-width reliability table and ECE for confidences in [0, 1]."""
    n = len(conf)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.minimum(np.digitize(conf, edges[1:-1], right=False), n_bins - 1)
    rows, ece = [], 0.0
    for b in range(n_bins):
        mask = idx == b
        nb = int(mask.sum())
        if nb:
            mean_conf, acc = float(conf[mask].mean()), float(correct[mask].mean())
            ece += nb / n * abs(acc - mean_conf)
        else:
            mean_conf = acc = None
        rows.append(
            {"bin": f"[{edges[b]:.2f}, {edges[b + 1]:.2f}{']' if b == n_bins - 1 else ')'}",
             "n": nb, "mean_confidence": mean_conf, "accuracy": acc}
        )  # fmt: skip
    return {"n": n, "ece": float(ece), "bins": rows}


def calibration_metrics(
    records: Sequence[PredictionRecord], level_ids: list[str], category_ids: list[str], n_bins: int
) -> dict[str, Any] | None:
    """Level top-label calibration and per-category probability calibration, or None if the
    records carry no scores."""
    scored = [r for r in records if r.has_prediction and r.level_probs]
    if not scored:
        return None
    calibrated = all(r.scores_calibrated for r in scored)

    conf = np.array([max(r.level_probs.values()) for r in scored])
    correct = np.array([r.pred_level == r.gold_level for r in scored], dtype=float)
    y = np.array([[float(r.gold_level == lv) for lv in level_ids] for r in scored])
    p = np.array([[r.level_probs.get(lv, 0.0) for lv in level_ids] for r in scored])
    level = {
        **_reliability(conf, correct, n_bins),
        "brier_multiclass": float(((p - y) ** 2).sum(axis=1).mean()),
        "definition": "top-label confidence vs correctness; Brier summed over the 4 classes",
    }

    cat_records = [r for r in scored if r.category_probs]
    categories: dict[str, Any] | None = None
    if cat_records:
        probs, labels = [], []
        per: dict[str, dict[str, Any]] = {}
        for c in category_ids:
            pc = np.array([r.category_probs.get(c, 0.0) for r in cat_records])
            yc = np.array([float(c in r.gold_categories) for r in cat_records])
            probs.append(pc)
            labels.append(yc)
            per[c] = {
                "n_positive": int(yc.sum()),
                "mean_probability": float(pc.mean()),
                "prevalence": float(yc.mean()),
                "brier": float(((pc - yc) ** 2).mean()),
            }
        allp, ally = np.concatenate(probs), np.concatenate(labels)
        categories = {
            **_reliability(allp, ally, n_bins),
            "brier": float(((allp - ally) ** 2).mean()),
            "per_category": per,
            "definition": "all (document, category) pairs: probability vs label",
        }
    return {"calibrated_claim": calibrated, "level": level, "categories": categories}


def threshold_sweep(
    records: Sequence[PredictionRecord],
    policy: TaxonomyPolicy,
    thresholds: Sequence[float],
) -> list[dict[str, Any]]:
    """Re-derive categories and high-risk at each global category threshold.

    The level is kept as the classifier decided it. Nothing here selects an operating point; it
    exposes the trade-off so the operating point can be chosen later on development data.
    """
    usable = [r for r in records if r.has_prediction and r.category_probs]
    out = []
    for t in thresholds:
        gold_c = [r.gold_categories for r in usable]
        pred_c = [sorted(c for c, p in r.category_probs.items() if p >= t) for r in usable]
        hr_pred = [
            policy.derive_high_risk(r.pred_level, pc).value
            for r, pc in zip(usable, pred_c, strict=True)
        ]
        hr = high_risk_metrics([r.gold_high_risk for r in usable], hr_pred)
        cm = category_metrics(gold_c, pred_c, policy.category_ids)
        out.append(
            {
                "threshold": t,
                "category_macro_f1": cm["macro"]["f1"],
                "category_micro_precision": cm["micro"]["precision"],
                "category_micro_recall": cm["micro"]["recall"],
                "high_risk_precision": hr["precision"],
                "high_risk_recall": hr["recall"],
                "high_risk_f1": hr["f1"],
                "high_risk_false_positive_rate": hr["false_positive_rate"],
            }
        )
    return out
