"""Per-document preprocessing: line index, header/table context, filename tokens."""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

from app.classification.schemas import Document

_DELIMS = re.compile(r"[,\t|]")
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


@dataclass
class Doc:
    """A document prepared for scanning. `text` keeps the original offsets."""

    text: str
    lower: str
    filename: str
    extension: str
    existing_labels: list[tuple[str, str]]
    metadata: dict[str, str]
    truncated: bool
    _line_starts: list[int] = field(default_factory=list, repr=False)
    header_line: str = ""  # a tabular header row, if the document looks like a table
    filename_tokens: set[str] = field(default_factory=set)

    def line_of(self, pos: int) -> int:
        """1-based line number of character offset `pos`."""
        return bisect.bisect_right(self._line_starts, pos)

    def line_bounds(self, pos: int) -> tuple[int, int]:
        idx = bisect.bisect_right(self._line_starts, pos) - 1
        start = self._line_starts[idx]
        end = self._line_starts[idx + 1] - 1 if idx + 1 < len(self._line_starts) else len(self.text)
        return start, end

    def first_lines(self, n: int) -> list[str]:
        """The first `n` non-empty lines."""
        out: list[str] = []
        for line in self.text.split("\n", 200):
            if line.strip():
                out.append(line.strip())
                if len(out) >= n:
                    break
        return out


def prepare(document: Document, max_chars: int) -> Doc:
    text = document.content
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
    name = document.filename or ""
    header = ""
    # A "tabular" document: one of its first lines has >= 2 delimiters. Its header row supplies
    # positive context (column names such as ssn / routing / account) to every row below.
    for line in text.split("\n", 6)[:5]:
        if line.strip() and len(_DELIMS.findall(line)) >= 2:
            header = line.lower()
            break
    return Doc(
        text=text,
        lower=text.lower(),
        filename=name,
        extension=(document.extension or "").lower().lstrip("."),
        existing_labels=[(lab.scheme, lab.value) for lab in document.existing_labels],
        metadata=dict(document.metadata),
        truncated=truncated,
        _line_starts=starts,
        header_line=header,
        filename_tokens={t for t in _TOKEN_SPLIT.split(name.lower()) if t},
    )
