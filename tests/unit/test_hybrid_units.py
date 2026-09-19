"""Router, fusion, review and routing-configuration units (pure functions and config)."""

from __future__ import annotations

import pytest

from app.classification.config_loader import ConfigError
from app.classification.fusion import StageResult, fuse
from app.classification.review import build_review
from app.classification.router import has_conflict, llm_accepted, ml_accepted, rules_sufficient
from app.classification.routing_config import (
    FusionCfg,
    LLMStage,
    MLStage,
    RulesStage,
    VariantConfig,
    load_gates,
    load_routing_config,
)
from app.classification.schemas import (
    NO_CONFIDENCE,
    CategoryPrediction,
    ClassificationResult,
    Confidence,
    Evidence,
    LevelPrediction,
    ReviewDecision,
    Routing,
    Scores,
    Supports,
)
from tests.helpers import CATS, LEVELS

RULES = RulesStage(enabled=True, short_circuit=False, min_level_strength="strong")


def mk(policy, level, cats=(), *, conf=None, cat_conf=None, status="ok", abstained=False,
       scores=None, evidence=(), review=None, warnings=()):  # fmt: skip
    conf = conf or Confidence(kind="rule_strength", raw="strong")
    cat_conf = cat_conf or conf
    ev = list(evidence)
    return ClassificationResult(
        request_id="r", content_hash="h", status=status,
        level=None if level is None else LevelPrediction(value=level, confidence=conf, decided_by="rules"),
        categories=[CategoryPrediction(id=c, confidence=cat_conf, decided_by="rules",
                                       evidence_ids=[e.evidence_id for e in ev if e.supports.value == c]) for c in cats],
        high_risk=None if level is None else policy.derive_high_risk(level, list(cats)),
        review=review or ReviewDecision(), evidence=ev,
        routing=Routing(abstained=abstained), scores=scores, warnings=list(warnings),
    )  # fmt: skip


def bucket(b):
    return Confidence(kind="verbalized_bucket", raw=b)


def ev(eid, cat, source="rules"):
    return Evidence(evidence_id=eid, source=source, supports=Supports(axis="category", value=cat),
                    type="pattern_match" if source == "rules" else "llm_rationale",
                    strength="strong" if source == "rules" else "n/a",
                    provenance="observed" if source == "rules" else "inferred")  # fmt: skip


# ---- Rules sufficiency ---------------------------------------------------------------------------
def test_rules_sufficient_needs_a_decisive_level_at_or_above_the_minimum_strength(bundle):
    p = bundle.policy
    assert rules_sufficient(mk(p, "HIGHLY_CONFIDENTIAL", ["PHI"]), RULES).accepted
    assert rules_sufficient(
        mk(p, "CONFIDENTIAL", conf=Confidence(kind="rule_strength", raw="definitive")), RULES
    ).accepted
    weak = mk(p, "CONFIDENTIAL", conf=Confidence(kind="rule_strength", raw="weak"))
    assert not rules_sufficient(weak, RULES).accepted
    definitive_only = RulesStage(enabled=True, short_circuit=False, min_level_strength="definitive")
    assert not rules_sufficient(mk(p, "CONFIDENTIAL"), definitive_only).accepted


def test_a_rules_abstention_is_never_sufficient_even_though_it_carries_a_default_level(bundle):
    a = mk(bundle.policy, "INTERNAL", conf=NO_CONFIDENCE, abstained=True)
    out = rules_sufficient(a, RULES)
    assert not out.accepted and out.reason == "rules_abstained"
    assert not rules_sufficient(None, RULES).accepted
    assert not rules_sufficient(mk(bundle.policy, None, status="error"), RULES).accepted


# ---- ML acceptance -------------------------------------------------------------------------------
def scores(level_p, cat_p, calibrated=True):
    return Scores(level=level_p, categories=cat_p, calibrated=calibrated,
                  calibration_ref="ref" if calibrated else None)  # fmt: skip


def test_ml_is_accepted_only_when_calibrated_and_reliable_on_every_axis(bundle):
    p = bundle.policy
    lv = {"PUBLIC": 0.05, "INTERNAL": 0.05, "CONFIDENTIAL": 0.85, "HIGHLY_CONFIDENTIAL": 0.05}
    cp = {c: 0.05 for c in CATS}
    cfg = MLStage(enabled=True, short_circuit=True, tau=0.8)
    ok = mk(p, "CONFIDENTIAL", scores=scores(lv, cp))
    assert ml_accepted(ok, cfg).accepted
    assert ml_accepted(ok, cfg.model_copy(update={"tau": 0.9})).reason == "ml_level_below_tau"
    unsure = mk(p, "CONFIDENTIAL", scores=scores(lv, {**cp, "PII": 0.5}))
    assert ml_accepted(unsure, cfg).reason == "ml_category_below_tau"
    assert (
        ml_accepted(mk(p, "CONFIDENTIAL", scores=scores(lv, cp, calibrated=False)), cfg).reason
        == "ml_uncalibrated"
    )
    assert ml_accepted(mk(p, "CONFIDENTIAL"), cfg).reason == "ml_unavailable"
    confident_no = mk(
        p, "CONFIDENTIAL", scores=scores(lv, {**cp, "PII": 0.95})
    )  # confident it IS present
    assert ml_accepted(confident_no, cfg).accepted


# ---- LLM acceptance ------------------------------------------------------------------------------
LLM = LLMStage(tier_order=["mid"], min_confidence="medium", accept_abstention=False)


def test_llm_acceptance_uses_the_bucket_as_an_ordinal_on_both_axes(bundle):
    p = bundle.policy
    good = mk(p, "CONFIDENTIAL", ["SOURCE_CODE"], conf=bucket("medium"))
    assert llm_accepted(good, LLM).accepted
    low_level = mk(p, "CONFIDENTIAL", ["SOURCE_CODE"], conf=bucket("low"), cat_conf=bucket("high"))
    assert llm_accepted(low_level, LLM).reason == "llm_low_confidence"
    low_cat = mk(p, "CONFIDENTIAL", ["SOURCE_CODE"], conf=bucket("high"), cat_conf=bucket("low"))
    assert llm_accepted(low_cat, LLM).reason == "llm_low_confidence"
    assert (
        llm_accepted(good, LLM.model_copy(update={"min_confidence": "high"})).reason
        == "llm_low_confidence"
    )
    assert llm_accepted(
        mk(p, "CONFIDENTIAL", conf=bucket("low")), LLM.model_copy(update={"min_confidence": "low"})
    ).accepted


def test_llm_results_needing_review_or_abstaining_or_absent_are_not_accepted(bundle):
    p = bundle.policy
    rv = mk(p, "HIGHLY_CONFIDENTIAL", ["PHI"], conf=bucket("high"), status="review_required",
            review=ReviewDecision(required=True, reason_codes=["EVIDENCE_UNVERIFIED"], provisional=True))  # fmt: skip
    assert llm_accepted(rv, LLM).reason == "llm_review:EVIDENCE_UNVERIFIED"
    ab = mk(p, "INTERNAL", conf=bucket("high"), abstained=True)
    assert llm_accepted(ab, LLM).reason == "llm_insufficient_information"
    assert llm_accepted(ab, LLM.model_copy(update={"accept_abstention": True})).accepted
    assert llm_accepted(None, LLM).reason == "llm_no_level"
    assert llm_accepted(mk(p, None, status="review_required", review=ReviewDecision(required=True, reason_codes=["LLM_UNAVAILABLE"])), LLM).reason == "llm_no_level"  # fmt: skip


# ---- conflict ------------------------------------------------------------------------------------
def test_conflict_means_high_risk_disagreement_or_a_level_gap_of_more_than_one_rank(bundle):
    p = bundle.policy
    a = mk(p, "HIGHLY_CONFIDENTIAL", ["PHI"])
    assert has_conflict(a, mk(p, "INTERNAL"), p, 2)  # 2 ranks apart
    assert not has_conflict(mk(p, "CONFIDENTIAL"), mk(p, "INTERNAL"), p, 2)  # adjacent ranks agree
    assert has_conflict(mk(p, "CONFIDENTIAL"), mk(p, "INTERNAL"), p, 1)  # stricter configuration
    # same level, but one is high-risk through a category and the other is not
    assert has_conflict(
        mk(p, "CONFIDENTIAL", ["PII"]), mk(p, "CONFIDENTIAL", ["SOURCE_CODE"]), p, 3
    )
    assert not has_conflict(mk(p, "CONFIDENTIAL", ["PII"]), mk(p, "CONFIDENTIAL", ["PII"]), p, 2)
    assert not has_conflict(mk(p, None, status="error"), a, p, 2)


# ---- review --------------------------------------------------------------------------------------
def test_review_orders_codes_dedupes_and_prioritises_provisional_high_risk():
    r = build_review(
        ["LOW_CONFIDENCE", "DETECTOR_CONFLICT", "LOW_CONFIDENCE"],
        provisional=True,
        provisional_high_risk=True,
    )
    assert (
        r.reason_codes == ["DETECTOR_CONFLICT", "LOW_CONFIDENCE"]
        and r.priority == 1
        and r.provisional
    )
    assert (
        build_review(["LOW_CONFIDENCE"], provisional=True, provisional_high_risk=False).priority
        == 2
    )
    assert (
        build_review(["LLM_UNAVAILABLE"], provisional=False, provisional_high_risk=True).priority
        == 2
    )
    assert build_review([], provisional=False, provisional_high_risk=False).required is False
    first = build_review(
        ["LOW_CONFIDENCE", "INJECTION_DOWNGRADE_ATTEMPT"],
        provisional=False,
        provisional_high_risk=False,
    )
    assert first.reason_codes[0] == "INJECTION_DOWNGRADE_ATTEMPT"


# ---- fusion --------------------------------------------------------------------------------------
FUSE = FusionCfg(rules_floor=True, category_floors=True)


def stage(policy, kind, label, level, cats=(), **kw):
    return StageResult(kind, label, mk(policy, level, cats, **kw))


def test_categories_are_the_union_with_rules_credited_first_and_evidence_kept(bundle):
    p = bundle.policy
    rules = stage(
        p,
        "rules",
        "rules",
        "HIGHLY_CONFIDENTIAL",
        ["PHI", "PII"],
        evidence=[ev("e1", "PHI"), ev("e2", "PII")],
    )
    llm = stage(p, "llm", "llm:mid", "HIGHLY_CONFIDENTIAL", ["PHI", "TRADE_SECRET"], conf=bucket("high"),
                evidence=[ev("e1", "TRADE_SECRET", source="llm")])  # fmt: skip
    out = fuse(p, FUSE, [rules, llm], rules_is_floor=True)
    by = {c.id: c for c in out.categories}
    assert set(by) == {"PHI", "PII", "TRADE_SECRET"}
    assert by["PHI"].decided_by == "rules" and by["PII"].decided_by == "rules"
    assert (
        by["TRADE_SECRET"].decided_by == "llm"
        and by["TRADE_SECRET"].confidence.kind == "verbalized_bucket"
    )
    ids = {e.evidence_id for e in out.evidence}
    assert ids == {"rules.e1", "rules.e2", "llm:mid.e1"}  # ids from different stages cannot collide
    assert by["TRADE_SECRET"].evidence_ids == ["llm:mid.e1"] and set(by["PHI"].evidence_ids) == {
        "rules.e1"
    }


def test_level_comes_from_the_most_authoritative_semantic_stage(bundle):
    p = bundle.policy
    llm = stage(p, "llm", "llm:mid", "CONFIDENTIAL", conf=bucket("high"))
    ml = stage(
        p,
        "ml",
        "ml",
        "INTERNAL",
        conf=Confidence(
            kind="calibrated_probability", raw=0.9, calibrated=True, calibration_ref="c"
        ),
    )
    rules = stage(p, "rules", "rules", "INTERNAL")
    assert (
        fuse(
            p,
            FusionCfg(rules_floor=False, category_floors=False),
            [rules, ml, llm],
            rules_is_floor=True,
        ).level.decided_by
        == "llm"
    )
    only_ml = fuse(
        p, FusionCfg(rules_floor=False, category_floors=False), [rules, ml], rules_is_floor=True
    )
    assert only_ml.level.decided_by == "ml"
    assert fuse(p, FUSE, [rules], rules_is_floor=True).level.decided_by == "rules"


def test_the_rules_level_is_a_floor_only_when_enabled_and_sufficient(bundle):
    p = bundle.policy
    rules = stage(p, "rules", "rules", "CONFIDENTIAL")
    llm = stage(p, "llm", "llm:mid", "INTERNAL", conf=bucket("high"))
    up = fuse(p, FUSE, [rules, llm], rules_is_floor=True)
    assert (
        up.level.value == "CONFIDENTIAL"
        and up.level.decided_by == "rules"
        and "rules_floor" in up.notes
    )
    assert (
        fuse(
            p, FusionCfg(rules_floor=False, category_floors=True), [rules, llm], rules_is_floor=True
        ).level.value
        == "INTERNAL"
    )
    assert fuse(p, FUSE, [rules, llm], rules_is_floor=False).level.value == "INTERNAL"
    never_lowered = fuse(
        p,
        FUSE,
        [
            stage(p, "rules", "rules", "INTERNAL"),
            stage(p, "llm", "llm:mid", "CONFIDENTIAL", conf=bucket("high")),
        ],
        rules_is_floor=True,
    )
    assert never_lowered.level.value == "CONFIDENTIAL"


def test_category_floors_raise_the_level_and_are_toggleable(bundle):
    p = bundle.policy
    llm = stage(p, "llm", "llm:mid", "PUBLIC", ["TRADE_SECRET"], conf=bucket("high"))
    up = fuse(p, FUSE, [llm], rules_is_floor=False)
    assert (
        up.level.value == "HIGHLY_CONFIDENTIAL"
        and up.level.decided_by == "fusion"
        and up.level.confidence.kind == "none"
    )
    assert "category_floor" in up.notes
    off = fuse(p, FusionCfg(rules_floor=True, category_floors=False), [llm], rules_is_floor=False)
    assert off.level.value == "PUBLIC"


def test_raise_to_only_ever_raises(bundle):
    p = bundle.policy
    llm = stage(p, "llm", "llm:mid", "CONFIDENTIAL", conf=bucket("high"))
    assert (
        fuse(p, FUSE, [llm], rules_is_floor=False, raise_to="HIGHLY_CONFIDENTIAL").level.value
        == "HIGHLY_CONFIDENTIAL"
    )
    assert (
        fuse(p, FUSE, [llm], rules_is_floor=False, raise_to="INTERNAL").level.value
        == "CONFIDENTIAL"
    )


def test_fusion_without_any_level_returns_nothing(bundle):
    p = bundle.policy
    assert fuse(p, FUSE, [], rules_is_floor=False) is None
    assert (
        fuse(
            p,
            FUSE,
            [StageResult("llm", "llm:mid", mk(p, None, status="error"))],
            rules_is_floor=False,
        )
        is None
    )


# ---- routing configuration -----------------------------------------------------------------------
def test_real_routing_config_loads_and_every_variant_resolves(bundle):
    cfg, sha = load_routing_config(bundle.policy)
    assert len(sha) == 64 and cfg.default_variant == "default"
    for name in cfg.variants:
        v = cfg.variant(name)
        assert isinstance(v, VariantConfig)
    d = cfg.variant("default")
    assert d.rules.enabled and not d.rules.short_circuit and not d.ml.enabled
    assert (
        d.llm.tier_order == ["mid", "large"]
        and d.fusion.rules_floor
        and d.injection.restrict_downgrade
    )


def test_variants_are_overrides_of_base_and_match_the_preregistered_list(bundle):
    cfg, _ = load_routing_config(bundle.policy)
    assert set(cfg.variants) == {
        "llm_mid_only", "default", "rules_short_circuit", "no_rules_floor", "no_category_floors",
        "small_first", "small_mid", "large_only", "strict_confidence", "no_injection_restriction",
        "ml_stage_50", "ml_stage_70", "ml_stage_90",
    }  # fmt: skip
    assert cfg.variant("rules_short_circuit").rules.short_circuit is True
    assert cfg.variant("rules_short_circuit").llm.tier_order == ["mid", "large"]  # rest inherited
    assert cfg.variant("small_first").llm.tier_order == ["small", "mid", "large"]
    assert cfg.variant("strict_confidence").llm.min_confidence == "high"
    assert [cfg.variant(f"ml_stage_{t}").ml.tau for t in (50, 70, 90)] == [0.5, 0.7, 0.9]
    m = cfg.variant("llm_mid_only")
    assert not m.rules.enabled and m.llm.tier_order == ["mid"] and not m.fusion.category_floors
    with pytest.raises(KeyError):
        cfg.variant("nope")


@pytest.mark.parametrize(
    "old, new",
    [
        ("  default: {}", "  default: {rules: {shortcircuit: true}}"),  # typo in an override
        ("default_variant: default", "default_variant: missing"),
        ("tier_order: [mid, large]", "tier_order: [mid, mid]"),
        ("tier_order: [mid, large]", "tier_order: [mid, huge]"),
        ("    tau: 0.7", "    tau: 0.2"),
        ("min_confidence: medium", "min_confidence: certain"),
        ("taxonomy_version: 1.0.0", "taxonomy_version: 9.9.9"),
    ],
)
def test_invalid_routing_config_fails_loudly(bundle, config_copy, old, new):
    p = config_copy / "routing/routing.v1.yaml"
    text = p.read_text()
    assert old in text
    p.write_text(text.replace(old, new, 1))
    with pytest.raises(ConfigError):
        load_routing_config(bundle.policy, config_copy)


def test_a_variant_with_no_stage_at_all_is_rejected(bundle, config_copy):
    p = config_copy / "routing/routing.v1.yaml"
    p.write_text(
        p.read_text().replace(
            "  default: {}", "  default: {rules: {enabled: false}, llm: {tier_order: []}}", 1
        )
    )
    with pytest.raises(ConfigError):
        load_routing_config(bundle.policy, config_copy)


def test_gates_load_and_keep_the_two_f1_gates_separate():
    g, sha = load_gates()
    assert (g.gates.level_macro_f1, g.gates.category_macro_f1, g.gates.high_risk_recall) == (
        0.85,
        0.85,
        0.90,
    )
    assert len(sha) == 64 and g.tool_call_limit_s == 10.0


def test_taxonomy_helpers_used_by_the_tests_exist():
    assert LEVELS[0] == "PUBLIC" and "TRADE_SECRET" in CATS
