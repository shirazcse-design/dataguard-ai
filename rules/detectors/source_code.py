"""Source-code detector.

Structure, not semantics: a code file extension plus syntax density. Publicly licensed code (an
open-source licence header) is not "Source Code" under this policy, so it is suppressed. Line
classification is per-line with bounded patterns (no cross-line backtracking).
"""

from __future__ import annotations

import re

from ..types import DetectorOutput
from .base import Detector, ScanContext

_MAX_LINE = 300
_DEF = re.compile(
    r"^(?:def|class|function|const|let|var|public|private|protected|package|func|namespace|using|import)\b"
)
_FLOW = re.compile(
    r"^(?:from\s+\S{1,80}\s+import\b|return\b.{0,120}|if\s*\(.{0,150}\)\s*\{?|for\s*\(|while\s*\(|else\s*\{|\}\s*else)"
)
_SQL = re.compile(
    r"(?i)^(?:select|create|alter|insert|update|delete|drop)\b.{0,200}(?:;|\(|\bfrom\b|\btable\b|\bset\b|\bview\b|\bindex\b|\binto\b)"
)
_ENDS = re.compile(r"^.{1,200}[;{}]$")
_ASSIGN = re.compile(r"^[\w.\[\]\"']{1,60}\s*(?::=|\+=|=)\s*.{1,150}$")
_CALL = re.compile(r"^[\w.]{1,60}\(.{0,200}\)[;{:]?$")
_DOCKER = re.compile(r"^(?:FROM|RUN|CMD|COPY|ENV|WORKDIR|ENTRYPOINT)\s+\S.{0,200}$")
_SHELL = re.compile(r"^(?:set -|export |echo |sudo |ssh |for \w+ in |done$|fi$|then$|do$|sleep )")
_COMMENT_PREFIXES = ("//", "/*", "*/", "--", "#!")


def is_code_line(line: str, comments_count: bool) -> bool:
    s = line.strip()
    if not s or len(s) > _MAX_LINE:
        return False
    if s.startswith(_COMMENT_PREFIXES):
        return comments_count or s.startswith("#!")
    return bool(
        _DEF.match(s)
        or _FLOW.match(s)
        or _SQL.match(s)
        or _ENDS.match(s)
        or _ASSIGN.match(s)
        or _CALL.match(s)
        or _DOCKER.match(s)
        or _SHELL.match(s)
    )


class CodeStructure(Detector):
    id, category = "code.structure", "SOURCE_CODE"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d = DetectorOutput(), c.doc
        cfg = c.cfg.code
        is_code_name = d.extension in {e.lower() for e in cfg.code_extensions} or (
            d.filename.lower() in {f.lower() for f in cfg.code_filenames}
        )
        lines = [ln for ln in d.text.split("\n") if ln.strip() and len(ln.strip()) <= _MAX_LINE]
        if not lines:
            return out
        n_code = sum(is_code_line(ln, comments_count=is_code_name) for ln in lines)
        density = n_code / len(lines)
        looks_like_code = n_code >= cfg.min_code_lines and density >= cfg.min_density
        if not (is_code_name or looks_like_code):
            return out
        if any(t in d.lower for t in cfg.oss_license_terms):
            out.suppressions.append(self.suppressed("oss_license", 0, 1))
            return out
        if is_code_name and looks_like_code:
            strength = "strong"
        elif is_code_name or (looks_like_code and density >= 0.5):
            strength = "weak"
        else:
            return out
        out.detections.append(
            self.found(
                c,
                strength=strength,
                kind="pattern_match",
                start=0,
                end=min(len(d.text), 1),
                masked=f"[CODE ext={d.extension or 'none'} lines={n_code}/{len(lines)}]",
            )
        )
        return out
