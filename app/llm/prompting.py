"""Prompt assembly: versioned template + taxonomy generated from config + train-only few-shot."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.classification.schemas import Document
from app.classification.schemas.config_models import TaxonomyConfig
from evals.classification.dataset.schema import DatasetDocument

from .config import LLMConfig
from .fewshot import render_expected

_SYSTEM_MARK = "<!-- SYSTEM -->"
_USER_MARK = "<!-- USER -->"
_PLACEHOLDER = re.compile(r"\{\{([A-Z]+)\}\}")


def _fill(template: str, values: dict[str, str]) -> str:
    """Single-pass substitution: substituted text is never re-scanned for placeholders."""
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def render_taxonomy(tax: TaxonomyConfig) -> str:
    """The taxonomy section, generated from configuration (one source of truth)."""
    lines = ["## Sensitivity levels (choose exactly one)"]
    for lv in sorted(tax.levels, key=lambda x: x.rank):
        lines.append(f"* {lv.id} ({lv.name}): {' '.join(lv.description.split())}")
    lines += ["", "## Data categories (choose zero or more)", ""]
    lines.append(f"Level floors: {' '.join(tax.policy_defaults_notice.split())}")
    for c in tax.categories:
        lines.append(
            f"* {c.id} ({c.name}; level floor {c.level_floor}): {' '.join(c.description.split())}"
        )
        if c.positive_examples:
            lines.append("  Applies: " + "; ".join(c.positive_examples))
        if c.counter_examples:
            lines.append("  Does NOT apply: " + "; ".join(c.counter_examples))
    return "\n".join(lines)


def boundary_token(seed: int, content_hash: str, content: str) -> str:
    """Deterministic (replayable) but unknowable to the document's author: it depends on the
    content hash, so a document cannot contain its own boundary token."""
    n = 0
    while True:
        token = hashlib.sha256(f"{seed}:{content_hash}:{n}".encode()).hexdigest()[:16]
        if token not in content:
            return token
        n += 1


def _one_line(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


@dataclass(frozen=True)
class BuiltPrompt:
    system: str
    user: str
    sent_text: str  # the document text actually shown to the model (after truncation)
    truncated: bool
    token: str


class PromptBuilder:
    def __init__(
        self,
        cfg: LLMConfig,
        taxonomy: TaxonomyConfig,
        fewshot_docs: list[DatasetDocument],
        repo_root: Path,
    ) -> None:
        self.cfg = cfg
        raw = (repo_root / cfg.prompt.file).read_text(encoding="utf-8")
        self.prompt_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if _SYSTEM_MARK not in raw or _USER_MARK not in raw:
            raise ValueError("prompt file must contain the SYSTEM and USER section markers")
        system_t, user_t = raw.split(_SYSTEM_MARK, 1)[1].split(_USER_MARK, 1)
        names = {c.id: c.name for c in taxonomy.categories}
        self.fewshot_ids = [d.doc_id for d in fewshot_docs]
        self._system = _fill(
            system_t.strip(),
            {
                "TAXONOMY": render_taxonomy(taxonomy),
                "FEWSHOT": self._render_fewshot(fewshot_docs, names),
            },
        )
        self._user_t = user_t.strip()

    @property
    def system_prompt(self) -> str:
        return self._system

    @property
    def version(self) -> str:
        return self.cfg.prompt.version

    def _render_fewshot(self, docs: list[DatasetDocument], names: dict[str, str]) -> str:
        if not docs:
            return ""
        out = [
            "## Worked examples",
            "These are labeled EXAMPLES from a training set, not the document to classify.",
        ]
        for i, d in enumerate(docs, 1):
            out += [
                "",
                f"### Example {i}",
                f"Filename: {_one_line(d.filename, 200)}",
                "<<<EXAMPLE DOCUMENT>>>",
                d.content,
                "<<<END EXAMPLE DOCUMENT>>>",
                "Expected answer:",
                json.dumps(render_expected(d, names), ensure_ascii=False),
            ]
        return "\n".join(out)

    def build(self, doc: Document) -> BuiltPrompt:
        limit = self.cfg.input.max_input_chars
        sent = doc.content[:limit]
        token = boundary_token(self.cfg.seed, doc.content_hash(), sent)
        filename = (
            _one_line(doc.filename, 200) if self.cfg.input.include_filename else "(not provided)"
        )
        user = _fill(
            self._user_t,
            {"FILENAME": filename, "TOKEN": token, "CONTENT": sent},
        )
        return BuiltPrompt(
            system=self._system,
            user=user,
            sent_text=sent,
            truncated=len(doc.content) > limit,
            token=token,
        )
