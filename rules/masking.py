"""Masking of matched values for evidence. Raw sensitive values are never exposed.

Value-type detections (identifiers, card/bank numbers, secrets) get a masked excerpt; keyword and
phrase detections show only the matched term, which is not sensitive by itself.
"""

from __future__ import annotations

import hashlib


def excerpt_hash(raw: str) -> str:
    """Correlation hash of the raw match (lets a reviewer tell two matches apart)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mask_keep_tail(raw: str, keep: int = 4, label: str = "") -> str:
    """`**** **** **** 1234`-style masking that preserves separators and the last `keep` digits."""
    total_alnum = sum(c.isalnum() for c in raw)
    seen = 0
    out = []
    for ch in raw:
        if ch.isalnum():
            seen += 1
            out.append(ch if seen > total_alnum - keep else "*")
        else:
            out.append(ch)
    masked = "".join(out)
    return f"{label}{masked}" if label else masked


def mask_full(raw: str, label: str) -> str:
    """Fully redacted, length-class only (used for secrets and identifiers)."""
    return f"[{label}:{'*' * min(len(raw), 8)}(len={len(raw)})]"


def phrase(raw: str, limit: int = 60) -> str:
    """A non-sensitive matched phrase, single-line and truncated."""
    one = " ".join(raw.split())
    return one if len(one) <= limit else one[: limit - 1] + "…"
