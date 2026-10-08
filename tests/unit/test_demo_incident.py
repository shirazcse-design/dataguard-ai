"""The demo's Incident Investigation (UC5) endpoints: curated incidents, the real pipeline in REPLAY,
the Foundry-recorded restriction, the demo-only analyst decisions (CRITICAL only here, nothing
executed), no golden labels in the payload, and the static page."""

from __future__ import annotations

import json

import pytest

from app.demo.classify import DemoError
from app.demo.incident import EXAMPLES, FOUNDRY_RECORDED
from app.demo.server import DemoApp, _static


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    return DemoApp(
        mode="replay", env={}, incident_review_path=tmp_path_factory.mktemp("i") / "r.jsonl"
    )


def test_describe_lists_curated_incidents_without_labels(app):
    env = app.incident_describe()
    ex = env["data"]["examples"]
    assert env["mode_label"] == "REPLAY" and [e["id"] for e in ex] == [e["id"] for e in EXAMPLES]
    blob = json.dumps(ex)
    assert "expected_severity" not in blob and "acceptable" not in blob and "u-20" not in blob


def test_flagship_replays_the_recorded_live_run(app):
    r = app.incident_investigate({"case_id": "INC-001"})["data"]
    d = r["decision"]
    assert d["severity"] == "HIGH" and d["review_status"] == "REQUIRED" and d["potential_sev1"]
    assert r["agent"]["stopped_reason"] == "final_answer" and r["remediation"].startswith(
        "NONE_EXECUTED"
    )
    assert r["timeline"] and r["correlations"] and {"data", "dlp", "policy"} <= set(r["evidence"])
    assert "messages" not in json.dumps(r) and "u-2043" not in json.dumps(r)


def test_foundry_backend_is_limited_to_recorded_cases(app):
    assert (
        app.incident_investigate({"case_id": FOUNDRY_RECORDED[0], "backend": "foundry-service"})[
            "data"
        ]["decision"]["severity"]
        == "HIGH"
    )
    with pytest.raises(DemoError):
        app.incident_investigate({"case_id": "INC-002", "backend": "foundry-service"})
    with pytest.raises(DemoError):
        app.incident_investigate({"case_id": "INC-999"})


def test_analyst_decisions_are_demo_only_and_critical_is_analyst_set(app):
    q = app.incident_review()["data"]
    key = q["pending"][0]["key"]
    with pytest.raises(DemoError):
        app.incident_review_decide({"key": key, "action": "MODIFY"})  # needs a severity
    rec = app.incident_review_decide(
        {"key": key, "action": "MODIFY", "severity": "CRITICAL", "note": "loss confirmed"}
    )["data"]["recorded"]
    assert (
        rec["severity"] == "CRITICAL"
        and rec["simulated"] is True
        and rec["remediation_executed"] is False
    )
    with pytest.raises(DemoError):
        app.incident_review_decide({"key": key, "action": "CONFIRM"})  # already decided


def test_static_page_is_served_and_safe():
    js = _static("incident.js").decode("utf-8")
    code = "\n".join(line for line in js.splitlines() if not line.lstrip().startswith("//"))
    assert "PAGES.incident" in code and "innerHTML" not in code  # DOM built with textContent only
    assert b"incident.js" in _static("index.html")
