"""The metadata-shown blind-review variant: same items, different order, metadata visible, still blind."""

from __future__ import annotations

import csv
import io
import json
import shutil
from pathlib import Path

import pytest

from app.classification.cli import main
from evals.classification.blind_compare import (
    PackageError,
    compare,
    load_package,
    read_reviewer,
    run,
)
from evals.classification.blind_review import (
    CONTENT,
    METADATA,
    RESPONSE_COLUMNS,
    VARIANTS,
    build_package,
    check_completed,
    disputed_families,
    leak_check,
    select_items,
    sheet_rows,
    source_metadata,
    to_csv,
)
from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents
from evals.classification.gold_review import REVIEW_SPLITS
from tests.helpers import only_explicit_locked_runs

DATA = Path(DEFAULT_DATA_DIR)
API = "hn_public_api_docs_placeholder_keys"


@pytest.fixture(scope="module")
def docs():
    return load_documents(str(DATA), splits=REVIEW_SPLITS)


@pytest.fixture(scope="module")
def mpkg():
    return load_package(DATA, METADATA)


@pytest.fixture(scope="module")
def cpkg():
    return load_package(DATA, CONTENT)


def _sheet(pkg, reviewer="rev-1", by_family=None, override=None):
    by_family, override = by_family or {}, override or {}
    orig = {r["sample_id"]: r for r in csv.DictReader(io.StringIO(pkg["sheet_text"]))}
    rows = []
    for k in pkg["key"]:
        vals = {
            "human_level": k["gold_level"], "human_categories": k["gold_categories"] or "NONE",
            "human_confidence": "high", "human_rationale": "Follows the definitions.",
            "human_taxonomy_ambiguity": "no", "human_alternative_levels": "",
            "human_insufficient_information": "no", "reviewer_id": reviewer, "review_date": "2026-09-21",
        }  # fmt: skip
        vals.update(by_family.get(k["family_id"], {}))
        vals.update(override.get(k["sample_id"], {}))
        rows.append({**orig[k["sample_id"]], **vals})
    return to_csv(rows, pkg["variant"].sheet_columns)


# ---- what the variant is -------------------------------------------------------------------------------------
def test_the_variants_are_registered_and_kept_apart():
    assert set(VARIANTS) == {"content", "metadata"}
    assert CONTENT.shows_metadata is False and METADATA.shows_metadata is True
    for f in ("sheet_file", "packet_file", "key_file", "manifest_file"):
        assert getattr(CONTENT, f) != getattr(METADATA, f)
    assert Path(METADATA.blind_dir) not in Path(METADATA.key_dir).parents
    assert CONTENT.default_results_dir != METADATA.default_results_dir
    assert (
        "source_metadata" in METADATA.input_columns
        and "source_metadata" not in CONTENT.input_columns
    )


def test_same_items_different_order(docs):
    disputed = disputed_families(str(DATA))
    a = select_items(docs, disputed, order_seed=CONTENT.order_seed)
    b = select_items(docs, disputed, order_seed=METADATA.order_seed)
    assert {(i["doc"].doc_id, i["role"]) for i in a} == {(i["doc"].doc_id, i["role"]) for i in b}
    assert [i["doc"].doc_id for i in a] != [i["doc"].doc_id for i in b]
    assert [i["order"] for i in b] == list(range(1, len(b) + 1))


def test_the_content_variant_selection_is_unchanged(docs):
    disputed = disputed_families(str(DATA))
    assert [i["doc"].doc_id for i in select_items(docs, disputed)] == [
        i["doc"].doc_id for i in select_items(docs, disputed, order_seed=CONTENT.order_seed)
    ]


# ---- what the reviewer sees ------------------------------------------------------------------------------------
def test_the_sheet_shows_metadata_and_nothing_else_new(docs):
    items = select_items(docs, disputed_families(str(DATA)), order_seed=METADATA.order_seed)
    rows = sheet_rows(items, METADATA)
    assert list(rows[0]) == METADATA.sheet_columns
    assert all(r[c] == "" for r in rows for c in RESPONSE_COLUMNS)
    api = [r for r in rows if "Public developer portal" in r["source_metadata"]]
    assert len(api) == 5
    d = next(i["doc"] for i in items if i["doc"].family_id == "hn_business_case_study")
    assert source_metadata(d) == "source_system=Public teaching materials"


def test_metadata_is_formatted_sorted_and_empty_is_empty(docs):
    d = next(d for d in docs if len(d.metadata) == 2)
    assert source_metadata(d) == "; ".join(f"{k}={v}" for k, v in sorted(d.metadata.items()))
    assert source_metadata(d.model_copy(update={"metadata": {}})) == ""


