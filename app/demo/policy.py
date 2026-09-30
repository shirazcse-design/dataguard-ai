"""The demo's Data Security Policy Copilot (UC6) session.

Answers come from the REAL pipeline (`app.policy.service.build_copilot`): REPLAY by default
(recorded embeddings, recorded `uc4-llm-medium` answers and recorded agent turns; no network),
LIVE only when the server was started in live mode. A question that was never recorded is still
answered honestly in REPLAY: the dense leg reports a replay miss and falls back to keyword search,
and generation reports `UNAVAILABLE`. It is never answered some other way.

Reviewer actions are DEMO-ONLY state, appended to `var/demo/policy-reviews.jsonl` (git-ignored)
and marked `not_gold_adjudication: true`. Nothing here reads or writes the golden set or any
evaluation artifact.

`summary()` reads the COMMITTED UC6 result files (docs/uc6/results/), so the numbers on screen are
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
RESULTS = REPO / "docs" / "uc6" / "results"
DEFAULT_REVIEW_PATH = REPO / "var" / "demo" / "policy-reviews.jsonl"
LEVELS = ("naive", "advanced", "agentic")
BACKENDS = ("chat-completions", "foundry-service")
REVIEW_ACTIONS = ("confirm", "escalate_to_policy_owner", "record_policy_gap")
MAX_QUESTION = 2000  # transport cap; the pipeline's own guard rejects anything over 1000
MAX_NOTE = 500

# Curated questions: all from the golden set, so every one is recorded for REPLAY at every level.
# `foundry` = also recorded through the Foundry agent (dataguard-policy-copilot v5).
EXAMPLES: list[dict[str, Any]] = [
    {"key": "cloud", "id": "S01", "title": "Primary demo: confidential data to personal cloud",
     "what": "Straightforward answer, three policies agree, citations verified", "foundry": True},
    {"key": "phi", "id": "M01", "title": "Approvals before sharing PHI with a third party",
     "what": "Multi-part answer across sections of the sharing policy", "foundry": True},
    {"key": "secret", "id": "M03", "title": "I committed an API key to Git: what now?",
     "what": "Multi-policy: secrets standard + incident reporting", "foundry": False},
    {"key": "cctv", "id": "I02", "title": "How long must CCTV footage be retained?",
     "what": "Insufficient evidence: the retention policy does not cover it", "foundry": True},
    {"key": "internal", "id": "C01", "title": "Internal documents in personal cloud storage",
     "what": "Unresolved conflict (Acceptable Use vs DLP) goes to human review", "foundry": False},
    {"key": "retention", "id": "C02", "title": "Retention of customer financial information",
     "what": "Superseded v1.0 vs current v2.0, resolved from metadata", "foundry": True},
    {"key": "injection", "id": "X01", "title": "Prompt injection in the question",
     "what": "Blocked by the input guard before retrieval or any model call", "foundry": True},
    {"key": "vendor", "id": "X02", "title": "Vendor data before the security assessment",
     "what": "The corpus holds a poisoned, unapproved draft; the answer must not follow it",
     "foundry": False},
]  # fmt: skip


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class PolicySession:
    def __init__(
        self, mode: str, env: dict[str, str], review_path: Path | str = DEFAULT_REVIEW_PATH
    ):
        self.mode = mode  # "replay" | "live" (the server's mode)
        self.env = env
        self.review_path = Path(review_path)
        self._copilots: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.pending: dict[str, dict[str, Any]] = {}  # request_id -> answers that need review
        self.decisions: list[dict[str, Any]] = []
        golden = REPO / "evals" / "policy" / "dataset" / "golden.v1.jsonl"
        self.golden = {
            json.loads(line)["id"]: json.loads(line)["question"]
            for line in golden.read_text(encoding="utf-8").splitlines() if line
        }  # fmt: skip
        self.recorded_questions = set(self.golden.values())

    # -- pipeline ----------------------------------------------------------------------------
    def copilot(self, backend: str) -> Any:
        from app.policy.service import build_copilot

        with self._lock:
            if backend not in self._copilots:
                self._copilots[backend] = build_copilot(
                    "live" if self.mode == "live" else "replay",
                    agent_backend=backend,
                    tenant_id=self.env.get("DATAGUARD_TENANT_ID") or None,
                )
            return self._copilots[backend]

    def describe(self) -> dict[str, Any]:
        items = [{**e, "question": self.golden[e["id"]]} for e in EXAMPLES]
        return {"examples": items, "levels": list(LEVELS), "backends": list(BACKENDS),
                "summary": self.summary()}  # fmt: skip

    def ask(self, body: dict[str, Any]) -> dict[str, Any]:
        question = body.get("question")
        if not isinstance(question, str) or not question.strip():
            raise DemoError("a question is required")
        if len(question) > MAX_QUESTION:
            raise DemoError("the question is too long")
        level = body.get("level") or "advanced"
        backend = body.get("backend") or "chat-completions"
        if level not in LEVELS or backend not in BACKENDS:
            raise DemoError("unknown level or agent backend")
        if backend == "foundry-service" and level != "agentic":
            backend = "chat-completions"  # the Foundry agent only exists for the agentic level
        started = time.perf_counter()
        answer = self.copilot(backend).answer(question, level)
        view = answer.model_dump()
        view["answer_text"] = answer.answer_text
        view["backend"] = backend if level == "agentic" else None
        view["recorded_question"] = question in self.recorded_questions
        view["wall_ms"] = round((time.perf_counter() - started) * 1000, 1)
        if answer.review.required:
            self.pending[answer.request_id] = {
                "request_id": answer.request_id, "level": level, "status": answer.status,
                "reasons": list(answer.review.reasons), "question": question[:300],
                "citations": answer.citations, "at": time.strftime("%H:%M:%S"),
            }  # fmt: skip
        return view

    # -- human review (demo-only state) --------------------------------------------------------
    def review_queue(self) -> dict[str, Any]:
        decided = {d["request_id"] for d in self.decisions}
        return {
            "pending": [v for k, v in self.pending.items() if k not in decided][::-1],
            "decisions": self.decisions[::-1][:20],
            "stored_at": self._display_path(),
        }

    def review_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        rid, action = body.get("request_id"), body.get("action")
        note = body.get("note") or ""
        if rid not in self.pending:
            raise DemoError("unknown or already-decided review item", status=404)
        if action not in REVIEW_ACTIONS:
            raise DemoError(f"action must be one of {list(REVIEW_ACTIONS)}")
        if not isinstance(note, str) or len(note) > MAX_NOTE:
            raise DemoError(f"the note must be text of at most {MAX_NOTE} characters")
        item = self.pending[rid]
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "request_id": rid, "action": action,
            "note": note, "level": item["level"], "status": item["status"],
            "reasons": item["reasons"], "not_gold_adjudication": True,
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
        answers = _load_json(RESULTS / "answers.uc6-answer.v2.json") or {}
        retrieval = _load_json(RESULTS / "retrieval.json") or {}
        guard = _load_json(RESULTS / "guardrails-verification.json") or {}
        obs = _load_json(RESULTS / "observability.json") or {}
        foundry_runs = sorted(RESULTS.glob("foundry-evals-*.json"))
        foundry = _load_json(foundry_runs[-1]) if foundry_runs else None
        levels = {
            lv: {"metrics": v["summary"]["metrics"], "agent": v["summary"].get("agent"),
                 "statuses": v["summary"]["status_counts"]}
            for lv, v in (answers.get("levels") or {}).items()
        }  # fmt: skip
        variants = {n: v["summary"]["overall"] | {
            "draft_in_top_k_rate": v["summary"]["draft_in_top_k_rate"]}
            for n, v in (retrieval.get("variants") or {}).items()}  # fmt: skip
        return {
            "answers": {"levels": levels, "provenance": answers.get("provenance")},
            "retrieval": {"variants": variants, "provenance": retrieval.get("provenance")},
            "guardrails": [
                {"id": c["id"], "name": c["name"], "verdict": c.get("verdict")}
                for c in guard.get("cases", [])
            ],
            "guardrails_date": guard.get("date"),
            "observability": {
                "privacy_audit": obs.get("privacy_audit"),
                "by_level": (obs.get("telemetry") or {}).get("by_level"),
            },
            "foundry_evals": foundry,
            "sources": {
                "answers": "docs/uc6/results/answers.uc6-answer.v2.json",
                "retrieval": "docs/uc6/results/retrieval.json",
                "guardrails": "docs/uc6/results/guardrails-verification.json",
                "observability": "docs/uc6/results/observability.json",
                "foundry_evals": str(foundry_runs[-1].relative_to(REPO)) if foundry_runs else None,
            },
        }
