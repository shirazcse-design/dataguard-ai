"""A deterministic, no-credentials-needed planner for `agent triage --agent-mode mock` (CLI/demo/CI
use, analogous to the classifier's own `llm_mode="off"`).

This is NOT a claim of agent intelligence - it is a fixed, inspectable policy: call
`classify_document` once, add `request_human_review` only if the tool itself already flagged
`review_required`, then emit a priority derived solely from the tool's own fields. It exists so the
loop, the batch runner, and the CLI can be exercised end to end without Foundry credentials. A real
planner run uses `FoundryAgentClient` via `--agent-mode foundry`.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .types import AgentTurn, ParsedToolCall

_CONTENT_RE = re.compile(
    r"^Filename: (.*)\n\nContent:\n(.*)\n\nTriage this document now\.\Z", re.DOTALL
)


def _extract_content_and_filename(messages: list[dict[str, Any]]) -> tuple[str, str | None]:
    """The filename matters: it shapes the classifier's prompt (extension-based hints), so a
    replay-cache lookup with the wrong filename is a cache miss dressed up as a real result -
    exactly the fairness-probe cache-artifact pitfall this project already hit once."""
    user = next(m for m in messages if m.get("role") == "user")
    match = _CONTENT_RE.search(user["content"])
    if not match:
        return user["content"], None
    return match.group(2), match.group(1)


def offline_policy(messages: list[dict[str, Any]]) -> AgentTurn:
    made: list[tuple[str, str]] = []
    results: dict[str, dict] = {}
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            made.extend((c["id"], c["function"]["name"]) for c in m["tool_calls"])
        elif m.get("role") == "tool":
            results[m["tool_call_id"]] = json.loads(m["content"])

    called = {name for _, name in made}
    if "classify_document" not in called:
        content, filename = _extract_content_and_filename(messages)
        args: dict[str, Any] = {"content": content}
        if filename:
            args["filename"] = filename
        call = ParsedToolCall(id="1", name="classify_document", arguments=args)
        return AgentTurn(tool_calls=[call])

    classify_id = next(cid for cid, name in made if name == "classify_document")
    result = results.get(classify_id, {})
    if result.get("status") == "review_required" and "request_human_review" not in called:
        return AgentTurn(
            tool_calls=[
                ParsedToolCall(
                    id="2",
                    name="request_human_review",
                    arguments={"reason": "classify_document marked this document review_required"},
                )
            ]
        )

    high_risk = bool((result.get("high_risk") or {}).get("value"))
    status = result.get("status", "error")
    priority = "high" if high_risk else ("medium" if status == "review_required" else "low")
    rationale = f"classify_document returned status={status}, high_risk={high_risk}"
    return AgentTurn(final_text=json.dumps({"priority": priority, "rationale": rationale}))
