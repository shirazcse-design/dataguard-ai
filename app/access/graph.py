"""The UC3 access graph: a small typed, in-memory graph, deterministic and authoritative for how a
user holds access. No graph database; no model ever adds or changes an edge.

Nodes: USER, ROLE, GROUP, PROJECT, ENTITLEMENT, RESOURCE.
Edges: MEMBER_OF (user -> role | group), ASSIGNED_TO (user -> project), GRANTS (role | group |
project -> entitlement), DIRECT_GRANT (user -> entitlement), PERMITS (entitlement -> resource),
PEER_OF (user -> role: the users who share a role are peers).

A grant is ACTIVE when its source still applies on `as_of`: a project grant ends with the project,
a direct grant at its expiry. Expired grants stay in the graph (they are evidence of stale access)
but never count as effective access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import synth

PRIVILEGE_RANK = {"read": 1, "write": 2, "admin": 3}


@dataclass(frozen=True)
class Node:
    id: str
    kind: str  # USER | ROLE | GROUP | PROJECT | ENTITLEMENT | RESOURCE
    attrs: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)


@dataclass(frozen=True)
class Edge:
    src: str
    rel: str
    dst: str
    attrs: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)


@dataclass
class Grant:
    """How a user holds one entitlement: the path from the user to the resource."""

    entitlement_id: str
    resource_id: str
    via_kind: str  # direct | role | group | project
    via_id: str  # the role, group or project id; for a direct grant, the grant id
    path: list[str]  # alternating node ids and relation names, user first, resource last
    active: bool
    expires: str | None

    @property
    def inherited(self) -> bool:
        return self.via_kind != "direct"

    def view(self) -> dict[str, Any]:
        return {"entitlement_id": self.entitlement_id, "resource_id": self.resource_id,
                "via": self.via_kind, "via_id": self.via_id, "inherited": self.inherited,
                "active": self.active, "expires": self.expires, "path": self.path}  # fmt: skip


class AccessGraph:
    def __init__(self, base: Path = synth.DATA) -> None:
        self.as_of = synth.load("meta.json", base)["as_of"]
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self._out: dict[str, list[Edge]] = {}
        for r in synth.load("resources.json", base):
            self._node(r["resource_id"], "RESOURCE", r)
        for e in synth.load("entitlements.json", base):
            self._node(e["entitlement_id"], "ENTITLEMENT", e)
            self._edge(e["entitlement_id"], "PERMITS", e["resource"])
        for r in synth.load("roles.json", base):
            self._node(r["role_id"], "ROLE", r)
            for g in r["grants"]:
                self._edge(r["role_id"], "GRANTS", g)
        for g in synth.load("groups.json", base):
            self._node(g["group_id"], "GROUP", g)
            for x in g["grants"]:
                self._edge(g["group_id"], "GRANTS", x)
        for p in synth.load("projects.json", base):
            self._node(p["project_id"], "PROJECT", p)
            for x in p["grants"]:
                self._edge(p["project_id"], "GRANTS", x)
        for u in synth.load("users.json", base):
            self._node(u["user_id"], "USER", u)
            self._edge(u["user_id"], "MEMBER_OF", u["role_id"], {"kind": "role"})
            self._edge(u["user_id"], "PEER_OF", u["role_id"])
            for g in u["groups"]:
                self._edge(u["user_id"], "MEMBER_OF", g, {"kind": "group"})
            for p in u["projects"]:
                self._edge(u["user_id"], "ASSIGNED_TO", p)
        for d in synth.load("direct_grants.json", base):
            self._edge(d["user_id"], "DIRECT_GRANT", d["entitlement_id"], d)
        self.usage = {
            (u["user_id"], u["entitlement_id"]): u for u in synth.load("usage.json", base)
        }
        self._check()

    # -- construction ----------------------------------------------------------------------------
    def _node(self, nid: str, kind: str, attrs: dict[str, Any]) -> None:
        if nid in self.nodes:
            raise ValueError(f"duplicate node {nid}")
        self.nodes[nid] = Node(nid, kind, attrs)

    def _edge(self, src: str, rel: str, dst: str, attrs: dict[str, Any] | None = None) -> None:
        e = Edge(src, rel, dst, attrs or {})
        self.edges.append(e)
        self._out.setdefault(src, []).append(e)

    def _check(self) -> None:
        """Every edge joins two known nodes of the right kinds (a typo is a build error)."""
        kinds = {"MEMBER_OF": ("USER", {"ROLE", "GROUP"}), "ASSIGNED_TO": ("USER", {"PROJECT"}),
                 "GRANTS": (None, {"ENTITLEMENT"}), "DIRECT_GRANT": ("USER", {"ENTITLEMENT"}),
                 "PERMITS": ("ENTITLEMENT", {"RESOURCE"}), "PEER_OF": ("USER", {"ROLE"})}  # fmt: skip
        for e in self.edges:
            src, dst = self.nodes.get(e.src), self.nodes.get(e.dst)
            want_src, want_dst = kinds[e.rel]
            if (
                src is None
                or dst is None
                or dst.kind not in want_dst
                or (want_src and src.kind != want_src)
            ):
                raise ValueError(f"bad edge {e.src} -{e.rel}-> {e.dst}")

    # -- lookups ---------------------------------------------------------------------------------
    def kind(self, nid: str) -> str | None:
        n = self.nodes.get(nid)
        return n.kind if n else None

    def user(self, uid: str) -> dict[str, Any]:
        n = self.nodes.get(uid)
        if n is None or n.kind != "USER":
            raise KeyError(uid)
        return n.attrs

    def entitlement(self, eid: str) -> dict[str, Any]:
        n = self.nodes.get(eid)
        if n is None or n.kind != "ENTITLEMENT":
            raise KeyError(eid)
        return n.attrs

    def resource(self, rid: str) -> dict[str, Any]:
        n = self.nodes.get(rid)
        if n is None or n.kind != "RESOURCE":
            raise KeyError(rid)
        return n.attrs

    def resource_of(self, eid: str) -> str:
        return next(e.dst for e in self._out.get(eid, []) if e.rel == "PERMITS")

    def family(self, eid: str) -> list[str]:
        fam = self.entitlement(eid)["family"]
        return sorted(
            n.id
            for n in self.nodes.values()
            if n.kind == "ENTITLEMENT" and n.attrs["family"] == fam
        )

    # -- queries ---------------------------------------------------------------------------------
    def grants(self, uid: str, include_inactive: bool = True) -> list[Grant]:
        """Every way the user holds any entitlement, with its full path."""
        self.user(uid)
        out: list[Grant] = []
        for e in self._out.get(uid, []):
            if e.rel == "DIRECT_GRANT":
                exp = e.attrs.get("expires")
                out.append(self._grant(uid, e.dst, "direct", e.attrs["grant_id"], [uid, "DIRECT_GRANT"],
                                       exp is None or exp >= self.as_of, exp))  # fmt: skip
            elif e.rel in ("MEMBER_OF", "ASSIGNED_TO"):
                via = self.nodes[e.dst]
                active, exp = True, None
                if via.kind == "PROJECT":
                    exp = via.attrs["ends"]
                    active = exp >= self.as_of
                for g in self._out.get(via.id, []):
                    if g.rel == "GRANTS":
                        out.append(self._grant(uid, g.dst, via.kind.lower(), via.id,
                                               [uid, e.rel, via.id, "GRANTS"], active, exp))  # fmt: skip
        out.sort(key=lambda g: (g.entitlement_id, g.via_kind, g.via_id))
        return out if include_inactive else [g for g in out if g.active]

    def _grant(self, uid, eid, via_kind, via_id, prefix, active, exp) -> Grant:
        rid = self.resource_of(eid)
        return Grant(eid, rid, via_kind, via_id, [*prefix, eid, "PERMITS", rid], active, exp)

    def effective(self, uid: str) -> set[str]:
        return {g.entitlement_id for g in self.grants(uid, include_inactive=False)}

    def paths(self, uid: str, resource_id: str) -> list[Grant]:
        return [g for g in self.grants(uid) if g.resource_id == resource_id]

    def peers(self, uid: str) -> list[str]:
        role = self.user(uid)["role_id"]
        return sorted(
            n.id
            for n in self.nodes.values()
            if n.kind == "USER" and n.attrs["role_id"] == role and n.id != uid
        )

    def peer_rate(self, uid: str, eid: str) -> dict[str, Any]:
        peers = self.peers(uid)
        holders = [p for p in peers if eid in self.effective(p)]
        return {"role_id": self.user(uid)["role_id"], "peers": len(peers), "holders": len(holders),
                "rate": round(len(holders) / len(peers), 3) if peers else None}  # fmt: skip

    def delta(self, uid: str, eid: str) -> dict[str, Any]:
        """What granting `eid` would ADD to the user's effective access."""
        ent = self.entitlement(eid)
        have = self.effective(uid)
        same_resource = sorted(x for x in have if self.resource_of(x) == ent["resource"])
        best = max(
            (PRIVILEGE_RANK[self.entitlement(x)["privilege"]] for x in same_resource), default=0
        )
        return {"entitlement_id": eid, "resource_id": ent["resource"], "already_held": eid in have,
                "held_on_resource": same_resource,
                "adds_privilege": PRIVILEGE_RANK[ent["privilege"]] > best,
                "privilege_from": next((k for k, v in PRIVILEGE_RANK.items() if v == best), None),
                "privilege_to": ent["privilege"], "scope": ent["scope"], "privileged": ent["privileged"]}  # fmt: skip

    def last_use(self, uid: str, eid: str) -> dict[str, Any] | None:
        return self.usage.get((uid, eid))

    def counts(self) -> dict[str, Any]:
        nodes: dict[str, int] = {}
        for n in self.nodes.values():
            nodes[n.kind] = nodes.get(n.kind, 0) + 1
        rels: dict[str, int] = {}
        for e in self.edges:
            rels[e.rel] = rels.get(e.rel, 0) + 1
        return {"nodes": dict(sorted(nodes.items())), "edges": dict(sorted(rels.items())),
                "total_nodes": len(self.nodes), "total_edges": len(self.edges)}  # fmt: skip
