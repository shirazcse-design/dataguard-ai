"""The pre-registered selection rule, the hybrid report and the CLI paths."""

from __future__ import annotations

import json

import pytest

from app.classification.cli import main
from evals.classification.hybrid_report import SANITY_VARIANT, dominates, recommend


def row(level=0.9, cat=0.9, p=0.9, r=0.95, tokens=1000.0, p95=3.0, stages=2, severe=0, fail=0):
    return {"level_f1": level, "cat_f1": cat, "hr_precision": p, "hr_recall": r, "tokens_per_doc": tokens,
            "p95_s": p95, "n_stages": stages, "severe_under": severe, "n_fail": fail}  # fmt: skip


# ---- dominance and the selection rule ------------------------------------------------------------
def test_dominance_treats_small_differences_as_ties_and_needs_a_real_improvement():
    a = row()
    assert not dominates(row(level=0.91), a)  # within 0.02: a tie, not an improvement
    assert dominates(row(level=0.95), a)  # better by more than 0.02, no worse elsewhere
    assert not dominates(row(level=0.95, cat=0.80), a)  # worse on one metric by more than eps
    assert dominates(row(level=0.95, cat=0.89), a)  # within eps on the other
    assert not dominates(a, a)


def test_step_one_filters_on_recall_severe_underclassification_and_missing_predictions():
    rows = {"ok": row(), "lowrec": row(r=0.85), "severe": row(severe=1), "fail": row(fail=2)}
    out = recommend(rows, 0.90)
    assert out["step1"] == ["ok"] and out["chosen"] == "ok"
    assert (
        "recall" in out["eliminated"]["lowrec"]
        and "under-classified" in out["eliminated"]["severe"]
    )
    assert "without a prediction" in out["eliminated"]["fail"]


def test_dominated_variants_are_dropped_and_the_cheapest_survivor_is_chosen():
    rows = {"strong_costly": row(level=0.95, tokens=6000), "strong_cheap": row(level=0.95, tokens=2000),
            "weak_cheaper": row(level=0.70, tokens=500), "tie": row(level=0.94, tokens=2500)}  # fmt: skip
    out = recommend(rows, 0.90)
    assert (
        "weak_cheaper" not in out["front"] and "dominated by" in out["eliminated"]["weak_cheaper"]
    )
    assert out["chosen"] == "strong_cheap"  # ties (within 0.02) are broken by cost


def test_tie_breaks_go_tokens_then_latency_then_fewest_stages_then_order():
    a = {"a": row(tokens=1000, p95=5.0, stages=3), "b": row(tokens=1000, p95=2.0, stages=3)}
    assert recommend(a, 0.9)["chosen"] == "b"
    c = {"a": row(tokens=1000, p95=2.0, stages=3), "b": row(tokens=1000, p95=2.0, stages=2)}
    assert recommend(c, 0.9)["chosen"] == "b"
    tokens_before_latency = {
        "cheap_slow": row(tokens=1000, p95=9.0),
        "dear_fast": row(tokens=2000, p95=1.0),
    }
    assert recommend(tokens_before_latency, 0.9)["chosen"] == "cheap_slow"
    d = {"first": row(), "second": row()}
    assert recommend(d, 0.9)["chosen"] == "first"


def test_the_sanity_variant_is_never_a_candidate_and_no_survivor_gives_no_choice():
    out = recommend({SANITY_VARIANT: row(tokens=1), "x": row(r=0.5)}, 0.90)
    assert (
        out["chosen"] is None
        and SANITY_VARIANT not in out["step1"]
        and SANITY_VARIANT not in out["eliminated"]
    )


def test_recall_is_a_filter_and_higher_recall_earns_no_preference_beyond_it():
    # a precision/recall trade-off: neither dominates, so both stay; recall does not pick the winner
    rows = {
        "recall_heavy": row(r=1.0, p=0.50, tokens=3000),
        "precise": row(r=0.91, p=0.99, tokens=1000),
    }
    out = recommend(rows, 0.90)
    assert set(out["front"]) == {"recall_heavy", "precise"} and out["chosen"] == "precise"
    assert (
        recommend({"below": row(r=0.89, tokens=1)}, 0.90)["chosen"] is None
    )  # but the filter is firm


# ---- CLI: eval run --------------------------------------------------------------------------------
def test_eval_run_hybrid_on_dev_replays_recordings_and_records_routing(tmp_path, capsys):
    rc = main(["eval", "run", "--classifier", "hybrid", "--hybrid-variant", "llm_mid_only", "--split", "dev",
               "--runs-dir", str(tmp_path)])  # fmt: skip
    out = capsys.readouterr().out
    assert rc == 0 and "independent families" in out
    run_dir = next(tmp_path.iterdir())
    recs = [json.loads(x) for x in (run_dir / "predictions.jsonl").read_text().splitlines()]
    assert len(recs) == 107 and all(r["has_prediction"] for r in recs)
    assert {r["stop_reason"] for r in recs} == {"llm:mid_accepted"} and all(
        r["stages_run"] == ["llm:mid"] for r in recs
    )
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    assert (
        manifest["classifier"]["name"] == "hybrid"
        and manifest["classifier"]["params"]["variant"] == "llm_mid_only"
    )


