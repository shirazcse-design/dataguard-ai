"""UC5 evidence services: thin adapters over the EXISTING capabilities, writing typed evidence into a
case-scoped ledger. Nothing here re-implements a capability:

  security logs, behaviour, identity, approvals  -> UC2 services (app/insider/services.py), unchanged
  data sensitivity                               -> UC4 through its MCP adapter (caller
                                                    `incident-investigation-agent`), via UC2's DataService
  policy                                         -> UC6 through UC2's PolicyService (UC1's templated
                                                    external-transfer question and effect mapping)
  destination class                              -> UC1's deterministic destination catalogue ONLY
                                                    (UC1's overall risk score and its employment-status
                                                    signal are deliberately NOT used)
  access paths, stale grants                     -> UC3's access engine over the UC5 access dataset

Evidence ids are assigned HERE from the content (stable across runs and across callers), so the
agent's tools and the harness see the same id for the same fact. Summaries are fixed-vocabulary:
no raw document, log or policy text is put into a summary.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .schemas import EvidenceItem, Incident

CALLER = "incident-investigation-agent"
SALT_ENV = "DATAGUARD_TELEMETRY_SALT"
LEVEL_RANK = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "HIGHLY_CONFIDENTIAL": 3}
USER_ID = re.compile(r"\bu-\d{4}\b")
CASE_ID = re.compile(r"\bINC-\d{3}\b")
NON_TRANSFER_OVERLAYS = (
    "dlp_alert",
)  # DLP records live in the DLP system, not in the security log


class ServiceError(Exception):
    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(kind)
        self.kind, self.detail = kind, detail


def pseudonym(user_id: str) -> str:
    """Telemetry pseudonym, salted per deployment."""
    salt = os.environ.get(SALT_ENV, "dataguard-uc5-demo")
    return "subj-" + hashlib.sha256(f"{salt}|{user_id}".encode()).hexdigest()[:12]


def alias(user_id: str) -> str:
    """The agent-facing subject alias. A FIXED salt: it is part of the agent's input, so it must not vary
    by machine, or recorded replays would miss. User ids never appear in tool results (UC3 lesson)."""
    return "subj-" + hashlib.sha256(f"uc5-subject|{user_id}".encode()).hexdigest()[:10]


def scrub_ids(text: str, user_id: str) -> str:
    """Replace the subject's raw id with its alias in any text handed to the agent."""
    return text.replace(user_id, alias(user_id))


@dataclass
class Ledger:
    """Evidence for ONE case. `returned` is what the agent's tools have shown it."""

    case_id: str
    items: dict[str, EvidenceItem] = field(default_factory=dict)
    returned: set[str] = field(default_factory=set)

    def add(self, **kw: Any) -> str:
        item = EvidenceItem(case_id=self.case_id, **kw)
        self.items.setdefault(item.evidence_id, item)
        return item.evidence_id

    def of(self, *prefixes: str) -> list[EvidenceItem]:
        return [i for k, i in sorted(self.items.items()) if k.startswith(prefixes)]


