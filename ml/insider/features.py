"""Deterministic feature engineering and per-user behavioural baselines.

For every user-day: the 7 raw features, a per-user, day-type-aware robust baseline (median and MAD
over the baseline window, weekdays against weekdays and weekends against weekends), a robust
z-score per feature, and `download_ratio_to_baseline`.

* TRAINING rows (historical period): each day is compared with the trailing `baseline_window_days`
  before it (the day itself excluded), so the model learns what "normal deviation" looks like.
* HOLDOUT rows: compared with the FROZEN historical baseline (the last `baseline_window_days` of
  the training period). The holdout never shifts a user's baseline, so an anomaly in week 1 of the
  holdout cannot make week 2 look normal.

No label, scenario or HR attribute is read here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from .synth import FEATURES

MODEL_INPUTS = tuple(f"z_{f}" for f in FEATURES) + ("log_download_ratio",)


@dataclass(frozen=True)
class Baseline:
    median: dict[str, float]
    scale: dict[str, float]  # 1.4826 * MAD, floored per feature
    days: int


def _weekend(d: str) -> bool:
    return date.fromisoformat(d).weekday() >= 5


def baseline_from(rows: list[dict[str, Any]], floors: dict[str, float]) -> Baseline:
    med, scale = {}, {}
    for f in FEATURES:
        v = np.array([r[f] for r in rows], dtype=float)
        m = float(np.median(v)) if len(v) else 0.0
        mad = float(np.median(np.abs(v - m))) if len(v) else 0.0
        med[f], scale[f] = m, max(1.4826 * mad, floors[f])
    return Baseline(med, scale, len(rows))


def featurize(row: dict[str, Any], b: Baseline) -> dict[str, float]:
    out = {f"z_{f}": (row[f] - b.median[f]) / b.scale[f] for f in FEATURES}
    out["log_download_ratio"] = float(
        np.log((row["files_downloaded"] + 1.0) / (b.median["files_downloaded"] + 1.0))
    )
    return out


def build_features(activity: list[dict[str, Any]], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """One record per scorable user-day: identifiers, raw values, baseline, z-scores, model inputs.
    Training days without a full baseline window are skipped (not scorable)."""
    window = cfg["features"]["baseline_window_days"]
    floors = cfg["features"]["scale_floor"]
    train_days = cfg["dataset"]["train_days"]
    by_user: dict[str, list[dict[str, Any]]] = {}
    for r in activity:
        by_user.setdefault(r["user_id"], []).append(r)
    out = []
    for uid in sorted(by_user):
        days = sorted(by_user[uid], key=lambda r: r["day"])
        frozen = {
            wk: baseline_from([r for r in days if train_days - window < r["day"] <= train_days
                               and _weekend(r["date"]) == wk], floors)
            for wk in (False, True)
        }  # fmt: skip
        for r in days:
            wk = _weekend(r["date"])
            if r["day"] <= train_days:
                if r["day"] <= window:
                    continue
                hist = [
                    h for h in days
                    if r["day"] - window <= h["day"] < r["day"] and _weekend(h["date"]) == wk
                ]  # fmt: skip
                b = baseline_from(hist, floors)
            else:
                b = frozen[wk]
            out.append({**r, "weekend": wk, "baseline": b, **featurize(r, b)})
    return out


def matrix(records: list[dict[str, Any]]) -> np.ndarray:
    return np.array([[rec[k] for k in MODEL_INPUTS] for rec in records], dtype=float)
