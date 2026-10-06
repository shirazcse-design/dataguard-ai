"""The UC3 agent's tools: READ-ONLY views over the authoritative services, bound to ONE request.

* Bound: the subject is the request's user (a pseudonym to the agent); any other subject is refused
  (`out_of_scope_subject`). Resources and entitlements are limited to the requested one and its
  family (the alternatives worth checking); anything else is refused (`out_of_scope`).
* Typed: each tool validates its arguments (`invalid_arguments`).
* Traceable: every result carries the evidence ids the agent may cite; ids are assigned here.
* No tool writes: there is no grant, revoke, group or role change anywhere in UC3. The only side
  effect is `request_human_review`, which records a reason for the analyst.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agent.bounded import Tool, params

from .governance import evaluate, load_config, sod_conflicts
from .requirements import applicable
from .services import AccessServices, Facts, ServiceError, identity_view, pseudonym

REVIEW_REASONS = ("sensitive_data", "sod_conflict", "privileged_access", "policy_unclear", "conflicting_evidence",
                  "insufficient_evidence", "suspicious_request")  # fmt: skip


@dataclass
class CaseState:
    svc: AccessServices
    facts: Facts
    returned: set[str] = field(default_factory=set)  # every evidence id a tool has returned
    review_requests: list[str] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    policy: Any = None  # a UC6 PolicyTools bound to this request (agent's own retrieval)

    @property
    def req(self) -> dict[str, Any]:
        return self.facts.request

    @property
    def subject(self) -> str:
        return pseudonym(self.req["user_id"])

    def scope_entitlements(self) -> set[str]:
        return set(self.svc.graph.family(self.req["entitlement_id"]))

    def scope_resources(self) -> set[str]:
        return {self.svc.graph.resource_of(e) for e in self.scope_entitlements()}


def _fail(state: CaseState, kind: str, detail: str = "") -> tuple[bool, dict[str, Any], str]:
    if kind.startswith("out_of_scope"):
        state.refusals.append(kind)
    return False, {"error": kind, **({"detail": detail} if detail else {})}, kind


def _ok(
    state: CaseState, result: dict[str, Any], ids: list[str]
) -> tuple[bool, dict[str, Any], None]:
    state.returned.update(ids)
    return True, {**result, "evidence_ids": ids}, None


def build_tools(state: CaseState) -> list[Tool]:  # noqa: C901 - one closure per tool keeps each bound
    # Read lazily: `tool_schemas()` builds the tools without a request (svc and facts are None).
    svc = state.svc
    g = svc.graph if svc is not None else None
    identity_down = state.facts is not None and "identity" in state.facts.failures

    def subject_ok(args: dict[str, Any]) -> bool:
        s = args.get("subject")
        return s in (None, state.subject)

    def need_identity() -> tuple[bool, dict[str, Any], str] | None:
        return (
            _fail(state, "tool_unavailable", "identity service unavailable")
            if identity_down
            else None
        )

    def ent_arg(args: dict[str, Any]) -> str | None:
        e = args.get("entitlement_id") or state.req["entitlement_id"]
        return e if isinstance(e, str) and e in state.scope_entitlements() else None

    def res_arg(args: dict[str, Any]) -> str | None:
        r = args.get("resource_id")
        return r if isinstance(r, str) and r in state.scope_resources() else None

    def get_user_profile(args):
        if not subject_ok(args):
            return _fail(
                state, "out_of_scope_subject", "only the requester's own context is available"
            )
        if (e := need_identity()) is not None:
            return e
        return _ok(
            state,
            {"profile": identity_view(g, state.req["user_id"]), "claim_type": "OBSERVED_FACT"},
            ["IDN"],
        )

    def get_current_entitlements(args):
        if not subject_ok(args):
            return _fail(
                state, "out_of_scope_subject", "only the requester's own context is available"
            )
        if (e := need_identity()) is not None:
            return e
        grants = g.grants(state.req["user_id"])
        return _ok(state, {"effective": sorted({x.entitlement_id for x in grants if x.active}),
                           "grants": [{"entitlement_id": x.entitlement_id, "via": x.via_kind, "via_id": x.via_id,
                                       "active": x.active, "expires": x.expires} for x in grants],
                           "claim_type": "OBSERVED_FACT"}, ["ENT"])  # fmt: skip

    def get_access_path(args):
        rid = res_arg(args)
        if rid is None:
            return _fail(
                state,
                "out_of_scope",
                "resource_id must be the requested resource or an alternative's",
            )
        if (e := need_identity()) is not None:
            return e
        paths = g.paths(state.req["user_id"], rid)
        ids = [f"PATH-{rid}-{i + 1}" for i in range(len(paths))]
        return _ok(state, {"resource_id": rid, "paths": [{"evidence_id": i, **p.view()} for i, p in zip(ids, paths, strict=True)],
                           "note": "no current path" if not paths else None, "claim_type": "OBSERVED_FACT"}, ids or [f"PATH-{rid}-NONE"])  # fmt: skip

    def get_resource_metadata(args):
        rid = res_arg(args)
        if rid is None:
            return _fail(
                state,
                "out_of_scope",
                "resource_id must be the requested resource or an alternative's",
            )
        r = g.resource(rid)
        return _ok(state, {"resource_id": rid, "name": r["name"], "environment": r["environment"],
                           "resource_class": r["resource_class"], "owner": r["owner"], "claim_type": "OBSERVED_FACT"}, [f"RES-{rid}"])  # fmt: skip

    def get_usage_history(args):
        eid = ent_arg(args)
        if eid is None:
            return _fail(
                state,
                "out_of_scope",
                "entitlement_id must be the requested entitlement or an alternative",
            )
        if (e := need_identity()) is not None:
            return e
        u = g.last_use(state.req["user_id"], eid)
        return _ok(state, {"entitlement_id": eid, "last_used": u["last_used"] if u else None, "uses_90d": u["uses_90d"] if u else 0,
                           "as_of": g.as_of, "claim_type": "OBSERVED_FACT"}, [f"USE-{eid}"])  # fmt: skip

    def get_peer_access_summary(args):
        eid = ent_arg(args)
        if eid is None:
            return _fail(
                state,
                "out_of_scope",
                "entitlement_id must be the requested entitlement or an alternative",
            )
        if (e := need_identity()) is not None:
            return e
        pr = g.peer_rate(state.req["user_id"], eid)
        return _ok(state, {"entitlement_id": eid, **pr, "claim_type": "OBSERVED_FACT",
                           "note": "context only: peer rarity is not, by itself, a reason to refuse access"}, [f"PEER-{eid}"])  # fmt: skip

    def classify_resource(args):
        rid = res_arg(args)
        if rid is None:
            return _fail(
                state,
                "out_of_scope",
                "resource_id must be the requested resource or an alternative's",
            )
        try:
            c = svc.classify(rid, unavailable=state.req.get("simulate_fault") == "uc4_unavailable")
        except ServiceError as err:
            return _fail(state, "tool_unavailable", err.kind)
        return _ok(
            state,
            {**c, "source": "UC4", "claim_type": "DETERMINISTIC_CONTROL_RESULT"},
            [f"CLS-{rid}"],
        )

    def search_policy(args):
        if state.req.get("simulate_fault") == "policy_unavailable":
            return _fail(state, "tool_unavailable", "policy service unavailable")
        ok, res, err = state.policy.call("search_policy", {"query": args.get("query", "")})
        if not ok:
            return False, res, err
        return _ok(state, {"results": res["results"], "source": "UC6", "claim_type": "POLICY_REQUIREMENT"},
                   [r["evidence_id"] for r in res["results"]])  # fmt: skip

    def get_policy_section(args):
        if state.req.get("simulate_fault") == "policy_unavailable":
            return _fail(state, "tool_unavailable", "policy service unavailable")
        ok, res, err = state.policy.call(
            "get_policy_section",
            {"policy_id": args.get("policy_id", ""), "section": args.get("section", "")},
        )
        if not ok:
            return False, res, err
        return _ok(state, {"results": res["results"], "source": "UC6", "claim_type": "POLICY_REQUIREMENT"},
                   [r["evidence_id"] for r in res["results"]])  # fmt: skip

    def check_sod(args):
        eid = ent_arg(args)
        if eid is None:
            return _fail(
                state,
                "out_of_scope",
                "entitlement_id must be the requested entitlement or an alternative",
            )
        if (e := need_identity()) is not None:
            return e
        conflicts = sod_conflicts(g, state.req["user_id"], eid, load_config())
        ids = [f"SOD-{c['rule_id']}" for c in conflicts]
        return _ok(state, {"entitlement_id": eid, "conflicts": [{"evidence_id": i, **c} for i, c in zip(ids, conflicts, strict=True)],
                           "claim_type": "DETERMINISTIC_CONTROL_RESULT"}, ids or [f"SOD-NONE-{eid}"])  # fmt: skip

    def evaluate_least_privilege(args):
        eid = ent_arg(args)
        if eid is None:
            return _fail(
                state,
                "out_of_scope",
                "entitlement_id must be the requested entitlement or an alternative",
            )
        if (e := need_identity()) is not None:
            return e
        f = state.facts
        req = {**state.req, "entitlement_id": eid}
        level = (
            (svc.classify(g.resource_of(eid)) or {}).get("level")
            if eid != state.req["entitlement_id"]
            else (f.sensitivity or {}).get("level")
        )
        res = evaluate(
            g, req, policy_max_hours=applicable(g, eid, level)["max_hours"], sensitivity=level
        )
        return _ok(
            state, {**res.view(), "claim_type": "DETERMINISTIC_CONTROL_RESULT"}, [f"LP-{eid}"]
        )

    def request_human_review(args):
        reason = args.get("reason")
        if reason not in REVIEW_REASONS:
            return _fail(
                state, "invalid_arguments", f"reason must be one of {list(REVIEW_REASONS)}"
            )
        state.review_requests.append(reason)
        return _ok(state, {"status": "review_requested", "reason": reason}, [])

    subj = {
        "subject": {
            "type": "string",
            "description": "optional; must be the request's subject pseudonym",
        }
    }
    rid_p = {
        "resource_id": {
            "type": "string",
            "description": "the requested resource, or a resource of an alternative entitlement",
        }
    }
    eid_p = {
        "entitlement_id": {
            "type": "string",
            "description": "the requested entitlement (default) or one in the same family",
        }
    }
    return [
        Tool("get_user_profile", "The requester's access context: role, department, groups, projects, whether they moved role. No HR data.", params(subj), get_user_profile),
        Tool("get_current_entitlements", "The requester's effective entitlements and how each is held (role, group, project, direct; active or expired).", params(subj), get_current_entitlements),
        Tool("get_access_path", "Deterministic graph paths from the requester to a resource (direct or inherited).", params(rid_p, ["resource_id"]), get_access_path),
        Tool("get_resource_metadata", "A resource's name, environment, class and owner.", params(rid_p, ["resource_id"]), get_resource_metadata),
        Tool("get_usage_history", "The requester's last use and 90-day use count of an entitlement.", params(eid_p), get_usage_history),
        Tool("get_peer_access_summary", "How many of the requester's role peers hold an entitlement. Context only.", params(eid_p), get_peer_access_summary),
        Tool("classify_resource", "UC4's authoritative sensitivity level and categories for a resource's data.", params(rid_p, ["resource_id"]), classify_resource),
        Tool("search_policy", "Search the access-control policy corpus (UC6). Returns cited sections with evidence ids.",
             params({"query": {"type": "string", "minLength": 3, "maxLength": 300}}, ["query"]), search_policy),
        Tool("get_policy_section", "Fetch one policy section by policy id and section number (UC6), e.g. POL-ACC 3.4.",
             params({"policy_id": {"type": "string"}, "section": {"type": "string"}}, ["policy_id", "section"]), get_policy_section),
        Tool("check_sod", "Deterministic segregation-of-duties check for an entitlement against what the requester holds.", params(eid_p), check_sod),
        Tool("evaluate_least_privilege", "Deterministic least-privilege findings and the narrowest alternative for an entitlement.", params(eid_p), evaluate_least_privilege),
        Tool("request_human_review", "Ask for an analyst. The only tool with an effect; it records a reason.",
             params({"reason": {"type": "string", "enum": list(REVIEW_REASONS)}}, ["reason"]), request_human_review),
    ]  # fmt: skip
