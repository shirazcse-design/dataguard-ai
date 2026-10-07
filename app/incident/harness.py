"""The UC5 severity harness: the deterministic decision authority.

After the agent has investigated, the harness gathers the authoritative facts ITSELF, through the
same services, into its own ledger (it never depends on which tools the agent chose to call), builds
the full timeline and correlations, and applies the versioned rubric. The agent's recommendation can
raise the severity (adopted, with review) but never lower it. CRITICAL is never set here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .correlate import correlate, timeline
from .schemas import SEVERITY_RANK, Correlation, Decision, Incident, IncidentReport, TimelineEntry
from .services import LEVEL_RANK, IncidentServices, Ledger, ServiceError

REPO = Path(__file__).resolve().parents[2]
RUBRIC = REPO / "config" / "incident" / "rubric.v1.yaml"
SENSITIVE = ("CONFIDENTIAL", "HIGHLY_CONFIDENTIAL")


def load_rubric(path: Path = RUBRIC) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@dataclass
class Facts:
    """Authoritative facts for one case, gathered by the harness. Built once, never by a model."""

    ledger: Ledger
    failures: list[str] = field(default_factory=list)
    level: str | None = None
    unclassified: list[str] = field(default_factory=list)
    transfers: list[dict[str, Any]] = field(
        default_factory=list
    )  # {evidence_id, destination, class}
    approval_verified: bool = False
    anomaly_band: str | None = None
    policy: dict[str, Any] | None = None
    injection_flagged: bool = False
    timeline: list[TimelineEntry] = field(default_factory=list)
    correlations: list[Correlation] = field(default_factory=list)

    def view(self) -> dict[str, Any]:
        return {"failures": self.failures, "level": self.level, "unclassified": self.unclassified,
                "transfers": self.transfers, "approval_verified": self.approval_verified, "anomaly_band": self.anomaly_band,
                "policy": self.policy, "injection_flagged": self.injection_flagged,
                "conflicts": [c.rule for c in self.correlations if c.kind == "conflict"],
                "gaps": [c.rule for c in self.correlations if c.kind == "gap"]}  # fmt: skip


def compute_facts(svc: IncidentServices, inc: Incident) -> Facts:  # noqa: C901
    led = Ledger(inc.case_id)
    f = Facts(ledger=led)

    def attempt(name: str, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except ServiceError:
            f.failures.append(name)
            return None

    logs = attempt("security_logs", svc.logs, inc, led)
    attempt("identity", svc.identity, inc, led)
    beh = attempt("behavior", svc.behavior, inc, led)
    f.anomaly_band = beh["anomaly_band"] if beh else None
    for rid in svc.case_resources(inc):
        attempt("access", svc.access, inc, led, rid)
    dlp = svc.dlp(inc, led)
    files = attempt("uc4", svc.classify, inc, led) or []
    known = [x for x in files if x["level"]]
    f.unclassified = [x["handle"] for x in files if not x["level"]]
    if f.unclassified and "uc4" not in f.failures:
        f.failures.append("uc4")
    if known:
        f.level = max((x["level"] for x in known), key=LEVEL_RANK.get)
    for a in dlp["alerts"]:
        f.transfers.append({"evidence_id": a["evidence_id"], "destination": a["destination"],
                            "class": a["destination_class"]["class"]})  # fmt: skip
    for e in logs or []:
        if e["type"] == "external_upload":
            from app.dlp.prechecks import classify_destination

            host, acct = e.get("destination") or "", e.get("account_type") or "personal"
            dclass = classify_destination(host, acct, svc.dlp_cfg)
            svc._dest(inc, led, host, acct, dclass)
            f.transfers.append(
                {
                    "evidence_id": e["evidence_id"],
                    "destination": e.get("destination"),
                    "class": dclass,
                }
            )
        if e.get("ref"):
            ap = svc.approval(inc, led, e["ref"])
            f.approval_verified = f.approval_verified or bool(ap.get("verified"))
    rub = load_rubric()
    non_approved = [t for t in f.transfers if t["class"] in rub["non_approved_destinations"]]
    if non_approved and known:
        top = max(known, key=lambda x: LEVEL_RANK[x["level"]])
        f.policy = attempt("policy", svc.policy, inc, led, "external_transfer", level=top["level"],
                           categories=top["categories"], destination_class=non_approved[0]["class"])  # fmt: skip
    attempt("policy", svc.policy, inc, led, "incident_procedure")
    f.injection_flagged = (any(i.data.get("injection_flagged") for i in led.items.values())
                           or any(i.data.get("justification_flagged") for i in led.items.values()))  # fmt: skip
    return f


def correlate_facts(f: Facts) -> None:
    """The full timeline and correlations over the harness's own evidence (spans in the pipeline)."""
    items = list(f.ledger.items.values())
    f.correlations = correlate(items)
    f.timeline = timeline(items)


