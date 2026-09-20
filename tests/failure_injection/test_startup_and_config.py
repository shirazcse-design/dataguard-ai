"""F04-F05: fail fast at startup, degrade at runtime; refuse to run with an invalid configuration."""

from __future__ import annotations

import json

import pytest

from app.classification.cli import main
from app.classification.config_loader import ConfigError, load_config
from app.classification.hybrid import build_hybrid_classifier
from app.classification.routing_config import load_gates, load_routing_config
from app.llm.config import load_llm_config
from guardrails.injection import load_injection_config
from guardrails.input import load_input_guard_config
from ml.classification import load_ml_config
from observability import load_observability_config
from rules.config import load_rules_config
from tests.failure_injection.conftest import chat_body, hybrid, llm_over_http, request


# ---- F04: ML missing or taxonomy mismatch -----------------------------------------------------
def test_row_F04_an_ml_config_for_another_taxonomy_version_fails_at_startup(bundle, config_copy):
    p = config_copy / "ml/ml.v1.yaml"
    p.write_text(p.read_text().replace("taxonomy_version: 1.0.0", "taxonomy_version: 9.9.9", 1))
    with pytest.raises(ConfigError, match="taxonomy"):
        load_ml_config(bundle.policy, config_copy)
    with pytest.raises(ConfigError):
        build_hybrid_classifier(bundle, variant="ml_stage_70", config_dir=config_copy)


def test_row_F04_missing_training_data_fails_when_the_ml_stage_is_built_not_at_request_time(
    bundle, tmp_path
):
    with pytest.raises((FileNotFoundError, ConfigError, ValueError, OSError)):
        build_hybrid_classifier(bundle, variant="ml_stage_70", data_dir=tmp_path / "no-data-here")


@pytest.mark.parametrize(
    "rel", ["llm/llm.v1.yaml", "routing/routing.v1.yaml", "rules/rules.v1.yaml", "ml/ml.v1.yaml"]
)
def test_row_F04_every_stage_config_written_for_another_taxonomy_is_refused_at_startup(
    bundle, config_copy, rel
):
    p = config_copy / rel
    text = p.read_text()
    assert "taxonomy_version: 1.0.0" in text
    p.write_text(text.replace("taxonomy_version: 1.0.0", "taxonomy_version: 9.9.9", 1))
    with pytest.raises(ConfigError):
        LOADERS[rel](config_copy)


def test_row_F04_changing_the_taxonomy_version_alone_stops_everything_from_loading(config_copy):
    tax = config_copy / "taxonomy/taxonomy.v1.yaml"
    tax.write_text(tax.read_text().replace("taxonomy_version: 1.0.0", "taxonomy_version: 2.0.0", 1))
    with pytest.raises(ConfigError):
        load_config(config_copy)  # high-risk and eval configs no longer match: no partial start


def test_row_F04_at_runtime_a_failing_ml_stage_is_skipped_and_the_result_is_degraded(
    bundle, parts, provider, plain_doc
):
    class BrokenML:
        name, version = "ml", "0"

        def classify(self, req):
            raise RuntimeError("model file corrupt: " + req.document.content[:20])

        def params(self):
            return {"model_id": "ml", "fit_splits": [], "calibration_splits": []}

    provider.default = (200, chat_body("CONFIDENTIAL", ["SOURCE_CODE"]), {}, 0.0)
    h = hybrid(
        parts,
        "ml_on",
        rules=parts.rules,
        ml=BrokenML(),
        llms={
            "mid": llm_over_http(parts, provider.url),
            "large": llm_over_http(parts, provider.url, "large"),
        },
        ml_on={"ml": {"enabled": True, "tau": 0.7}},
    )
    r = h.classify(request(plain_doc.content))
    assert r.status == "degraded" and r.level.value == "CONFIDENTIAL"  # the LLM still decided
    assert "stage_error:ml:RuntimeError" in r.warnings and "degraded:ml" in r.warnings
    assert plain_doc.content[:20] not in r.model_dump_json()  # the exception message never surfaces


