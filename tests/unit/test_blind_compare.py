"""The blind-review comparison: gold vs human vs model predictions. Read-only, verified inputs, counts only."""

from __future__ import annotations

import csv
import io
import shutil
from pathlib import Path

import pytest

import evals.classification.blind_compare as bc
from app.classification.cli import main
from evals.classification.blind_compare import (
    PackageError,
    cohen_kappa,
    compare,
    inter_reviewer,
    load_package,
    parse_pred,
    read_reviewer,
    run,
    triple_pattern,
)
from evals.classification.blind_review import SHEET_COLUMNS, to_csv
from evals.classification.dataset.build import DEFAULT_DATA_DIR

DATA = Path(DEFAULT_DATA_DIR)
API = "hn_public_api_docs_placeholder_keys"
PHI = "phi_prescription_record"


@pytest.fixture(scope="module")
def package():
    return load_package(DATA)


def _sheet(package, reviewer="rev-1", override=None, by_family=None):
    """A completed sheet in which the human copies the synthetic gold, then `override`s per sample/family."""
    override, by_family = override or {}, by_family or {}
    orig = {r["sample_id"]: r for r in csv.DictReader(io.StringIO(package["sheet_text"]))}
    rows = []
    for k in package["key"]:
        cats = k["gold_categories"] or "NONE"
        vals = {
            "human_level": k["gold_level"], "human_categories": cats, "human_confidence": "high",
            "human_rationale": "Follows the definitions.", "human_taxonomy_ambiguity": "no",
            "human_alternative_levels": "", "human_insufficient_information": "no",
            "reviewer_id": reviewer, "review_date": "2026-09-21",
        }  # fmt: skip
        vals.update(by_family.get(k["family_id"], {}))
        vals.update(override.get(k["sample_id"], {}))
        rows.append({**orig[k["sample_id"]], **vals})
    return to_csv(rows, SHEET_COLUMNS)


def _by_id(rows):
    return {r["sample_id"]: r for r in rows}


def _ids(package, family):
    return [k["sample_id"] for k in package["key"] if k["family_id"] == family]


# ---- parsing predictions -------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        (
            "HIGHLY_CONFIDENTIAL | PHI;PII conf=high",
            ("label", ("HIGHLY_CONFIDENTIAL", ("PHI", "PII"))),
        ),
        ("PII;PHI".join(["INTERNAL | ", " conf=0.78"]), ("label", ("INTERNAL", ("PHI", "PII")))),
        ("INTERNAL | - conf=0.78", ("label", ("INTERNAL", ()))),
        ("PUBLIC | -", ("label", ("PUBLIC", ()))),
        ("INTERNAL | - [abstained: default level, not a finding]", ("abstained", None)),
        ("not available (no recorded run for this split)", ("unavailable", None)),
        ("NO LABEL (error)", ("unavailable", None)),
    ],
)
def test_prediction_cells_are_parsed_and_abstentions_are_not_labels(cell, expected):
    assert parse_pred(cell) == expected


def test_triple_patterns_cover_every_case():
    assert triple_pattern("a", "a", "a") == "all agree"
    assert triple_pattern("a", "a", "b") == "gold=human≠pred"
    assert triple_pattern("a", "b", "b") == "human=pred≠gold"
    assert triple_pattern("a", "b", "a") == "gold=pred≠human"
    assert triple_pattern("a", "b", "c") == "all differ"


def test_cohen_kappa_known_values():
    assert cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    assert cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == 0.0
    assert cohen_kappa(["a", "a"], ["a", "a"]) is None  # chance agreement is 1: undefined
    assert cohen_kappa([], []) is None


# ---- the package ---------------------------------------------------------------------------------------------
def test_the_package_loads_and_covers_every_item(package):
    assert len(package["key"]) == 33
    assert {k["role"] for k in package["key"]} == {"disputed", "control"}


def test_a_changed_key_or_adjudication_artifact_is_refused(tmp_path):
    shutil.copytree(DATA / "review", tmp_path / "review")
    (tmp_path / "review/blind_key/blind_review_key.csv").write_text("tampered\n")
    with pytest.raises(PackageError, match="blind key differs"):
        load_package(tmp_path)
    shutil.copy(DATA / "review/blind_key/blind_review_key.csv", tmp_path / "review/blind_key")
    with (tmp_path / "review/adjudication_sheet.csv").open("a") as f:
        f.write("\n")
    with pytest.raises(PackageError, match="adjudication_sheet.csv changed"):
        load_package(tmp_path)


