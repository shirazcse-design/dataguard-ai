"""UC5 wiring: services + the agent's planner for each mode (offline / replay / record / live) and
backend (chat-completions, or the Foundry Agent Service agent `dataguard-incident-investigator`,
which the product owner creates MANUALLY in the Foundry portal; nothing here creates it)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .pipeline import IncidentInvestigator, OfflinePlanner, load_agent_config
from .services import IncidentServices, build_services

REPO = Path(__file__).resolve().parents[2]
MODES = ("offline", "replay", "record", "live")
AGENT_ENV = "DATAGUARD_INCIDENT_AGENT"  # the Foundry agent name, if it differs from the config


def tool_schemas() -> list[dict[str, Any]]:
    """The agent's tool definitions (identical for every case; part of the replay key and of the
    Foundry agent's configuration)."""
    from .tools import CaseState, build_tools

    return [t.schema() for t in build_tools(CaseState(svc=None, incident=None, ledger=None))]  # type: ignore[arg-type]


def planner(mode: str, backend: str = "chat-completions", tenant_id: str | None = None) -> Any:
    if mode == "offline":
        return OfflinePlanner()
    from app.agent.foundry_agent import FoundryAgentClient
    from app.agent.types import AgentError
    from app.policy.agent import ReplayAgentClient
    from app.policy.service import load_llm_config

    cfg, llm = load_agent_config(), load_llm_config()
    tier = llm.tiers[cfg["model_tier"]]
    schemas = tool_schemas()
    foundry = backend == "foundry-service"
    live = None
    if mode in ("live", "record"):
        if foundry:
            from app.agent.foundry_service import (
                FoundryAgentServiceClient,
                project_client,
                project_endpoint,
            )

            project = project_client(project_endpoint(), tenant_id=tenant_id)
            deployment = os.environ.get(tier.deployment_env, "") or tier.replay_model_id
            # A bounded per-call timeout and one retry: run 2's Foundry calls hung ~300 s each.
            oa = project.get_openai_client().with_options(
                timeout=float(cfg.get("call_timeout_s", 30)), max_retries=1
            )
            live = FoundryAgentServiceClient(oa, deployment,
                                             agent_name=os.environ.get(AGENT_ENV, "") or cfg["name"])  # fmt: skip
        else:
            deployment = os.environ.get(tier.deployment_env, "")
            if not deployment:
                raise AgentError("not_configured", f"{tier.deployment_env} is not set")
            live = FoundryAgentClient(llm.foundry, deployment, tools=schemas, max_output_tokens=4000,
                                      timeout_s=float(cfg.get("call_timeout_s", 30)))  # fmt: skip
    if mode == "live":
        return live
    model_ns = (os.environ.get(AGENT_ENV, "") or cfg["name"]) if foundry else tier.replay_model_id
    version = (
        cfg["prompt_version"].replace("uc5-", "uc5-service-") if foundry else cfg["prompt_version"]
    )
    return ReplayAgentClient(REPO / llm.cache.dir, model_ns, version, schemas,
                             inner=live if mode == "record" else None, always_live=foundry)  # fmt: skip


def build_investigator(mode: str = "offline", *, backend: str = "chat-completions", tenant_id: str | None = None,
                       tracer: Any = None, svc: IncidentServices | None = None) -> IncidentInvestigator:  # fmt: skip
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    # offline: scripted agent, UC4 replay, UC6 offline (no network). replay/record/live as UC3.
    svc = svc or build_services(
        {"record": "record", "live": "live", "offline": "offline"}.get(mode, "replay")
    )
    label = "offline" if mode == "offline" else backend
    return IncidentInvestigator(svc, planner(mode, backend, tenant_id), mode={"record": "live"}.get(mode, mode),
                                backend=label, tracer=tracer)  # fmt: skip


DESCRIPTION = ("UC5 Incident Investigation: investigates ONE data-security incident case with 10 read-only, case-bound "
               "tools and writes an evidence-backed report. It cannot act; a deterministic harness sets severity and an "
               "analyst owns every action.")  # fmt: skip


def register_agent(tenant_id: str | None, rai_policy_id: str | None = None) -> dict[str, Any]:
    """Create a NEW VERSION of `dataguard-incident-investigator` in Foundry Agent Service: instructions, the
    `mid` deployment and the 10 function-tool DEFINITIONS (the tools still execute in DataGuard). Additive:
    never edits or deletes a version. Done at the product owner's explicit request (2026-10-07), replacing
    the planned manual portal step. The agent is read back to verify what was stored."""
    from azure.ai.projects.models import FunctionTool, PromptAgentDefinition, RaiConfig

    from app.agent.foundry_service import project_client, project_endpoint
    from app.policy.service import load_llm_config

    cfg = load_agent_config()
    tier = load_llm_config().tiers[cfg["model_tier"]]
    deployment = os.environ.get(tier.deployment_env, "") or tier.replay_model_id
    project = project_client(project_endpoint(), tenant_id=tenant_id)
    try:
        existing = [str(v.version) for v in project.agents.list_versions(agent_name=cfg["name"])]
    except Exception:  # noqa: BLE001 - a missing agent is the normal case
        existing = []
    tools = [FunctionTool(name=t["function"]["name"], description=t["function"]["description"],
                          parameters=t["function"]["parameters"], strict=False) for t in tool_schemas()]  # fmt: skip
    instructions = (REPO / cfg["prompt_file"]).read_text("utf-8").strip()
    agent = project.agents.create_version(
        agent_name=cfg["name"], description=DESCRIPTION,
        definition=PromptAgentDefinition(model=deployment, instructions=instructions, tools=tools,
                                         rai_config=RaiConfig(rai_policy_name=rai_policy_id) if rai_policy_id else None),
    )  # fmt: skip
    back = project.agents.get_version(agent_name=cfg["name"], agent_version=agent.version)
    d = back.definition
    stored = sorted(t.name for t in (d.tools or []))
    return {"name": agent.name, "version": str(agent.version), "previous_versions": existing, "model": d.model,
            "tools": stored, "tools_match": stored == sorted(t.name for t in tools),
            "instructions_match": (d.instructions or "").strip() == instructions, "instructions_version": cfg["prompt_version"],
            "guardrail": rai_policy_id.rsplit("/", 1)[-1] if rai_policy_id else None,
            "guardrail_read_back": ((getattr(d, "rai_config", None) and d.rai_config.rai_policy_name) or "").rsplit("/", 1)[-1] or None}  # fmt: skip
