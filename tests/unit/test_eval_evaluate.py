"""evaluate(), the run manifest, run artifacts and reports."""

from __future__ import annotations

import json

import pytest

from evals.classification.baselines import MajorityClassifier, OracleClassifier, RandomClassifier
from evals.classification.evaluate import HARNESS_VERSION, evaluate, fingerprint
from evals.classification.reporting import render_run_report, write_run
from tests.helpers import mkdoc

MANIFEST = {
    "dataset_id": "t",
    "dataset_version": "1.0.0",
    "dataset_sha256": "a" * 64,
    "spec_hash": "b" * 64,
}


def make_docs():
    docs = []
    spec = [
        ("g1", "T1", "HIGHLY_CONFIDENTIAL", ["PHI"]), ("g1", "T1", "HIGHLY_CONFIDENTIAL", ["PHI"]),
        ("g2", "T2", "CONFIDENTIAL", ["SOURCE_CODE"]), ("g3", "T1", "INTERNAL", []),
        ("g4", "T4", "PUBLIC", []), ("g5", "T5", "INTERNAL", []), ("g5", "T5", "INTERNAL", []),
        ("g6", "T3", "HIGHLY_CONFIDENTIAL", ["TRADE_SECRET"]),
    ]  # fmt: skip
    for i, (g, tier, level, cats) in enumerate(spec):
        extra = {}
        if tier == "T3":
            extra = {
                "ambiguity_flag": True,
                "annotation_notes": "n",
                "acceptable_alternative_levels": ["CONFIDENTIAL"],
            }
        docs.append(
            mkdoc(f"d{i}", level=level, cats=cats, group=g, tier=tier, split="test", **extra)
        )
    return docs


@pytest.fixture()
def docs():
    return make_docs()


def run(clf, docs, bundle):
    return evaluate(clf, docs, bundle, MANIFEST, cli_args={"x": 1})


def test_oracle_scores_perfectly_and_headline_excludes_t5(bundle, docs):
    res = run(OracleClassifier.from_docs(docs, bundle.policy), docs, bundle)
    head = res.metrics["headline"]
    assert head["tiers"] == ["T1", "T2", "T3", "T4"]
    assert head["metrics"]["coverage"]["n_docs"] == 6  # 8 documents minus the two T5 ones
    assert res.metrics["all_tiers"]["coverage"]["n_docs"] == 8
    assert (
        head["metrics"]["level"]["macro"]["f1"] == 1.0
        and head["metrics"]["high_risk"]["recall"] == 1.0
    )


def test_t5_appears_as_its_own_slice(bundle, docs):
    res = run(OracleClassifier.from_docs(docs, bundle.policy), docs, bundle)
    tier_slices = res.metrics["slices"]["tier"]
    assert set(tier_slices) == {"T1", "T2", "T3", "T4", "T5"} and tier_slices["T5"]["n_docs"] == 2
    assert set(res.metrics["slices"]) == {"tier", "format", "generator", "ambiguity_flag"}
    assert res.metrics["slices"]["ambiguity_flag"]["True"]["n_docs"] == 1
    assert all(s["small_sample"] for s in tier_slices.values())  # all far below the threshold


def test_no_combined_headline_score_anywhere(bundle, docs):
    res = run(OracleClassifier.from_docs(docs, bundle.policy), docs, bundle)
    blob = json.dumps(res.metrics).lower()
    assert "composite" not in blob and "apf" not in blob and "overall_score" not in blob
    assert "no combined headline score" in render_run_report(res).lower()


def test_manifest_records_provenance(bundle, docs):
    res = run(MajorityClassifier.from_docs(docs, bundle.policy), docs, bundle)
    m = res.manifest
    assert m["run_id"].startswith("majority-test-") and m["run_id"].endswith(res.fingerprint[:8])
    assert m["harness_version"] == HARNESS_VERSION and m["cli_args"] == {"x": 1}
    assert (
        m["dataset"]["dataset_sha256"] == "a" * 64
        and m["dataset"]["evaluated_locked_test_split"] is True
    )
    assert m["dataset"]["n_documents"] == 8 and m["dataset"]["n_groups"] == 6
    assert m["classifier"]["name"] == "majority" and "level" in m["classifier"]["params"]
    assert m["config"]["versions"] == bundle.versions() and set(m["config"]["file_hashes"]) == set(
        bundle.file_hashes
    )
    assert m["eval_settings"]["bootstrap"]["unit"] == "group"
    assert {"python", "numpy", "scikit_learn"} <= set(m["environment"])
    assert set(m["git"]) == {"commit", "branch", "dirty"}
    assert "reproduced exactly" in m["reproducibility"]