def test_a_malformed_or_ambiguous_sheet_is_refused(package, bundle):
    with pytest.raises(PackageError, match="not well-formed"):
        read_reviewer(
            _sheet(package, override={_ids(package, API)[0]: {"human_level": "SECRET"}}),
            package,
            bundle,
        )
    rows = list(csv.DictReader(io.StringIO(_sheet(package))))
    rows[0]["reviewer_id"] = "someone-else"
    with pytest.raises(PackageError, match="exactly one reviewer_id"):
        read_reviewer(to_csv(rows, SHEET_COLUMNS), package, bundle)


# ---- comparing -----------------------------------------------------------------------------------------------
def test_a_human_who_copies_the_gold_agrees_everywhere(package, bundle):
    rows = compare(package, read_reviewer(_sheet(package), package, bundle))
    assert len(rows) == 33
    assert all(
        r["level_human_eq_gold"] and r["cats_human_eq_gold"] and r["lenient_level_match"]
        for r in rows
    )
    for r in rows:
        for a in bc.APPROACHES:
            p = r["preds"][a]
            if p["status"] == "label":
                # human == gold, so the pattern can only be all-agree or gold=human≠pred
                assert p["level_pattern"] in ("all agree", "gold=human≠pred")


def test_a_disagreeing_human_is_counted_on_the_right_family(package, bundle):
    ids = _ids(package, API)
    rows = _by_id(compare(package, read_reviewer(
        _sheet(package, by_family={API: {"human_level": "INTERNAL", "human_taxonomy_ambiguity": "yes", "human_alternative_levels": "PUBLIC"}}),
        package, bundle)))  # fmt: skip
    for sid in ids:
        r = rows[sid]
        assert r["gold"][0] == "PUBLIC" and r["human"][0] == "INTERNAL"
        assert r["level_human_eq_gold"] is False and r["cats_human_eq_gold"] is True
        assert r["lenient_level_match"] is True  # PUBLIC is in the human's own alternatives
        assert (
            r["preds"]["hybrid"]["level_pattern"] == "human=pred≠gold"
        )  # hybrid says INTERNAL on all 5
    other = [r for sid, r in rows.items() if sid not in ids]
    assert all(r["level_human_eq_gold"] for r in other)


def test_lenient_match_uses_the_gold_alternatives_too(package, bundle):
    # the draft-customer-story gold is CONFIDENTIAL with alternatives PUBLIC and INTERNAL (decision A28)
    fam = "amb_customer_case_study_draft"
    rows = compare(
        package,
        read_reviewer(_sheet(package, by_family={fam: {"human_level": "PUBLIC"}}), package, bundle),
    )
    got = [r for r in rows if r["family_id"] == fam]
    assert got and all(not r["level_human_eq_gold"] and r["lenient_level_match"] for r in got)
    rows = compare(
        package,
        read_reviewer(
            _sheet(package, by_family={fam: {"human_level": "INTERNAL"}}), package, bundle
        ),
    )
    assert all(r["lenient_level_match"] for r in rows if r["family_id"] == fam)  # A28
    rows = compare(
        package,
        read_reviewer(
            _sheet(package, by_family={fam: {"human_level": "HIGHLY_CONFIDENTIAL"}}),
            package,
            bundle,
        ),
    )
    assert all(not r["lenient_level_match"] for r in rows if r["family_id"] == fam)


def test_categories_are_compared_as_sets_and_none_means_empty(package, bundle):
    ids = _ids(package, PHI)
    rows = _by_id(
        compare(
            package,
            read_reviewer(
                _sheet(package, by_family={PHI: {"human_categories": "PII;PHI"}}), package, bundle
            ),
        )
    )
    assert all(
        rows[s]["human"][1] == ("PHI", "PII") and rows[s]["cats_human_eq_gold"] is False
        for s in ids
    )
    assert all(rows[s]["preds"]["hybrid"]["cats_pattern"] == "gold=pred≠human" for s in ids)
    control = next(k for k in package["key"] if k["role"] == "control" and not k["gold_categories"])
    r = _by_id(compare(package, read_reviewer(_sheet(package), package, bundle)))[
        control["sample_id"]
    ]
    assert r["human"][1] == () and r["cats_human_eq_gold"] is True


