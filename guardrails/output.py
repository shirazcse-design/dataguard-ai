"""Structured LLM output: schema, strict validation, and code-based evidence verification.

The model's answer is *untrusted data*. It is accepted only if it parses as exactly the expected
JSON object with labels drawn from the taxonomy. Evidence quotes are then checked against the text
that was actually sent; a quote the document does not contain is never treated as observed evidence.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import ConfigDict, Field, ValidationError, field_validator

from app.classification.schemas.common import StrictModel

Bucket = Literal["low", "medium", "high"]
BUCKET_ORDER = {"low": 0, "medium": 1, "high": 2}


class OutputValidationError(Exception):
    """Malformed model output. `reason` is a short code; the raw output is never included."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Quote(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True)  # no type coercion of model output

    quote: str
    supports_axis: Literal["level", "category"]
    supports_value: str


class LLMOutput(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True)  # "no" is not False, "1" is not True

    level: str
    categories: list[str]
    evidence: list[Quote]
    rationale: str = Field(max_length=1200)
    level_confidence: Bucket
    category_confidence: Bucket
    insufficient_information: bool

    @field_validator("categories")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate categories")
        return v


def build_json_schema(level_ids: list[str], category_ids: list[str]) -> dict[str, Any]:
    """The strict JSON schema sent to the provider, generated from the taxonomy."""
    buckets = {"type": "string", "enum": ["low", "medium", "high"]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "level", "categories", "evidence", "rationale",
            "level_confidence", "category_confidence", "insufficient_information",
        ],
        "properties": {
            "level": {"type": "string", "enum": list(level_ids)},
            "categories": {
                "type": "array",
                "items": {"type": "string", "enum": list(category_ids)},
            },
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["quote", "supports_axis", "supports_value"],
                    "properties": {
                        "quote": {"type": "string"},
                        "supports_axis": {"type": "string", "enum": ["level", "category"]},
                        "supports_value": {"type": "string"},
                    },
                },
            },
            "rationale": {"type": "string"},
            "level_confidence": buckets,
            "category_confidence": buckets,
            "insufficient_information": {"type": "boolean"},
        },
    }  # fmt: skip


def parse_llm_output(
    text: str, level_ids: list[str], category_ids: list[str], *, max_quotes: int = 8
) -> LLMOutput:
    """Parse and validate. Raises OutputValidationError(reason) with a code, never the text."""
    try:
        data = json.loads(text.strip())
    except (ValueError, TypeError) as exc:
        raise OutputValidationError("not_json") from exc
    if not isinstance(data, dict):
        raise OutputValidationError("not_an_object")
    try:
        out = LLMOutput.model_validate(data)
    except ValidationError as exc:
        raise OutputValidationError("schema_mismatch") from exc
    if out.level not in level_ids:
        raise OutputValidationError("unknown_level")
    if any(c not in category_ids for c in out.categories):
        raise OutputValidationError("unknown_category")
    for q in out.evidence:
        allowed = level_ids if q.supports_axis == "level" else category_ids
        if q.supports_value not in allowed:
            raise OutputValidationError("evidence_unknown_label")
    if len(out.evidence) > max_quotes:
        raise OutputValidationError("too_many_quotes")
    return out


def verify_quote(quote: str, sent_text: str) -> tuple[bool, int, int]:
    """Is `quote` in the text that was sent? Exact, or equal after collapsing whitespace runs.

    Returns (verified, char_start, char_end); the span is (0, 0) when not verified. A quote must
    have at least three non-space characters, so a stray letter cannot "verify".
    """
    stripped = quote.strip()
    if len(stripped.replace(" ", "")) < 3:
        return False, 0, 0
    at = sent_text.find(stripped)
    if at >= 0:
        return True, at, at + len(stripped)
    parts = stripped.split()
    if len(parts) > 1:
        m = re.search(r"\s+".join(re.escape(p) for p in parts), sent_text)
        if m:
            return True, m.start(), m.end()
    return False, 0, 0


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_TOKEN = re.compile(r"\S{20,}")
_DIGIT_TOKEN = re.compile(r"\S*\d\S*")


def mask_excerpt(quote: str, limit: int) -> str:
    """A short excerpt that never carries raw identifiers, keys or emails."""
    one = " ".join(quote.split())
    one = _EMAIL.sub("[email]", one)
    one = _LONG_TOKEN.sub("[token]", one)

    def _digits(m: re.Match[str]) -> str:
        tok = m.group(0)
        return re.sub(r"\d", "#", tok) if sum(c.isdigit() for c in tok) >= 3 else tok

    one = _DIGIT_TOKEN.sub(_digits, one)
    return one if len(one) <= limit else one[: limit - 1] + "…"


def cap_bucket(bucket: Bucket, cap: Bucket) -> Bucket:
    return bucket if BUCKET_ORDER[bucket] <= BUCKET_ORDER[cap] else cap
