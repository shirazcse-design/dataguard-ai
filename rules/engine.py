"""The Rules Engine: run every detector, aggregate, derive a level (or abstain).

Aggregation rules (all deterministic):
* A category is ASSERTED only if its strongest detection is at or above `emit_min_strength`.
  Weaker evidence is retained in the result but never asserts a category or a level.
* The level is the highest of: the taxonomy floors of the asserted categories, government-id style
  hints (`level_hint`), and banner/label floors.
* No decisive evidence => the level ABSTAINS (`level is None`). No rule match does NOT mean Public.
"""

from __future__ import annotations

import time

from app.classification.policy import TaxonomyPolicy
from app.classification.schemas import Document

from .config import RulesConfig
from .context import ContextMatcher
from .detectors.base import Detector, ScanContext
from .detectors.credentials import (
    AwsAccessKey,
    Bearer,
    Jwt,
    PasswordHash,
    PrefixedToken,
    PrivateKeyBlock,
    SecretAssignment,
    UrlPassword,
)
from .detectors.financial import CardPan, Iban, NonPublicFinancials, UsBankAccount
from .detectors.ip_ts_ma import (
    IpMarkers,
    IpNovelty,
    MaMarkers,
    MaTransactionSecrecy,
    TsMarkers,
    TsSecrecy,
)
from .detectors.markings import Banner, Filename
from .detectors.phi import ClinicalDensity, Hl7Pid, IcdContext, Mrn, PatientClinical
from .detectors.pii import BulkContact, DobField, FieldLabels, PassportField, SsnLike
from .detectors.source_code import CodeStructure
from .preprocess import prepare
from .types import STRENGTH_RANK, Detection, RulesResult, Suppression

# Fixed order => deterministic output.
DETECTOR_CLASSES: list[type[Detector]] = [
    SsnLike,
    PassportField,
    DobField,
    FieldLabels,
    BulkContact,
    Mrn,
    Hl7Pid,
    PatientClinical,
    IcdContext,
    ClinicalDensity,
    CardPan,
    Iban,
    UsBankAccount,
    NonPublicFinancials,
    SecretAssignment,
    PrefixedToken,
    Jwt,
    Bearer,
    UrlPassword,
    AwsAccessKey,
    PrivateKeyBlock,
    PasswordHash,
    CodeStructure,
    IpMarkers,
    IpNovelty,
    TsMarkers,
    TsSecrecy,
    MaMarkers,
    MaTransactionSecrecy,
    Banner,
    Filename,
]


class RulesEngine:
    def __init__(self, cfg: RulesConfig, policy: TaxonomyPolicy) -> None:
        self.cfg = cfg
        self.policy = policy
        self.matcher = ContextMatcher(cfg)
        rank = {lv: policy.level_rank(lv) for lv in policy.level_ids}
        self.detectors = [cls(cfg, self.matcher, rank) for cls in DETECTOR_CLASSES]
        self._rank = rank

    @property
    def ruleset_version(self) -> str:
        return self.cfg.ruleset_version

    def analyze(self, document: Document) -> RulesResult:
        started = time.perf_counter()
        doc = prepare(document, self.cfg.max_content_chars)
        scan = ScanContext(doc, self.cfg, self.matcher)
        detections: list[Detection] = []
        suppressions: list[Suppression] = []
        for det in self.detectors:
            out = det.detect(scan)
            detections.extend(out.detections)
            suppressions.extend(out.suppressions)

        # ---- categories --------------------------------------------------------------------
        best: dict[str, str] = {}
        for d in detections:
            if d.axis == "category":
                cur = best.get(d.value)
                if cur is None or STRENGTH_RANK[d.strength] > STRENGTH_RANK[cur]:
                    best[d.value] = d.strength
        emitted = {
            cat: s
            for cat, s in best.items()
            if STRENGTH_RANK[s] >= STRENGTH_RANK[self.cfg.emit_min(cat)]
        }
        weak_only = sorted(set(best) - set(emitted))

        # ---- level -------------------------------------------------------------------------
        candidates: list[tuple[str, str, str]] = []  # (level, strength, source)
        floor = self.policy.floor_level_for(emitted)
        if floor:
            candidates.append(
                (floor, max(emitted.values(), key=STRENGTH_RANK.__getitem__), "category_floor")
            )
        for d in detections:
            if (
                d.axis == "category"
                and d.value in emitted
                and d.level_hint
                and (STRENGTH_RANK[d.strength] >= STRENGTH_RANK[self.cfg.emit_min(d.value)])
            ):
                candidates.append((d.level_hint, d.strength, f"{d.detector_id}:level_hint"))
            if (
                d.axis == "level"
                and STRENGTH_RANK[d.strength] >= STRENGTH_RANK[self.cfg.emit_min_strength.default]
            ):
                candidates.append((d.value, d.strength, d.detector_id))
        level = strength = None
        sources: list[str] = []
        if candidates:
            top = max(candidates, key=lambda x: self._rank[x[0]])[0]
            level = top
            strength = max((c[1] for c in candidates if c[0] == top), key=STRENGTH_RANK.__getitem__)
            sources = sorted({c[2] for c in candidates if c[0] == top})

        return RulesResult(
            ruleset_version=self.cfg.ruleset_version,
            detections=detections,
            suppressions=suppressions,
            categories=emitted,
            level=level,
            level_strength=strength,
            level_sources=sources,
            no_signal=not detections,
            truncated=doc.truncated,
            weak_only_categories=weak_only,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
