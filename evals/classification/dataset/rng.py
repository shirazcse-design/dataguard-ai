"""Deterministic random source.

Only `random.Random.random()` (Mersenne Twister, stable across Python versions) is used as the
primitive; every other operation is derived from it. This keeps generated datasets byte-identical
across Python 3.11/3.12/... instead of depending on the internals of `randrange`/`shuffle`.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def seed_from(*parts: object) -> int:
    """Stable 64-bit seed from arbitrary parts (independent of PYTHONHASHSEED)."""
    digest = hashlib.sha256(":".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


class DetRandom:
    def __init__(self, *seed_parts: object) -> None:
        self._r = random.Random(seed_from(*seed_parts))

    def random(self) -> float:
        return self._r.random()

    def randint(self, lo: int, hi: int) -> int:
        """Uniform integer in [lo, hi] inclusive."""
        if hi < lo:
            raise ValueError(f"empty range [{lo}, {hi}]")
        return min(hi, lo + int(self.random() * (hi - lo + 1)))

    def chance(self, p: float) -> bool:
        return self.random() < p

    def choice(self, seq: Sequence[T]) -> T:
        if not seq:
            raise ValueError("cannot choose from an empty sequence")
        return seq[self.randint(0, len(seq) - 1)]

    def shuffle(self, items: list[T]) -> None:
        for i in range(len(items) - 1, 0, -1):
            j = self.randint(0, i)
            items[i], items[j] = items[j], items[i]

    def sample(self, seq: Sequence[T], k: int) -> list[T]:
        pool = list(seq)
        if k > len(pool):
            raise ValueError("sample larger than population")
        out: list[T] = []
        for _ in range(k):
            out.append(pool.pop(self.randint(0, len(pool) - 1)))
        return out
