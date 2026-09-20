"""Instrumentation helpers: outcome attributes and a tracing wrapper around any Classifier.

`TracedClassifier` starts one trace per request and records request and outcome attributes. It
delegates unchanged: the wrapped classifier's results are identical with tracing on or off.
"""

from __future__ import annotations

from typing import Any

from app.classification.boundary import NO_HASH
from app.classification.interfaces import Classifier
from app.classification.schemas import ClassificationRequest, ClassificationResult

from .redaction import pseudonymize
from .trace import Tracer, current_span


def request_attrs(request: ClassificationRequest, salt: bytes) -> dict[str, Any]:
    doc = request.document
    try:
        digest, size = doc.content_hash(), doc.size_bytes
    except UnicodeEncodeError:
        digest, size = NO_HASH, doc.size_bytes
    return {
        "dg.request_id": request.request_id,
        "dg.document_id": doc.document_id or "",
        "dg.caller": pseudonymize(request.caller.caller_id, salt),
        "dg.content_hash": digest,
        "dg.content_bytes": size or 0,
        "dg.extension": doc.extension,
    }


def outcome_attrs(result: ClassificationResult) -> dict[str, Any]:
    """What was decided, never why in words: no evidence text, rationale or excerpt."""
    lv = result.level
    return {
        "dg.outcome.status": result.status,
        "dg.outcome.level": lv.value if lv else "",
        "dg.outcome.level_decided_by": lv.decided_by if lv else "",
        "dg.outcome.level_confidence_kind": lv.confidence.kind if lv else "",
        "dg.outcome.n_categories": len(result.categories),
        "dg.outcome.categories": [c.id for c in result.categories],
        "dg.outcome.high_risk": bool(result.high_risk and result.high_risk.value),
        "dg.outcome.review_required": result.review.required,
        "dg.outcome.review_reasons": list(result.review.reason_codes),
        "dg.outcome.provisional": result.review.provisional,
        "dg.outcome.warnings_count": len(result.warnings),
        "dg.stop_reason": result.routing.stop_reason or "",
        "dg.escalations": result.routing.escalations,
        "dg.short_circuited": result.routing.short_circuited,
        "dg.stages_run": list(result.routing.stages_run),
        "dg.latency_ms": float(result.telemetry.latency_ms.get("total", 0.0)),
        "dg.tokens_in": int(result.telemetry.tokens.get("prompt", 0)),
        "dg.tokens_out": int(result.telemetry.tokens.get("completion", 0)),
    }


def emit_guardrail_events(result: ClassificationResult) -> None:
    sp = current_span()
    for g in result.guardrail_events:
        sp.event(
            "guardrail",
            dg__guardrail__type=g.type,
            dg__guardrail__trigger=g.trigger,
            dg__guardrail__action=g.action,
        )


class TracedClassifier:
    """Wraps a classifier so each `classify` call is one trace."""

    def __init__(self, inner: Classifier, tracer: Tracer, salt: bytes) -> None:
        self.inner, self._tracer, self._salt = inner, tracer, salt
        self.name = inner.name
        self.version = inner.version

    def params(self) -> dict[str, Any]:
        return self.inner.params()

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        attrs = request_attrs(request, self._salt)
        attrs["dg.classifier"] = self.inner.name
        attrs["dg.classifier_version"] = self.inner.version
        variant = getattr(self.inner, "variant", None)
        if variant:
            attrs["dg.variant"] = variant
        with self._tracer.trace("classify", **attrs) as root:
            result = self.inner.classify(request)
            root.set(**outcome_attrs(result))
            emit_guardrail_events(result)
            if result.status == "error":
                root.fail("error_result")
            return result
