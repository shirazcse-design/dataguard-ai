"""Builds a `DLPInvestigator` for a run mode. The only place modes become clients.

* `replay`  - UC4 recorded classifications, UC6 recorded answers/embeddings, recorded agent turns.
              No network. An unrecorded input is reported as such (UC4 review_required, UC6
              UNAVAILABLE, agent planner_error:replay_miss), never answered some other way.
* `offline` - as replay for UC4/UC6, but the agent is the labelled `offline-planner` (no model).
* `live`    - real Foundry calls (UC4 `foundry`, UC6 `live`, chat-completions or Agent
              Service planner).
* `record`  - live, and every new response is written to the committed caches for replay.

Agent backend: `chat-completions` (UC4's FoundryAgentClient on the `mid` deployment) or
`foundry-service` (the `dataguard-dlp-investigator` agent YOU create in Foundry Agent Service,
referenced by name only; nothing here creates it).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.agent.foundry_agent import FoundryAgentClient
from app.agent.types import AgentError
from app.classification.service import ClassificationService
from app.policy.agent import ReplayAgentClient
from app.policy.guard import load_policy_scanner
from app.policy.service import build_copilot as build_policy_copilot
from app.policy.service import load_llm_config
from mcp_adapter.adapter import ClassifyDocumentAdapter
from mcp_adapter.config import load_mcp_config
from rules.config import load_rules_config
from rules.engine import RulesEngine

from .agent import DlpAgent, OfflinePlanner, tool_schemas
from .config import load_dlp_configs
from .context import ActivityStore, DeterministicBehavior, ExceptionRegister, IdentityStore
from .integration import DocumentStore, PolicyIntelligence, Uc4Classifier
from .pipeline import DLPInvestigator

REPO = Path(__file__).resolve().parents[2]
MODES = ("replay", "offline", "live", "record")
AGENT_NAME_ENV = "DATAGUARD_DLP_AGENT_NAME"
SERVICE_PROMPT_VERSION = "uc1-agent-service.v1"


def _planner(mode: str, backend: str, dlp, tenant_id: str | None):
    llm = load_llm_config()
    tier = llm.tiers["mid"]
    tools = tool_schemas()
    if mode == "offline":
        return OfflinePlanner()
    if backend == "foundry-service":
        from app.agent.foundry_service import (
            FoundryAgentServiceClient,
            project_client,
            project_endpoint,
        )

        name = os.environ.get(AGENT_NAME_ENV, "") or dlp.agent.name
        live = None
        if mode in ("live", "record"):
            deployment = os.environ.get(tier.deployment_env, "") or tier.replay_model_id
            project = project_client(project_endpoint(), tenant_id=tenant_id)
            live = FoundryAgentServiceClient(
                project.get_openai_client(), deployment, agent_name=name
            )
        if mode == "live":
            return live
        return ReplayAgentClient(
            REPO / llm.cache.dir,
            name,
            SERVICE_PROMPT_VERSION,
            tools,
            inner=live if mode == "record" else None,
            always_live=True,
        )

    def foundry():
        deployment = os.environ.get(tier.deployment_env, "")
        if not deployment:
            raise AgentError("not_configured", f"{tier.deployment_env} is not set")
        return FoundryAgentClient(
            llm.foundry, deployment, tools=tools, max_output_tokens=dlp.agent.max_output_tokens
        )

    if mode == "live":
        return foundry()
    return ReplayAgentClient(
        REPO / llm.cache.dir,
        tier.replay_model_id,
        dlp.agent.prompt_version,
        tools,
        inner=foundry() if mode == "record" else None,
    )


AGENT_DESCRIPTION = (
    "UC1 Agentic DLP investigation agent: investigates a DLP event from a fixed evidence pack and "
    "proposes an outcome. Read-only tools execute in the DataGuard application; a deterministic "
    "harness makes the decision; no blocking."
)


def register_dlp_agent(tenant_id: str | None, rai_policy_id: str | None = None) -> dict:
    """Create a NEW VERSION of `dataguard-dlp-investigator` in Foundry Agent Service: the
    instructions (prompts/uc1/agent.v1.md), the `mid` deployment and the five function-tool
    DEFINITIONS (the tools still execute here). Additive: it never edits or deletes a version.
    Done at the product owner's explicit request (2026-10-01); guardrails, observability and
    evaluations remain manual. `rai_policy_id` (the full ARM id of an existing RAI policy) attaches
    that guardrail to the new version, the same mechanism as the UC6 agent."""
    from azure.ai.projects.models import FunctionTool, PromptAgentDefinition, RaiConfig

    from app.agent.foundry_service import project_client, project_endpoint

    dlp, *_ = load_dlp_configs()
    tier = load_llm_config().tiers["mid"]
    deployment = os.environ.get(tier.deployment_env, "") or tier.replay_model_id
    instructions = (REPO / dlp.agent.prompt_file).read_text(encoding="utf-8").strip()
    project = project_client(project_endpoint(), tenant_id=tenant_id)
    agent = project.agents.create_version(
        agent_name=dlp.agent.name,
        description=AGENT_DESCRIPTION,
        definition=PromptAgentDefinition(
            model=deployment,
            instructions=instructions,
            tools=[
                FunctionTool(
                    name=t["function"]["name"],
                    description=t["function"]["description"],
                    parameters=t["function"]["parameters"],
                    strict=False,
                )
                for t in tool_schemas()
            ],
            rai_config=RaiConfig(rai_policy_name=rai_policy_id) if rai_policy_id else None,
        ),
    )
    return {
        "name": agent.name,
        "version": str(agent.version),
        "model": deployment,
        "tools": [t["function"]["name"] for t in tool_schemas()],
        "instructions_version": dlp.agent.prompt_version,
        "guardrail": rai_policy_id.rsplit("/", 1)[-1] if rai_policy_id else None,
    }


def build_investigator(
    mode: str = "replay",
    *,
    agent_backend: str = "chat-completions",
    tenant_id: str | None = None,
    tracer: Any = None,
    planner: Any = None,
    with_agent: bool = True,
    copilot: Any = None,
    classification_service: ClassificationService | None = None,
) -> DLPInvestigator:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    dlp, mapping, rubric, hashes = load_dlp_configs()
    uc4_mode = {"replay": "replay", "offline": "replay", "live": "foundry", "record": "record"}[
        mode
    ]
    svc = classification_service or ClassificationService(llm_mode=uc4_mode)
    mcp_cfg, _ = load_mcp_config()
    adapter = ClassifyDocumentAdapter(svc, mcp_cfg, caller_id=Uc4Classifier.CALLER)
    rules_cfg, _ = load_rules_config(svc.bundle.policy)
    uc6_mode = {"replay": "replay", "offline": "offline", "live": "live", "record": "record"}[mode]
    copilot = copilot or build_policy_copilot(uc6_mode)
    identities, activity = IdentityStore(), ActivityStore()
    agent = None
    if with_agent:
        p = planner or _planner(mode, agent_backend, dlp, tenant_id)
        system = (REPO / dlp.agent.prompt_file).read_text(encoding="utf-8").strip()
        agent = DlpAgent(
            p, dlp.agent, system, backend=agent_backend if mode != "offline" else "offline"
        )
    return DLPInvestigator(
        dlp=dlp,
        mapping=mapping,
        rubric=rubric,
        hashes=hashes,
        documents=DocumentStore(),
        classifier=Uc4Classifier(adapter),
        rules_engine=RulesEngine(rules_cfg, svc.bundle.policy),
        identities=identities,
        activity=activity,
        exceptions=ExceptionRegister(),
        behavior=DeterministicBehavior(dlp.behavior, activity, identities),
        policy=PolicyIntelligence(copilot),
        copilot=copilot,
        agent=agent,
        scanner=load_policy_scanner(copilot.cfg.guard),
        mode={"record": "live"}.get(mode, mode),
        tracer=tracer,
    )
