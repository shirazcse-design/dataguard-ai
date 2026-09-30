"""Grounded generation: prompt + strict JSON schema over labelled evidence, and two generators.

The model sees evidence as `<policy_evidence id="E1" ...>` blocks and may cite ONLY those labels.
It never sees chunk ids or section numbers as something it could type into a citation: the
harness maps a label back to its chunk, so a citation the model invents cannot name a real policy
section (a label outside E1..En is recorded as a fabricated citation and dropped).

* `LLMGenerator`: the UC4 `LLMClient` stack (Foundry, or its record/replay cache) on the UC4 `mid`
  tier (`uc4-llm-medium`).
* `ExtractiveGenerator`: OFFLINE and deterministic, for tests and runs with no recordings. It quotes
  the first sentence of the top evidence. It is labelled `offline-extractive` everywhere and can
  neither detect conflicts nor decline; it exists to exercise the harness, not to answer well.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field, ValidationError

from app.classification.schemas.common import StrictModel
from app.llm.config import RetryConfig
from app.llm.retry import call_with_retry
from app.llm.types import LLMClient, LLMError, LLMRequest

from .config import GenerationConfig
from .schemas import Evidence, LLMCall

OFFLINE_GENERATOR_ID = "offline-extractive"
_CLOSE_TAG = re.compile(r"</?\s*policy_evidence", re.IGNORECASE)


class RawClaim(StrictModel):
    text: str = Field(min_length=1)
    evidence_id: str
    quote: str


class ModelOutput(StrictModel):
    status: Literal["ANSWERED", "INSUFFICIENT_EVIDENCE", "CONFLICT"]
    claims: list[RawClaim]
    conflict_evidence_ids: list[str]
    conflict_note: str


JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "claims", "conflict_evidence_ids", "conflict_note"],
    "properties": {
        "status": {"type": "string", "enum": ["ANSWERED", "INSUFFICIENT_EVIDENCE", "CONFLICT"]},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "evidence_id", "quote"],
                "properties": {
                    "text": {"type": "string"},
                    "evidence_id": {"type": "string"},
                    "quote": {"type": "string"},
                },
            },
        },
        "conflict_evidence_ids": {"type": "array", "items": {"type": "string"}},
        "conflict_note": {"type": "string"},
    },
}


def _attr(value: str) -> str:
    return value.replace('"', "'").replace("<", "(").replace(">", ")")


def render_evidence(evidence: list[Evidence]) -> str:
    blocks = []
    for e in evidence:
        body = _CLOSE_TAG.sub("[policy_evidence tag removed]", e.body)
        blocks.append(
            f'<policy_evidence id="{e.evidence_id}" policy="{_attr(e.title)}" '
            f'section="{_attr(e.section + " " + e.heading)}" version="{e.version}" '
            f'status="{e.status}" effective_date="{e.effective_date}">\n{body}\n</policy_evidence>'
        )
    return "\n\n".join(blocks)


def build_user_message(question: str, evidence: list[Evidence]) -> str:
    q = _CLOSE_TAG.sub("", question).replace("</question>", "")
    return (
        f"<question>\n{q}\n</question>\n\n"
        f"Policy evidence ({len(evidence)} blocks, retrieved for this question):\n\n"
        f"{render_evidence(evidence)}\n\n"
        "Answer using only this evidence, in the required JSON format."
    )


@dataclass
class Generation:
    output: ModelOutput | None
    call: LLMCall | None
    error: str | None = None  # an error KIND (never provider text)
    raw_invalid: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


class Generator(Protocol):
    mode: str  # replay | live | offline
    model_id: str

    def generate(self, question: str, evidence: list[Evidence]) -> Generation: ...


class LLMGenerator:
    def __init__(
        self,
        client: LLMClient,
        cfg: GenerationConfig,
        retry: RetryConfig,
        system_prompt: str,
        *,
        temperature: float | None,
        mode: str,
    ) -> None:
        self.client = client
        self.cfg = cfg
        self.retry = retry
        self.system = system_prompt
        self.temperature = temperature
        self.mode = mode
        self.model_id = client.model_id

    def request(self, question: str, evidence: list[Evidence]) -> LLMRequest:
        return LLMRequest(
            system=self.system,
            user=build_user_message(question, evidence),
            schema_name="policy_answer",
            json_schema=JSON_SCHEMA,
            prompt_version=self.cfg.prompt_version,
            temperature=self.temperature,
            max_output_tokens=self.cfg.max_output_tokens,
            timeout_s=self.cfg.timeout_s,
        )

    def generate(self, question: str, evidence: list[Evidence]) -> Generation:
        req = self.request(question, evidence)
        try:
            resp, attempts = call_with_retry(
                lambda: self.client.complete_structured(req), self.retry
            )
        except LLMError as err:
            return Generation(None, None, error=err.kind)
        call = LLMCall(
            model_id=resp.model_id,
            served_model=resp.served_model,
            cached=resp.cached,
            tokens_in=resp.prompt_tokens,
            tokens_out=resp.completion_tokens,
            latency_ms=round(resp.latency_ms, 1),
            attempts=attempts,
        )
        try:
            output = ModelOutput.model_validate(json.loads(resp.text))
        except (ValueError, ValidationError):
            return Generation(None, call, error="output_invalid", raw_invalid=True)
        return Generation(output, call)


class ExtractiveGenerator:
    mode = "offline"
    model_id = OFFLINE_GENERATOR_ID

    def generate(self, question: str, evidence: list[Evidence]) -> Generation:
        claims = []
        for e in evidence[:2]:
            sentence = re.split(r"(?<=[.!?])\s+", e.body.strip())[0]
            claims.append(RawClaim(text=sentence, evidence_id=e.evidence_id, quote=sentence))
        status = "ANSWERED" if claims else "INSUFFICIENT_EVIDENCE"
        return Generation(
            ModelOutput(status=status, claims=claims, conflict_evidence_ids=[], conflict_note=""),
            None,
        )


def load_system_prompt(repo: Path, cfg: GenerationConfig) -> str:
    return (repo / cfg.prompt_file).read_text(encoding="utf-8").strip()
