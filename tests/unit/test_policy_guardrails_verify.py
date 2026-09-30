"""UC6 guardrails verification: provider-verdict parsing (codes/flags only, no text), the local G8
fabricated-citation case, the report, and recorders that capture a content-filter verdict."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

from evals.policy import guardrails_verify as gv

BLOCK = {"error": {"code": "content_filter", "message": "The prompt SECRETPROMPT was filtered",
                   "innererror": {"code": "ResponsibleAIPolicyViolation",
                                  "content_filter_result": {"jailbreak": {"filtered": True, "detected": True},
                                                            "violence": {"filtered": False, "severity": "safe"}}}}}  # fmt: skip


def test_error_verdict_keeps_codes_and_flags_but_no_text():
    v = gv._error_verdict(400, BLOCK)
    assert v == {
        "outcome": "blocked",
        "http": 400,
        "code": "content_filter",
        "flags": ["jailbreak:detected+filtered"],
    }
    assert "SECRETPROMPT" not in json.dumps(v)


def test_other_error_shapes_list_only_categories_that_triggered():
    body = {"code": "content_filter", "content_filters": [
        {"indirect_attack": {"filtered": True, "detected": True}},
        {"hate": {"filtered": False, "severity": "safe"}, "violence": {"filtered": False}}]}  # fmt: skip
    v = gv._error_verdict(400, body)
    assert v["outcome"] == "blocked" and v["flags"] == ["indirect_attack:detected+filtered"]


def test_g8_fabricated_citations_are_stopped_by_verification():
    r = gv.g8_fabricated_citation()
    assert r["stopped_by"] == "app_citation_verification" and r["status"] == "INSUFFICIENT_EVIDENCE"
    assert set(r["dropped"]) == {"fabricated_evidence_id"}


def test_recording_opener_captures_the_verdict_and_reraises():
    rec = gv.RecordingOpener()

    def boom(request, timeout):
        raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(json.dumps(BLOCK).encode()))

    orig = urllib.request.urlopen
    urllib.request.urlopen = boom
    try:
        try:
            rec(object(), 5)
        except urllib.error.HTTPError as err:
            assert err.code == 400 and json.loads(err.read())["error"]["code"] == "content_filter"
    finally:
        urllib.request.urlopen = orig
    assert rec.last["outcome"] == "blocked"


def test_report_and_verdicts():
    row = {"id": "G1", "name": "x", "app_advanced": {"status": "BLOCKED", "stopped_by": "app_input_guard", "stages": [], "citations": [], "foundry": None},
           "app_agentic": {"status": "UNAVAILABLE", "stopped_by": "foundry", "stages": [], "citations": [], "foundry": gv._error_verdict(400, BLOCK)},
           "direct_model": {"outcome": "blocked", "http": 400, "code": "content_filter", "flags": ["jailbreak:detected+filtered"]},
           "direct_agent": {"outcome": "passed", "stage": "user_input", "flags": []}}  # fmt: skip
    row["verdict"] = gv.verdict(row)
    assert (
        row["verdict"]
        == "application: app_input_guard, foundry; Foundry alone: blocked on 1 of 2 direct paths"
    )
    md = gv.render_markdown({"date": "2026-09-29", "cases": [row]})
    assert (
        "**app_input_guard**" in md
        and "jailbreak:detected+filtered" in md
        and "SECRETPROMPT" not in md
    )
