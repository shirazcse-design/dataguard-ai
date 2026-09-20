"""The gold-review preparation: read-only, no locked test split, every disagreement adjudicated."""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

import pytest

from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents
from evals.classification.evaluate import git_info
from evals.classification.gold_review import (
    COLUMNS,
    DECISIONS_FILE,
    REVIEW_SPLITS,
    SHEET_FILE,
    build_review,
    build_rows,
    collect_predictions,
    load_decisions,
    rows_to_csv,
    scenario_changes,
    with_gold,
)
from tests.helpers import mkdoc

ROOT = Path(__file__).resolve().parents[2]
DATA = str(DEFAULT_DATA_DIR)
REQUIRED = {"sample_id", "original_gold_level", "proposed_human_gold_level", "disagreement_type", "reviewer_rationale",
            "taxonomy_ambiguity_flag", "recommended_action", "pred_rules", "pred_ml", "pred_llm_small", "pred_llm_mid",
            "pred_llm_large", "pred_hybrid"}  # fmt: skip
TYPES = {
    "gold_label_error",
    "taxonomy_ambiguity",
    "genuine_model_error",
    "insufficient_information",
    "none",
}


@pytest.fixture(scope="module")
def docs():
    return load_documents(DATA, splits=REVIEW_SPLITS)


@pytest.fixture(scope="module")
def decisions():
    return load_decisions(DATA)[0]


@pytest.fixture(scope="module")
def built(bundle, docs, decisions):
    preds = collect_predictions(bundle, docs, DATA)
    return preds, build_rows(bundle, docs, preds, decisions)


# ---- the locked test split is never involved ----------------------------------------------------------
def test_the_review_only_ever_loads_development_splits(docs):
    assert "test" not in REVIEW_SPLITS and {d.split for d in docs} == {
        "train",
        "calibration",
        "dev",
    }


def test_prediction_collection_refuses_a_locked_test_document(bundle):
    with pytest.raises(AssertionError, match="locked test split"):
        collect_predictions(bundle, [mkdoc("t1", split="test")], DATA)


def test_no_sheet_row_belongs_to_the_locked_split(built):
    _, rows = built
    assert {r["split"] for r in rows} <= {"train", "calibration", "dev"} and rows


# ---- the decisions file ---------------------------------------------------------------------------
def test_decisions_are_valid_against_the_taxonomy_and_the_floors(bundle, decisions):
    pol = bundle.policy
    for fam, d in decisions.families.items():
        assert d.proposed_gold_level in pol.level_ids, fam
        assert set(d.proposed_gold_categories) <= set(pol.category_ids), fam
        assert not pol.violates_floor(d.proposed_gold_level, d.proposed_gold_categories), fam
        assert d.reviewer_rationale.strip() and d.recommended_action.strip(), fam


def test_a_proposed_label_change_really_differs_from_the_current_gold(docs, decisions):
    for fam, d in decisions.families.items():
        gold = {
            (x.gold_level, tuple(sorted(x.gold_categories))) for x in docs if x.family_id == fam
        }
        proposed = (d.proposed_gold_level, tuple(sorted(d.proposed_gold_categories)))
        assert (proposed not in gold) == d.label_change_proposed, fam


def test_only_the_named_family_proposes_a_label_change(decisions):
    assert [f for f, d in decisions.families.items() if d.label_change_proposed] == [
        "hn_public_api_docs_placeholder_keys"
    ]
    d = decisions.families["hn_public_api_docs_placeholder_keys"]
    assert (
        d.proposed_gold_level,
        d.proposed_ambiguity_flag,
        d.proposed_acceptable_alternative_levels,
    ) == ("INTERNAL", True, ["PUBLIC"])
    assert d.human_decision_required and "HUMAN DECISION REQUIRED" in d.recommended_action


# ---- the sheet -------------------------------------------------------------------------------------
def test_the_sheet_has_the_required_columns_and_every_row_is_typed(built):
    _, rows = built
    assert set(COLUMNS) >= REQUIRED
    for r in rows:
        assert (
            r["disagreement_type"] in TYPES
            and r["reviewer_rationale"].strip()
            and r["recommended_action"].strip()
        )
        for col in COLUMNS:
            assert col in r


def test_the_sheet_covers_every_dev_document_and_only_adjudicated_families_elsewhere(
    docs, built, decisions
):
    _, rows = built
    ids = {r["sample_id"] for r in rows}
    assert {d.doc_id for d in docs if d.split == "dev"} <= ids
    others = {r["family_id"] for r in rows if r["split"] != "dev"}
    assert others == {
        f for f in decisions.families if any(d.family_id == f and d.split != "dev" for d in docs)
    }
    assert len(rows) == 127


def test_original_gold_in_the_sheet_equals_the_dataset_gold_for_every_row(docs, built):
    by_id = {d.doc_id: d for d in docs}
    for r in built[1]:
        d = by_id[r["sample_id"]]
        assert r["original_gold_level"] == d.gold_level
        assert r["original_gold_categories"] == ";".join(sorted(d.gold_categories))
        assert r["content"] == d.content and r["original_ambiguity_flag"] == d.ambiguity_flag


def test_every_non_ml_disagreement_carries_an_adjudication(built):
    for r in built[1]:
        bad = set(r["approaches_disagreeing_with_gold"].split(";")) - {"", "ml"}
        if bad:
            assert r["disagreement_type"] in TYPES - {"none"} and r["priority"] in (
                "P1",
                "P2",
                "P3",
            ), r["sample_id"]


