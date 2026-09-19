"""Taxonomy policy helpers: level floors, label validation and derived high-risk.

Pure functions over the validated config. `high_risk` is always derived here from the configured
definition [DEC-3]; nothing in the codebase predicts it directly or hard-codes its membership.
"""

from __future__ import annotations

from collections.abc import Iterable

from .schemas import HighRisk, HighRiskConfig, TaxonomyConfig
from .schemas.result import HighRiskReason


class TaxonomyPolicy:
    def __init__(self, taxonomy: TaxonomyConfig, high_risk: HighRiskConfig) -> None:
        self._taxonomy = taxonomy
        self._high_risk = high_risk
        self._rank = {lv.id: lv.rank for lv in taxonomy.levels}
        self._floors = {c.id: c.level_floor for c in taxonomy.categories}
        self._hr_categories = set(high_risk.categories)
        self._hr_levels = set(high_risk.levels)

    # -- vocab ---------------------------------------------------------------------------------
    @property
    def level_ids(self) -> list[str]:
        """Level ids ordered by ascending rank."""
        return [lv.id for lv in sorted(self._taxonomy.levels, key=lambda lv: lv.rank)]

    @property
    def category_ids(self) -> list[str]:
        return [c.id for c in self._taxonomy.categories]

    @property
    def taxonomy_version(self) -> str:
        return self._taxonomy.taxonomy_version

    @property
    def high_risk_version(self) -> str:
        return self._high_risk.high_risk_version

    @property
    def enforce_level_floors(self) -> bool:
        return self._taxonomy.constraints.enforce_level_floors

    def level_rank(self, level_id: str) -> int:
        return self._rank[level_id]

    # -- floors --------------------------------------------------------------------------------
    def floor_level_for(self, categories: Iterable[str]) -> str | None:
        """Highest level floor among `categories`, or None if there are no categories."""
        floors = [self._floors[c] for c in categories]
        if not floors:
            return None
        return max(floors, key=lambda lv: self._rank[lv])

    def violates_floor(self, level: str, categories: Iterable[str]) -> bool:
        """True if `level` is below the floor implied by `categories`."""
        floor = self.floor_level_for(categories)
        return floor is not None and self._rank[level] < self._rank[floor]

    # -- validation ----------------------------------------------------------------------------
    def label_errors(self, level: str | None, categories: Iterable[str]) -> list[str]:
        """Return human-readable problems with a (level, categories) label; empty if valid."""
        errors: list[str] = []
        cats = list(categories)
        if level is None:
            errors.append("level is missing")
        elif level not in self._rank:
            errors.append(f"unknown level {level!r}")
        unknown = [c for c in cats if c not in self._floors]
        if unknown:
            errors.append(f"unknown categories {sorted(unknown)}")
        if len(set(cats)) != len(cats):
            errors.append("duplicate categories")
        if (
            not errors
            and level is not None
            and self.enforce_level_floors
            and self.violates_floor(level, cats)
        ):
            errors.append(
                f"level {level} is below the floor {self.floor_level_for(cats)} "
                f"implied by categories {sorted(cats)}"
            )
        return errors

    # -- high risk -----------------------------------------------------------------------------
    def derive_high_risk(self, level: str | None, categories: Iterable[str]) -> HighRisk:
        """Derive high-risk from the configured definition (any listed category OR listed level)."""
        reasons: list[HighRiskReason] = []
        if level is not None and level in self._hr_levels:
            reasons.append(HighRiskReason(axis="level", value=level))
        for cat in sorted(set(categories)):
            if cat in self._hr_categories:
                reasons.append(HighRiskReason(axis="category", value=cat))
        return HighRisk(
            value=bool(reasons),
            reasons=reasons,
            config_version=self._high_risk.high_risk_version,
        )