def test_the_committed_variant_files_carry_metadata_but_no_answer_information(docs, bundle):
    files = {p: (DATA / p).read_text() for p in (METADATA.sheet_file, METADATA.packet_file)}
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
        assert "source_system=Public developer portal" in text
    assert "(with source metadata)" in files[METADATA.packet_file]
    assert "source metadata recorded with it" in files[METADATA.packet_file]
    # the content-only files still do not show it
    for p in (CONTENT.sheet_file, CONTENT.packet_file):
        assert "source_system" not in (DATA / p).read_text()


def test_the_variant_generator_refuses_a_leak(bundle, monkeypatch):
    import evals.classification.blind_review as br

    leaking = [dict.fromkeys(METADATA.sheet_columns, f"source_system=Pharmacy system {API}")]
    monkeypatch.setattr(br, "sheet_rows", lambda items, variant=None: leaking)
    with pytest.raises(ValueError, match="leaks answer information"):
        build_package(bundle, {}, str(DATA), METADATA)


def test_the_committed_variant_package_is_in_sync_and_pinned(bundle):
    files, manifest = build_package(
        bundle, {"commit": "x", "branch": "b", "dirty": False}, str(DATA), METADATA
    )
    for rel, text in files.items():
        assert (DATA / rel).read_text() == text, (
            f"{rel} is stale; run `review blind-package --variant metadata`"
        )
    committed = json.loads((DATA / METADATA.manifest_file).read_text())
    for k in ("variant", "shows_metadata", "order_seed", "n_items", "n_disputed", "n_controls", "dataset_sha256",
              "adjudication_artifacts_sha256", "reviewer_files_sha256", "key_sha256", "leak_check"):  # fmt: skip
        assert committed[k] == manifest[k], k
    assert committed["locked_test_split_read"] is False and committed["labels_changed"] is False
    assert (
        committed["dataset_sha256"]
        == json.loads((DATA / "manifest.json").read_text())["dataset_sha256"]
    )
    only_explicit_locked_runs(DATA)


def test_both_keys_carry_the_same_gold_for_the_same_samples(mpkg, cpkg):
    def strip(pkg):
        return {
            k["sample_id"]: {c: v for c, v in k.items() if c != "review_order"} for k in pkg["key"]
        }

    assert strip(mpkg) == strip(cpkg)


# ---- checking returned sheets ------------------------------------------------------------------------------------
def test_a_well_formed_variant_sheet_passes_and_wrong_variant_or_edited_metadata_fails(
    mpkg, cpkg, bundle
):
    good = _sheet(mpkg)
    assert check_completed(good, mpkg["sheet_text"], bundle, METADATA) == []
    # a content-only sheet is not a metadata-variant sheet, and vice versa
    assert check_completed(_sheet(cpkg), mpkg["sheet_text"], bundle, METADATA)[0].startswith(
        "columns differ"
    )
    assert check_completed(good, cpkg["sheet_text"], bundle, CONTENT)[0].startswith(
        "columns differ"
    )
    rows = list(csv.DictReader(io.StringIO(good)))
    rows[0]["source_metadata"] += "; edited=yes"
    errs = check_completed(
        to_csv(rows, METADATA.sheet_columns), mpkg["sheet_text"], bundle, METADATA
    )
    assert any("input text was edited" in e for e in errs)


def test_the_variant_package_integrity_is_verified(tmp_path):
    shutil.copytree(DATA / "review", tmp_path / "review")
    with (tmp_path / METADATA.key_file).open("a") as f:
        f.write("\n")
    with pytest.raises(PackageError, match="blind key differs"):
        load_package(tmp_path, METADATA)
    load_package(DATA, CONTENT)  # the content package is unaffected


# ---- comparing -----------------------------------------------------------------------------------------------
def test_the_variant_comparison_is_labelled_and_tagged(mpkg, bundle, tmp_path):
    s = tmp_path / "m.csv"
    s.write_text(_sheet(mpkg, "meta-rev"))
    report, per_sample = run([s], bundle, DATA, METADATA)
    assert report.startswith("# Blind review (source metadata shown)")
    assert "never receive metadata" in report and "information gap" in report
    lines = list(csv.DictReader(io.StringIO(per_sample)))
    assert len(lines) == 33 and {r["variant"] for r in lines} == {"metadata"}
    assert "review/blind_metadata/blind_review_metadata_sheet.csv" in report


