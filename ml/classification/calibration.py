"""Platt (sigmoid) calibration with an honest fallback."""

from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


class PlattScaler:
    """Maps a decision score to a probability: `sigmoid(a * score + b)` fitted on held-out data.

    If the held-out data has too few positives or negatives the scaler is NOT fitted; `fitted` stays
    False and `transform` returns the raw sigmoid of the score, which callers must report as an
    UNCALIBRATED score.
    """

    def __init__(self, min_positives: int, min_negatives: int) -> None:
        self.min_positives = min_positives
        self.min_negatives = min_negatives
        self.a, self.b = 1.0, 0.0
        self.fitted = False
        self.n_positive = self.n_negative = 0

    def fit(self, scores: np.ndarray, y: np.ndarray) -> PlattScaler:
        y = np.asarray(y).astype(int)
        self.n_positive, self.n_negative = int(y.sum()), int(len(y) - y.sum())
        if self.n_positive < self.min_positives or self.n_negative < self.min_negatives:
            self.fitted = False
            return self
        lr = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000)
        lr.fit(np.asarray(scores, dtype=float).reshape(-1, 1), y)
        self.a, self.b = float(lr.coef_[0, 0]), float(lr.intercept_[0])
        self.fitted = True
        return self

    def transform(self, scores: np.ndarray) -> np.ndarray:
        s = np.asarray(scores, dtype=float)
        return sigmoid(self.a * s + self.b) if self.fitted else sigmoid(s)

    def describe(self) -> dict:
        return {
            "fitted": self.fitted,
            "a": self.a if self.fitted else None,
            "b": self.b if self.fitted else None,
            "n_positive": self.n_positive,
            "n_negative": self.n_negative,
        }
