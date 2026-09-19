"""Detector base class and helpers."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import RulesConfig
from ..context import ContextMatcher
from ..masking import excerpt_hash
from ..preprocess import Doc
from ..types import Axis, Detection, DetectorOutput, EvidenceKind, Strength, Suppression


@dataclass
class ScanContext:
    doc: Doc
    cfg: RulesConfig
    matcher: ContextMatcher


class Detector:
    """One deterministic detector. Subclasses set `id`, `category` and implement `detect`."""

    id: str = ""
    version: str = "1.0.0"
    category: str | None = None  # the category this detector supports (None for level markers)

    def __init__(
        self, cfg: RulesConfig, matcher: ContextMatcher, level_rank: dict[str, int]
    ) -> None:
        self.cfg = cfg
        self.matcher = matcher
        self.level_rank = level_rank  # taxonomy level id -> rank (higher = more sensitive)
        self.setup()

    def setup(self) -> None:
        """Compile config-dependent patterns once (called from __init__)."""

    def detect(self, c: ScanContext) -> DetectorOutput:  # pragma: no cover - interface
        raise NotImplementedError

    # ---- helpers ----------------------------------------------------------------------------
    def found(
        self,
        c: ScanContext,
        *,
        strength: Strength,
        kind: EvidenceKind,
        start: int,
        end: int,
        masked: str,
        axis: Axis = "category",
        value: str | None = None,
        source: str = "rules",
        level_hint: str | None = None,
    ) -> Detection:
        raw = c.doc.text[start:end]
        return Detection(
            detector_id=self.id,
            detector_version=self.version,
            axis=axis,
            value=value if value is not None else (self.category or ""),
            strength=strength,
            kind=kind,
            start=start,
            end=end,
            line=c.doc.line_of(start),
            masked_excerpt=masked,
            excerpt_hash=excerpt_hash(raw),
            source=source,  # type: ignore[arg-type]
            level_hint=level_hint,
        )

    def suppressed(self, reason: str, start: int, end: int) -> Suppression:
        return Suppression(self.id, reason, start, end)
