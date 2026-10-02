"""UC1 Foundry evaluation rows: built from the replay run, the local string-check counts match the
golden evaluation, every row is a string field, and no document text or filename is included."""

from __future__ import annotations

import json

import pytest

from evals.dlp import foundry_evals as fe


@pytest.fixture(scope="module")
def rows():
    return fe.build_rows("offline")


def test_outcome_rows_cover_every_case_and_counts_are_local_truth(rows):
    outcomes, _, _ = rows
    assert len(outcomes) == 30
    as_str = [{f: str(r[f]) for f in fe.OUTCOME_FIELDS} for r in outcomes]
    counts = fe.local_outcome_counts(as_str)
    assert set(counts) == set(fe.CHECKS)
    assert counts["no_critical_false_negative"]["passed"] == 30


def test_agent_rows_carry_the_conversation_but_no_document_content(rows):
    _, agent_rows, _ = rows
    assert agent_rows
    for r in agent_rows:
        assert set(fe.AGENT_FIELDS) <= set(r)
        json.loads(r["tool_calls"])
        assert [t["name"] for t in json.loads(r["tool_definitions"])][0] == "search_policy"
    blob = json.dumps(agent_rows)
    for secret in ("Kestrel Sensor Systems", "Acquisition_Targets_2027", "410-460"):
        assert secret not in blob


def test_agent_criteria_use_builtin_evaluators_with_the_judge():
    crit = fe.agent_criteria("judge-x")
    assert {c["evaluator_name"] for c in crit} == {
        "builtin.task_adherence", "builtin.intent_resolution", "builtin.groundedness",
        "builtin.tool_call_accuracy"}  # fmt: skip
    assert all(c["initialization_parameters"]["deployment_name"] == "judge-x" for c in crit)


def test_agent_payload_sends_tool_fields_as_lists(rows, tmp_path):
    _, agent_rows, _ = rows
    path = tmp_path / "agent.jsonl"
    fe.write_jsonl(path, agent_rows)
    loaded = fe.load_agent_rows(path)
    assert all(isinstance(r["tool_definitions"], list) and isinstance(r["tool_calls"], list)
               for r in loaded)  # fmt: skip
    p = fe.agent_payload(fe.agent_criteria("j"), loaded)
    props = p["data_source_config"]["item_schema"]["properties"]
    assert props["tool_definitions"]["type"] == "array" and props["query"]["type"] == "string"
