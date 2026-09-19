"""Classification metrics for the two-axis UC4 taxonomy.

Design rules:
* Confusion structures come from scikit-learn; precision/recall/F1 are derived from the resulting
  integer counts so that UNDEFINED values (empty denominators) are reported as `None`, never
  silently as 0.
* Macro averages are taken over labels with gold support > 0 in the evaluated subset (the
  `macro_convention` string travels with the numbers). An undefined *precision* of a supported
  label counts as 0 in the macro average, matching scikit-learn's zero_division=0.
* A missing prediction is a miss, never a skipped document: level -> the NO_PREDICTION column,
  categories -> empty set (false negatives), high-risk -> predicted negative.
* There is deliberately NO combined headline score; each axis and the high-risk metric stand alone.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from typing import Any

import numpy as np
from sklearn.metrics import confusion_matrix, multilabel_confusion_matrix
from sklearn.preprocessing import MultiLabelBinarizer

from .records import NO_PREDICTION, PredictionRecord

MACRO_CONVENTION = (
    "macro = unweighted mean over labels with gold support > 0 in the evaluated subset; an "
    "undefined precision of a supported label counts as 0"
)


def _div(num: float, den: float) -> float | None:
    return None if den == 0 else num / den


def _prf(tp: int, predicted: int, support: int) -> dict[str, float | None]:
    """Precision/recall/F1 from counts; F1 = 2TP / (predicted + support)."""
    return {
        "precision": _div(tp, predicted),
        "recall": _div(tp, support),
        "f1": _div(2 * tp, predicted + support),
    }


def _macro(per_label: dict[str, dict[str, Any]]) -> dict[str, Any]:
    supported = [m for m in per_label.values() if m["support"] > 0]
    if not supported:
        return {"precision": None, "recall": None, "f1": None, "n_labels": 0}
    return {
        "precision": float(np.mean([m["precision"] or 0.0 for m in supported])),
        "recall": float(np.mean([m["recall"] for m in supported])),
        "f1": float(np.mean([m["f1"] for m in supported])),
        "n_labels": len(supported),
    }


# ---------------------------------------------------------------------------------------------
# Sensitivity level (single label, ordinal)
# ---------------------------------------------------------------------------------------------
def level_metrics(
    gold: Sequence[str], pred: Sequence[str | None], level_ids: list[str]
) -> dict[str, Any]:
    """`level_ids` must be ordered by ascending rank."""
    n = len(gold)
    y_pred = [p if p is not None else NO_PREDICTION for p in pred]
    columns = [*level_ids, NO_PREDICTION]
    if n == 0:  # scikit-learn rejects empty input; an empty subset is a valid (all-undefined) case
        cm = np.zeros((len(level_ids), len(columns)), dtype=int)
    else:
        cm = confusion_matrix(list(gold), y_pred, labels=columns)[: len(level_ids), :]

    per_class: dict[str, dict[str, Any]] = {}
    for i, label in enumerate(level_ids):
        tp = int(cm[i, i])
        support = int(cm[i, :].sum())
        predicted = int(cm[:, i].sum())
        per_class[label] = {
            **_prf(tp, predicted, support),
            "support": support,
            "predicted": predicted,
            "tp": tp,
        }

    tp_total = sum(per_class[lv]["tp"] for lv in level_ids)
    predicted_total = sum(per_class[lv]["predicted"] for lv in level_ids)
    rank = {lv: i for i, lv in enumerate(level_ids)}
    under = severe = over = 0
    for g, p in zip(gold, pred, strict=True):
        if p is None:
            under += 1  # a missing prediction is treated as the worst case
            severe += 1
            continue
        gap = rank[g] - rank[p]
        under += gap > 0
        severe += gap >= 2
        over += gap < 0
    return {
        "n": n,
        "labels": level_ids,
        "confusion_matrix": {
            "rows_gold": level_ids,
            "cols_predicted": columns,
            "values": cm.tolist(),
        },
        "per_class": per_class,
        "macro": _macro(per_class),
        "micro": _prf(tp_total, predicted_total, n),
        "accuracy": _div(tp_total, n),
        "n_no_prediction": int(cm[:, -1].sum()),
        "unsupported_labels": [lv for lv in level_ids if per_class[lv]["support"] == 0],
        "ordinal_errors": {
            "under_classification_rate": _div(under, n),
            "severe_under_classification_rate": _div(severe, n),
            "over_classification_rate": _div(over, n),
            "definition": (
                "under: predicted rank < gold rank; severe: gap >= 2 ranks; missing predictions "
                "count as severe under-classification"
            ),
        },
        "macro_convention": MACRO_CONVENTION,
    }


# ---------------------------------------------------------------------------------------------
# Data categories (multilabel)
# ---------------------------------------------------------------------------------------------
def category_metrics(
    gold: Sequence[Sequence[str]], pred: Sequence[Sequence[str]], category_ids: list[str]
) -> dict[str, Any]:
    n = len(gold)
    mlb = MultiLabelBinarizer(classes=category_ids)
    y_true = mlb.fit_transform([list(g) for g in gold])
    y_pred = mlb.transform([list(p) for p in pred])
    if n == 0:  # scikit-learn rejects empty input
        mcm = np.zeros((len(category_ids), 2, 2), dtype=int)
    else:
        mcm = multilabel_confusion_matrix(y_true, y_pred)  # per label: [[tn, fp], [fn, tp]]

    per_label: dict[str, dict[str, Any]] = {}
    for i, label in enumerate(category_ids):
        (tn, fp), (fn, tp) = mcm[i]
        tn, fp, fn, tp = int(tn), int(fp), int(fn), int(tp)
        per_label[label] = {
            **_prf(tp, tp + fp, tp + fn),
            "support": tp + fn,
            "predicted": tp + fp,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "false_positive_rate": _div(fp, fp + tn),
        }
    tp_t = sum(m["tp"] for m in per_label.values())
    pred_t = sum(m["predicted"] for m in per_label.values())
    sup_t = sum(m["support"] for m in per_label.values())
    exact = int((y_true == y_pred).all(axis=1).sum()) if n else 0
    return {
        "n": n,
        "labels": category_ids,
        "per_label": per_label,
        "macro": _macro(per_label),
        "micro": _prf(tp_t, pred_t, sup_t),
        "exact_match_ratio": _div(exact, n),
        "mean_gold_cardinality": _div(int(y_true.sum()), n),
        "mean_predicted_cardinality": _div(int(y_pred.sum()), n),
        "docs_with_false_positive_category": int(((y_pred == 1) & (y_true == 0)).any(axis=1).sum()),
        "unsupported_labels": [c for c in category_ids if per_label[c]["support"] == 0],
        "macro_convention": MACRO_CONVENTION,
    }


# ---------------------------------------------------------------------------------------------
# High risk (derived from the configured definition; recall is reported on its own)
# ---------------------------------------------------------------------------------------------
def high_risk_metrics(gold: Sequence[bool], pred: Sequence[bool]) -> dict[str, Any]:
    n = len(gold)
    tp = sum(g and p for g, p in zip(gold, pred, strict=True))
    fp = sum((not g) and p for g, p in zip(gold, pred, strict=True))
    fn = sum(g and (not p) for g, p in zip(gold, pred, strict=True))
    tn = n - tp - fp - fn
    return {
        "n": n,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        **_prf(tp, tp + fp, tp + fn),
        "false_positive_rate": _div(fp, fp + tn),
        "prevalence": _div(tp + fn, n),
        "accuracy": _div(tp + tn, n),
    }


# ---------------------------------------------------------------------------------------------
# Whole-record helpers
# ---------------------------------------------------------------------------------------------
def coverage(records: Sequence[PredictionRecord]) -> dict[str, Any]:
    n = len(records)
    reasons = Counter(r.failure for r in records if r.failure)
    return {
        "n_docs": n,
        "n_groups": len({r.group_id for r in records}),
        "n_with_prediction": sum(r.has_prediction for r in records),
        "n_failed": sum(r.failed for r in records),
        "n_deferred_to_review": sum(r.deferred for r in records),
        "n_auto_decided": sum(r.status in ("ok", "degraded") and r.has_prediction for r in records),
        "failure_reasons": dict(sorted(reasons.items())),
        "classifier_high_risk_mismatches": sum(r.classifier_high_risk_mismatch for r in records),
    }


def compute_metrics(
    records: Sequence[PredictionRecord], level_ids: list[str], category_ids: list[str]
) -> dict[str, Any]:
    """All three metric families plus coverage for one set of records."""
    return {
        "coverage": coverage(records),
        "level": level_metrics(
            [r.gold_level for r in records],
            [r.pred_level if r.has_prediction else None for r in records],
            level_ids,
        ),
        "categories": category_metrics(
            [r.gold_categories for r in records],
            [r.pred_categories if r.has_prediction else [] for r in records],
            category_ids,
        ),
        "high_risk": high_risk_metrics(
            [r.gold_high_risk for r in records],
            [r.pred_high_risk if r.has_prediction else False for r in records],
        ),
    }


def compact(metrics: dict[str, Any]) -> dict[str, Any]:
    """The few numbers used for slices and alternative deferral views."""
    hr = metrics["high_risk"]
    return {
        "n_docs": metrics["coverage"]["n_docs"],
        "n_groups": metrics["coverage"]["n_groups"],
        "level_macro_f1": metrics["level"]["macro"]["f1"],
        "level_labels_supported": metrics["level"]["macro"]["n_labels"],
        "level_accuracy": metrics["level"]["accuracy"],
        "category_macro_f1": metrics["categories"]["macro"]["f1"],
        "category_labels_supported": metrics["categories"]["macro"]["n_labels"],
        "high_risk_recall": hr["recall"],
        "high_risk_precision": hr["precision"],
        "high_risk_false_positive_rate": hr["false_positive_rate"],
        "high_risk_positives": hr["tp"] + hr["fn"],
        "docs_with_false_positive_category": metrics["categories"][
            "docs_with_false_positive_category"
        ],
    }


def round_floats(obj: Any, digits: int = 12) -> Any:
    """Recursively round floats (used before hashing, to avoid last-digit platform noise)."""
    if isinstance(obj, float):
        return None if math.isnan(obj) else round(obj, digits)
    if isinstance(obj, dict):
        return {k: round_floats(v, digits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [round_floats(v, digits) for v in obj]
    return obj
