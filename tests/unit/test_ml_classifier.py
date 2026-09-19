"""ML classifier contract, harness integration and protocol guarantees."""

from __future__ import annotations

import json

import pytest

from app.classification.cli import main
from app.classification.interfaces import Classifier
from app.classification.schemas import ClassificationRequest, Document
from evals.classification.dataset import build as dataset_build
from evals.classification.dataset.build import load_documents, load_manifest
from evals.classification.evaluate import evaluate
from evals.classification.reporting import render_run_report
from ml.classification import build_ml_classifier

LEVELS = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_CONFIDENTIAL"]


@pytest.fixture(scope="module")
def ml(bundle):
    return build_ml_classifier(bundle)


def req(text, filename="x.txt", evidence=True):
    r = ClassificationRequest(
        request_id="r1",
        document=Document(document_id="d1", content=text, filename=filename, extension="txt"),
    )
    r.options.include_evidence = evidence
    return r


def test_interface_and_params(ml):
    assert isinstance(ml, Classifier) and ml.name == "ml"
    p = ml.params()
    assert p["fit_splits"] == ["train"] and p["calibration_splits"] == ["calibration"]
    assert p["level_C"] == 0.3 and p["category_C"] == 1.0 and p["category_threshold"] == 0.5
    assert len(p["ml_config_sha256"]) == 64 and p["model_id"].startswith("ml-1.0.0-")
    assert p["training_data"]["n_train"] > 300 and p["training_data"]["n_calibration"] > 90


def test_result_carries_calibrated_probabilities_and_valid_scores(ml):
    res = ml.classify(req("Quarterly notes about the sales team offsite and lunch plans."))
    assert res.status == "ok" and res.level.value in LEVELS and res.level.decided_by == "ml"
    c = res.level.confidence
    assert c.kind == "calibrated_probability" and c.calibrated and c.calibration_ref == ml.model_id
    assert res.scores.calibrated and res.scores.calibration_ref == ml.model_id
    assert set(res.scores.level) == set(LEVELS) and sum(res.scores.level.values()) == pytest.approx(
        1.0
    )
    assert len(res.scores.categories) == 8 and res.level.confidence.raw == max(
        res.scores.level.values()
    )
    assert res.routing.abstained is False and res.routing.stages_run == ["ml"]
    assert res.versions.ml_model == ml.model_id and res.versions.classifier == "ml@1.0.0"


def test_categories_follow_the_configured_threshold_and_high_risk_is_derived(ml, bundle):
    docs = load_documents(splits=["dev"])[:40]
    for d in docs:
        res = ml.classify(d.to_request())
        for cat in res.categories:
            assert (
                res.scores.categories[cat.id] >= 0.5
                and cat.confidence.kind == "calibrated_probability"
            )
        expected = bundle.policy.derive_high_risk(res.level.value, [c.id for c in res.categories])
        assert res.high_risk.value == expected.value


def test_higher_threshold_asserts_fewer_categories(bundle):
    from ml.classification.config import load_ml_config

    cfg, _ = load_ml_config(bundle.policy)
    strict = cfg.model_copy(
        update={"decision": cfg.decision.model_copy(update={"category_threshold": 0.99})}
    )
    a, b = build_ml_classifier(bundle), build_ml_classifier(bundle, cfg_override=strict)
    docs = load_documents(splits=["dev"])[:60]
    n = lambda clf: sum(len(clf.classify(d.to_request()).categories) for d in docs)  # noqa: E731
    assert n(b) <= n(a)


def test_evidence_is_inferred_and_shows_only_safe_words(ml):
    res = ml.classify(
        req(
            "patient diagnosis medication note for the clinic. Contact ana.smith@x.example 912-34-5678"
        )
    )
    assert res.evidence, "expected feature-attribution evidence"
    for e in res.evidence:
        assert e.source == "ml" and e.type == "feature_attribution" and e.provenance == "inferred"
        assert e.locator is None and e.strength == "n/a"
        assert not any(ch.isdigit() or ch == "@" for ch in e.excerpt)
    ids = {e.evidence_id for e in res.evidence}
    assert all(i in ids for c in res.categories for i in c.evidence_ids)


def test_evidence_can_be_omitted(ml):
    assert ml.classify(req("hello world", evidence=False)).evidence == []


def test_classification_is_deterministic(ml):
    a = ml.classify(req("Board memo about a possible acquisition."))
    b = ml.classify(req("Board memo about a possible acquisition."))
    assert a.scores == b.scores and a.level == b.level


