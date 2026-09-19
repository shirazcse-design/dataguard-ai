"""Local prompt-injection scan (a lexicon, not a guarantee).

The scan is a *supplement* to treating the document strictly as data inside delimiters. A finding
becomes a `GuardrailEvent`; it never blocks a document and never lowers a classification. The
lexicon lives in `config/guardrails/injection.v1.yaml` and is version-controlled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field, ValidationError, field_validator

from app.classification.config_loader import ConfigError, default_config_dir, read_yaml
from app.classification.schemas import GuardrailEvent
from app.classification.schemas.common import SEMVER_RE, StrictModel

INJECTION_FILE = "guardrails/injection.v1.yaml"


class InjectionPattern(StrictModel):
    id: str
    description: str
    regex: str

    @field_validator("regex")
    @classmethod
    def _compiles(cls, v: str) -> str:
        try:
            re.compile(v, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc
        return v


class InjectionConfig(StrictModel):
    guardrail_version: str
    max_scan_chars: int = Field(ge=1000)
    patterns: list[InjectionPattern] = Field(min_length=1)

    @field_validator("guardrail_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError("guardrail_version must be a semantic version")
        return v

    @field_validator("patterns")
    @classmethod
    def _unique(cls, v: list[InjectionPattern]) -> list[InjectionPattern]:
        ids = [p.id for p in v]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate injection pattern ids")
        return v


@dataclass(frozen=True)
class InjectionFinding:
    rule_id: str
    char_start: int
    char_end: int


def load_injection_config(config_dir: Path | str | None = None) -> tuple[InjectionConfig, str]:
    base = Path(config_dir) if config_dir is not None else default_config_dir()
    path = base / INJECTION_FILE
    data, digest = read_yaml(path)
    try:
        return InjectionConfig.model_validate(data), digest
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc


class InjectionScanner:
    def __init__(self, cfg: InjectionConfig) -> None:
        self.cfg = cfg
        self._compiled = [(p.id, re.compile(p.regex, re.IGNORECASE)) for p in cfg.patterns]

    @property
    def version(self) -> str:
        return self.cfg.guardrail_version

    def scan(self, text: str) -> list[InjectionFinding]:
        window = text[: self.cfg.max_scan_chars]
        out: list[InjectionFinding] = []
        for rule_id, rx in self._compiled:
            m = rx.search(window)
            if m:
                out.append(InjectionFinding(rule_id, m.start(), m.end()))
        return sorted(out, key=lambda f: (f.char_start, f.rule_id))

    def event(self, findings: list[InjectionFinding]) -> GuardrailEvent | None:
        """A guardrail event that names the rules that fired but never quotes the document."""
        if not findings:
            return None
        return GuardrailEvent(
            type="prompt_injection_suspected",
            trigger=",".join(sorted({f.rule_id for f in findings})),
            action="continued_as_data",
            detail=f"{len(findings)} instruction-override pattern(s); guardrail {self.version}",
        )
