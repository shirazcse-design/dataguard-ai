"""The confidence contract (approved DEC-10): kinds are not interchangeable."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.classification.schemas import NO_CONFIDENCE, Confidence


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "rule_strength", "raw": "definitive"},
        {"kind": "rule_strength", "raw": "weak"},
        {
            "kind": "calibrated_probability",
            "raw": 0.92,
            "calibrated": True,
            "calibration_ref": "cal-1",
        },
        {"kind": "uncalibrated_score", "raw": 0.4},
        {"kind": "verbalized_bucket", "raw": "high"},
        {"kind": "none"},
        {
            "kind": "calibrated_probability",
            "raw": 0.9,
            "calibrated": True,
            "calibration_ref": "cal-1",
            "est_reliability": 0.95,
        },
    ],
)
def test_valid_confidences(kwargs):
    assert Confidence(**kwargs).kind == kwargs["kind"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"kind": "rule_strength", "raw": 0.9}, "rule_strength raw must be"),
        ({"kind": "rule_strength", "raw": "definitive", "calibrated": True}, "never a calibrated"),
        ({"kind": "calibrated_probability", "raw": 0.9}, "requires calibrated=True"),
        (
            {"kind": "calibrated_probability", "raw": 0.9, "calibrated": True},
            "calibration_ref",
        ),
        (
            {
                "kind": "calibrated_probability",
                "raw": 1.5,
                "calibrated": True,
                "calibration_ref": "c",
            },
            "within",
        ),
        (
            {
                "kind": "calibrated_probability",
                "raw": "high",
                "calibrated": True,
                "calibration_ref": "c",
            },
            "must be a number",
        ),
        ({"kind": "uncalibrated_score", "raw": 0.4, "calibrated": True}, "calibrated=False"),
        ({"kind": "verbalized_bucket", "raw": 0.9}, "verbalized_bucket raw must be"),
        (
            {"kind": "verbalized_bucket", "raw": "high", "calibrated": True},
            "never be marked calibrated",
        ),
        ({"kind": "none", "raw": 0.5}, "carries no value"),
        ({"kind": "rule_strength", "raw": "strong", "est_reliability": 0.9}, "calibration_ref"),
        (
            {
                "kind": "verbalized_bucket",
                "raw": "low",
                "est_reliability": 1.5,
                "calibration_ref": "c",
            },
            "less than or equal",
        ),
        ({"kind": "bogus"}, "Input should be"),
    ],
)
def test_invalid_confidences(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        Confidence(**kwargs)


def test_llm_confidence_can_only_gain_reliability_through_a_measured_reference():
    """DEC-10: a verbalized bucket becomes routing-usable only with a measured mapping."""
    c = Confidence(
        kind="verbalized_bucket", raw="high", est_reliability=0.93, calibration_ref="dev-run-7"
    )
    assert c.calibrated is False and c.est_reliability == 0.93


def test_no_confidence_constant():
    assert NO_CONFIDENCE.kind == "none"
