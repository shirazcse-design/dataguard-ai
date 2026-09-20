"""Deny-by-default attribute redaction and caller pseudonymisation (PRD 19).

Only allow-listed keys leave the process. Strings are collapsed, masked (digit runs, emails, long
tokens) and truncated. Keys that can carry document text are never allowed, even if listed by
mistake: the configuration is refused at load time.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections.abc import Mapping
from typing import Any

from guardrails.output import mask_excerpt

from .types import AttrValue

# Names that would carry document text; refused in the allow-list and always dropped.
FORBIDDEN_KEYS = frozenset(
    {
        "content",
        "text",
        "quote",
        "quotes",
        "excerpt",
        "rationale",
        "document",
        "body",
        "prompt",
        "response",
        "message",
        "filename",
        "metadata",
        "labels",
    }  # fmt: skip
)


def is_forbidden(key: str) -> bool:
    """A key is content-bearing if its FINAL segment names text (`dg.x.content`, `dg.quote`).

    `dg.content_hash`, `dg.content_bytes`, `dg.document_id` and `dg.llm.prompt_version` are safe:
    their final segment says they hold a hash, a count, an id or a version, never the text itself.
    """
    last = key.lower().replace("-", ".").replace("_", ".").split(".")[-1]
    return last in FORBIDDEN_KEYS


def pseudonymize(value: str, salt: bytes) -> str:
    """A stable, non-reversible pseudonym for a caller/session id."""
    return "p_" + hmac.new(salt, value.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def salt_from_env(env_var: str, environ: Mapping[str, str] | None = None) -> bytes:
    env = environ if environ is not None else os.environ
    value = env.get(env_var)
    return value.encode("utf-8") if value else os.urandom(16)  # per-process random if unset


# Identifier-typed keys are VALIDATED by shape, never masked (masking would destroy correlation).
_ID = r"[A-Za-z0-9._:\-]{0,80}"
ID_SHAPES: dict[str, re.Pattern[str]] = {
    "dg.request_id": re.compile(_ID),
    "dg.document_id": re.compile(_ID),
    "dg.caller": re.compile(r"p_[0-9a-f]{16}"),
    "dg.content_hash": re.compile(r"[0-9a-f]{64}"),
}
# A whitespace-free code (reason code, model id, tier) passes unless it looks like a secret.
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9._:@+/=\-]{1,120}")
_SECRETISH = re.compile(
    r"[A-Za-z0-9+/]{20,}|\d{9,}|\d{3}-\d{2}-\d{4}|[\w.+-]+@[\w-]+\.[\w.-]+|dgsk_|dgtok_|AKIA|eyJ"
)


class Redactor:
    def __init__(
        self, allowed: frozenset[str] | set[str] | list[str], max_chars: int = 120
    ) -> None:
        keys = frozenset(allowed)
        bad = sorted(k for k in keys if is_forbidden(k))
        if bad:
            raise ValueError(f"the allow-list may not contain content-bearing keys: {bad}")
        self.allowed = keys
        self.max_chars = max_chars

    def _string(self, key: str, v: str) -> str | None:
        shape = ID_SHAPES.get(key)
        if shape is not None:
            return v if shape.fullmatch(v) else None  # an id that is not id-shaped is dropped
        if _SAFE_TOKEN.fullmatch(v) and not _SECRETISH.search(v):
            return v[: self.max_chars]
        return mask_excerpt(v, self.max_chars)

    def _value(self, key: str, v: Any) -> AttrValue | None:
        if isinstance(v, bool | int | float):
            return v
        if isinstance(v, str):
            return self._string(key, v)
        if isinstance(v, list | tuple | set | frozenset):
            out = [self._string(key, str(x)) for x in list(v)[:20]]
            return [x for x in out if x is not None]
        return None  # objects, dicts, bytes: not exportable

    def scrub(self, attrs: Mapping[str, Any]) -> tuple[dict[str, AttrValue], int]:
        """Return (clean attributes, number dropped)."""
        clean: dict[str, AttrValue] = {}
        dropped = 0
        for key, raw in attrs.items():
            if key not in self.allowed or is_forbidden(key):
                dropped += 1
                continue
            value = self._value(key, raw)
            if value is None:
                dropped += 1
                continue
            clean[key] = value
        return clean, dropped
