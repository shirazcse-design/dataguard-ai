"""Agent Triage and Human Review for the demo dashboard (`app/demo/agent.py`, `review.py`).

Agent runs use the REAL Batch Triage Agent with the offline planner and the real classifier in
replay mode (no network). Reviewer actions go to a temporary file, never the repository.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from app.demo import examples
from app.demo.agent import AGENT_BATCH, agent_view
from app.demo.classify import DemoError
from app.demo.server import DemoApp, build_server


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    return DemoApp(
        mode="replay", env={}, review_path=tmp_path_factory.mktemp("rv") / "reviews.jsonl"
    )


# ---- the agent ---------------------------------------------------------------------------------
def test_the_replay_planner_is_labelled_as_no_model_and_never_live(app):
    d = app.agent_describe()
    assert d["mode_label"] == "REPLAY" and d["data_class"] == "REPLAY"
    assert d["data"]["planner_is_model"] is False
    assert "no model" in d["data"]["planner"] and "LIVE" not in d["data"]["planner"]
    assert [b["key"] for b in d["data"]["batch"]] == [b.key for b in AGENT_BATCH]


@pytest.mark.parametrize("key", [b.key for b in AGENT_BATCH])
def test_every_batch_document_runs_the_real_loop_and_holds_the_invariant(app, key):
    v = app.agent_run({"doc": key})["data"]
    assert v["annotation"]["stopped_reason"] == "completed"
    assert [(s["kind"], s.get("tool") or s.get("turn")) for s in v["steps"]] == [
        ("planner", "tool_calls"), ("tool", "classify_document"), ("planner", "final"),
    ]  # fmt: skip
    assert v["invariant_violated"] is False and all(c["holds"] for c in v["checks"])


def test_the_injection_document_shows_the_real_guardrail_event(app):
    v = app.agent_run({"doc": "injection-exfil"})["data"]
    assert v["guardrail_events"] == [
        {
            "type": "prompt_injection_suspected",
            "trigger": "inj.system_override",
            "action": "continued_as_data",
        }
    ]
    assert (
        v["annotation"]["level"] == "HIGHLY_CONFIDENTIAL"
    )  # the injection did not change the decision


def test_an_invariant_violation_is_reported_never_hidden(app):
    v = app.agent_run({"doc": "healthcare"})["data"]
    from observability import Span

    spans = [Span.model_validate({**s, "trace_id": "0" * 32, "span_id": f"{i:016x}", "start_ns": i,
                                  "end_ns": i + 1}) for i, s in enumerate(_as_spans(v["spans"]))]  # fmt: skip
    tampered = {**v["annotation"], "level": "PUBLIC"}  # the agent claims a lower level
    bad = agent_view(tampered, spans)
    assert bad["invariant_violated"] is True
    assert bad["checks"][0]["holds"] is False and "PUBLIC" in bad["checks"][0]["detail"]


def _as_spans(views):
    return [
        {"name": s["name"], "status": s["status"], "attributes": s["attributes"]} for s in views
    ]


def test_agent_spans_carry_no_document_text(app):
    v = app.agent_run({"doc": "healthcare"})["data"]
    blob = json.dumps(v["spans"])
    content = examples._dev_docs()["uc4-19c5bd1307"].content
    assert "Theo Bianchi" not in blob and "sertraline" not in blob
    assert content[:40] not in blob


def test_an_unknown_batch_document_is_refused(app):
    with pytest.raises(DemoError):
        app.agent_run({"doc": "../../etc/passwd"})


def test_a_live_agent_without_configuration_fails_loudly_and_never_uses_the_offline_planner(
    tmp_path,
):
    live = DemoApp(mode="live", env={}, review_path=tmp_path / "r.jsonl")
    with pytest.raises(DemoError) as info:
        live.agent_run({"doc": "public"})
    assert info.value.status == 503


# ---- the review queue ------------------------------------------------------------------------
def test_only_results_the_service_sent_to_review_are_queued(tmp_path):
    a = DemoApp(mode="replay", env={}, review_path=tmp_path / "r.jsonl")
    ok = a.classify({"example": "healthcare"})["data"]
    assert ok["queued_for_review"] is None
    rv = a.classify({"example": "review"})["data"]
    assert rv["queued_for_review"] == rv["request_id"]
    items = a.review_queue()["data"]["items"]
    assert [i["id"] for i in items] == [rv["request_id"]]
    assert items[0]["ai_level"] is None and items[0]["reasons"] == ["LOW_CONFIDENCE"]


def test_seeding_adds_real_fail_safe_reviews(tmp_path):
    a = DemoApp(mode="replay", env={}, review_path=tmp_path / "r.jsonl")
    added = a.review_seed({})["data"]["added"]
    assert len(added) == 2
    assert all(i["status"] == "open" for i in a.review_queue()["data"]["items"])


def test_decisions_are_demo_only_records_in_their_own_file(tmp_path):
    path = tmp_path / "r.jsonl"
    a = DemoApp(mode="replay", env={}, review_path=path)
    item = a.review_seed({})["data"]["added"][0]
    out = a.review_decide(
        {"id": item, "action": "override", "level": "CONFIDENTIAL", "note": "draft"}
    )
    assert out["data_class"] == "DEMO-ONLY STATE"
    assert out["data"]["item"]["status"] == "overridden"
    (rec,) = [json.loads(line) for line in path.read_text().splitlines()]
    assert rec["record_type"] == "demo_reviewer_decision" and rec["not_gold_adjudication"] is True
    assert rec["final_level"] == "CONFIDENTIAL" and rec["ai_level"] is None


def test_review_actions_are_validated(tmp_path):
    a = DemoApp(mode="replay", env={}, review_path=tmp_path / "r.jsonl")
    item = a.review_seed({})["data"]["added"][0]
    with pytest.raises(DemoError, match="no AI label"):
        a.review_decide({"id": item, "action": "approve"})  # nothing to approve
    with pytest.raises(DemoError, match="override needs a level"):
        a.review_decide({"id": item, "action": "override", "level": "SECRET"})
    with pytest.raises(DemoError, match="action must be"):
        a.review_decide({"id": item, "action": "delete"})
    with pytest.raises(DemoError, match="at most"):
        a.review_decide({"id": item, "action": "escalate", "note": "x" * 501})
    with pytest.raises(DemoError) as info:
        a.review_decide({"id": "nope", "action": "escalate"})
    assert info.value.status == 404
    assert not (tmp_path / "r.jsonl").exists()  # nothing was written by refused actions


# ---- over HTTP -------------------------------------------------------------------------------
def test_agent_and_review_endpoints_over_http(tmp_path):
    srv = build_server(DemoApp(mode="replay", env={}, review_path=tmp_path / "r.jsonl"), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:

        def call(path, body=None):
            data = None if body is None else json.dumps(body).encode()
            req = urllib.request.Request(base + path, data=data, method="GET" if body is None else "POST",
                                         headers={"Content-Type": "application/json"})  # fmt: skip
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        status, payload = call("/api/agent/run", {"doc": "public"})
        assert status == 200 and payload["mode_label"] == "REPLAY"
        assert payload["data"]["annotation"]["level"] == "PUBLIC"
        status, _ = call("/api/agent/run", {"doc": "nope"})
        assert status == 400
        status, seeded = call("/api/review/seed", {})
        assert status == 200 and len(seeded["data"]["added"]) == 2
        status, q = call("/api/review")
        assert (
            status == 200 and q["data_class"] == "DEMO-ONLY STATE" and len(q["data"]["items"]) == 2
        )
        status, _ = call("/api/review/decide", {"id": "nope", "action": "escalate"})
        assert status == 404
    finally:
        srv.shutdown()
        srv.server_close()


def test_seeding_twice_does_not_duplicate_and_paths_are_not_absolute(tmp_path):
    from app.demo import review

    a = DemoApp(mode="replay", env={}, review_path=review.REPO / "var" / "demo" / "test-seed.jsonl")
    assert len(a.review_seed({})["data"]["added"]) == 2
    assert a.review_seed({})["data"]["added"] == []
    assert len(a.review_queue()["data"]["items"]) == 2
    assert a.review_queue()["data"]["stored_at"] == "var/demo/test-seed.jsonl"
