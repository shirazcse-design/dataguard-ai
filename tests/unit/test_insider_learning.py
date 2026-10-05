"""UC2 controlled learning loop (evals/insider/learning_loop.py): offline, governed, and never
self-modifying. The runtime does not import the loop or read candidates; the gate rejects unsafe
candidates; safety-sensitive patterns never become automatic candidates; approval deploys nothing."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from app.insider.harness import load_rubric
from evals.insider import learning_loop as ll

CANDIDATE = ll.CANDIDATES / "risk.v1.3.0-candidate.yaml"


@pytest.fixture(scope="module")
def base_rows():
    return ll.replay_outcomes(load_rubric())


def test_runtime_never_imports_the_loop_or_reads_candidates():
    for f in (ll.REPO / "app").rglob("*.py"):
        if f.name == "cli.py":  # the offline `learn` command is tooling, not the runtime path
            continue
        src = f.read_text(encoding="utf-8")
        assert "learning_loop" not in src and "candidates/" not in src, f
    assert (
        load_rubric()["rubric_version"] == "1.2.0" and "agent_review_request" not in load_rubric()
    )


def test_mining_reads_labels_and_flags_safety_sensitive_patterns():
    pats = {f"{p['signal']}:{p['key']}": p for p in ll.mine(ll.collect())}
    assert pats["false_review:review:agent_disagreement"]["cases"] == ["I05", "I11"]
    assert not pats["false_review:review:agent_disagreement"]["safety_sensitive"]
    assert pats["analyst_disagree_lower:outcome:ESCALATE"][
        "safety_sensitive"
    ]  # I21: lower an escalation
    assert all(p["safety_sensitive"] for k, p in pats.items() if k.startswith("over_escalation"))


def test_candidate_passes_the_gate_and_changes_only_what_it_targets(base_rows):
    cand = ll.replay_outcomes(ll.load_candidate(CANDIDATE))
    g = ll.gate(base_rows, cand, ["I05", "I11"])
    assert g["verdict"] == "PASS" and g["in_sample_only"]
    assert {x["id"] for x in g["changed_cases"]} == {"I05", "I11"}
    assert all(x["after"] == "MONITOR" for x in g["changed_cases"])


def test_gate_rejects_a_candidate_that_weakens_safety(base_rows):
    bad = copy.deepcopy(load_rubric())
    bad["floors"] = []
    bad["bands"] = [
        {"min": 1000, "outcome": "ESCALATE"},
        {"min": 30, "outcome": "INVESTIGATE"},
        {"min": -1000, "outcome": "MONITOR"},
    ]
    g = ll.gate(base_rows, ll.replay_outcomes(bad), [])
    assert g["verdict"] == "FAIL"
    # The flagship still escalates here only because the risk agent recommended ESCALATE (a higher
    # recommendation is adopted); every other escalation that relied on the floors is caught.
    assert not g["checks"]["no_escalation_lowered"]


def test_safety_sensitive_or_unmined_targets_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(ll, "CANDIDATES", tmp_path)
    for target in ("over_escalation:band:HIGH_ANOMALY", "made_up:pattern"):
        c = yaml.safe_load(CANDIDATE.read_text(encoding="utf-8"))
        c["learning_loop"] = {"targets": target}
        p = tmp_path / "c.yaml"
        p.write_text(yaml.safe_dump(c), encoding="utf-8")
        with pytest.raises(ValueError):
            ll.run(p)
    with pytest.raises(ValueError):  # candidates must live in the candidates directory
        ll.load_candidate(Path(ll.RUNTIME_RUBRIC))


def test_loop_run_leaves_protected_files_unchanged():
    before = ll.protected_hashes()
    r = ll.run(CANDIDATE)
    assert r["status"] == "AWAITING_HUMAN_APPROVAL" and r["protected_files_unchanged"]
    assert ll.protected_hashes() == before
    assert any(k.startswith("prompts/uc2/") for k in before)
    assert all(e["status"] == "NEEDS_HUMAN_LABEL" for e in r["eval_candidates"])


def test_approval_needs_a_person_and_deploys_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(ll, "APPROVALS", tmp_path / "approvals.jsonl")
    before = ll.sha(ll.RUNTIME_RUBRIC)
    for bad in (("", "approve", "ok"), ("Reviewer", "approve", ""), ("Reviewer", "ship_it", "ok")):
        with pytest.raises(ValueError):
            ll.approve(CANDIDATE, *bad)
    rec = ll.approve(CANDIDATE, "Reviewer", "reject", "needs fresh labelled cases first")
    assert rec["deployed"] is False and rec["decision"] == "reject"
    assert (
        json.loads((tmp_path / "approvals.jsonl").read_text().splitlines()[0])["approver"]
        == "Reviewer"
    )
    assert ll.sha(ll.RUNTIME_RUBRIC) == before


def test_committed_report_matches_the_committed_candidate():
    r = json.loads((ll.RESULTS / "learning-loop.json").read_text(encoding="utf-8"))
    assert r["candidate"]["sha256"] == ll.sha(CANDIDATE) and r["gate"]["verdict"] == "PASS"
