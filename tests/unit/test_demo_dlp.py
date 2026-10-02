"""The demo's Agentic DLP (UC1) endpoints: REPLAY investigations from the real pipeline, honest
labels, input validation, the demo-only simulated-action approval log, the committed-results
summary, and the static page."""

from __future__ import annotations

import json

import pytest

from app.demo.classify import DemoError
from app.demo.dlp import EXAMPLES, REVIEW_ACTIONS
from app.demo.server import DemoApp, _static


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    return DemoApp(mode="replay", env={}, dlp_review_path=tmp_path_factory.mktemp("d") / "r.jsonl")


def test_describe_lists_curated_golden_cases(app):
    env = app.dlp_describe()
    assert env["mode_label"] == "REPLAY"
    ex = env["data"]["examples"]
    assert [e["id"] for e in ex] == [e["id"] for e in EXAMPLES] and ex[0]["id"] == "D11"
    assert {e["key"] for e in ex} >= {"safe", "flagship", "low_conf", "conflict", "injection",
                                      "tool_failure"}  # fmt: skip
    assert "expected_outcome" not in json.dumps(ex)  # the page never shows a scripted answer


def test_flagship_replays_through_the_real_pipeline(app):
    env = app.dlp_investigate({"case_id": "D11"})
    r = env["data"]
    assert env["data_class"] == "REPLAY" and r["mode"] == "replay"
    assert r["decision"]["outcome"] == "ESCALATE" and r["decision"]["human_review_required"]
    assert "simulated" in r["decision"]["simulated_action"]
    assert [s["name"] for s in r["stages"]][-1] == "decision"
    assert r["agent"]["stopped_reason"] == "final_answer"


def test_foundry_backend_replays_its_own_recording_or_says_it_is_not_recorded(app):
    r = app.dlp_investigate({"case_id": "D11", "backend": "foundry-service"})["data"]
    assert r["backend"] == "foundry-service" and r["recorded_for_backend"] is True
    assert r["agent"]["stopped_reason"] == "final_answer"
    r = app.dlp_investigate({"case_id": "D19", "backend": "foundry-service"})["data"]
    assert r["recorded_for_backend"] is False
    assert r["decision"]["outcome"] != "ALLOW"  # a replay miss never becomes an ALLOW


def test_simulated_fault_is_labelled(app):
    r = app.dlp_investigate({"case_id": "D28"})["data"]
    assert r["simulated_faults"] == ["activity_tool_error"]


def test_inputs_are_validated(app):
    for body in ({"case_id": "D99"}, {"case_id": "D11", "backend": "rogue"}, {}):
        with pytest.raises(DemoError):
            app.dlp_investigate(body)


def test_approval_is_demo_only_and_never_executes(app, tmp_path):
    local = DemoApp(mode="replay", env={}, dlp_review_path=tmp_path / "r.jsonl")
    local.dlp_investigate({"case_id": "D11"})
    pending = local.dlp_review()["data"]["pending"]
    assert pending and pending[0]["case_id"] == "D11"
    rec = local.dlp_review_decide({"key": pending[0]["key"], "action": REVIEW_ACTIONS[0]})
    row = json.loads((tmp_path / "r.jsonl").read_text().splitlines()[0])
    assert row["executed"] is False and row["simulated"] is True and row["not_gold_adjudication"]
    assert rec["data_class"] == "DEMO-ONLY STATE"
    with pytest.raises(DemoError):  # cannot be decided twice
        local.dlp_review_decide({"key": pending[0]["key"], "action": REVIEW_ACTIONS[0]})
    local.dlp_investigate({"case_id": "D21"})
    key = local.dlp_review()["data"]["pending"][0]["key"]
    with pytest.raises(DemoError):
        local.dlp_review_decide({"key": key, "action": "block_now"})


def test_summary_reads_committed_results(app):
    s = app.dlp_describe()["data"]["summary"]
    assert s["eval"]["v2"]["critical_false_negative_count"] == 0
    assert s["eval"]["v1"]["acceptable_outcome_accuracy"] == pytest.approx(0.8333, abs=1e-3)
    fo = s["foundry"]["outcomes"]
    assert all(fo["per_criterion"][k]["passed"] == fo["local"][k]["passed"] for k in fo["local"])
    assert s["foundry"]["quality_v2"]["groundedness"]["passed"] == 30
    assert len(s["guardrails"]) == 7 and s["observability"]["privacy_audit"]["clean"] is True


def test_dlp_page_is_served_registered_and_safe():
    js = _static("dlp.js").decode()
    assert "PAGES.dlp" in js and ".innerHTML" not in js
    assert 'JUMP_TARGETS.push(["Agentic DLP"' in js
    html = _static("index.html").decode()
    assert "/static/dlp.js" in html and 'data-page="dlp"' in html
    assert html.index("/static/policy.js") < html.index("/static/dlp.js")  # needs extendPage
