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
