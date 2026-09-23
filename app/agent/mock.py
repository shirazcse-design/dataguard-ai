"""A scripted `AgentLLMClient` for deterministic tests, matching `app.llm.mock.MockLLMClient`'s
pattern: no unit test drives the real Foundry tool-calling client end to end."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .types import AgentError, AgentTurn

Script = Callable[[list[dict[str, Any]]], AgentTurn]


class MockAgentClient:
    name = "mock"

    def __init__(self, script: Script | list[AgentTurn | AgentError]) -> None:
        self._script = script
        self.calls: list[list[dict[str, Any]]] = []

    def next_turn(self, messages: list[dict[str, Any]]) -> AgentTurn:
        self.calls.append(messages)
        if callable(self._script):
            out = self._script(messages)
        else:
            if not self._script:
                raise AgentError("transport", "mock script exhausted")
            out = self._script.pop(0)
        if isinstance(out, AgentError):
            raise out
        return out
