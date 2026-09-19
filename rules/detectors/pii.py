"""PII detectors: government identifiers, birth dates, labelled personal fields, bulk contact data.

Business contact details (a work email, a title) are NOT PII under this policy, so a lone email or
phone number is only ever weak evidence.
"""

from __future__ import annotations

import re

from ..masking import mask_full, mask_keep_tail, phrase
from ..types import DetectorOutput
from ..validators import ssn_structure_valid
from .base import Detector, ScanContext

HC = "HIGHLY_CONFIDENTIAL"
_NEG = ("placeholder", "test", "documentation")

_SSN = re.compile(r"(?<![\d-])(\d{3})-(\d{2})-(\d{4})(?![\d-])")
_PASSPORT = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,2}\d{7,9}(?![A-Za-z0-9])")
_DATE = re.compile(
    r"(?<!\d)(?:\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|[A-Z][a-z]{2,8} \d{1,2}, \d{4})(?!\d)"
)
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,4}"
)
_PHONE = re.compile(r"(?<![\d(])(?:\(\d{3}\)\s?|\d{3}[-.\s])\d{3}[-.]\d{4}(?!\d)")


def _non_blank(value: str) -> bool:
    """A filled-in value, not a blank form line such as `______` or `...`."""
    return bool(re.search(r"[A-Za-z0-9]", value))


class SsnLike(Detector):
    id, category = "pii.ssn_like", "PII"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        dummy = set(c.cfg.context.known_dummy.ssn)
        doc_neg = m.doc_negative(d)
        for hit in _SSN.finditer(d.text):
            s, e = hit.span()
            if hit.group(0) in dummy:
                out.suppressions.append(self.suppressed("known_dummy_value", s, e))
                continue
            if not ssn_structure_valid(*hit.groups()):
                continue
            neg = (
                doc_neg
                or m.near_negative(d, s, e, _NEG)
                or (
                    f"non_person_identifier_context:{t}"
                    if (t := m.near_positive(d, s, e, "ssn_negative_keywords"))
                    else None
                )
            )
            if neg:
                out.suppressions.append(self.suppressed(neg, s, e))
                continue
            pos = m.near_positive(d, s, e, "pii_id_keywords")
            masked = mask_keep_tail(hit.group(0), 2, "SSN:")
            if pos:
                out.detections.append(
                    self.found(
                        c,
                        strength="strong",
                        kind="validator_passed",
                        start=s,
                        end=e,
                        masked=masked,
                        level_hint=HC,
                    )
                )
            else:
                out.detections.append(
                    self.found(
                        c, strength="weak", kind="pattern_match", start=s, end=e, masked=masked
                    )
                )
        return out


class PassportField(Detector):
    id, category = "pii.passport_field", "PII"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        for hit in _PASSPORT.finditer(d.text):
            s, e = hit.span()
            if not m.near_positive(d, s, e, "passport_keywords"):
                continue
            neg = m.doc_negative(d) or m.near_negative(d, s, e, _NEG)
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
                    masked=mask_full(hit.group(0), "PASSPORT"),
                    level_hint=HC,
                )
            )
        return out


class DobField(Detector):
    id, category = "pii.dob_field", "PII"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        for hit in _DATE.finditer(d.text):
            s, e = hit.span()
            if not m.near_positive(d, s, e, "dob_keywords", before_only=True):
                continue
            neg = m.doc_negative(d) or m.near_negative(d, s, e, _NEG)
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
                    masked=mask_full(hit.group(0), "DOB"),
                )
            )
        return out


class FieldLabels(Detector):
    """`Home address: <value>`-style labelled personal fields with a non-blank value."""

    id, category = "pii.field_labels", "PII"

    def setup(self) -> None:
        labels = "|".join(
            re.escape(x)
            for x in sorted(self.cfg.lexicons["pii_field_labels"], key=len, reverse=True)
        )
        self._pattern = re.compile(rf"(?im)^[ \t>*-]*({labels})[ \t]*:[ \t]*(\S[^\n]{{3,}})$")

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        for hit in self._pattern.finditer(d.text):
            value = hit.group(2)
            s, e = hit.start(2), hit.end(2)
            if not _non_blank(value):
                continue
            neg = m.doc_negative(d) or m.near_negative(d, s, e, ("placeholder", "test"))
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
                    masked=f"{phrase(hit.group(1))}: [VALUE]",
                )
            )
        return out


class BulkContact(Detector):
    """Many personal contact records in one document (bulk contact list)."""

    id, category = "pii.bulk_contact", "PII"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        emails = {x.group(0).lower(): x for x in _EMAIL.finditer(d.text)}
        phones = {re.sub(r"\D", "", x.group(0)): x for x in _PHONE.finditer(d.text)}
        n_e, n_p = len(emails), len(phones)
        if n_e < 3 and n_p < 3:
            return out
        first = min([*emails.values(), *phones.values()], key=lambda x: x.start())
        s, e = first.span()
        neg = m.doc_negative(d)
        if neg:
            out.suppressions.append(self.suppressed(neg, s, e))
            return out
        strength = "strong" if (n_e >= 3 and n_p >= 3) else "weak"
        out.detections.append(
            self.found(
                c,
                strength=strength,
                kind="pattern_match",
                start=s,
                end=e,
                masked=f"[CONTACT-RECORDS emails={n_e} phones={n_p}]",
            )
        )
        return out
