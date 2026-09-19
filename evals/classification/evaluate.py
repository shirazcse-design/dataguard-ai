"""Evaluate a classifier on dataset documents and produce metrics + a reproducible run manifest."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import sklearn

from app.classification.config_loader import ConfigBundle
from app.classification.interfaces import Classifier

from .bootstrap import bootstrap_intervals
from .dataset.schema import DatasetDocument
from .lock import LockedTestAuthorization, check_access
from .metrics import compact, compute_metrics, round_floats
from .records import PredictionRecord
from .runner import run_classifier

HARNESS_VERSION = "1.0.0"
REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------------------------
# Handling of deferred (review_required) documents: alternative, clearly-labeled views
# ---------------------------------------------------------------------------------------------
def _deferred_as_errors(records: list[PredictionRecord]) -> list[PredictionRecord]:
    return [
        r.model_copy(update={"has_prediction": False, "failure": "deferred"}) if r.deferred else r
        for r in records
    ]


def _perfect_reviewer(records: list[PredictionRecord]) -> list[PredictionRecord]:
    return [
        r.model_copy(
            update={
                "has_prediction": True,
                "pred_level": r.gold_level,
                "pred_categories": list(r.gold_categories),
                "pred_high_risk": r.gold_high_risk,
                "failure": None,
            }
        )
        if r.deferred
        else r
        for r in records
    ]


def _auto_only(records: list[PredictionRecord]) -> list[PredictionRecord]:
    return [r for r in records if r.status in ("ok", "degraded") and r.has_prediction]


def _latency(values: list[float], percentiles: list[int]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    arr = np.array(values, dtype=float)
    out: dict[str, Any] = {
        "n": len(values),
        "mean_ms": float(arr.mean()),
        "max_ms": float(arr.max()),
        "total_ms": float(arr.sum()),
    }
    for p in percentiles:
        out[f"p{p}_ms"] = float(np.percentile(arr, p))
    return out


# ---------------------------------------------------------------------------------------------
# Metrics for a whole run
# ---------------------------------------------------------------------------------------------
def build_metrics(records: list[PredictionRecord], bundle: ConfigBundle) -> dict[str, Any]:
    """Headline (T1-T4), all-tier and sliced metrics. Deterministic given the records + config."""
    policy, cfg = bundle.policy, bundle.eval
    levels, cats = policy.level_ids, policy.category_ids
    headline_tiers = set(cfg.headline_tiers)
    headline = [r for r in records if r.tier in headline_tiers]

    primary = compute_metrics(headline, levels, cats)
    n_deferred = sum(r.deferred for r in headline)
    if n_deferred:
        deferral_views: dict[str, Any] = {
            "n_deferred": n_deferred,
            "auto_only": compact(compute_metrics(_auto_only(headline), levels, cats)),
            "deferred_as_errors": compact(
                compute_metrics(_deferred_as_errors(headline), levels, cats)
            ),
            "deferred_resolved_by_perfect_reviewer_HYPOTHETICAL": compact(
                compute_metrics(_perfect_reviewer(headline), levels, cats)
            ),
        }
    else:
        deferral_views = {"n_deferred": 0, "note": "no deferrals: all views are identical"}

    intervals = bootstrap_intervals(
        headline,
        levels,
        cats,
        n_resamples=cfg.bootstrap.n_resamples,
        confidence_level=cfg.bootstrap.confidence_level,
        seed=cfg.bootstrap.seed,
        unit=cfg.bootstrap.unit,
    )

    slices: dict[str, dict[str, Any]] = {}
    for field in cfg.slice_fields:
        groups: dict[str, list[PredictionRecord]] = {}
        for r in records:
            groups.setdefault(str(getattr(r, field)), []).append(r)
        slices[field] = {
            value: {
                **compact(compute_metrics(subset, levels, cats)),
                "small_sample": len(subset) < cfg.min_support_flag,
            }
            for value, subset in sorted(groups.items())
        }

    small = {
        "warning_code": "SMALL_SAMPLE",
        "min_support_flag": cfg.min_support_flag,
        # label -> number of gold positives in the evaluated headline subset (only labels below
        # the threshold are listed; full per-label counts are in the per_class / per_label tables)
        "levels": {
            lv: m["support"]
            for lv, m in primary["level"]["per_class"].items()
            if m["support"] < cfg.min_support_flag
        },
        "categories": {
            c: m["support"]
            for c, m in primary["categories"]["per_label"].items()
            if m["support"] < cfg.min_support_flag
        },
    }
    return {
        "headline": {
            "tiers": sorted(headline_tiers),
            "note": "T5 (adversarial) is excluded here and reported as its own slice.",
            "metrics": primary,
            "confidence_intervals": intervals,
            "deferral_views": deferral_views,
            "small_sample_labels": small,
        },
        "all_tiers": compute_metrics(records, levels, cats),
        "slices": slices,
    }


def fingerprint(deterministic_metrics: dict[str, Any]) -> str:
    """SHA-256 over the deterministic part of the metrics (no latency), floats rounded."""
    blob = json.dumps(round_floats(deterministic_metrics), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------------------------
# Run manifest
# ---------------------------------------------------------------------------------------------
def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def git_info() -> dict[str, Any]:
    status = _git("status", "--porcelain")
    return {
        "commit": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": None if status is None else bool(status),
    }


def build_run_manifest(
    *,
    classifier: Classifier,
    docs: list[DatasetDocument],
    dataset_manifest: dict[str, Any],
    bundle: ConfigBundle,
    started_at: datetime,
    finished_at: datetime,
    metrics_fingerprint: str,
    cli_args: dict[str, Any] | None,
    locked_test_authorization: LockedTestAuthorization | None = None,
) -> dict[str, Any]:
    splits = sorted({d.split for d in docs})
    stamp = f"{started_at:%Y%m%dT%H%M%SZ}"
    run_id = f"{classifier.name}-{'+'.join(splits)}-{stamp}-{metrics_fingerprint[:8]}"
    git = git_info()
    locked = "test" in splits
    manifest = {
        "run_id": run_id,
        "harness_version": HARNESS_VERSION,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "git": git,
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
        },
        "classifier": {
            "name": classifier.name,
            "version": classifier.version,
            "params": classifier.params(),
        },
        "dataset": {
            "dataset_id": dataset_manifest["dataset_id"],
            "dataset_version": dataset_manifest["dataset_version"],
            "dataset_sha256": dataset_manifest["dataset_sha256"],
            "spec_hash": dataset_manifest["spec_hash"],
            "label_status": dataset_manifest["label_status"],
            "splits_evaluated": splits,
            "n_documents": len(docs),
            "n_groups": len({d.group_id for d in docs}),
            "evaluated_locked_test_split": locked,
            "locked_test_authorization": (
                locked_test_authorization.to_dict() if locked_test_authorization else None
            ),
        },
        "config": {"versions": bundle.versions(), "file_hashes": bundle.file_hashes},
        "eval_settings": {
            "headline_tiers": bundle.eval.headline_tiers,
            "bootstrap": bundle.eval.bootstrap.model_dump(),
            "min_support_flag": bundle.eval.min_support_flag,
            "reference_targets": bundle.eval.reference_targets,
        },
        "cli_args": cli_args or {},
        "metrics_fingerprint": metrics_fingerprint,
        "reproducibility": (
            "Given the same dataset_sha256, config file_hashes, classifier name/version/params and "
            "bootstrap seed, every metric (everything except latency) is reproduced exactly; "
            "metrics_fingerprint is the SHA-256 of that deterministic content."
        ),
    }
    if locked:
        # A consolidated audit record: everything needed to answer "who touched the locked test
        # split, with what, when, and under which authorisation".
        manifest["locked_test_access"] = {
            "authorized": locked_test_authorization is not None,
            "authorization": locked_test_authorization.to_dict()
            if locked_test_authorization
            else None,
            "git_commit": git["commit"],
            "git_dirty": git["dirty"],
            "dataset_sha256": dataset_manifest["dataset_sha256"],
            "config_versions": bundle.versions(),
            "classifier": f"{classifier.name}@{classifier.version}",
            "classifier_params": classifier.params(),
            "timestamp": started_at.isoformat(),
        }
    return manifest


# ---------------------------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------------------------
@dataclass
class EvaluationResult:
    run_id: str
    records: list[PredictionRecord]
    metrics: dict[str, Any]  # deterministic metrics + a separate "latency" block
    fingerprint: str
    manifest: dict[str, Any]


def evaluate(
    classifier: Classifier,
    docs: list[DatasetDocument],
    bundle: ConfigBundle,
    dataset_manifest: dict[str, Any],
    cli_args: dict[str, Any] | None = None,
    locked_test_authorization: LockedTestAuthorization | None = None,
) -> EvaluationResult:
    """Evaluate `classifier` on `docs`.

    Raises LockedTestSplitError if any document belongs to the locked test split and no
    `locked_test_authorization` is supplied.
    """
    check_access({d.split for d in docs}, locked_test_authorization)
    started = datetime.now(UTC)
    records = run_classifier(classifier, docs, bundle.policy)
    deterministic = build_metrics(records, bundle)
    fp = fingerprint(deterministic)
    finished = datetime.now(UTC)

    pcts = bundle.eval.latency_percentiles
    metrics = {
        **deterministic,
        "latency": {
            "wall_clock": _latency([r.latency_ms for r in records], pcts),
            "classifier_reported": _latency(
                [r.reported_latency_ms for r in records if r.reported_latency_ms is not None], pcts
            ),
            "note": "Excluded from the metrics fingerprint (varies run to run).",
        },
        "cost": {
            "documents_with_reported_cost": sum(r.est_cost_usd is not None for r in records),
            "est_cost_usd_total": (
                sum(r.est_cost_usd for r in records if r.est_cost_usd is not None)
                if any(r.est_cost_usd is not None for r in records)
                else None
            ),
        },
    }
    manifest = build_run_manifest(
        classifier=classifier,
        docs=docs,
        dataset_manifest=dataset_manifest,
        bundle=bundle,
        started_at=started,
        finished_at=finished,
        metrics_fingerprint=fp,
        cli_args=cli_args,
        locked_test_authorization=locked_test_authorization,
    )
    return EvaluationResult(manifest["run_id"], records, metrics, fp, manifest)
