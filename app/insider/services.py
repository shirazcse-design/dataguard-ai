"""UC2's deterministic, AUTHORITATIVE capabilities. Agents call these through bound tools; nothing
here asks a model to decide anything, and nothing here can act.

* `InsiderData`      synthetic users, permissions, approvals, activity, and the fitted detector
* `EvidenceStore`    per-case evidence ids, provenance and a call cache (repeat calls are free and
                     counted as unnecessary)
* `LogService`       synthetic security events, generated deterministically from the activity data
                     plus a case's few overlay events; free-text fields are UNTRUSTED and scanned
* `IdentityService`  role, privilege, expected access. No demographics, HR or employment status
* `BehaviorService`  the Isolation Forest result (authoritative), baseline and activity series
* `DataService`      UC4 classification through UC4's own adapter (caller `insider-risk-agent`)
* `PolicyService`    UC6 verified policy answers (external-transfer effects via UC1's mapping)
* `ApprovalService`  the approvals register: a justification counts only if a record verifies it
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from ml.insider import detectors as D
from ml.insider import features as F
from ml.insider import synth

from .schemas import (
    AnomalyResult,
    ClassificationSummary,
    EvidenceItem,
    FileSensitivity,
    IdentityContext,
    VerifiedPolicyResult,
)

REPO = Path(__file__).resolve().parents[2]
LEVEL_RANK = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "HIGHLY_CONFIDENTIAL": 3}
CALLER = "insider-risk-agent"


class ServiceError(Exception):
    """A capability failed. Carries a code only, never content."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class InsiderData:
    """Loads the frozen synthetic data and fits the detector on the historical period."""

    def __init__(self) -> None:
        self.cfg = synth.load_config()
        self.users = {u["user_id"]: u for u in synth.load_users()}
        self.permissions = json.loads((synth.DATA / "permissions.json").read_text("utf-8"))
        self.approvals = {
            a["ref"]: a for a in json.loads((synth.DATA / "approvals.json").read_text("utf-8"))
        }
        self.activity = synth.load_activity()
        self.records = F.build_features(self.activity, self.cfg)
        self.by_key = {(r["user_id"], r["date"]): r for r in self.records}
        self.rows = {(r["user_id"], r["date"]): r for r in self.activity}
        train = [r for r in self.records if r["period"] == "train"]
        self.detector = D.IsolationForestDetector.fit(train, self.cfg)
        self.statistical = D.StatisticalBaseline.from_config(self.cfg)


@dataclass
class EvidenceStore:
    """Evidence for ONE case. Ids are assigned here (never by a model); the cache serves an
    identical repeat call without re-running it and counts it."""

    items: dict[str, EvidenceItem] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)
    repeats: int = 0
    counters: dict[str, int] = field(default_factory=dict)

    def add(
        self, prefix: str, claim_type: str, source: str, summary: str, fixed: str | None = None
    ) -> str:
        if fixed:
            eid = fixed
        else:
            self.counters[prefix] = self.counters.get(prefix, 0) + 1
            eid = f"{prefix}{self.counters[prefix]}"
        self.items.setdefault(
            eid, EvidenceItem(id=eid, claim_type=claim_type, source=source, summary=summary)
        )
        return eid

    def cached(self, key: str, fn):
        if key in self.cache:
            self.repeats += 1
            return self.cache[key]
        self.cache[key] = fn()
        return self.cache[key]


def _day_of(d: str, start: str) -> int:
    return (date.fromisoformat(d) - date.fromisoformat(start)).days + 1


