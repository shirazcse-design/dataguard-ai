"""Application input/evidence guardrails for the Policy Copilot.

* Input size: always enforced (system hygiene, every level).
* Prompt injection in the QUESTION (levels with `input_guard`): a lexicon hit BLOCKS the question.
  A policy assistant has no legitimate need for instruction-override phrasing, and refusing is
  the conservative outcome; the user is told why.
* Prompt injection in RETRIEVED CHUNKS (levels with `chunk_injection_scan`): a flagged chunk is
  removed from the evidence the model sees and a guardrail event names the rule and chunk id.
  This supplements, and does not replace, delimiting evidence as data in the prompt.

Findings name rule ids only, never the matched text. The scanner class is UC4's
(`guardrails/injection.py`); only the pattern file is UC6's.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.classification.config_loader import ConfigError, default_config_dir, read_yaml
from guardrails.injection import InjectionConfig, InjectionScanner

from .config import GuardConfig


@dataclass(frozen=True)
class GuardVerdict:
    ok: bool
    reason: str | None = None  # input_too_long | empty | prompt_injection
    rules: tuple[str, ...] = ()


def load_policy_scanner(cfg: GuardConfig, config_dir: Path | str | None = None) -> InjectionScanner:
    path = (Path(config_dir) if config_dir else default_config_dir()) / cfg.injection_file
    data, _ = read_yaml(path)
    try:
        return InjectionScanner(InjectionConfig.model_validate(data))
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def check_question(
    question: str, cfg: GuardConfig, scanner: InjectionScanner, *, scan: bool
) -> GuardVerdict:
    if not question.strip():
        return GuardVerdict(False, "empty")
    if len(question) > cfg.max_question_chars:
        return GuardVerdict(False, "input_too_long")
    if scan:
        rules = tuple(sorted({f.rule_id for f in scanner.scan(question)}))
        if rules:
            return GuardVerdict(False, "prompt_injection", rules)
    return GuardVerdict(True)


def scan_chunk(text: str, scanner: InjectionScanner) -> tuple[str, ...]:
    return tuple(sorted({f.rule_id for f in scanner.scan(text)}))
