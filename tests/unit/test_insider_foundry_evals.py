"""UC2 Foundry evaluation rows (evals/insider/foundry_evals.py): built offline from the replay of
live run 3. No network."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from evals.insider import foundry_evals as fe
from evals.insider import golden_build as G


@pytest.fixture(scope="module")
def rows():
    return fe.build_rows({"I13", "I30"})


def test_outcome_rows_cover_all_cases_with_pass_fail_checks(rows):
    outcomes, _, _ = rows
    assert len(outcomes) == 36
    assert all(r[c] in ("pass", "fail") for r in outcomes for c in fe.CHECKS)
    assert set(outcomes[0]) == set(fe.OUTCOME_FIELDS)
    counts = fe.local_outcome_counts(outcomes)
    assert counts["no_critical_miss"]["passed"] == counts["no_critical_miss"]["total"] == 36


def test_agent_rows_hold_lists_and_the_right_tools(rows):
    _, tools, risk = rows
    assert {r["agent"] for r in tools} <= {"orchestrator", "behavior", "investigator"}
    assert [r["agent"] for r in risk] == ["risk", "risk"]
    for r in tools:
        calls, defs = json.loads(r["tool_calls"]), json.loads(r["tool_definitions"])
        assert isinstance(json.loads(r["response"]), list)
        assert {c["name"] for c in calls} <= {d["name"] for d in defs}
    assert all("tool_calls" not in r for r in risk)


def test_rows_carry_no_untrusted_text(rows):
    blob = json.dumps(rows)
    for marker in (G.INJECTION, G.POISON, G.CANARY):
        assert marker[:40] not in blob


def test_payloads_type_list_fields_and_map_every_field(tmp_path, rows):
    _, tools, risk = rows
    fe.write_jsonl(tmp_path / "a.jsonl", tools)
    fe.write_jsonl(tmp_path / "r.jsonl", risk)
    p = fe.list_payload(fe.AGENT_EVAL, fe.AGENT_FIELDS, fe.criteria(fe.AGENT_MAPPING, "j"),
                        fe.load_list_rows(tmp_path / "a.jsonl", fe.AGENT_FIELDS))  # fmt: skip
    assert p["data_source_config"]["item_schema"]["properties"]["tool_calls"]["type"] == "array"
    q = fe.list_payload(fe.RISK_EVAL, fe.RISK_FIELDS, fe.criteria(fe.RISK_MAPPING, "j"),
                        fe.load_list_rows(tmp_path / "r.jsonl", fe.RISK_FIELDS))  # fmt: skip
    assert "tool_call_accuracy" not in {c["name"] for c in q["testing_criteria"]}
    assert "tool_definitions" not in q["data_source_config"]["item_schema"]["properties"]


def test_per_row_scores_keep_the_agent():
    item = {"datasource_item": {"id": "I13", "agent": "behavior"},
            "results": [{"name": "task_adherence-x", "score": 4, "passed": True}]}  # fmt: skip
    client = SimpleNamespace(evals=SimpleNamespace(runs=SimpleNamespace(output_items=SimpleNamespace(
        list=lambda **k: [item]))))  # fmt: skip
    scores = fe.per_row_scores(client, "e", "r")
    assert scores[0]["agent"] == "behavior" and scores[0]["task_adherence"] == 4
    assert fe.per_agent(scores, ("task_adherence",))["behavior"]["task_adherence"]["passed"] == 1


def test_judges_see_the_raw_final_reply_not_the_harness_output(rows):
    """Run 20261004-1844: the parsed output added schema_version and truncated fields, and the
    judges failed task adherence for "extra field" / "truncated". Rows must carry the raw reply."""
    _, tools, risk = rows
    finals = [json.loads(r["response"])[-1]["content"][0] for r in tools + risk]
    texts = [f["text"] for f in finals if "text" in f]
    assert texts and not any("schema_version" in t for t in texts)
    assert any(len(t) > 1000 for t in texts)
