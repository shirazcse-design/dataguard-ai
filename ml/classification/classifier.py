"""Standalone ML classifier (Approach B) implementing the common `Classifier` interface."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.classification.config_loader import ConfigBundle
from app.classification.schemas import (
    CategoryPrediction,
    ClassificationRequest,
    ClassificationResult,
    Confidence,
    Evidence,
    LevelPrediction,
    Routing,
    Scores,
    Supports,
    Telemetry,
    Versions,
)
from evals.classification.dataset.build import load_documents, load_manifest
from evals.classification.dataset.schema import DatasetDocument
from evals.classification.lock import DEVELOPMENT_SPLITS

from .config import MLConfig, load_ml_config
from .model import MLModel

FIT_SPLITS = ("train",)
CALIBRATION_SPLITS = ("calibration",)


def _confidence(p: float, calibrated: bool, ref: str) -> Confidence:
    if calibrated:
        return Confidence(
            kind="calibrated_probability", raw=float(p), calibrated=True, calibration_ref=ref
        )
    return Confidence(kind="uncalibrated_score", raw=float(p))


class MLClassifier:
    name = "ml"

    def __init__(
        self, model: MLModel, cfg: MLConfig, policy, config_sha256: str, data_info: dict[str, Any]
    ) -> None:
        self.model = model
        self.cfg = cfg
        self.version = cfg.ml_version
        self._policy = policy
        self._sha = config_sha256
        self._data = data_info
        self.model_id = (
            f"ml-{cfg.ml_version}-{config_sha256[:8]}-{data_info.get('train_sha256', '')[:8]}"
        )

    def params(self) -> dict[str, Any]:
        return {
            "ml_version": self.cfg.ml_version,
            "ml_config_sha256": self._sha,
            "model_id": self.model_id,
            "fit_splits": list(FIT_SPLITS),
            "calibration_splits": list(CALIBRATION_SPLITS),
            "training_data": self._data,
            "level_C": self.cfg.level_head.C,
            "category_C": self.cfg.category_head.C,
            "category_threshold": self.cfg.decision.category_threshold,
            "filename_block": self.cfg.features.filename_block,
            "seed": self.cfg.seed,
        }

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        started = time.perf_counter()
        doc = request.document
        pred = self.model.predict([doc])
        m = self.model
        lp = pred.level_probs[0]
        k = int(lp.argmax())
        level_id = m.level_ids[k]
        ref = self.model_id

        chosen: list[tuple[str, float]] = []
        for j, c in enumerate(m.category_ids):
            p = float(pred.category_probs[0, j])
            if p >= self.cfg.threshold_for(c):
                chosen.append((c, p))

        evidence: list[Evidence] = []
        cat_ev: dict[str, list[str]] = {}
        if request.options.include_evidence:

            def add(axis: str, value: str, words: list[tuple[str, float]]) -> list[str]:
                if not words:
                    return []
                ev_id = f"e{len(evidence) + 1}"
                evidence.append(
                    Evidence(
                        evidence_id=ev_id,
                        source="ml",
                        supports=Supports(axis=axis, value=value),
                        type="feature_attribution",
                        excerpt=", ".join(w for w, _ in words),
                        weight=float(sum(x for _, x in words)),
                        provenance="inferred",
                    )
                )
                return [ev_id]

            add("level", level_id, m.explain_level(doc, level_id))  # level evidence: list only
            for c, _ in chosen:
                cat_ev[c] = add("category", c, m.explain_category(doc, c))

        categories = [
            CategoryPrediction(
                id=c,
                confidence=_confidence(p, pred.category_calibrated[c], ref),
                decided_by="ml",
                evidence_ids=cat_ev.get(c, []),
            )
            for c, p in chosen
        ]
        elapsed = (time.perf_counter() - started) * 1000
        all_cal = pred.level_calibrated and all(pred.category_calibrated.values())
        return ClassificationResult(
            request_id=request.request_id,
            document_id=doc.document_id,
            content_hash=doc.content_hash(),
            status="ok",
            level=LevelPrediction(
                value=level_id,
                confidence=_confidence(lp[k], pred.level_calibrated, ref),
                decided_by="ml",
            ),
            categories=categories,
            high_risk=self._policy.derive_high_risk(level_id, [c for c, _ in chosen]),
            evidence=evidence,
            routing=Routing(stages_run=["ml"], stop_reason="ml_decision", abstained=False),
            versions=Versions(
                taxonomy=self._policy.taxonomy_version,
                high_risk_config=self._policy.high_risk_version,
                ml_model=self.model_id,
                classifier=f"{self.name}@{self.version}",
            ),
            telemetry=Telemetry(latency_ms={"ml": elapsed, "total": elapsed}),
            scores=Scores(
                level={lv: float(lp[i]) for i, lv in enumerate(m.level_ids)},
                categories={
                    c: float(pred.category_probs[0, j]) for j, c in enumerate(m.category_ids)
                },
                calibrated=all_cal,
                calibration_ref=ref if all_cal else None,
            ),
        )


def _train(
    bundle: ConfigBundle, cfg: MLConfig, train: list[DatasetDocument], calib: list[DatasetDocument]
) -> MLModel:
    policy = bundle.policy
    model = MLModel(cfg, policy.level_ids, policy.category_ids)
    docs = [d.to_request().document for d in train]
    model.fit_heads(docs, [d.gold_level for d in train], [d.gold_categories for d in train])
    model.calibrate(
        [d.to_request().document for d in calib],
        [d.gold_level for d in calib],
        [d.gold_categories for d in calib],
    )
    return model


def build_ml_classifier(
    bundle: ConfigBundle,
    *,
    data_dir: Path | str | None = None,
    config_dir: Path | str | None = None,
    cfg_override: MLConfig | None = None,
) -> MLClassifier:
    """Fit on `train`, calibrate on `calibration`. Only development splits are ever loaded."""
    cfg, digest = load_ml_config(bundle.policy, config_dir)
    if cfg_override is not None:
        cfg = cfg_override
    docs = load_documents(data_dir, splits=list(DEVELOPMENT_SPLITS))
    train = [d for d in docs if d.split in FIT_SPLITS]
    calib = [d for d in docs if d.split in CALIBRATION_SPLITS]
    files = load_manifest(data_dir)["files"]
    info = {
        "train_sha256": files["train"]["sha256"],
        "calibration_sha256": files["calibration"]["sha256"],
        "n_train": len(train),
        "n_calibration": len(calib),
    }
    model = _train(bundle, cfg, train, calib)
    return MLClassifier(model, cfg, bundle.policy, digest, info)
