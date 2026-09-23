"""Batch Triage Agent evals: task completion, tool-call accuracy, and the scoped HHH/APF built from
a real `BatchTriageReport` (constructed directly here - no live agent run needed for these)."""

from __future__ import annotations

from app.agent.schemas import BatchTriageReport, DocumentAnnotation, ToolCallRecord
from evals.classification.agent_eval import (
    agent_apf,
    agent_hhh,
    safety_invariant_compliance,
    task_completion,
    tool_call_accuracy,
)


def ann(
    doc_id, *, status="ok", high_risk=False, review_reason=None, n_calls=1
) -> DocumentAnnotation:
    return DocumentAnnotation(
        doc_id=doc_id,
        request_id=doc_id,
        status=status,
        high_risk=high_risk,
        review_requested=review_reason is not None or status == "review_required",
        review_reason=review_reason,
        tool_calls=[ToolCallRecord(step=i + 1, tool="classify_document", ok=True) for i in range(n_calls)],
    )  # fmt: skip


def report(*anns) -> BatchTriageReport:
    return BatchTriageReport(
        agent_config_version="1.0.0", n_documents=len(anns), documents=list(anns)
    )


# ---- task_completion ----------------------------------------------------------------------
def test_task_completion_is_perfect_when_every_input_doc_is_reported():
    r = report(ann("d1"), ann("d2"))
    out = task_completion(r, ["d1", "d2"])
    assert out["rate"] == 1.0 and out["missing_doc_ids"] == []


def test_task_completion_flags_a_missing_document_by_id():
    r = report(ann("d1"))
    out = task_completion(r, ["d1", "d2"])
    assert out["missing_doc_ids"] == ["d2"] and out["rate"] == 0.5


def test_task_completion_on_an_empty_input_set_is_perfect_not_a_division_error():
    assert task_completion(report(), [])["rate"] == 1.0


# ---- tool_call_accuracy ----------------------------------------------------------------------
def test_tool_call_accuracy_counts_the_four_outcomes():
    r = report(
        ann("d1", high_risk=True, review_reason="flagged"),  # tp
        ann("d2", high_risk=False, review_reason="over-cautious"),  # fp
        ann("d3", status="review_required", review_reason=None),  # fn
        ann("d4", status="ok", high_risk=False, review_reason=None),  # tn
    )
    out = tool_call_accuracy(r)
    assert (out["tp"], out["fp"], out["fn"], out["tn"]) == (1, 1, 1, 1)
    assert out["precision"] == 0.5 and out["recall"] == 0.5


def test_tool_call_accuracy_reports_none_precision_when_the_agent_never_requests_review():
    r = report(ann("d1", status="ok", high_risk=False))
    out = tool_call_accuracy(r)
    assert out["precision"] is None and out["recall"] is None  # no positives at all, not 0/0


# ---- safety_invariant_compliance --------------------------------------------------------------
def test_safety_invariant_compliance_is_stated_as_structural_not_measured():
    out = safety_invariant_compliance()
    assert out["rate"] == 1.0 and "structural" in out["method"]


# ---- agent_hhh / agent_apf ----------------------------------------------------------------------
def test_agent_hhh_helpful_tracks_completion_and_harmless_is_perfect_by_construction():
    r = report(ann("d1"))
    out = agent_hhh(r, ["d1", "d2"])
    assert out["helpful"] == 0.5 and out["harmless"] == 1.0 and out["honest"] is None


def test_agent_apf_composite_averages_only_the_present_dimensions():
    r = report(ann("d1", n_calls=2), ann("d2", n_calls=2))
    out = agent_apf(r, ["d1", "d2"], step_budget=6)
    assert out["reliability"] is None
    assert out["effectiveness"] == 1.0 and out["efficiency"] == 1.0
    assert (
        out["composite"] == (out["effectiveness"] + out["efficiency"] + out["trustworthiness"]) / 3
    )


def test_agent_apf_efficiency_penalizes_documents_that_used_more_steps_than_the_budget():
    r = report(ann("d1", n_calls=1), ann("d2", n_calls=10))
    out = agent_apf(r, ["d1", "d2"], step_budget=6)
    assert out["efficiency"] == 0.5
