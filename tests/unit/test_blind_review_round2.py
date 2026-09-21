"""Round 2 blind review: the ambiguous and disputed families, still blind, development splits only."""

from __future__ import annotations

import csv
import io
import json
import shutil
from pathlib import Path

import pytest

from app.classification.cli import main
from evals.classification.blind_compare import PackageError, load_package, read_reviewer
from evals.classification.blind_compare_round2 import FOCUS_FAMILY, run_round2
from evals.classification.blind_review import (
    RESPONSE_COLUMNS,
    ROUND2,
    ROUND2_EXTRA_DOCS,
    build_package,
    check_completed,
    disputed_families,
    round2_families,
    select_round2_items,
    to_csv,
)
from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents
from evals.classification.evaluate import git_info
from evals.classification.gold_review import REVIEW_SPLITS
from tests.helpers import mkdoc, only_explicit_locked_runs

DATA = Path(DEFAULT_DATA_DIR)


@pytest.fixture(scope="module")
def docs():
    return load_documents(DATA, splits=REVIEW_SPLITS)


@pytest.fixture(scope="module")
def built(bundle):
    return build_package(bundle, git_info(), str(DATA), ROUND2)


@pytest.fixture(scope="module")
def package():
    return load_package(DATA, ROUND2)


def _sheet(package, reviewer="rev-1", override=None, by_family=None):
    """A completed sheet in which the reviewer copies the gold, then overrides per sample / family."""
    override, by_family = override or {}, by_family or {}
    orig = {r["sample_id"]: r for r in csv.DictReader(io.StringIO(package["sheet_text"]))}
    rows = []
    for k in package["key"]:
        vals = {
            "human_level": k["gold_level"], "human_categories": k["gold_categories"] or "NONE",
            "human_confidence": "high", "human_rationale": "Follows the definitions.",
            "human_taxonomy_ambiguity": "no", "human_alternative_levels": "",
            "human_insufficient_information": "no", "reviewer_id": reviewer,
            "review_date": "2026-09-22",
        }  # fmt: skip
        vals.update(by_family.get(k["family_id"], {}))
        vals.update(override.get(k["sample_id"], {}))
        rows.append({**orig[k["sample_id"]], **vals})
    return to_csv(rows, ROUND2.sheet_columns)


# ---- selection ---------------------------------------------------------------------------------
def test_round2_covers_every_flagged_family_and_the_round1_disputed_families(docs):
    fams = round2_families(docs, disputed_families(DATA))
    flagged = {d.family_id for d in docs if d.ambiguity_flag}
    assert flagged <= set(fams) and set(disputed_families(DATA)) <= set(fams)
    assert FOCUS_FAMILY in fams


def test_one_document_per_family_except_the_focus_family_plus_controls(docs):
    fams = round2_families(docs, disputed_families(DATA))
    items = select_round2_items(docs, fams)
    review = [it for it in items if it["role"] == "review"]
    per = {f: sum(it["doc"].family_id == f for it in review) for f in fams}
    assert all(n == ROUND2_EXTRA_DOCS.get(f, 1) for f, n in per.items())
    controls = [it for it in items if it["role"] == "control"]
    assert controls and all(
        it["doc"].family_id not in fams and it["doc"].split == "dev" for it in controls
    )
    assert all(it["doc"].tier != "T5" for it in controls)
    assert [it["order"] for it in items] == list(range(1, len(items) + 1))


def test_selection_is_deterministic_and_never_loads_the_locked_split(docs):
    fams = round2_families(docs, disputed_families(DATA))
    a = [it["doc"].doc_id for it in select_round2_items(docs, fams)]
    assert a == [it["doc"].doc_id for it in select_round2_items(list(reversed(docs)), fams)]
    assert {d.split for d in docs} <= set(REVIEW_SPLITS)
    with pytest.raises(AssertionError, match="locked test split"):
        select_round2_items([mkdoc("t1", split="test", group="fam")], ["fam"])
    with pytest.raises(AssertionError, match="locked test split"):
        round2_families([mkdoc("t1", split="test", group="fam")], [])


