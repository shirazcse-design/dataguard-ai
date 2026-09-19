"""Run a classifier over dataset documents and account for every one of them.

Failure semantics (no document is ever dropped):
* an exception inside `classify`          -> status "error",  failure "exception:<ClassName>"
* a result for the wrong request/content  -> status "error",  failure "contract_violation:*"
* labels outside the taxonomy             -> no usable prediction, failure "invalid_labels"
Exception *messages* are never recorded: they could contain document text.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from app.classification.interfaces import Classifier
from app.classification.policy import TaxonomyPolicy
from app.classification.schemas import ClassificationResult

from .dataset.schema import DatasetDocument
from .records import PredictionRecord


def _base_fields(doc: DatasetDocument, policy: TaxonomyPolicy) -> dict:
    return {
        "doc_id": doc.doc_id,
        "group_id": doc.group_id,
        "family_id": doc.family_id,
        "split": doc.split,
        "tier": doc.tier,
        "format": doc.format,
        "generator": doc.generator,
        "ambiguity_flag": doc.ambiguity_flag,
        "decoy_for": sorted(doc.decoy_for),
        "gold_level": doc.gold_level,
        "gold_categories": sorted(doc.gold_categories),
        "gold_high_risk": policy.derive_high_risk(doc.gold_level, doc.gold_categories).value,
    }


def _failure_record(doc, policy, latency_ms, status, failure) -> PredictionRecord:
    return PredictionRecord(
        **_base_fields(doc, policy),
        status=status,
        failure=failure,
        has_prediction=False,
        latency_ms=latency_ms,
    )


def _record_from_result(
    doc: DatasetDocument,
    result: ClassificationResult,
    policy: TaxonomyPolicy,
    request_id: str,
    latency_ms: float,
) -> PredictionRecord:
    if result.request_id != request_id:
        return _failure_record(doc, policy, latency_ms, "error", "contract_violation:request_id")
    if result.content_hash != doc.content_hash:
        return _failure_record(doc, policy, latency_ms, "error", "contract_violation:content_hash")

    level = result.level.value if result.level else None
    cats = sorted(c.id for c in result.categories)
    status = result.status
    usable = level is not None and status in ("ok", "degraded", "review_required")
    if usable:
        # Only the vocabulary is checked here. Floor consistency is a *classifier quality*
        # question, so it must be measured, not rejected.
        vocabulary_errors = [
            e for e in policy.label_errors(level, cats) if "below the floor" not in e
        ]
        if vocabulary_errors:
            return _failure_record(doc, policy, latency_ms, "error", "invalid_labels")

    if not usable:
        return PredictionRecord(
            **_base_fields(doc, policy),
            status=status,
            failure=f"no_label:{status}",
            has_prediction=False,
            review_required=result.review.required,
            abstained=result.routing.abstained,
            latency_ms=latency_ms,
            reported_latency_ms=result.telemetry.latency_ms.get("total"),
            est_cost_usd=result.telemetry.est_cost_usd,
        )

    derived = policy.derive_high_risk(level, cats)
    mismatch = result.high_risk is not None and result.high_risk.value != derived.value
    return PredictionRecord(
        **_base_fields(doc, policy),
        status=status,
        has_prediction=True,
        pred_level=level,
        pred_categories=cats,
        pred_high_risk=derived.value,  # never trust the classifier's own field
        review_required=result.review.required,
        abstained=result.routing.abstained,
        level_probs=result.scores.level if result.scores else None,
        category_probs=result.scores.categories if result.scores else None,
        scores_calibrated=bool(result.scores and result.scores.calibrated),
        classifier_high_risk_mismatch=mismatch,
        latency_ms=latency_ms,
        reported_latency_ms=result.telemetry.latency_ms.get("total"),
        est_cost_usd=result.telemetry.est_cost_usd,
    )


def run_classifier(
    classifier: Classifier, docs: Iterable[DatasetDocument], policy: TaxonomyPolicy
) -> list[PredictionRecord]:
    """Classify every document (in doc_id order) and return one record per document."""
    if not isinstance(classifier, Classifier):
        raise TypeError("classifier does not implement the Classifier interface")
    records: list[PredictionRecord] = []
    for doc in sorted(docs, key=lambda d: d.doc_id):
        request = doc.to_request()
        start = time.perf_counter()
        try:
            result = classifier.classify(request)
        except Exception as exc:  # noqa: BLE001 - any failure must be recorded, not raised
            latency = (time.perf_counter() - start) * 1000
            records.append(
                _failure_record(doc, policy, latency, "error", f"exception:{type(exc).__name__}")
            )
            continue
        latency = (time.perf_counter() - start) * 1000
        records.append(_record_from_result(doc, result, policy, request.request_id, latency))
    return records
