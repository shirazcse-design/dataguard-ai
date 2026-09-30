"""Builds a `PolicyCopilot` for a run mode. The only place modes become clients.

* `replay`: recorded vectors only; recorded `uc4-llm-medium` responses only.
* `offline`: recorded vectors only; the `offline-extractive` generator (no model at all).
* `live`: recorded chunk vectors, new queries embedded live and kept in memory (never written);
  live Foundry generation, not written.
* `record`: misses embedded and answered live, and WRITTEN to the committed caches.

`replay` and `offline` need no network and no credentials. A replay miss is reported as such
(sparse-only retrieval, or `UNAVAILABLE`), never silently answered some other way.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.agent.foundry_agent import FoundryAgentClient
from app.agent.types import AgentError, AgentLLMClient
from app.classification.config_loader import default_config_dir, read_yaml
from app.llm.config import LLMConfig
from app.llm.foundry import FoundryClient
from app.llm.replay import ReplayLLMClient
from app.llm.types import LLMClient, LLMError

from .agent import AgentRunner, OfflinePlanner, ReplayAgentClient, tool_schemas
from .config import PolicyConfig, load_policy_config
from .corpus import Corpus, load_corpus
from .embeddings import CachedEmbedder, Embedder, FoundryEmbeddingClient
from .generate import ExtractiveGenerator, Generator, LLMGenerator, load_system_prompt
from .guard import load_policy_scanner
from .pipeline import PolicyCopilot
from .retrieval import Retriever

REPO = Path(__file__).resolve().parents[2]
MODES = ("replay", "offline", "live", "record")


def load_llm_config() -> LLMConfig:
    """The UC4 LLM configuration (tiers, Foundry adapter, retry, cache dir). Read directly: UC6
    does not need the UC4 taxonomy the full UC4 loader cross-checks."""
    data, _ = read_yaml(default_config_dir() / "llm" / "llm.v1.yaml")
    return LLMConfig.model_validate(data)


def build_embedder(cfg: PolicyConfig, mode: str, llm: LLMConfig) -> Embedder:
    live = mode in ("live", "record")
    return CachedEmbedder(
        REPO / cfg.embedding.cache_dir,
        cfg.embedding.replay_model_id,
        cfg.embedding.dimensions,
        inner=FoundryEmbeddingClient(llm.foundry, cfg.embedding) if live else None,
        persist=mode == "record",
    )


def build_generator(
    cfg: PolicyConfig, mode: str, llm: LLMConfig, client: LLMClient | None = None
) -> Generator:
    if mode == "offline":
        return ExtractiveGenerator()
    tier = llm.tiers[cfg.generation.tier]
    if client is None:

        def foundry() -> LLMClient:
            deployment = os.environ.get(tier.deployment_env, "")
            if not deployment:
                raise LLMError("not_configured", f"{tier.deployment_env} is not set")
            return FoundryClient(llm.foundry, deployment, api=tier.api)

        if mode == "live":
            client = foundry()
        else:  # replay / record share the committed cache, keyed by the replay model id
            client = ReplayLLMClient(
                REPO / llm.cache.dir,
                tier.replay_model_id,
                inner=foundry() if mode == "record" else None,
            )
    return LLMGenerator(
        client,
        cfg.generation,
        llm.retry,
        load_system_prompt(REPO, cfg.generation),
        temperature=llm.generation.temperature if tier.send_temperature else None,
        mode="live" if mode in ("live", "record") else "replay",
    )


AGENT_BACKENDS = ("chat-completions", "foundry-service")
AGENT_NAME_ENV = "DATAGUARD_POLICY_AGENT_NAME"
SERVICE_PROMPT_VERSION = "uc6-agent-service.v1"


def foundry_service_planner(cfg: PolicyConfig, llm: LLMConfig, tenant_id: str | None) -> Any:
    """UC4's Agent Service client pointed at the UC6 agent YOU created in the portal. Nothing is
    created or updated here: the agent is only referenced by name. Entra ID sign-in (browser)."""
    from app.agent.foundry_service import (
        FoundryAgentServiceClient,
        project_client,
        project_endpoint,
    )

    tier = llm.tiers[cfg.generation.tier]
    deployment = os.environ.get(tier.deployment_env, "") or tier.replay_model_id
    name = os.environ.get(AGENT_NAME_ENV, "") or cfg.agent.name
    project = project_client(project_endpoint(), tenant_id=tenant_id)
    return FoundryAgentServiceClient(project.get_openai_client(), deployment, agent_name=name)


AGENT_DESCRIPTION = (
    "UC6 Data Security Policy Copilot: answers data-security policy questions only from "
    "retrieved, cited policy evidence. Tools execute in the DataGuard application "
    "(dataguard-policy ask --level agentic --agent-backend foundry-service), not in the portal."
)


def register_policy_agent(cfg: PolicyConfig, llm: LLMConfig, tenant_id: str | None) -> dict:
    """Create a NEW VERSION of the UC6 agent in Foundry Agent Service: instructions
    (prompts/uc6/agent.v1.md), the `mid` deployment and the four function-tool DEFINITIONS.
    Additive: earlier versions (including any started in the portal) are kept; runs use the
    latest. Done at the product owner's explicit request (2026-09-29); evaluations, guardrails and
    observability remain manual."""
    from azure.ai.projects.models import FunctionTool, PromptAgentDefinition

    from app.agent.foundry_service import project_client, project_endpoint

    tier = llm.tiers[cfg.generation.tier]
    deployment = os.environ.get(tier.deployment_env, "") or tier.replay_model_id
    instructions = (REPO / cfg.agent.prompt_file).read_text(encoding="utf-8").strip()
    project = project_client(project_endpoint(), tenant_id=tenant_id)
    agent = project.agents.create_version(
        agent_name=cfg.agent.name,
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
        ),
    )
    return {
        "name": agent.name,
        "version": str(agent.version),
        "model": deployment,
        "tools": [t["function"]["name"] for t in tool_schemas()],
        "instructions_version": cfg.agent.prompt_version,
    }


def build_agent(
    copilot: PolicyCopilot,
    cfg: PolicyConfig,
    mode: str,
    llm: LLMConfig,
    planner: AgentLLMClient | None = None,
    *,
    backend: str = "chat-completions",
    tenant_id: str | None = None,
) -> AgentRunner:
    """The Agentic RAG runner.

    * `chat-completions` (default): chat-completions tool calling on the `mid` deployment (UC4's
      FoundryAgentClient); recordings under `<replay model id>/uc6-agent.v1`.
    * `foundry-service`: the Foundry Agent Service agent created MANUALLY in the portal
      (docs/uc6/foundry-agent-setup.md); recordings under `<agent name>/uc6-agent-service.v1`.
    """
    if backend not in AGENT_BACKENDS:
        raise ValueError(f"unknown agent backend {backend!r}")
    tier = llm.tiers[cfg.generation.tier]
    tools = tool_schemas()
    if planner is None and backend == "foundry-service" and mode != "offline":
        name = os.environ.get(AGENT_NAME_ENV, "") or cfg.agent.name
        live = foundry_service_planner(cfg, llm, tenant_id) if mode in ("live", "record") else None
        if mode == "live":
            planner = live
        else:
            planner = ReplayAgentClient(
                REPO / llm.cache.dir, name, SERVICE_PROMPT_VERSION, tools,
                inner=live if mode == "record" else None, always_live=True,
            )  # fmt: skip
    if planner is None:
        if mode == "offline":
            planner = OfflinePlanner()
        else:

            def foundry() -> AgentLLMClient:
                deployment = os.environ.get(tier.deployment_env, "")
                if not deployment:
                    raise AgentError("not_configured", f"{tier.deployment_env} is not set")
                return FoundryAgentClient(
                    llm.foundry, deployment, tools=tools,
                    max_output_tokens=cfg.agent.max_output_tokens,
                )  # fmt: skip

            if mode == "live":
                planner = foundry()
            else:
                planner = ReplayAgentClient(
                    REPO / llm.cache.dir, tier.replay_model_id, cfg.agent.prompt_version, tools,
                    inner=foundry() if mode == "record" else None,
                )  # fmt: skip
    system = (REPO / cfg.agent.prompt_file).read_text(encoding="utf-8").strip()
    label = {"offline": "offline", "live": "live", "record": "live"}.get(mode, "replay")
    runner = AgentRunner(copilot, planner, cfg.agent, system, mode=label)
    runner.backend = backend if mode != "offline" else "offline"
    return runner


def build_copilot(
    mode: str = "replay",
    *,
    llm_client: LLMClient | None = None,
    embedder: Embedder | None = None,
    tracer: Any = None,
    config: tuple[PolicyConfig, Corpus] | None = None,
    agent_planner: AgentLLMClient | None = None,
    agent_backend: str = "chat-completions",
    tenant_id: str | None = None,
) -> PolicyCopilot:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    if config is None:
        cfg, _ = load_policy_config()
        corpus = load_corpus(REPO / cfg.corpus.dir, cfg.chunking)
    else:
        cfg, corpus = config
    llm = load_llm_config()
    retriever = Retriever(corpus, cfg, embedder or build_embedder(cfg, mode, llm))
    copilot = PolicyCopilot(
        corpus,
        cfg,
        retriever,
        build_generator(cfg, mode, llm, llm_client),
        load_policy_scanner(cfg.guard),
        tracer=tracer,
    )
    copilot.agent = build_agent(
        copilot, cfg, mode, llm, agent_planner, backend=agent_backend, tenant_id=tenant_id
    )
    return copilot