def _floor(f: Facts, rub: dict[str, Any]) -> tuple[str, str]:
    non_approved = any(t["class"] in rub["non_approved_destinations"] for t in f.transfers)
    access_concern = any(c.rule in ("stale_grant_on_case_resource", "data_without_active_path", "data_outside_expected_classes")
                         for c in f.correlations) and f.level in SENSITIVE  # fmt: skip
    # An approval never lowers a transfer to a non-approved destination: no register entry here can
    # authorise personal storage, so a cited approval is checked (and may conflict) but does not excuse it.
    if non_approved:
        if f.level == "HIGHLY_CONFIDENTIAL":
            return "sensitive_exfiltration", "HIGH"
        if f.level == "CONFIDENTIAL":
            return "confidential_exfiltration", "MEDIUM"
        if f.level is None:
            return "unclassified_exfiltration", "MEDIUM"
    # A verified approval for this subject on this date is the documented business reason for
    # out-of-pattern access (it never covers a non-approved destination, above).
    if f.approval_verified:
        return "approved_activity", "LOW"
    if access_concern:
        return "sensitive_access_concern", "MEDIUM"
    return "no_exposure_indicated", "LOW"


def decide(f: Facts, report: IncidentReport | None, *, validation: dict[str, Any], review_requests: list[str],
           refusals: list[str], agent_failed: bool, rubric: dict[str, Any] | None = None) -> Decision:  # fmt: skip
    rub = rubric or load_rubric()
    rule, floor = _floor(f, rub)
    reasons = [f"rule:{rule}", *(f"failure:{x}" for x in f.failures),
               *(f"{c.kind}:{c.rule}" for c in f.correlations if c.kind in ("conflict", "gap"))]  # fmt: skip
    review: list[str] = []
    severity, effect = floor, "not_run"
    if agent_failed or report is None:
        effect = "agent_failed"
        review.append("agent_failed")
    else:
        diff = SEVERITY_RANK[report.severity_recommendation] - SEVERITY_RANK[floor]
        if diff > 0:
            severity, effect = report.severity_recommendation, "raised_adopted"
            review.append("agent_raised")
        elif diff == 0:
            effect = "agreed"
        elif -diff >= rub["agent"]["disagreement_levels"]:
            effect = "disagreement_logged"
            review.append("agent_disagreement")
        else:
            effect = "lower_ignored"
    if severity == "HIGH":
        review.append("severity_high")
    if f.failures:
        review.append("capability_failure")
    # A policy conflict is material only for sensitive data (UC6 reports the AUP/DLP conflict for
    # INTERNAL data in personal storage; it does not change a LOW-sensitivity outcome).
    material = [
        c
        for c in f.correlations
        if c.kind == "conflict" and (c.rule != "policy_conflict" or f.level in SENSITIVE)
    ]
    if material:
        review.append("conflicting_evidence")
    pol = f.policy or {}
    if pol.get("insufficient_policy_evidence") and f.level in SENSITIVE:
        review.append("policy_insufficient")
    if f.injection_flagged:
        review.append("untrusted_input_flagged")
    if rule == "sensitive_access_concern":
        review.append("sensitive_access_concern")
    if review_requests:
        review.append("agent_requested_review")
    if validation.get("unsupported") or validation.get("withheld"):
        review.append("unsupported_or_withheld_claims")
    if refusals:
        review.append("out_of_scope_attempts")
    material_missing = any(x in f.failures for x in ("security_logs", "uc4"))
    status = ("INSUFFICIENT_EVIDENCE" if material_missing else
              "POTENTIAL_INCIDENT" if SEVERITY_RANK[severity] >= 1 else "NO_INCIDENT_INDICATED")  # fmt: skip
    personal = any(t["class"] in rub["non_approved_destinations"] for t in f.transfers)
    sev1 = (severity == "HIGH" and f.level == "HIGHLY_CONFIDENTIAL" and personal and f.anomaly_band == "HIGH_ANOMALY"
            and any(i.event_type == "dlp_alert" for i in f.ledger.items.values()))  # fmt: skip
    return Decision(
        rubric_version=rub["rubric_version"],
        severity=severity,
        deterministic_severity=floor,  # type: ignore[arg-type]
        incident_status=status,
        rule=rule,
        reason_codes=reasons,  # type: ignore[arg-type]
        review_status="REQUIRED" if review else "NOT_REQUIRED",
        review_reasons=sorted(set(review)),
        agent_recommendation=report.severity_recommendation if report else None,
        agent_effect=effect,
        potential_sev1=sev1,
    )  # type: ignore[arg-type]
