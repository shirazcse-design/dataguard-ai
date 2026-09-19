"""Cross-cutting markings: confidentiality banners, embedded labels, filename tokens.

Embedded labels and banners can RAISE the level floor. They never lower it: a PUBLIC/INTERNAL
label is not evidence of low sensitivity (labels go stale, and a spoofable 'public' marker must
not downgrade).
"""

from __future__ import annotations

from ..context import term_regex
from ..masking import phrase
from ..types import DetectorOutput
from .base import Detector, ScanContext

_FILENAME_LEVEL_TOKENS = {
    "confidential": "CONFIDENTIAL",
    "restricted": "HIGHLY_CONFIDENTIAL",
    "secret": "HIGHLY_CONFIDENTIAL",
}


def banner_lines(c: ScanContext) -> list[tuple[str, int]]:
    """Short lines among the first few non-empty lines, as (text, offset in the document)."""
    d, out = c.doc, []
    limit = c.cfg.existing_labels.banner_max_line_chars
    offset = seen = 0
    for line in d.text.split("\n", 30):
        stripped = line.strip()
        if stripped:
            seen += 1
            if len(stripped) <= limit:
                out.append((stripped, d.text.find(stripped, offset)))
            if seen >= c.cfg.existing_labels.banner_lines:
                break
        offset += len(line) + 1
    return out


def marking_fraction(pattern, text: str) -> float:
    """Share of the line taken up by the longest marking phrase it contains (0 if none)."""
    best = 0
    for hit in pattern.finditer(text):
        best = max(best, len(hit.group(0)))
    return best / len(text) if text else 0.0


class Banner(Detector):
    id, category = "mark.banner", None

    def setup(self) -> None:
        self._levels = {
            level: term_regex(terms) for level, terms in self.cfg.existing_labels.levels.items()
        }

    def _classify(self, text: str, *, min_fraction: float = 0.0) -> str | None:
        """The HIGHEST level whose banner vocabulary makes up at least `min_fraction` of `text`."""
        best: str | None = None
        for level, pattern in self._levels.items():
            if marking_fraction(pattern, text) <= min_fraction and not (
                min_fraction == 0.0 and pattern.search(text)
            ):
                continue
            if best is None or self.level_rank[level] > self.level_rank[best]:
                best = level
        return best

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d = DetectorOutput(), c.doc
        min_fraction = c.cfg.existing_labels.banner_min_marking_fraction
        # 1) banner lines: short lines among the first few that are mostly the marking itself
        for stripped, start in banner_lines(c):
            level = self._classify(stripped, min_fraction=min_fraction)
            if level:
                out.detections.append(
                    self.found(
                        c,
                        strength="strong",
                        kind="dictionary_hit",
                        start=start,
                        end=start + len(stripped),
                        masked=f"[BANNER: {phrase(stripped, 50)}]",
                        axis="level",
                        value=level,
                    )
                )
        # 2) embedded labels (metadata): dedicated fields, so no fraction requirement
        for scheme, value in d.existing_labels:
            level = self._classify(value)
            if level:
                out.detections.append(
                    self.found(
                        c,
                        strength="strong",
                        kind="existing_label",
                        start=0,
                        end=min(1, len(d.text)),
                        masked=f"[LABEL {scheme}={phrase(value, 40)}]",
                        axis="level",
                        value=level,
                        source="metadata",
                    )
                )
        return out


class Filename(Detector):
    id, category = "mark.filename", None

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d = DetectorOutput(), c.doc
        for token in sorted(d.filename_tokens & set(_FILENAME_LEVEL_TOKENS)):
            out.detections.append(
                self.found(
                    c,
                    strength="weak",
                    kind="filename_signal",
                    start=0,
                    end=min(1, len(d.text)),
                    masked=f"[FILENAME-TOKEN: {token}]",
                    axis="level",
                    value=_FILENAME_LEVEL_TOKENS[token],
                    source="metadata",
                )
            )
        return out
