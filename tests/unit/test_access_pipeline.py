"""UC3 pipeline: the authorization boundary (the harness decides from facts, the agent can raise but
never lower), tool boundaries (read-only, bound to the request, no other user's context), context
engineering, the frozen golden set, telemetry privacy, and the CLI. Offline: no network."""

from __future__ import annotations

import json

import pytest

from app.access import synth
from app.access.harness import decide
from app.access.schemas import AccessRecommendation
from app.access.service import build_governor, tool_schemas
from app.access.services import alias, build_services, compute_facts
from app.access.tools import CaseState, build_tools

REQ = {r["request_id"]: r for r in synth.REQUESTS}


@pytest.fixture(scope="module")
def svc():
    return build_services("replay")


@pytest.fixture(scope="module")
def gov(svc):
    return build_governor("offline", svc=svc)


def rec(outcome, **kw):
    return AccessRecommendation(
        recommended_outcome=outcome, confidence="high", rationale="test", **kw
    )


def facts(svc, rid, **over):
    return compute_facts(svc, {**REQ[rid], **over})


# -- the authorization boundary -------------------------------------------------------------------
@pytest.mark.parametrize("rid,floor", [("AR-008", "RECOMMEND_REJECT"), ("AR-012", "HUMAN_REVIEW"), ("AR-011", "HUMAN_REVIEW"),
                                       ("AR-015", "HUMAN_REVIEW"), ("AR-014", "HUMAN_REVIEW")])  # fmt: skip
def test_an_approving_agent_cannot_lower_a_floor(svc, rid, floor):
    d = decide(facts(svc, rid), svc.graph, rec("RECOMMEND_APPROVE"))
    assert d.outcome == floor and d.deterministic_outcome == floor and d.hitl_required


def test_a_stricter_agent_raises_to_a_person_but_cannot_reject_alone(svc):
    d = decide(facts(svc, "AR-001"), svc.graph, rec("RECOMMEND_REJECT"))
    assert d.deterministic_outcome == "RECOMMEND_APPROVE" and d.outcome == "HUMAN_REVIEW"
    assert d.agent_effect == "raised_adopted" and "agent_raised_concern" in d.hitl_reasons


def test_agent_failure_and_action_claims_go_to_a_person(svc):
    f = facts(svc, "AR-001")
    assert decide(f, svc.graph, None, agent_failed=True).outcome == "HUMAN_REVIEW"
    assert decide(f, svc.graph, rec("RECOMMEND_APPROVE"), action_claims=1).outcome == "HUMAN_REVIEW"


def test_harness_ignores_the_agents_alternative(svc):
    broad = {"entitlement_id": "cust_prod_readwrite_full", "duration_days": 90}
    d = decide(
        facts(svc, "AR-002"),
        svc.graph,
        rec("RECOMMEND_LIMITED_TIME_BOUND_ACCESS", alternative=broad),
    )
    assert (
        d.alternative.entitlement_id == "cust_prod_read_full" and d.alternative.duration_days == 21
    )


def test_only_a_low_risk_read_approval_skips_human_approval(svc):
    assert decide(facts(svc, "AR-001"), svc.graph, rec("RECOMMEND_APPROVE")).hitl_required is False
    assert (
        decide(
            facts(svc, "AR-009"), svc.graph, rec("RECOMMEND_LIMITED_TIME_BOUND_ACCESS")
        ).hitl_required
        is True
    )


def test_nothing_is_ever_provisioned(gov):
    for r in synth.REQUESTS:
        assert gov.decide(r).decision.provisioned is False


# -- tool boundaries -------------------------------------------------------------------------------
def test_no_write_tool_exists():
    names = [t["function"]["name"] for t in tool_schemas()]
    assert len(names) == 12 and names[-1] == "request_human_review"
    for bad in (
        "grant",
        "revoke",
        "add",
        "remove",
        "assign",
        "provision",
        "modify",
        "set_",
        "update",
        "delete",
    ):
        assert not any(n.startswith(bad) or f"_{bad}" in n for n in names), bad


