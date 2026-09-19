"""Deterministic Rules Engine (Approach A): detectors, engine and the standalone classifier.

No rule match does not mean Public. Rules may abstain; the standalone benchmark applies a
configurable default level only so it can be scored.
"""

from .classifier import RulesClassifier, build_rules_classifier
from .config import RulesConfig, load_rules_config
from .engine import RulesEngine
from .types import RulesResult

__all__ = [
    "RulesClassifier",
    "RulesConfig",
    "RulesEngine",
    "RulesResult",
    "build_rules_classifier",
    "load_rules_config",
]
