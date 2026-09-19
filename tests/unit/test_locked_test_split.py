"""The locked test split: hard protection, explicit authorisation, and audit trail."""

from __future__ import annotations

import json

import pytest

from app.classification.cli import main
from evals.classification.baselines import OracleClassifier
from evals.classification.dataset.build import load_documents, load_manifest
from evals.classification.evaluate import evaluate
from evals.classification.lock import (
    ALLOW_FLAG,
    DEVELOPMENT_SPLITS,
    LockedTestAuthorization,
    LockedTestSplitError,
    check_access,
    requires_authorization,
)
from evals.classification.reporting import append_access_log, render_run_report
from tests.helpers import mkdoc

MANIFEST = {
    "dataset_id": "t", "dataset_version": "1.0.0", "dataset_sha256": "a" * 64, "spec_hash": "b" * 64,
    "label_status": "AI-generated synthetic dataset — pending human gold-label review",
}  # fmt: skip


def test_development_splits_are_exactly_train_calibration_dev():
    assert DEVELOPMENT_SPLITS == ("train", "calibration", "dev")
    assert not requires_authorization(DEVELOPMENT_SPLITS) and requires_authorization(
        ["dev", "test"]
    )


def test_check_access_refuses_without_authorisation_and_names_the_flag():
    check_access(["train", "dev"], None)
    with pytest.raises(LockedTestSplitError, match=ALLOW_FLAG):
        check_access(["test"], None)
    check_access(["test"], LockedTestAuthorization.now())


def test_authorisation_records_mechanism_time_and_purpose():
    auth = LockedTestAuthorization.now()
    d = auth.to_dict()
    assert d["mechanism"] == ALLOW_FLAG and d["authorized_at"].endswith("+00:00")
    assert "no tuning" in d["purpose"]


@pytest.fixture()
def test_docs():
    return [
        mkdoc("t1", level="HIGHLY_CONFIDENTIAL", cats=["PHI"], group="g1", split="test"),
        mkdoc("t2", level="INTERNAL", group="g2", split="test"),
    ]


def test_evaluate_refuses_test_documents_without_authorisation(bundle, test_docs):
    clf = OracleClassifier.from_docs(test_docs, bundle.policy)
    with pytest.raises(LockedTestSplitError):
        evaluate(clf, test_docs, bundle, MANIFEST)
    dev = [mkdoc("d1", split="dev")]
    evaluate(OracleClassifier.from_docs(dev, bundle.policy), dev, bundle, MANIFEST)  # fine


def test_authorised_test_run_records_the_full_audit_manifest(bundle, test_docs):
    auth = LockedTestAuthorization.now()
    clf = OracleClassifier.from_docs(test_docs, bundle.policy)
    res = evaluate(clf, test_docs, bundle, MANIFEST, locked_test_authorization=auth)
    man = res.manifest
    assert man["dataset"]["evaluated_locked_test_split"] is True
    assert man["dataset"]["locked_test_authorization"] == auth.to_dict()
    access = man["locked_test_access"]
    # everything the approved requirement lists: git SHA, dataset hash, config versions,
    # model/rules version, timestamp and the explicit authorisation
    assert access["authorized"] is True and access["authorization"] == auth.to_dict()
    assert set(access["git_commit"] and ["ok"]) == {"ok"} or access["git_commit"] is None
    assert access["dataset_sha256"] == "a" * 64
    assert access["config_versions"] == bundle.versions()
    assert access["classifier"] == "oracle@1.0" and "classifier_params" in access
    assert access["timestamp"] == man["started_at"]


def test_report_carries_a_prominent_locked_test_banner(bundle, test_docs):
    res = evaluate(OracleClassifier.from_docs(test_docs, bundle.policy), test_docs, bundle, MANIFEST,
                   locked_test_authorization=LockedTestAuthorization.now())  # fmt: skip
    report = render_run_report(res)
    assert "LOCKED TEST SPLIT EVALUATED - REPORT-ONLY" in report and ALLOW_FLAG in report
    assert "must not be used to tune" in report


