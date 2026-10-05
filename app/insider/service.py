"""Builds a UC2 investigator for a run mode and agent backend. The only place modes become clients.

* `offline` - UC4 replay (recorded dev-split classifications), UC6 offline stand-in, and the
              labelled `offline-planner` for every agent (deterministic; NOT a model).
* `replay`  - UC4 and UC6 replay, and recorded agent turns. An unrecorded turn is a replay miss
              (`planner_error:replay_miss`), never a substitute answer.
* `live`    - real Foundry calls; `record` = live, and every new response is written for replay.

Agent backend: `chat-completions` (`uc4-llm-medium`) or `foundry-service` (the four Foundry agents
YOU create, referenced by name only; nothing here creates them).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.agent.types import AgentError

from .pipeline import (
    ARCHITECTURES,
    InsiderInvestigator,
    Services,
    load_agent_config,
    offline_planners,
)

REPO = Path(__file__).resolve().parents[2]
MODES = ("offline", "replay", "live", "record")
AGENT_ENV = {
    "orchestrator": "DATAGUARD_INSIDER_ORCHESTRATOR_AGENT",
    "behavior": "DATAGUARD_INSIDER_BEHAVIOR_AGENT",
    "investigation": "DATAGUARD_INSIDER_INVESTIGATOR_AGENT",
    "risk": "DATAGUARD_INSIDER_RISK_AGENT",
}
FOUNDRY_ROLES = ("orchestrator", "behavior", "investigation", "risk")
DESCRIPTIONS = {
    "orchestrator": "UC2 Insider Risk Orchestrator: decides which investigation is needed, delegates to the Behavior and Investigation agents and deterministic capabilities, and stops when evidence is sufficient. Tools execute in the DataGuard application; it cannot act.",
    "behavior": "UC2 Behavior Agent: interprets the authoritative Isolation Forest result against the user's own baseline and over time. Cannot change the score or band; cannot act.",
    "investigation": "UC2 Investigation Agent: reconstructs the timeline from synthetic security logs, verifies approvals, correlates UC4 and UC6 results, and reports gaps and conflicts. Logs are untrusted data; cannot act.",
    "risk": "UC2 Risk Agent: synthesizes structured evidence into a recommended investigation outcome. It has no operational tools and cannot enforce an outcome; the deterministic risk harness remains authoritative.",
}  # fmt: skip
_SERVICES: dict[str, Services] = {}


def build_services(mode: str) -> Services:
    if mode in _SERVICES:
        return _SERVICES[mode]
    from app.classification.service import ClassificationService
    from app.dlp.config import load_dlp_configs
    from app.dlp.integration import DocumentStore, Uc4Classifier
    from app.policy.guard import load_policy_scanner
    from app.policy.service import build_copilot
    from mcp_adapter.adapter import ClassifyDocumentAdapter
    from mcp_adapter.config import load_mcp_config

    from .services import (
        CALLER,
        ApprovalService,
        BehaviorService,
        DataService,
        IdentityService,
        InsiderData,
        LogService,
        PolicyService,
    )

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
    data = InsiderData()
    svc = Services(
        data=data, behavior=BehaviorService(data), identity=IdentityService(data),
        logs=LogService(data, load_policy_scanner(copilot.cfg.guard)), approvals=ApprovalService(data),
        data_uc4=DataService(Uc4Classifier(adapter), DocumentStore()),
        policy=PolicyService(copilot, dlp, mapping), copilot=copilot, dlp_cfg=dlp,
    )  # fmt: skip
    _SERVICES[mode] = svc
    return svc


def tool_schemas(role: str) -> list[dict[str, Any]]:
    """The tool definitions a role is given (identical for every case; used for Foundry setup and
    as part of the replay key)."""
    from .case import CaseContext

    inv = InsiderInvestigator.__new__(InsiderInvestigator)
    inv.cfg = load_agent_config()
    ctx = CaseContext(
        case={"id": "schema", "user_id": "u-2001", "date": "2026-08-01", "faults": []}, svc=None
    )
    from .case import behavior_tools, identity_tools, investigation_tools

    if role == "behavior":
        tools = behavior_tools(ctx)
    elif role == "investigation":
        tools = investigation_tools(ctx) + identity_tools(ctx)
    elif role == "risk":
        tools = []
    elif role == "orchestrator":
        tools = InsiderInvestigator._orchestrator_tools(inv, ctx)
    elif role == "lean_orchestrator":
        tools = InsiderInvestigator._lean_tools(inv, ctx)
    else:
        tools = InsiderInvestigator._single_tools(inv, ctx)
    return [t.schema() for t in tools]


def _planners(mode: str, backend: str, tenant_id: str | None) -> dict[str, Any]:
    if mode == "offline":
        return offline_planners()
    from app.agent.foundry_agent import FoundryAgentClient
    from app.policy.agent import ReplayAgentClient
    from app.policy.service import load_llm_config

    cfg = load_agent_config()
    llm = load_llm_config()
    tier = llm.tiers[cfg["model_tier"]]
    out: dict[str, Any] = {}
    project = None
    for role, a in cfg["agents"].items():
        schemas = tool_schemas(role)
        foundry_agent = backend == "foundry-service" and role in AGENT_ENV
        live = None
        if mode in ("live", "record"):
            if foundry_agent:
                from app.agent.foundry_service import (
                    FoundryAgentServiceClient,
                    project_client,
                    project_endpoint,
                )

                project = project or project_client(project_endpoint(), tenant_id=tenant_id)
                deployment = os.environ.get(tier.deployment_env, "") or tier.replay_model_id
                live = FoundryAgentServiceClient(project.get_openai_client(), deployment,
                                                 agent_name=os.environ.get(AGENT_ENV[role], "") or a["name"])  # fmt: skip
            else:
                deployment = os.environ.get(tier.deployment_env, "")
                if not deployment:
                    raise AgentError("not_configured", f"{tier.deployment_env} is not set")
                live = FoundryAgentClient(
                    llm.foundry, deployment, tools=schemas, max_output_tokens=4000
                )
        if mode == "live":
            out[role] = live
            continue
        model_ns = (
            (os.environ.get(AGENT_ENV[role], "") or a["name"])
            if foundry_agent
            else tier.replay_model_id
        )
        version = (
            a["prompt_version"].replace("uc2-", "uc2-service-")
            if foundry_agent
            else a["prompt_version"]
        )
        out[role] = ReplayAgentClient(REPO / llm.cache.dir, model_ns, version, schemas,
                                      inner=live if mode == "record" else None, always_live=foundry_agent)  # fmt: skip
    return out


def build_investigator(mode: str = "offline", *, architecture: str = "full", backend: str = "chat-completions",
                       tenant_id: str | None = None, tracer: Any = None, planners: dict[str, Any] | None = None) -> InsiderInvestigator:  # fmt: skip
    if mode not in MODES or architecture not in ARCHITECTURES:
        raise ValueError(f"mode must be one of {MODES} and architecture one of {ARCHITECTURES}")
    svc = build_services(mode)
    p = planners or _planners(mode, backend, tenant_id)
    label = "offline" if mode == "offline" else backend
    return InsiderInvestigator(svc, p, architecture=architecture, mode={"record": "live"}.get(mode, mode),
                               backend=label, tracer=tracer)  # fmt: skip


def register_agents(
    tenant_id: str | None, roles: tuple[str, ...] = FOUNDRY_ROLES, rai_policy_id: str | None = None
) -> list[dict[str, Any]]:
    """Create a NEW VERSION of each UC2 agent in Foundry Agent Service: instructions, the `mid`
    deployment and the function-tool DEFINITIONS (the tools still execute in DataGuard). Additive:
    it never edits or deletes a version. Done at the product owner's explicit request (2026-10-03).
    `rai_policy_id` (the full ARM id of an existing guardrail) attaches it, as for UC1 and UC6.
    """
    from azure.ai.projects.models import FunctionTool, PromptAgentDefinition, RaiConfig

    from app.agent.foundry_service import project_client, project_endpoint
    from app.policy.service import load_llm_config

    cfg = load_agent_config()
    tier = load_llm_config().tiers[cfg["model_tier"]]
    deployment = os.environ.get(tier.deployment_env, "") or tier.replay_model_id
    project = project_client(project_endpoint(), tenant_id=tenant_id)
    out = []
    for role in roles:
        a = cfg["agents"][role]
        tools = [FunctionTool(name=t["function"]["name"], description=t["function"]["description"],
                              parameters=t["function"]["parameters"], strict=False) for t in tool_schemas(role)]  # fmt: skip
        agent = project.agents.create_version(
            agent_name=a["name"], description=DESCRIPTIONS[role],
            definition=PromptAgentDefinition(model=deployment, instructions=(REPO / a["prompt_file"]).read_text("utf-8").strip(),
                                             tools=tools or None,
                                             rai_config=RaiConfig(rai_policy_name=rai_policy_id) if rai_policy_id else None),
        )  # fmt: skip
        out.append({"role": role, "name": agent.name, "version": str(agent.version), "model": deployment,
                    "tools": [t.name for t in tools], "instructions_version": a["prompt_version"],
                    "guardrail": rai_policy_id.rsplit("/", 1)[-1] if rai_policy_id else None})  # fmt: skip
    return out
