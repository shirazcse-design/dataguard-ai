"""The vector store interface and its local implementation.

`VectorStore` is the seam where a managed store (e.g. Azure AI Search) would plug in later; the
retriever depends only on this interface. `LocalVectorStore` is an exact (brute-force) cosine search
over an in-memory matrix: at ~75 chunks an approximate index would add complexity and no speed.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np


class VectorStore(Protocol):
    def add(self, ids: list[str], vectors: np.ndarray) -> None: ...

    def search(
        self, query: np.ndarray, k: int, allowed: set[str] | None = None
    ) -> list[tuple[str, float]]:
        """Up to `k` `(id, cosine)` pairs, best first, restricted to `allowed` when given."""
        ...


class LocalVectorStore:
    def __init__(self) -> None:
        self._ids: list[str] = []
        self._m: np.ndarray | None = None

    def __len__(self) -> int:
        return len(self._ids)

    def add(self, ids: list[str], vectors: np.ndarray) -> None:
        if len(ids) != len(vectors):
            raise ValueError("ids and vectors differ in length")
        if set(ids) & set(self._ids) or len(set(ids)) != len(ids):
            raise ValueError("duplicate id in vector store")
        v = np.asarray(vectors, dtype=np.float32)
        self._m = v if self._m is None else np.vstack([self._m, v])
        self._ids.extend(ids)

    def search(
        self, query: np.ndarray, k: int, allowed: set[str] | None = None
    ) -> list[tuple[str, float]]:
        if self._m is None:
            return []
        sims = self._m @ np.asarray(query, dtype=np.float32).reshape(-1)
        order = np.argsort(-sims, kind="stable")
        out: list[tuple[str, float]] = []
        for i in order:
            cid = self._ids[i]
            if allowed is None or cid in allowed:
                out.append((cid, float(sims[i])))
                if len(out) == k:
                    break
        return out
