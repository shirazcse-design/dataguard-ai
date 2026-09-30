"""Deterministic tokenisation shared by the sparse index, query processing and the reranker."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+")

# A small, fixed English stopword list: enough to stop BM25 and the reranker's term coverage from
# rewarding function words. Deliberately short so it cannot remove policy vocabulary.
STOPWORDS = frozenset(
    """a an and are as at be been but by can could do does for from has have how i if in into is it
    its may me must my no not of on or our should so such than that the their them then there these
    they this to up was we what when where which who whom why will with you your""".split()  # noqa: SIM905
)


def stem(token: str) -> str:
    """A very light plural stemmer (`records` -> `record`, `policies` -> `policy`). Predictable
    beats clever here: every transformation is visible in the retrieval trace."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def terms(text: str) -> list[str]:
    """Content terms, in order, with repeats (BM25 needs term frequencies)."""
    return [stem(t) for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS]