def test_access_log_is_append_only_and_complete(bundle, test_docs, tmp_path):
    log = tmp_path / "access.jsonl"
    auth = LockedTestAuthorization.now()
    clf = OracleClassifier.from_docs(test_docs, bundle.policy)
    for _ in range(2):
        append_access_log(
            evaluate(clf, test_docs, bundle, MANIFEST, locked_test_authorization=auth), log
        )
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(entries) == 2
    for e in entries:
        assert {"run_id", "timestamp", "git_commit", "git_dirty", "dataset_sha256", "config_versions",
                "classifier", "authorization", "metrics_fingerprint"} <= set(e)  # fmt: skip
        assert e["authorization"]["mechanism"] == ALLOW_FLAG
    dev = [mkdoc("d1", split="dev")]
    with pytest.raises(ValueError, match="did not touch"):
        append_access_log(
            evaluate(OracleClassifier.from_docs(dev, bundle.policy), dev, bundle, MANIFEST), log
        )
    assert len(log.read_text().splitlines()) == 2  # unchanged


# ---- CLI ------------------------------------------------------------------------------------
def test_cli_refuses_the_test_split_without_the_flag(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    rc = main(
        [
            "eval",
            "run",
            "--classifier",
            "oracle",
            "--split",
            "test",
            "--runs-dir",
            str(tmp_path / "r"),
            "--access-log",
            str(log),
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "locked" in err and ALLOW_FLAG in err
    assert not (tmp_path / "r").exists() and not log.exists()  # nothing written, nothing logged


@pytest.mark.parametrize("split", ["train,test", "dev,calibration,test", "test,dev"])
def test_cli_refuses_the_test_split_even_when_mixed_with_others(tmp_path, capsys, split):
    assert (
        main(
            [
                "eval",
                "run",
                "--classifier",
                "majority",
                "--split",
                split,
                "--runs-dir",
                str(tmp_path),
            ]
        )
        == 2
    )
    assert list(tmp_path.iterdir()) == []


def test_cli_all_means_development_splits_only(tmp_path, capsys):
    assert (
        main(
            ["eval", "run", "--classifier", "oracle", "--split", "all", "--runs-dir", str(tmp_path)]
        )
        == 0
    )
    manifest = json.loads(next(tmp_path.glob("*/run_manifest.json")).read_text())
    assert manifest["dataset"]["splits_evaluated"] == ["calibration", "dev", "train"]
    assert manifest["dataset"]["evaluated_locked_test_split"] is False
    assert "independent families" in capsys.readouterr().out


def test_cli_with_the_flag_warns_logs_and_records_authorisation(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    rc = main(["eval", "run", "--classifier", "oracle", "--split", "test", "--allow-locked-test",
               "--runs-dir", str(tmp_path / "r"), "--access-log", str(log)])  # fmt: skip
    assert rc == 0
    err = capsys.readouterr().err
    assert (
        "LOCKED TEST SPLIT AUTHORISED" in err and "REPORT-ONLY" in err and "access recorded" in err
    )
    manifest = json.loads(next((tmp_path / "r").glob("*/run_manifest.json")).read_text())
    assert manifest["locked_test_access"]["authorization"]["mechanism"] == ALLOW_FLAG
    assert manifest["dataset"]["evaluated_locked_test_split"] is True
    (entry,) = [json.loads(line) for line in log.read_text().splitlines()]
    assert entry["run_id"] == manifest["run_id"] and entry["classifier"] == "oracle@1.0"


def test_flag_alone_does_not_touch_the_test_split_unless_requested(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    assert main(["eval", "run", "--classifier", "oracle", "--split", "dev", "--allow-locked-test",
                 "--runs-dir", str(tmp_path / "r"), "--access-log", str(log)]) == 0  # fmt: skip
    assert "LOCKED TEST SPLIT AUTHORISED" not in capsys.readouterr().err and not log.exists()


def test_validate_harness_defaults_to_development_splits(tmp_path):
    assert main(["eval", "validate-harness", "--out-dir", str(tmp_path)]) == 0
    md = (tmp_path / "harness-validation.md").read_text()
    assert "splits validated: train, calibration, dev" in md and "| test |" not in md
    assert "pending human gold-label review" in md
    data = json.loads((tmp_path / "harness-validation.json").read_text())
    assert {r["split"] for r in data["runs"]} == {"train", "calibration", "dev"}


def test_validate_harness_includes_test_only_with_the_flag(tmp_path, capsys):
    assert (
        main(["eval", "validate-harness", "--allow-locked-test", "--out-dir", str(tmp_path)]) == 0
    )
    assert "LOCKED TEST SPLIT AUTHORISED" in capsys.readouterr().err
    data = json.loads((tmp_path / "harness-validation.json").read_text())
    assert "test" in {r["split"] for r in data["runs"]}


def test_default_loader_never_returns_test_documents():
    assert all(d.split != "test" for d in load_documents())
    assert load_manifest()["files"]["test"]["n_docs"] > 0  # the split exists; it is just guarded
