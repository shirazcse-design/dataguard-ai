"""Fusion (architecture section 11, S4): union of categories with per-category provenance, one
level, floors, and derived high-risk. Pure functions over stage results."""

from __future__ import annotations

from dataclasses import dataclass, field

from .policy import TaxonomyPolicy
from .routing_config import FusionCfg
from .schemas import (
    NO_CONFIDENCE,
    CategoryPrediction,
    ClassificationResult,
    Confidence,
    Evidence,
    LevelPrediction,
)

# Who is credited with a CATEGORY when several stages asserted it: observed rule evidence first.
_CATEGORY_PRECEDENCE = ("rules", "llm", "ml")
# Who supplies the LEVEL: the most authoritative semantic stage first, Rules last (Rules then act
# as a floor).
_LEVEL_PRECEDENCE = ("llm", "ml", "rules")


@dataclass(frozen=True)
class StageResult:
    kind: str  # "rules" | "ml" | "llm"
    label: str  # "rules", "ml", "llm:mid", ...
    result: ClassificationResult


@dataclass
class Fused:
    level: LevelPrediction
    categories: list[CategoryPrediction]
    evidence: list[Evidence]
    notes: list[str] = field(default_factory=list)  # floors applied, for the trace


def _prefixed(stage: StageResult, evidence: list[Evidence]) -> dict[str, Evidence]:
    out: dict[str, Evidence] = {}
    for e in evidence:
        new_id = f"{stage.label}.{e.evidence_id}"
        out[new_id] = e.model_copy(update={"evidence_id": new_id})
    return out


def fuse(
    policy: TaxonomyPolicy,
    cfg: FusionCfg,
    stages: list[StageResult],
    *,
    rules_is_floor: bool,
    raise_to: str | None = None,
) -> Fused | None:
    """Fuse the given stages. Returns None if no stage produced a level.

    `rules_is_floor`: the Rules level is decisive evidence (sufficient) and may act as a floor.
    `raise_to`: an extra floor (used by the injection restriction) that is never lowered.
    """
    with_level = [s for s in stages if s.result.level is not None]
    if not with_level:
        return None

    # ---- categories: union, provenance per category ------------------------------------------
    merged_ev: dict[str, Evidence] = {}
    by_cat: dict[str, list[tuple[StageResult, CategoryPrediction]]] = {}
    for s in stages:
        prefixed = _prefixed(s, s.result.evidence)
        merged_ev.update(prefixed)
        for c in s.result.categories:
            by_cat.setdefault(c.id, []).append((s, c))
    categories: list[CategoryPrediction] = []
    for cid in sorted(by_cat):
        asserted = by_cat[cid]
        lead = min(asserted, key=lambda sc: _CATEGORY_PRECEDENCE.index(sc[0].kind))
        ev_ids = [f"{s.label}.{i}" for s, c in asserted for i in c.evidence_ids]
        categories.append(
            CategoryPrediction(
                id=cid,
                confidence=lead[1].confidence,
                decided_by=lead[0].kind,  # type: ignore[arg-type]
                evidence_ids=[i for i in dict.fromkeys(ev_ids) if i in merged_ev],
            )
        )
    cat_ids = [c.id for c in categories]

    # ---- level ---------------------------------------------------------------------------------
    lead_stage = min(with_level, key=lambda s: _LEVEL_PRECEDENCE.index(s.kind))
    level_value = lead_stage.result.level.value  # type: ignore[union-attr]
    level_conf = lead_stage.result.level.confidence  # type: ignore[union-attr]
    decided_by = lead_stage.kind
    notes: list[str] = []

    def raise_level(new: str, source: str, conf: Confidence, note: str) -> None:
        nonlocal level_value, level_conf, decided_by
        if policy.level_rank(new) > policy.level_rank(level_value):
            level_value, level_conf, decided_by = new, conf, source
            notes.append(note)

    if cfg.rules_floor and rules_is_floor:
        for s in with_level:
            if s.kind == "rules":
                raise_level(
                    s.result.level.value,  # type: ignore[union-attr]
                    "rules",
                    s.result.level.confidence,  # type: ignore[union-attr]
                    "rules_floor",
                )
    if raise_to is not None:
        raise_level(raise_to, "fusion", NO_CONFIDENCE, "injection_restriction")
    if cfg.category_floors:
        floor = policy.floor_level_for(cat_ids)
        if floor is not None:
            raise_level(floor, "fusion", NO_CONFIDENCE, "category_floor")

    return Fused(
        level=LevelPrediction(value=level_value, confidence=level_conf, decided_by=decided_by),  # type: ignore[arg-type]
        categories=categories,
        evidence=list(merged_ev.values()),
        notes=notes,
    )