def test_insufficient_information_without_a_level_is_kept_out_of_agreement(package, bundle):
    sid = _ids(package, API)[0]
    rows = _by_id(compare(package, read_reviewer(
        _sheet(package, override={sid: {"human_level": "", "human_insufficient_information": "yes"}}), package, bundle)))  # fmt: skip
    r = rows[sid]
    assert (
        r["human"] is None and r["level_human_eq_gold"] is None and r["human_insufficient"] is True
    )
    assert all("level_pattern" not in p for p in r["preds"].values())


def test_unavailable_and_abstained_predictions_are_never_compared(package, bundle):
    rows = compare(package, read_reviewer(_sheet(package), package, bundle))
    statuses = {p["status"] for r in rows for p in r["preds"].values()}
    assert {"label", "abstained", "unavailable"} <= statuses
    for r in rows:
        for p in r["preds"].values():
            assert ("level_pattern" in p) == (p["status"] == "label")
    calibration = [r for r in rows if r["split"] == "calibration"]
    assert calibration and all(r["preds"]["hybrid"]["status"] == "unavailable" for r in calibration)


# ---- two reviewers -------------------------------------------------------------------------------------------
def test_inter_reviewer_agreement_and_kappa(package, bundle):
    a = compare(package, read_reviewer(_sheet(package, "a"), package, bundle))
    same = compare(package, read_reviewer(_sheet(package, "b"), package, bundle))
    r = inter_reviewer(a, same)
    assert (
        r["n"] == 33
        and r["level_agree"] == 33
        and r["cats_agree"] == 33
        and r["disagreements"] == []
    )
    ids = _ids(package, API)
    diff = compare(
        package,
        read_reviewer(
            _sheet(package, "c", by_family={API: {"human_level": "INTERNAL"}}), package, bundle
        ),
    )
    r = inter_reviewer(a, diff)
    assert r["level_agree"] == 33 - len(ids) and {d[0] for d in r["disagreements"]} == set(ids)
    assert r["kappa_level"] is not None and r["kappa_level"] < 1.0


def test_the_same_reviewer_id_twice_is_refused(package, bundle, tmp_path):
    s = tmp_path / "s.csv"
    s.write_text(_sheet(package, "same"))
    t = tmp_path / "t.csv"
    t.write_text(_sheet(package, "same"))
    with pytest.raises(PackageError, match="more than one sheet"):
        run([s, t], bundle, DATA)


# ---- the report ----------------------------------------------------------------------------------------------
def test_the_report_states_its_limits_and_has_no_headline_score(package, bundle, tmp_path):
    s = tmp_path / "s.csv"
    s.write_text(
        _sheet(
            package,
            "rev-1",
            by_family={API: {"human_level": "INTERNAL"}, PHI: {"human_categories": "PHI;PII"}},
        )
    )
    report, per_sample = run([s], bundle, DATA)
    assert "pending human gold-label review" in report
    assert "locked test split was not read" in report
    assert "SMALL_SAMPLE" in report and "4 independent decisions" in report
    assert (
        "no combined headline score" in report.lower()
        or "There is no combined headline score" in report
    )
    for fam in (API, PHI, "hn_business_case_study", "amb_customer_case_study_draft"):
        assert f"`{fam}`" in report
    assert "documents where the human added PII: 5 of 5" in report
    assert "human=pred≠gold" in report
    lines = per_sample.strip().splitlines()
    assert len(lines) == 34
    header = lines[0].split(",")
    assert {
        "reviewer_id",
        "sample_id",
        "gold_level",
        "human_level",
        "pred_hybrid",
        "level_pattern_hybrid",
    } <= set(header)
    assert "macro" not in report.lower() and "f-score" not in report.lower()


def test_the_report_lists_disagreeing_controls(package, bundle, tmp_path):
    ctrl = next(
        k for k in package["key"] if k["role"] == "control" and k["gold_level"] == "INTERNAL"
    )
    s = tmp_path / "s.csv"
    s.write_text(_sheet(package, override={ctrl["sample_id"]: {"human_level": "CONFIDENTIAL"}}))
    report, _ = run([s], bundle, DATA)
    assert ctrl["sample_id"] in report and "Every labelled control matches" not in report
    ok = tmp_path / "ok.csv"
    ok.write_text(_sheet(package))
    assert "Every labelled control matches" in run([ok], bundle, DATA)[0]


