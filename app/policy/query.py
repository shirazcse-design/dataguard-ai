"""Deterministic query processing (Advanced/Agentic): configured phrase expansion.

Expansion only APPENDS configured terms (acronyms, synonyms, product names -> policy vocabulary); it
never rewrites or drops the user's words, so the original intent is always part of the query. The
added terms come from configuration, so they are safe to show in a trace (they are not user text).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessedQuery:
    original: str
    text: str  # what retrieval uses
    expansions: list[str]  # configured phrases that matched (safe to trace)
    added_terms: list[str]


def process_query(question: str, expansions: dict[str, list[str]], enabled: bool) -> ProcessedQuery:
    normalised = " ".join(question.split())
    if not enabled:
        return ProcessedQuery(question, normalised, [], [])
    lower = normalised.lower()
    matched: list[str] = []
    added: list[str] = []
    for phrase in sorted(expansions, key=lambda p: (-len(p), p)):
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase.lower())}(?![a-z0-9])", lower):
            matched.append(phrase)
            for term in expansions[phrase]:
                if term.lower() not in lower and term not in added:
                    added.append(term)
    text = normalised if not added else f"{normalised} ({'; '.join(added)})"
    return ProcessedQuery(question, text, matched, added)
