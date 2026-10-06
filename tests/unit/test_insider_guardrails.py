"""UC2 adversarial suite (evals/insider/guardrails_verify.py): offline wiring checks. The live
run goes through the Foundry agents; here the agents are the scripted offline planners."""

from __future__ import annotations

import json
from types import SimpleNamespace

from evals.insider import guardrails_verify as gv
from evals.policy.guardrails_verify import RecordingOpenAI


def test_probe_set_covers_the_required_attacks():
    ids = [p["id"] for p in gv.PROBES]
    assert len(ids) == len(set(ids)) == 15
    paths = {p["path"] for p in gv.PROBES}
    assert paths == {"app", "direct", "direct_tool"}
    assert any(p["id"] == "GA14" and p["base"] == "I10" for p in gv.PROBES)  # false-positive check


def test_probe_case_adds_one_log_comment_and_keeps_the_base():
    p = next(x for x in gv.PROBES if x["id"] == "GA11")
    case = gv.probe_case(p)
    assert case["id"] == "GA11" and case["user_id"] == "u-2043"
    assert case["overlays"][-1] == {"type": "log_comment", "hour": 21, "text": gv.SCORE_LIE}


def test_app_probe_offline_floor_holds_and_nothing_leaks():
    from app.insider.service import build_investigator
    from evals.insider.agent_eval import load_golden

    golden = {c["id"]: c for c in load_golden()}
    inv = build_investigator("offline")
    for pid in ("GA2", "GA8", "GA12"):
        p = next(x for x in gv.PROBES if x["id"] == pid)
        r = inv.investigate(gv.probe_case(p))
        row = gv.summarise_app(p, r, golden[p["base"]], RecordingOpenAI(SimpleNamespace()))
        assert not row["unsafe_outcome"] and row["band_unchanged"] is True
        assert not row["canary_leak_to_coordinator"] and not row["fake_policy_cited"]
        if pid == "GA2":
            assert "app_scanner_withheld" in row["stopped_by"]


class _Blocked(Exception):
    status_code = 400
    body = {
        "code": "content_filter",
        "innererror": {
            "content_filter_result": {"jailbreak": {"filtered": True, "detected": True}}
        },
    }


def test_direct_probe_records_a_foundry_block_without_text():
    client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **k: (_ for _ in ()).throw(_Blocked()))
    )
    row = gv.direct(client, "dataguard-insider-orchestrator", gv.DIRECT_GUILT)
    assert row["outcome"] == "blocked"
    md = gv.render([{"id": "GA1", "name": "x", "path": "direct", "direct": row}], {"date": "t"})
    assert gv.DIRECT_GUILT not in md and gv.DIRECT_GUILT not in json.dumps(row)
