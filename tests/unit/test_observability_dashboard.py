"""The static observability dashboard (DG-018, offline): correct numbers, escaping, no leakage."""

from __future__ import annotations

import re

from app.classification import cli
from app.classification.service import ClassificationService
from observability import read_jsonl, render_dashboard, summarize

RECORD = "Employee record\nNational ID: 905-37-6209\nDate of birth: 01/27/1960\n"


def traced_summary(tmp_path):
    trace = tmp_path / "spans.jsonl"
    svc = ClassificationService(llm_mode="off", trace_path=trace)
    for i in range(3):
        svc.classify_text(RECORD, filename="employee_record.txt", request_id=f"r{i}", mode="rules")
    svc.classify_text("", request_id="empty")  # rejected
    return trace, summarize(read_jsonl(trace))


def test_the_dashboard_shows_the_computed_numbers(tmp_path):
    _, s = traced_summary(tmp_path)
    page = render_dashboard(s)
    assert f">{s['n_traces']}<" in page
    for status, n in s["status_distribution"].items():
        assert status in page and f">{n}<" in page.replace(" ", "")
    for stage in s["stage_latency"]:
        assert stage in page


def test_the_page_is_self_contained_with_no_script_or_external_load(tmp_path):
    _, s = traced_summary(tmp_path)
    page = render_dashboard(s)
    assert "<script" not in page and not re.search(r"(src|href)=[\"']?https?:", page)
    assert "@import" not in page and "url(" not in page


def test_hostile_labels_are_escaped():
    s = summarize([])
    s["route_distribution"] = {"<img src=x onerror=alert(1)>": 2}
    s["guardrail_triggers"] = {"</td><script>alert(1)</script>": 1}
    page = render_dashboard(s, title="<b>t</b>")
    assert "<img" not in page and "<script" not in page and "<b>t</b>" not in page
    assert "&lt;img" in page


def test_an_empty_summary_renders_without_error_and_says_so():
    page = render_dashboard(summarize([]))
    assert "n/a" in page and "none recorded" in page and "no stage spans" in page


def test_the_dashboard_from_real_spans_contains_no_document_text(tmp_path):
    trace, _ = traced_summary(tmp_path)
    out = tmp_path / "d.html"
    assert cli.main(["obs", "dashboard", "--spans", str(trace), "--out", str(out)]) == 0
    page = out.read_text()
    assert "905-37-6209" not in page and "Employee record" not in page


def test_dropped_attributes_are_surfaced_not_hidden():
    s = summarize([])
    s["dropped_attributes"] = 7
    assert "7 span attributes were dropped" in render_dashboard(s)
