"""Input guard: validates a document BEFORE any stage sees it.

Rejection reasons are short codes; the document text is never included in a reason or a message.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import Field, ValidationError, model_validator

from app.classification.config_loader import ConfigError, default_config_dir, read_yaml
from app.classification.schemas import Document, GuardrailEvent
from app.classification.schemas.common import SEMVER_RE, StrictModel

INPUT_FILE = "guardrails/input.v1.yaml"


class InputGuardConfig(StrictModel):
    guardrail_version: str
    min_non_space_chars: int = Field(ge=1)
    soft_max_bytes: int = Field(ge=1000)
    hard_max_bytes: int = Field(ge=1000)
    max_replacement_char_ratio: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _ok(self) -> InputGuardConfig:
        if not SEMVER_RE.match(self.guardrail_version):
            raise ValueError("guardrail_version must be a semantic version")
        if self.hard_max_bytes < self.soft_max_bytes:
            raise ValueError("hard_max_bytes must be >= soft_max_bytes")
        return self


@dataclass(frozen=True)
class InputVerdict:
    ok: bool
    reason: str | None = None  # empty_content | undecodable_text | oversize
    truncate: bool = False  # above the soft limit: stages will truncate and flag it

    def event(self) -> GuardrailEvent | None:
        if self.ok and not self.truncate:
            return None
        if self.ok:
            return GuardrailEvent(
                type="input_truncation", trigger="soft_max_bytes", action="truncated"
            )
        return GuardrailEvent(
            type="input_rejected", trigger=self.reason or "invalid", action="rejected"
        )


def load_input_guard_config(config_dir: Path | str | None = None) -> tuple[InputGuardConfig, str]:
    base = Path(config_dir) if config_dir is not None else default_config_dir()
    path = base / INPUT_FILE
    data, digest = read_yaml(path)
    try:
        return InputGuardConfig.model_validate(data), digest
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc


def check_document(doc: Document, cfg: InputGuardConfig) -> InputVerdict:
    """Order: undecodable first (nothing else can be trusted), then empty, then size."""
    text = doc.content
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return InputVerdict(False, "undecodable_text")
    if text and text.count("�") / len(text) > cfg.max_replacement_char_ratio:
        return InputVerdict(False, "undecodable_text")
    if len("".join(text.split())) < cfg.min_non_space_chars:
        return InputVerdict(False, "empty_content")
    if size > cfg.hard_max_bytes:
        return InputVerdict(False, "oversize")
    return InputVerdict(True, truncate=size > cfg.soft_max_bytes)
