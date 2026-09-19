"""Standalone Rules classifier (Approach A) implementing the common `Classifier` interface.

Abstention semantics: when rules find no decisive level evidence the engine ABSTAINS. This adapter
applies the configurable `standalone_default_level` (INTERNAL) ONLY so the standalone benchmark can
be scored, marks the result `abstained`, and gives the level confidence kind "none". It is NOT a
finding that the document is low-sensitivity, and NO RULE MATCH DOES NOT MEAN PUBLIC. The future
hybrid path will treat abstention as a signal to escalate to ML/LLM instead.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.classification.config_loader import ConfigBundle
from app.classification.schemas import (
    NO_CONFIDENCE,
    CategoryPrediction,
    ClassificationRequest,
    ClassificationResult,
    Confidence,
    DetectorRef,
    Evidence,
    LevelPrediction,
    Locator,
    Routing,
    Supports,
    Telemetry,
    Versions,
)

from .config import config_summary, load_rules_config
from .engine import RulesEngine
from .types import STRENGTH_RANK, Detection

MAX_SUPPRESSION_WARNINGS = 25


class RulesClassifier:
    name = "rules"

    def __init__(self, engine: RulesEngine, config_sha256: str = "") -> None:
        self.engine = engine
        self.version = engine.ruleset_version
        self._config_sha256 = config_sha256
        self._policy = engine.policy
        self._default_level = engine.cfg.standalone_default_level

    def params(self) -> dict[str, Any]:
        return {
            **config_summary(self.engine.cfg),
            "rules_config_sha256": self._config_sha256,
            "detectors": sorted(f"{d.id}@{d.version}" for d in self.engine.detectors),
        }

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        started = time.perf_counter()
        doc = request.document
        result = self.engine.analyze(doc)
        emit_min = self.engine.cfg.emit_min

        evidence: list[Evidence] = []
        ids_by_category: dict[str, list[str]] = {}
        if request.options.include_evidence:
            for i, det in enumerate(result.detections, start=1):
                ev_id = f"e{i}"
                evidence.append(self._evidence(ev_id, det))
                if (
                    det.axis == "category"
                    and det.value in result.categories
                    and STRENGTH_RANK[det.strength] >= STRENGTH_RANK[emit_min(det.value)]
                ):
                    ids_by_category.setdefault(det.value, []).append(ev_id)

        categories = [
            CategoryPrediction(
                id=cat,
                confidence=Confidence(kind="rule_strength", raw=strength),
                decided_by="rules",
                evidence_ids=ids_by_category.get(cat, []),
            )
            for cat, strength in sorted(result.categories.items())
        ]
        abstained = result.level is None
        if abstained:
            level = LevelPrediction(
                value=self._default_level, confidence=NO_CONFIDENCE, decided_by="rules"
            )
        else:
            level = LevelPrediction(
                value=result.level,
                confidence=Confidence(kind="rule_strength", raw=result.level_strength),
                decided_by="rules",
            )

        warnings: list[str] = []
        if abstained:
            warnings.append(f"level_abstained:standalone_default={self._default_level}")
        if result.weak_only_categories:
            warnings.append("weak_signals_only:" + ",".join(result.weak_only_categories))
        if result.truncated:
            warnings.append("input_truncated")
        seen: set[str] = set()
        for sup in result.suppressions:
            note = f"suppressed:{sup.detector_id}:{sup.reason}"
            if note not in seen and len(seen) < MAX_SUPPRESSION_WARNINGS:
                seen.add(note)
                warnings.append(note)

        elapsed_ms = (time.perf_counter() - started) * 1000
        return ClassificationResult(
            request_id=request.request_id,
            document_id=doc.document_id,
            content_hash=doc.content_hash(),
            status="ok",
            level=level,
            categories=categories,
            high_risk=self._policy.derive_high_risk(level.value, [c.id for c in categories]),
            evidence=evidence,
            routing=Routing(
                stages_run=["rules"],
                stop_reason="rules_abstained_default_level" if abstained else "rules_evidence",
                abstained=abstained,
            ),
            versions=Versions(
                taxonomy=self._policy.taxonomy_version,
                high_risk_config=self._policy.high_risk_version,
                ruleset=self.version,
                classifier=f"{self.name}@{self.version}",
            ),
            telemetry=Telemetry(latency_ms={"rules": result.elapsed_ms, "total": elapsed_ms}),
            warnings=warnings,
        )

    @staticmethod
    def _evidence(ev_id: str, det: Detection) -> Evidence:
        return Evidence(
            evidence_id=ev_id,
            source=det.source,
            supports=Supports(axis=det.axis, value=det.value),
            type=det.kind,
            locator=Locator(char_start=det.start, char_end=det.end, line=det.line),
            excerpt=det.masked_excerpt,
            excerpt_hash=det.excerpt_hash,
            detector=DetectorRef(id=det.detector_id, version=det.detector_version),
            strength=det.strength,
            provenance="observed",
        )


def build_rules_classifier(
    bundle: ConfigBundle, config_dir: Path | str | None = None
) -> RulesClassifier:
    cfg, digest = load_rules_config(bundle.policy, config_dir)
    return RulesClassifier(RulesEngine(cfg, bundle.policy), digest)
