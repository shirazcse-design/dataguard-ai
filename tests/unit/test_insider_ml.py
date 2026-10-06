"""UC2 behavioural ML: reproducible synthetic data, label-free training, frozen holdout baselines,
the authoritative flagship result, and no demographic / HR attributes anywhere."""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from ml.insider import detectors as D
from ml.insider import features as F
from ml.insider import synth


@pytest.fixture(scope="module")
def fitted():
    cfg = synth.load_config()
    recs = F.build_features(synth.load_activity(), cfg)
    train = [r for r in recs if r["period"] == "train"]
    return cfg, recs, train, D.IsolationForestDetector.fit(train, cfg)


def test_data_regenerates_byte_identically(tmp_path):
    synth.write(tmp_path)
    for name in ("users.json", "permissions.json", "activity.csv", "eval_labels.csv"):
        assert (tmp_path / name).read_bytes() == (synth.DATA / name).read_bytes(), name


def test_frozen_manifest_matches_committed_files():
    m = json.loads(Path("evals/insider/dataset/FROZEN.json").read_text())
    for f, h in m["sha256"].items():
        assert hashlib.sha256(Path(f).read_bytes()).hexdigest() == h, f


def test_training_never_reads_labels_and_scenarios_live_in_the_holdout():
    for mod in ("features", "detectors"):  # synth only WRITES the evaluation labels
        src = Path(f"ml/insider/{mod}.py").read_text()
        assert "eval_labels" not in src and "load_labels" not in src and "SCENARIOS" not in src
    train_days = synth.load_config()["dataset"]["train_days"]
    for sc in synth.SCENARIOS:
        assert min(sc["days"]) > train_days
        assert all((date(2026, 6, 1) + timedelta(d - 1)).weekday() < 5 for d in sc["days"])
    assert "kind" not in synth.load_activity()[0]


def test_no_demographic_hr_or_employment_attributes():
    users = synth.load_users()
    banned = {"name", "display_name", "gender", "sex", "age", "race", "ethnicity", "religion", "region",
              "employment_status", "notice_period", "disability", "nationality"}  # fmt: skip
    assert not banned & set(users[0])
    assert set(F.MODEL_INPUTS) == {f"z_{f}" for f in synth.FEATURES} | {"log_download_ratio"}


def test_holdout_uses_the_frozen_historical_baseline(fitted):
    cfg, recs, _, _ = fitted
    hold = [
        r
        for r in recs
        if r["user_id"] == "u-2016" and r["period"] == "holdout" and not r["weekend"]
    ]
    assert (
        len({id(r["baseline"]) for r in hold}) == 1
    )  # one frozen weekday baseline for the holdout


def test_flagship_is_high_anomaly_with_explainable_signals(fitted):
    _, recs, _, det = fitted
    rec = next(r for r in recs if r["user_id"] == "u-2043" and r["date"] == "2026-08-25")
    a = D.assess(det, rec)
    assert a["anomaly_band"] == "HIGH_ANOMALY"
    top = [c["feature"] for c in a["contributing_signals"]]
    assert "external_upload_volume_mb" in top and "files_downloaded" in top
    dl = next(c for c in a["contributing_signals"] if c["feature"] == "files_downloaded")
    assert dl["ratio_to_baseline"] > 15 and dl["score_drop_if_reset"] > 0


def test_band_thresholds_come_from_training_percentiles(fitted):
    cfg, _, train, det = fitted
    import numpy as np

    s = det.score(train)
    assert det.elevated == pytest.approx(float(np.percentile(s, 95)))
    assert np.mean(s >= det.high) == pytest.approx(0.01, abs=0.006)


def test_model_fingerprint_is_stable_across_platforms(fitted):
    """The fingerprint (a hash of the raw training matrix) is in every agent's input, so the
    recorded replays depend on it. np.log gave last-bit differences on some CI CPUs (fingerprint
    f762eba0fcc6 instead of this); features now use math.log. If this fails on a new platform,
    the replay recordings will miss there too."""
    assert fitted[3].fingerprint == "3c540600954b"
