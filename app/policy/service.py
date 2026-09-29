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

from app.classification.config_loader import default_config_dir, read_yaml
from app.llm.config import LLMConfig
from app.llm.foundry import FoundryClient
from app.llm.replay import ReplayLLMClient
from app.llm.types import LLMClient, LLMError

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


def build_copilot(
    mode: str = "replay",
    *,
    llm_client: LLMClient | None = None,
    embedder: Embedder | None = None,
    tracer: Any = None,
    config: tuple[PolicyConfig, Corpus] | None = None,
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
    return PolicyCopilot(
        corpus,
        cfg,
        retriever,
        build_generator(cfg, mode, llm, llm_client),
        load_policy_scanner(cfg.guard),
        tracer=tracer,
    )
