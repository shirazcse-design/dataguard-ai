"""OpenTelemetry-compatible span data model (ids, parents, ns timestamps, attributes, events)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.classification.schemas.common import StrictModel

AttrValue = str | int | float | bool | list[str]


class SpanEvent(StrictModel):
    name: str
    ts_ns: int = Field(ge=0)
    attributes: dict[str, AttrValue] = Field(default_factory=dict)


class Span(StrictModel):
    trace_id: str = Field(min_length=32, max_length=32)
    span_id: str = Field(min_length=16, max_length=16)
    parent_span_id: str | None = None
    name: str
    start_ns: int = Field(ge=0)
    end_ns: int = Field(ge=0)
    status: Literal["ok", "error", "unset"] = "unset"
    attributes: dict[str, AttrValue] = Field(default_factory=dict)
    events: list[SpanEvent] = Field(default_factory=list)
    dropped_attributes: int = Field(default=0, ge=0)  # attributes removed by the redactor

    @property
    def duration_ms(self) -> float:
        return (self.end_ns - self.start_ns) / 1e6
