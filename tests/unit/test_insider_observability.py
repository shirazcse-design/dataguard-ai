"""UC2 telemetry through the PRODUCTION redactor: allow-listed keys only, a pseudonymous subject,
no user id / repository / destination / log text / canary / document or policy text, the target
span tree, and GenAI mapping of the agent, tool and planner spans."""

from __future__ import annotations

import pytest

from app.insider.pipeline import pseudonym
from evals.insider.observability_report import privacy_audit, run_traced, summarise
from observability.config import load_observability_config
from observability.sinks import _genai_attrs


@pytest.fixture(scope="module")
def traced():
    return run_traced("offline")


def test_privacy_audit_is_clean_and_keys_are_allow_listed(traced):
    spans, cases, inv = traced
    a = privacy_audit(spans, cases, inv)
    assert a["clean"], a["leaks"]
    allowed = set(load_observability_config()[0].allowed_attributes)
    for s in spans:
        assert set(s.attributes) <= allowed, (s.name, set(s.attributes) - allowed)


def test_subject_is_pseudonymous_and_the_tree_is_complete(traced):
    spans, cases, _ = traced
    roots = [s for s in spans if s.name == "uc2.case"]
    assert len(roots) == len(cases)
    flag = next(r for r in roots if r.attributes["dg.ir.case_id"] == "I13")
    assert flag.attributes["dg.ir.subject"] == pseudonym("u-2043") != "u-2043"
    names = {s.name for s in spans}
    assert {"uc2.anomaly", "uc2.orchestrator", "uc2.behavior_agent", "uc2.identity", "uc2.data", "uc2.policy",
            "uc2.investigation_agent", "uc2.risk_agent", "uc2.deterministic_risk", "uc2.hitl", "uc2.tool"} <= names  # fmt: skip
    assert summarise(spans)["outcomes"]


def test_review_reasons_are_counted_for_every_human_review(traced):
    """The reason codes sit on uc2.deterministic_risk, not the root (the first report read the
    root and showed no review reasons beside 8 HUMAN_REVIEW outcomes)."""
    spans, _, _ = traced
    t = summarise(spans)
    n_review = t["outcomes"].get("HUMAN_REVIEW", 0)
    assert n_review and sum(t["review_reasons"].values()) >= n_review


def test_uc2_spans_map_to_genai_conventions():
    assert (
        _genai_attrs("uc2.risk_agent", {"dg.agent.name": "dataguard-insider-risk"})[
            "gen_ai.operation.name"
        ]
        == "invoke_agent"
    )
    assert (
        _genai_attrs("uc2.tool", {"dg.agent.tool": "search_security_logs"})["gen_ai.tool.name"]
        == "search_security_logs"
    )
    live = {"dg.llm.model_id": "uc4-llm-medium", "dg.llm.cached": False, "dg.tokens_in": 10}
    assert _genai_attrs("uc2.agent.planner", live)["gen_ai.operation.name"] == "chat"
    assert _genai_attrs("uc2.agent.planner", {**live, "dg.llm.cached": True}) == {}
