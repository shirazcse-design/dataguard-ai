"""Dataset statistics (all counts are computed from the generated documents, never hand-entered)."""

from __future__ import annotations

from collections import Counter
from statistics import mean, median
from typing import Any

from app.classification.policy import TaxonomyPolicy

from .schema import SPLIT_NAMES, TIERS, DatasetDocument


def compute_stats(docs: list[DatasetDocument], policy: TaxonomyPolicy) -> dict[str, Any]:
    by_split = {s: [d for d in docs if d.split == s] for s in SPLIT_NAMES}

    def counts(sub: list[DatasetDocument]) -> dict[str, Any]:
        lengths = [len(d.content) for d in sub] or [0]
        return {
            "n_docs": len(sub),
            "n_families": len({d.family_id for d in sub}),
            "tiers": {t: sum(d.tier == t for d in sub) for t in TIERS},
            "levels": {lv: sum(d.gold_level == lv for d in sub) for lv in policy.level_ids},
            "categories": {
                c: sum(c in d.gold_categories for d in sub) for c in policy.category_ids
            },
            "docs_with_no_category": sum(not d.gold_categories for d in sub),
            "docs_with_multiple_categories": sum(len(d.gold_categories) > 1 for d in sub),
            "high_risk_docs": sum(
                policy.derive_high_risk(d.gold_level, d.gold_categories).value for d in sub
            ),
            "ambiguous_docs": sum(d.ambiguity_flag for d in sub),
            "content_chars": {
                "min": min(lengths),
                "median": round(median(lengths)),
                "mean": round(mean(lengths)),
                "max": max(lengths),
            },
        }

    return {
        "overall": counts(docs),
        "by_split": {s: counts(by_split[s]) for s in SPLIT_NAMES},
        "formats": dict(sorted(Counter(d.format for d in docs).items())),
        "generators": dict(sorted(Counter(d.generator for d in docs).items())),
        "adversarial_types": dict(
            sorted(Counter(d.adversarial_type for d in docs if d.adversarial_type).items())
        ),
        "existing_label_schemes": dict(
            sorted(Counter(lab.scheme for d in docs for lab in d.existing_labels).items())
        ),
    }