def test_two_builds_produce_identical_models(bundle):
    a, b = build_ml_classifier(bundle), build_ml_classifier(bundle)
    r = req("Some document text about payroll and employees.")
    assert a.classify(r).scores == b.classify(r).scores and a.model_id == b.model_id


def test_training_only_ever_loads_development_splits(bundle, monkeypatch):
    seen = []
    real = dataset_build.load_documents

    def spy(data_dir=None, splits=None, verify=True, locked_test_authorization=None):
        seen.append((list(splits) if splits else None, locked_test_authorization))
        return real(data_dir, splits, verify, locked_test_authorization)

    monkeypatch.setattr("ml.classification.classifier.load_documents", spy)
    build_ml_classifier(bundle)
    assert seen and all(auth is None for _, auth in seen)
    assert {s for splits, _ in seen for s in (splits or [])} <= {"train", "calibration", "dev"}
    assert "test" not in {s for splits, _ in seen for s in (splits or [])}


def test_harness_integration_reports_calibration_and_flags_in_sample(ml, bundle):
    dev = load_documents(splits=["dev"])
    res = evaluate(ml, dev, bundle, load_manifest())
    cal = res.metrics["headline"]["calibration"]
    assert (
        cal["calibrated_claim"] is True
        and 0 <= cal["level"]["ece"] <= 1
        and cal["categories"]["n"] > 0
    )
    text = render_run_report(res)
    assert "Calibration (probabilities" in text and "IN-SAMPLE WARNING" not in text
    assert res.manifest["classifier"]["params"]["fit_splits"] == ["train"]
    assert (
        res.metrics["all_tiers"]["coverage"]["n_failed"] == 0
        and res.metrics["all_tiers"]["coverage"]["n_abstained"] == 0
    )

    insample = evaluate(ml, load_documents(splits=["train"])[:60], bundle, load_manifest())
    assert "IN-SAMPLE WARNING" in render_run_report(insample) and "fitted on" in render_run_report(
        insample
    )
    calib = evaluate(ml, load_documents(splits=["calibration"])[:60], bundle, load_manifest())
    assert "used to fit its calibrators" in render_run_report(calib)


def test_cli_runs_ml_on_dev_and_refuses_the_locked_split(tmp_path, capsys):
    assert (
        main(["eval", "run", "--classifier", "ml", "--split", "dev", "--runs-dir", str(tmp_path)])
        == 0
    )
    out = capsys.readouterr().out
    assert "high-risk precision" in out
    manifest = json.loads(next(tmp_path.glob("*/run_manifest.json")).read_text())
    assert (
        manifest["classifier"]["name"] == "ml"
        and manifest["dataset"]["evaluated_locked_test_split"] is False
    )
    assert (
        main(
            [
                "eval",
                "run",
                "--classifier",
                "ml",
                "--split",
                "test",
                "--runs-dir",
                str(tmp_path / "x"),
            ]
        )
        == 2
    )
    assert "locked" in capsys.readouterr().err


def test_ml_select_prints_the_selection_protocol(tmp_path, capsys):
    out = tmp_path / "cv.json"
    assert main(["ml", "select", "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert "TRAIN only" in data["protocol"] and len(data["rows"]) == 4
    assert set(data["selected"]) == {"level_head_C", "category_head_C"}
    assert data["n_train_families"] > 50


def test_a_classifier_never_claims_calibration_it_did_not_achieve(bundle):
    """With too few calibration positives the calibrators are not fitted; results must say so."""
    from ml.classification.config import load_ml_config

    cfg, _ = load_ml_config(bundle.policy)
    thin = cfg.model_copy(
        update={"calibration": cfg.calibration.model_copy(update={"min_positives": 200})}
    )
    clf = build_ml_classifier(bundle, cfg_override=thin)
    res = clf.classify(req("Some document about a patient diagnosis and medication."))
    assert res.scores.calibrated is False and res.scores.calibration_ref is None
    assert (
        res.level.confidence.kind == "uncalibrated_score"
        and res.level.confidence.calibrated is False
    )
    assert all(c.confidence.kind == "uncalibrated_score" for c in res.categories)
    docs = load_documents(splits=["dev"])[:30]
    m = evaluate(clf, docs, bundle, load_manifest()).metrics["headline"]["calibration"]
    assert m["calibrated_claim"] is False