def test_the_report_flags_an_inconsistent_family(package, bundle, tmp_path):
    ids = _ids(package, API)
    s = tmp_path / "s.csv"
    s.write_text(_sheet(package, override={ids[0]: {"human_level": "INTERNAL"}}))
    report, _ = run([s], bundle, DATA)
    row = next(line for line in report.splitlines() if line.startswith(f"| `{API}`"))
    assert row.rstrip().endswith("NO |")


# ---- read-only, no locked test ---------------------------------------------------------------------------------
def test_the_comparison_never_touches_the_dataset_or_the_locked_test_split(
    package, bundle, tmp_path
):
    assert not hasattr(bc, "load_documents")
    src = Path(bc.__file__).read_text()
    assert "load_documents" not in src and "locked_test_authorization" not in src
    before = Path(DATA, "locked_test_access.jsonl").read_bytes()
    s = tmp_path / "s.csv"
    s.write_text(_sheet(package))
    run([s], bundle, DATA)
    assert Path(DATA, "locked_test_access.jsonl").read_bytes() == before


def test_the_cli_writes_only_to_the_output_directory(package, tmp_path, capsys):
    s = tmp_path / "s.csv"
    s.write_text(_sheet(package))
    out = tmp_path / "out"
    tracked = {p: p.read_bytes() for p in DATA.glob("review/**/*") if p.is_file()}
    assert main(["review", "blind-compare", "--sheet", str(s), "--out-dir", str(out)]) == 0
    assert {p.name for p in out.iterdir()} == {
        "blind_review_comparison.md",
        "blind_review_comparison.csv",
    }
    assert {
        p: p.read_bytes() for p in DATA.glob("review/**/*") if p.is_file()
    } == tracked  # nothing else touched


def test_the_cli_reports_a_bad_sheet(package, tmp_path, capsys):
    s = tmp_path / "s.csv"
    s.write_text(_sheet(package, override={_ids(package, API)[0]: {"human_level": "SECRET"}}))
    assert (
        main(["review", "blind-compare", "--sheet", str(s), "--out-dir", str(tmp_path / "o")]) == 1
    )
    assert "ERROR" in capsys.readouterr().out
    assert not (tmp_path / "o").exists()


# ---- survivors of mutation testing, closed -------------------------------------------------------------------
def test_a_changed_reviewer_sheet_is_refused(tmp_path):
    shutil.copytree(DATA / "review", tmp_path / "review")
    with (tmp_path / "review/blind/blind_review_sheet.csv").open("a") as f:
        f.write("\n")
    with pytest.raises(PackageError, match="reviewer sheet differs"):
        load_package(tmp_path)


def test_missing_executed_predictions_are_refused(tmp_path):
    import hashlib
    import json

    shutil.copytree(DATA / "review", tmp_path / "review")
    adj = tmp_path / "review/adjudication_sheet.csv"
    key = list(
        csv.DictReader(
            io.StringIO((tmp_path / "review/blind_key/blind_review_key.csv").read_text())
        )
    )
    csv.field_size_limit(10**9)
    rows = list(csv.DictReader(io.StringIO(adj.read_text())))
    rows = [r for r in rows if r["sample_id"] != key[0]["sample_id"]]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    adj.write_text(buf.getvalue())
    mp = tmp_path / "review/blind_key/blind_review_manifest.json"
    m = json.loads(mp.read_text())
    m["adjudication_artifacts_sha256"]["review/adjudication_sheet.csv"] = hashlib.sha256(
        adj.read_bytes()
    ).hexdigest()
    mp.write_text(json.dumps(m))
    with pytest.raises(PackageError, match="no executed predictions"):
        load_package(tmp_path)


def test_inter_reviewer_counts_only_documents_both_labelled(package, bundle):
    sid = _ids(package, API)[0]
    a = compare(
        package,
        read_reviewer(
            _sheet(
                package,
                "a",
                override={sid: {"human_level": "", "human_insufficient_information": "yes"}},
            ),
            package,
            bundle,
        ),
    )
    b = compare(package, read_reviewer(_sheet(package, "b"), package, bundle))
    assert inter_reviewer(a, b)["n"] == 32 and inter_reviewer(b, a)["n"] == 32


