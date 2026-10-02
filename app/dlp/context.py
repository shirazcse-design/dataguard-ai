"""Synthetic, read-only context: identity, 7-day activity, the DLP exception register, and the
deterministic behaviour bands.

`BehaviorProvider` is the seam UC2 replaces: anything with `assess(user_id, event, identity)`
returning a `Behavior` (band + contributing signals) plugs in unchanged.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from .config import BehaviorConfig
from .schemas import Behavior, DLPEvent, Identity

DATA = Path(__file__).resolve().parents[2] / "data" / "dlp"


class ContextError(Exception):
    """A context source failed. Messages carry codes only."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _load(name: str) -> Any:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


class IdentityStore:
    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        rows = records if records is not None else _load("identities.json")
        self._rows = {r["user_id"]: r for r in rows}

    def get_user_profile(self, user_id: str) -> Identity:
        row = self._rows.get(user_id)
        if row is None:
            raise ContextError("unknown_user")
        return Identity(**{k: row[k] for k in Identity.model_fields})

    def known_destinations(self, user_id: str) -> list[str]:
        return list(self._rows.get(user_id, {}).get("known_destinations", []))


class ActivityStore:
    def __init__(self, records: dict[str, Any] | None = None) -> None:
        self._rows = records if records is not None else _load("activity.json")

    def get_user_activity(self, user_id: str) -> dict[str, Any]:
        row = self._rows.get(user_id)
        if row is None:
            raise ContextError("unknown_user")
        return dict(row)


class ExceptionRegister:
    """DLP exceptions (DLP Policy 5): a record matches only for the same user and host, a level
    at or below its `max_level`, and an event date inside [valid_from, expires]."""

    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self._rows = records if records is not None else _load("exceptions.json")

    def check(
        self, user_id: str, host: str, level_rank: int, ranks: dict[str, int], on: str
    ) -> dict:
        day = date.fromisoformat(on[:10])
        candidates = [
            r for r in self._rows if r["user_id"] == user_id and r["destination_host"] == host
        ]
        for r in candidates:
            if not (date.fromisoformat(r["valid_from"]) <= day <= date.fromisoformat(r["expires"])):
                continue
            if level_rank > ranks[r["max_level"]]:
                continue
            return {"match": True, "exception": r}
        why = "no_record" if not candidates else "expired_or_level_exceeds_exception"
        return {"match": False, "reason": why}


class BehaviorProvider(Protocol):
    name: str

    def assess(self, user_id: str, event: DLPEvent, identity: Identity | None) -> Behavior: ...


class DeterministicBehavior:
    """UC1's lightweight behaviour context: count simple, named signals; bands by count. Not an
    anomaly model (that is UC2)."""

    name = "uc1-deterministic-v1"

    def __init__(
        self, cfg: BehaviorConfig, activity: ActivityStore, identities: IdentityStore
    ) -> None:
        self.cfg = cfg
        self.activity = activity
        self.identities = identities

    def assess(self, user_id: str, event: DLPEvent, identity: Identity | None) -> Behavior:
        a = self.activity.get_user_activity(user_id)
        hour = int(event.timestamp[11:13])
        hours = self.cfg.business_hours
        ratio = a["recent_download_volume_mb_7d"] / max(a["baseline_download_volume_mb_7d"], 1)
        features = {
            "after_hours_activity": not (hours["start"] <= hour < hours["end"]),
            "recent_external_upload_count_7d": a["recent_external_upload_count_7d"],
            "download_volume_ratio": round(ratio, 2),
            "sensitive_file_access_count_7d": a["sensitive_file_access_count_7d"],
            "new_destination": event.destination.host
            not in self.identities.known_destinations(user_id),
        }
        signals = []
        if features["after_hours_activity"]:
            signals.append("after_hours_activity")
        if features["recent_external_upload_count_7d"] >= self.cfg.external_upload_count_7d:
            signals.append("frequent_external_uploads")
        if ratio >= self.cfg.download_volume_ratio:
            signals.append("unusual_download_volume")
        if features["sensitive_file_access_count_7d"] >= self.cfg.sensitive_file_access_7d:
            signals.append("high_sensitive_file_access")
        if features["new_destination"]:
            signals.append("new_destination")
        band = "NORMAL"
        if len(signals) >= self.cfg.bands["UNUSUAL"]:
            band = "UNUSUAL"
        elif len(signals) >= self.cfg.bands["ELEVATED"]:
            band = "ELEVATED"
        return Behavior(band=band, signals=signals, features=features, provider=self.name)
