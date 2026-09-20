"""A small, dependency-free tracer with an OpenTelemetry-compatible data model.

Tracing is a NO-OP unless a trace is active: `span()` outside a trace costs a context-variable read
and changes no behaviour. A trace is one request; its spans are flushed to the sinks when the root
span ends. Spans record error CLASS names, never messages.
"""

from __future__ import annotations

import contextlib
import secrets
import threading
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol

from .redaction import Redactor
from .types import Span, SpanEvent


class Sink(Protocol):
    def write(self, spans: list[Span]) -> None: ...


class SeededIds:
    """Deterministic ids for tests and reproducible reports (never for production)."""

    def __init__(self, seed: int = 0) -> None:
        self._n = seed
        self._lock = threading.Lock()

    def _next(self, width: int) -> str:
        with self._lock:
            self._n += 1
            return f"{self._n:0{width}x}"

    def trace_id(self) -> str:
        return self._next(32)

    def span_id(self) -> str:
        return self._next(16)


class RandomIds:
    def trace_id(self) -> str:
        return secrets.token_hex(16)

    def span_id(self) -> str:
        return secrets.token_hex(8)


@dataclass
class _Ctx:
    tracer: Tracer
    trace_id: str
    current: SpanHandle | None = None
    spans: list[Span] = field(default_factory=list)


_CURRENT: ContextVar[_Ctx | None] = ContextVar("dg_trace", default=None)


class SpanHandle:
    def __init__(
        self, ctx: _Ctx, name: str, parent: SpanHandle | None, attrs: dict[str, Any]
    ) -> None:
        self._ctx = ctx
        self.name = name
        self.parent = parent
        self.span_id = ctx.tracer.ids.span_id()
        self.start_ns = ctx.tracer.clock()
        self._attrs: dict[str, Any] = dict(attrs)
        self._events: list[tuple[str, int, dict[str, Any]]] = []
        self.status = "unset"

    def set(self, **attrs: Any) -> None:
        self._attrs.update({k.replace("__", "."): v for k, v in attrs.items()})

    def fail(self, error_type: str) -> None:
        """Mark the span failed. Only the error CLASS name is recorded, never a message."""
        self.status = "error"
        self._attrs["dg.error.type"] = error_type

    def event(self, name: str, **attrs: Any) -> None:
        self._events.append(
            (name, self._ctx.tracer.clock(), {k.replace("__", "."): v for k, v in attrs.items()})
        )

    def _finish(self) -> Span:
        red = self._ctx.tracer.redactor
        clean, dropped = red.scrub(self._attrs)
        events = []
        for name, ts, a in self._events:
            ea, d = red.scrub(a)
            dropped += d
            events.append(SpanEvent(name=name, ts_ns=ts, attributes=ea))
        return Span(
            trace_id=self._ctx.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent.span_id if self.parent else None,
            name=self.name,
            start_ns=self.start_ns,
            end_ns=max(self._ctx.tracer.clock(), self.start_ns),
            status=self.status,  # type: ignore[arg-type]
            attributes=clean,
            events=events,
            dropped_attributes=dropped,
        )


class _NoopHandle:
    """Returned when no trace is active: every call is a no-op."""

    def set(self, **attrs: Any) -> None: ...
    def fail(self, error_type: str) -> None: ...
    def event(self, name: str, **attrs: Any) -> None: ...


_NOOP = _NoopHandle()


class Tracer:
    def __init__(
        self,
        sinks: list[Sink],
        redactor: Redactor,
        *,
        ids: SeededIds | RandomIds | None = None,
        clock: Callable[[], int] = time.time_ns,
    ) -> None:
        self.sinks, self.redactor = list(sinks), redactor
        self.ids = ids or RandomIds()
        self.clock = clock

    @contextlib.contextmanager
    def trace(self, name: str, **attrs: Any) -> Iterator[SpanHandle]:
        """Start a trace (one request). Nested calls join the active trace as child spans."""
        active = _CURRENT.get()
        if active is not None:
            with span(name, **attrs) as h:
                yield h  # type: ignore[misc]
            return
        ctx = _Ctx(self, self.ids.trace_id())
        token = _CURRENT.set(ctx)
        root = SpanHandle(ctx, name, None, {k.replace("__", "."): v for k, v in attrs.items()})
        ctx.current = root
        try:
            yield root
            root.status = root.status if root.status != "unset" else "ok"
        except BaseException as exc:
            root.status = "error"
            root._attrs["dg.error.type"] = type(exc).__name__
            raise
        finally:
            ctx.spans.append(root._finish())
            _CURRENT.reset(token)
            for sink in self.sinks:
                with contextlib.suppress(
                    Exception
                ):  # a broken sink must never break classification
                    sink.write(list(ctx.spans))


@contextlib.contextmanager
def span(name: str, **attrs: Any) -> Iterator[SpanHandle | _NoopHandle]:
    ctx = _CURRENT.get()
    if ctx is None:
        yield _NOOP
        return
    h = SpanHandle(ctx, name, ctx.current, {k.replace("__", "."): v for k, v in attrs.items()})
    ctx.current = h
    try:
        yield h
        h.status = h.status if h.status != "unset" else "ok"
    except BaseException as exc:
        h.status = "error"
        h._attrs["dg.error.type"] = type(exc).__name__
        raise
    finally:
        ctx.current = h.parent
        ctx.spans.append(h._finish())


def current_span() -> SpanHandle | _NoopHandle:
    ctx = _CURRENT.get()
    return ctx.current if ctx is not None and ctx.current is not None else _NOOP


def tracing_active() -> bool:
    return _CURRENT.get() is not None
