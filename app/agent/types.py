"""Shared types between the real Foundry tool-calling client and the scripted test client, so the
loop drives both identically."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ParsedToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AgentTurn:
    """Exactly one of `tool_calls` (non-empty) or `final_text` (not None) is populated, matching
    the chat-completions contract: an assistant message either calls tools or gives a final answer,
    never both in this design."""

    tool_calls: list[ParsedToolCall] = field(default_factory=list)
    final_text: str | None = None


class AgentError(Exception):
    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind = kind


class AgentLLMClient(Protocol):
    def next_turn(self, messages: list[dict[str, Any]]) -> AgentTurn:
        """One planner turn given the conversation so far. Raises `AgentError` on failure - never
        returns a fabricated turn."""
        ...
