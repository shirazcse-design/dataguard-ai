"""Replay/record cache adapter.

Responses are keyed by `(prompt_version, model_id, input_hash)` (architecture section 10). A miss is
an `LLMError("replay_miss")`: it is NEVER silently answered with something else. In record mode a
miss is forwarded to an inner client and the real response is stored, so one real run becomes a
committed cache that CI can replay without any network or credentials.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from .types import LLMClient, LLMError, LLMRequest, LLMResponse

_SAFE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
CACHE_SCHEMA = 1


def _safe(part: str, what: str) -> str:
    if not _SAFE.match(part) or part in (".", ".."):
        raise ValueError(f"unsafe {what} for a cache path: {part!r}")
    return part


class ReplayLLMClient(LLMClient):
    name = "replay"

    def __init__(
        self, cache_dir: Path | str, model_id: str, *, inner: LLMClient | None = None
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.model_id = _safe(model_id, "model_id")
        self.inner = inner  # when set, misses are recorded from it

    def _path(self, prompt_version: str, input_hash: str) -> Path:
        return (
            self.cache_dir
            / self.model_id
            / _safe(prompt_version, "prompt_version")
            / f"{_safe(input_hash, 'input_hash')}.json"
        )

    def complete_structured(self, request: LLMRequest) -> LLMResponse:
        path = self._path(request.prompt_version, request.input_hash())
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("schema") != CACHE_SCHEMA or data.get("input_hash") != request.input_hash():
                raise LLMError("replay_miss", "cache entry does not match its key")
            return LLMResponse(
                text=data["text"],
                model_id=self.model_id,
                served_model=data.get("served_model"),
                prompt_tokens=data.get("prompt_tokens"),
                completion_tokens=data.get("completion_tokens"),
                latency_ms=float(data.get("latency_ms", 0.0)),
                cached=True,
            )
        if self.inner is None:
            raise LLMError("replay_miss", "no recorded response for this input")
        live = self.inner.complete_structured(request)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": CACHE_SCHEMA,
                    "prompt_version": request.prompt_version,
                    "model_id": self.model_id,
                    "input_hash": request.input_hash(),
                    "text": live.text,
                    "served_model": live.served_model,
                    "prompt_tokens": live.prompt_tokens,
                    "completion_tokens": live.completion_tokens,
                    "latency_ms": live.latency_ms,
                    "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "recorded_from": self.inner.name,
                },
                indent=1,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return live
