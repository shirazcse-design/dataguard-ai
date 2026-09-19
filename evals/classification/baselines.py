"""Sanity-check classifiers used to validate the evaluation harness itself.

They implement the same `Classifier` interface as the real approaches. They are NOT approaches to
be compared: they exist so the harness can be shown to give known answers on known inputs.

* OracleClassifier    - returns the gold labels; the harness must report perfect scores.
* MajorityClassifier  - always predicts the training-set majority; the harness must match
                        independently computed expectations.
* RandomClassifier    - seeded uniform random; the harness must land near the analytic chance rate.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from app.classification.policy import TaxonomyPolicy
from app.classification.schemas import (
    NO_CONFIDENCE,
    CategoryPrediction,
    ClassificationRequest,
    ClassificationResult,
    LevelPrediction,
    Versions,
)

from .dataset.rng import DetRandom
from .dataset.schema import DatasetDocument


def _result(
    request: ClassificationRequest,
    policy: TaxonomyPolicy,
    level: str,
    categories: Iterable[str],
    classifier_ref: str,
) -> ClassificationResult:
    cats = sorted(set(categories))
    return ClassificationResult(
        request_id=request.request_id,
        document_id=request.document.document_id,
        content_hash=request.document.content_hash(),
        status="ok",
        level=LevelPrediction(value=level, confidence=NO_CONFIDENCE, decided_by="baseline"),
        categories=[
            CategoryPrediction(id=c, confidence=NO_CONFIDENCE, decided_by="baseline") for c in cats
        ],
        high_risk=policy.derive_high_risk(level, cats),
        versions=Versions(
            taxonomy=policy.taxonomy_version,
            high_risk_config=policy.high_risk_version,
            classifier=classifier_ref,
        ),
    )


class OracleClassifier:
    name = "oracle"
    version = "1.0"

    def __init__(self, gold: dict[str, tuple[str, list[str]]], policy: TaxonomyPolicy) -> None:
        self._gold = gold
        self._policy = policy

    @classmethod
    def from_docs(cls, docs: Iterable[DatasetDocument], policy: TaxonomyPolicy) -> OracleClassifier:
        return cls({d.doc_id: (d.gold_level, list(d.gold_categories)) for d in docs}, policy)

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        level, cats = self._gold[request.document.document_id or ""]
        return _result(request, self._policy, level, cats, f"{self.name}@{self.version}")

    def params(self) -> dict[str, Any]:
        return {"n_gold_documents": len(self._gold)}


class MajorityClassifier:
    """Predicts the training-set majority level and every category whose prevalence is >= 0.5."""

    name = "majority"
    version = "1.0"

    def __init__(self, level: str, categories: list[str], policy: TaxonomyPolicy) -> None:
        self._level = level
        self._categories = sorted(categories)
        self._policy = policy

    @classmethod
    def from_docs(
        cls, train_docs: list[DatasetDocument], policy: TaxonomyPolicy
    ) -> MajorityClassifier:
        if not train_docs:
            raise ValueError("majority baseline needs training documents")
        counts = Counter(d.gold_level for d in train_docs)
        # Highest count wins; a tie goes to the HIGHER (more protective) level, deterministically.
        level = max(counts, key=lambda lv: (counts[lv], policy.level_rank(lv)))
        n = len(train_docs)
        cats = [
            c
            for c in policy.category_ids
            if sum(c in d.gold_categories for d in train_docs) / n >= 0.5
        ]
        return cls(level, cats, policy)

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        return _result(
            request, self._policy, self._level, self._categories, f"{self.name}@{self.version}"
        )

    def params(self) -> dict[str, Any]:
        return {"level": self._level, "categories": self._categories}


class RandomClassifier:
    """Uniform random level; each category present with probability 0.5.

    The stream is derived from (seed, doc_id), so predictions are reproducible and independent of
    evaluation order.
    """

    name = "random"
    version = "1.0"

    def __init__(self, seed: int, policy: TaxonomyPolicy) -> None:
        self._seed = seed
        self._policy = policy

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        rng = DetRandom(self._seed, "random-baseline", request.document.document_id)
        level = rng.choice(self._policy.level_ids)
        cats = [c for c in self._policy.category_ids if rng.chance(0.5)]
        return _result(request, self._policy, level, cats, f"{self.name}@{self.version}")

    def params(self) -> dict[str, Any]:
        return {"seed": self._seed, "level_distribution": "uniform", "category_probability": 0.5}
