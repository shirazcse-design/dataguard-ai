"""UC3 deterministic least-privilege and segregation-of-duties engine. AUTHORITATIVE: findings here
are DETERMINISTIC_CONTROL_RESULTs. The agent may explain them; it cannot add, remove or change one,
and the authorization harness recomputes them itself.

Inputs: the access graph, one request, the policy limits that apply (from requirements.v1.yaml) and,
when known, UC4's sensitivity level for the resource. Free-text justifications are never read here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from . import synth
from .graph import PRIVILEGE_RANK, AccessGraph

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "access" / "governance.v1.yaml"
HIGH_SENSITIVITY = ("HIGHLY_CONFIDENTIAL",)


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@dataclass
class Signal:
    code: str
    detail: str
    contextual: bool = False  # context only: shown and explained, never decides an outcome
    data: dict[str, Any] = field(default_factory=dict)

    def view(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "contextual": self.contextual,
                "claim_type": "DETERMINISTIC_CONTROL_RESULT", **({"data": self.data} if self.data else {})}  # fmt: skip


@dataclass
class GovernanceResult:
    request_id: str
    governance_version: str
    signals: list[Signal]
    sod: list[dict[str, Any]]
    alternative: (
        dict[str, Any] | None
    )  # the least-privilege entitlement + duration, when one exists
    allowed_hours: float | None  # the longest duration the policy AND the business need allow
    need: dict[str, Any] | None  # what the purpose needs (privilege, full scope)

    def codes(self) -> list[str]:
        return [s.code for s in self.signals]

    def has(self, code: str) -> bool:
        return code in self.codes()

    def view(self) -> dict[str, Any]:
        return {"request_id": self.request_id, "governance_version": self.governance_version,
                "signals": [s.view() for s in self.signals], "sod": self.sod,
                "alternative": self.alternative, "allowed_hours": self.allowed_hours, "need": self.need}  # fmt: skip


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def satisfies(graph: AccessGraph, eid: str, need: dict[str, Any]) -> bool:
    """Does entitlement `eid` give everything the purpose needs?"""
    e = graph.entitlement(eid)
    if PRIVILEGE_RANK[e["privilege"]] < PRIVILEGE_RANK[need["privilege"]]:
        return False
    return not (need["full_scope"] and e["scope"] == "sanitized")


def _breadth(graph: AccessGraph, eid: str) -> tuple[int, int]:
    e = graph.entitlement(eid)
    return (PRIVILEGE_RANK[e["privilege"]], 1 if e["scope"] == "full" else 0)


def sod_conflicts(
    graph: AccessGraph, uid: str, eid: str, cfg: dict[str, Any]
) -> list[dict[str, Any]]:
    """SoD pairs the request would complete: the user already holds one side, asks for the other."""
    have = graph.effective(uid)
    out = []
    for r in cfg["sod_rules"]:
        for asked, held in ((r["a"], r["b"]), (r["b"], r["a"])):
            if eid == asked and held in have:
                via = [
                    g.view()["path"]
                    for g in graph.grants(uid, include_inactive=False)
                    if g.entitlement_id == held
                ]
                out.append(
                    {
                        "rule_id": r["rule_id"],
                        "requested": asked,
                        "held": held,
                        "why": r["why"],
                        "held_via": via,
                    }
                )
    return out


def approved_exception(uid: str, rule_id: str, as_of: str) -> dict[str, Any] | None:
    """A register lookup: an APPROVED exception for this user and SoD rule, valid on `as_of`."""
    for x in synth.load("exceptions.json"):
        if x["user_id"] == uid and x.get("rule_id") == rule_id and x["status"] == "approved" \
                and x["valid_from"] <= as_of <= x["valid_to"]:  # fmt: skip
            return x
    return None


def stale_grants(graph: AccessGraph, uid: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Active DIRECT grants unused for `stale_after_days` (or never used since granted that long)."""
    out = []
    for g in graph.grants(uid, include_inactive=False):
        if g.via_kind != "direct":
            continue
        use = graph.last_use(uid, g.entitlement_id)
        since = (
            use["last_used"]
            if use
            else next(
                e.attrs["granted"]
                for e in graph.edges
                if e.rel == "DIRECT_GRANT" and e.attrs.get("grant_id") == g.via_id
            )
        )
        idle = _days(since, graph.as_of)
        if idle >= cfg["stale_after_days"]:
            out.append({"entitlement_id": g.entitlement_id, "grant_id": g.via_id, "idle_days": idle,
                        "last_used": use["last_used"] if use else None})  # fmt: skip
    return out


