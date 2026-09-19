"""Provider-neutral request/response types for the LLM client interface."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field

from app.classification.schemas.common import StrictModel

ErrorKind = Literal[
    "timeout", "rate_limited", "transport", "auth", "bad_request", "content_filtered",
    "replay_miss", "not_configured",
]  # fmt: skip
RETRYABLE: frozenset[str] = frozenset({"timeout", "rate_limited", "transport"})


class LLMError(Exception):
    """A provider failure. Messages never contain the prompt or the document."""

    def __init__(self, kind: ErrorKind, detail: str = "", retry_after_s: float | None = None):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind: ErrorKind = kind
        self.retry_after_s = retry_after_s
        self.attempts = 1

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


class LLMRequest(StrictModel):
    system: str
    user: str
    schema_name: str = "classification"
    json_schema: dict[str, Any]
    prompt_version: str
    temperature: float = 0.0
    max_output_tokens: int = Field(gt=0)
    timeout_s: float = Field(gt=0)

    def input_hash(self) -> str:
        """Hash of everything that determines the model's input (used as the replay key)."""
        blob = json.dumps(
            {
                "system": self.system,
                "user": self.user,
                "schema": self.json_schema,
                "temperature": self.temperature,
                "max_output_tokens": self.max_output_tokens,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class LLMResponse(StrictModel):
    text: str
    model_id: str
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float = Field(ge=0)
    cached: bool = False  # True when replayed from a recorded run (latency is the RECORDED one)


class LLMClient:
    """Interface. `complete_structured` returns the raw JSON text; validation lives elsewhere."""

    name = "llm-client"
    model_id: str = "unknown"

    def complete_structured(self, request: LLMRequest) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError
