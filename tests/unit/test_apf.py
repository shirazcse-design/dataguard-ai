"""HHH and APF (PRD 14-15), scoped for UC4: composite scoring over real, already-computed metrics."""

from __future__ import annotations

import pytest

from app.classification.config_loader import ConfigError
from evals.classification.apf import APFConfig, compute_apf, compute_hhh, load_apf_config


@pytest.fixture(scope="module")
def apf_cfg():
    return load_apf_config()[0]


def fake_metrics(*, level_f1=0.9, cat_f1=1.0, hr_recall=1.0, n=10, n_with_pred=10):
    return {
        "metrics": {
            "coverage": {"n_docs": n, "n_with_prediction": n_with_pred},
            "level": {"macro": {"f1": level_f1}},
            "categories": {"macro": {"f1": cat_f1}},
            "high_risk": {"recall": hr_recall},
        }
    }


def fake_obs(
    *,
    evidence_fail_rate=0.0,
    p95_ms=2000,
    review_rate=0.0,
    schema_failure_rate=0.0,
    failed_spans=0,
    n_traces=10,
):
    return {
        "n_traces": n_traces,
        "review_rate": review_rate,
        "schema_failure_rate": schema_failure_rate,
        "evidence_verification_failure_rate": evidence_fail_rate,
        "stage_latency": {"llm.call": {"p95_ms": p95_ms}},
        "fallbacks": {"failed_stage_spans": failed_spans},
    }


# ---- config ---------------------------------------------------------------------------------
def test_the_shipped_config_loads_and_weights_sum_to_one(apf_cfg):
    assert abs(sum(apf_cfg.weights.model_dump().values()) - 1.0) < 1e-9
    assert apf_cfg.efficiency_latency_budget_s > 0


def test_weights_must_sum_to_one(tmp_path):
    import shutil

    from app.classification.config_loader import default_config_dir

    dst = tmp_path / "config"
    shutil.copytree(default_config_dir(), dst)
    p = dst / "eval/apf.v1.yaml"
    p.write_text(p.read_text().replace("effectiveness: 0.35", "effectiveness: 0.99"))
    with pytest.raises(ConfigError, match="sum to 1.0"):
        load_apf_config(dst)


def test_apfweights_reject_a_value_outside_zero_one():
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        APFConfig(
            apf_config_version="1.0.0",
            weights={"effectiveness": 1.5, "efficiency": 0, "reliability": 0, "trustworthiness": 0},
            efficiency_latency_budget_s=10,
        )


# ---- HHH ------------------------------------------------------------------------------------
def test_hhh_helpful_is_the_coverage_rate():
    hhh = compute_hhh(fake_metrics(n=10, n_with_pred=8))
    assert hhh["helpful"]["score"] == pytest.approx(0.8)


def test_hhh_honest_is_none_without_a_trace_and_computed_with_one():
    assert compute_hhh(fake_metrics())["honest"]["score"] is None
    hhh = compute_hhh(fake_metrics(), fake_obs(evidence_fail_rate=0.1))
    assert hhh["honest"]["score"] == pytest.approx(0.9)


def test_hhh_harmless_is_high_risk_recall_and_can_fold_in_content_safety():
    hhh = compute_hhh(fake_metrics(hr_recall=0.75))
    assert hhh["harmless"]["score"] == pytest.approx(0.75)
    hhh2 = compute_hhh(fake_metrics(hr_recall=0.75), content_safety_pass_rate=1.0)
    assert hhh2["harmless"]["score"] == pytest.approx(0.875)


def test_hhh_scope_note_says_tool_use_is_dropped():
    note = compute_hhh(fake_metrics())["scope_note"]
    assert "tool" in note.lower() and "dropped" in note.lower()


# ---- APF --------------------------------------------------------------------------------------
def test_apf_effectiveness_is_the_mean_of_the_three_headline_metrics(apf_cfg):
    apf = compute_apf(fake_metrics(level_f1=0.8, cat_f1=1.0, hr_recall=0.9), None, apf_cfg)
    assert apf["dimensions"]["effectiveness"]["score"] == pytest.approx((0.8 + 1.0 + 0.9) / 3)


def test_apf_efficiency_is_none_without_a_trace_and_scored_against_the_latency_budget(apf_cfg):
    apf = compute_apf(fake_metrics(), None, apf_cfg)
    assert apf["dimensions"]["efficiency"]["score"] is None
    apf2 = compute_apf(fake_metrics(), fake_obs(p95_ms=5000), apf_cfg)  # budget is 10s
    assert apf2["dimensions"]["efficiency"]["score"] == pytest.approx(0.5)


def test_apf_efficiency_clamps_at_zero_when_the_budget_is_blown(apf_cfg):
    apf = compute_apf(fake_metrics(), fake_obs(p95_ms=50_000), apf_cfg)
    assert apf["dimensions"]["efficiency"]["score"] == 0.0


def test_apf_reliability_penalises_review_schema_failure_and_stage_failure(apf_cfg):
    perfect = compute_apf(fake_metrics(), fake_obs(), apf_cfg)
    assert perfect["dimensions"]["reliability"]["score"] == pytest.approx(1.0)
    worse = compute_apf(
        fake_metrics(), fake_obs(review_rate=0.3, schema_failure_rate=0.1, failed_spans=1), apf_cfg
    )
    assert worse["dimensions"]["reliability"]["score"] < 1.0


def test_apf_trustworthiness_reuses_the_hhh_honest_score(apf_cfg):
    hhh = compute_hhh(fake_metrics(), fake_obs(evidence_fail_rate=0.2))
    apf = compute_apf(fake_metrics(), fake_obs(evidence_fail_rate=0.2), apf_cfg, hhh=hhh)
    assert apf["dimensions"]["trustworthiness"]["score"] == pytest.approx(0.8)


def test_apf_composite_is_a_weighted_mean_when_every_dimension_is_present(apf_cfg):
    obs = fake_obs(p95_ms=0)  # a perfect run on every dimension: composite must be exactly 1.0
    hhh = compute_hhh(fake_metrics(hr_recall=1.0), obs)
    apf = compute_apf(fake_metrics(level_f1=1.0, cat_f1=1.0, hr_recall=1.0), obs, apf_cfg, hhh=hhh)
    assert apf["composite"] == pytest.approx(1.0)
    assert apf["composite_note"] is None


def test_apf_composite_renormalises_when_a_dimension_is_missing_never_treats_it_as_zero(apf_cfg):
    # no obs_summary: efficiency, reliability and trustworthiness(without hhh) are all None
    apf = compute_apf(fake_metrics(level_f1=1.0, cat_f1=1.0, hr_recall=1.0), None, apf_cfg)
    assert apf["composite"] == pytest.approx(1.0)  # effectiveness alone, not dragged down by zeros
    assert apf["composite_note"] is not None and "effectiveness" in apf["composite_note"]


def test_apf_scope_note_says_no_tool_selection(apf_cfg):
    note = compute_apf(fake_metrics(), None, apf_cfg)["scope_note"]
    assert "tool" in note.lower()


def test_apf_weights_are_versioned_and_the_prd_default_is_recorded(apf_cfg):
    apf = compute_apf(fake_metrics(), fake_obs(), apf_cfg)
    assert apf["apf_config_version"] == apf_cfg.apf_config_version
    assert apf["weights"] == {
        "effectiveness": 0.35,
        "efficiency": 0.15,
        "reliability": 0.25,
        "trustworthiness": 0.25,
    }


# ---- against a real computed run (no fakes) ---------------------------------------------------
def test_against_a_real_dev_run_apf_is_well_formed_and_reproducible(apf_cfg):
    from app.classification.config_loader import load_config
    from app.classification.hybrid import build_hybrid_classifier
    from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents
    from evals.classification.evaluate import build_metrics
    from evals.classification.runner import run_classifier

    bundle = load_config()
    docs = [
        d
        for d in load_documents(DEFAULT_DATA_DIR, splits=["dev"])
        if d.split == "dev" and d.tier != "T5"
    ][:20]
    classifier = build_hybrid_classifier(bundle, llm_mode="replay")
    records = run_classifier(classifier, docs, bundle.policy)
    metrics = build_metrics(records, bundle)
    hhh = compute_hhh(metrics["headline"])
    apf = compute_apf(metrics["headline"], None, apf_cfg, hhh=hhh)
    assert 0.0 <= hhh["helpful"]["score"] <= 1.0
    assert 0.0 <= hhh["harmless"]["score"] <= 1.0
    assert apf["dimensions"]["effectiveness"]["score"] is not None
    # reproducible: same inputs, same outputs
    assert compute_apf(metrics["headline"], None, apf_cfg, hhh=hhh) == apf
