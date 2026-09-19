"""Shared test fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.classification.config_loader import ConfigBundle, default_config_dir, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def bundle() -> ConfigBundle:
    return load_config()


@pytest.fixture()
def config_copy(tmp_path: Path) -> Path:
    """A private, mutable copy of the real config directory."""
    dst = tmp_path / "config"
    shutil.copytree(default_config_dir(), dst)
    return dst


@pytest.fixture(scope="session")
def rules_engine(bundle):
    """A RulesEngine built from the real rules config."""
    from rules.config import load_rules_config
    from rules.engine import RulesEngine

    cfg, _ = load_rules_config(bundle.policy)
    return RulesEngine(cfg, bundle.policy)


@pytest.fixture(scope="session")
def rules_classifier(bundle):
    from rules import build_rules_classifier

    return build_rules_classifier(bundle)


@pytest.fixture(scope="session")
def analyze(rules_engine):
    """analyze(text, filename='x.txt', labels=()) -> RulesResult"""
    from app.classification.schemas import Document, ExistingLabel

    def _run(text, filename="x.txt", labels=()):
        ext = filename.rsplit(".", 1)[1] if "." in filename else ""
        doc = Document(
            content=text,
            filename=filename,
            extension=ext,
            existing_labels=[ExistingLabel(scheme=s, value=v) for s, v in labels],
        )
        return rules_engine.analyze(doc)

    return _run
