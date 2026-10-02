"""UC1 guardrail verification runner, offline with a fake Agent Service client: verdicts are
recorded as codes, a Foundry block is attributed to Foundry, and no probe text is written."""

from __future__ import annotations

import json
from types import SimpleNamespace

from evals.dlp import guardrails_verify as gv


class Blocked(Exception):
    status_code = 400
    body = {"error": {"code": "content_filter", "innererror": {
        "content_filter_result": {"jailbreak": {"detected": True, "filtered": True}}}}}  # fmt: skip


def _final(outcome):
    text = json.dumps({"proposed_outcome": outcome, "findings": [], "missing_evidence": []})
    return SimpleNamespace(id="r", output=[], output_text=text, model="m",
                           usage=SimpleNamespace(input_tokens=1, output_tokens=1))  # fmt: skip


class FakeOpenAI:
    """Blocks any request whose input mentions ReviewBot; otherwise answers ESCALATE at once."""

    def __init__(self):
        self.responses = self

    def create(self, **kw):
        if "ReviewBot" in json.dumps(kw.get("input", ""), default=str):
            raise Blocked()
        return _final("ESCALATE")


def test_runner_attributes_layers_and_stores_no_probe_text(tmp_path):
    rows = gv.run(FakeOpenAI(), "uc4-llm-medium", "dataguard-dlp-investigator")
    by = {r["id"]: r for r in rows}
    assert set(by) == {p["id"] for p in gv.PROBES}
    assert by["GU1"]["app"]["stopped_by"] == "app_justification_withheld"
    assert by["GU2"]["direct"]["outcome"] == "blocked"
    assert "jailbreak" in " ".join(by["GU2"]["direct"]["flags"])
    assert by["GU5"]["app"]["guardrail_events"] == []  # no false positive from the app lexicon
    assert by["GU7"]["direct"]["outcome"] == "not_tested"  # fake agent calls no tool
    md = gv.render(rows, {"date": "test"})
    blob = json.dumps(rows) + md
    for p in gv.PROBES:
        if p["justification"]:
            assert p["justification"][:30] not in blob
    assert gv.POISON[:30] not in blob
