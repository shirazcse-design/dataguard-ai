"""Shared constants and base model for UC4 schemas."""

from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = "1.0"

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class StrictModel(BaseModel):
    """Base model: unknown fields are rejected so typos in configs/payloads fail loudly."""

    model_config = ConfigDict(extra="forbid")


def sha256_text(text: str) -> str:
    """Hex SHA-256 of UTF-8 text (used for content hashes; never log the text itself)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
