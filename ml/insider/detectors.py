"""The two anomaly detectors, and the contributing-signal explanation.

* `IsolationForestDetector` (primary, AUTHORITATIVE for the anomaly score and band). Trained on the
  historical period's model inputs only: no labels exist at training time. Score = the negated
  `score_samples`, so a higher score means more unusual. Bands are percentiles of the TRAINING
  score distribution, so "ELEVATED" means "more unusual than 95% of normal historical days".
* `StatisticalBaseline` (comparison): flag a user-day when any feature's robust z exceeds
  `single_z`, or at least `multi_count` features exceed `multi_z`. Its ranking score is the largest
  robust z.

The score measures UNUSUALNESS relative to the user's own history. It is not evidence of intent,
a policy violation or wrongdoing.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest

from .features import MODEL_INPUTS, matrix
from .synth import FEATURES

BANDS = ("NORMAL", "ELEVATED", "HIGH_ANOMALY")
MODEL_ID = "uc2-isolation-forest"


@dataclass
class IsolationForestDetector:
    model: IsolationForest
    elevated: float
    high: float
    version: str
    fingerprint: str

    @classmethod
    def fit(cls, train: list[dict[str, Any]], cfg: dict[str, Any]) -> IsolationForestDetector:
        p = cfg["isolation_forest"]
        x = matrix(train)
        model = IsolationForest(
            n_estimators=p["n_estimators"], max_samples=p["max_samples"],
            random_state=p["random_state"], contamination="auto",
        )  # fmt: skip
        model.fit(x)
        scores = -model.score_samples(x)
        fp = hashlib.sha256(x.tobytes() + repr(sorted(p.items())).encode()).hexdigest()[:12]
        return cls(
            model,
            float(np.percentile(scores, p["elevated_percentile"])),
            float(np.percentile(scores, p["high_percentile"])),
            cfg["insider_version"],
            fp,
        )

    def score(self, records: list[dict[str, Any]]) -> np.ndarray:
        return -self.model.score_samples(matrix(records))

    def band(self, score: float) -> str:
        return (
            "HIGH_ANOMALY"
            if score >= self.high
            else "ELEVATED"
            if score >= self.elevated
            else "NORMAL"
        )

    def contributions(self, rec: dict[str, Any]) -> list[dict[str, Any]]:
        """Reset-to-baseline counterfactual: for each feature, how much the score drops when that
        one feature is put back at the user's baseline (its z to 0; for downloads, the ratio to 1).
        Faithful to this model, and readable: "if downloads had been normal, the score would fall
        by X". It explains the SCORE; it is not evidence of intent."""
        base = float(self.score([rec])[0])
        out = []
        b = rec["baseline"]
        for f in FEATURES:
            reset = dict(rec)
            reset[f"z_{f}"] = 0.0
            if f == "files_downloaded":
                reset["log_download_ratio"] = 0.0
            drop = base - float(self.score([reset])[0])
            ratio = None if b.median[f] <= 0 else round(rec[f] / b.median[f], 1)
            out.append({
                "feature": f, "observed": rec[f], "baseline_median": round(b.median[f], 1),
                "ratio_to_baseline": ratio, "robust_z": round(rec[f"z_{f}"], 1),
                "score_drop_if_reset": round(drop, 4),
            })  # fmt: skip
        return sorted(out, key=lambda c: -c["score_drop_if_reset"])


@dataclass(frozen=True)
class StatisticalBaseline:
    single_z: float
    multi_z: float
    multi_count: int

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> StatisticalBaseline:
        s = cfg["statistical_baseline"]
        return cls(s["single_z"], s["multi_z"], s["multi_count"])

    def score(self, records: list[dict[str, Any]]) -> np.ndarray:
        return np.array([max(r[f"z_{f}"] for f in FEATURES) for r in records], dtype=float)

    def flags(self, records: list[dict[str, Any]]) -> np.ndarray:
        out = []
        for r in records:
            zs = [r[f"z_{f}"] for f in FEATURES]
            out.append(
                max(zs) > self.single_z or sum(z > self.multi_z for z in zs) >= self.multi_count
            )
        return np.array(out, dtype=bool)


def assess(det: IsolationForestDetector, rec: dict[str, Any], top: int = 4) -> dict[str, Any]:
    """The authoritative anomaly result for one user-day (the `AnomalyResult` payload)."""
    s = float(det.score([rec])[0])
    contribs = det.contributions(rec)
    return {
        "model_id": MODEL_ID,
        "model_version": det.version,
        "model_fingerprint": det.fingerprint,
        "user_id": rec["user_id"],
        "date": rec["date"],
        "anomaly_score": round(s, 4),
        "anomaly_band": det.band(s),
        "thresholds": {"elevated": round(det.elevated, 4), "high": round(det.high, 4)},
        "contributing_signals": [c for c in contribs if c["score_drop_if_reset"] > 0][:top],
        "inputs": list(MODEL_INPUTS),
    }
