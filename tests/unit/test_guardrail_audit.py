"""Second-opinion guardrail audit: aggregation logic, tested with a fake Content Safety client.

The fake stands in for `ContentSafetyClient` (duck-typed: only `shield_prompt` /
`detect_groundedness` are called), so this never touches the network. The real client's REST
shape is covered separately in test_azure_content_safety.py.
"""

from __future__ import annotations

from app.classification.schemas import ClassificationResult, GuardrailEvent, Routing
from evals.classification.guardrail_audit import audit_groundedness, audit_injection
from guardrails.azure_content_safety import ContentSafetyError


def result(request_id="r1", flagged=False):
    events = (
        [GuardrailEvent(type="prompt_injection_suspected", trigger="t", action="flagged")]
        if flagged
        else []
    )
    return ClassificationResult(
        request_id=request_id, content_hash="0" * 64, status="rejected",
        routing=Routing(), guardrail_events=events, warnings=["x"],
    )  # fmt: skip


class FakeShield:
    def __init__(self, flagged: bool, *, raises: str | None = None):
        self.flagged, self.raises = flagged, raises

    def shield_prompt(self, *, documents, user_prompt=""):
        if self.raises:
            raise ContentSafetyError(self.raises)
        return {"attack_detected": self.flagged}


class FakeGrounded:
    def __init__(self, grounded: bool, *, raises: str | None = None):
        self.grounded, self.raises = grounded, raises

    def detect_groundedness(self, *, text, grounding_sources):
        if self.raises:
            raise ContentSafetyError(self.raises)
        return {"ungrounded_detected": not self.grounded}


# ---- injection audit ----------------------------------------------------------------------------
def test_no_client_means_every_row_is_unscored_not_a_silent_pass():
    report = audit_injection([("doc text", result(flagged=True))], client=None)
    assert report["n_documents"] == 1
    assert report["n_scored_by_azure"] == 0
    assert report["agreement_rate"] is None
    assert report["rows"][0] == {
        "request_id": "r1",
        "custom_flagged": True,
        "azure_flagged": None,
        "azure_error": None,
    }


def test_agreement_when_both_flag_the_same_document():
    report = audit_injection([("x", result(flagged=True))], client=FakeShield(True))
    assert report["agreement_rate"] == 1.0
    assert report["custom_only_flagged"] == 0 and report["azure_only_flagged"] == 0


def test_disagreement_is_counted_on_the_right_side():
    custom_only = audit_injection([("x", result(flagged=True))], client=FakeShield(False))
    assert custom_only["agreement_rate"] == 0.0 and custom_only["custom_only_flagged"] == 1
    azure_only = audit_injection([("x", result(flagged=False))], client=FakeShield(True))
    assert azure_only["agreement_rate"] == 0.0 and azure_only["azure_only_flagged"] == 1


def test_a_content_safety_error_excludes_the_row_from_scoring_never_counts_as_agreement():
    report = audit_injection(
        [("x", result(flagged=True))], client=FakeShield(False, raises="timeout")
    )
    assert report["n_scored_by_azure"] == 0 and report["agreement_rate"] is None
    assert report["rows"][0]["azure_error"] == "timeout"


def test_no_row_carries_document_text():
    report = audit_injection([("the actual sensitive document text", result())], client=None)
    assert "the actual sensitive document text" not in str(report)


# ---- groundedness audit --------------------------------------------------------------------------
def test_groundedness_no_client_means_unscored():
    report = audit_groundedness([("excerpt", "source", True)], client=None)
    assert report["n_claims"] == 1 and report["n_scored_by_azure"] == 0
    assert report["agreement_rate"] is None


def test_groundedness_agreement_and_disagreement():
    agree = audit_groundedness([("e", "s", True)], client=FakeGrounded(True))
    assert agree["agreement_rate"] == 1.0
    disagree = audit_groundedness([("e", "s", True)], client=FakeGrounded(False))
    assert disagree["agreement_rate"] == 0.0


def test_groundedness_error_excludes_from_scoring():
    report = audit_groundedness([("e", "s", True)], client=FakeGrounded(True, raises="http_error"))
    assert report["n_scored_by_azure"] == 0
    assert report["rows"][0]["azure_error"] == "http_error"


def test_no_row_carries_the_excerpt_or_source_text():
    report = audit_groundedness(
        [("a very specific evidence excerpt", "the source document", True)], client=None
    )
    assert "a very specific evidence excerpt" not in str(report)
    assert "the source document" not in str(report)


def test_aggregate_rate_over_several_claims():
    client = FakeGrounded(True)
    report = audit_groundedness([("e1", "s1", True), ("e2", "s2", False)], client=client)
    # e1: custom True, azure grounded=True -> agree; e2: custom False, azure grounded=True -> disagree
    assert report["n_scored_by_azure"] == 2 and report["agreement_rate"] == 0.5
