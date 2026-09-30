"""UC6 Foundry Evals: payload shape, local counts, per-row score parsing and the judge-vs-proxy
cross-check, against a fake client (no test contacts Foundry)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from evals.policy import foundry_evals as fe

DATA = Path("data/uc6/foundry_eval")


def test_outcome_payload_and_local_counts():
    rows = fe.load_rows(DATA / "outcomes.jsonl", fe.OUTCOME_FIELDS)
    p = fe.payload(fe.OUTCOMES_EVAL, fe.OUTCOME_FIELDS, fe.OUTCOME_CRITERIA, rows)
    assert p["name"] == "dataguard-policy-outcomes" and len(p["rows"]) == 108
    assert all(isinstance(v, str) for r in p["rows"] for v in r.values())
    assert {k: v["passed"] for k, v in fe.local_outcome_counts(rows).items()} == {
        "status_ok": 101, "no_forbidden_phrase": 108, "no_unverified_shown": 108, "no_fabricated_citation": 108}  # fmt: skip


def test_quality_criteria_map_the_documented_columns():
    crit = {c["name"]: c for c in fe.quality_criteria("uc4-llm-medium")}
    assert set(crit) == {"groundedness", "relevance", "retrieval"}
    assert crit["groundedness"]["evaluator_name"] == "builtin.groundedness"
    assert crit["groundedness"]["data_mapping"]["context"] == "{{item.context}}"
    assert crit["relevance"]["initialization_parameters"]["deployment_name"] == "uc4-llm-medium"
    rows = fe.load_rows(DATA / "answers.jsonl", fe.QUALITY_FIELDS)
    assert len(rows) == 76 and set(rows[0]) == set(fe.QUALITY_FIELDS)


class _Item:
    def __init__(self, d):
        self._d = d

    def model_dump(self):
        return self._d


def test_per_row_scores_and_disagreements():
    items = [
        _Item({"datasource_item": {"id": "S01-advanced", "level": "advanced", "category": "straightforward"},
               "results": [{"name": "groundedness-abc", "score": 5, "passed": True},
                           {"name": "relevance", "score": 4, "passed": True}]}),
        _Item({"datasource_item": {"id": "P01-advanced", "level": "advanced", "category": "paraphrase"},
               "results": [{"name": "groundedness", "score": 2, "passed": False}]}),
    ]  # fmt: skip
    client = SimpleNamespace(evals=SimpleNamespace(runs=SimpleNamespace(
        output_items=SimpleNamespace(list=lambda run_id, eval_id: items))))  # fmt: skip
    rows = fe.per_row_scores(client, "e", "r")
    assert rows[0]["groundedness"] == 5 and rows[0]["relevance"] == 4
    local = {
        "S01-advanced": {"local_all_claims_verified": True},
        "P01-advanced": {"local_all_claims_verified": True},
    }
    s = fe.summarise_scores(rows, local)
    assert s["by_level"]["advanced"]["groundedness"]["mean"] == 3.5
    assert s["verified_but_judged_ungrounded"] == [{"id": "P01-advanced", "groundedness": 2}]
