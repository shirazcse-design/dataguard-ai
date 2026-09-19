"""The LLM status report, its refusal to show numbers for models that were not run, and the CLI.

Where a tier IS shown, the responses come from a TEST-ONLY scripted cache built here; the numbers in
those tests mean nothing about any model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.classification.cli import main
from app.llm import LLMClassifier, MockLLMClient, ReplayLLMClient
from app.llm.config import load_llm_config
from app.llm.fewshot import load_fewshot
from app.llm.prompting import PromptBuilder
from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents, load_manifest
from evals.classification.evaluate import git_info
from evals.classification.llm_report import build_llm_report
from evals.classification.lock import DEVELOPMENT_SPLITS
from guardrails.injection import InjectionScanner, load_injection_config

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def env(bundle):
    cfg, sha = load_llm_config(bundle.policy)
    docs = load_documents(DEFAULT_DATA_DIR, splits=list(DEVELOPMENT_SPLITS))
    by_split = {s: [d for d in docs if d.split == s] for s in DEVELOPMENT_SPLITS}
    guard, gsha = load_injection_config()
    shots = load_fewshot(ROOT / cfg.prompt.fewshot_file, by_split["train"])
    builder = PromptBuilder(cfg, bundle.taxonomy, shots, ROOT)

    def clf(client):
        return LLMClassifier(client, cfg, bundle.policy, builder, InjectionScanner(guard), tier="small",
                             config_sha256=sha, guardrail_sha256=gsha, sleep=lambda s: None)  # fmt: skip

    return {
        "bundle": bundle,
        "by_split": by_split,
        "clf": clf,
        "manifest": load_manifest(DEFAULT_DATA_DIR),
    }


def report(env, tiers):
    probe = env["clf"](MockLLMClient([]))
    return build_llm_report(
        env["bundle"], probe, tiers, env["by_split"], env["manifest"], git_info()
    )


def oracle_script(dev_docs, *, skip_every=0):
    """TEST-ONLY responder: echoes each document's gold answer (recorded once per document)."""
    by_content = {d.content: d for d in dev_docs}
    seen = {"n": 0}

    def script(request):
        doc = next(d for c, d in by_content.items() if c in request.user)
        seen["n"] += 1
        quotes = [
            {"quote": s.text[:60], "supports_axis": "category", "supports_value": s.label}
            for s in doc.gold_evidence_spans[:1]
            if s.label in doc.gold_categories
        ]
        return json.dumps({
            "level": doc.gold_level, "categories": sorted(doc.gold_categories), "evidence": quotes,
            "rationale": "test", "level_confidence": "high", "category_confidence": "medium",
            "insufficient_information": False,
        })  # fmt: skip

    return script


def recorded_tier(env, tmp_path, model_id, docs):
    """Populate a replay cache for `docs` from the test-only responder, return a replay classifier."""
    recorder = env["clf"](
        ReplayLLMClient(
            tmp_path, model_id, inner=MockLLMClient(oracle_script(env["by_split"]["dev"]))
        )
    )
    for d in docs:
        recorder.classify(d.to_request())
    return env["clf"](ReplayLLMClient(tmp_path, model_id))


def test_report_with_no_models_says_nothing_was_run_and_shows_no_model_numbers(env):
    text = report(env, {})
    assert "NO LLM BENCHMARK HAS BEEN RUN" in text
    assert "no LLM accuracy, calibration, latency or cost numbers" in text
    for t in ("small", "mid", "large"):
        assert f"| {t} | NOT RUN (no model configured for this tier) |" in text
    assert "## Tier `" not in text and "macro-F1" not in text
    assert (
        "unverified against a real endpoint" in text and "pending human gold-label review" in text
    )
    assert "The locked test split was not read" in text
    assert "eval run --classifier llm" in text


def test_report_contains_model_independent_measurements(env):
    text = report(env, {})
    for needle in ("## Prompt audit", "## Local injection guard", "few-shot examples | 13",
                   "all few-shot examples are train documents | True",
                   "few-shot families also present in calibration/dev | 0",
                   "T5 documents flagged", "false positives", "developed on", "held out", "anecdotal"):  # fmt: skip
        assert needle in text, needle


def test_guard_numbers_in_the_report_match_a_direct_measurement(env):
    text = report(env, {})
    guard = InjectionScanner(load_injection_config()[0])
    dev = env["by_split"]["dev"]
    t5 = [d for d in dev if d.tier == "T5"]
    flagged = sum(bool(guard.scan(d.content)) for d in t5)
    assert f"({flagged}/{len(t5)})" in text


