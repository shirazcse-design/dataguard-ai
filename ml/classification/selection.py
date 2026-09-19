"""Grouped cross-validation on the TRAIN split (hyperparameter selection)."""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.model_selection import GroupKFold

from app.classification.schemas import Document
from evals.classification.dataset.schema import DatasetDocument
from evals.classification.metrics import category_metrics, level_metrics

from .config import HeadConfig, MLConfig
from .features import FeatureBuilder
from .model import _lr


def cross_validate(
    cfg: MLConfig,
    train_docs: list[DatasetDocument],
    level_ids: list[str],
    category_ids: list[str],
) -> list[dict[str, Any]]:
    """Out-of-fold level and category macro-F1 for every C in the grid.

    Folds group by scenario family (`group_id`), so a family never spans train and validation.
    Vectorisers are fitted inside each fold. Predictions are raw (level argmax; category decision
    > 0), i.e. before calibration.
    """
    docs = [d.to_request().document for d in train_docs]
    groups = [d.group_id for d in train_docs]
    n = len(docs)
    grid = cfg.selection.c_grid
    oof_level = {c: [None] * n for c in grid}
    oof_cats = {c: [[] for _ in range(n)] for c in grid}

    splitter = GroupKFold(n_splits=min(cfg.selection.cv_folds, len(set(groups))))
    for tr, va in splitter.split(np.zeros(n), groups=groups):
        fb = FeatureBuilder(cfg.features, cfg.seed).fit([docs[i] for i in tr])
        x_tr = fb.transform([docs[i] for i in tr])
        x_va = fb.transform([docs[i] for i in va])
        y_level = [train_docs[i].gold_level for i in tr]
        for c in grid:
            lr = _lr(
                HeadConfig(
                    C=c, class_weight=cfg.level_head.class_weight, max_iter=cfg.level_head.max_iter
                ),
                cfg.seed,
            )
            lr.fit(x_tr, y_level)
            for i, lv in zip(va, lr.predict(x_va), strict=True):
                oof_level[c][i] = lv
            for cat in category_ids:
                y = np.array([cat in train_docs[i].gold_categories for i in tr], dtype=int)
                if y.sum() < 2 or len(y) - y.sum() < 2:
                    continue
                head = _lr(
                    HeadConfig(
                        C=c,
                        class_weight=cfg.category_head.class_weight,
                        max_iter=cfg.category_head.max_iter,
                    ),
                    cfg.seed,
                )
                head.fit(x_tr, y)
                for i, s in zip(va, head.decision_function(x_va), strict=True):
                    if s > 0:
                        oof_cats[c][i].append(cat)
    gold_level = [d.gold_level for d in train_docs]
    gold_cats = [d.gold_categories for d in train_docs]
    rows = []
    for c in grid:
        lm = level_metrics(gold_level, oof_level[c], level_ids)
        cm = category_metrics(gold_cats, oof_cats[c], category_ids)
        rows.append(
            {
                "C": c,
                "level_macro_f1": lm["macro"]["f1"],
                "level_accuracy": lm["accuracy"],
                "category_macro_f1": cm["macro"]["f1"],
                "category_micro_f1": cm["micro"]["f1"],
            }
        )
    return rows


def select_c(rows: list[dict[str, Any]], key: str) -> float:
    """Best C by `key`; ties go to the smaller C (more regularisation)."""
    best = max(rows, key=lambda r: (round(r[key], 12), -r["C"]))
    return best["C"]


def document_view(d: DatasetDocument) -> Document:
    return d.to_request().document


def leakage_diagnostic(
    cfg: MLConfig, train_docs: list[DatasetDocument], level_ids: list[str], category_ids: list[str]
) -> dict[str, Any]:
    """Not used for selection: in-sample vs random (leaky) vs grouped CV at the configured C.

    Documents in a family are variations of one template, so random folds put the same template on
    both sides and score near-perfectly; grouped folds are the honest estimate.
    """
    from sklearn.model_selection import KFold

    docs = [d.to_request().document for d in train_docs]
    n = len(docs)
    gl = [d.gold_level for d in train_docs]
    gc = [d.gold_categories for d in train_docs]

    def fit_predict(tr, va):
        fb = FeatureBuilder(cfg.features, cfg.seed).fit([docs[i] for i in tr])
        x_tr, x_va = fb.transform([docs[i] for i in tr]), fb.transform([docs[i] for i in va])
        lv = _lr(cfg.level_head, cfg.seed).fit(x_tr, [gl[i] for i in tr]).predict(x_va)
        cats = [[] for _ in va]
        for cat in category_ids:
            y = np.array([cat in gc[i] for i in tr], dtype=int)
            if y.sum() < 2 or len(y) - y.sum() < 2:
                continue
            for j, sc in enumerate(
                _lr(cfg.category_head, cfg.seed).fit(x_tr, y).decision_function(x_va)
            ):
                if sc > 0:
                    cats[j].append(cat)
        return list(lv), cats

    def score(pred_level, pred_cats):
        return {
            "level_macro_f1": level_metrics(gl, pred_level, level_ids)["macro"]["f1"],
            "category_macro_f1": category_metrics(gc, pred_cats, category_ids)["macro"]["f1"],
        }

    out = {"in_sample": score(*fit_predict(list(range(n)), list(range(n))))}
    for name, splitter, groups in (
        (
            "random_kfold_leaky",
            KFold(cfg.selection.cv_folds, shuffle=True, random_state=cfg.seed),
            None,
        ),
        (
            "grouped_kfold_honest",
            GroupKFold(n_splits=cfg.selection.cv_folds),
            [d.group_id for d in train_docs],
        ),
    ):
        pl, pc = [None] * n, [[] for _ in range(n)]
        it = splitter.split(np.zeros(n), groups=groups) if groups else splitter.split(np.zeros(n))
        for tr, va in it:
            lv, cats = fit_predict(list(tr), list(va))
            for i, a, b in zip(va, lv, cats, strict=True):
                pl[i], pc[i] = a, b
        out[name] = score(pl, pc)
    return out
