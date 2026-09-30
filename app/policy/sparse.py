"""Okapi BM25 over the chunks' `index_text` (the keyword/sparse retrieval leg)."""

from __future__ import annotations

import math
from collections import Counter

from .text import terms


class BM25Index:
    def __init__(self, ids: list[str], texts: list[str], *, k1: float, b: float) -> None:
        self.ids = ids
        self.k1, self.b = k1, b
        self._tf = [Counter(terms(t)) for t in texts]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg = sum(self._len) / max(len(self._len), 1)
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = len(texts)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def scores(self, query: str) -> list[float]:
        q = set(terms(query))
        out = []
        for tf, length in zip(self._tf, self._len, strict=True):
            s = 0.0
            for t in q:
                f = tf.get(t)
                if f:
                    norm = self.k1 * (1 - self.b + self.b * length / self._avg)
                    s += self._idf[t] * f * (self.k1 + 1) / (f + norm)
            out.append(s)
        return out