# ---- F05: config invalid ----------------------------------------------------------------------
LOADERS = {
    "taxonomy/taxonomy.v1.yaml": lambda d: load_config(d),
    "taxonomy/high_risk.v1.yaml": lambda d: load_config(d),
    "eval/eval.v1.yaml": lambda d: load_config(d),
    "ml/ml.v1.yaml": lambda d: load_ml_config(load_config(d).policy, d),
    "rules/rules.v1.yaml": lambda d: load_rules_config(load_config(d).policy, d),
    "llm/llm.v1.yaml": lambda d: load_llm_config(load_config(d).policy, d),
    "routing/routing.v1.yaml": lambda d: load_routing_config(load_config(d).policy, d),
    "eval/gates.v1.yaml": lambda d: load_gates(d),
    "guardrails/injection.v1.yaml": lambda d: load_injection_config(d),
    "guardrails/input.v1.yaml": lambda d: load_input_guard_config(d),
    "observability/observability.v1.yaml": lambda d: load_observability_config(d),
}


def corrupt(path, how):
    if how == "missing":
        path.unlink()
    elif how == "invalid_yaml":
        path.write_text("key: [unclosed\n  - : :\n")
    elif how == "unknown_key":
        path.write_text(path.read_text() + "\nsurprise_key_that_is_not_in_the_schema: 1\n")
    elif how == "duplicate_key":
        text = path.read_text()
        first = next(
            x for x in text.splitlines() if x and not x.startswith(("#", " ", "-")) and ":" in x
        )
        path.write_text(text + "\n" + first + "\n")
    elif how == "empty":
        path.write_text("")


@pytest.mark.parametrize(
    "how", ["missing", "invalid_yaml", "unknown_key", "duplicate_key", "empty"]
)
@pytest.mark.parametrize("rel", sorted(LOADERS))
def test_row_F05_an_invalid_config_file_is_refused_by_its_loader(config_copy, rel, how):
    corrupt(config_copy / rel, how)
    with pytest.raises(ConfigError):
        LOADERS[rel](config_copy)


def test_row_F05_the_cli_refuses_to_validate_or_start_with_a_broken_config(config_copy, capsys):
    (config_copy / "routing/routing.v1.yaml").write_text("not: [valid")
    rc = main(["config", "validate", "--config-dir", str(config_copy)])
    out = capsys.readouterr()
    assert rc == 2 and "CONFIG INVALID" in out.err and '"status": "ok"' not in out.out


def test_row_F05_a_valid_config_validates_and_reports_every_component(capsys):
    assert main(["config", "validate"]) == 0
    versions = json.loads(capsys.readouterr().out)["versions"]
    assert {
        "taxonomy",
        "high_risk",
        "eval",
        "llm",
        "routing",
        "gates",
        "observability",
        "input_guardrail",
        "injection_guardrail",
        "ml",
        "ruleset",
    } <= set(versions)


def test_row_F05_a_partial_taxonomy_is_never_accepted(config_copy):
    p = config_copy / "taxonomy/taxonomy.v1.yaml"
    text = p.read_text()
    start = text.index("  - id: TRADE_SECRET")
    end = text.index("  - id: MA_CORP_STRATEGY")
    p.write_text(text[:start] + text[end:])  # drop a whole category
    with pytest.raises(ConfigError):
        load_config(config_copy)


def test_row_F05_a_taxonomy_level_floor_naming_an_unknown_level_is_refused(config_copy):
    p = config_copy / "taxonomy/taxonomy.v1.yaml"
    p.write_text(p.read_text().replace("level_floor: CONFIDENTIAL", "level_floor: SECRETISH", 1))
    with pytest.raises(ConfigError):
        load_config(config_copy)


def test_row_F05_the_hybrid_will_not_build_from_a_broken_routing_config(bundle, config_copy):
    p = config_copy / "routing/routing.v1.yaml"
    p.write_text(p.read_text().replace("tier_order: [mid, large]", "tier_order: [mid, mid]", 1))
    with pytest.raises(ConfigError):
        build_hybrid_classifier(bundle, variant="default", config_dir=config_copy)
