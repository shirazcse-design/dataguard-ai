"""UC3 Foundry evaluation rows (evals/access/foundry_evals.py), built offline from the replay."""

from __future__ import annotations

import json

from evals.access import foundry_evals as fe


def test_rows_mirror_the_local_eval_and_judge_the_raw_reply():
    outcomes, agents = fe.build_rows()
    assert len(outcomes) == 16 and all(r[c] == "pass" for r in outcomes for c in fe.CHECKS)
    assert {tuple(sorted(r)) for r in outcomes} == {tuple(sorted(fe.OUTCOME_FIELDS))}
    assert {r["id"] for r in agents} <= {r["id"] for r in outcomes}
    flagship = next(r for r in agents if r["id"] == "AR-002")
    last = json.loads(flagship["response"])[-1]
    final = json.loads(last["content"][0]["text"])
    assert "schema_version" not in final  # the model's own reply, not the harness's parsed copy
    assert final["recommended_outcome"] == "RECOMMEND_LIMITED_TIME_BOUND_ACCESS"
    assert len(json.loads(flagship["tool_definitions"])) == 12


def test_instruction_like_justifications_never_reach_the_rows():
    _, agents = fe.build_rows()
    blob = json.dumps(agents)
    assert "Ignore all previous instructions" not in blob and "withheld: instruction-like" in blob
