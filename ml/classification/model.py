"""Two-head supervised model: multinomial level head + one-vs-rest category heads (Platt)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from sklearn.linear_model import LogisticRegression

from app.classification.schemas import Document

from .calibration import PlattScaler, sigmoid
from .config import HeadConfig, MLConfig
from .features import FeatureBuilder

_ALPHA_TOKEN = re.compile(r"^[a-z]+(?: [a-z]+)?$")  # one or two alphabetic words; no digits, no '@'


def _lr(head: HeadConfig, seed: int) -> LogisticRegression:
    return LogisticRegression(
        C=head.C,
        class_weight=head.class_weight,
        max_iter=head.max_iter,
        solver="lbfgs",
        random_state=seed,
    )


@dataclass
class Prediction:
    level_probs: np.ndarray  # (n, n_levels), rows sum to 1
    level_calibrated: bool
    category_probs: np.ndarray  # (n, n_categories)
    category_calibrated: dict[str, bool]


@dataclass
class MLModel:
    cfg: MLConfig
    level_ids: list[str]
    category_ids: list[str]
    features: FeatureBuilder | None = field(default=None, repr=False)
    level_lr: LogisticRegression | None = field(default=None, repr=False)
    level_scalers: list[PlattScaler] = field(default_factory=list)
    cat_lrs: dict[str, LogisticRegression | None] = field(default_factory=dict, repr=False)
    cat_scalers: dict[str, PlattScaler] = field(default_factory=dict)

    # ---- fitting ----------------------------------------------------------------------------
    def fit_heads(
        self, docs: list[Document], levels: list[str], cats: list[list[str]]
    ) -> np.ndarray:
        """Fit features and both heads on `docs`; returns the feature matrix."""
        self.features = FeatureBuilder(self.cfg.features, self.cfg.seed).fit(docs)
        x = self.features.transform(docs)
        self.level_lr = _lr(self.cfg.level_head, self.cfg.seed).fit(x, levels)
        self.cat_lrs = {}
        for c in self.category_ids:
            y = np.array([c in cs for cs in cats], dtype=int)
            if y.sum() < 2 or (len(y) - y.sum()) < 2:
                self.cat_lrs[c] = None
                continue
            self.cat_lrs[c] = _lr(self.cfg.category_head, self.cfg.seed).fit(x, y)
        return x

    def calibrate(self, docs: list[Document], levels: list[str], cats: list[list[str]]) -> None:
        cc = self.cfg.calibration
        x = self.features.transform(docs)
        scores = self._level_scores(x)
        self.level_scalers = []
        for k, lv in enumerate(self.level_ids):
            y = np.array([g == lv for g in levels], dtype=int)
            self.level_scalers.append(
                PlattScaler(cc.min_positives, cc.min_negatives).fit(scores[:, k], y)
            )
        self.cat_scalers = {}
        for c in self.category_ids:
            y = np.array([c in cs for cs in cats], dtype=int)
            sc = PlattScaler(cc.min_positives, cc.min_negatives)
            if self.cat_lrs.get(c) is not None:
                sc.fit(self.cat_lrs[c].decision_function(x), y)
            self.cat_scalers[c] = sc

    # ---- scoring ----------------------------------------------------------------------------
    def _level_scores(self, x) -> np.ndarray:
        raw = self.level_lr.decision_function(x)
        order = [list(self.level_lr.classes_).index(lv) for lv in self.level_ids]
        return raw[:, order]

    def predict(self, docs: list[Document]) -> Prediction:
        x = self.features.transform(docs)
        scores = self._level_scores(x)
        level_cal = bool(self.level_scalers) and all(s.fitted for s in self.level_scalers)
        if level_cal:
            p = np.column_stack(
                [s.transform(scores[:, k]) for k, s in enumerate(self.level_scalers)]
            )
            p = p / p.sum(axis=1, keepdims=True)
        else:
            order = [list(self.level_lr.classes_).index(lv) for lv in self.level_ids]
            p = self.level_lr.predict_proba(x)[:, order]
        cat_p = np.zeros((len(docs), len(self.category_ids)))
        cal: dict[str, bool] = {}
        for j, c in enumerate(self.category_ids):
            lr = self.cat_lrs.get(c)
            if lr is None:
                cal[c] = False
                continue
            sc = self.cat_scalers.get(c)
            raw = lr.decision_function(x)
            if sc is not None and sc.fitted:
                cat_p[:, j] = sc.transform(raw)
                cal[c] = True
            else:
                cat_p[:, j] = sigmoid(raw)
                cal[c] = False
        return Prediction(p, level_cal, cat_p, cal)

    # ---- explanation (masked) ---------------------------------------------------------------
    def _top_words(self, x_row, coef: np.ndarray, n: int) -> list[tuple[str, float]]:
        lo, hi = self.features.blocks["word"]
        names = self.features.word_feature_names()
        row = x_row.tocoo()
        contrib: dict[int, float] = {}
        for j, v in zip(row.col, row.data, strict=True):
            if lo <= j < hi:
                contrib[j] = float(coef[j] * v)
        out: list[tuple[str, float]] = []
        limit = self.cfg.evidence.max_token_chars
        for j, w in sorted(contrib.items(), key=lambda kv: -kv[1]):
            if w <= 0:
                break
            token = str(names[j - lo])
            if len(token) <= limit and _ALPHA_TOKEN.match(token):
                out.append((token, w))
            if len(out) >= n:
                break
        return out

    def explain_level(self, doc: Document, level: str) -> list[tuple[str, float]]:
        x = self.features.transform([doc])
        k = list(self.level_lr.classes_).index(level)
        return self._top_words(x, self.level_lr.coef_[k], self.cfg.evidence.top_features)

    def explain_category(self, doc: Document, category: str) -> list[tuple[str, float]]:
        lr = self.cat_lrs.get(category)
        if lr is None:
            return []
        x = self.features.transform([doc])
        return self._top_words(x, lr.coef_[0], self.cfg.evidence.top_features)

    def describe_calibration(self) -> dict:
        return {
            "level": {
                lv: s.describe() for lv, s in zip(self.level_ids, self.level_scalers, strict=False)
            },
            "categories": {c: s.describe() for c, s in self.cat_scalers.items()},
        }