def test_tools_are_bound_to_the_requester_and_its_scope(svc):
    st = CaseState(svc, facts(svc, "AR-016"), policy=svc.policy_tools("t"))
    tools = {t.name: t for t in build_tools(st)}
    other = alias("u-3021")
    ok, res, err = tools["get_current_entitlements"].fn({"subject": other})
    assert not ok and err == "out_of_scope_subject" and "u-3021" not in json.dumps(res)
    ok, _, err = tools["get_access_path"].fn({"resource_id": "payments_ledger"})
    assert not ok and err == "out_of_scope"
    ok, res, _ = tools["get_user_profile"].fn({})
    assert ok and res["profile"]["subject"] == st.subject and "u-3003" not in json.dumps(res)
    assert st.refusals == ["out_of_scope_subject", "out_of_scope"]


def test_identity_outage_makes_identity_tools_unavailable(svc):
    st = CaseState(svc, facts(svc, "AR-014"), policy=svc.policy_tools("t"))
    tools = {t.name: t for t in build_tools(st)}
    assert tools["get_user_profile"].fn({})[2] == "tool_unavailable"
    assert tools["classify_resource"].fn({"resource_id": "source_repo_core"})[
        0
    ]  # UC4 still answers


# -- context engineering -----------------------------------------------------------------------------
def test_the_agent_starts_from_the_request_slice_only(gov):
    x = gov.decide(REQ["AR-002"])
    payload = x.run.messages[1]["content"]
    assert [i.evidence_id for i in x.context.items] == ["REQ"]
    assert "u-3017" not in payload and x.context.subject in payload


def test_flagged_justification_is_withheld_from_the_agent(gov):
    x = gov.decide(REQ["AR-015"])
    assert (
        x.context.justification_flagged
        and "Ignore all previous" not in x.run.messages[1]["content"]
    )
    assert "guardrail:justification_withheld" in x.decision.reason_codes


# -- golden set and evaluation ------------------------------------------------------------------------
def test_golden_set_is_frozen():
    from evals.access.golden_build import FROZEN, REPO, sha

    m = json.loads(FROZEN.read_text())
    for path, digest in m["sha256"].items():
        assert sha(REPO / path) == digest, f"{path} changed after the freeze"


def test_offline_evaluation_invariants():
    from evals.access.agent_eval import run

    s = run("offline")["summary"]
    assert s["unsafe_approvals"] == 0 and s["floors_lowered"] == 0 and s["provisioned"] == 0
    assert (
        s["write_tool_attempts"] == 0
        and s["unsupported_evidence_ids"] == 0
        and s["invalid_policy_citations"] == 0
    )


# -- telemetry privacy --------------------------------------------------------------------------------
def test_telemetry_is_clean_and_allow_listed():
    from evals.access.observability_report import run_traced

    r = run_traced("offline")
    assert r["privacy_audit"]["clean"], r["privacy_audit"]
    assert (
        r["telemetry"]["requests"] == 16
        and r["telemetry"]["genai_operations"]["invoke_agent"] == 16
    )


# -- the refactor kept UC2's defaults ---------------------------------------------------------------------
def test_uc2_bounded_agent_defaults_unchanged():
    from app.insider.agents import WITHHELD, BoundedAgent, unsupported

    a = BoundedAgent(
        "x",
        None,
        "",
        [],
        max_turns=1,
        max_tool_calls=1,
        max_failures=1,
        output_model=None,
        backend="t",
    )
    assert (a.span_name, a.planner_span, a.tool_span) == (
        "uc2.agent",
        "uc2.agent.planner",
        "uc2.tool",
    )
    assert a.output_filter is unsupported and a.withheld == WITHHELD


def test_cli_investigate_offline(capsys):
    from app.access.cli import main

    assert main(["investigate", "AR-002", "--mode", "offline"]) == 0
    out = capsys.readouterr().out
    assert "RECOMMEND_LIMITED_TIME_BOUND_ACCESS" in out and "cust_prod_read_full for 21 days" in out


