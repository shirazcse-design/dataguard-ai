"""PHI detectors: record numbers, HL7 patient segments, patient fields with clinical content."""

from __future__ import annotations

import re

from ..masking import mask_full, phrase
from ..types import DetectorOutput
from .base import Detector, ScanContext

_MRN = re.compile(r"(?i)(?<![A-Za-z0-9])MRN[-\s:#]{0,3}(\d{6,10})(?!\d)")
_HL7_PID = re.compile(r"(?m)^PID\|[^\n]{0,400}\|[^\n]{0,400}$")
_ICD = re.compile(r"(?<![A-Za-z0-9.])[A-TV-Z]\d{2}(?:\.\d{1,4})?(?![A-Za-z0-9])")
_PATIENT_FIELD = re.compile(r"(?im)^[ \t>*-]*patient(?:[ \t]+name)?[ \t]*[:\-][ \t]*(\S[^\n]{2,})$")


class Mrn(Detector):
    id, category = "phi.mrn", "PHI"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        for hit in _MRN.finditer(d.text):
            s, e = hit.span()
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
                    masked=mask_full(hit.group(0), "MRN"),
                )
            )
        return out


class Hl7Pid(Detector):
    id, category = "phi.hl7_pid", "PHI"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d = DetectorOutput(), c.doc
        hit = _HL7_PID.search(d.text)
        if hit:
            out.detections.append(
                self.found(
                    c,
                    strength="strong",
                    kind="pattern_match",
                    start=hit.start(),
                    end=hit.start() + 3,
                    masked="PID|[PATIENT-SEGMENT]",
                )
            )
        return out


class PatientClinical(Detector):
    """A filled-in `Patient:` field together with clinical vocabulary."""

    id, category = "phi.patient_clinical", "PHI"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        hit = _PATIENT_FIELD.search(d.text)
        if not hit or not re.search(r"[A-Za-z0-9]", hit.group(1)):
            return out
        s, e = hit.start(1), hit.end(1)
        neg = m.doc_negative(d)
        if neg:
            out.suppressions.append(self.suppressed(neg, s, e))
            return out
        clinical = set(m.distinct_terms(d, "clinical_terms")) - {"patient", "patients"}
        strength = "strong" if clinical else "weak"
        out.detections.append(
            self.found(
                c,
                strength=strength,
                kind="pattern_match",
                start=s,
                end=e,
                masked=f"Patient: [NAME] + {len(clinical)} clinical term(s)",
            )
        )
        return out


class IcdContext(Detector):
    id, category = "phi.icd_context", "PHI"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        for hit in _ICD.finditer(d.text):
            s, e = hit.span()
            key = m.near_positive(d, s, e, "icd_context_keywords", header=False)
            if not key:
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
                    masked=f"ICD-10:{phrase(hit.group(0))[:1]}**",
                )
            )
            break  # one detection per document is enough evidence
        return out


class ClinicalDensity(Detector):
    id, category = "phi.clinical_density", "PHI"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d = DetectorOutput(), c.doc
        found = c.matcher.distinct_terms(d, "clinical_terms")
        if len(found) >= 4:
            first = min(found.values())
            out.detections.append(
                self.found(
                    c,
                    strength="weak",
                    kind="dictionary_hit",
                    start=first,
                    end=first + 1,
                    masked=f"[CLINICAL-TERMS n={len(found)}]",
                )
            )
        return out
