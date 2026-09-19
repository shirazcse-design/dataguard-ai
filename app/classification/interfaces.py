"""The common `Classifier` interface.

Rules (A), ML (B), LLM (C), Hybrid (D), and the evaluation baselines all implement this, so the
evaluation harness and the future service drive exactly the same code path [architecture section 1].
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .schemas import ClassificationRequest, ClassificationResult


@runtime_checkable
class Classifier(Protocol):
    name: str
    version: str

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        """Classify one document. Must return a valid result; failures are values (`status`)."""
        ...

    def params(self) -> dict[str, Any]:
        """JSON-serialisable parameters recorded in the run manifest (seeds, thresholds, ...)."""
        ...
