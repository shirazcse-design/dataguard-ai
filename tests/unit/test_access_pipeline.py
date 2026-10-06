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