def test_non_test_splits_are_not_flagged_as_locked_test(bundle):
    docs = [mkdoc(f"x{i}", split="dev") for i in range(3)]
    res = run(OracleClassifier.from_docs(docs, bundle.policy), docs, bundle)
    assert res.manifest["dataset"]["evaluated_locked_test_split"] is False


def test_fingerprint_is_deterministic_and_excludes_latency_and_cost(bundle, docs):
    a = run(RandomClassifier(1, bundle.policy), docs, bundle)
    b = run(RandomClassifier(1, bundle.policy), docs, bundle)
    c = run(RandomClassifier(2, bundle.policy), docs, bundle)
    assert a.fingerprint == b.fingerprint != c.fingerprint
    deterministic = {k: v for k, v in a.metrics.items() if k not in ("latency", "cost")}
    assert a.fingerprint == fingerprint(deterministic)  # i.e. latency/cost are not in the hash
    assert fingerprint({"a": 0.1 + 0.2}) == fingerprint({"a": 0.3})  # float noise is rounded away


def test_latency_and_cost_blocks(bundle, docs):
    res = run(OracleClassifier.from_docs(docs, bundle.policy), docs, bundle)
    lat = res.metrics["latency"]["wall_clock"]
    assert lat["n"] == 8 and {"mean_ms", "p50_ms", "p95_ms", "max_ms", "total_ms"} <= set(lat)
    assert res.metrics["latency"]["classifier_reported"] == {"n": 0}
    assert res.metrics["cost"] == {"documents_with_reported_cost": 0, "est_cost_usd_total": None}


def test_small_sample_labels_are_listed(bundle, docs):
    sm = run(OracleClassifier.from_docs(docs, bundle.policy), docs, bundle).metrics["headline"][
        "small_sample_labels"
    ]
    assert sm["min_support_flag"] == 25 and "PUBLIC" in sm["levels"] and "PHI" in sm["categories"]


def test_no_deferrals_note(bundle, docs):
    dv = run(OracleClassifier.from_docs(docs, bundle.policy), docs, bundle).metrics["headline"][
        "deferral_views"
    ]
    assert dv == {"n_deferred": 0, "note": "no deferrals: all views are identical"}


def test_write_run_creates_artifacts_and_report_is_grounded(bundle, docs, tmp_path):
    res = run(OracleClassifier.from_docs(docs, bundle.policy), docs, bundle)
    out = write_run(res, tmp_path)
    assert out.name == res.run_id
    assert {p.name for p in out.iterdir()} == {
        "run_manifest.json",
        "metrics.json",
        "predictions.jsonl",
        "report.md",
    }
    assert len((out / "predictions.jsonl").read_text().splitlines()) == 8
    assert json.loads((out / "run_manifest.json").read_text())["run_id"] == res.run_id
    report = (out / "report.md").read_text()
    assert (
        res.run_id in report
        and res.fingerprint in report
        and "no combined headline score" in report.lower()
    )
    assert (
        "Confusion matrix" in report
        and "**by tier**" in report
        and "Small-sample caveats" in report
    )
    assert "locked test split" in report  # the docs are in the test split


def test_write_run_leaves_nothing_behind_if_serialisation_fails(bundle, docs, tmp_path):
    res = run(OracleClassifier.from_docs(docs, bundle.policy), docs, bundle)
    res.manifest["unserialisable"] = object()
    with pytest.raises(TypeError):
        write_run(res, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_report_shows_undefined_as_na_not_zero(bundle):
    docs = [mkdoc("a", level="INTERNAL", split="dev", tier="T1")]
    res = run(OracleClassifier.from_docs(docs, bundle.policy), docs, bundle)
    report = render_run_report(res)
    assert "n/a" in report  # high-risk recall etc. are undefined with no positives
    assert res.metrics["headline"]["metrics"]["high_risk"]["recall"] is None
