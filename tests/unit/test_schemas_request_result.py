"""ClassificationRequest / ClassificationResult schemas and the Classifier interface."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.classification.interfaces import Classifier
from app.classification.schemas import (
    NO_CONFIDENCE,
    ClassificationRequest,
    ClassificationResult,
    Confidence,
    Document,
    Evidence,
    sha256_text,
)


def _doc(**kw):
    base = {
        "content": "Q3 acquisition shortlist",
        "filename": "Acquisition_Targets_2027.xlsx",
        "extension": "xlsx",
    }
    base.update(kw)
    return Document(**base)


def test_request_defaults_and_size_bytes_computed():
    req = ClassificationRequest(request_id="r1", document=_doc(content="héllo"))
    assert req.schema_version == "1.0"
    assert req.document.size_bytes == len("héllo".encode())  # bytes, not characters
    assert req.options.mode == "hybrid" and req.options.include_evidence is True
    assert req.caller.caller_id == "local"


def test_content_hash_is_sha256_of_content_and_stable():
    d = _doc()
    assert d.content_hash() == sha256_text(d.content) == _doc().content_hash()
    assert _doc(content="different").content_hash() != d.content_hash()


def test_request_rejects_unknown_fields_and_bad_mode():
    with pytest.raises(ValidationError):
        ClassificationRequest(request_id="r", document=_doc(), surprise=1)
    with pytest.raises(ValidationError):
        ClassificationRequest(request_id="r", document=_doc(), options={"mode": "agentic"})


def _ok_result(**kw):
    base = {
        "request_id": "r1",
        "content_hash": "abc",
        "status": "ok",
        "level": {
            "value": "HIGHLY_CONFIDENTIAL",
            "confidence": NO_CONFIDENCE.model_dump(),
            "decided_by": "baseline",
        },
        "high_risk": {
            "value": True,
            "reasons": [{"axis": "level", "value": "HIGHLY_CONFIDENTIAL"}],
            "config_version": "1.0.0",
        },
    }
    base.update(kw)
    return ClassificationResult(**base)


def test_valid_result_roundtrips_through_json():
    res = _ok_result(
        categories=[
            {
                "id": "MA_CORP_STRATEGY",
                "confidence": {"kind": "verbalized_bucket", "raw": "high"},
                "decided_by": "llm",
                "evidence_ids": ["e1"],
            }
        ],
        evidence=[
            Evidence(
                evidence_id="e1",
                source="llm",
                supports={"axis": "category", "value": "MA_CORP_STRATEGY"},
                type="llm_rationale",
                provenance="inferred",
            )
        ],
    )
    again = ClassificationResult.model_validate_json(res.model_dump_json())
    assert again == res


@pytest.mark.parametrize("status", ["ok", "degraded"])
def test_ok_and_degraded_require_level_and_high_risk(status):
    with pytest.raises(ValidationError, match="requires a level"):
        ClassificationResult(request_id="r", content_hash="h", status=status)
    with pytest.raises(ValidationError, match="high_risk"):
        _ok_result(status=status, high_risk=None)


@pytest.mark.parametrize("status", ["rejected", "error"])
def test_rejected_and_error_may_omit_label(status):
    res = ClassificationResult(request_id="r", content_hash="h", status=status, warnings=["boom"])
    assert res.level is None and res.high_risk is None


def test_review_required_status_and_review_block_must_agree():
    review = {
        "required": True,
        "reason_codes": ["LOW_CONFIDENCE"],
        "provisional": True,
        "priority": 1,
    }
    res = ClassificationResult(
        request_id="r", content_hash="h", status="review_required", review=review
    )
    assert res.review.provisional
    with pytest.raises(ValidationError, match="requires review.required"):
        ClassificationResult(request_id="r", content_hash="h", status="review_required")
    with pytest.raises(ValidationError, match="incompatible"):
        _ok_result(review=review)


def test_review_block_rules():
    from app.classification.schemas import ReviewDecision

    with pytest.raises(ValidationError, match="reason code"):
        ReviewDecision(required=True)
    with pytest.raises(ValidationError, match="only allowed"):
        ReviewDecision(required=False, reason_codes=["LOW_CONFIDENCE"])
    with pytest.raises(ValidationError):
        ReviewDecision(required=True, reason_codes=["NOT_A_REASON"])


def test_duplicate_categories_and_dangling_evidence_ids_rejected():
    cat = {"id": "PII", "confidence": NO_CONFIDENCE.model_dump(), "decided_by": "baseline"}
    with pytest.raises(ValidationError, match="duplicate category"):
        _ok_result(categories=[cat, cat])
    with pytest.raises(ValidationError, match="unknown evidence ids"):
        _ok_result(categories=[{**cat, "evidence_ids": ["nope"]}])


def test_duplicate_evidence_ids_rejected():
    ev = Evidence(
        evidence_id="e1",
        source="rules",
        supports={"axis": "level", "value": "PUBLIC"},
        type="existing_label",
        provenance="observed",
    )
    with pytest.raises(ValidationError, match="duplicate evidence ids"):
        _ok_result(evidence=[ev, ev])


def test_classifier_protocol_is_runtime_checkable():
    class Dummy:
        name = "dummy"
        version = "0"

        def classify(self, request):  # pragma: no cover - never called
            raise NotImplementedError

        def params(self):
            return {}

    class NotAClassifier:
        name = "x"

    assert isinstance(Dummy(), Classifier)
    assert not isinstance(NotAClassifier(), Classifier)


def test_confidence_type_is_reused_in_results():
    res = _ok_result()
    assert isinstance(res.level.confidence, Confidence)


def test_size_bytes_is_recomputed_so_a_caller_cannot_lie():
    from app.classification.schemas import Document

    d = Document(content="héllo", filename="a.txt", extension="txt", size_bytes=1)
    assert d.size_bytes == len("héllo".encode())
    lone = Document(content="x\ud800", filename="a.txt", extension="txt", size_bytes=7)
    assert lone.size_bytes == 7  # undecodable: the caller's figure is kept, the guard rejects it
    assert Document(content="x\ud800", filename="a.txt", extension="txt").size_bytes == 0