def test_a_disagreement_with_no_adjudication_is_an_error_not_a_silent_skip(
    bundle, docs, built, decisions
):
    preds, _ = built
    stripped = decisions.model_copy(
        update={
            "families": {
                k: v for k, v in decisions.families.items() if k != "cred_ci_pipeline_token"
            }
        }
    )
    with pytest.raises(ValueError, match="no adjudication"):
        build_rows(bundle, docs, preds, stripped)


def test_ml_only_disagreements_are_typed_as_model_errors_and_agreement_as_none(built, decisions):
    whole_family = {
        f for f, d in decisions.families.items() if d.applies_to == "all"
    }  # typed by their decision
    for r in built[1]:
        bad = [x for x in r["approaches_disagreeing_with_gold"].split(";") if x]
        if bad == ["ml"] and r["family_id"] not in whole_family:
            assert r["disagreement_type"] == "genuine_model_error" and r["priority"] == "P4"
        if not bad and r["family_id"] not in whole_family:
            assert r["disagreement_type"] == "none"


def test_a_rules_abstention_is_not_a_disagreement(built):
    for r in built[1]:
        if "[abstained" in r["pred_rules"]:
            assert "rules" not in r["approaches_disagreeing_with_gold"].split(";")


def test_the_named_family_rows_carry_every_required_field(built):
    fam = [r for r in built[1] if r["family_id"] == "hn_public_api_docs_placeholder_keys"]
    assert len(fam) == 5
    for r in fam:
        assert (
            r["original_gold_level"],
            r["proposed_human_gold_level"],
            r["disagreement_type"],
        ) == ("PUBLIC", "INTERNAL", "gold_label_error")
        assert (
            r["taxonomy_ambiguity_flag"] is True
            and r["label_change_proposed"] is True
            and r["human_decision_required"] is True
        )
        assert (
            r["proposed_acceptable_alternative_levels"] == "PUBLIC"
            and r["proposed_ambiguity_flag"] is True
        )
        assert "INTERNAL" in r["pred_llm_mid"] and "INTERNAL" in r["pred_hybrid"]
        assert (
            r["llm_rationale_mid"]
            and r["llm_rationale_large"]
            and "PUBLIC" in r["taxonomy_definitions_applicable"]
        )


def test_human_columns_are_left_blank_for_the_human(built):
    for r in built[1]:
        for col in (
            "human_decision",
            "human_final_gold_level",
            "human_final_gold_categories",
            "human_comment",
            "human_reviewer",
            "human_review_date",
        ):
            assert r[col] == ""


def test_the_csv_round_trips_with_the_documented_columns(built):
    text = rows_to_csv(built[1])
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert len(parsed) == 127 and list(parsed[0]) == COLUMNS
    assert {"TRUE", "FALSE"} >= {p["taxonomy_ambiguity_flag"] for p in parsed}


# ---- read-only and hypothetical --------------------------------------------------------------------
def test_counterfactual_labels_are_applied_to_copies_only(docs, decisions):
    changes = scenario_changes(decisions)["S1 apply the proposed label change"]
    modified = with_gold(docs, changes)
    named = "hn_public_api_docs_placeholder_keys"
    assert all(
        d.gold_level == "PUBLIC" for d in docs if d.family_id == named
    )  # originals untouched
    assert all(d.gold_level == "INTERNAL" for d in modified if d.family_id == named)
    assert [d.doc_id for d in modified] == [d.doc_id for d in docs]
    assert set(scenario_changes(decisions)) == {
        "S0 current gold",
        "S1 apply the proposed label change",
        "S2 = S1 + MRN counts as an identifier",
    }
    assert scenario_changes(decisions)["S0 current gold"] == {}


def sha(paths):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def test_building_the_review_changes_no_label_taxonomy_or_config_file(bundle):
    watched = [*Path(DATA, "docs").glob("*.jsonl"), Path(DATA, "manifest.json"), *ROOT.joinpath("config").rglob("*.yaml"),
               *ROOT.joinpath("docs/uc4/schema").glob("*.json"), *ROOT.joinpath("prompts").rglob("*")]  # fmt: skip
    watched = [p for p in watched if p.is_file()]
    before = sha(watched)
    build_review(bundle, git_info(), DATA)  # returns the artifacts; writes nothing itself
    assert sha(watched) == before


def test_the_committed_sheet_is_in_sync_with_the_code_that_generates_it(bundle):
    rows, report, meta = build_review(bundle, git_info(), DATA)
    assert Path(DATA, SHEET_FILE).read_text() == rows_to_csv(rows)
    assert meta["locked_test_split_read"] is False and meta["labels_changed"] is False
    assert Path(DATA, DECISIONS_FILE).exists()


def test_the_report_answers_the_review_questions(bundle):
    _, report, _ = build_review(bundle, git_info(), DATA)
    for needle in ("NOT human validation", "locked test split was not read", "1. likely synthetic gold-label error", "2. taxonomy ambiguity",
                   "3. genuine model error", "Exact proposed changes (NONE APPLIED", "HYPOTHETICAL", "What drives the level macro-F1 lower bound",
                   "primarily by one family", "tuning on dev", "blind"):  # fmt: skip
        assert needle in report, needle
    assert "0.631" in report and "1.000 [1.000, 1.000]" in report


def test_the_load_bearing_numbers_hold(bundle):
    """The conclusion depends on these; if they drift the conclusion must be re-examined."""
    _, report, _ = build_review(bundle, git_info(), DATA)
    assert (
        "| hybrid | 0.870 [0.631, 1.000]"
        in report.replace("S0 current gold | ", "").replace("| S0 current gold ", "")
        or "0.870 [0.631, 1.000]" in report
    )
    assert "All 5 of the hybrid's level errors" in report
