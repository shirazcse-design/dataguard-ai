"""Foundry chat-completions client WITH tool-calling, for the Batch Triage Agent only.

A separate, small adapter from `app/llm/foundry.py` (which does single-shot structured-output
classification): tool-calling needs a growing message history, not one system+user pair. Reuses
the SAME `FoundryConfig` (same resource, same endpoint/auth env vars) since it is the identical
Azure AI Foundry deployment, just a different call shape. Stdlib HTTP only, same conventions as the
classification adapter: credentials from the environment, never logged, never sent over plain HTTP
to a non-loopback host. Tested against a local fake server; no unit test contacts a real Foundry
deployment.

Scope: chat-completions tool-calling only (the `small`/`mid` tiers). The `large` tier's Responses
API has a different tool-calling shape and is not implemented here - `planner_tier: large` is a
config error, not a silent mismatch.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlparse

from app.llm.config import FoundryConfig

from .types import AgentError, AgentTurn, ParsedToolCall

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class FoundryAgentClient:
    """One chat-completions call per turn, with `tools` attached. `deployment` is the planner
    tier's deployment name (from `DATAGUARD_LLM_DEPLOYMENT_<TIER>`, resolved by the caller)."""

    def __init__(
        self,
        cfg: FoundryConfig,
        deployment: str,
        *,
        tools: list[dict[str, Any]],
        max_output_tokens: int = 2000,
        env: Mapping[str, str] | None = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.cfg = cfg
        self.deployment = deployment
        self.tools = tools
        self.max_output_tokens = max_output_tokens
        self._env = env if env is not None else os.environ
        self._opener = opener

    def _required(self, var: str) -> str:
        value = self._env.get(var)
        if not value:
            raise AgentError("not_configured", f"environment variable {var} is not set")
        return value

    def _base(self) -> str:
        endpoint = self._required(self.cfg.endpoint_env).rstrip("/")
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" and parsed.hostname not in _LOOPBACK:
            raise AgentError("not_configured", "the endpoint must be https (or loopback for tests)")
        cut = parsed.path.find("/api/projects")
        if cut >= 0:
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path[:cut]}"
        return endpoint

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.cfg.auth == "api_key":
            headers["api-key"] = self._required(self.cfg.api_key_env)
        else:
            raise AgentError("not_configured", "the agent client supports auth='api_key' only")
        return headers

    def next_turn(self, messages: list[dict[str, Any]]) -> AgentTurn:
        url = self.cfg.url_template.format(endpoint=self._base(), deployment=self.deployment)
        body = {
            "model": self.deployment,
            "messages": messages,
            "tools": self.tools,
            "tool_choice": "auto",
            self.cfg.max_tokens_param: self.max_output_tokens,
        }
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        try:
            with self._opener(request, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            raise AgentError("http_error", f"HTTP {err.code}") from None
        except TimeoutError:
            raise AgentError("timeout", "no response within the timeout") from None
        except urllib.error.URLError as err:
            raise AgentError("transport", type(err.reason).__name__) from None
        except (ValueError, UnicodeDecodeError):
            raise AgentError("transport", "response was not valid JSON") from None
        return self._parse(payload)

    def _parse(self, payload: Any) -> AgentTurn:
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise AgentError("transport", "unexpected response shape") from None
        raw_calls = message.get("tool_calls") or []
        if raw_calls:
            calls = []
            for c in raw_calls:
                try:
                    fn = c["function"]
                    args = json.loads(fn["arguments"]) if fn.get("arguments") else {}
                    calls.append(ParsedToolCall(id=c["id"], name=fn["name"], arguments=args))
                except (KeyError, TypeError, ValueError):
                    raise AgentError("transport", "malformed tool_call in response") from None
            return AgentTurn(tool_calls=calls)
        content = message.get("content")
        return AgentTurn(final_text=content if isinstance(content, str) else "")


def build_agent_client(
    cfg: FoundryConfig, planner_tier: str, deployment: str, tools: list[dict[str, Any]]
) -> FoundryAgentClient:
    if planner_tier == "large":
        raise AgentError(
            "not_configured",
            "planner_tier 'large' uses the Responses API, whose tool-calling shape is not "
            "implemented here; use 'small' or 'mid'",
        )
    return FoundryAgentClient(cfg, deployment, tools=tools)
