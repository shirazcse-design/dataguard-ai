"""Configuration loading, validation and cross-file consistency."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.classification.config_loader import (
    CONFIG_DIR_ENV,
    ConfigError,
    load_config,
)
from app.classification.schemas import TaxonomyConfig

# The approved INITIAL level-floor defaults (configurable synthetic policy defaults).
# If these are changed deliberately after evaluation, update this test in the same commit.
APPROVED_FLOORS = {
    "PII": "CONFIDENTIAL",
    "PHI": "HIGHLY_CONFIDENTIAL",
    "FINANCIAL_PCI": "HIGHLY_CONFIDENTIAL",
    "SOURCE_CODE": "CONFIDENTIAL",
    "CREDENTIALS_SECRETS": "HIGHLY_CONFIDENTIAL",
    "INTELLECTUAL_PROPERTY": "CONFIDENTIAL",
    "TRADE_SECRET": "HIGHLY_CONFIDENTIAL",
    "MA_CORP_STRATEGY": "HIGHLY_CONFIDENTIAL",
}


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"test setup: {old!r} not found in {path.name}"
    path.write_text(text.replace(old, new, 1))


def test_real_config_loads(bundle):
    assert bundle.versions() == {"taxonomy": "1.0.0", "high_risk": "1.0.0", "eval": "1.0.0"}
    assert set(bundle.file_hashes) == {
        "taxonomy/taxonomy.v1.yaml",
        "taxonomy/high_risk.v1.yaml",
        "eval/eval.v1.yaml",
    }


def test_two_axis_taxonomy_shape(bundle):
    assert bundle.policy.level_ids == ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_CONFIDENTIAL"]
    assert sorted(bundle.policy.category_ids) == sorted(APPROVED_FLOORS)
    assert len(bundle.policy.category_ids) == 8


def test_level_floors_match_approved_initial_defaults(bundle):
    floors = {c.id: c.level_floor for c in bundle.taxonomy.categories}
    assert floors == APPROVED_FLOORS


def test_taxonomy_documents_that_floors_are_defaults(bundle):
    notice = bundle.taxonomy.policy_defaults_notice.lower()
    assert "default" in notice and "not universal" in notice


def test_high_risk_matches_approved_initial_definition(bundle):
    hr = bundle.high_risk
    assert sorted(hr.categories) == sorted(
        ["PII", "PHI", "FINANCIAL_PCI", "CREDENTIALS_SECRETS", "TRADE_SECRET"]
    )
    assert hr.levels == ["HIGHLY_CONFIDENTIAL"]
    assert hr.combine == "any"


def test_eval_config_headline_excludes_adversarial(bundle):
    assert "T5" not in bundle.eval.headline_tiers
    assert bundle.eval.bootstrap.n_resamples >= 1


def test_env_override_selects_config_dir(config_copy, monkeypatch):
    _edit(
        config_copy / "taxonomy/taxonomy.v1.yaml",
        "taxonomy_version: 1.0.0",
        "taxonomy_version: 1.0.1",
    )
    _edit(
        config_copy / "taxonomy/high_risk.v1.yaml",
        "taxonomy_version: 1.0.0",
        "taxonomy_version: 1.0.1",
    )
    monkeypatch.setenv(CONFIG_DIR_ENV, str(config_copy))
    assert load_config().taxonomy.taxonomy_version == "1.0.1"


def test_hashes_change_when_file_changes(config_copy):
    before = load_config(config_copy).file_hashes["eval/eval.v1.yaml"]
    _edit(config_copy / "eval/eval.v1.yaml", "min_support_flag: 25", "min_support_flag: 30")
    assert load_config(config_copy).file_hashes["eval/eval.v1.yaml"] != before


def test_missing_file_is_config_error(config_copy):
    (config_copy / "taxonomy/high_risk.v1.yaml").unlink()
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(config_copy)


def test_duplicate_yaml_key_is_rejected(config_copy):
    _edit(
        config_copy / "eval/eval.v1.yaml",
        "min_support_flag: 25",
        "min_support_flag: 25\nmin_support_flag: 5",
    )
    with pytest.raises(ConfigError, match="duplicate key"):
        load_config(config_copy)


def test_unknown_field_is_rejected(config_copy):
    _edit(
        config_copy / "eval/eval.v1.yaml",
        "min_support_flag: 25",
        "min_support_flag: 25\nsurprise: true",
    )
    with pytest.raises(ConfigError, match="schema validation failed"):
        load_config(config_copy)


def test_floor_referencing_unknown_level_is_rejected(config_copy):
    _edit(
        config_copy / "taxonomy/taxonomy.v1.yaml",
        "level_floor: CONFIDENTIAL",
        "level_floor: SECRET",
    )
    with pytest.raises(ConfigError, match="not a level"):
        load_config(config_copy)


def test_high_risk_unknown_category_is_rejected(config_copy):
    _edit(config_copy / "taxonomy/high_risk.v1.yaml", "  - PII\n", "  - PII\n  - NOT_A_CATEGORY\n")
    with pytest.raises(ConfigError, match="unknown categories"):
        load_config(config_copy)


def test_high_risk_unknown_level_is_rejected(config_copy):
    _edit(config_copy / "taxonomy/high_risk.v1.yaml", "  - HIGHLY_CONFIDENTIAL", "  - TOP_SECRET")
    with pytest.raises(ConfigError, match="unknown levels"):
        load_config(config_copy)


def test_taxonomy_version_mismatch_fails_fast(config_copy):
    _edit(
        config_copy / "taxonomy/high_risk.v1.yaml",
        "taxonomy_version: 1.0.0",
        "taxonomy_version: 2.0.0",
    )
    with pytest.raises(ConfigError, match="written against taxonomy 2.0.0"):
        load_config(config_copy)


def test_non_semver_version_is_rejected(config_copy):
    _edit(
        config_copy / "taxonomy/taxonomy.v1.yaml", "taxonomy_version: 1.0.0", "taxonomy_version: v1"
    )
    with pytest.raises(ConfigError, match="semantic version"):
        load_config(config_copy)


def _taxonomy_dict(bundle):
    return bundle.taxonomy.model_dump()


def test_taxonomy_schema_rejects_duplicate_level_ids(bundle):
    data = _taxonomy_dict(bundle)
    data["levels"][1]["id"] = "PUBLIC"
    with pytest.raises(ValueError, match="duplicate level ids"):
        TaxonomyConfig.model_validate(data)


def test_taxonomy_schema_rejects_noncontiguous_ranks(bundle):
    data = _taxonomy_dict(bundle)
    data["levels"][3]["rank"] = 7
    with pytest.raises(ValueError, match="contiguous"):
        TaxonomyConfig.model_validate(data)


def test_taxonomy_schema_rejects_duplicate_category_ids(bundle):
    data = _taxonomy_dict(bundle)
    data["categories"][1]["id"] = data["categories"][0]["id"]
    with pytest.raises(ValueError, match="duplicate category ids"):
        TaxonomyConfig.model_validate(data)


def test_taxonomy_schema_rejects_id_used_as_level_and_category(bundle):
    data = _taxonomy_dict(bundle)
    data["categories"][0]["id"] = "PUBLIC"
    with pytest.raises(ValueError, match="both level and category"):
        TaxonomyConfig.model_validate(data)


def test_taxonomy_schema_rejects_bad_id_format(bundle):
    data = _taxonomy_dict(bundle)
    data["categories"][0]["id"] = "pii-lowercase"
    with pytest.raises(ValueError, match="UPPER_SNAKE_CASE"):
        TaxonomyConfig.model_validate(data)
