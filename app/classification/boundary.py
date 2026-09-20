"""The classification boundary: `classify_safely` never raises to its caller.

Architecture section 19: the classification path always returns a valid `ClassificationResult` with
a `status`. Malformed payloads and unusable text become `rejected`; an unexpected failure inside a
classifier becomes `error`. Neither ever carries a sensitivity, so nothing can be mistaken for a
low-sensitivity decision. Messages never contain document text: only short codes and field names.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from guardrails.input import InputGuardConfig, check_document, load_input_guard_config

from .interfaces import Classifier
from .schemas import ClassificationRequest, ClassificationResult, GuardrailEvent, Routing
from .schemas.common import sha256_text

NO_HASH = "0" * 64  # sentinel: there is no valid content to hash


def bare_result(
    request_id: str, digest: str, status: str, code: str, event: GuardrailEvent | None
) -> ClassificationResult:
    # A hostile request id must not break the rejection: echo it only if the schema accepts it.
    if not (isinstance(request_id, str) and 1 <= len(request_id) <= 200):
        request_id = "unknown"
    return ClassificationResult(
        request_id=request_id,
        content_hash=digest,
        status=status,  # type: ignore[arg-type]
        routing=Routing(stop_reason=code),
        guardrail_events=[event] if event else [],
        warnings=[code],
    )


def classify_safely(
    classifier: Classifier,
    payload: ClassificationRequest | dict[str, Any],
    *,
    guard: InputGuardConfig | None = None,
) -> ClassificationResult:
    guard = guard or load_input_guard_config()[0]
    if isinstance(payload, ClassificationRequest):
        request = payload
    else:
        try:
            request = ClassificationRequest.model_validate(payload)
        except ValidationError as exc:
            # field paths only: validation messages can echo the offending (sensitive) value
            fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})[:5]
            rid = payload.get("request_id") if isinstance(payload, dict) else None
            code = "invalid_request:" + ",".join(fields)
            return bare_result(
                rid if isinstance(rid, str) and rid else "unknown", NO_HASH, "rejected", code,
                GuardrailEvent(type="input_rejected", trigger="invalid_request", action="rejected"),
            )  # fmt: skip
        except Exception:  # noqa: BLE001 - a hostile payload must not escape as an exception
            return bare_result(
                "unknown", NO_HASH, "rejected", "invalid_request",
                GuardrailEvent(type="input_rejected", trigger="invalid_request", action="rejected"),
            )  # fmt: skip

    verdict = check_document(request.document, guard)
    if not verdict.ok:
        try:
            digest = request.document.content_hash()
        except UnicodeEncodeError:
            digest = NO_HASH
        return bare_result(
            request.request_id,
            digest,
            "rejected",
            f"input_rejected:{verdict.reason}",
            verdict.event(),
        )

    try:
        result = classifier.classify(request)
    except Exception as exc:  # noqa: BLE001 - the path never raises
        return bare_result(
            request.request_id, request.document.content_hash(), "error",
            f"stage_error:classifier:{type(exc).__name__}",
            GuardrailEvent(
                type="classifier_error", trigger=type(exc).__name__, action="error result"
            ),
        )  # fmt: skip
    if result.request_id != request.request_id or result.content_hash != sha256_text(
        request.document.content
    ):
        return bare_result(
            request.request_id, request.document.content_hash(), "error",
            "stage_error:classifier:contract_violation", None,
        )  # fmt: skip
    return result
