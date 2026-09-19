"""Financial / PCI detectors: payment cards, IBANs, US bank routing/account numbers, and
pre-release financial results."""

from __future__ import annotations

import re

from ..masking import mask_full, mask_keep_tail
from ..types import DetectorOutput
from ..validators import IBAN_LENGTHS, aba_valid, card_network, iban_valid, luhn_valid
from .base import Detector, ScanContext

_NEG = ("placeholder", "test", "documentation")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_IBAN = re.compile(
    r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?(?![A-Za-z0-9])"
)
_NINE = re.compile(r"(?<!\d)(\d{9})(?!\d)")
_ACCT = re.compile(r"(?<!\d)(\d{8,17})(?!\d)")


class CardPan(Detector):
    """Payment card numbers: Luhn-valid with a plausible network prefix and length."""

    id, category = "fin.card_pan", "FINANCIAL_PCI"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        dummy = set(c.cfg.context.known_dummy.pan)
        doc_neg = m.doc_negative(d)
        for hit in _CARD.finditer(d.text):
            s, e = hit.span()
            digits = re.sub(r"\D", "", hit.group(0))
            if not (13 <= len(digits) <= 19):
                continue
            if not card_network(digits) or not luhn_valid(digits):
                continue
            if digits in dummy:
                out.suppressions.append(self.suppressed("known_test_value", s, e))
                continue
            if len(set(digits)) < 4:  # e.g. 0000000000000000: shaped like a card, but not one
                continue
            neg = doc_neg or m.near_negative(d, s, e, _NEG)
            if neg:
                out.suppressions.append(self.suppressed(neg, s, e))
                continue
            has_ctx = m.near_positive(d, s, e, "card_keywords") is not None
            out.detections.append(
                self.found(
                    c,
                    strength="definitive" if has_ctx else "strong",
                    kind="validator_passed",
                    start=s,
                    end=e,
                    masked=mask_keep_tail(hit.group(0), 4, "PAN:"),
                )
            )
        return out


class Iban(Detector):
    id, category = "fin.iban", "FINANCIAL_PCI"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        for hit in _IBAN.finditer(d.text):
            s, e = hit.span()
            compact = hit.group(0).replace(" ", "")
            valid, len_ok = iban_valid(compact)
            if not valid:
                continue
            known = compact[:2] in IBAN_LENGTHS
            if known and not len_ok:
                continue
            if not known and not 15 <= len(compact) <= 34:
                continue
            neg = m.doc_negative(d) or m.near_negative(d, s, e, _NEG)
            if neg:
                out.suppressions.append(self.suppressed(neg, s, e))
                continue
            out.detections.append(
                self.found(
                    c,
                    strength="definitive" if len_ok else "strong",
                    kind="validator_passed",
                    start=s,
                    end=e,
                    masked=f"IBAN:{compact[:2]}{'*' * (len(compact) - 4)}{compact[-2:]}",
                )
            )
        return out


class UsBankAccount(Detector):
    """ABA-checksummed routing numbers and labelled account numbers (context required)."""

    id, category = "fin.us_bank_account", "FINANCIAL_PCI"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        doc_neg = m.doc_negative(d)
        for hit in _NINE.finditer(d.text):
            s, e = hit.span()
            if not aba_valid(hit.group(1)) or not m.near_positive(d, s, e, "routing_keywords"):
                continue
            neg = doc_neg or m.near_negative(d, s, e, _NEG)
            if neg:
                out.suppressions.append(self.suppressed(neg, s, e))
                continue
            out.detections.append(
                self.found(
                    c,
                    strength="definitive",
                    kind="validator_passed",
                    start=s,
                    end=e,
                    masked=mask_full(hit.group(1), "ROUTING"),
                )
            )
        for hit in _ACCT.finditer(d.text):
            s, e = hit.span()
            if not m.near_positive(d, s, e, "account_keywords"):
                continue
            neg = doc_neg or m.near_negative(d, s, e, _NEG)
            if neg:
                out.suppressions.append(self.suppressed(neg, s, e))
                continue
            out.detections.append(
                self.found(
                    c,
                    strength="strong",
                    kind="pattern_match",
                    start=s,
                    end=e,
                    masked=mask_keep_tail(hit.group(1), 4, "ACCT:"),
                )
            )
        return out


class NonPublicFinancials(Detector):
    """Financial-results vocabulary together with non-public / embargo language."""

    id, category = "fin.nonpublic_financials", "FINANCIAL_PCI"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        fin = m.distinct_terms(d, "financial_results_terms")
        if len(fin) < 2:
            return out
        nonpublic = m.distinct_terms(d, "nonpublic_terms")
        published = m.distinct_terms(d, "published_terms")
        if nonpublic and not published:
            pos = min(nonpublic.values())
            out.detections.append(
                self.found(
                    c,
                    strength="strong",
                    kind="dictionary_hit",
                    start=pos,
                    end=pos + 1,
                    masked=(
                        f"[NON-PUBLIC-FINANCIALS fin_terms={len(fin)} "
                        f"nonpublic_terms={len(nonpublic)}]"
                    ),
                )
            )
        else:
            if published:
                out.suppressions.append(self.suppressed("published_context", 0, 1))
            pos = min(fin.values())
            out.detections.append(
                self.found(
                    c,
                    strength="weak",
                    kind="dictionary_hit",
                    start=pos,
                    end=pos + 1,
                    masked=f"[FINANCIAL-TERMS n={len(fin)}]",
                )
            )
        return out