def test_a_complete_recorded_tier_gets_a_results_section(env, tmp_path):
    dev = env["by_split"]["dev"]
    tier = recorded_tier(env, tmp_path, "test-model", dev)
    text = report(env, {"small": tier})
    assert "| small | RUN (complete) |" in text and "| mid | NOT RUN" in text
    assert "NO LLM BENCHMARK HAS BEEN RUN" not in text
    for h in ("## Tier `small`", "### Output validity and evidence", "### Reliability of the model's verbalized confidence",
              "### Per category F1", "### By tier", "### Latency, tokens, cost"):  # fmt: skip
        assert h in text, h
    assert "family-level bootstrap" in text and "independent families" in text
    assert "SMALL_SAMPLE" in text and "not estimated (no price configured)" in text
    assert "never calibrated probabilities" in text
    assert "recorded when the responses were captured" in text
    assert "schema-valid rate (after the repair retry) | 1.000" in text


def test_a_partially_recorded_tier_is_incomplete_and_shows_no_results(env, tmp_path):
    dev = env["by_split"]["dev"]
    tier = recorded_tier(env, tmp_path, "partial", dev[:10])
    text = report(env, {"small": tier})
    assert "| small | INCOMPLETE (10/107 recorded) |" in text
    assert "## Tier `small`" not in text and "NO LLM BENCHMARK HAS BEEN RUN" in text


def test_a_tier_with_an_empty_cache_is_not_run(env, tmp_path):
    tier = env["clf"](ReplayLLMClient(tmp_path, "empty"))
    text = report(env, {"small": tier})
    assert "| small | NOT RUN (no recorded responses) |" in text and "## Tier `small`" not in text


# ---- CLI -------------------------------------------------------------------------------------
def test_cli_report_writes_the_file(tmp_path):
    out = tmp_path / "llm.md"
    assert main(["llm", "report", "--out", str(out)]) == 0
    assert "NO LLM BENCHMARK HAS BEEN RUN" in out.read_text()


def test_cli_report_rejects_a_malformed_tier_argument(tmp_path, capsys):
    assert main(["llm", "report", "--tier", "huge=x", "--out", str(tmp_path / "x.md")]) == 2
    assert main(["llm", "report", "--tier", "small", "--out", str(tmp_path / "x.md")]) == 2
    assert "small|mid|large=<model-id>" in capsys.readouterr().err


def test_cli_report_with_an_unrecorded_model_says_not_run(tmp_path):
    out = tmp_path / "llm.md"
    rc = main(
        [
            "llm",
            "report",
            "--tier",
            "small=ghost",
            "--llm-cache-dir",
            str(tmp_path / "cache"),
            "--out",
            str(out),
        ]
    )
    assert rc == 0 and "| small | NOT RUN (no recorded responses) |" in out.read_text()


def test_cli_fewshot_regenerates_the_committed_file(tmp_path):
    out = tmp_path / "fs.json"
    assert main(["llm", "fewshot", "--out", str(out)]) == 0
    committed = json.loads((ROOT / "prompts/uc4/fewshot.v1.json").read_text())
    assert json.loads(out.read_text()) == committed


def test_eval_run_with_the_llm_classifier_accounts_for_every_document_even_with_no_cache(
    tmp_path, capsys
):
    rc = main(["eval", "run", "--classifier", "llm", "--split", "dev", "--llm-model-id", "ghost",
               "--llm-cache-dir", str(tmp_path / "c"), "--runs-dir", str(tmp_path / "runs")])  # fmt: skip
    out = capsys.readouterr().out
    assert rc == 0 and "independent families" in out
    run_dir = next((tmp_path / "runs").iterdir())
    records = [json.loads(x) for x in (run_dir / "predictions.jsonl").read_text().splitlines()]
    assert len(records) == 107 and all(not r["has_prediction"] for r in records)
    assert all(r["failure"] == "no_label:review_required:llm_error:replay_miss" for r in records)


def test_eval_run_refuses_the_locked_split_for_the_llm_too(tmp_path, capsys):
    rc = main(["eval", "run", "--classifier", "llm", "--split", "test", "--llm-model-id", "x",
               "--llm-cache-dir", str(tmp_path), "--runs-dir", str(tmp_path)])  # fmt: skip
    assert rc == 2 and "locked" in capsys.readouterr().err


def test_config_validate_covers_the_llm_and_guardrail_configs(capsys):
    assert main(["config", "validate"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["versions"]["llm"] == "1.0.0" and out["versions"]["prompt"] == "classifier.v1"
    assert out["versions"]["injection_guardrail"] and len(out["llm_config_sha256"]) == 64