def test_eval_run_hybrid_without_recordings_accounts_for_every_document(tmp_path, capsys):
    rc = main(
        ["eval", "run", "--classifier", "hybrid", "--split", "train", "--runs-dir", str(tmp_path)]
    )
    assert rc == 0
    run_dir = next(tmp_path.iterdir())
    recs = [json.loads(x) for x in (run_dir / "predictions.jsonl").read_text().splitlines()]
    assert len(recs) == 416  # nothing dropped, even though no LLM response was recorded for train
    sufficient = [r for r in recs if r["has_prediction"]]
    assert all(
        r["review_required"] and "LLM_UNAVAILABLE" in r["review_reasons"] for r in sufficient
    )
    assert all(
        r["failure"] == "no_label:review_required:llm_error:replay_miss"
        for r in recs
        if not r["has_prediction"]
    )


def test_eval_run_hybrid_refuses_the_locked_split(tmp_path, capsys):
    rc = main(
        ["eval", "run", "--classifier", "hybrid", "--split", "test", "--runs-dir", str(tmp_path)]
    )
    assert rc == 2 and "locked" in capsys.readouterr().err


def test_unknown_variant_is_a_clear_error(tmp_path):
    with pytest.raises(KeyError, match="unknown hybrid variant"):
        main(
            [
                "eval",
                "run",
                "--classifier",
                "hybrid",
                "--hybrid-variant",
                "nope",
                "--split",
                "dev",
                "--runs-dir",
                str(tmp_path),
            ]
        )


# ---- the report -----------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("h") / "hybrid.md"
    assert main(["hybrid", "report", "--out", str(out)]) == 0
    return out.read_text()


def test_report_states_provenance_and_that_dev_was_used_for_selection(report):
    assert (
        "pending human gold-label review" in report
        and "The locked test split was not read" in report
    )
    assert "Dev was used both to choose the recommended variant and to evaluate it" in report
    assert "independent families" in report and "no provider call was made" in report


def test_report_contains_every_required_section(report):
    for h in ("## Sanity check", "## All approaches and variants", "## Gates", "## Recommended variant (pre-registered selection rule)",
              "## Which stage decided each document", "## Evidence for the thresholds", "### Rules sufficiency", "### ML acceptance by tau",
              "### LLM confidence floor", "## Safety mechanisms exercised", "## Behaviour when LLM tiers fail", "## Caveats"):  # fmt: skip
        assert h in report, h


def test_report_covers_every_standalone_approach_and_every_preregistered_variant(report):
    for name in ("Rules 1.0.3 (A)", "ML (B)", "LLM small (C)", "LLM mid (C)", "LLM large (C)"):
        assert f"| {name} |" in report
    for v in ("llm_mid_only", "default", "rules_short_circuit", "no_rules_floor", "no_category_floors", "small_first",
              "small_mid", "large_only", "strict_confidence", "no_injection_restriction", "ml_stage_50", "ml_stage_70", "ml_stage_90"):  # fmt: skip
        assert f"hybrid `{v}`" in report, v


def test_report_sanity_check_passes_and_gates_are_reported_separately(report):
    assert (
        "reproduces the standalone mid tier exactly" in report
        and "DOES NOT reproduce" not in report
    )
    assert "two separate gates" in report and "NOT a release gate" in report
    assert "point / lower bound" in report


def test_report_never_presents_recall_alone_and_says_the_recommendation_is_a_recommendation(report):
    assert "HR precision" in report and "HR FPR" in report
    assert "operating point is the product owner's decision" in report
    assert "It does not say the others are worse" in report


def test_report_fault_injection_shows_failsafe_behaviour(report):
    sec = report[report.index("## Behaviour when LLM tiers fail") :]
    assert (
        "mid and large unavailable for every document" in sec
        and "Missing labels count as misses" in sec
    )
    assert "never produces a low default" in sec


def test_report_warns_that_locked_test_was_not_run(report):
    assert "has not been authorised or run" in report


def test_the_whole_report_is_reproducible_run_to_run(tmp_path):
    """No live timing noise may leak into the tables or the recommendation."""
    texts = []
    for i in range(2):
        out = tmp_path / f"h{i}.md"
        assert main(["hybrid", "report", "--out", str(out)]) == 0
        texts.append(
            "\n".join(x for x in out.read_text().splitlines() if not x.startswith("* git "))
        )
    assert texts[0] == texts[1]
    assert "**Recommendation: `default`.**" in texts[0]
