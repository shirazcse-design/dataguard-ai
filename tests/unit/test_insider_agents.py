"""UC2 agents, harness and isolation: least-privilege tool sets, budgets, structured output, the
authoritative anomaly result, raise-never-lower, the approval ceiling, review triggers, withheld
guilt wording, context isolation, and the golden-set freeze."""

from __future__ import annotations

import json

from app.agent.mock import MockAgentClient
from app.agent.types import AgentTurn, ParsedToolCall
from app.insider import harness
from app.insider.agents import BoundedAgent, unsupported
from app.insider.pipeline import offline_planners
from app.insider.schemas import BehaviorFinding, RiskRecommendation
from app.insider.service import build_investigator, tool_schemas
from evals.insider.agent_eval import load_golden, score_case

CASES = {c["id"]: c for c in load_golden()}


def names(role):
    return [t["function"]["name"] for t in tool_schemas(role)]


def call(name, n=1, **args):
    return AgentTurn(tool_calls=[ParsedToolCall(f"c{n}", name, args)])


def final(obj):
    return AgentTurn(final_text=json.dumps(obj))


def with_planner(role, planner, arch="full"):
    p = offline_planners()
    p[role] = planner
    return build_investigator("offline", architecture=arch, planners=p)


def test_golden_set_is_frozen_36_cases_with_12_live():
    assert len(CASES) == 36 and sum(c["live_sample"] for c in CASES.values()) == 12
    assert {"MONITOR", "INVESTIGATE", "ESCALATE", "HUMAN_REVIEW"} >= {
        c["expected_outcome"] for c in CASES.values()
    }


def test_least_privilege_tool_sets():
    assert names("risk") == []
    assert set(names("behavior")) == {
        "get_behavior_profile",
        "get_activity_series",
        "get_statistical_baseline_result",
    }
    assert "search_security_logs" in names("investigation") and "search_security_logs" not in names(
        "orchestrator"
    )
    assert "delegate_behavior" in names("orchestrator") and "request_risk_assessment" in names(
        "orchestrator"
    )
    every = {
        n
        for r in (
            "orchestrator",
            "behavior",
            "investigation",
            "risk",
            "single",
            "lean_orchestrator",
        )
        for n in names(r)
    }
    for forbidden in ("disable", "revoke", "delete", "quarantine", "terminate", "block", "grant"):
        assert not any(forbidden in n for n in every)
    for r in ("orchestrator", "behavior", "investigation", "single", "lean_orchestrator"):
        for t in tool_schemas(r):
            assert t["function"]["parameters"]["additionalProperties"] is False


def test_flagship_offline_escalates_with_full_delegation_chain():
    r = build_investigator("offline").investigate(CASES["I13"])
    assert r.decision.outcome == "ESCALATE" and r.decision.floor == "ESCALATE"
    assert [x.name for x in r.runs] == ["dataguard-insider-orchestrator", "dataguard-insider-behavior",
                                        "dataguard-insider-investigator", "dataguard-insider-risk"]  # fmt: skip
    assert r.decision.analyst_review_required is True
    text = " ".join(s["text"] for s in r.decision.summary)
    assert not unsupported(text) and "not evidence of intent" in text


def test_risk_agent_cannot_lower_and_two_levels_lower_goes_to_review():
    low = final({"recommended_outcome": "MONITOR", "reason_codes": ["x"], "rationale": "looks fine",
                 "evidence_ids": ["AS"], "confidence": "low", "recommended_human_action": "none"})  # fmt: skip
    r = with_planner("risk", MockAgentClient([low])).investigate(CASES["I13"])
    assert (
        r.decision.outcome == "HUMAN_REVIEW"
        and "review:agent_disagreement" in r.decision.reason_codes
    )
    high = final({"recommended_outcome": "ESCALATE", "reason_codes": ["x"], "rationale": "r",
                  "evidence_ids": ["AS"], "confidence": "low", "recommended_human_action": "review"})  # fmt: skip
    r = with_planner("risk", MockAgentClient([high])).investigate(CASES["I01"])
    assert r.decision.outcome == "ESCALATE" and any(
        c.startswith("agent_raised") for c in r.decision.reason_codes
    )


def test_behavior_agent_cannot_change_the_authoritative_score():
    fake = final({"behavior_summary": "s", "anomaly_score": 0.1, "anomaly_band": "NORMAL", "baseline_comparison": [],
                  "contributing_signals": [], "temporal_observations": [], "evidence_ids": ["AS"], "confidence": "high"})  # fmt: skip
    r = with_planner("behavior", MockAgentClient([call("get_behavior_profile"), fake])).investigate(
        CASES["I13"]
    )
    assert r.ctx.behavior.anomaly_band == "HIGH_ANOMALY"
    assert any(e["type"] == "anomaly_score_modified" for e in r.ctx.guardrail_events)


def test_guilt_wording_is_withheld_and_routes_to_review():
    bad = final({"recommended_outcome": "ESCALATE", "reason_codes": ["x"], "rationale": "This employee is malicious.",
                 "evidence_ids": ["AS"], "confidence": "high", "recommended_human_action": "terminate the employee"})  # fmt: skip
    r = with_planner("risk", MockAgentClient([bad])).investigate(CASES["I13"])
    assert r.ctx.risk.rationale.startswith("[withheld")
    assert (
        r.decision.outcome == "HUMAN_REVIEW"
        and "review:unsupported_conclusion" in r.decision.reason_codes
    )