class BehaviorService:
    def __init__(self, data: InsiderData) -> None:
        self.data = data

    def anomaly(self, user_id: str, d: str, store: EvidenceStore) -> AnomalyResult:
        rec = self.data.by_key.get((user_id, d))
        if rec is None:
            raise ServiceError("no_activity_record")
        a = D.assess(self.data.detector, rec)
        ids = [store.add("A", "INFERRED_ANOMALY", "isolation_forest",
                         f"{c['feature']} contributed to the anomaly score (reset-to-baseline drop "
                         f"{c['score_drop_if_reset']})") for c in a["contributing_signals"]]  # fmt: skip
        store.add("AS", "INFERRED_ANOMALY", "isolation_forest",
                  f"anomaly score {a['anomaly_score']} band {a['anomaly_band']}", fixed="AS")  # fmt: skip
        return AnomalyResult(**{k: a[k] for k in ("model_id", "model_version", "model_fingerprint",
                                                   "anomaly_score", "anomaly_band", "thresholds",
                                                   "contributing_signals")}, evidence_ids=["AS", *ids])  # fmt: skip

    def profile(self, user_id: str, d: str) -> dict[str, Any]:
        rec = self.data.by_key.get((user_id, d))
        if rec is None:
            raise ServiceError("no_activity_record")
        b = rec["baseline"]
        a = D.assess(self.data.detector, rec)
        return {
            "evidence_id": "AS", "date": d, "day_type": "weekend" if rec["weekend"] else "weekday",
            "anomaly_score": a["anomaly_score"], "anomaly_band": a["anomaly_band"],
            "thresholds": a["thresholds"], "model": f"{a['model_id']}@{a['model_version']}",
            "today": {f: rec[f] for f in synth.FEATURES},
            "baseline_median": {f: round(b.median[f], 1) for f in synth.FEATURES},
            "contributing_signals": a["contributing_signals"],
            "note": "The anomaly score measures unusualness against this user's own history. It is not evidence of intent.",
        }  # fmt: skip

    def series(self, user_id: str, d: str, days: int, malformed: bool = False) -> dict[str, Any]:
        end = date.fromisoformat(d)
        out = []
        for i in range(days - 1, -1, -1):
            k = (user_id, (end - timedelta(days=i)).isoformat())
            if k in self.data.rows:
                r = self.data.rows[k]
                out.append({"date": r["date"], **{f: r[f] for f in synth.FEATURES}})
        if malformed:  # SIMULATED fault: a corrupted upstream record
            out[-1] = {"date": out[-1]["date"], "files_downloaded": "many", "note": "<<corrupted>>"}
        for row in out:
            bad = [k for k, v in row.items() if k != "date" and not isinstance(v, int | float)]
            if bad:
                raise ServiceError("malformed_tool_result")
        return {"evidence_id": "SERIES", "days": out}

    def statistical(self, user_id: str, d: str) -> dict[str, Any]:
        rec = self.data.by_key.get((user_id, d))
        if rec is None:
            raise ServiceError("no_activity_record")
        zs = {f: round(rec[f"z_{f}"], 1) for f in synth.FEATURES}
        flag = bool(self.data.statistical.flags([rec])[0])
        return {"evidence_id": "STAT", "statistical_flag": flag, "robust_z": zs,
                "rule": "any robust z > 3, or two or more > 2"}  # fmt: skip


class IdentityService:
    def __init__(self, data: InsiderData) -> None:
        self.data = data

    def context(
        self, user_id: str, store: EvidenceStore, unavailable: bool = False
    ) -> IdentityContext:
        if unavailable:  # SIMULATED fault
            raise ServiceError("identity_unavailable")
        u = self.data.users.get(user_id)
        if u is None:
            raise ServiceError("unknown_user")
        p = self.data.permissions[user_id]
        eid = store.add("IDN", "OBSERVED_FACT", "identity",
                        f"{u['role']} ({u['role_family']}), privilege {u['privilege_level']}", fixed="IDN")  # fmt: skip
        return IdentityContext(
            available=True, role=u["role"], role_family=u["role_family"], department=u["department"],
            privilege_level=u["privilege_level"], work_hours=u["work_hours"],
            expected_repositories=[r["name"] for r in p["repositories"]],
            expected_data_classes=p["expected_data_classes"], evidence_ids=[eid],
        )  # fmt: skip

    def permissions(self, user_id: str, unavailable: bool = False) -> dict[str, Any]:
        if unavailable:
            raise ServiceError("identity_unavailable")
        if user_id not in self.permissions_store():
            raise ServiceError("unknown_user")
        return {"evidence_id": "PERM", **self.permissions_store()[user_id]}

    def permissions_store(self) -> dict[str, Any]:
        return self.data.permissions


