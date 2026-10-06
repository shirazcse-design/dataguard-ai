"""UC3 authoritative services and the per-request FACTS the harness decides on.

`compute_facts()` runs in the precheck, BEFORE and INDEPENDENTLY of the agent: identity, the access
graph, UC4 sensitivity (reused, recorded), the POL-ACC sections UC6 returns, and the least-privilege /
SoD findings. The harness reads only these facts, so an agent that skips or misuses a tool cannot
weaken a floor. The agent's tools are read-only views over the same services.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any

from . import synth
from .governance import GovernanceResult, evaluate
from .graph import AccessGraph
from .requirements import applicable

CALLER = "access-governance-agent"
SALT_ENV = "DATAGUARD_TELEMETRY_SALT"


def pseudonym(user_id: str) -> str:
    salt = os.environ.get(SALT_ENV, "dataguard-uc3-demo")
    return "subj-" + hashlib.sha256(f"{salt}|{user_id}".encode()).hexdigest()[:12]


class ServiceError(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass
class AccessServices:
    graph: AccessGraph
    uc4: Any  # app.dlp.integration.Uc4Classifier over the shared UC4 adapter (caller access-governance-agent)
    documents: Any  # app.dlp.integration.DocumentStore
    copilot: Any  # UC6 PolicyCopilot
    scanner: Any  # UC6 untrusted-text scanner
    _cls: dict[str, Any] = field(default_factory=dict)

    def classify(self, resource_id: str, *, unavailable: bool = False) -> dict[str, Any]:
        """UC4 is authoritative. Only the level, categories and confidence leave UC4."""
        if unavailable:
            raise ServiceError("uc4_unavailable")
        if resource_id not in self._cls:
            r = self.graph.resource(resource_id)
            c = self.uc4.classify(
                self.documents.get(r["uc4_sample"]),
                f"uc3-{resource_id}",
                semantic_unavailable=False,
            )
            self._cls[resource_id] = {"resource_id": resource_id, "level": c.level, "categories": list(c.categories),
                                      "confidence": c.confidence, "status": c.status, "decided_by": c.decided_by}  # fmt: skip
        return self._cls[resource_id]

    def policy_tools(self, run_id: str) -> Any:
        from app.policy.agent import PolicyTools
        from app.policy.pipeline import Run

        cp = self.copilot
        return PolicyTools(
            cp,
            cp.cfg.levels["agentic"],
            Run(run_id, "agentic", cp.retriever.embedding_model_id),
            cp.cfg.agent,
        )

    def scan(self, text: str) -> bool:
        """True = instruction-like text (withheld from the agent)."""
        return bool(text) and bool(self.scanner.scan(text))


@dataclass
class Facts:
    """Everything the harness decides on. Built once per request, never by a model."""

    request: dict[str, Any]
    failures: list[str]
    identity: dict[str, Any] | None
    sensitivity: dict[str, Any] | None
    requirements: dict[str, Any] | None
    policy: list[dict[str, Any]]  # verified POL-ACC sections from UC6: req_id, citation, text
    policy_missing: list[str]  # requirement ids with no retrievable section, or an unmapped class
    governance: GovernanceResult | None
    justification_flagged: bool
    paths: list[dict[str, Any]]
    delta: dict[str, Any] | None

    def view(self) -> dict[str, Any]:
        return {"failures": self.failures, "identity": self.identity, "sensitivity": self.sensitivity,
                "requirements": self.requirements, "policy": self.policy, "policy_missing": self.policy_missing,
                "governance": self.governance.view() if self.governance else None,
                "justification_flagged": self.justification_flagged, "paths": self.paths, "delta": self.delta}  # fmt: skip


def identity_view(graph: AccessGraph, uid: str) -> dict[str, Any]:
    u = graph.user(uid)
    role = graph.nodes[u["role_id"]].attrs
    return {"subject": pseudonym(uid), "role_id": u["role_id"], "department": role["department"],
            "groups": u["groups"], "projects": u["projects"], "role_since": u["role_since"],
            "moved_role": u.get("previous_role_id") is not None}  # fmt: skip


def compute_facts(svc: AccessServices, req: dict[str, Any]) -> Facts:
    g, fault = svc.graph, req.get("simulate_fault")
    failures: list[str] = []
    eid = req["entitlement_id"]
    identity = None
    if fault == "identity_unavailable":
        failures.append("identity")
    else:
        identity = identity_view(g, req["user_id"])
    rid = g.resource_of(eid)
    sensitivity = None
    try:
        sensitivity = svc.classify(rid, unavailable=fault == "uc4_unavailable")
    except ServiceError:
        failures.append("uc4")
    level = sensitivity["level"] if sensitivity else None
    reqs = applicable(g, eid, level)
    policy, missing = [], []
    if not reqs["mapped"]:
        missing.append(f"resource_class:{reqs['resource_class']}")
    if fault == "policy_unavailable":
        failures.append("policy")
    else:
        tools = svc.policy_tools(f"uc3-{req['request_id']}-facts")
        for r in reqs["requirements"]:
            ok, res, _ = tools.call(
                "get_policy_section", {"policy_id": reqs["policy_id"], "section": r["section"]}
            )
            hit = res.get("results", [{}])[0] if ok else {}
            if ok and "text" in hit:
                policy.append(
                    {
                        "req_id": r["req_id"],
                        "citation": hit["citation"],
                        "section": hit["section"],
                        "text": hit["text"],
                    }
                )
            else:
                missing.append(r["req_id"])
    gov = None
    if identity is not None:  # without identity the graph facts about the user cannot be trusted
        gov = evaluate(g, req, policy_max_hours=reqs["max_hours"], sensitivity=level)
    return Facts(request=req, failures=failures, identity=identity, sensitivity=sensitivity, requirements=reqs,
                 policy=policy, policy_missing=missing, governance=gov,
                 justification_flagged=svc.scan(req.get("justification") or ""),
                 paths=[p.view() for p in g.paths(req["user_id"], rid)] if identity else [],
                 delta=g.delta(req["user_id"], eid) if identity else None)  # fmt: skip


def build_services(mode: str = "replay") -> AccessServices:
    from app.classification.service import ClassificationService
    from app.dlp.integration import DocumentStore, Uc4Classifier
    from app.policy.guard import load_policy_scanner
    from app.policy.service import build_copilot
    from mcp_adapter.adapter import ClassifyDocumentAdapter
    from mcp_adapter.config import load_mcp_config

    uc4_mode = {"offline": "replay", "replay": "replay", "live": "foundry", "record": "record"}[
        mode
    ]
    uc6_mode = {"offline": "offline", "replay": "replay", "live": "live", "record": "record"}[mode]
    mcp_cfg, _ = load_mcp_config()
    adapter = ClassifyDocumentAdapter(
        ClassificationService(llm_mode=uc4_mode), mcp_cfg, caller_id=CALLER
    )
    copilot = build_copilot(uc6_mode)
    return AccessServices(graph=AccessGraph(synth.DATA), uc4=Uc4Classifier(adapter), documents=DocumentStore(),
                          copilot=copilot, scanner=load_policy_scanner(copilot.cfg.guard))  # fmt: skip
