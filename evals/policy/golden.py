"""The UC6 golden set (`dataset/golden.v1.jsonl`): schema, loader, and checks against the corpus.

The golden set was written BEFORE any retrieval code existed (docs/uc6/evaluation-plan.md) and is
not edited to fit results. `expected_sections` are section keys (`POL-DLP@2.1#4.2`): relevance is
judged per policy section, so a section split into parts counts once.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from app.classification.schemas.common import StrictModel, sha256_text
from app.policy.corpus import Corpus

DEFAULT_PATH = Path(__file__).parent / "dataset" / "golden.v1.jsonl"
Category = Literal[
    "straightforward", "multi_policy", "paraphrase", "ambiguous", "insufficient", "conflict",
    "adversarial",
]  # fmt: skip
Status = Literal["ANSWERED", "INSUFFICIENT_EVIDENCE", "CONFLICT_REVIEW", "BLOCKED"]


class GoldenItem(StrictModel):
    id: str = Field(pattern=r"^[A-Z]\d{2}$")
    question: str = Field(min_length=5)
    category: Category
    expected_policy_ids: list[str]
    expected_sections: list[str]
    # Each point is a list of acceptable phrasings (case-insensitive substrings): a HEURISTIC check.
    expected_answer_points: list[list[str]]
    forbidden_answer_points: list[str]
    answerable: bool
    expected_status: Status
    acceptable_statuses: list[Status] = Field(min_length=1)
    conflict_sections: list[str]
    risk_type: str

    @model_validator(mode="after")
    def _consistent(self) -> GoldenItem:
        if self.expected_status not in self.acceptable_statuses:
            raise ValueError(f"{self.id}: expected_status must be one of acceptable_statuses")
        if self.answerable != bool(self.expected_sections):
            raise ValueError(f"{self.id}: answerable must equal 'has expected_sections'")
        ids = sorted({s.split("@")[0] for s in self.expected_sections})
        if ids != sorted(self.expected_policy_ids):
            raise ValueError(f"{self.id}: expected_policy_ids do not match expected_sections")
        return self


def load_golden(path: Path | str = DEFAULT_PATH) -> tuple[list[GoldenItem], str]:
    raw = Path(path).read_text(encoding="utf-8")
    items = [GoldenItem.model_validate(json.loads(line)) for line in raw.splitlines() if line]
    if len({i.id for i in items}) != len(items):
        raise ValueError("duplicate golden ids")
    return items, sha256_text(raw)


def check_against_corpus(items: list[GoldenItem], corpus: Corpus) -> list[str]:
    """Problems (empty = consistent): every referenced section must exist in the corpus."""
    keys = {c.section_key for c in corpus.chunks}
    problems = []
    for item in items:
        for ref in item.expected_sections + item.conflict_sections:
            if ref not in keys:
                problems.append(f"{item.id}: {ref} is not a section in the corpus")
    return problems