def evaluate(graph: AccessGraph, req: dict[str, Any], *, policy_max_hours: float | None = None,
             sensitivity: str | None = None, cfg: dict[str, Any] | None = None) -> GovernanceResult:  # fmt: skip
    cfg = cfg or load_config()
    uid, eid = req["user_id"], req["entitlement_id"]
    ent = graph.entitlement(eid)
    user = graph.user(uid)
    signals: list[Signal] = []
    need = cfg["purposes"].get(req.get("purpose_category") or "")

    if need is None:
        signals.append(
            Signal("BUSINESS_PURPOSE_MISSING", "no recognised purpose category on the request")
        )

    # Duration: the policy maximum and the business need (the project end) both bound it.
    allowed = policy_max_hours
    project = req.get("project_id")
    if project:
        if project not in user["projects"]:
            signals.append(Signal("PROJECT_NOT_ASSIGNED", f"the request cites {project}, but the user is not assigned to it",
                                  data={"project_id": project}))  # fmt: skip
        else:
            left = _days(graph.as_of, graph.nodes[project].attrs["ends"]) * 24
            allowed = left if allowed is None else min(allowed, left)
    requested_hours = req["duration_days"] * 24
    if allowed is not None and requested_hours > allowed:
        signals.append(Signal("DURATION_EXCESSIVE", f"{req['duration_days']} days requested; {allowed / 24:g} days allowed by policy and business need",
                              data={"requested_hours": requested_hours, "allowed_hours": allowed}))  # fmt: skip

    if ent["privileged"]:
        signals.append(Signal("PRIVILEGED_ACCESS", "administrator access"))
    if sensitivity in HIGH_SENSITIVITY:
        signals.append(
            Signal(
                "HIGH_SENSITIVITY_RESOURCE", f"UC4 classifies the resource's data as {sensitivity}"
            )
        )

    # Least privilege over the entitlement family (entitlements over the same data).
    lower = None
    if need is not None:
        holding = sorted(
            x for x in graph.family(eid) if x in graph.effective(uid) and satisfies(graph, x, need)
        )
        if holding:
            via = [
                g.view()
                for g in graph.grants(uid, include_inactive=False)
                if g.entitlement_id in holding
            ]
            signals.append(Signal("EXISTING_ACCESS_SUFFICIENT", f"already holds {', '.join(holding)}, which meets the stated need",
                                  data={"held": holding, "inherited": any(v["inherited"] for v in via), "paths": [v["path"] for v in via]}))  # fmt: skip
        else:
            fits = sorted(
                (x for x in graph.family(eid) if satisfies(graph, x, need)),
                key=lambda x: (_breadth(graph, x), x),
            )
            best = fits[0] if fits else None
            if PRIVILEGE_RANK[ent["privilege"]] > PRIVILEGE_RANK[need["privilege"]]:
                signals.append(Signal("WRITE_NOT_REQUIRED" if need["privilege"] == "read" else "PRIVILEGE_EXCESSIVE",
                                      f"{ent['privilege']} requested; the purpose needs {need['privilege']}"))  # fmt: skip
            if (
                ent["scope"] == "full"
                and not need["full_scope"]
                and best
                and graph.entitlement(best)["scope"] == "sanitized"
            ):
                signals.append(
                    Signal(
                        "REQUESTED_SCOPE_EXCESSIVE",
                        "full records requested; a sanitized dataset meets the need",
                    )
                )
            if best and best != eid and _breadth(graph, best) < _breadth(graph, eid):
                lower = best
                signals.append(Signal("LOWER_PRIVILEGE_ALTERNATIVE_AVAILABLE", f"{best} meets the need with less privilege",
                                      data={"entitlement_id": best}))  # fmt: skip
    # The least-privilege alternative: the narrower entitlement (if any), for the allowed duration.
    too_long = any(s.code == "DURATION_EXCESSIVE" for s in signals)
    alternative = None
    if lower or too_long:
        alternative = {"entitlement_id": lower or eid,
                       "duration_hours": min(requested_hours, allowed) if allowed is not None else requested_hours}  # fmt: skip

    sod = sod_conflicts(graph, uid, eid, cfg)
    for c in sod:
        signals.append(
            Signal(
                "SOD_CONFLICT",
                f"{c['rule_id']}: would hold {c['held']} and {c['requested']} ({c['why']})",
                data=c,
            )
        )

    for c in sod:
        exc = approved_exception(uid, c["rule_id"], graph.as_of)
        if exc:
            signals.append(Signal("EXCEPTION_APPROVED", f"{exc['exception_id']} ({exc['approved_by']}, to {exc['valid_to']}) covers {c['rule_id']}",
                                  data=exc))  # fmt: skip

    for s in stale_grants(graph, uid, cfg):
        signals.append(
            Signal(
                "STALE_EXISTING_ENTITLEMENT",
                f"{s['entitlement_id']} ({s['grant_id']}) unused for {s['idle_days']} days",
                data=s,
            )
        )

    pr = graph.peer_rate(uid, eid)
    if pr["rate"] is not None and pr["rate"] < cfg["unusual_for_role_below"]:
        signals.append(
            Signal(
                "UNUSUAL_FOR_ROLE",
                f"{pr['holders']} of {pr['peers']} {pr['role_id']} peers hold it",
                contextual=True,
                data=pr,
            )
        )

    return GovernanceResult(req["request_id"], cfg["governance_version"], signals, sod, alternative,
                            allowed, dict(need) if need else None)  # fmt: skip
