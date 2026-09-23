"""The Batch Triage Agent (docs/uc4/agent-plan.md): a NEW, separate orchestrator that uses
`classify_document` as a tool. It does not modify ClassificationService, the hybrid router,
fusion or review logic in any way."""

from .batch import run_batch
from .config import AgentConfig, load_agent_config
from .foundry_agent import FoundryAgentClient, build_agent_client
from .loop import run_document
from .mock import MockAgentClient
from .offline_policy import offline_policy
from .schemas import BatchTriageReport, DocumentAnnotation, ToolCallRecord
from .tools import ToolRegistry, tool_schemas
from .types import AgentError, AgentLLMClient, AgentTurn, ParsedToolCall

__all__ = [
    "AgentConfig",
    "AgentError",
    "AgentLLMClient",
    "AgentTurn",
    "BatchTriageReport",
    "DocumentAnnotation",
    "FoundryAgentClient",
    "MockAgentClient",
    "ParsedToolCall",
    "ToolCallRecord",
    "ToolRegistry",
    "build_agent_client",
    "load_agent_config",
    "offline_policy",
    "run_batch",
    "run_document",
    "tool_schemas",
]
