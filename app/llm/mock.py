"""Scriptable mock client for unit tests. It produces NO evidence about any real model."""

from __future__ import annotations

from collections.abc import Callable

from .types import LLMClient, LLMError, LLMRequest, LLMResponse

Script = Callable[[LLMRequest], "str | LLMError"]


class MockLLMClient(LLMClient):
    name = "mock"

    def __init__(
        self,
        script: Script | list[str | LLMError],
        *,
        model_id: str = "mock-model",
        tokens: tuple[int, int] = (100, 50),
        latency_ms: float = 1.0,
    ) -> None:
        self._script = script
        self.model_id = model_id
        self._tokens = tokens
        self._latency = latency_ms
        self.calls: list[LLMRequest] = []

    def complete_structured(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if callable(self._script):
            out = self._script(request)
        else:
            if not self._script:
                raise LLMError("transport", "mock script exhausted")
            out = self._script.pop(0)
        if isinstance(out, LLMError):
            raise out
        return LLMResponse(
            text=out,
            model_id=self.model_id,
            prompt_tokens=self._tokens[0],
            completion_tokens=self._tokens[1],
            latency_ms=self._latency,
        )
