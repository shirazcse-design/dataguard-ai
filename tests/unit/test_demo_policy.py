"""The demo's Policy Copilot (UC6) endpoints: REPLAY answers from the real pipeline, honest labels,
input validation, demo-only review log, the committed-results summary, and the static page."""

from __future__ import annotations

import json

import pytest

from app.demo.classify import DemoError
from app.demo.policy import EXAMPLES
from app.demo.server import DemoApp, _static

S01 = (
    "Can an employee upload confidential customer information to a personal cloud-storage account?"
)


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    return DemoApp(
        mode="replay", env={}, policy_review_path=tmp_path_factory.mktemp("r") / "rev.jsonl"
    )


def test_describe_lists_curated_golden_questions(app):
    env = app.policy_describe()
    assert env["mode_label"] == "REPLAY"
    ex = env["data"]["examples"]
    assert [e["id"] for e in ex] == [e["id"] for e in EXAMPLES] and ex[0]["question"] == S01
    assert env["data"]["levels"] == ["naive", "advanced", "agentic"]


@pytest.mark.parametrize("level", ["naive", "advanced", "agentic"])
def test_replayed_primary_question_at_every_level(app, level):
    env = app.policy_ask({"question": S01, "level": level})
    a = env["data"]
    assert env["data_class"] == "REPLAY" and a["mode"] == "replay"
    assert a["status"] == "ANSWERED" and a["recorded_question"] is True
    assert a["citations"] and all(c["verified"] for c in a["claims"])
    assert (a["agent"] is not None) == (level == "agentic")


def test_foundry_agent_backend_replays_its_own_recording(app):
    a = app.policy_ask({"question": S01, "level": "agentic", "backend": "foundry-service"})["data"]
    assert a["backend"] == "foundry-service" and a["agent"]["backend"] == "foundry-service"
    assert a["status"] == "ANSWERED" and a["mode"] == "replay"


def test_unrecorded_question_is_unavailable_not_invented(app):
    a = app.policy_ask({"question": "Is our office dog policy written down anywhere?"})["data"]
    assert a["recorded_question"] is False
    assert a["status"] in ("UNAVAILABLE", "INSUFFICIENT_EVIDENCE") and a["claims"] == []


def test_injection_is_blocked_and_queued_for_review(app):
    q = [e for e in app.policy_describe()["data"]["examples"] if e["id"] == "X01"][0]["question"]
    a = app.policy_ask({"question": q, "level": "advanced"})["data"]
    assert a["status"] == "BLOCKED" and "guardrail_injection" in a["review"]["reasons"]
    pending = app.policy_review()["data"]["pending"]
    assert any(p["request_id"] == a["request_id"] for p in pending)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"question": "  "},
        {"question": "x" * 3000},
        {"question": "ok?", "level": "turbo"},
        {"question": "ok?", "backend": "rogue"},
    ],
)
def test_bad_requests_are_rejected(app, body):
    with pytest.raises(DemoError):
        app.policy_ask(body)


def test_review_decision_is_logged_demo_only(app, tmp_path):
    local = DemoApp(mode="replay", env={}, policy_review_path=tmp_path / "rev.jsonl")
    c01 = [e for e in local.policy_describe()["data"]["examples"] if e["id"] == "C01"][0][
        "question"
    ]
    a = local.policy_ask({"question": c01, "level": "advanced"})["data"]
    assert a["status"] == "CONFLICT_REVIEW"
    with pytest.raises(DemoError):
        local.policy_review_decide({"request_id": a["request_id"], "action": "approve_everything"})
    out = local.policy_review_decide(
        {"request_id": a["request_id"], "action": "escalate_to_policy_owner", "note": "AUP owner"}
    )
    rec = json.loads((tmp_path / "rev.jsonl").read_text().splitlines()[0])
    assert rec["not_gold_adjudication"] is True and rec["action"] == "escalate_to_policy_owner"
    assert out["data_class"] == "DEMO-ONLY STATE"
    assert all(p["request_id"] != a["request_id"] for p in local.policy_review()["data"]["pending"])
    with pytest.raises(
        DemoError
    ):  # a decided item cannot be decided twice... it is no longer pending
        local.policy_review_decide({"request_id": "nope", "action": "confirm"})


def test_summary_reads_committed_results(app):
    s = app.policy_describe()["data"]["summary"]
    assert set(s["answers"]["levels"]) == {"naive", "advanced", "agentic"}
    assert s["answers"]["levels"]["advanced"]["metrics"]["status_accuracy"] == pytest.approx(
        35 / 36, abs=1e-3
    )
    assert "advanced" in s["retrieval"]["variants"] and len(s["guardrails"]) == 8
    assert s["observability"]["privacy_audit"]["clean"] is True
    fe = s["foundry_evals"]
    assert fe["outcomes"]["per_criterion"]["status_ok"]["passed"] == 101


def test_policy_page_is_served_and_registered():
    js = _static("policy.js").decode()
    assert "PAGES.policy" in js and ".innerHTML" not in js
    html = _static("index.html").decode()
    assert "/static/policy.js" in html and 'data-page="policy"' in html
