"""The UC1 golden set: 30 DLP investigation cases, written before any recording or replay result
and never edited to fit results. Expected outcomes reflect the TRUE nature of the data and the
destination (what an analyst should conclude), not what UC4 predicts - so a UC4 error that
propagates (D24) counts against UC1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from app.classification.schemas.common import StrictModel, sha256_text
from app.dlp.schemas import DLPEvent, Outcome

DEFAULT_PATH = Path(__file__).parent / "dataset" / "golden.v1.jsonl"


class DlpCase(StrictModel):
    id: str = Field(pattern=r"^D\d{2}$")
    category: Literal["safe", "warn", "risky", "flagship", "hitl", "adversarial", "tool_failure"]
    risk_type: str
    event: DLPEvent
    expected_outcome: Outcome
    acceptable_outcomes: list[Outcome] = Field(min_length=1)
    high_risk: bool
    destination_class: str
    expected_policy_sections: list[str]
    required_tools: list[str]

    @model_validator(mode="after")
    def _ok(self) -> DlpCase:
        if self.expected_outcome not in self.acceptable_outcomes:
            raise ValueError(f"{self.id}: expected outcome must be acceptable")
        if self.event.case_id != self.id:
            raise ValueError(f"{self.id}: event.case_id must equal the case id")
        if self.high_risk != (self.expected_outcome in ("ESCALATE", "HUMAN_REVIEW")):
            raise ValueError(f"{self.id}: high_risk must mean expected ESCALATE/HUMAN_REVIEW")
        return self


def load_golden(path: Path | str = DEFAULT_PATH) -> tuple[list[DlpCase], str]:
    raw = Path(path).read_text(encoding="utf-8")
    cases = [DlpCase.model_validate(json.loads(x)) for x in raw.splitlines() if x]
    if len({c.id for c in cases}) != len(cases):
        raise ValueError("duplicate case ids")
    return cases, sha256_text(raw)
