"""The Batch Triage Agent's fixed tool allowlist (`docs/uc4/agent-plan.md`).

Any tool name outside `ToolRegistry.call`'s three branches is rejected at the function-calling
boundary with `error_kind="unknown_tool"` - never silently ignored, never routed anywhere. Each
tool is a thin, stateless wrapper: `classify_document` calls the SAME `ClassificationService` the
MCP adapter uses, unchanged; `lookup_taxonomy_definition` is read-only against the loaded config;
`request_human_review` only ever returns a structured recommendation, never remediates.

The "only request review for a document classify_document already flagged" invariant is enforced
by the LOOP after the fact (`app/agent/loop.py`), not here - this registry is deliberately
stateless per call, so it stays simple to test and audit in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.classification.config_loader import ConfigBundle
from app.classification.service import ClassificationService

TOOL_NAMES = ("classify_document", "lookup_taxonomy_definition", "request_human_review")


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error_kind: str | None = None  # "invalid_arguments" | "unknown_id" | "unknown_tool"


def tool_schemas() -> list[dict[str, Any]]:
    """OpenAI/Azure-compatible function-calling tool definitions, in allowlist order."""
    return [
        {
            "type": "function",
            "function": {
                "name": "classify_document",
                "description": (
                    "Classify one pre-extracted document by sensitivity level and data "
                    "categories. Returns a recommendation; it never blocks or remediates."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "the document's text"},
                        "filename": {"type": "string"},
                    },
                    "required": ["content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lookup_taxonomy_definition",
                "description": "Look up the verbatim definition of a sensitivity level or data "
                "category id (e.g. HIGHLY_CONFIDENTIAL, PHI). Read-only.",
                "parameters": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "request_human_review",
                "description": "Recommend one document for human review, with a reason. Never "
                "blocks, deletes, quarantines or notifies anything external.",
                "parameters": {
                    "type": "object",
                    "properties": {"reason": {"type": "string"}},
                    "required": ["reason"],
                },
            },
        },
    ]


class ToolRegistry:
    def __init__(self, service: ClassificationService, bundle: ConfigBundle) -> None:
        self.service = service
        self.bundle = bundle

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name == "classify_document":
            return self._classify_document(arguments)
        if name == "lookup_taxonomy_definition":
            return self._lookup_taxonomy_definition(arguments)
        if name == "request_human_review":
            return self._request_human_review(arguments)
        return ToolResult(ok=False, error_kind="unknown_tool")

    def _classify_document(self, arguments: dict[str, Any]) -> ToolResult:
        content = arguments.get("content")
        if not isinstance(content, str) or not content:
            return ToolResult(ok=False, error_kind="invalid_arguments")
        filename = arguments.get("filename")
        filename = filename if isinstance(filename, str) and filename else "document.txt"
        result = self.service.classify_text(content, filename=filename)
        return ToolResult(ok=True, data=result.model_dump(mode="json"))

    def _lookup_taxonomy_definition(self, arguments: dict[str, Any]) -> ToolResult:
        id_ = arguments.get("id")
        if not isinstance(id_, str) or not id_:
            return ToolResult(ok=False, error_kind="invalid_arguments")
        for lv in self.bundle.taxonomy.levels:
            if lv.id == id_:
                return ToolResult(
                    ok=True, data={"id": lv.id, "name": lv.name, "description": lv.description}
                )
        for c in self.bundle.taxonomy.categories:
            if c.id == id_:
                return ToolResult(
                    ok=True, data={"id": c.id, "name": c.name, "description": c.description}
                )
        return ToolResult(ok=False, error_kind="unknown_id")

    def _request_human_review(self, arguments: dict[str, Any]) -> ToolResult:
        reason = arguments.get("reason")
        if not isinstance(reason, str) or not reason:
            return ToolResult(ok=False, error_kind="invalid_arguments")
        return ToolResult(ok=True, data={"requested": True, "reason": reason[:200]})
