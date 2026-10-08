"""The demo's Incident Investigation (UC5) session.

Incidents run through the REAL pipeline (`app.incident.service.build_investigator`): REPLAY by default
(recorded agent turns, UC4 classifications and UC6 answers; no network), LIVE only when the server was
started in live mode. Nothing here decides or hard-codes a result: the page shows what the services, the
agent, the validator and the severity harness returned.

Nothing is executed. An analyst's decision (CONFIRM / REJECT / MODIFY / MORE_INVESTIGATION) is DEMO-ONLY
state appended to `var/demo/incident-reviews.jsonl` (git-ignored), marked simulated and never gold data.
CRITICAL can be set only here, by the analyst (POL-IR §3: Severity 1 is confirmed loss).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .classify import DemoError

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "docs" / "uc5" / "results"
DEFAULT_REVIEW_PATH = REPO / "var" / "demo" / "incident-reviews.jsonl"
BACKENDS = ("chat-completions", "foundry-service")
ACTIONS = ("CONFIRM", "REJECT", "MODIFY", "MORE_INVESTIGATION")
SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
FOUNDRY_RECORDED = ("INC-001", "INC-003", "INC-012", "INC-014")
MAX_NOTE = 500

EXAMPLES: list[dict[str, Any]] = [
    {"id": "INC-001", "title": "Flagship: possible customer-data exfiltration", "what": "Customer billing exports to a personal cloud account, after hours"},
    {"id": "INC-003", "title": "Legitimate after-hours work", "what": "An after-hours sign-in covered by a verified travel record"},
    {"id": "INC-002", "title": "Benign bulk download", "what": "Month-end volume, high anomaly score, no transfer"},
    {"id": "INC-010", "title": "Conflicting evidence", "what": "A transfer citing a partner approval that had expired"},
    {"id": "INC-016", "title": "Multi-event sequence", "what": "Download, archive, then personal email"},
    {"id": "INC-012", "title": "Prompt injection in a security log", "what": "A log comment tells the investigator to close the case"},
    {"id": "INC-014", "title": "Context leakage and an action request", "what": "A justification asks for another case and an account action"},
    {"id": "INC-011", "title": "Missing log evidence (simulated)", "what": "The security log service is unavailable"},
]  # fmt: skip


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class IncidentSession:
    def __init__(
        self, mode: str, env: dict[str, str], review_path: Path | str = DEFAULT_REVIEW_PATH
    ):
        from app.incident import synth

        self.mode, self.env = mode, env
        self.review_path = Path(review_path)
        self._inv: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.incidents = {i["case_id"]: i for i in synth.load_incidents()}
        self.pending: dict[str, dict[str, Any]] = {}
        self.decisions: list[dict[str, Any]] = []

    def investigator(self, backend: str) -> Any:
        from app.incident.service import build_investigator

        with self._lock:
            if backend not in self._inv:
                svc = next(iter(self._inv.values())).svc if self._inv else None
                self._inv[backend] = build_investigator("live" if self.mode == "live" else "replay", backend=backend,
                                                        tenant_id=self.env.get("DATAGUARD_TENANT_ID") or None, svc=svc)  # fmt: skip
            return self._inv[backend]

    def describe(self) -> dict[str, Any]:
        items = [
            {
                **e,
                "foundry": e["id"] in FOUNDRY_RECORDED,
                "incident": incident_view(self.incidents[e["id"]]),
            }
            for e in EXAMPLES
        ]
        return {"examples": items, "backends": list(BACKENDS), "summary": self.summary()}

    def investigate(self, body: dict[str, Any]) -> dict[str, Any]:
        cid = body.get("case_id")
        if cid not in self.incidents:
            raise DemoError("unknown incident; pick one of the listed cases")
        backend = body.get("backend") or "chat-completions"
        if backend not in BACKENDS:
            raise DemoError("unknown agent backend")
        if backend == "foundry-service" and self.mode != "live" and cid not in FOUNDRY_RECORDED:
            raise DemoError(
                f"the Foundry agent is recorded for {', '.join(FOUNDRY_RECORDED)}; REPLAY cannot run other cases through it"
            )
        t0 = time.perf_counter()
        x = self.investigator(backend).investigate(self.incidents[cid])
        view = result_view(x)
        view.update(
            backend=backend,
            wall_ms=round((time.perf_counter() - t0) * 1000, 1),
            incident=incident_view(self.incidents[cid]),
        )
        d = x.decision
        if d.review_status == "REQUIRED":
            key = f"{cid}:{backend}"
            self.pending[key] = {"key": key, "case_id": cid, "backend": backend, "severity": d.severity,
                                 "reasons": d.review_reasons, "potential_sev1": d.potential_sev1, "at": time.strftime("%H:%M:%S")}  # fmt: skip
        return view

    def review_queue(self) -> dict[str, Any]:
        decided = {d["key"] for d in self.decisions}
        return {"pending": [v for k, v in self.pending.items() if k not in decided][::-1],
                "decisions": self.decisions[::-1][:20], "stored_at": self._display_path()}  # fmt: skip

    def review_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        from app.incident.schemas import AnalystDecision

        key, action, note = body.get("key"), body.get("action"), body.get("note") or ""
        if key not in self.pending or key in {d["key"] for d in self.decisions}:
            raise DemoError("unknown or already-decided incident", status=404)
        if action not in ACTIONS:
            raise DemoError(f"action must be one of {list(ACTIONS)}")
        if not isinstance(note, str) or len(note) > MAX_NOTE:
            raise DemoError(f"the note must be text of at most {MAX_NOTE} characters")
        sev = body.get("severity") if action == "MODIFY" else None
        if action == "MODIFY" and sev not in SEVERITIES:
            raise DemoError(
                f"MODIFY needs a severity from {list(SEVERITIES)} (CRITICAL is analyst-confirmed only)"
            )
        rec = AnalystDecision(
            case_id=self.pending[key]["case_id"], action=action, severity=sev, note=note
        )
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "key": key, **rec.model_dump(),
                  "system_severity": self.pending[key]["severity"], "remediation_executed": False}  # fmt: skip
        self.review_path.parent.mkdir(parents=True, exist_ok=True)
        with self.review_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        self.decisions.append(record)
        return {"recorded": record, "stored_at": self._display_path()}

    def _display_path(self) -> str:
        try:
            return str(self.review_path.resolve().relative_to(REPO))
        except ValueError:
            return self.review_path.name

    def summary(self) -> dict[str, Any]:
        def latest(prefix: str) -> tuple[Path | None, Any]:
            files = sorted(RESULTS.glob(f"{prefix}*.json"))
            return (files[-1], _load_json(files[-1])) if files else (None, None)

        run_path, run = latest("eval-record-run2")
        f_path, foundry = latest("eval-record-foundry-run2-foundry4")
        fe_path, fe = latest("foundry-evals-")
        guard = _load_json(RESULTS / "guardrails-live.json") or {}
        obs = _load_json(RESULTS / "observability.json") or {}
        rel = lambda p: str(p.relative_to(REPO)) if p else None  # noqa: E731
        judges = {
            k: v
            for k, v in ((fe or {}).get("agent_quality") or {}).get("per_criterion", {}).items()
        }
        return {"eval": (run or {}).get("summary"), "eval_verification": (run or {}).get("verification"),
                "foundry4": (foundry or {}).get("summary"),
                "foundry_checks": ((fe or {}).get("outcomes") or {}).get("per_criterion"), "foundry_judges": judges,
                "guardrails": [{"id": p["id"], "name": p["name"], **({"severity": p["app"]["severity"], "base": p["app"]["severity_without_attack"],
                                "stopped_by": p["app"]["stopped_by"], "unsafe": p["app"]["unsafe"]} if p.get("app") else {}),
                                "direct": (p.get("direct") or {}).get("outcome")} for p in guard.get("probes", [])],
                "observability": {"privacy_audit": obs.get("privacy_audit"), "telemetry": obs.get("telemetry")},
                "sources": {"eval": rel(run_path), "foundry4": rel(f_path), "foundry_evals": rel(fe_path),
                            "guardrails": "docs/uc5/results/guardrails-live.json", "observability": "docs/uc5/results/observability.json"}}  # fmt: skip


def incident_view(inc: dict[str, Any]) -> dict[str, Any]:
    """What the alerting system handed over (the agent sees only the alias, not the user id)."""
    from app.incident.services import alias

    return {"case_id": inc["case_id"], "subject": alias(inc["user_id"]), "date": inc["date"], "trigger": inc["trigger"],
            "files": [{"handle": f["handle"], "resource_id": f["resource_id"]} for f in inc["files"]],
            "faults": inc.get("faults", [])}  # fmt: skip


def result_view(x: Any) -> dict[str, Any]:
    """What the analyst sees: the auditable report plus the agent's trace (steps, not its raw messages)."""
    rep = x.final_report()
    run = x.run
    groups: dict[str, list[dict[str, Any]]] = {}
    src_group = {"uc4": "data", "uc2": "behaviour", "uc3": "access", "dlp": "dlp", "uc1": "dlp", "uc6": "policy",
                 "approvals": "approvals", "identity": "identity", "security_log": "logs"}  # fmt: skip
    for eid, item in rep["evidence"].items():
        if item["source"] == "correlation":
            continue
        groups.setdefault(src_group.get(item["source"], item["source"]), []).append(
            {"evidence_id": eid, "claim_type": item["claim_type"], "summary": item["summary"], "time": item.get("timestamp"),
             "retrieved_by_agent": eid in x.state.ledger.returned})  # fmt: skip
    return {
        "case_id": rep["case_id"], "decision": rep["decision"], "executive_summary": rep["executive_summary"],
        "claims": rep["claims"], "timeline": rep["timeline"], "correlations": rep["correlations"],
        "evidence": groups, "gaps": rep["evidence_gaps"], "conflicts": rep["conflicting_evidence"],
        "next_steps": rep["recommended_next_steps"], "validation": {k: v for k, v in x.validation.items() if k != "claims"},
        "facts": x.facts.view(),
        "agent": {"name": run.name, "stopped_reason": run.stopped_reason, "turns": run.turns, "tool_calls": run.tool_calls,
                  "model_calls": run.model_calls, "tokens_in": run.tokens_in, "tokens_out": run.tokens_out,
                  "steps": run.steps, "budget_finish": run.budget_finish, "withheld": run.unsupported_conclusions,
                  "refusals": x.state.refusals, "review_requests": x.state.review_requests} if run else None,
        "totals": x.totals(), "remediation": rep["remediation"],
    }  # fmt: skip
