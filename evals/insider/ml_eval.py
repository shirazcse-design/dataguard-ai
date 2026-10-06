"""UC2 anomaly-detector evaluation on the HOLDOUT period (`dataguard-insider ml eval`).

Unsupervised model training does not use anomaly labels; labels are used only for evaluation.

The model's job is to rank unusual user-days for a limited analyst review budget, not to predict
"insider threat". So the headline metrics are anomaly separation and precision/recall at a review
budget, compared with a simple statistical baseline at the SAME budget; not accuracy.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import roc_auc_score

from ml.insider import detectors as D
from ml.insider import features as F
from ml.insider import synth

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "docs" / "uc2" / "results"
LABEL_STATEMENT = (
    "Unsupervised model training does not use anomaly labels; labels are used only for evaluation."
)


def load_labels() -> dict[tuple[str, int], dict[str, str]]:
    with (synth.DATA / "eval_labels.csv").open(encoding="utf-8") as f:
        return {(r["user_id"], int(r["day"])): r for r in csv.DictReader(f)}


def fit_all(cfg: dict[str, Any] | None = None):
    cfg = cfg or synth.load_config()
    recs = F.build_features(synth.load_activity(), cfg)
    train = [r for r in recs if r["period"] == "train"]
    hold = [r for r in recs if r["period"] == "holdout"]
    det = D.IsolationForestDetector.fit(train, cfg)  # labels are not loaded until after this line
    return cfg, recs, train, hold, det


def _pr(flags: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    tp = int((flags & y).sum())
    n = int(flags.sum())
    return {"alerts": n, "alert_rate": round(n / len(y), 4), "precision": round(tp / n, 3) if n else None,
            "recall": round(tp / int(y.sum()), 3), "true_positives": tp}  # fmt: skip


def _budget(scores: np.ndarray, days: np.ndarray, k: int) -> np.ndarray:
    flags = np.zeros(len(scores), dtype=bool)
    for d in np.unique(days):
        idx = np.where(days == d)[0]
        flags[idx[np.argsort(-scores[idx])[:k]]] = True
    return flags


def evaluate() -> dict[str, Any]:
    cfg, _, train, hold, det = fit_all()
    labels = load_labels()
    stat = D.StatisticalBaseline.from_config(cfg)
    if_s, st_s = det.score(hold), stat.score(hold)
    kind = np.array([labels.get((r["user_id"], r["day"]), {}).get("kind", "normal") for r in hold])
    scen = [labels.get((r["user_id"], r["day"]), {}).get("scenario") for r in hold]
    y = kind == "anomaly"
    days = np.array([r["day"] for r in hold])
    bands = np.array([det.band(s) for s in if_s])
    k = cfg["evaluation"]["review_budget_per_day"]

    def groups(s: np.ndarray) -> dict[str, Any]:
        return {g: {"n": int((kind == g).sum()), "median": round(float(np.median(s[kind == g])), 3),
                    "p90": round(float(np.percentile(s[kind == g], 90)), 3),
                    "min": round(float(s[kind == g].min()), 3), "max": round(float(s[kind == g].max()), 3)}
                for g in ("normal", "legitimate", "anomaly")}  # fmt: skip

    sweep = []
    train_s = det.score(train)
    for pct in (90, 95, 97, 99, 99.5):
        thr = float(np.percentile(train_s, pct))
        sweep.append({"train_percentile": pct, "threshold": round(thr, 4), **_pr(if_s >= thr, y)})
    budgets = {}
    for kk in (1, k, 5):
        budgets[kk] = {"isolation_forest": _pr(_budget(if_s, days, kk), y),
                       "statistical_baseline": _pr(_budget(st_s, days, kk), y)}  # fmt: skip
    if_b, st_b = _budget(if_s, days, k), _budget(st_s, days, k)
    st_flags = stat.flags(hold)
    per_scenario = []
    for sid in sorted({s for s in scen if s}):
        idx = [i for i, s in enumerate(scen) if s == sid]
        per_scenario.append({
            "scenario": sid, "kind": kind[idx[0]], "user_days": len(idx),
            "if_bands": dict(Counter(bands[idx].tolist())),
            "if_caught_at_budget": int(if_b[idx].sum()), "stat_caught_at_budget": int(st_b[idx].sum()),
            "if_alert_band": int((bands[idx] != "NORMAL").sum()), "stat_rule_flag": int(st_flags[idx].sum()),
            "if_max_score": round(float(if_s[idx].max()), 3),
        })  # fmt: skip
    users = {u["user_id"]: u for u in synth.load_users()}
    fam_rate: dict[str, dict[str, float]] = defaultdict(dict)
    for fam in sorted({u["role_family"] for u in users.values()}):
        m = np.array([users[r["user_id"]]["role_family"] == fam for r in hold]) & (kind == "normal")
        fam_rate[fam] = {"normal_user_days": int(m.sum()),
                         "if_alert_rate": round(float((bands[m] != "NORMAL").mean()), 4),
                         "stat_flag_rate": round(float(st_flags[m].mean()), 4)}  # fmt: skip
    flag_rec = next(r for r in hold if r["user_id"] == "u-2043" and r["day"] == 86)
    flagship = D.assess(det, flag_rec)
    return {
        "statement": LABEL_STATEMENT,
        "provenance": {"insider_version": cfg["insider_version"], "model_fingerprint": det.fingerprint,
                       "train_user_days": len(train), "holdout_user_days": len(hold),
                       "positives": int(y.sum()), "hard_legitimate": int((kind == "legitimate").sum()),
                       "review_budget_per_day": k, "verification": "OFFLINE (deterministic, no model calls)"},
        "thresholds": {"elevated": round(det.elevated, 4), "high": round(det.high, 4)},
        "separation": {"roc_auc": {"isolation_forest": round(float(roc_auc_score(y, if_s)), 4),
                                   "statistical_baseline": round(float(roc_auc_score(y, st_s)), 4)},
                       "isolation_forest_scores": groups(if_s), "statistical_scores": groups(st_s)},
        "bands": {"if_elevated_or_higher": _pr(bands != "NORMAL", y), "if_high": _pr(bands == "HIGH_ANOMALY", y),
                  "statistical_rule": _pr(st_flags, y)},
        "matched_budget": {str(kk): v for kk, v in budgets.items()},
        "threshold_sweep": sweep,
        "per_scenario": per_scenario,
        "role_family_alert_rates": fam_rate,
        "flagship": flagship,
    }  # fmt: skip


def write(report: dict[str, Any]) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "ml-eval.json").write_text(
        json.dumps(report, indent=1, sort_keys=True) + "\n", "utf-8"
    )
    return RESULTS / "ml-eval.json"


if __name__ == "__main__":
    r = evaluate()
    write(r)
    print(json.dumps({k: r[k] for k in ("separation", "bands", "matched_budget")}, indent=1)[:4000])