def test_small_sample_is_flagged_on_every_section_heading(package, bundle, tmp_path):
    s = tmp_path / "s.csv"
    s.write_text(_sheet(package))
    report, _ = run([s], bundle, DATA)
    headings = [
        line
        for line in report.splitlines()
        if line.startswith("### ")
        and "Evidence" in line
        or "human vs synthetic gold" in line
        or "Gold vs human vs prediction" in line
    ]
    assert len(headings) >= 4 and all("(SMALL_SAMPLE)" in h for h in headings)


def test_a_control_with_only_a_category_difference_is_listed(package, bundle, tmp_path):
    ctrl = next(k for k in package["key"] if k["role"] == "control" and k["gold_categories"])
    s = tmp_path / "s.csv"
    s.write_text(_sheet(package, override={ctrl["sample_id"]: {"human_categories": "NONE"}}))
    report, _ = run([s], bundle, DATA)
    assert ctrl["sample_id"] in report and "Every labelled control matches" not in report


def test_every_markdown_table_row_has_the_same_number_of_cells_as_its_header(
    package, bundle, tmp_path
):
    s = tmp_path / "s.csv"
    s.write_text(_sheet(package, by_family={API: {"human_level": "INTERNAL"}}))
    report, _ = run([s], bundle, DATA)
    width = None
    for line in report.splitlines():
        if line.startswith("|"):
            width = line.count("|") if width is None else width
            assert line.count("|") == width, line
        else:
            width = None


# ---- AI-reviewer labelling -------------------------------------------------------------------------------------
def test_an_ai_review_is_labelled_in_the_report_and_the_csv(package, bundle, tmp_path):
    s = tmp_path / "ai.csv"
    s.write_text(_sheet(package, reviewer="some-model"), encoding="utf-8")
    report, per_sample = run([s], bundle, DATA, reviewer_kind="ai")
    assert "AI REVIEW: NOT HUMAN VALIDATION" in report and report.startswith("# AI review")
    assert "decision A20" in report and "must not be used to apply label" in report
    assert "The human applied" not in report and "One human is one opinion" not in report
    assert "One AI model is one opinion" in report
    rows = list(csv.DictReader(io.StringIO(per_sample)))
    assert {r["reviewer_kind"] for r in rows} == {"ai"}


def test_the_default_human_output_is_unchanged_and_carries_no_ai_label(package, bundle, tmp_path):
    s = tmp_path / "h.csv"
    s.write_text(_sheet(package), encoding="utf-8")
    report, per_sample = run([s], bundle, DATA)
    assert "AI REVIEW" not in report and report.startswith("# Blind review: gold vs human")
    assert "reviewer_kind" not in per_sample.splitlines()[0]


def test_an_unknown_reviewer_kind_is_refused(package, bundle, tmp_path):
    s = tmp_path / "x.csv"
    s.write_text(_sheet(package), encoding="utf-8")
    with pytest.raises(PackageError):
        run([s], bundle, DATA, reviewer_kind="robot")


def test_the_ai_cli_writes_to_a_separate_default_directory(package, tmp_path, capsys):
    data = tmp_path / "data"
    shutil.copytree(DATA, data)
    s = tmp_path / "ai.csv"
    s.write_text(_sheet(package, reviewer="some-model"), encoding="utf-8")
    assert main(["review", "blind-compare", "--sheet", str(s), "--data-dir", str(data),
                 "--reviewer-kind", "ai"]) == 0  # fmt: skip
    assert (data / "review/blind_results_ai/blind_review_comparison.md").exists()
    human = {p.name: p.read_bytes() for p in (data / "review/blind_results").glob("*")}
    assert human == {p.name: p.read_bytes() for p in (DATA / "review/blind_results").glob("*")}


def test_a_provenance_note_is_printed_verbatim_and_absent_by_default(package, bundle, tmp_path):
    s = tmp_path / "h.csv"
    s.write_text(_sheet(package), encoding="utf-8")
    plain, _ = run([s], bundle, DATA)
    noted, _ = run([s], bundle, DATA, note="Sheet identical to another sheet on all 33 rows.")
    assert "Provenance note" not in plain
    assert "Provenance note (recorded verbatim" in noted
    assert "Sheet identical to another sheet on all 33 rows." in noted
