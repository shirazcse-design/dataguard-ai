"""Confidence contract.  [approved DEC-10; architecture section 6]

`confidence` is NOT a single comparable number. Each value declares its *kind*, and the validator
enforces the contract:

* rule_strength           - detector certainty tier (definitive/strong/weak). Not a probability.
* calibrated_probability  - ML probability that has been calibrated on a held-out split.
* uncalibrated_score      - a raw model score before/without calibration.
* verbalized_bucket       - an LLM self-report (low/medium/high). NEVER treated as calibrated.
* none                    - not applicable (oracle / baseline classifiers).

`est_reliability` is the *measured* precision at this raw value on a dev split. It may only be set
together with the `calibration_ref` of the artifact that measured it, so an unmeasured number can
never masquerade as a reliability estimate.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import StrictModel

ConfidenceKind = Literal[
    "rule_strength",
    "calibrated_probability",
    "uncalibrated_score",
    "verbalized_bucket",
    "none",
]

RULE_STRENGTHS = ("definitive", "strong", "weak")
VERBALIZED_BUCKETS = ("low", "medium", "high")


class Confidence(StrictModel):
    kind: ConfidenceKind
    raw: float | str | None = None
    calibrated: bool = False
    calibration_ref: str | None = None
    est_reliability: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _enforce_contract(self) -> Confidence:
        k = self.kind
        if k == "rule_strength":
            if self.raw not in RULE_STRENGTHS:
                raise ValueError(f"rule_strength raw must be one of {RULE_STRENGTHS}")
            if self.calibrated:
                raise ValueError("rule_strength is never a calibrated probability")
        elif k in ("calibrated_probability", "uncalibrated_score"):
            if not isinstance(self.raw, (int, float)) or isinstance(self.raw, bool):
                raise ValueError(f"{k} raw must be a number")
            if not 0.0 <= float(self.raw) <= 1.0:
                raise ValueError(f"{k} raw must be within [0, 1]")
            if k == "calibrated_probability":
                if not self.calibrated or not self.calibration_ref:
                    raise ValueError(
                        "calibrated_probability requires calibrated=True and a calibration_ref"
                    )
            elif self.calibrated:
                raise ValueError("uncalibrated_score must have calibrated=False")
        elif k == "verbalized_bucket":
            if self.raw not in VERBALIZED_BUCKETS:
                raise ValueError(f"verbalized_bucket raw must be one of {VERBALIZED_BUCKETS}")
            if self.calibrated:
                raise ValueError(
                    "LLM self-reported confidence must never be marked calibrated (DEC-10)"
                )
        elif k == "none":
            if self.raw is not None or self.calibrated or self.est_reliability is not None:
                raise ValueError("kind 'none' carries no value")
        if self.est_reliability is not None and not self.calibration_ref:
            raise ValueError("est_reliability requires the calibration_ref that measured it")
        return self


NO_CONFIDENCE = Confidence(kind="none")
