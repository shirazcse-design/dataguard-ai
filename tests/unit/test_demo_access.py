"""The demo's Access Governance (UC3) endpoints: curated requests, the real pipeline in REPLAY,
input validation, the Foundry-recorded restriction, the demo-only APPROVE / REJECT / MODIFY log
(nothing provisioned), and the static page."""

from __future__ import annotations

import json
import re

import pytest

from app.demo.access import ACTIONS, EXAMPLES, FOUNDRY_RECORDED
from app.demo.classify import DemoError
from app.demo.server import DemoApp, _static


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    return DemoApp(
        mode="replay", env={}, access_review_path=tmp_path_factory.mktemp("a") / "r.jsonl"
    )


def test_describe_lists_curated_requests_without_labels(app):
    env = app.access_describe()
    ex = env["data"]["examples"]
    assert env["mode_label"] == "REPLAY" and [e["id"] for e in ex] == [e["id"] for e in EXAMPLES]
    assert (
        ex[0]["id"] == "AR-002"
        and ex[0]["request"]["resource_name"] == "Customer production database"
    )
    blob = json.dumps(ex)
    assert "expected_outcome" not in blob and "acceptable" not in blob


def test_a_request_runs_the_real_pipeline_and_provisions_nothing(app):
    r = app.access_investigate({"request_id": "AR-008"})["data"]
    assert r["decision"]["outcome"] == "RECOMMEND_REJECT" and r["decision"]["provisioned"] is False
    assert r["facts"]["sod"] and "messages" not in json.dumps(r)


def test_inputs_and_unrecorded_foundry_runs_are_refused(app):
    for body in (
        {"request_id": "AR-999"},
        {"request_id": "AR-002", "backend": "rogue"},
        {"request_id": "AR-008", "backend": "foundry-service"},
    ):
        with pytest.raises(DemoError):
            app.access_investigate(body)
    assert "AR-008" not in FOUNDRY_RECORDED


def test_approver_decisions_are_demo_only(tmp_path):
    local = DemoApp(mode="replay", env={}, access_review_path=tmp_path / "r.jsonl")
    local.access_investigate({"request_id": "AR-012"})
    (p,) = local.access_review()["data"]["pending"]
    with pytest.raises(DemoError):  # MODIFY outside the entitlement's family
        local.access_review_decide(
            {
                "key": p["key"],
                "action": "MODIFY",
                "modified": {"entitlement_id": "platform_admin", "duration_days": 5},
            }
        )
    with pytest.raises(DemoError):
        local.access_review_decide({"key": p["key"], "action": "GRANT_NOW"})
    rec = local.access_review_decide(
        {
            "key": p["key"],
            "action": "MODIFY",
            "modified": {"entitlement_id": "partner_share_read", "duration_days": 7},
        }
    )
    row = json.loads((tmp_path / "r.jsonl").read_text().splitlines()[0])
    assert (
        row["action"] == "MODIFY"
        and row["provisioned"] is False
        and row["simulated"]
        and row["not_gold_adjudication"]
    )
    assert rec["data_class"] == "DEMO-ONLY STATE" and ACTIONS == ("APPROVE", "REJECT", "MODIFY")


def test_a_low_risk_approval_needs_no_approver(tmp_path):
    local = DemoApp(mode="replay", env={}, access_review_path=tmp_path / "r.jsonl")
    r = local.access_investigate({"request_id": "AR-001"})["data"]
    assert r["decision"]["hitl_required"] is False and not local.access_review()["data"]["pending"]


def test_page_is_served_registered_and_safe():
    js = _static("access.js").decode()
    assert (
        "PAGES.access" in js
        and ".innerHTML" not in js
        and 'JUMP_TARGETS.push(["Access Governance"' in js
    )
    html = _static("index.html").decode()
    assert "/static/access.js" in html and 'data-page="access"' in html

    def names(src: str) -> set[str]:
        return set(re.findall(r"^(?:const|let|function|async function)\s+(\w+)", src, re.M))

    others = set().union(
        *(names(_static(f).decode()) for f in ("app.js", "policy.js", "dlp.js", "insider.js"))
    )
    assert not names(js) & others
