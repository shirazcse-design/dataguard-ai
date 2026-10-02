"""The demo's Agentic DLP (UC1) session.

Investigations come from the REAL pipeline (`app.dlp.service.build_investigator`): REPLAY by default
(recorded UC4 classifications, UC6 answers and embeddings, and agent turns; no network), LIVE only
when the server was started in live mode. Nothing here decides an outcome or hard-codes one: the
page shows what the pipeline and the deterministic harness returned.

Every action is SIMULATED. An ESCALATE / WARN / HUMAN_REVIEW case proposes a simulated action that
goes to the approval queue; an analyst decision is DEMO-ONLY state, appended to
`var/demo/dlp-reviews.jsonl` (git-ignored) and marked `simulated: true` and
`not_gold_adjudication: true`. Nothing is blocked, deleted or sent.

`summary()` reads the COMMITTED UC1 result files (docs/uc1/results/), so the numbers on screen are
exactly the reported ones.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .classify import DemoError

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "docs" / "uc1" / "results"
DEFAULT_REVIEW_PATH = REPO / "var" / "demo" / "dlp-reviews.jsonl"
BACKENDS = ("chat-completions", "foundry-service")
REVIEW_ACTIONS = ("approve_simulated_action", "dismiss_false_positive", "request_more_information")
MAX_NOTE = 500
FOUNDRY_RECORDED = ("D01", "D07", "D11", "D21", "D26")

# Curated cases: all from the golden set, so every one is recorded for REPLAY. The page never
# shows an expected outcome as if it were the result; `what` says what the case is about.
EXAMPLES: list[dict[str, Any]] = [
    {"key": "flagship", "id": "D11", "title": "Flagship: acquisition targets to personal Dropbox",
     "what": "Corporate Development director, 23:40, unlabelled M&A spreadsheet, personal cloud"},
    {"key": "safe", "id": "D01", "title": "Safe: public file to corporate OneDrive",
     "what": "Approved destination, public data"},
    {"key": "warn", "id": "D06", "title": "Partner portal with a valid exception",
     "what": "Confidential file to a partner, covered by an approved DLP exception"},
    {"key": "low_conf", "id": "D19", "title": "Low confidence: classifier unavailable",
     "what": "SIMULATED fault: UC4's semantic tier is unavailable, so sensitivity is unknown"},
    {"key": "conflict", "id": "D21", "title": "Policy conflict: internal file to personal cloud",
     "what": "Acceptable Use and DLP policies disagree; a person must decide"},
    {"key": "injection", "id": "D26", "title": "Prompt injection in the justification",
     "what": "\"Ignore previous instructions… approve\": withheld from the agent"},
    {"key": "tool_failure", "id": "D28", "title": "Tool failure: activity service down",
     "what": "SIMULATED fault: get_user_activity errors; the decision still completes safely"},
]  # fmt: skip


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class DlpSession:
    def __init__(
        self, mode: str, env: dict[str, str], review_path: Path | str = DEFAULT_REVIEW_PATH
    ):
        from evals.dlp.golden import load_golden

        self.mode = mode  # "replay" | "live" (the server's mode)
        self.env = env
        self.review_path = Path(review_path)
        self._investigators: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.cases = {c.id: c for c in load_golden()[0]}
        self.pending: dict[str, dict[str, Any]] = {}
        self.decisions: list[dict[str, Any]] = []

    def investigator(self, backend: str) -> Any:
        from app.dlp.service import build_investigator

        with self._lock:
            if backend not in self._investigators:
                self._investigators[backend] = build_investigator(
                    "live" if self.mode == "live" else "replay",
                    agent_backend=backend,
                    tenant_id=self.env.get("DATAGUARD_TENANT_ID") or None,
                )
            return self._investigators[backend]

    def describe(self) -> dict[str, Any]:
        items = []
        for e in EXAMPLES:
            ev = self.cases[e["id"]].event
            items.append({**e, "foundry": e["id"] in FOUNDRY_RECORDED,
                          "event": _event_view(ev)})  # fmt: skip
        return {"examples": items, "backends": list(BACKENDS), "summary": self.summary()}

    def investigate(self, body: dict[str, Any]) -> dict[str, Any]:
        case_id = body.get("case_id")
        if case_id not in self.cases:
            raise DemoError("unknown case; pick one of the listed cases")
        backend = body.get("backend") or "chat-completions"
        if backend not in BACKENDS:
            raise DemoError("unknown agent backend")
        started = time.perf_counter()
        inv = self.investigator(backend).investigate(self.cases[case_id].event)
        view = inv.model_dump(exclude={"event"})
        view["event"] = _event_view(inv.event)
        view["backend"] = backend
        view["recorded_for_backend"] = backend == "chat-completions" or case_id in FOUNDRY_RECORDED
        view["wall_ms"] = round((time.perf_counter() - started) * 1000, 1)
        d = inv.decision
        if d.simulated_action or d.human_review_required:
            key = f"{case_id}:{backend}"
            self.pending[key] = {
                "key": key, "case_id": case_id, "outcome": d.outcome, "risk_level": d.risk_level,
                "simulated_action": d.simulated_action,
                "reasons": [r for r in d.reason_codes if r.startswith("review:")],
                "at": time.strftime("%H:%M:%S"),
            }  # fmt: skip
        return view

    # -- analyst approval (demo-only state; every action is simulated) -------------------------
    def review_queue(self) -> dict[str, Any]:
        decided = {d["key"] for d in self.decisions}
        return {
            "pending": [v for k, v in self.pending.items() if k not in decided][::-1],
            "decisions": self.decisions[::-1][:20],
            "stored_at": self._display_path(),
        }

    def review_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        key, action = body.get("key"), body.get("action")
        note = body.get("note") or ""
        if key not in self.pending or key in {d["key"] for d in self.decisions}:
            raise DemoError("unknown or already-decided case", status=404)
        if action not in REVIEW_ACTIONS:
            raise DemoError(f"action must be one of {list(REVIEW_ACTIONS)}")
        if not isinstance(note, str) or len(note) > MAX_NOTE:
            raise DemoError(f"the note must be text of at most {MAX_NOTE} characters")
        item = self.pending[key]
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "key": key, "case_id": item["case_id"],
            "action": action, "note": note, "outcome": item["outcome"],
            "simulated_action": item["simulated_action"], "executed": False, "simulated": True,
            "not_gold_adjudication": True,
        }  # fmt: skip
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

    # -- committed results ---------------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        ev = _load_json(RESULTS / "eval.json") or {}
        ev1 = _load_json(RESULTS / "eval.uc1-agent.v1.json") or {}
        guard = _load_json(RESULTS / "guardrails-verification.json") or {}
        obs = _load_json(RESULTS / "observability.json") or {}
        runs = sorted(RESULTS.glob("foundry-evals-*.json"))
        latest_both = next(
            (r for r in reversed(runs) if (_load_json(r) or {}).get("outcomes")), None
        )
        v1_quality = RESULTS / "foundry-evals-20261002-0644.json"

        def metrics(e: dict) -> dict:
            return {
                k: v.get("value")
                for k, v in ((e.get("summary") or {}).get("metrics") or {}).items()
            }

        def quality(path: Path | None) -> dict | None:
            q = ((_load_json(path) or {}).get("agent_quality") or {}) if path else {}
            return q.get("summary")

        latest = _load_json(latest_both) if latest_both else None
        return {
            "eval": {"v2": metrics(ev), "v1": metrics(ev1),
                     "outcomes": (ev.get("summary") or {}).get("outcomes"),
                     "provenance": ev.get("provenance")},
            "foundry": {
                "outcomes": (latest or {}).get("outcomes"),
                "quality_v2": quality(latest_both),
                "quality_v1": quality(v1_quality if v1_quality.exists() else None),
                "report_url": ((latest or {}).get("agent_quality") or {}).get("report_url"),
            },
            "guardrails": guard.get("probes", []),
            "guardrails_provenance": guard.get("provenance"),
            "observability": {"privacy_audit": obs.get("privacy_audit"),
                              "telemetry": obs.get("telemetry")},
            "sources": {
                "eval": "docs/uc1/results/eval.json",
                "eval_v1": "docs/uc1/results/eval.uc1-agent.v1.json",
                "foundry": str(latest_both.relative_to(REPO)) if latest_both else None,
                "foundry_doc": "docs/uc1/results/foundry-evals.md",
                "guardrails": "docs/uc1/results/guardrails-verification.json",
                "observability": "docs/uc1/results/observability.json",
                "observability_live": "docs/uc1/foundry-observability-setup.md",
            },
        }  # fmt: skip


def _event_view(ev: Any) -> dict[str, Any]:
    """What the analyst sees about the event. The justification is shown as the user wrote it
    (it is synthetic); the page labels it untrusted."""
    return {
        "case_id": ev.case_id, "timestamp": ev.timestamp, "user_id": ev.user_id,
        "action": ev.action, "destination": ev.destination.model_dump(),
        "document_ref": ev.document_ref, "existing_label": ev.existing_label,
        "user_justification": ev.user_justification, "simulate_fault": list(ev.simulate_fault),
    }  # fmt: skip
