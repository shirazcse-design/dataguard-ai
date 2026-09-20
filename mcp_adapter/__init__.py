"""MCP adapter for `classify_document` (docs/uc4/mcp-contract.md). The core is SDK-independent;
`server.py` needs the optional `mcp` extra."""

from .adapter import ClassifyDocumentAdapter, McpAuthorizationError
from .config import McpConfig, load_mcp_config

__all__ = [
    "ClassifyDocumentAdapter",
    "McpAuthorizationError",
    "McpConfig",
    "load_mcp_config",
]
