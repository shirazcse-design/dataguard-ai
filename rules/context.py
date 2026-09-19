"""Positive and negative context.

* Positive context: a keyword on the same line near a match, or a tabular header row naming the
  column (e.g. `ssn`, `routing`), promotes a bare pattern.
* Negative context (placeholder / test / documentation vocabulary near the match, in the document
  header zone, or in the filename) suppresses or demotes a detection. Negative context always wins.

Terms are literal phrases from config (never regex). They match on word boundaries with common
inflections, and are never matched when glued to a `.` or `@` (so the reserved `.example` domain
suffix is not mistaken for the placeholder word "example").
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .config import RulesConfig
from .preprocess import Doc

_SUFFIX = r"(?:s|es|d|ed|ing)?"


def term_regex(terms: Iterable[str]) -> re.Pattern[str]:
    parts = sorted({re.escape(t.lower()) for t in terms}, key=len, reverse=True)
    return re.compile(rf"(?<![A-Za-z0-9.@])(?:{'|'.join(parts)}){_SUFFIX}(?![A-Za-z0-9])", re.I)


class ContextMatcher:
    def __init__(self, cfg: RulesConfig) -> None:
        self.cfg = cfg
        self.lexicon: dict[str, re.Pattern[str]] = {
            name: term_regex(terms) for name, terms in cfg.lexicons.items()
        }
        c = cfg.context
        self._neg = {
            "placeholder": term_regex(c.placeholder_terms),
            "test": term_regex(c.test_terms),
            "documentation": term_regex(c.documentation_terms),
        }
        self._filename_neg = {t.lower() for t in c.filename_negative_tokens}
        self.window = c.window_chars
        self.header_chars = c.header_context_chars

    # ---- lexicon helpers ------------------------------------------------------------------
    def distinct_terms(self, doc: Doc, name: str) -> dict[str, int]:
        """Distinct lexicon terms found in the document -> first character offset."""
        found: dict[str, int] = {}
        for m in self.lexicon[name].finditer(doc.text):
            found.setdefault(m.group(0).lower(), m.start())
        return found

    # ---- positive context -----------------------------------------------------------------
    def near_positive(
        self,
        doc: Doc,
        start: int,
        end: int,
        lexicon: str,
        *,
        header: bool = True,
        before_only: bool = False,
    ) -> str | None:
        pattern = self.lexicon[lexicon]
        line_start, line_end = doc.line_bounds(start)
        lo = max(line_start, start - self.window)
        hi = start if before_only else min(line_end, end + self.window)
        hit = pattern.search(doc.lower, lo, hi)
        if hit:
            return hit.group(0)
        if header and doc.header_line:
            hit = pattern.search(doc.header_line)
            if hit:
                return f"header:{hit.group(0)}"
        return None

    # ---- negative context -----------------------------------------------------------------
    def near_negative(self, doc: Doc, start: int, end: int, classes: Iterable[str]) -> str | None:
        line_start, line_end = doc.line_bounds(start)
        lo, hi = max(line_start, start - self.window), min(line_end, end + self.window)
        for cls in classes:
            hit = self._neg[cls].search(doc.lower, lo, hi)
            if hit:
                return f"{cls}:{hit.group(0)}"
        return None

    def doc_negative(
        self, doc: Doc, classes: Iterable[str] = ("placeholder", "test")
    ) -> str | None:
        """Negative context that applies to the WHOLE document: its header zone or filename."""
        header = doc.lower[: self.header_chars]
        for cls in classes:
            hit = self._neg[cls].search(header)
            if hit:
                return f"header_{cls}:{hit.group(0)}"
        token = doc.filename_tokens & self._filename_neg
        if token:
            return f"filename:{sorted(token)[0]}"
        return None
