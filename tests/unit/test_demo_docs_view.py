"""Overview / Responsible AI / Observability content for the demo (`app/demo/docs_view.py`), and the
interview mode's no-autoplay rule. Everything is read from committed documents and config; statuses
follow the documents, so changing a document changes the page."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from app.demo import docs_view as dv
from app.demo import metrics as m
from app.demo.server import DemoApp

REPO = Path(__file__).resolve().parents[2]
SOURCES = (
    m.COMPLETION_REPORT, m.RESPONSIBLE_AI, dv.DECISIONS, dv.OBS_ENGINE, dv.OBS_BASELINE,
    dv.OBS_CONFIG, ".github/workflows/ci.yml",
)  # fmt: skip


def copy_sources(tmp_path: Path) -> Path:
    for rel in SOURCES:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, tmp_path / rel)
    return tmp_path


# ---- Responsible AI ----------------------------------------------------------------------------
def test_classifier_hhh_and_apf_are_the_documents_own_results():
    r = dv.responsible_ai()
    hhh = {x["pillar"]: x["result"] for x in r["classifier_hhh"]}
    assert set(hhh) == {"Helpful", "Honest", "Harmless"}
    assert hhh["Helpful"].startswith("1.000 (97/97)")
    assert hhh["Harmless"].startswith("1.000 (111/111")
    apf = {x["dimension"]: x["result"] for x in r["classifier_apf"]}
    assert apf == {"Effectiveness": "0.957", "Efficiency": "0.695", "Reliability": "1.000",
                   "Trustworthiness": "1.000"}  # fmt: skip
    assert [p["pillar"] for p in r["pillars"]] == [
        "Privacy & Security", "Fairness & Inclusion", "Transparency & Control",
        "Robustness & Safety", "Governance & Accountability",
    ]  # fmt: skip


def test_the_fairness_probe_scope_is_stated_exactly_and_not_widened():
    f = dv.fairness()
    assert (f["documents"], f["substitute_names"], f["mode"], f["skipped"]) == (66, 10, "rules", 31)
    assert any("ML or LLM" in n for n in f["not_claimed"])


def statuses(root=REPO):
    return {g["name"]: g["status"] for g in dv.guardrails(root)}


def test_content_safety_is_fake_server_tested_while_its_live_verification_is_open():
    st = statuses()
    cs = next(k for k in st if k.startswith("Azure AI Content Safety second opinion"))
    assert st[cs] == "fake_server_tested"
    assert st["Per-request Content Safety blocking"] == "not_applicable"
    assert st["Span redaction + privacy audit"] == "ci_gate"


def test_closing_the_content_safety_item_is_what_flips_its_status(tmp_path):
    root = copy_sources(tmp_path)
    p = root / m.COMPLETION_REPORT
    text = p.read_text()
    old = "| 7 | **Azure AI Content Safety second opinion — live verification** |"
    assert old in text
    p.write_text(
        text.replace(
            old, "| 7 | ~~**Azure AI Content Safety second opinion — live verification**~~ |"
        )
    )
    cs = next(v for k, v in statuses(root).items() if k.startswith("Azure AI Content Safety"))
    assert cs == "live_verified"


def test_without_decision_a38_the_foundry_filter_is_unknown_not_assumed(tmp_path):
    root = copy_sources(tmp_path)
    p = root / dv.DECISIONS
    p.write_text(p.read_text().replace("CustomContentFilter412", "SomeOtherFilter"))
    assert (
        statuses(root)["Foundry content filter (Prompt Shields) on all 3 deployments"] == "unknown"
    )


def test_a_changed_fairness_statement_is_an_error(tmp_path):
    root = copy_sources(tmp_path)
    p = root / m.RESPONSIBLE_AI
    p.write_text(p.read_text().replace("substitute names", "alternative names"))
    with pytest.raises(m.ArtifactError):
        dv.fairness(root)


# ---- Observability ----------------------------------------------------------------------------
def test_foundry_tracing_status_comes_from_the_engine_doc_and_is_verified():
    o = dv.observability()
    got = {f["concern"]: f["status"] for f in o["foundry_status"]}
    assert set(got) == {
        "Azure Monitor export",
        "Foundry Trace view rendering",
        "Batch Triage Agent tracing",
    }
    assert got["Azure Monitor export"].startswith("Portal-confirmed")
    assert "live-verified" in got["Foundry Trace view rendering"].lower()


def test_the_allow_list_count_is_read_from_config_and_the_stale_baseline_header_is_flagged():
    o = dv.observability()
    cfg = yaml.safe_load((REPO / dv.OBS_CONFIG).read_text())
    assert o["allow_listed_keys"] == len(cfg["allowed_attributes"])
    assert o["baseline"]["header_predates_foundry_verification"] is True
    assert o["baseline"]["privacy_audit"][-2]["result"] in ("**0**", "0") or any(
        "leaks" in r["check"] for r in o["baseline"]["privacy_audit"]
    )
    assert {r["result"] for r in o["baseline"]["failure_matrix"]} == {"PASS"}


def test_session_traces_are_labelled_by_mode_and_carry_no_document_text(tmp_path):
    a = DemoApp(mode="replay", env={}, review_path=tmp_path / "r.jsonl")
    a.classify({"example": "healthcare"})
    a.agent_run({"doc": "pii-notes"})
    o = a.observability()
    assert o["mode_label"] == "REPLAY" and o["data"]["session"]["data_class"] == "REPLAY"
    traces = o["data"]["session"]["traces"]
    assert {t["root"] for t in traces} == {"classify", "agent.document"}
    blob = json.dumps(traces)
    assert "Theo Bianchi" not in blob and "sertraline" not in blob
    for t in traces:
        assert all(s["offset_ms"] >= 0 for s in t["spans"])


def test_the_rai_endpoint_is_static_documentation(tmp_path):
    a = DemoApp(mode="replay", env={}, review_path=tmp_path / "r.jsonl")
    r = a.rai()
    assert r["data_class"] == "STATIC DOCUMENTATION"
    assert r["data"]["agent"]["safety_invariant_compliance"] == 1.0


# ---- Interview mode never autoplays -----------------------------------------------------------
def test_interview_steps_navigate_and_preload_but_never_trigger_a_run():
    js = (REPO / "app/demo/static/app.js").read_text(encoding="utf-8")
    body = re.search(r"function goStep\(i\) \{(.*?)\n\}", js, re.S).group(1)
    assert "analyze(" not in body and "runAgentBatch(" not in body and "postJSON(" not in body
    steps = re.search(r"const DEMO_STEPS = \[(.*?)\n\];", js, re.S).group(1)
    assert steps.count("page:") == 9


def test_each_foundry_tracing_row_gets_the_verdict_its_own_text_records():
    got = {f["concern"]: f["verdict"] for f in dv.observability()["foundry_status"]}
    assert got["Azure Monitor export"] == "Portal-confirmed"
    assert got["Foundry Trace view rendering"] == "Live-verified in Foundry"
    assert got["Batch Triage Agent tracing"] == "Live-verified in Foundry"
    assert dv._verdict("SDK-confirmed, portal-confirmation pending") == "SDK-confirmed only"
    assert dv._verdict("instrumented") == "Not verified"


def test_baseline_cells_are_clean_text():
    rows = dv.observability()["baseline"]["privacy_audit"]
    assert all("**" not in r["check"] and "**" not in r["result"] for r in rows)
