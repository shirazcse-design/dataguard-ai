"""UC5's main new capability: a deterministic incident TIMELINE and evidence CORRELATION.

Both are pure functions of evidence items already in the ledger. They never invent an event, never
change a timestamp, and never decide severity:

* timeline: events ordered by their authoritative timestamps, keeping each item's id, source and
  claim type. Precision is preserved: UC2's routine log events are hour-level, so two events in the
  same hour where one has no minute are marked `order_uncertain_with` each other rather than given a
  made-up order.
* correlation: versioned rules that LINK items (`finding`), flag contradictions (`conflict`) and
  name what the evidence does not establish (`gap`). The agent may explain a link; it cannot create
  one.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .schemas import Correlation, EvidenceItem, TimelineEntry

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "incident" / "correlation.v1.yaml"
TRANSFER_TYPES = ("external_upload", "dlp_alert")
DOWNLOAD_TYPES = ("bulk_download", "file_download_batch")


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _t(item: EvidenceItem) -> datetime:
    return datetime.fromisoformat(item.timestamp)  # type: ignore[arg-type]


def timeline(items: list[EvidenceItem]) -> list[TimelineEntry]:
    events = [i for i in items if i.timestamp and i.time_precision in ("minute", "hour")]
    events.sort(
        key=lambda i: (i.timestamp[:13], i.time_precision == "minute", i.timestamp, i.evidence_id)
    )
    out = []
    for i in events:
        bucket = [j for j in events if j is not i and j.timestamp[:13] == i.timestamp[:13]
                  and "hour" in (i.time_precision, j.time_precision)]  # fmt: skip
        out.append(TimelineEntry(time=i.timestamp, precision=i.time_precision, evidence_id=i.evidence_id,
                                 source=i.source, event_type=i.event_type, summary=i.summary, claim_type=i.claim_type,
                                 order_uncertain_with=sorted(j.evidence_id for j in bucket)))  # fmt: skip
    return out


def _minutes_apart(a: EvidenceItem, b: EvidenceItem) -> float:
    return abs((_t(a) - _t(b)).total_seconds()) / 60


def _same_window(a: EvidenceItem, b: EvidenceItem, minutes: int) -> bool:
    if "hour" in (
        a.time_precision,
        b.time_precision,
    ):  # hour precision: same hour bucket, or within the window
        return a.timestamp[:13] == b.timestamp[:13] or _minutes_apart(a, b) <= minutes
    return _minutes_apart(a, b) <= minutes


def correlate(items: list[EvidenceItem], cfg: dict[str, Any] | None = None) -> list[Correlation]:  # noqa: C901
    cfg = cfg or load_config()
    by = {i.evidence_id: i for i in items}
    logs = [i for i in items if i.source == "security_log"]
    uploads = [i for i in logs if i.event_type == "external_upload"]
    alerts = [i for i in items if i.event_type == "dlp_alert"]
    transfers = sorted(uploads + alerts, key=lambda i: i.timestamp or "")
    downloads = [i for i in logs if i.event_type in DOWNLOAD_TYPES]
    files = {i.data["handle"]: i for i in items if i.event_type == "classification"}
    dests = {i.data["destination"]: i for i in items if i.event_type == "destination_class"}
    idn = by.get("IDN")
    out: list[Correlation] = []

    def add(kind: str, rule: str, ids: list[str], summary: str) -> None:
        cid = f"{'GAP' if kind == 'gap' else 'COR'}-{rule}-{len([c for c in out if c.rule == rule]) + 1}"
        out.append(Correlation(correlation_id=cid, kind=kind, rule=rule, evidence_ids=sorted(set(ids)), summary=summary,  # type: ignore[arg-type]
                               claim_type="UNKNOWN_OR_GAP" if kind == "gap" else "DETERMINISTIC_FINDING"))  # fmt: skip

    # C1 a logged upload and a DLP alert to the same destination, close in time
    matched_uploads: set[str] = set()
    for a in alerts:
        m = [u for u in uploads if u.data.get("destination") == a.data.get("destination")
             and _same_window(u, a, cfg["transfer_match_minutes"])]  # fmt: skip
        for u in m:
            matched_uploads.add(u.evidence_id)
            add(
                "finding",
                "transfer_matches_alert",
                [u.evidence_id, a.evidence_id],
                f"the logged upload and the DLP alert both go to {a.data.get('destination')} within the same window",
            )
    # C2 downloads earlier the same day preceded a transfer
    for t in transfers:
        before = [d for d in downloads if d.timestamp[:10] == t.timestamp[:10] and _t(d) <= _t(t)
                  and (_t(t) - _t(d)).total_seconds() <= cfg["download_before_transfer_hours"] * 3600]  # fmt: skip
        if before:
            n = sum(int(d.data.get("files") or 0) for d in before)
            add(
                "finding",
                "download_before_transfer",
                [*(d.evidence_id for d in before), t.evidence_id],
                f"{n} files were downloaded earlier the same day, before the transfer at {t.timestamp[11:16]}",
            )
    # C3 transfer outside the subject's usual working hours
    if idn and idn.data.get("work_hours"):
        start, end = idn.data["work_hours"]
        for t in transfers:
            h = int(t.timestamp[11:13])
            if not start <= h < end:
                add(
                    "finding",
                    "transfer_outside_work_hours",
                    [t.evidence_id, "IDN"],
                    f"the transfer at {t.timestamp[11:16]} is outside the subject's usual hours ({start}:00-{end}:00)",
                )
    # C4 sensitive files named in a DLP alert; C8 archive created from those files before the transfer
    for a in alerts:
        for h in a.data.get("files", []):
            f = files.get(h)
            if f and f.data.get("level") in cfg["sensitive_levels"]:
                add(
                    "finding",
                    "sensitive_file_in_transfer",
                    [a.evidence_id, f.evidence_id],
                    f"the alert includes {h}, classified {f.data['level']} by UC4",
                )
        for ar in (i for i in logs if i.event_type == "archive_created"):
            if set(ar.data.get("files", [])) & set(a.data.get("files", [])) and _t(ar) <= _t(a):
                add(
                    "finding",
                    "archive_before_transfer",
                    [ar.evidence_id, a.evidence_id],
                    "an archive of the alerted files was created before the transfer",
                )
    # C5 approvals: verified ones cover a transfer; unverified ones conflict with the claim of approval
    for ap in (i for i in items if i.event_type == "approval_check"):
        if ap.data.get("verified"):
            add(
                "finding",
                "approval_verified",
                [ap.evidence_id],
                f"approval {ap.data['ref']} is valid for this subject and date",
            )
        elif ap.data.get("found"):
            add(
                "conflict",
                "approval_not_valid",
                [ap.evidence_id, *(t.evidence_id for t in transfers)],
                f"approval {ap.data['ref']} is referenced but is expired or not this subject's",
            )
    # C6 access: stale grant used, or no active path to a file's resource
    for st in (i for i in items if i.event_type == "stale_grant"):
        rel = [
            f.evidence_id
            for f in files.values()
            if f.data.get("resource_id") == st.data.get("resource_id")
        ]
        add(
            "finding",
            "stale_grant_on_case_resource",
            [st.evidence_id, *rel],
            f"the case files' resource is held through stale direct grant {st.data['grant_id']}",
        )
    for npth in (i for i in items if i.event_type == "no_active_path"):
        rel = [
            f.evidence_id
            for f in files.values()
            if f.data.get("resource_id") == npth.data.get("resource_id")
        ]
        add(
            "conflict",
            "data_without_active_path",
            [npth.evidence_id, *rel, *(d.evidence_id for d in downloads)],
            f"files from {npth.data['resource_id']} are in the case, but the access snapshot shows no active path",
        )
    # C7 data outside the subject's expected data classes
    if idn:
        expected = set(idn.data.get("expected_data_classes", []))
        for f in files.values():
            extra = [c for c in f.data.get("categories", []) if c not in expected]
            if extra and f.data.get("level") in cfg["sensitive_levels"]:
                add(
                    "finding",
                    "data_outside_expected_classes",
                    [f.evidence_id, "IDN"],
                    f"{f.data['handle']} carries {', '.join(extra)}, outside the subject's expected data classes",
                )
    # policy conflicts reported by UC6
    for p in (i for i in items if i.event_type == "policy_result" and i.data.get("conflict")):
        add(
            "conflict",
            "policy_conflict",
            [p.evidence_id],
            f"UC6 reports conflicting policy evidence for {p.data['topic']}",
        )
    # gaps
    for t in transfers:
        cls = t.data.get("destination_class") or (
            dests.get(t.data.get("destination")) or EvidenceItem.model_construct(data={})
        ).data.get("destination_class")
        if cls in cfg["personal_destinations"]:
            add(
                "gap",
                "destination_control_unknown",
                [t.evidence_id],
                f"the evidence does not establish who controls the account at {t.data.get('destination')}",
            )
            break
    for u in uploads:
        if u.evidence_id not in matched_uploads:
            add(
                "gap",
                "upload_content_unknown",
                [u.evidence_id],
                f"the upload to {u.data.get('destination')} has no DLP record, so what was sent is not recorded",
            )
    if logs:
        for a in alerts:
            if not any(u.data.get("destination") == a.data.get("destination") for u in uploads):
                add(
                    "gap",
                    "alert_without_logged_upload",
                    [a.evidence_id],
                    "no upload to the alerted destination appears in the retrieved security log",
                )
    for f in files.values():
        if not f.data.get("ok"):
            add(
                "gap",
                "sensitivity_not_established",
                [f.evidence_id],
                f"UC4 could not establish the sensitivity of {f.data['handle']}",
            )
    for p in (i for i in items if i.event_type == "policy_result" and i.data.get("insufficient")):
        add(
            "gap",
            "policy_evidence_insufficient",
            [p.evidence_id],
            f"policy evidence is insufficient for {p.data['topic']}",
        )
    return out
