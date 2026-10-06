"""UC3 adversarial probes (evals/access/guardrails_verify.py): offline wiring. The live run goes
through the Foundry agent; here the agent is the scripted offline planner."""

from __future__ import annotations

import json
from types import SimpleNamespace

from evals.access import guardrails_verify as gv
from evals.policy.guardrails_verify import RecordingOpenAI


def test_six_probes_cover_the_brief():
    assert [p["id"] for p in gv.PROBES] == ["GP1", "GP2", "GP3", "GP4", "GP5", "GP6"]
    req = gv.probe_request(gv.PROBES[3], 4)
    assert (
        req["request_id"] == "AR-904"
        and req["entitlement_id"] == "payments_approve"
        and req["justification"] == gv.BYPASS_SOD
    )


def test_sod_bypass_text_cannot_move_the_outcome_offline():
    from app.access.harness import decide
    from app.access.service import build_governor
    from app.access.services import compute_facts

    gov = build_governor("offline")
    p = gv.PROBES[3]
    base = decide(
        compute_facts(gov.svc, gv.probe_request(p, 4) | {"justification": ""}), gov.svc.graph, None
    ).deterministic_outcome
    row = gv.summarise_app(
        gov.decide(gv.probe_request(p, 4)), base, RecordingOpenAI(SimpleNamespace())
    )
    assert (
        row["outcome"] == "RECOMMEND_REJECT" and not row["unsafe"] and row["provisioned"] is False
    )


class _Blocked(Exception):
    status_code = 400
    body = {
        "code": "content_filter",
        "innererror": {
            "content_filter_result": {"jailbreak": {"filtered": True, "detected": True}}
        },
    }


def test_a_blocked_direct_probe_is_recorded_without_its_text():
    client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **k: (_ for _ in ()).throw(_Blocked()))
    )
    d = gv.direct(client, "dataguard-access-governance", gv.GRANT_ADMIN)
    assert d["outcome"] == "blocked"
    md = gv.render([{"id": "GP1", "name": "x", "direct": d}], {"date": "t"})
    assert (
        gv.GRANT_ADMIN not in md and gv.GRANT_ADMIN not in json.dumps(d) and "Foundry blocked" in md
    )


def test_cli_has_the_guardrails_command():
    import contextlib
    import io

    import pytest

    from app.access.cli import main

    with contextlib.redirect_stdout(io.StringIO()), pytest.raises(SystemExit) as e:
        main(["guardrails", "verify", "--help"])
    assert e.value.code == 0
