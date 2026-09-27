"""The demo's human-in-the-loop review queue.

Queue items are REAL `review_required` results produced in this demo session (Classify, or the
seeded fail-safe cases, which are genuine classifications with the LLM tiers turned off). Reviewer
actions are DEMO-ONLY state: each is appended to a separate JSONL file (default
`var/demo/reviews.jsonl`, git-ignored) and marked `not_gold_adjudication: true`. Nothing here reads
or writes the dataset, gold labels, or any evaluation artifact.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .classify import LEVEL_ORDER, DemoError

ACTIONS = ("approve", "override", "escalate")
MAX_NOTE = 500
REPO = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO / "var" / "demo" / "reviews.jsonl"


class ReviewStore:
    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.seeded: set[str] = set()

    @property
    def display_path(self) -> str:
        """Repo-relative inside the repository, so no home-directory path is shown on screen."""
        try:
            return str(self.path.resolve().relative_to(REPO))
        except ValueError:
            return self.path.name

    def add_from_classify(self, payload: dict[str, Any], title: str | None) -> str | None:
        """Queue a classification that the service itself sent to review; anything else is not
        queued (the queue never invents a review)."""
        sm = payload["summary"]
        if not sm["review_required"]:
            return None
        res = payload["result"]
        item = {
            "id": payload["request_id"],
            "source": "classify",
            "title": title or res.get("document_id") or payload["request_id"],
            "ai_level": sm["level"],
            "provisional": sm["provisional"],
            "confidence": sm["level_confidence"],
            "categories": [c["id"] for c in sm["categories"]],
            "reasons": sm["review_reasons"],
            "priority": sm["review_priority"],
            "high_risk": sm["high_risk"],
            "evidence": [
                {
                    "id": e["evidence_id"],
                    "supports": e["supports"],
                    "excerpt": e.get("excerpt"),
                    "provenance": e.get("provenance"),
                }
                for e in res.get("evidence", [])
            ],  # fmt: skip
            "stop_reason": (res.get("routing") or {}).get("stop_reason"),
            "status": "open",
            "decision": None,
            "queued_at": time.time(),
        }
        with self._lock:
            self._items.setdefault(item["id"], item)
        return item["id"]

    def queue(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(
                self._items.values(), key=lambda i: (i["status"] != "open", i["queued_at"])
            )

    def decide(self, body: dict[str, Any]) -> dict[str, Any]:
        item_id, action = body.get("id"), body.get("action")
        note = body.get("note") or ""
        if action not in ACTIONS:
            raise DemoError(f"action must be one of {ACTIONS}")
        if not isinstance(note, str) or len(note) > MAX_NOTE:
            raise DemoError(f"note must be text of at most {MAX_NOTE} characters")
        with self._lock:
            item = self._items.get(item_id)
            if item is None:
                raise DemoError("unknown review item", status=404)
            level = body.get("level")
            if action == "override" and level not in LEVEL_ORDER:
                raise DemoError(f"override needs a level: one of {LEVEL_ORDER}")
            if action == "approve" and not item["ai_level"]:
                raise DemoError(
                    "there is no AI label to approve; override with a level or escalate"
                )
            record = {
                "record_type": "demo_reviewer_decision",
                "not_gold_adjudication": True,
                "item_id": item_id,
                "source": item["source"],
                "action": action,
                "ai_level": item["ai_level"],
                "final_level": level if action == "override" else (
                    item["ai_level"] if action == "approve" else None),
                "note": note,
                "reviewer": "demo reviewer",
                "decided_at": time.time(),
            }  # fmt: skip
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            item["status"] = {
                "approve": "approved",
                "override": "overridden",
                "escalate": "escalated",
            }[action]
            item["decision"] = record
            return {"item": item, "stored_at": self.display_path}
