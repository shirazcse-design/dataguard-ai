"""Classify + Decision Trace for the demo dashboard (`app/demo/classify.py`, `examples.py`).

Every classification here goes through the REAL `ClassificationService` in replay mode (recorded
LLM responses; no network). The display mapping is checked against the frozen schema's own golden
examples, one per status.
"""

from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app.demo import examples
from app.demo.classify import ClassifySession, summarize
from app.demo.server import DemoApp, build_server

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "docs/uc4/schema/examples"


@pytest.fixture(scope="module")
def session():
    return ClassifySession("replay")


# ---- the display summary maps the frozen schema, and never promotes a failure --------------------
@pytest.mark.parametrize(
    ("name", "outcome"),
    [
        ("ok-hybrid.json", "success"),
        ("ok-rules.json", "success"),
        ("degraded.json", "success"),
        ("review-required.json", "review"),
        ("rejected.json", "failure"),
        ("error.json", "failure"),
    ],
)
def test_every_golden_status_maps_to_the_right_outcome(name, outcome):
    result = json.loads((GOLDEN / name).read_text(encoding="utf-8"))["result"]
    sm = summarize(result)
    assert sm["status"] == result["status"] and sm["outcome"] == outcome
    if outcome == "failure":
        assert sm["level"] is None and sm["failure_reason"]  # a failure never shows a level
    if outcome == "review":
        assert sm["review_required"] and sm["review_reasons"]
    if result["status"] == "degraded":
        assert sm["degraded"] is True
    if result.get("level"):
        assert sm["level"] == result["level"]["value"]
        assert sm["level_decided_by"] == result["level"]["decided_by"]


@pytest.mark.parametrize("status", ["rejected", "error", "something_new", ""])
def test_a_failed_or_unknown_status_is_never_shown_as_success(status):
    fake = {"status": status, "level": {"value": "PUBLIC", "decided_by": "rules", "confidence": {}}}
    assert summarize(fake)["outcome"] == "failure"


# ---- curated examples behave exactly as documented --------------------------------------------
@pytest.mark.parametrize("ex", examples.EXAMPLES, ids=lambda e: e.key)
def test_each_curated_example_produces_its_documented_outcome(session, ex):
    p = session.classify({"example": ex.key})
    assert p["summary"]["status"] == ex.expect_status
    assert p["summary"]["level"] == ex.expect_level
    assert p["result"]["request_id"] == p["request_id"]


def test_examples_are_real_dev_split_documents():
    for item in examples.listing():
        assert item["doc_id"].startswith("uc4-") and item["content"]
    assert {e.key for e in examples.EXAMPLES} >= {
        "public",
        "internal",
        "confidential",
        "healthcare",
        "review",
    }


# ---- the decision trace shows what really ran -----------------------------------------------
def stages(p):
    return {s["name"]: s for s in p["trace"]["stages"]}


def test_healthcare_trace_shows_rules_llm_mid_and_the_disabled_ml_stage(session):
    st = stages(session.classify({"example": "healthcare"}))
    assert st["Rules"]["state"] == "executed" and st["Rules"]["detail"] == "decisive level found"
    assert st["ML classifier"]["state"] == "not_in_variant"  # from the routing config, not assumed
    mid = st["LLM tier: mid"]
    assert (
        mid["state"] == "executed" and mid["replayed"] is True and mid["model"] == "uc4-llm-medium"
    )
    assert "recorded latency" in mid["latency_note"]
    assert st["LLM tier: large"]["state"] == "not_needed"
    assert st["Review decision"]["state"] == "executed"


def test_the_review_example_shows_the_llm_as_not_used_and_the_escalation(session):
    p = session.classify({"example": "review"})
    st = stages(p)
    assert st["LLM tier: mid"]["state"] == "disabled_by_request"
    assert st["LLM tier: large"]["state"] == "disabled_by_request"
    assert st["Review decision"]["state"] == "escalated"
    assert p["summary"]["outcome"] == "review" and p["summary"]["level"] is None


def test_new_text_in_replay_is_an_honest_replay_miss_not_a_success(session):
    p = session.classify({"text": "Quarterly offsite agenda: team lunch and planning."})
    assert p["replay_miss"] is True
    assert p["summary"]["outcome"] == "review"
    st = stages(p)
    assert st["LLM tier: mid"]["state"] == "failed" and st["LLM tier: mid"]["replay_miss"] is True


