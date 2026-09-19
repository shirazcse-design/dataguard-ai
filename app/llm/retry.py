"""Bounded retry with exponential backoff and jitter, for transport failures only."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from .config import RetryConfig
from .types import LLMError

T = TypeVar("T")


def call_with_retry(
    fn: Callable[[], T],
    cfg: RetryConfig,
    *,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> tuple[T, int]:
    """Return (result, attempts). Raises the last LLMError when attempts are exhausted or the
    error is not retryable (auth, bad request, content filter, replay miss are never retried)."""
    rng = rng or random.Random(0)
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn(), attempt
        except LLMError as err:
            if not err.retryable or attempt >= cfg.max_attempts:
                err.attempts = attempt
                raise
            delay = min(cfg.max_delay_s, cfg.base_delay_s * (2 ** (attempt - 1)))
            if err.retry_after_s is not None:
                delay = min(cfg.max_delay_s, max(delay, err.retry_after_s))
            delay *= 1 - cfg.jitter + cfg.jitter * rng.random()
            sleep(delay)
