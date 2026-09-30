"""Policy corpus ingestion: parse -> section chunks -> metadata.

Each corpus file is one policy VERSION: YAML front matter followed by numbered Markdown sections
(`## 4.` / `### 4.2`). The policy's own section numbers are the chunk and citation unit, so a
citation such as `POL-DLP §4.2` always names a section a reader can open. A section longer than
`max_words` is split into overlapping parts that keep the same section number.

Chunk ids are deterministic (`POL-DLP@2.1#4.2`, `/p2` for later parts): the golden set, the
embedding cache and citation verification all key off them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from app.classification.schemas.common import StrictModel, sha256_text

from .config import ChunkingConfig

Status = Literal["current", "superseded", "draft"]

_HEADING = re.compile(r"^(#{2,3})\s+(\d+(?:\.\d+)*)\.?\s+(.+?)\s*$")
_POLICY_ID = re.compile(r"^POL-[A-Z0-9-]+$")
_VERSION = re.compile(r"^\d+\.\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CorpusError(Exception):
    """A corpus file is malformed. Raised at ingest time, never silently skipped."""


class PolicyMeta(StrictModel):
    policy_id: str = Field(pattern=_POLICY_ID.pattern)
    title: str = Field(min_length=1)
    version: str = Field(pattern=_VERSION.pattern)
    effective_date: str = Field(pattern=_DATE.pattern)
    status: Status
    supersedes: str | None = Field(default=None, pattern=_VERSION.pattern)
    policy_owner: str = Field(min_length=1)
    source: str = Field(min_length=1)


class Chunk(StrictModel):
    chunk_id: str
    policy_id: str
    title: str
    version: str
    effective_date: str
    status: Status
    supersedes: str | None
    policy_owner: str
    source: str
    section: str
    heading: str
    part: int = Field(ge=1)
    body: str  # the section text only: what citation quotes are verified against
    index_text: str  # what is embedded and keyword-indexed (body, optionally with a header)
    content_hash: str

    @property
    def citation(self) -> str:
        """Human-readable citation. Non-current versions always say which version they are."""
        if self.status == "current":
            return f"{self.policy_id} §{self.section}"
        return f"{self.policy_id} v{self.version} §{self.section}"

    @property
    def section_key(self) -> str:
        """`POL-DLP@2.1#4.2`: the chunk id without a part suffix (the golden-set relevance unit)."""
        return f"{self.policy_id}@{self.version}#{self.section}"


def _front_matter(text: str, path: Path) -> tuple[PolicyMeta, str]:
    if not text.startswith("---\n"):
        raise CorpusError(f"{path.name}: missing YAML front matter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise CorpusError(f"{path.name}: unterminated YAML front matter")
    try:
        meta = PolicyMeta.model_validate(yaml.safe_load(text[4:end]))
    except (yaml.YAMLError, ValueError) as exc:
        raise CorpusError(f"{path.name}: invalid front matter: {exc}") from exc
    return meta, text[end + 5 :]


def _sections(body: str, path: Path) -> list[tuple[str, str, str]]:
    """`(section, heading, text)` for every numbered heading that has text of its own."""
    out: list[tuple[str, str, str]] = []
    current: tuple[str, str] | None = None
    lines: list[str] = []
    seen: set[str] = set()

    def flush() -> None:
        text = " ".join(" ".join(lines).split())
        if current is not None and text:
            out.append((current[0], current[1], text))

    for line in body.splitlines():
        m = _HEADING.match(line)
        if m:
            flush()
            section = m.group(2)
            if section in seen:
                raise CorpusError(f"{path.name}: duplicate section {section}")
            seen.add(section)
            current, lines = (section, m.group(3)), []
        elif line.startswith("#"):
            continue  # the document title (`# ...`) is carried by the metadata
        else:
            lines.append(line)
    flush()
    if not out:
        raise CorpusError(f"{path.name}: no numbered sections")
    return out


def _parts(text: str, max_words: int, overlap: int) -> list[str]:
    words = text.split()
    if len(words) <= max_words:
        return [text]
    step = max_words - overlap
    return [" ".join(words[i : i + max_words]) for i in range(0, len(words) - overlap, step)]


def chunk_document(path: Path, cfg: ChunkingConfig) -> tuple[PolicyMeta, list[Chunk]]:
    meta, body = _front_matter(path.read_text(encoding="utf-8"), path)
    chunks: list[Chunk] = []
    for section, heading, text in _sections(body, path):
        parts = _parts(text, cfg.max_words, cfg.overlap_words)
        for n, part in enumerate(parts, start=1):
            chunk_id = f"{meta.policy_id}@{meta.version}#{section}"
            if len(parts) > 1:
                chunk_id += f"/p{n}"
            header = f"{meta.title} §{section} {heading}. "
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    **meta.model_dump(),
                    section=section,
                    heading=heading,
                    part=n,
                    body=part,
                    index_text=(header + part) if cfg.contextual_header else part,
                    content_hash=sha256_text(part),
                )
            )
    return meta, chunks


class Corpus:
    """The ingested corpus: every policy version and its chunks, in a stable order."""

    def __init__(self, docs: list[PolicyMeta], chunks: list[Chunk]) -> None:
        self.docs = docs
        self.chunks = chunks
        self.by_id = {c.chunk_id: c for c in chunks}
        if len(self.by_id) != len(chunks):
            raise CorpusError("duplicate chunk ids across the corpus")
        versions = [(d.policy_id, d.version) for d in docs]
        if len(set(versions)) != len(versions):
            raise CorpusError("two corpus files declare the same policy_id and version")

    def versions_of(self, policy_id: str) -> list[PolicyMeta]:
        return sorted(
            (d for d in self.docs if d.policy_id == policy_id), key=lambda d: d.effective_date
        )

    def fingerprint(self) -> str:
        """Changes whenever any chunk's id, text or metadata changes: recorded in every report,
        and the key under which recorded embeddings are valid."""
        return sha256_text("\n".join(c.model_dump_json() for c in self.chunks))


def load_corpus(corpus_dir: Path | str, cfg: ChunkingConfig) -> Corpus:
    paths = sorted(p for p in Path(corpus_dir).glob("*.md") if p.name != "README.md")
    if not paths:
        raise CorpusError(f"no policy files in {corpus_dir}")
    docs: list[PolicyMeta] = []
    chunks: list[Chunk] = []
    for path in paths:
        meta, doc_chunks = chunk_document(path, cfg)
        docs.append(meta)
        chunks.extend(doc_chunks)
    return Corpus(docs, chunks)