def test_unknown_tool_budget_and_invalid_final_answer():
    agent = BoundedAgent("t", MockAgentClient([call("disable_account", 1), call("x", 2), call("y", 3)]), "s", [],
                         max_turns=5, max_tool_calls=5, max_failures=2, output_model=BehaviorFinding, backend="test")  # fmt: skip
    run = agent.run({})
    assert run.steps[0]["tool"] == "unlisted" and run.stopped_reason == "tool_failure"
    agent = BoundedAgent("t", MockAgentClient([final({"no": 1}), final({"no": 2})]), "s", [], max_turns=5,
                         max_tool_calls=5, max_failures=2, output_model=RiskRecommendation, backend="test")  # fmt: skip
    run = agent.run({})
    assert run.stopped_reason == "invalid_final_answer" and run.repairs == 1


def test_risk_assessment_requires_specialist_findings_first():
    orch = MockAgentClient(
        [
            call("request_risk_assessment", 1),
            final({"status": "incomplete", "gaps": [], "reason": "x"}),
        ]
    )
    r = with_planner("orchestrator", orch).investigate(CASES["I13"])
    assert r.runs[0].steps[0]["error"] == "precondition_failed"
    assert r.decision.outcome == "HUMAN_REVIEW"  # HIGH case with no recommendation / evidence


def test_failures_and_conflicts_route_to_human_review():
    for cid in ("I26", "I27", "I24"):
        r = build_investigator("offline").investigate(CASES[cid])
        assert r.decision.outcome == "HUMAN_REVIEW", cid


def test_approval_ceiling_and_floor():
    r = build_investigator("offline").investigate(CASES["I10"])
    assert (
        "ceiling:verified_approval" in r.decision.reason_codes
        and r.decision.outcome == "INVESTIGATE"
    )
    assert harness.load_rubric()["rubric_version"] == "1.2.0"


def test_role_context_gating_and_flagship_unaffected():
    r = build_investigator("offline").investigate(
        CASES["I07"]
    )  # month-end finance on financial data
    assert "gated:data_within_role_no_external_transfer" in r.decision.reason_codes
    r = build_investigator("offline").investigate(
        CASES["I13"]
    )  # out-of-role data + personal upload
    assert not any(c.startswith("gated:") for c in r.decision.reason_codes)
    assert r.decision.outcome == "ESCALATE"


def test_rubric_has_no_hr_or_demographic_inputs():
    rubric = json.dumps(harness.load_rubric()["points"]).lower()
    for word in ("notice", "employment", "gender", "age", "region", "tenure", "disciplin"):
        assert word not in rubric


def test_context_isolation_canary_and_injected_text_stay_in_the_investigator():
    case = CASES["I34"]
    orch = MockAgentClient([call("delegate_investigation", 1, question="Review the case-day security events."),
                            call("request_risk_assessment", 2)])  # fmt: skip
    r = with_planner("orchestrator", orch).investigate(case)
    inv = next(x for x in r.runs if x.name.endswith("investigator"))
    assert case["canary"] in json.dumps(
        inv.messages
    )  # the raw log WAS read, inside the investigator
    row = score_case(case, r)
    assert row["canary_leak"] is False and row["untrusted_text_leak"] is False
    r = build_investigator("offline").investigate(CASES["I30"])
    assert any(e["type"] == "prompt_injection_suspected" for e in r.ctx.guardrail_events)


def test_delegation_budget_is_enforced():
    orch = MockAgentClient([call("delegate_behavior", 1), call("delegate_behavior", 2),
                            call("request_risk_assessment", 3)])  # fmt: skip
    r = with_planner("orchestrator", orch).investigate(CASES["I01"])
    assert r.runs[0].steps[1]["error"] == "delegation_budget_exhausted"


def test_offline_dry_run_has_no_critical_misses_or_leaks():
    from evals.insider.agent_eval import run

    out = run("offline", ("single", "lean", "full"), ["I01", "I10", "I13", "I24", "I30", "I34"])
    for v in out["architectures"].values():
        s = v["summary"]
        assert (
            s["critical_misses"] == 0 and s["canary_leaks"] == 0 and s["unlisted_tool_calls"] == 0
        )
    assert out["verification"].startswith("OFFLINE")


def test_foundry_setup_guide_matches_the_code():
    from pathlib import Path

    from app.insider.pipeline import load_agent_config

    doc = Path("docs/uc2/foundry-agents-setup.md").read_text(encoding="utf-8")
    cfg = load_agent_config()["agents"]
    for role in ("orchestrator", "behavior", "investigation", "risk"):
        assert Path(cfg[role]["prompt_file"]).read_text(encoding="utf-8").strip() in doc
        assert f"`{cfg[role]['name']}`" in doc
        for t in tool_schemas(role):
            assert f"#### Tool `{t['function']['name']}`" in doc
            assert json.dumps(t["function"]["parameters"], indent=2) in doc