def test_trace_spans_carry_no_document_text(session):
    ex = examples.BY_KEY["healthcare"]
    content = examples.document(ex).content
    p = session.classify({"example": "healthcare"})
    blob = json.dumps(p["trace"]["spans"])
    for line in content.splitlines():
        if len(line.strip()) > 8:
            assert line.strip() not in blob
    assert "Theo Bianchi" not in blob and "MRN-9952462" not in blob
    assert all(k.startswith("dg.") for s in p["trace"]["spans"] for k in s["attributes"])


# ---- uploads use the service's own rejection path ------------------------------------------
def test_an_undecodable_upload_is_rejected_by_the_service_not_guessed(session):
    p = session.classify_upload("x.bin", b"\xff\xfe\x00not utf-8")
    assert p["summary"]["outcome"] == "failure"
    assert p["summary"]["failure_reason"] == "input_rejected:undecodable_text"


def test_an_oversize_upload_is_rejected(session):
    big = b"a" * (session.service.max_document_bytes + 1)
    p = session.classify_upload("big.txt", big)
    assert p["summary"]["status"] == "rejected" and p["summary"]["outcome"] == "failure"


def test_bad_request_options_are_refused(session):
    from app.demo.classify import DemoError

    with pytest.raises(DemoError):
        session.classify({"example": "nope"})
    with pytest.raises(DemoError):
        session.classify({"text": "x", "llm_tiers": "sometimes"})
    with pytest.raises(DemoError):
        session.classify({"text": 5})


# ---- over HTTP ------------------------------------------------------------------------------
@pytest.fixture()
def serve():
    servers = []

    def start(app: DemoApp) -> str:
        srv = build_server(app, port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def post(url: str, body: bytes, ctype: str = "application/json"):
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_classify_over_http_is_labelled_replay_everywhere(serve):
    base = serve(DemoApp(mode="replay", env={}))
    status, payload = post(base + "/api/classify", json.dumps({"example": "public"}).encode())
    assert status == 200
    assert payload["mode_label"] == "REPLAY" and payload["data_class"] == "REPLAY"
    assert payload["data"]["llm_mode"] == "replay"
    assert payload["data"]["summary"]["level"] == "PUBLIC"


def test_a_live_server_that_cannot_start_the_classifier_says_so_and_never_falls_back(serve):
    base = serve(DemoApp(mode="live", env={}))  # no Foundry credentials in the environment
    status, payload = post(base + "/api/classify", json.dumps({"example": "public"}).encode())
    assert status == 503
    assert payload["mode_label"] == "LIVE" and "did not start" in payload["error"]
    assert payload["data"] is None  # no replay result slipped in


def test_upload_over_http_and_body_limits(serve):
    base = serve(DemoApp(env={}))
    data = base64.b64encode(b"Team offsite notes").decode()
    status, payload = post(
        base + "/api/classify-upload", json.dumps({"name": "n.txt", "data_base64": data}).encode()
    )
    assert status == 200 and payload["data"]["summary"]["outcome"] in ("success", "review")
    status, _ = post(
        base + "/api/classify-upload", json.dumps({"name": "n.txt", "data_base64": "@@@"}).encode()
    )
    assert status == 400
    status, _ = post(base + "/api/classify", b"not json")
    assert status == 400
    status, _ = post(base + "/api/nope", b"{}")
    assert status == 404


def test_an_oversize_body_is_refused_before_it_is_read(serve):
    from app.demo.server import MAX_BODY

    base = serve(DemoApp(env={}))
    req = urllib.request.Request(
        base + "/api/classify", data=b"{}", method="POST",
        headers={"Content-Type": "application/json", "Content-Length": str(MAX_BODY + 1)},
    )  # fmt: skip
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(req, timeout=10)
    assert info.value.code == 413


def test_no_secret_value_reaches_a_classify_payload(serve):
    secret = "sk-demo-secret-987"
    env = {"DATAGUARD_FOUNDRY_API_KEY": secret, "DATAGUARD_AZURE_MONITOR_CONNECTION_STRING": secret}
    base = serve(DemoApp(mode="replay", env=env))
    _, payload = post(base + "/api/classify", json.dumps({"example": "healthcare"}).encode())
    assert secret not in json.dumps(payload)
