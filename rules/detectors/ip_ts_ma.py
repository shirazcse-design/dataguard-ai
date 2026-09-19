"""Intellectual Property, Trade Secret and M&A detectors.

These categories are largely SEMANTIC. Rules can only see explicit markers ("invention
disclosure", a "TRADE SECRET" banner, "letter of intent") and vocabulary combinations. Marker hits
are strong; vocabulary alone is weak and never asserts a category.
"""

from __future__ import annotations

import re

from ..context import term_regex
from ..masking import phrase
from ..types import DetectorOutput
from .base import Detector, ScanContext
from .markings import banner_lines, marking_fraction

_CLAIM = re.compile(r"(?im)^[ \t]*1\.[ \t]+A[ \t]+(?:system|method|device|apparatus|composition)\b")


class IpMarkers(Detector):
    id, category = "ip.markers", "INTELLECTUAL_PROPERTY"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        hits = m.distinct_terms(d, "ip_markers")
        claim = _CLAIM.search(d.text)
        if not hits and not claim:
            return out
        negative = m.distinct_terms(d, "ip_negative_terms")
        first = min(hits.values()) if hits else claim.start()
        if negative:
            out.suppressions.append(
                self.suppressed(f"published_context:{sorted(negative)[0]}", first, first + 1)
            )
            return out
        term = sorted(hits, key=hits.get)[0] if hits else "claim format"
        out.detections.append(
            self.found(
                c,
                strength="strong",
                kind="dictionary_hit",
                start=first,
                end=first + 1,
                masked=f"[IP-MARKER: {phrase(term)}]",
            )
        )
        return out


class IpNovelty(Detector):
    id, category = "ip.novelty_language", "INTELLECTUAL_PROPERTY"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d = DetectorOutput(), c.doc
        hits = c.matcher.distinct_terms(d, "ip_novelty_terms")
        if len(hits) >= 2:
            pos = min(hits.values())
            out.detections.append(
                self.found(
                    c,
                    strength="weak",
                    kind="dictionary_hit",
                    start=pos,
                    end=pos + 1,
                    masked=f"[NOVELTY-LANGUAGE n={len(hits)}]",
                )
            )
        return out


class TsMarkers(Detector):
    """A "trade secret" BANNER or embedded label (the catalog's marker), not the phrase anywhere.

    Restricting the marker to the banner zone matters: attacker- or author-controlled body text can
    contain the words "trade secret" (for example a prompt-injection sentence) and must not steer a
    keyword rule into asserting the category.
    """

    id, category = "ts.markers", "TRADE_SECRET"

    def setup(self) -> None:
        self._rx = term_regex(self.cfg.lexicons["ts_markers"])
        self._neg = term_regex(self.cfg.lexicons["ts_negative_terms"])

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d = DetectorOutput(), c.doc
        min_fraction = c.cfg.existing_labels.banner_min_marking_fraction
        candidates = [
            (text, pos)
            for text, pos in banner_lines(c)
            if marking_fraction(self._rx, text) >= min_fraction
        ]
        candidates += [(value, 0) for _scheme, value in d.existing_labels]  # dedicated label fields
        for text, pos in candidates:
            if not self._rx.search(text):
                continue
            if self._neg.search(text):
                out.suppressions.append(self.suppressed("legal_discussion", pos, pos + 1))
                continue
            out.detections.append(
                self.found(
                    c,
                    strength="strong",
                    kind="dictionary_hit",
                    start=max(pos, 0),
                    end=max(pos, 0) + 1,
                    masked="[TRADE-SECRET-MARKER]",
                )
            )
            break
        return out


class TsSecrecy(Detector):
    id, category = "ts.secrecy_language", "TRADE_SECRET"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d = DetectorOutput(), c.doc
        hits = c.matcher.distinct_terms(d, "ts_secrecy_terms")
        if len(hits) >= 2:
            pos = min(hits.values())
            out.detections.append(
                self.found(
                    c,
                    strength="weak",
                    kind="dictionary_hit",
                    start=pos,
                    end=pos + 1,
                    masked=f"[SECRECY-LANGUAGE n={len(hits)}]",
                )
            )
        return out


class MaMarkers(Detector):
    id, category = "ma.markers", "MA_CORP_STRATEGY"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        hits = m.distinct_terms(d, "mna_markers")
        if not hits:
            return out
        pos = min(hits.values())
        neg = self._negative(c)
        if neg:
            out.suppressions.append(self.suppressed(neg, pos, pos + 1))
            return out
        term = sorted(hits, key=hits.get)[0]
        out.detections.append(
            self.found(
                c,
                strength="strong",
                kind="dictionary_hit",
                start=pos,
                end=pos + 1,
                masked=f"[DEAL-MARKER: {phrase(term)}]",
            )
        )
        return out

    def _negative(self, c: ScanContext) -> str | None:
        announced = c.matcher.distinct_terms(c.doc, "mna_announced_terms")
        if announced:
            return f"announced_or_historical:{sorted(announced)[0]}"
        return c.matcher.doc_negative(c.doc, ("documentation",))


class MaTransactionSecrecy(Detector):
    id, category = "ma.transaction_secrecy", "MA_CORP_STRATEGY"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        trans = m.distinct_terms(d, "mna_transaction_terms")
        if not trans:
            return out
        pos = min(trans.values())
        secrecy = m.distinct_terms(d, "mna_secrecy_terms")
        announced = m.distinct_terms(d, "mna_announced_terms")
        doc_neg = m.doc_negative(d, ("documentation",))
        if secrecy and not announced and not doc_neg:
            out.detections.append(
                self.found(
                    c,
                    strength="strong",
                    kind="dictionary_hit",
                    start=pos,
                    end=pos + 1,
                    masked=(
                        f"[TRANSACTION+SECRECY transaction_terms={len(trans)} "
                        f"secrecy_terms={len(secrecy)}]"
                    ),
                )
            )
            return out
        if secrecy:
            reason = f"announced_or_historical:{sorted(announced)[0]}" if announced else doc_neg
            out.suppressions.append(self.suppressed(str(reason), pos, pos + 1))
        out.detections.append(
            self.found(
                c,
                strength="weak",
                kind="dictionary_hit",
                start=pos,
                end=pos + 1,
                masked=f"[TRANSACTION-TERMS n={len(trans)}]",
            )
        )
        return out