# ---- the package -------------------------------------------------------------------------------
def test_the_reviewer_files_hold_no_answers_and_no_round1_clarifications(built):
    files, manifest = built
    text = files[ROUND2.sheet_file] + files[ROUND2.packet_file]
    assert manifest["leak_check"] == "passed" and manifest["locked_test_split_read"] is False
    assert manifest["labels_changed"] is False and manifest["splits_loaded"] == REVIEW_SPLITS
    # the decisions taken after Round 1 are guideline clarifications the reviewers must not be steered by
    assert "Clarifications after the blind review" not in text
    assert "source_system" not in text and "author_department" not in text
    rows = list(csv.DictReader(io.StringIO(files[ROUND2.sheet_file])))
    assert all(not r[c] for r in rows for c in RESPONSE_COLUMNS)


def test_the_key_and_manifest_are_kept_apart_from_the_reviewer_directory(built):
    files, _ = built
    assert {Path(p).parent.as_posix() for p in (ROUND2.sheet_file, ROUND2.packet_file)} == {
        ROUND2.blind_dir
    }
    assert Path(ROUND2.key_file).parent.as_posix() == ROUND2.key_dir != ROUND2.blind_dir
    assert set(files) == {ROUND2.sheet_file, ROUND2.packet_file, ROUND2.key_file}


def test_the_committed_round2_package_is_in_sync_with_its_generator(built):
    files, manifest = built
    for rel, text in files.items():
        assert (DATA / rel).read_text(encoding="utf-8") == text, rel
    committed = json.loads((DATA / ROUND2.manifest_file).read_text())
    for k in (
        "n_items",
        "n_review",
        "n_controls",
        "review_families",
        "reviewer_files_sha256",
        "key_sha256",
        "dataset_sha256",
    ):
        assert committed[k] == manifest[k], k
    assert "adjudication_artifacts_sha256" not in committed  # independent of Round 1's artifacts
    only_explicit_locked_runs(DATA)


def test_round2_is_a_superset_of_the_round1_disputed_families(package):
    fams = {k["family_id"] for k in package["key"] if k["role"] == "review"}
    assert set(disputed_families(DATA)) <= fams and FOCUS_FAMILY in fams


# ---- checking a returned sheet ----------------------------------------------------------------
def test_a_returned_sheet_is_checked_and_an_edited_input_is_refused(package, bundle):
    good = _sheet(package)
    assert check_completed(good, package["sheet_text"], bundle, ROUND2) == []
    rows = list(csv.DictReader(io.StringIO(good)))
    rows[0]["content"] += " edited"
    errs = check_completed(
        to_csv(rows, ROUND2.sheet_columns), package["sheet_text"], bundle, ROUND2
    )
    assert any("input text was edited" in e for e in errs)


# ---- comparison ------------------------------------------------------------------------------
def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_a_reviewer_who_copies_the_gold_agrees_everywhere(package, bundle, tmp_path):
    p = _write(tmp_path, "a.csv", _sheet(package))
    report, per_sample = run_round2([p], bundle, DATA)
    assert "Round 2 blind review" in report and "AI REVIEW" not in report
    rows = list(csv.DictReader(io.StringIO(per_sample)))
    assert len(rows) == len(package["key"])
    assert all(
        r["level_human_eq_gold"] == "TRUE" and r["cats_human_eq_gold"] == "TRUE" for r in rows
    )
    assert "reviewer_kind" in rows[0] and {r["reviewer_kind"] for r in rows} == {"human"}
    assert "locked test split was not read" in report and "no model predictions" in report.lower()


