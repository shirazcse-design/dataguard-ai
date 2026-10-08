"""UC5 Foundry evaluation rows (evals/incident/foundry_evals.py), built from the run-2 replay."""

from __future__ import annotations

import json
import re

from evals.incident import foundry_evals as fe


def test_rows_are_code_checks_and_raw_agent_runs_without_raw_ids():
    outcomes, agents = fe.build_rows()
    assert len(outcomes) == 16 and set(outcomes[0]) == set(fe.OUTCOME_FIELDS)
    assert all(r[c] in ("pass", "fail") for r in outcomes for c in fe.CHECKS)
    assert all(r["floor_kept"] == "pass" and r["case_boundary_kept"] == "pass" for r in outcomes)
    flagship = next(r for r in agents if r["id"] == "INC-001")
    final = json.loads(json.loads(flagship["response"])[-1]["content"][0]["text"])
    assert "schema_version" not in final and final["severity_recommendation"] == "HIGH"
    assert len(json.loads(flagship["tool_definitions"])) == 10
    blob = json.dumps(agents)
    assert not re.search(r"\bu-20\d\d\b", blob)  # the subject only ever appears as an alias
    assert "ignore your previous instructions" not in blob.lower()