class LogService:
    """Synthetic security events for the case's user, generated from the activity rows (routine
    events) plus the case's overlay events. Event ids are stable hashes. Free text is UNTRUSTED:
    it is scanned with UC6's injection scanner, and flagged text is withheld."""

    WINDOW_DAYS = 7  # the investigator may look at most this far either side of the case date

    def __init__(self, data: InsiderData, scanner: Any) -> None:
        self.data = data
        self.scanner = scanner
        weekday_medians: dict[str, float] = {}
        for uid in data.users:
            vals = [r["files_downloaded"] for r in data.activity
                    if r["user_id"] == uid and r["period"] == "train" and date.fromisoformat(r["date"]).weekday() < 5]  # fmt: skip
            weekday_medians[uid] = float(np.median(vals)) if vals else 0.0
        self.bulk_threshold = {u: max(50.0, 3 * m) for u, m in weekday_medians.items()}

    @staticmethod
    def _eid(user_id: str, d: str, kind: str, n: int) -> str:
        return "L-" + hashlib.sha256(f"{user_id}|{d}|{kind}|{n}".encode()).hexdigest()[:6]

    def _day_events(
        self, u: dict[str, Any], d: str, overlays: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        r = self.data.rows.get((u["user_id"], d))
        out: list[dict[str, Any]] = []
        if r is None:
            return out
        start_h = u["work_hours"][0]
        day = _day_of(d, self.data.cfg["dataset"]["start_date"])
        det = synth.event_details(u, day)

        def ev(kind: str, hour: int, **fields: Any) -> None:
            out.append({"id": self._eid(u["user_id"], d, kind, len(out)), "time": f"{d}T{hour:02d}:00",
                        "type": kind, **fields})  # fmt: skip

        if r["files_downloaded"] > 0:
            ev("login", start_h, channel="sso")
        if r["after_hours_activity"] > 0:
            ev("after_hours_login", 22, sessions=int(r["after_hours_activity"]))
        new = det.get("new_repositories", [])[: int(r["new_repository_access"])]
        new += [f"{u['known_repositories'][0].rsplit('-', 2)[0]}-sandbox-{i + 1}"
                for i in range(int(r["new_repository_access"]) - len(new))]  # fmt: skip
        for name in new:
            ev(
                "repo_access_new",
                start_h + 1 if r["after_hours_activity"] == 0 else 21,
                repository=name,
            )
        if r["files_downloaded"] > 0:
            kind = (
                "bulk_download"
                if r["files_downloaded"] >= self.bulk_threshold[u["user_id"]]
                else "file_download_batch"
            )
            ev(kind, start_h + 2, files=int(r["files_downloaded"]), sensitive_files=int(r["sensitive_files_accessed"]),
               repositories=int(r["distinct_repositories"]))  # fmt: skip
        if r["external_upload_volume_mb"] > 0:
            up = det.get("upload", {})
            ev("external_upload", 23 if r["after_hours_activity"] > 5 else start_h + 4,
               destination=up.get("host"), account_type=up.get("account_type"),
               volume_mb=r["external_upload_volume_mb"])  # fmt: skip
        if r["failed_access_attempts"] > 0:
            ev("auth_failure", start_h + 3, attempts=int(r["failed_access_attempts"]))
        for o in overlays:
            fields = {k: v for k, v in o.items() if k not in ("type", "hour")}
            ev(o["type"], int(o["hour"]), **fields)
        return out

    def search(self, user_id: str, case_date: str, overlays: list[dict[str, Any]], *, start: str,
               end: str, event_types: list[str] | None, limit: int, unavailable: bool = False) -> dict[str, Any]:  # fmt: skip
        if unavailable:  # SIMULATED fault
            raise ServiceError("tool_unavailable")
        u = self.data.users.get(user_id)
        if u is None:
            raise ServiceError("unknown_user")
        lo = date.fromisoformat(case_date) - timedelta(days=self.WINDOW_DAYS)
        hi = date.fromisoformat(case_date) + timedelta(days=1)
        s, e = max(date.fromisoformat(start), lo), min(date.fromisoformat(end), hi)
        events, withheld = [], 0
        d = s
        while d <= e:
            ds = d.isoformat()
            for x in self._day_events(u, ds, overlays if ds == case_date else []):
                if event_types and x["type"] not in event_types:
                    continue
                if isinstance(x.get("text"), str) and self.scanner.scan(x["text"]):
                    x = {
                        **x,
                        "text": "[withheld: instruction-like text detected]",
                        "untrusted_flagged": True,
                    }
                    withheld += 1
                events.append(x)
            d += timedelta(days=1)
        clipped = s != date.fromisoformat(start) or e != date.fromisoformat(end)
        return {"events": events[:limit], "total": len(events), "truncated": len(events) > limit,
                "window_clipped": clipped, "withheld_untrusted": withheld}  # fmt: skip


class ApprovalService:
    def __init__(self, data: InsiderData) -> None:
        self.data = data

    def check(self, ref: str, user_id: str, case_date: str) -> dict[str, Any]:
        a = self.data.approvals.get(ref)
        if a is None:
            return {"evidence_id": "APR", "found": False, "verified": False, "reason": "no_record"}
        valid = a["valid_from"] <= case_date <= a["valid_to"]
        same_user = a["user_id"] == user_id
        return {"evidence_id": "APR", "found": True, "verified": valid and same_user, "type": a["type"],
                "scope": a["scope"], "valid_from": a["valid_from"], "valid_to": a["valid_to"],
                "reason": "verified" if valid and same_user else "expired_or_not_this_user"}  # fmt: skip


class DataService:
    """UC4 is authoritative for sensitivity. Only the case's own files can be classified."""

    def __init__(self, classifier: Any, documents: Any) -> None:
        self.classifier = classifier  # app.dlp.integration.Uc4Classifier over a UC4 adapter
        self.documents = documents  # app.dlp.integration.DocumentStore

    def classify(self, refs: list[str], case_id: str, store: EvidenceStore, *, semantic_unavailable: bool) -> ClassificationSummary:  # fmt: skip
        files = []
        for ref in refs:
            doc = self.documents.get(ref)
            c = self.classifier.classify(
                doc, f"uc2-{case_id}-{ref[-6:]}", semantic_unavailable=semantic_unavailable
            )
            eid = store.add("D", "OBSERVED_FACT", "uc4",
                            f"file classified {c.level or 'UNKNOWN'} {','.join(c.categories) or 'no category'}")  # fmt: skip
            files.append(FileSensitivity(file_ref=ref, evidence_id=eid, ok=c.ok and c.level is not None,
                                         level=c.level, categories=c.categories, confidence=c.confidence,
                                         review_required=c.review_required, injection_flagged=c.injection_flagged))  # fmt: skip
        levels = [f.level for f in files if f.level]
        return ClassificationSummary(
            files=files, max_level=max(levels, key=LEVEL_RANK.get) if levels else None,
            high_risk_categories=sorted({c for f in files for c in f.categories
                                         if c in ("CREDENTIALS_SECRETS", "TRADE_SECRET", "PHI", "FINANCIAL_PCI", "SOURCE_CODE", "MA_CORP_STRATEGY")}),
            uncertain=any(not f.ok or f.review_required for f in files),
        )  # fmt: skip


POLICY_TOPICS: dict[str, str] = {
    "access_beyond_role": "Is an employee allowed to access systems or repositories outside their role without an approved access request?",
    "incident_procedure": "How must a suspected data-security incident be reported, and how must its evidence be preserved?",
    "monitoring": "May the company monitor the use of company devices, accounts and networks?",
}  # fmt: skip


class PolicyService:
    """UC6 is authoritative for policy. External-transfer questions reuse UC1's templated question
    and versioned effect mapping; other topics are fixed questions. Only VERIFIED claims return."""

    def __init__(self, copilot: Any, dlp_cfg: Any, mapping: Any) -> None:
        from app.dlp.integration import PolicyIntelligence

        self.copilot = copilot
        self.intel = PolicyIntelligence(copilot)
        self.dlp_cfg = dlp_cfg
        self.mapping = mapping

    def _claims(
        self, answer_claims: list[dict[str, Any]], store: EvidenceStore
    ) -> list[dict[str, Any]]:
        out = []
        for c in answer_claims:
            eid = store.add(
                "P", "POLICY_REQUIREMENT", "uc6", f"verified policy claim ({c.get('citation')})"
            )
            out.append({"evidence_id": eid, "text": c.get("text"), "citation": c.get("citation")})
        return out

    def external_transfer(self, level: str, categories: list[str], dest_class: str, store: EvidenceStore) -> VerifiedPolicyResult:  # fmt: skip
        from app.dlp.integration import policy_question

        q = policy_question(level, categories, dest_class, self.dlp_cfg, self.mapping)
        ctx = self.intel.assess(q, dest_class, level, self.mapping)
        return VerifiedPolicyResult(topic="external_transfer", question=q, status=ctx.status,
                                    claims=self._claims(ctx.claims, store), effect=ctx.effect,
                                    conflict=ctx.conflict, insufficient=ctx.status == "INSUFFICIENT_EVIDENCE")  # fmt: skip

    def topic(self, topic: str, store: EvidenceStore) -> VerifiedPolicyResult:
        q = POLICY_TOPICS[topic]
        a = self.copilot.answer(q, "advanced")
        claims = [{"text": c.text, "citation": c.citation} for c in a.claims if c.verified]
        return VerifiedPolicyResult(topic=topic, question=q, status=a.status, claims=self._claims(claims, store),
                                    conflict=a.status == "CONFLICT_REVIEW",
                                    insufficient=a.status in ("INSUFFICIENT_EVIDENCE", "UNAVAILABLE"))  # fmt: skip