@dataclass
class IncidentServices:
    uc2: (
        Any  # app.insider.service.Services (logs, behaviour, identity, approvals, data_uc4, policy)
    )
    graph: Any  # app.access.graph.AccessGraph over data/incident/access
    dlp_cfg: Any  # UC1 DlpConfig (destination catalogue)
    scanner: Any  # UC6's injection scanner
    stale_cfg: dict[str, Any]
    mode: str = "replay"
    _cache: dict[tuple, Any] = field(default_factory=dict)

    # -- helpers ---------------------------------------------------------------------------------
    def window(self, inc: Incident) -> tuple[str, str]:
        d = date.fromisoformat(inc.date)
        return (d - timedelta(days=inc.window_days)).isoformat(), inc.trigger.time[:10]

    def _cached(self, key: tuple, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    # -- security logs (UC2) -------------------------------------------------------------------------
    def logs(self, inc: Incident, led: Ledger, *, start: str | None = None, end: str | None = None,
             event_types: list[str] | None = None, limit: int = 60) -> list[dict[str, Any]]:  # fmt: skip
        if "logs_unavailable" in inc.faults:  # SIMULATED
            raise ServiceError("tool_unavailable", "security log service unavailable (simulated)")
        lo, hi = self.window(inc)
        start, end = max(start or lo, lo), min(end or hi, hi)
        overlays = [o for o in inc.overlays if o["type"] not in NON_TRANSFER_OVERLAYS]
        from app.insider.services import ServiceError as Uc2Error

        try:
            res = self.uc2.logs.search(inc.user_id, inc.date, overlays, start=start, end=end,
                                       event_types=event_types or None, limit=limit)  # fmt: skip
        except Uc2Error as err:
            raise ServiceError(err.kind) from err
        out = []
        for e in res["events"]:
            minute = e.get("minute")
            ts = e["time"] if minute is None else f"{e['time'][:14]}{int(minute):02d}"
            eid = "LOG-" + e["id"].removeprefix("L-")
            flagged = bool(e.get("untrusted_flagged"))
            led.add(evidence_id=eid, timestamp=ts, time_precision="minute" if minute is not None else "hour",
                    source="security_log", source_type="log", subject=alias(inc.user_id), event_type=e["type"],
                    summary=log_summary(e), claim_type="OBSERVED_FACT",
                    provenance={"capability": "uc2.LogService", "mode": self.mode},
                    data={k: v for k, v in e.items() if k not in ("id", "time", "text")} | {"injection_flagged": flagged})  # fmt: skip
            view = {"evidence_id": eid, "time": ts, "type": e["type"],
                    **{k: v for k, v in e.items() if k not in ("id", "time", "type", "minute")}}  # fmt: skip
            if isinstance(view.get("text"), str):
                view["text"] = scrub_ids(view["text"], inc.user_id)
                view["untrusted"] = True
            out.append(view)
        return out

    # -- identity (UC2) -----------------------------------------------------------------------------
    def identity(self, inc: Incident, led: Ledger) -> dict[str, Any]:
        if "identity_unavailable" in inc.faults:
            raise ServiceError("tool_unavailable", "identity service unavailable (simulated)")
        from app.insider.services import EvidenceStore

        c = self.uc2.identity.context(inc.user_id, EvidenceStore())
        view = {"role": c.role, "role_family": c.role_family, "department": c.department,
                "privilege_level": c.privilege_level, "work_hours": c.work_hours,
                "expected_data_classes": c.expected_data_classes}  # fmt: skip
        led.add(evidence_id="IDN", source="identity", source_type="profile", subject=alias(inc.user_id),
                event_type="identity_profile", claim_type="OBSERVED_FACT",
                summary=f"{c.role} ({c.role_family}), privilege {c.privilege_level}, expected data classes "
                        f"{', '.join(c.expected_data_classes)}",
                provenance={"capability": "uc2.IdentityService"}, data=view)  # fmt: skip
        return view

    # -- behaviour (UC2 Isolation Forest, authoritative) ---------------------------------------------
    def behavior(self, inc: Incident, led: Ledger) -> dict[str, Any]:
        from app.insider.services import EvidenceStore
        from app.insider.services import ServiceError as Uc2Error

        try:
            a = self.uc2.behavior.anomaly(inc.user_id, inc.date, EvidenceStore())
        except Uc2Error as err:
            raise ServiceError(err.kind) from err
        sig_ids = []
        for s in a.contributing_signals:
            sid = led.add(evidence_id=f"UC2-SIG-{s['feature']}", timestamp=inc.date, time_precision="day",
                          source="uc2", source_type="model", subject=alias(inc.user_id), event_type="anomaly_signal",
                          claim_type="DETERMINISTIC_FINDING",
                          summary=f"{s['feature']} contributed to the anomaly score (drop if reset {s['score_drop_if_reset']})",
                          provenance={"capability": "uc2.isolation_forest", "model_version": a.model_version},
                          data=dict(s))  # fmt: skip
            sig_ids.append(sid)
        led.add(evidence_id="UC2-ANOM", timestamp=inc.date, time_precision="day", source="uc2", source_type="model",
                subject=alias(inc.user_id), event_type="anomaly_score", claim_type="DETERMINISTIC_FINDING",
                summary=f"anomaly score {a.anomaly_score} band {a.anomaly_band} for the day",
                provenance={"capability": "uc2.isolation_forest", "model_version": a.model_version,
                            "fingerprint": a.model_fingerprint},
                data={"band": a.anomaly_band, "score": a.anomaly_score}, related_evidence_ids=sig_ids)  # fmt: skip
        return {"anomaly_score": a.anomaly_score, "anomaly_band": a.anomaly_band, "model_version": a.model_version,
                "signals": [{"evidence_id": i, "feature": s["feature"], "score_drop_if_reset": s["score_drop_if_reset"]}
                            for i, s in zip(sig_ids, a.contributing_signals, strict=True)],
                "note": "an anomaly is not evidence of intent"}  # fmt: skip

    # -- access (UC3 engine over the UC5 access dataset) ---------------------------------------------
    def case_resources(self, inc: Incident) -> list[str]:
        return sorted({f.resource_id for f in inc.files})

    def access(self, inc: Incident, led: Ledger, resource_id: str) -> dict[str, Any]:
        if resource_id not in self.case_resources(inc):
            raise ServiceError(
                "out_of_scope", "resource_id must be one of the case files' resources"
            )
        if "identity_unavailable" in inc.faults:
            raise ServiceError("tool_unavailable", "identity service unavailable (simulated)")
        from app.access.governance import stale_grants

        g = self.graph
        try:
            paths = g.paths(inc.user_id, resource_id)
        except KeyError as err:
            raise ServiceError("unknown_subject") from err
        stale = {s["grant_id"]: s for s in stale_grants(g, inc.user_id, self.stale_cfg)}
        res_meta = g.resource(resource_id)
        out, ids = [], []
        for i, p in enumerate(paths, 1):
            pid = led.add(evidence_id=f"UC3-PATH-{resource_id}-{i}", source="uc3", source_type="graph",
                          subject=alias(inc.user_id), event_type="access_path", claim_type="OBSERVED_FACT",
                          summary=f"holds {p.entitlement_id} via {p.via_kind} {p.via_id} ({'active' if p.active else 'not active'} "
                                  f"at the {g.as_of} snapshot)",
                          provenance={"capability": "uc3.AccessGraph", "as_of": g.as_of},
                          data={"entitlement_id": p.entitlement_id, "via": p.via_kind, "via_id": p.via_id,
                                "active": p.active, "expires": p.expires, "resource_id": resource_id})  # fmt: skip
            ids.append(pid)
            view = {"evidence_id": pid, "entitlement_id": p.entitlement_id, "via": p.via_kind, "via_id": p.via_id,
                    "active": p.active, "expires": p.expires}  # fmt: skip
            if p.via_kind == "direct" and p.via_id in stale:
                s = stale[p.via_id]
                sid = led.add(evidence_id=f"UC3-STALE-{p.via_id}", source="uc3", source_type="rule",
                              subject=alias(inc.user_id), event_type="stale_grant", claim_type="DETERMINISTIC_FINDING",
                              summary=f"direct grant {p.via_id} for {p.entitlement_id} idle {s['idle_days']} days "
                                      f"(stale at {self.stale_cfg['stale_after_days']}+)",
                              provenance={"capability": "uc3.governance.stale_grants", "as_of": g.as_of},
                              data={"grant_id": p.via_id, "idle_days": s["idle_days"], "resource_id": resource_id},
                              related_evidence_ids=[pid])  # fmt: skip
                ids.append(sid)
                view["stale"] = {"evidence_id": sid, "idle_days": s["idle_days"]}
            out.append(view)
        if not any(p.active for p in paths):
            nid = led.add(evidence_id=f"UC3-NOPATH-{resource_id}", source="uc3", source_type="graph",
                          subject=alias(inc.user_id), event_type="no_active_path", claim_type="OBSERVED_FACT",
                          summary=f"no active access path to {resource_id} in the {g.as_of} snapshot",
                          provenance={"capability": "uc3.AccessGraph", "as_of": g.as_of},
                          data={"resource_id": resource_id})  # fmt: skip
            ids.append(nid)
        return {"resource_id": resource_id, "resource_name": res_meta["name"], "owner": res_meta["owner"],
                "snapshot_as_of": g.as_of, "paths": out, "evidence_ids": ids}  # fmt: skip

    # -- DLP alerts + UC1 destination catalogue -------------------------------------------------------
    def dlp(self, inc: Incident, led: Ledger, host: str | None = None) -> dict[str, Any]:
        from app.dlp.prechecks import classify_destination

        alerts = []
        for o in inc.overlays:
            if o["type"] != "dlp_alert":
                continue
            ts = f"{inc.date}T{int(o['hour']):02d}:{int(o.get('minute', 0)):02d}"
            dclass = classify_destination(o["destination"], o["account_type"], self.dlp_cfg)
            aid = f"DLP-{o['alert_id'].split('-')[-1]}"
            did = self._dest(inc, led, o["destination"], o["account_type"], dclass)
            just = o.get("user_justification")
            flagged = self.justification_flagged(inc, just) if just else False
            led.add(evidence_id=aid, timestamp=ts, time_precision="minute", source="dlp", source_type="alert",
                    subject=alias(inc.user_id), event_type="dlp_alert", claim_type="OBSERVED_FACT",
                    summary=f"DLP alert {o['alert_id']}: {o['channel']} to {o['destination']} ({o['account_type']}), "
                            f"files {', '.join(o['files'])}",
                    provenance={"capability": "dlp_system"}, related_evidence_ids=[did],
                    data={"destination": o["destination"], "account_type": o["account_type"], "destination_class": dclass,
                          "files": o["files"], "channel": o["channel"], "justification_flagged": flagged,
                          "has_justification": bool(just)})  # fmt: skip
            view = {"evidence_id": aid, "time": ts, "alert_id": o["alert_id"], "channel": o["channel"],
                    "destination": o["destination"], "account_type": o["account_type"], "files": o["files"],
                    "destination_class": {"evidence_id": did, "class": dclass}}  # fmt: skip
            if just:
                view["user_justification"] = ("[withheld: instruction-like text or a reference to another user or case]"
                                              if flagged else scrub_ids(just, inc.user_id))  # fmt: skip
                view["user_justification_untrusted"] = True
            alerts.append(view)
        out: dict[str, Any] = {"alerts": alerts}
        if host:
            seen = {
                i.data.get("destination")
                for i in led.items.values()
                if i.evidence_id in led.returned
            }
            if host not in seen:
                raise ServiceError(
                    "out_of_scope", "host must appear in evidence already returned for this case"
                )
            acct = next((i.data.get("account_type") for i in led.items.values()
                         if i.evidence_id in led.returned and i.data.get("destination") == host), "personal")  # fmt: skip
            dclass = classify_destination(host, acct or "personal", self.dlp_cfg)
            out["destination"] = {"evidence_id": self._dest(inc, led, host, acct or "personal", dclass), "host": host,
                                  "class": dclass}  # fmt: skip
        return out

    def _dest(self, inc: Incident, led: Ledger, host: str, account: str, dclass: str) -> str:
        return led.add(evidence_id=f"UC1-DEST-{host}", source="uc1", source_type="catalogue",
                       event_type="destination_class", claim_type="DETERMINISTIC_FINDING",
                       summary=f"{host} ({account}) is {dclass} in the UC1 destination catalogue",
                       provenance={"capability": "uc1.classify_destination"},
                       data={"destination": host, "account_type": account, "destination_class": dclass})  # fmt: skip

    def justification_flagged(self, inc: Incident, text: str) -> bool:
        """Instruction-like (UC6 scanner) or naming another user or case (the UC3 GP6 lesson)."""
        others = set(USER_ID.findall(text)) - {inc.user_id}
        cases = set(CASE_ID.findall(text)) - {inc.case_id}
        return bool(self.scanner.scan(text)) or bool(others) or bool(cases)

    # -- data sensitivity (UC4, authoritative) --------------------------------------------------------
    def classify(
        self, inc: Incident, led: Ledger, handles: list[str] | None = None
    ) -> list[dict[str, Any]]:
        from app.insider.services import EvidenceStore

        files = {f.handle: f for f in inc.files}
        want = handles or sorted(files)
        bad = [h for h in want if h not in files]
        if bad:
            raise ServiceError(
                "out_of_scope",
                f"unknown file handle(s) {bad}; only the case's files can be classified",
            )
        out = []
        for h in want:
            f = files[h]
            key = ("uc4", f.ref, "uc4_unavailable" in inc.faults)
            summ = self._cached(key, lambda f=f: self.uc2.data_uc4.classify(
                [f.ref], inc.case_id, EvidenceStore(), semantic_unavailable="uc4_unavailable" in inc.faults))  # fmt: skip
            fs = summ.files[0]
            ok = fs.ok and fs.level is not None
            cid = led.add(evidence_id=f"UC4-{h}", source="uc4", source_type="classifier", event_type="classification",
                          claim_type="DETERMINISTIC_FINDING" if ok else "UNKNOWN_OR_GAP", confidence=(fs.confidence or "low") if ok else "low",
                          summary=(f"{h} classified {fs.level} ({', '.join(fs.categories) or 'no category'})" if ok
                                   else f"{h}: UC4 could not establish a sensitivity level"),
                          provenance={"capability": "uc4.classify_document", "caller": CALLER, "mode": self.mode},
                          data={"handle": h, "resource_id": f.resource_id, "level": fs.level if ok else None,
                                "categories": fs.categories, "review_required": fs.review_required,
                                "injection_flagged": fs.injection_flagged, "ok": ok})  # fmt: skip
            out.append({"evidence_id": cid, "handle": h, "resource_id": f.resource_id, "level": fs.level if ok else None,
                        "categories": fs.categories, "confidence": fs.confidence, "review_required": fs.review_required,
                        "injection_flagged": fs.injection_flagged, "status": "ok" if ok else "unavailable"})  # fmt: skip
        return out

    # -- policy (UC6, authoritative) -------------------------------------------------------------------
    def policy(self, inc: Incident, led: Ledger, topic: str, *, level: str | None = None,
               categories: list[str] | None = None, destination_class: str | None = None) -> dict[str, Any]:  # fmt: skip
        if "policy_unavailable" in inc.faults:
            raise ServiceError("tool_unavailable", "policy service unavailable (simulated)")
        from app.insider.services import EvidenceStore

        if topic == "external_transfer":
            key = ("uc6", topic, level, tuple(categories or []), destination_class)
            r = self._cached(key, lambda: self.uc2.policy.external_transfer(level, list(categories or []),
                                                                             destination_class, EvidenceStore()))  # fmt: skip
        else:
            r = self._cached(("uc6", topic), lambda: self.uc2.policy.topic(topic, EvidenceStore()))
        ids, claims = [], []
        for c in r.claims:
            pid = led.add(evidence_id="UC6-" + re.sub(r"[^A-Za-z0-9.]+", "-", c["citation"]).strip("-"),
                          source="uc6", source_type="policy", event_type="policy_claim", claim_type="POLICY_REQUIREMENT",
                          summary=f"verified policy claim ({c['citation']})",
                          provenance={"capability": "uc6.copilot", "mode": self.mode},
                          data={"citation": c["citation"], "topic": topic})  # fmt: skip
            if pid not in ids:
                ids.append(pid)
                claims.append({"evidence_id": pid, "citation": c["citation"], "text": c["text"]})
        tag = f"{topic}" + (f"-{level}-{destination_class}" if topic == "external_transfer" else "")
        insufficient = r.insufficient or (topic == "external_transfer" and r.effect == "unknown")
        eff = led.add(evidence_id=f"UC6-RESULT-{tag}", source="uc6", source_type="policy", event_type="policy_result",
                      claim_type="DETERMINISTIC_FINDING" if not insufficient else "UNKNOWN_OR_GAP",
                      summary=(f"policy result for {topic}: status {r.status}, effect {r.effect}"
                               + (", conflict" if r.conflict else "") + (", insufficient policy evidence" if insufficient else "")),
                      provenance={"capability": "uc6 + uc1 effect mapping" if topic == "external_transfer" else "uc6"},
                      data={"topic": topic, "status": r.status, "effect": r.effect, "conflict": r.conflict,
                            "insufficient": insufficient, "level": level, "destination_class": destination_class},
                      related_evidence_ids=ids)  # fmt: skip
        return {"topic": topic, "status": r.status, "effect": r.effect, "conflict": r.conflict,
                "insufficient_policy_evidence": insufficient, "result_evidence_id": eff, "claims": claims,
                "evidence_ids": [eff, *ids]}  # fmt: skip

    # -- approvals (UC2 register) ----------------------------------------------------------------------
    def approval(self, inc: Incident, led: Ledger, ref: str) -> dict[str, Any]:
        r = self.uc2.approvals.check(ref, inc.user_id, inc.date)
        aid = led.add(evidence_id=f"APR-{ref}", source="approvals", source_type="register", subject=alias(inc.user_id),
                      event_type="approval_check", claim_type="OBSERVED_FACT",
                      summary=(f"approval {ref}: " + ("verified for this subject and date" if r["verified"]
                               else ("not found" if not r["found"] else "expired or not this subject"))),
                      provenance={"capability": "uc2.ApprovalService"},
                      data={k: v for k, v in r.items() if k != "evidence_id"} | {"ref": ref})  # fmt: skip
        return {
            "evidence_id": aid,
            "ref": ref,
            **{k: v for k, v in r.items() if k not in ("evidence_id",)},
        }


def log_summary(e: dict[str, Any]) -> str:
    t = e["type"]
    if t == "bulk_download":
        return f"bulk download: {e.get('files')} files ({e.get('sensitive_files')} sensitive) from {e.get('repositories')} repositories"
    if t == "file_download_batch":
        return f"file downloads: {e.get('files')} files ({e.get('sensitive_files')} sensitive)"
    if t == "external_upload":
        return f"external upload to {e.get('destination')} ({e.get('account_type')}), {e.get('volume_mb')} MB"
    if t == "login":
        return "sign-in"
    if t == "after_hours_login":
        return f"after-hours sign-in ({e.get('sessions', 1)} sessions)"
    if t == "repo_access_new":
        return "first access to a repository not used before"
    if t == "auth_failure":
        return f"{e.get('attempts')} failed access attempts"
    if t in ("change_ticket", "travel_record", "partner_transfer_approval", "access_request"):
        return f"{t.replace('_', ' ')} reference {e.get('ref')} recorded"
    if t == "archive_created":
        return f"archive created containing {', '.join(e.get('files', []))}"
    if t == "log_comment":
        return "free-text log comment (untrusted)" + (
            " - withheld as instruction-like" if e.get("untrusted_flagged") else ""
        )
    return t.replace("_", " ")


def build_services(mode: str = "replay") -> IncidentServices:
    """UC5's own wiring of the shared capabilities (UC4 caller `incident-investigation-agent`)."""
    from app.access.governance import load_config as load_access_config
    from app.access.graph import AccessGraph
    from app.classification.service import ClassificationService
    from app.dlp.config import load_dlp_configs
    from app.dlp.integration import DocumentStore, Uc4Classifier
    from app.insider.service import Services
    from app.insider.services import (
        ApprovalService,
        BehaviorService,
        DataService,
        IdentityService,
        LogService,
        PolicyService,
    )
    from app.policy.guard import load_policy_scanner
    from app.policy.service import build_copilot
    from mcp_adapter.adapter import ClassifyDocumentAdapter
    from mcp_adapter.config import load_mcp_config

    from . import synth

    uc4_mode = {"offline": "replay", "replay": "replay", "live": "foundry", "record": "record"}[
        mode
    ]
    uc6_mode = {"offline": "offline", "replay": "replay", "live": "live", "record": "record"}[mode]
    dlp, mapping, _, _ = load_dlp_configs()
    mcp_cfg, _ = load_mcp_config()
    adapter = ClassifyDocumentAdapter(
        ClassificationService(llm_mode=uc4_mode), mcp_cfg, caller_id=CALLER
    )
    copilot = build_copilot(uc6_mode)
    scanner = load_policy_scanner(copilot.cfg.guard)
    data = _insider_data()
    uc2 = Services(data=data, behavior=BehaviorService(data), identity=IdentityService(data),
                   logs=LogService(data, scanner), approvals=ApprovalService(data),
                   data_uc4=DataService(Uc4Classifier(adapter), DocumentStore()),
                   policy=PolicyService(copilot, dlp, mapping), copilot=copilot, dlp_cfg=dlp)  # fmt: skip
    return IncidentServices(uc2=uc2, graph=AccessGraph(synth.ACCESS), dlp_cfg=dlp, scanner=scanner,
                            stale_cfg=load_access_config(), mode=mode)  # fmt: skip


_DATA: list[Any] = []


def _insider_data() -> Any:
    """UC2's data and fitted Isolation Forest, loaded once per process (deterministic)."""
    if not _DATA:
        from app.insider.services import InsiderData

        _DATA.append(InsiderData())
    return _DATA[0]
