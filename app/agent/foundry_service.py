"""The Batch Triage Agent registered in Microsoft Foundry Agent Service (decision D9.33).

Foundry holds the agent's DEFINITION - its instructions (`SYSTEM_PROMPT`), its planner model and the
three function-tool schemas - so it is a real, versioned agent on the portal's Agents page. It does
NOT hold the tools: Agent Service function tools are executed by the caller. Each planner turn is
one Responses API call that references the registered agent; any `function_call` it returns is
handed back to `app/agent/loop.py`, which runs it through the same `ToolRegistry`, allowlist, step
budget and never-downgrade safety invariant as every other planner. Nothing about the decision path
moves into Foundry.

Auth is Microsoft Entra ID only (Agent Service does not take the resource API key):
`DefaultAzureCredential`, with the interactive browser sign-in enabled as its last resort, so no
Azure CLI is needed. The SDKs (`azure-ai-projects`, `azure-identity`, `openai`) are the optional
`foundry-agents` extra and are imported lazily; unit tests inject fakes and never reach Azure.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from .loop import SYSTEM_PROMPT
from .tools import tool_schemas
from .types import AgentError, AgentTurn, ParsedToolCall

AGENT_NAME = "dataguard-batch-triage"
PROJECT_ENDPOINT_ENV = "DATAGUARD_FOUNDRY_PROJECT_ENDPOINT"


def project_endpoint(env: Mapping[str, str] | None = None) -> str:
    env = env if env is not None else os.environ
    endpoint = (env.get(PROJECT_ENDPOINT_ENV) or "").rstrip("/")
    if not endpoint:
        raise AgentError(
            "not_configured", f"environment variable {PROJECT_ENDPOINT_ENV} is not set"
        )
    if not endpoint.startswith("https://") or "/api/projects/" not in endpoint:
        raise AgentError(
            "not_configured",
            f"{PROJECT_ENDPOINT_ENV} must be a project endpoint: "
            "https://<resource>.services.ai.azure.com/api/projects/<project>",
        )
    return endpoint


def project_client(endpoint: str) -> Any:
    """An `AIProjectClient` signed in with Entra ID. Raises `ImportError` without the extra."""
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
    return AIProjectClient(endpoint=endpoint, credential=credential)


def function_tool_specs() -> list[dict[str, Any]]:
    """The same three tool schemas the loop's allowlist uses, in Agent Service's FunctionTool
    shape. Not `strict`: `classify_document.filename` is optional, which strict mode forbids."""
    return [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "parameters": t["function"]["parameters"],
            "strict": False,
        }
        for t in tool_schemas()
    ]


def register_agent(project: Any, model_deployment: str) -> tuple[str, str]:
    """Create a new version of the agent in Foundry Agent Service; returns `(name, version)`.
    Re-registering is safe: Agent Service keeps each version, and runs use the latest."""
    from azure.ai.projects.models import FunctionTool, PromptAgentDefinition

    agent = project.agents.create_version(
        agent_name=AGENT_NAME,
        definition=PromptAgentDefinition(
            model=model_deployment,
            instructions=SYSTEM_PROMPT,
            tools=[FunctionTool(**spec) for spec in function_tool_specs()],
        ),
    )
    return agent.name, str(agent.version)


class FoundryAgentServiceClient:
    """One Responses API call per planner turn, referencing the registered agent.

    The loop passes its whole message list each turn; Agent Service keeps the conversation
    server-side, so this client sends only what is new - the user message on a document's first
    turn, then the `function_call_output`s for the tool results the loop appended - chained with
    `previous_response_id`. A message list of just [system, user] means a new document, so no
    state can carry over from one document to the next (the batch runner's isolation guarantee).
    """

    name = "foundry-agent-service"

    def __init__(self, openai_client: Any, model_deployment: str, agent_name: str = AGENT_NAME):
        self._openai = openai_client
        self.deployment = model_deployment
        self.agent_name = agent_name
        self._previous_id: str | None = None
        self._consumed = 0

    def next_turn(self, messages: list[dict[str, Any]]) -> AgentTurn:
        if len(messages) <= 2:  # [system, user]: a new document
            self._previous_id, self._consumed = None, 0
            new_input: list[dict[str, Any]] = [
                {"role": "user", "content": m["content"]} for m in messages if m["role"] == "user"
            ]
        else:
            new_input = [
                {
                    "type": "function_call_output",
                    "call_id": m["tool_call_id"],
                    "output": m["content"],
                }
                for m in messages[self._consumed :]
                if m.get("role") == "tool"
            ]
        kwargs: dict[str, Any] = {
            "input": new_input,
            "extra_body": {"agent_reference": {"name": self.agent_name, "type": "agent_reference"}},
        }
        if self._previous_id is not None:
            kwargs["previous_response_id"] = self._previous_id
        try:
            response = self._openai.responses.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped to a modeled error; class name only
            raise AgentError(_error_kind(exc), type(exc).__name__) from None
        self._previous_id = getattr(response, "id", None)
        self._consumed = len(messages)
        return _parse(response, self.deployment)


def _error_kind(exc: Exception) -> str:
    name = type(exc).__name__
    if "Timeout" in name:
        return "timeout"
    if getattr(exc, "status_code", None) is not None:
        return "http_error"
    return "transport"


def _parse(response: Any, deployment: str) -> AgentTurn:
    usage = getattr(response, "usage", None)
    meta = {
        "model_id": deployment,
        "served_model": _str_or_none(getattr(response, "model", None)),
        "tokens_in": _int_or_none(getattr(usage, "input_tokens", None)),
        "tokens_out": _int_or_none(getattr(usage, "output_tokens", None)),
    }
    calls = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "function_call":
            continue
        try:
            raw = item.arguments
            args = json.loads(raw) if raw else {}
            if not isinstance(args, dict):
                raise ValueError
            calls.append(ParsedToolCall(id=item.call_id, name=item.name, arguments=args))
        except (AttributeError, TypeError, ValueError):
            raise AgentError("transport", "malformed function_call in response") from None
    if calls:
        return AgentTurn(tool_calls=calls, **meta)
    text = getattr(response, "output_text", None)
    return AgentTurn(final_text=text if isinstance(text, str) else "", **meta)


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
