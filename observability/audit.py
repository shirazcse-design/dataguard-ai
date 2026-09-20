"""Privacy audit: prove that exported spans carry no document text or sensitive values.

The audit scans the WHOLE serialised span output (every key and value) for
* any run of `window` consecutive words taken from a document (case/whitespace-insensitive),
* every gold evidence span text,
* document filenames (stems), which can themselves be sensitive,
* sensitive-value patterns (SSN-like, card-like, e-mail, well-known secret prefixes).
It reports counts so a clean result is checkable, not just asserted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

_WORD = re.compile(r"\S+")
_PATTERNS = {
    "ssn_like": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "card_like": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "secret_prefix": re.compile(
        r"\b(?:dgsk_|dgtok_|AKIA[0-9A-Z]{8}|sk-[A-Za-z0-9]{8}|eyJ[A-Za-z0-9_-]{8})"
    ),
    "private_key": re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
}


_HEX_ID = re.compile(r"\b[0-9a-f]{16,}\b")  # trace/span ids and content hashes are not content


def _strings(obj: object) -> Iterator[str]:
    """Every string in a span record: keys and values, nested attributes and events."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def strings_of(spans_text: str) -> str:
    """All string content of a JSONL span dump (numbers such as timestamps are excluded)."""
    out: list[str] = []
    for line in spans_text.splitlines():
        try:
            out.extend(_strings(json.loads(line)))
        except ValueError:
            out.append(line)  # an unparseable line is scanned whole, never skipped
    return "\n".join(out)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


@dataclass
class AuditResult:
    documents: int = 0
    windows_checked: int = 0
    evidence_spans_checked: int = 0
    filenames_checked: int = 0
    pattern_checks: int = 0
    leaks: list[tuple[str, str]] = field(default_factory=list)  # (kind, document id or pattern)

    @property
    def clean(self) -> bool:
        return not self.leaks


def audit_spans(
    spans_text: str, docs: list, *, window: int = 6, min_evidence_chars: int = 12
) -> AuditResult:
    """`docs` are DatasetDocument-like objects (content, filename, doc_id, gold_evidence_spans)."""
    text = strings_of(spans_text)
    hay = _norm(text)
    pattern_text = _HEX_ID.sub(" ", text)
    res = AuditResult(documents=len(docs))
    for d in docs:
        words = _norm(d.content).split()
        for i in range(max(len(words) - window + 1, 0)):
            res.windows_checked += 1
            if " ".join(words[i : i + window]) in hay:
                res.leaks.append(("content_window", d.doc_id))
                break
        for sp in d.gold_evidence_spans:
            if len(sp.text) >= min_evidence_chars:
                res.evidence_spans_checked += 1
                if _norm(sp.text) in hay:
                    res.leaks.append(("evidence_span", d.doc_id))
        stem = d.filename.rsplit(".", 1)[0]
        if len(stem) >= 6:
            res.filenames_checked += 1
            if _norm(stem) in hay or _norm(d.filename) in hay:
                res.leaks.append(("filename", d.doc_id))
    for name, rx in _PATTERNS.items():
        res.pattern_checks += 1
        if rx.search(pattern_text):
            res.leaks.append(("pattern", name))
    return res
