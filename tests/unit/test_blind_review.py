"""The blind human-review package: blind by construction, deterministic, in sync, and checkable."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

import pytest

from app.classification.cli import main
from evals.classification.blind_review import (
    BLIND_DIR,
    KEY_DIR,
    KEY_FILE,
    MANIFEST_FILE,
    NO_CATEGORY,
    PACKET_FILE,
    PRIOR_ARTIFACTS,
    RESPONSE_COLUMNS,
    SHEET_COLUMNS,
    SHEET_FILE,
    _fence,
    _guideline_excerpt,
    build_package,
    check_completed,
    disputed_families,
    key_rows,
    leak_check,
    select_items,
    sheet_rows,
    taxonomy_markdown,
    to_csv,
)
from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents
from evals.classification.gold_review import REVIEW_SPLITS
from tests.helpers import mkdoc, only_explicit_locked_runs

DATA = str(DEFAULT_DATA_DIR)


@pytest.fixture(scope="module")
def docs():
    return load_documents(DATA, splits=REVIEW_SPLITS)


@pytest.fixture(scope="module")
def items(docs):
    return select_items(docs, disputed_families(DATA))


@pytest.fixture(scope="module")
def package(bundle):
    return build_package(bundle, {"commit": "x", "branch": "b", "dirty": False}, DATA)


# ---- the locked test split is never involved -----------------------------------------------------------
def test_selection_refuses_a_locked_test_document():
    with pytest.raises(AssertionError, match="locked test split"):
        select_items([mkdoc("t1", split="test", group="fam")], ["fam"])


def test_the_package_is_built_from_development_splits_only(docs, package):
    assert {d.split for d in docs} <= set(REVIEW_SPLITS)
    assert package[1]["splits_loaded"] == ["train", "calibration", "dev"]
    assert package[1]["locked_test_split_read"] is False
    only_explicit_locked_runs(DATA)


# ---- which samples ---------------------------------------------------------------------------------------
def test_the_disputed_families_are_the_ones_needing_a_human_decision():
    assert disputed_families(DATA) == [
        "amb_customer_case_study_draft",
        "hn_business_case_study",
        "hn_public_api_docs_placeholder_keys",
        "phi_prescription_record",
    ]


def test_every_disputed_document_is_included_and_controls_are_clean(items, docs):
    disputed = set(disputed_families(DATA))
    want = {d.doc_id for d in docs if d.family_id in disputed}
    got = {it["doc"].doc_id for it in items if it["role"] == "disputed"}
    assert got == want and len(got) == 21
    controls = [it["doc"] for it in items if it["role"] == "control"]
    assert len(controls) == 12
    assert all(
        c.split == "dev" and c.tier != "T5" and c.family_id not in disputed for c in controls
    )
    for level in ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_CONFIDENTIAL"):
        assert sum(c.gold_level == level for c in controls) == 3
    assert len({it["doc"].doc_id for it in items}) == len(items)


def test_selection_is_deterministic_and_independent_of_input_order(docs):
    disputed = disputed_families(DATA)
    a = [(i["doc"].doc_id, i["role"]) for i in select_items(docs, disputed)]
    b = [(i["doc"].doc_id, i["role"]) for i in select_items(list(reversed(docs)), disputed)]
    assert a == b
    other = [i["doc"].doc_id for i in select_items(docs, disputed, seed="another seed")]
    assert other != [x for x, _ in a]


def test_the_order_does_not_cluster_the_disputed_families(items):
    roles = [it["role"] for it in items]
    assert roles != sorted(roles)  # controls and disputed documents are interleaved


# ---- blindness ------------------------------------------------------------------------------------------
def test_the_reviewer_sheet_has_only_the_input_and_empty_response_fields(items):
    rows = sheet_rows(items)
    assert list(rows[0]) == SHEET_COLUMNS
    assert all(r[c] == "" for r in rows for c in RESPONSE_COLUMNS)
    assert [c for c in SHEET_COLUMNS if c not in RESPONSE_COLUMNS] == [
        "sample_id",
        "filename",
        "content",
    ]


def test_the_committed_reviewer_files_contain_no_answer_information(docs, bundle):
    files = {p: Path(DATA, p).read_text() for p in (SHEET_FILE, PACKET_FILE)}
    assert leak_check(files, docs, bundle) == []
    for text in files.values():
        low = text.lower()
        for token in (
            "original_gold",
            "pred_",
            "proposed",
            "adjudicat",
            "hybrid",
            "llm_",
            "rules engine",
        ):
            assert token not in low
    # metadata the classifiers never saw is not shown either
    for meta in {d.metadata.get("source_system") for d in docs if d.metadata.get("source_system")}:
        assert meta not in files[SHEET_FILE]


def test_the_leak_check_catches_planted_leaks(docs, bundle):
    fam = "hn_public_api_docs_placeholder_keys"
    note = next(d.annotation_notes for d in docs if d.family_id == fam)
    assert leak_check({"f": f"see {fam}"}, docs, bundle)
    assert leak_check({"f": note}, docs, bundle)
    assert leak_check({"f": "pred_hybrid,original_gold_level"}, docs, bundle)
    assert leak_check({"f": "an ordinary document"}, docs, bundle) == []


def test_a_leaking_package_is_refused(bundle, monkeypatch):
    import evals.classification.blind_review as br

    leaking = [dict.fromkeys(SHEET_COLUMNS, "hn_public_api_docs_placeholder_keys")]
    monkeypatch.setattr(br, "sheet_rows", lambda items, variant=None: leaking)
    with pytest.raises(ValueError, match="leaks answer information"):
        build_package(bundle, {}, DATA)


def test_the_key_holds_the_answers_and_lives_apart_from_the_reviewer_files(items):
    rows = key_rows(items)
    assert {"role", "family_id", "gold_level", "gold_categories"} <= set(rows[0])
    assert Path(KEY_DIR) != Path(BLIND_DIR) and Path(BLIND_DIR) not in Path(KEY_DIR).parents
    assert {r["role"] for r in rows} == {"disputed", "control"}


def test_the_definitions_are_verbatim_from_the_taxonomy_and_guidelines(bundle):
    md = taxonomy_markdown(bundle)
    for c in bundle.taxonomy.categories:
        assert " ".join(c.description.split()) in md
        assert all(x in md for x in c.counter_examples)
    for lv in bundle.taxonomy.levels:
        assert " ".join(lv.description.split()) in md
    ex = _guideline_excerpt()
    assert "Tie-break (ambiguity)" in ex and "### 3. Deciding categories" in ex
    assert (
        "Evidence annotation" not in ex and "Hard negative" not in ex
    )  # dataset-construction detail


def test_the_packet_states_what_is_and_is_not_shown():
    packet = Path(DATA, PACKET_FILE).read_text()
    assert "AI-generated synthetic dataset — pending human gold-label review" in packet
    assert "deliberately not part of this exercise" in packet
    assert "Do **not** use an AI model" in packet


def test_content_containing_backticks_cannot_break_out_of_its_fence():
    assert _fence("plain") == "```"
    assert len(_fence("has ``` inside")) == 4
    assert len(_fence("has ````` inside")) == 6


# ---- in sync, and the prior adjudication artifacts are untouched ------------------------------------------
def test_the_committed_package_is_in_sync_with_its_generator(package):
    files, manifest = package
    for rel, text in files.items():
        assert Path(DATA, rel).read_text() == text, (
            f"{rel} is stale; run `dataguard-uc4 review blind-package`"
        )
    committed = json.loads(Path(DATA, MANIFEST_FILE).read_text())
    for k in ("n_items", "n_disputed", "n_controls", "seed", "dataset_sha256", "decisions_sha256",
              "reviewer_files_sha256", "key_sha256", "adjudication_artifacts_sha256", "leak_check"):  # fmt: skip
        assert committed[k] == manifest[k], k
    assert committed["labels_changed"] is False and committed["locked_test_split_read"] is False


def test_the_adjudication_artifacts_are_unchanged_since_the_package_was_built():
    committed = json.loads(Path(DATA, MANIFEST_FILE).read_text())
    for rel in PRIOR_ARTIFACTS:
        assert (
            hashlib.sha256(Path(DATA, rel).read_bytes()).hexdigest()
            == committed["adjudication_artifacts_sha256"][rel]
        )


def test_the_dataset_is_unchanged_since_the_package_was_built():
    committed = json.loads(Path(DATA, MANIFEST_FILE).read_text())
    manifest = json.loads(Path(DATA, "manifest.json").read_text())
    assert committed["dataset_sha256"] == manifest["dataset_sha256"]


# ---- checking a returned sheet ---------------------------------------------------------------------------
GOOD = {
    "human_level": "INTERNAL", "human_categories": NO_CATEGORY, "human_confidence": "medium",
    "human_rationale": "Ordinary business content with no public-release intent.",
    "human_taxonomy_ambiguity": "yes", "human_alternative_levels": "PUBLIC",
    "human_insufficient_information": "no", "reviewer_id": "reviewer-1", "review_date": "2026-09-21",
}  # fmt: skip


def _filled(items, **override):
    rows = sheet_rows(items)
    for r in rows:
        r.update({**GOOD, **override})
    return rows


def test_a_well_formed_returned_sheet_passes(items, bundle):
    original = to_csv(sheet_rows(items), SHEET_COLUMNS)
    assert check_completed(to_csv(_filled(items), SHEET_COLUMNS), original, bundle) == []


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"human_level": "SECRET"}, "human_level"),
        ({"human_level": ""}, "human_level"),
        ({"human_categories": ""}, "human_categories"),
        ({"human_categories": "PII;NOPE"}, "human_categories"),
        ({"human_categories": "NONE;PII"}, "human_categories"),
        ({"human_confidence": "certain"}, "human_confidence"),
        ({"human_rationale": "  "}, "human_rationale"),
        ({"human_taxonomy_ambiguity": "maybe"}, "human_taxonomy_ambiguity"),
        ({"human_taxonomy_ambiguity": "no"}, "alternative levels given"),
        ({"human_alternative_levels": "PUBLIC;NOPE"}, "human_alternative_levels"),
        ({"human_insufficient_information": ""}, "human_insufficient_information"),
        ({"reviewer_id": ""}, "reviewer_id"),
        ({"review_date": "21/09/2026"}, "review_date"),
    ],
)
def test_a_malformed_returned_sheet_is_rejected(items, bundle, override, fragment):
    original = to_csv(sheet_rows(items), SHEET_COLUMNS)
    errs = check_completed(to_csv(_filled(items, **override), SHEET_COLUMNS), original, bundle)
    assert errs and any(fragment in e for e in errs)


def test_a_blank_level_is_allowed_only_with_insufficient_information(items, bundle):
    original = to_csv(sheet_rows(items), SHEET_COLUMNS)
    ok = _filled(items, human_level="", human_insufficient_information="yes")
    assert check_completed(to_csv(ok, SHEET_COLUMNS), original, bundle) == []


def test_edited_input_missing_duplicate_and_unknown_rows_are_rejected(items, bundle):
    original = to_csv(sheet_rows(items), SHEET_COLUMNS)
    rows = _filled(items)
    edited = [dict(r) for r in rows]
    edited[0]["content"] += " tampered"
    assert any(
        "input text was edited" in e
        for e in check_completed(to_csv(edited, SHEET_COLUMNS), original, bundle)
    )
    assert any(
        "missing sample" in e
        for e in check_completed(to_csv(rows[1:], SHEET_COLUMNS), original, bundle)
    )
    assert any(
        "duplicated" in e
        for e in check_completed(to_csv(rows + rows[:1], SHEET_COLUMNS), original, bundle)
    )
    unknown = [dict(rows[0], sample_id="uc4-unknown")] + rows[1:]
    assert any(
        "unknown sample id" in e
        for e in check_completed(to_csv(unknown, SHEET_COLUMNS), original, bundle)
    )
    assert check_completed("sample_id,content\nx,y\n", original, bundle)  # wrong columns


def test_the_check_cli_reports_and_sets_the_exit_code(items, tmp_path, capsys):
    good = tmp_path / "good.csv"
    good.write_text(to_csv(_filled(items), SHEET_COLUMNS))
    assert main(["review", "blind-check", "--sheet", str(good)]) == 0
    assert "well-formed" in capsys.readouterr().out
    bad = tmp_path / "bad.csv"
    bad.write_text(to_csv(_filled(items, human_level="SECRET"), SHEET_COLUMNS))
    assert main(["review", "blind-check", "--sheet", str(bad)]) == 1
    assert "ERROR" in capsys.readouterr().out


def test_the_committed_sheet_parses_and_matches_the_key(items):
    sheet = list(csv.DictReader(io.StringIO(Path(DATA, SHEET_FILE).read_text())))
    key = list(csv.DictReader(io.StringIO(Path(DATA, KEY_FILE).read_text())))
    assert [r["sample_id"] for r in sheet] == [r["sample_id"] for r in key]
    assert [r["review_order"] for r in key] == [str(i) for i in range(1, len(key) + 1)]


# ---- the decision brief names exactly the affected samples ---------------------------------------------------
def test_the_decision_brief_lists_the_real_samples_of_each_family(docs):
    import re

    brief = (Path(DATA).parents[2] / "docs/uc4/results/gold-review-decision-brief.md").read_text()
    sections = re.split(r"^## ", brief, flags=re.M)
    fam_by_letter = {
        "A.": {"hn_public_api_docs_placeholder_keys", "hn_business_case_study"},
        "B.": {"phi_prescription_record"},
        "C.": {"amb_customer_case_study_draft"},
    }
    for sec in sections:
        for letter, fams in fam_by_letter.items():
            if sec.startswith(letter):
                want = {d.doc_id for d in docs if d.family_id in fams}
                assert set(re.findall(r"uc4-[0-9a-f]{10}", sec)) == want, letter
