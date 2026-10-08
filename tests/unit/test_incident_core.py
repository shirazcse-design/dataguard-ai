"""UC5 core: evidence model, provenance, timeline and correlation (pure functions, no services)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.incident.correlate import correlate, timeline
from app.incident.schemas import CLAIM_TYPES, EvidenceItem, IncidentReport


def item(
    eid: str,
    ts: str | None,
    prec: str,
    etype: str,
    source: str = "security_log",
    claim: str = "OBSERVED_FACT",
    **data,
):
    return EvidenceItem(evidence_id=eid, case_id="INC-900", timestamp=ts, time_precision=prec, source=source,
                        source_type="log" if source == "security_log" else "alert", event_type=etype,
                        summary=etype, claim_type=claim, data=data)  # fmt: skip


def test_claim_types_are_the_five_required():
    assert CLAIM_TYPES == (
        "OBSERVED_FACT",
        "DETERMINISTIC_FINDING",
        "POLICY_REQUIREMENT",
        "AGENT_INFERENCE",
        "UNKNOWN_OR_GAP",
    )


def test_evidence_item_rejects_unknown_claim_types_and_fields():
    with pytest.raises(ValidationError):
        item("X", None, "none", "x", claim="CONCLUSION")
    with pytest.raises(ValidationError):
        EvidenceItem(evidence_id="X", case_id="C", source="s", source_type="log", event_type="e", summary="s",
                     claim_type="OBSERVED_FACT", raw_text="not allowed")  # fmt: skip


def test_report_schema_forbids_critical_and_extra_fields():
    base = {"case_id": "INC-001", "executive_summary": "s", "incident_status": "POTENTIAL_INCIDENT",
            "severity_recommendation": "HIGH", "claims": [], "confidence": "low", "human_review_requested": True}  # fmt: skip
    IncidentReport.model_validate(base)
    with pytest.raises(ValidationError):
        IncidentReport.model_validate(
            base | {"severity_recommendation": "CRITICAL"}
        )  # analyst-only
    with pytest.raises(ValidationError):
        IncidentReport.model_validate(base | {"verdict": "guilty"})


def test_timeline_orders_by_authoritative_time_and_marks_uncertain_order():
    tl = timeline([
        item("DLP-1", "2026-08-25T23:48", "minute", "dlp_alert", source="dlp"),
        item("LOG-up", "2026-08-25T23:00", "hour", "external_upload", destination="dropbox.com"),
        item("LOG-dl", "2026-08-25T11:00", "hour", "bulk_download", files=5),
        item("UC2-ANOM", "2026-08-25", "day", "anomaly_score", source="uc2", claim="DETERMINISTIC_FINDING"),
        item("IDN", None, "none", "identity_profile", source="identity"),
    ])  # fmt: skip
    assert [e.evidence_id for e in tl] == [
        "LOG-dl",
        "LOG-up",
        "DLP-1",
    ]  # day-level and timeless items are not events
    up, dlp = tl[1], tl[2]
    assert up.order_uncertain_with == ["DLP-1"] and dlp.order_uncertain_with == [
        "LOG-up"
    ]  # same hour, one has no minute
    assert tl[0].order_uncertain_with == []
    assert [e.time for e in tl] == [
        "2026-08-25T11:00",
        "2026-08-25T23:00",
        "2026-08-25T23:48",
    ]  # times unchanged


def test_minute_precision_events_in_the_same_hour_are_ordered():
    tl = timeline([item("B", "2026-08-16T15:22", "minute", "dlp_alert", source="dlp"),
                   item("A", "2026-08-16T15:20", "minute", "external_upload")])  # fmt: skip
    assert [e.evidence_id for e in tl] == ["A", "B"] and all(not e.order_uncertain_with for e in tl)


def test_correlation_links_conflicts_and_gaps():
    items = [
        item("LOG-dl", "2026-08-17T11:00", "hour", "file_download_batch", files=30),
        item("LOG-up", "2026-08-17T13:00", "hour", "external_upload", destination="drive.google.com", account_type="personal"),
        item("DLP-1", "2026-08-17T13:04", "minute", "dlp_alert", source="dlp", destination="drive.google.com",
             destination_class="personal_cloud", files=["F1"]),
        item("UC4-F1", None, "none", "classification", source="uc4", claim="DETERMINISTIC_FINDING", handle="F1",
             level="HIGHLY_CONFIDENTIAL", categories=["FINANCIAL_PCI"], ok=True, resource_id="r1"),
        item("APR-PTA", None, "none", "approval_check", source="approvals", ref="PTA", found=True, verified=False),
        item("UC3-NOPATH-r1", None, "none", "no_active_path", source="uc3", resource_id="r1"),
    ]  # fmt: skip
    rules = {(c.kind, c.rule) for c in correlate(items)}
    assert ("finding", "transfer_matches_alert") in rules
    assert ("finding", "download_before_transfer") in rules
    assert ("finding", "sensitive_file_in_transfer") in rules
    assert ("conflict", "approval_not_valid") in rules
    assert ("conflict", "data_without_active_path") in rules
    assert ("gap", "destination_control_unknown") in rules


def test_missing_evidence_becomes_a_gap_not_a_guess():
    items = [item("UC4-F1", None, "none", "classification", source="uc4", claim="UNKNOWN_OR_GAP", handle="F1",
                  level=None, categories=[], ok=False, resource_id="r1"),
             item("DLP-1", "2026-08-12T16:40", "minute", "dlp_alert", source="dlp", destination="x.example",
                  destination_class="unknown_external", files=["F1"])]  # fmt: skip
    cs = correlate(items)
    assert {c.rule for c in cs if c.kind == "gap"} >= {
        "sensitivity_not_established",
        "destination_control_unknown",
    }
    assert all(c.claim_type == "UNKNOWN_OR_GAP" for c in cs if c.kind == "gap")
    assert not any(
        c.rule == "sensitive_file_in_transfer" for c in cs
    )  # no level, no sensitivity claim