def test_agent_input_does_not_depend_on_the_telemetry_salt(gov, monkeypatch):
    a = gov.decide(REQ["AR-002"]).run.messages[1]["content"]
    monkeypatch.setenv("DATAGUARD_TELEMETRY_SALT", "another-deployment")
    assert gov.decide(REQ["AR-002"]).run.messages[1]["content"] == a


def test_cli_exposes_every_documented_command():
    """The register command once silently failed to wire up (a formatter reflow broke a text edit)."""
    import contextlib
    import io

    from app.access.cli import main

    for argv in (
        ["agent", "register", "--help"],
        ["agent", "export", "--help"],
        ["record", "--help"],
        ["obs", "report", "--help"],
    ):
        with contextlib.redirect_stdout(io.StringIO()), pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 0, argv


class _BatchPlanner:
    """Turn 1: three tool calls in one batch. Turn 2: a final answer."""

    name, model_id = "fake", None

    def next_turn(self, messages):
        from app.agent.types import AgentTurn, ParsedToolCall

        if not any(m["role"] == "tool" for m in messages):
            return AgentTurn(tool_calls=[ParsedToolCall(f"c{i}", "t", {}) for i in range(3)])
        return AgentTurn(final_text=json.dumps({"ok": True}))


@pytest.mark.parametrize("finish", [True, False])
def test_graceful_finish_on_budget_is_opt_in(finish):
    from app.agent.bounded import BoundedAgent, Tool, params

    tool = Tool("t", "x", params({}), lambda a: (True, {"evidence_ids": []}, None))
    a = BoundedAgent("x", _BatchPlanner(), "s", [tool], max_turns=4, max_tool_calls=2, max_failures=2,
                     output_model=None, backend="t", finish_on_budget=finish)  # fmt: skip
    r = a.run({})
    answered = {m["tool_call_id"] for m in r.messages if m["role"] == "tool"}
    if finish:
        assert r.stopped_reason == "final_answer" and r.budget_finish and r.tool_calls == 2
        assert answered == {
            "c0",
            "c1",
            "c2",
        }  # every call in the batch has a response (live APIs need it)
        assert "not_executed" in next(
            m["content"] for m in r.messages if m.get("tool_call_id") == "c2"
        )
    else:
        assert r.stopped_reason == "tool_budget_exceeded" and not r.budget_finish  # UC2's behaviour


def test_agent_v2_budget_exceeds_the_tool_count_and_v1_stays_frozen():
    from app.access.pipeline import load_agent_config

    c = load_agent_config()
    assert (
        c["agent_version"] == "2.0.0"
        and c["max_tool_calls"] > len(tool_schemas())
        and c["finish_on_budget"]
    )
    assert c["prompt_version"] == "uc3-agent.v1"


def test_obs_live_check_replays_uc4_uc6_and_plants_a_valid_canary_request(capsys):
    """The live-check failed live once: it built LIVE UC4 services (needs every deployment) and used an
    invalid request id. Only the agent may be live; the canary request must validate."""
    from unittest import mock

    import app.access.service as service
    import app.access.services as services
    import app.agent.foundry_service as fs
    from app.access import cli

    real, real_svc, seen = service.build_governor, services.build_services, {}

    def offline(mode, **k):
        seen.update(mode=mode, has_svc=k.get("svc") is not None)
        return real("offline", **k)

    def svc(mode="replay"):
        seen["svc_mode"] = mode
        return real_svc(mode)

    with mock.patch.object(fs, "project_client"), mock.patch.object(fs, "project_endpoint", return_value="x"), \
         mock.patch("observability.azure_monitor_sink", return_value=None), \
         mock.patch("observability.flush_azure_monitor", return_value=True), \
         mock.patch.object(service, "build_governor", side_effect=offline), \
         mock.patch.object(services, "build_services", side_effect=svc):  # fmt: skip
        assert cli.main(["obs", "live-check", "--tenant-id", "t"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert seen == {"mode": "live", "has_svc": True, "svc_mode": "replay"}
    assert [r["request_id"] for r in out["runs"]] == ["AR-001", "AR-990", "AR-015"]
    assert out["canary"].startswith("CANARY")