def test_the_content_report_is_unchanged_in_kind(cpkg, bundle, tmp_path):
    s = tmp_path / "c.csv"
    s.write_text(_sheet(cpkg, "content-rev"))
    report, per_sample = run([s], bundle, DATA)
    assert (
        report.startswith("# Blind review: gold vs human") and "source metadata shown" not in report
    )
    assert {r["variant"] for r in csv.DictReader(io.StringIO(per_sample))} == {"content"}


def test_paired_effect_of_metadata_by_the_same_reviewer(cpkg, mpkg, bundle, tmp_path):
    c = tmp_path / "c.csv"
    c.write_text(_sheet(cpkg, "r1", by_family={API: {"human_level": "INTERNAL"}}))
    m = tmp_path / "m.csv"
    m.write_text(_sheet(mpkg, "r1"))  # with metadata the reviewer follows the gold (PUBLIC)
    report, _ = run([m], bundle, DATA, METADATA, [c])
    assert "## Effect of showing metadata: `r1` (content only) vs `r1` (metadata shown)" in report
    assert "The same reviewer" in report and "anchored on the first" in report
    row = next(
        x
        for x in report.splitlines()
        if x.startswith(f"| `{API}`")
        and "content only" not in x
        and x.count("5/5") >= 1
        and "INTERNAL" in x
    )
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[5] == "5/5" and cells[6] == "0/5"  # level changed 5/5, categories changed 0/5
    assert (
        cells[7] == "0/5" and cells[8] == "5/5"
    )  # level = gold: content-only 0/5, metadata-shown 5/5
    assert "Controls (SMALL_SAMPLE): level changed 0/12" in report


def test_paired_effect_with_different_reviewers_is_caveated(cpkg, mpkg, bundle, tmp_path):
    c = tmp_path / "c.csv"
    c.write_text(_sheet(cpkg, "alice"))
    m = tmp_path / "m.csv"
    m.write_text(_sheet(mpkg, "bob"))
    report, _ = run([m], bundle, DATA, METADATA, [c])
    assert "Different reviewers" in report and "mixes the effect of metadata" in report
    assert "No document's label changed when the metadata was shown." in report


def test_pairing_is_refused_for_the_content_variant_and_duplicate_ids(cpkg, mpkg, bundle, tmp_path):
    c = tmp_path / "c.csv"
    c.write_text(_sheet(cpkg, "r1"))
    with pytest.raises(PackageError, match="only with the metadata variant"):
        run([c], bundle, DATA, CONTENT, [c])
    m = tmp_path / "m.csv"
    m.write_text(_sheet(mpkg, "r1"))
    c2 = tmp_path / "c2.csv"
    c2.write_text(_sheet(cpkg, "r1"))
    with pytest.raises(PackageError, match="more than one content-only sheet"):
        run([m], bundle, DATA, METADATA, [c, c2])


def test_a_content_sheet_cannot_be_compared_as_a_metadata_sheet(cpkg, bundle, tmp_path):
    c = tmp_path / "c.csv"
    c.write_text(_sheet(cpkg, "r1"))
    with pytest.raises(PackageError, match="not well-formed"):
        run([c], bundle, DATA, METADATA)


# ---- CLI ----------------------------------------------------------------------------------------------------------
def test_the_cli_checks_and_compares_the_variant(cpkg, mpkg, tmp_path, capsys):
    m = tmp_path / "m.csv"
    m.write_text(_sheet(mpkg, "r1"))
    c = tmp_path / "c.csv"
    c.write_text(_sheet(cpkg, "r1"))
    assert main(["review", "blind-check", "--variant", "metadata", "--sheet", str(m)]) == 0
    assert main(["review", "blind-check", "--variant", "metadata", "--sheet", str(c)]) == 1
    capsys.readouterr()
    out = tmp_path / "out"
    before = {p: p.read_bytes() for p in DATA.glob("review/**/*") if p.is_file()}
    args = [
        "review",
        "blind-compare",
        "--variant",
        "metadata",
        "--sheet",
        str(m),
        "--content-sheet",
        str(c),
        "--out-dir",
        str(out),
    ]
    assert main(args) == 0
    assert "Effect of showing metadata" in (out / "blind_review_comparison.md").read_text()
    assert {p: p.read_bytes() for p in DATA.glob("review/**/*") if p.is_file()} == before


def test_the_metadata_comparison_never_touches_the_locked_test_split(mpkg, bundle, tmp_path):
    before = Path(DATA, "locked_test_access.jsonl").read_bytes()
    s = tmp_path / "m.csv"
    s.write_text(_sheet(mpkg))
    run([s], bundle, DATA, METADATA)
    assert Path(DATA, "locked_test_access.jsonl").read_bytes() == before
    rows = compare(mpkg, read_reviewer(s.read_text(), mpkg, bundle))
    assert len(rows) == 33
