"""The demo's Insider Risk Investigation (UC2) endpoints: REPLAY investigations from the real
pipeline, recorded-combination checks, input validation, the demo-only analyst-decision log, the
committed-results summary, and the static page."""

from __future__ import annotations

import json

import pytest

from app.demo.classify import DemoError
from app.demo.insider import EXAMPLES, FOUNDRY_RECORDED, REVIEW_ACTIONS
from app.demo.server import DemoApp, _static


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    return DemoApp(
        mode="replay", env={}, insider_review_path=tmp_path_factory.mktemp("i") / "r.jsonl"
    )


def test_describe_lists_curated_live_sample_cases(app):
    env = app.insider_describe()
    assert env["mode_label"] == "REPLAY"
    ex = env["data"]["examples"]
    assert [e["id"] for e in ex] == [e["id"] for e in EXAMPLES] and ex[0]["id"] == "I13"
    cases = app.insider_session().cases
    assert all(cases[e["id"]]["live_sample"] for e in ex)  # every architecture is recorded
    assert "expected_outcome" not in json.dumps(ex) and "acceptable" not in json.dumps(ex)


@pytest.mark.parametrize("arch", ["full", "lean", "single"])
def test_flagship_escalates_in_every_architecture(app, arch):
    env = app.insider_investigate({"case_id": "I13", "architecture": arch})
    r = env["data"]
    assert env["data_class"] == "REPLAY" and r["mode"] == "replay" and r["architecture"] == arch
    assert r["decision"]["outcome"] == "ESCALATE" and r["anomaly"]["anomaly_band"] == "HIGH_ANOMALY"
    assert all(not a["stopped_reason"].startswith("planner_error") for a in r["agents"])


def test_foundry_agents_replay_their_own_recordings(app):
    for cid in FOUNDRY_RECORDED:
        r = app.insider_investigate({"case_id": cid, "backend": "foundry-service"})["data"]
        assert r["backend"] == "foundry-service" and {"orchestrator", "risk"} <= {
            a["role"] for a in r["agents"]
        }
        assert all(not a["stopped_reason"].startswith("planner_error") for a in r["agents"])


def test_unrecorded_combinations_are_refused_not_faked(app):
    for body in ({"case_id": "I01", "backend": "foundry-service"},
                 {"case_id": "I13", "architecture": "single", "backend": "foundry-service"},
                 {"case_id": "I05", "architecture": "single"}):  # fmt: skip
        with pytest.raises(DemoError):
            app.insider_investigate(body)


def test_simulated_failure_and_injection_are_visible(app):
    r = app.insider_investigate({"case_id": "I27"})["data"]
    assert r["case"]["simulated_faults"] == ["identity_unavailable"] and "identity" in r["failures"]
    assert r["decision"]["outcome"] == "HUMAN_REVIEW"
    r = app.insider_investigate({"case_id": "I30"})["data"]
    assert any(g["type"] == "prompt_injection_suspected" for g in r["guardrail_events"])
    assert r["decision"]["outcome"] != "MONITOR"


def test_view_carries_no_agent_messages_or_canary(app):
    from evals.insider import golden_build as G

    r = app.insider_investigate({"case_id": "I34"})["data"]
    blob = json.dumps(r)
    assert G.CANARY not in blob and '"messages"' not in blob


def test_inputs_are_validated(app):
    for body in ({"case_id": "I99"}, {"case_id": "I13", "architecture": "swarm"},
                 {"case_id": "I13", "backend": "rogue"}, {}):  # fmt: skip
        with pytest.raises(DemoError):
            app.insider_investigate(body)


def test_analyst_decision_is_demo_only_and_never_executes(tmp_path):
    local = DemoApp(mode="replay", env={}, insider_review_path=tmp_path / "r.jsonl")
    local.insider_investigate({"case_id": "I13"})
    pending = local.insider_review()["data"]["pending"]
    assert pending and pending[0]["case_id"] == "I13"
    rec = local.insider_review_decide({"key": pending[0]["key"], "action": REVIEW_ACTIONS[0]})
    row = json.loads((tmp_path / "r.jsonl").read_text().splitlines()[0])
    assert row["executed"] is False and row["simulated"] is True and row["not_gold_adjudication"]
    assert rec["data_class"] == "DEMO-ONLY STATE"
    with pytest.raises(DemoError):  # cannot be decided twice
        local.insider_review_decide({"key": pending[0]["key"], "action": REVIEW_ACTIONS[0]})
    local.insider_investigate({"case_id": "I24"})
    key = local.insider_review()["data"]["pending"][0]["key"]
    for bad in (
        {"key": key, "action": "terminate"},
        {"key": key, "action": "agree", "note": "x" * 501},
    ):
        with pytest.raises(DemoError):
            local.insider_review_decide(bad)


def test_normal_day_needs_no_analyst(tmp_path):
    local = DemoApp(mode="replay", env={}, insider_review_path=tmp_path / "r.jsonl")
    r = local.insider_investigate({"case_id": "I01"})["data"]
    assert r["decision"]["outcome"] == "MONITOR" and not local.insider_review()["data"]["pending"]


def test_summary_reads_committed_results(app):
    s = app.insider_describe()["data"]["summary"]
    assert s["ml"]["separation"]["roc_auc"]["isolation_forest"] == pytest.approx(0.9942, abs=1e-4)
    assert s["agents"]["full36"]["critical_misses"] == 0
    assert s["agents"]["full12"]["cases"] == 12 and s["agents"]["single12"]["cases"] == 12
    fo = s["foundry"]["outcomes"]
    assert all(fo["per_criterion"][k]["passed"] == fo["local"][k]["passed"] for k in fo["local"])
    assert s["foundry"]["agent_quality"]["behavior"]["groundedness"]["passed"] == 12
    assert len(s["guardrails"]) == 15 and s["observability"]["privacy_audit"]["clean"] is True


def test_insider_page_is_served_registered_and_safe():
    js = _static("insider.js").decode()
    assert "PAGES.insider" in js and ".innerHTML" not in js
    assert 'JUMP_TARGETS.push(["Insider Risk"' in js
    html = _static("index.html").decode()
    assert "/static/insider.js" in html and 'data-page="insider"' in html
    # Scripts share one global scope: no top-level name may collide with the other page scripts.
    import re

    def names(src: str) -> set[str]:
        return set(re.findall(r"^(?:const|let|function|async function)\s+(\w+)", src, re.M))

    others = set().union(*(names(_static(f).decode()) for f in ("app.js", "policy.js", "dlp.js")))
    assert not names(js) & others