def test_two_reviewers_show_agreement_and_where_they_differ_from_gold(package, bundle, tmp_path):
    focus = [k["sample_id"] for k in package["key"] if k["family_id"] == FOCUS_FAMILY]
    other = {"human_level": "CONFIDENTIAL", "human_rationale": "Small cells but no identifiers."}
    a = _write(tmp_path, "a.csv", _sheet(package, "rev-a"))
    b = _write(tmp_path, "b.csv", _sheet(package, "rev-b", by_family={FOCUS_FAMILY: other}))
    c = _write(tmp_path, "c.csv", _sheet(package, "rev-c", by_family={FOCUS_FAMILY: other}))
    report, _ = run_round2([a, b], bundle, DATA)
    assert "Inter-reviewer agreement: `rev-a` vs `rev-b`" in report and "reviewers differ" in report
    assert "Cohen's kappa" in report
    report, _ = run_round2([b, c], bundle, DATA)
    assert "both agree with each other, differ from gold" in report
    assert (
        "Small cells but no identifiers." in report
    )  # the focus family shows the reviewers' words
    assert len(focus) == ROUND2_EXTRA_DOCS[FOCUS_FAMILY]


def test_an_ai_review_and_a_note_are_labelled_and_a_bad_kind_is_refused(package, bundle, tmp_path):
    p = _write(tmp_path, "a.csv", _sheet(package))
    report, per_sample = run_round2([p], bundle, DATA, "ai", "same text as another sheet")
    assert report.startswith("# Round 2 blind review: gold vs blind reviewer(s) (AI review)")
    assert "AI REVIEW: NOT HUMAN VALIDATION" in report and "same text as another sheet" in report
    assert "ai" in per_sample.splitlines()[1]
    with pytest.raises(PackageError):
        run_round2([p], bundle, DATA, "robot")


def test_a_duplicate_reviewer_id_and_a_malformed_sheet_are_refused(package, bundle, tmp_path):
    a = _write(tmp_path, "a.csv", _sheet(package))
    with pytest.raises(PackageError, match="more than one sheet"):
        run_round2([a, a], bundle, DATA)
    rows = list(csv.DictReader(io.StringIO(_sheet(package))))
    rows[0]["human_level"] = "SECRET"
    bad = _write(tmp_path, "bad.csv", to_csv(rows, ROUND2.sheet_columns))
    with pytest.raises(PackageError, match="not well-formed"):
        run_round2([bad], bundle, DATA)


def test_a_changed_key_or_sheet_fails_the_integrity_check(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(DATA, data)
    key = data / ROUND2.key_file
    key.write_text(key.read_text().replace("HIGHLY_CONFIDENTIAL", "PUBLIC", 1))
    with pytest.raises(PackageError, match="integrity"):
        load_package(data, ROUND2)


def test_the_cli_writes_only_to_the_round2_results_directory(package, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(DATA, data)
    s = _write(tmp_path, "s.csv", _sheet(package, "rev-a"))
    tracked = {p: p.read_bytes() for p in (data / "review").rglob("*") if p.is_file()}
    assert (
        main(
            [
                "review",
                "blind-check",
                "--variant",
                "round2",
                "--sheet",
                str(s),
                "--data-dir",
                str(data),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "review",
                "blind-compare",
                "--variant",
                "round2",
                "--sheet",
                str(s),
                "--data-dir",
                str(data),
            ]
        )
        == 0
    )
    out = data / "review/blind_results_round2"
    assert {p.name for p in out.iterdir()} == {
        "blind_review_comparison.md",
        "blind_review_comparison.csv",
    }
    assert {p: p.read_bytes() for p in tracked if p.exists()} == tracked
    assert (
        main(
            [
                "review",
                "blind-compare",
                "--variant",
                "round2",
                "--sheet",
                str(s),
                "--data-dir",
                str(data),
                "--reviewer-kind",
                "ai",
            ]
        )
        == 0
    )
    assert (data / "review/blind_results_round2_ai/blind_review_comparison.md").exists()


def test_read_reviewer_normalises_round2_sheets(package, bundle):
    rv = read_reviewer(_sheet(package, "rev-a"), package, bundle)
    assert rv["reviewer_id"] == "rev-a" and len(rv["items"]) == len(package["key"])
