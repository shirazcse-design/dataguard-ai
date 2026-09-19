"""Level floors, label validation and configurable high-risk derivation."""

from __future__ import annotations

import pytest

from app.classification.policy import TaxonomyPolicy
from app.classification.schemas import HighRiskConfig

HC = "HIGHLY_CONFIDENTIAL"


def test_level_rank_orders_levels(bundle):
    p = bundle.policy
    ranks = [p.level_rank(lv) for lv in p.level_ids]
    assert ranks == sorted(ranks) == [0, 1, 2, 3]


@pytest.mark.parametrize(
    ("cats", "expected"),
    [
        ([], None),
        (["SOURCE_CODE"], "CONFIDENTIAL"),
        (["PII", "SOURCE_CODE"], "CONFIDENTIAL"),
        (["PII", "PHI"], HC),
        (["MA_CORP_STRATEGY"], HC),
    ],
)
def test_floor_level_for(bundle, cats, expected):
    assert bundle.policy.floor_level_for(cats) == expected


def test_acquisition_targets_example_from_decision_1(bundle):
    """Approved example: Acquisition_Targets_2027.xlsx -> Highly Confidential + M&A."""
    p = bundle.policy
    assert p.label_errors(HC, ["MA_CORP_STRATEGY"]) == []
    hr = p.derive_high_risk(HC, ["MA_CORP_STRATEGY"])
    assert hr.value is True
    # High-risk purely via level: M&A is NOT in the approved high-risk category list.
    assert [(r.axis, r.value) for r in hr.reasons] == [("level", HC)]


@pytest.mark.parametrize(
    ("level", "cats", "is_high", "reason_pairs"),
    [
        ("PUBLIC", [], False, []),
        ("INTERNAL", [], False, []),
        ("CONFIDENTIAL", ["SOURCE_CODE"], False, []),
        ("CONFIDENTIAL", ["INTELLECTUAL_PROPERTY"], False, []),
        ("CONFIDENTIAL", ["PII"], True, [("category", "PII")]),
        (HC, [], True, [("level", HC)]),
        (HC, ["PHI", "PII"], True, [("level", HC), ("category", "PHI"), ("category", "PII")]),
        ("CONFIDENTIAL", ["TRADE_SECRET"], True, [("category", "TRADE_SECRET")]),
    ],
)
def test_derive_high_risk_default_definition(bundle, level, cats, is_high, reason_pairs):
    hr = bundle.policy.derive_high_risk(level, cats)
    assert hr.value is is_high
    assert [(r.axis, r.value) for r in hr.reasons] == reason_pairs
    assert hr.config_version == "1.0.0"


def test_high_risk_definition_is_configurable_not_hard_coded(bundle):
    """Changing the config changes the derivation with no code change (approved DEC-3)."""
    alt = HighRiskConfig(
        high_risk_version="1.1.0",
        taxonomy_version=bundle.taxonomy.taxonomy_version,
        categories=["SOURCE_CODE", "MA_CORP_STRATEGY"],
        levels=[],
    )
    policy = TaxonomyPolicy(bundle.taxonomy, alt)
    assert policy.derive_high_risk("CONFIDENTIAL", ["SOURCE_CODE"]).value is True
    assert policy.derive_high_risk(HC, ["PII"]).value is False  # no longer high-risk
    assert policy.derive_high_risk(HC, ["PII"]).config_version == "1.1.0"


def test_label_errors_detect_problems(bundle):
    p = bundle.policy
    assert p.label_errors("INTERNAL", []) == []
    assert any("missing" in e for e in p.label_errors(None, []))
    assert any("unknown level" in e for e in p.label_errors("SECRET", []))
    assert any("unknown categories" in e for e in p.label_errors("INTERNAL", ["NOPE"]))
    assert any("duplicate" in e for e in p.label_errors("INTERNAL", ["PII", "PII"]))


def test_floor_violation_is_reported(bundle):
    errors = bundle.policy.label_errors("INTERNAL", ["PHI"])
    assert len(errors) == 1 and "below the floor HIGHLY_CONFIDENTIAL" in errors[0]
    assert bundle.policy.label_errors("PUBLIC", ["PII"])  # public + PII contradicts the floor


def test_floor_enforcement_is_toggleable(bundle):
    data = bundle.taxonomy.model_dump()
    data["constraints"]["enforce_level_floors"] = False
    relaxed = TaxonomyPolicy(type(bundle.taxonomy).model_validate(data), bundle.high_risk)
    assert relaxed.label_errors("INTERNAL", ["PHI"]) == []
