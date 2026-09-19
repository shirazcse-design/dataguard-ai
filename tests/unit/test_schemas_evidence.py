"""Evidence schema: provenance must agree with evidence type (PRD 8.4 grounding contract)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.classification.schemas import Evidence


def _ev(**overrides):
    base = {
        "evidence_id": "e1",
        "source": "rules",
        "supports": {"axis": "category", "value": "PII"},
        "type": "pattern_match",
        "provenance": "observed",
    }
    base.update(overrides)
    return Evidence(**base)


def test_rules_pattern_match_is_observed():
    ev = _ev(locator={"char_start": 3, "char_end": 14, "line": 2}, strength="definitive")
    assert ev.provenance == "observed" and ev.locator.char_end == 14


@pytest.mark.parametrize(
    "t",
    ["pattern_match", "validator_passed", "dictionary_hit", "existing_label", "filename_signal"],
)
def test_observation_types_cannot_be_inferred(t):
    with pytest.raises(ValidationError, match="must have provenance 'observed'"):
        _ev(type=t, provenance="inferred")


@pytest.mark.parametrize("t", ["feature_attribution", "llm_rationale"])
def test_inference_types_cannot_be_observed(t):
    with pytest.raises(ValidationError, match="must have provenance 'inferred'"):
        _ev(source="ml", type=t, provenance="observed")
    assert _ev(source="ml", type=t, provenance="inferred").provenance == "inferred"


def test_llm_excerpt_observed_only_when_verified():
    assert _ev(source="llm", type="llm_excerpt", verified=True, provenance="observed")
    assert _ev(source="llm", type="llm_excerpt", verified=False, provenance="inferred")
    with pytest.raises(ValidationError, match="only when verified"):
        _ev(source="llm", type="llm_excerpt", verified=False, provenance="observed")
    with pytest.raises(ValidationError, match="only when verified"):
        _ev(source="llm", type="llm_excerpt", verified=True, provenance="inferred")


def test_verified_flag_only_for_llm_excerpts():
    with pytest.raises(ValidationError, match="applies only to llm_excerpt"):
        _ev(verified=True)


def test_locator_must_be_ordered():
    with pytest.raises(ValidationError, match="char_end"):
        _ev(locator={"char_start": 10, "char_end": 3})


def test_excerpt_is_length_capped_and_extra_fields_rejected():
    with pytest.raises(ValidationError):
        _ev(excerpt="x" * 501)
    with pytest.raises(ValidationError):
        _ev(raw_document="whole sensitive document")
