"""`classify_document`: a thin, deny-by-default adapter over `ClassificationService`.

It implements docs/uc4/mcp-contract.md and nothing more. It is independent of any MCP SDK, so the
policy (allowlist, per-caller caps, input subset, size limit) is testable and the SDK layer in
`server.py` stays a few lines.

* The result is the service's `ClassificationResult`, unchanged (no forked or wrapped schema).
* Statuses are data: a `rejected` / `review_required` result is a normal tool result. Only a caller
  that is not allowlisted gets an error (`McpAuthorizationError`), before any classification.
* `existing_labels`, `metadata`, `caller` and `document_id` are not accepted (spoofable, or not
  resolvable yet); the caller comes from the connection identity, never from tool arguments.
* Rejection reasons are field paths and short codes, never document text.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import Field, ValidationError

from app.classification.config_loader import ConfigError
from app.classification.schemas import Caller, ClassificationRequest, ClassificationResult
from app.classification.schemas.common import StrictModel
from app.classification.schemas.request import Budget, Document, Options
from app.classification.service import ClassificationService

from .config import TIER_RANK, CallerPolicy, McpConfig

PURPOSE = "mcp:classify_document"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,80}$")
_SAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9_.]")


class McpAuthorizationError(PermissionError):
    """The caller is not on the allowlist. Raised before any classification (MCP authorization
    error, not a result)."""


class ToolDocument(StrictModel):
    content: str
    filename: str = Field(default="document.txt", max_length=255)
    extension: str | None = Field(default=None, max_length=32)


class ToolOptions(StrictModel):
    mode: Literal["hybrid", "rules", "ml", "llm"] = "hybrid"
    max_llm_tier: Literal["none", "small", "mid", "large"] | None = None
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_latency_ms: int | None = Field(default=None, ge=1)
    include_evidence: bool | None = None


class ToolInput(StrictModel):
    request_id: str | None = None
    document: ToolDocument
    options: ToolOptions = Field(default_factory=ToolOptions)


def _lowest(a: float | None, b: float | None) -> float | None:
    vals = [v for v in (a, b) if v is not None]
    return min(vals) if vals else None


class ClassifyDocumentAdapter:
    def __init__(
        self, service: ClassificationService, config: McpConfig, *, caller_id: str
    ) -> None:
        if config.max_content_bytes > service.max_document_bytes:
            raise ConfigError(
                f"mcp max_content_bytes {config.max_content_bytes} exceeds the service hard limit "
                f"{service.max_document_bytes}"
            )
        self.service = service
        self.config = config
        self.caller_id = caller_id
        self._authorize()  # refuse to start for a caller that could never be served

    @property
    def tool_name(self) -> str:
        return self.config.tool_name

    # ---- the tool -----------------------------------------------------------------------------
    def classify_document(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run one tool call. Returns the `ClassificationResult` as JSON-compatible data."""
        policy = self._authorize()
        rid = self._request_id(arguments)
        try:
            parsed = ToolInput.model_validate(arguments)
        except ValidationError as exc:
            fields = sorted(
                {
                    _SAFE_PATH_CHARS.sub("_", ".".join(str(p) for p in e["loc"]))[:40]
                    for e in exc.errors()
                }
            )[:5]
            return self._reject(rid, "tool_input_invalid:" + ",".join(fields))
        try:
            size = len(parsed.document.content.encode("utf-8"))
        except UnicodeEncodeError:
            return self._reject(rid, "undecodable_text")
        if size > self.config.max_content_bytes:
            return self._reject(rid, "content_too_large")

        request = self._build_request(parsed, rid, policy)
        result: ClassificationResult = self.service.classify(request)
        return result.model_dump(mode="json")

    # ---- policy -------------------------------------------------------------------------------
    def _authorize(self) -> CallerPolicy:
        policy = self.config.callers.get(self.caller_id)
        if policy is None:
            raise McpAuthorizationError(f"caller is not allowed to call {self.config.tool_name}")
        return policy

    def _build_request(
        self, parsed: ToolInput, rid: str, policy: CallerPolicy
    ) -> ClassificationRequest:
        o = parsed.options
        tier = o.max_llm_tier or policy.max_llm_tier
        if TIER_RANK[tier] > TIER_RANK[policy.max_llm_tier]:
            tier = policy.max_llm_tier
        want_evidence = (
            self.config.default_include_evidence
            if o.include_evidence is None
            else o.include_evidence
        )
        filename = parsed.document.filename
        ext = parsed.document.extension
        if ext is None:
            ext = filename.rsplit(".", 1)[-1] if "." in filename else "txt"
        latency = _lowest(o.max_latency_ms, policy.max_latency_ms)
        return ClassificationRequest(
            request_id=rid,
            document=Document(content=parsed.document.content, filename=filename, extension=ext),
            options=Options(
                mode=o.mode,
                max_llm_tier=tier,
                budget=Budget(
                    max_cost_usd=_lowest(o.max_cost_usd, policy.max_cost_usd),
                    max_latency_ms=int(latency) if latency is not None else None,
                ),
                include_evidence=want_evidence and policy.allow_evidence,
            ),
            caller=Caller(caller_id=self.caller_id, purpose=PURPOSE),
        )

    def _request_id(self, arguments: Any) -> str:
        rid = arguments.get("request_id") if isinstance(arguments, dict) else None
        if isinstance(rid, str) and _REQUEST_ID_RE.match(rid):
            return rid
        return f"mcp-{uuid.uuid4().hex[:16]}"

    def _reject(self, rid: str, reason: str) -> dict[str, Any]:
        return self.service.reject(rid, reason).model_dump(mode="json")
