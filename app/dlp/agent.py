"""The DLP investigation agent (`dataguard-dlp-investigator`): bounded, read-only, an ADVISOR.

It receives an evidence pack (never the document text), may call 5 allow-listed tools, and returns
structured findings + a PROPOSED outcome. The deterministic harness (`harness.py`) owns the final
response: the agent's proposal can only raise it to HUMAN_REVIEW, never lower it.

Controls enforced HERE, in code:
* allow-list of 5 tools; any other name -> `unknown_tool` (a failed step); no write tools exist;
* every tool is bound to THIS event: `get_user_activity` and `check_dlp_exception` take no user or
  host argument, so the agent cannot look at another user or another destination;
* arguments validated; review reasons from a fixed list;
* at most `max_tool_calls` calls and `max_turns` turns; `max_consecutive_tool_failures` failures
  in a row stop the run; a stopped run proposes nothing (the harness decides without it);
* a verified DLP exception comes ONLY from a `check_dlp_exception` result (the harness re-reads it);
* findings must cite an evidence id that exists in the pack or a tool result, else they are kept
  as UNVERIFIED (counted, never used by the harness);
* the user's justification is scanned for injection; flagged text is withheld from the pack.

Reuses UC6: `search_policy` / `get_policy_section` are UC6's `PolicyTools` (Advanced retrieval,
withheld injected chunks, harness-assigned E-ids); planners are UC6's `ReplayAgentClient` and UC4's
chat-completions / Agent Service clients.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from app.agent.types import AgentError, AgentTurn, ParsedToolCall
from observability import span

from .config import AgentConfig
from .context import ContextError
from .schemas import AgentResult

REVIEW_REASONS = (
    "insufficient_evidence",
    "conflicting_evidence",
    "high_risk_needs_analyst",
    "suspicious_justification",
    "other",
)
OUTCOMES = ("ALLOW", "WARN", "ESCALATE", "HUMAN_REVIEW")


def tool_schemas() -> list[dict[str, Any]]:
    def fn(name: str, description: str, props: dict, required: list[str]) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }

    return [
        fn(
            "search_policy",
            "Search the approved data-security policy corpus for a specific clause. "
            "Returns up to 5 sections with evidence ids, citation, status and text.",
            {"query": {"type": "string", "description": "What to look for, in plain words."}},
            ["query"],
        ),
        fn(
            "get_policy_section",
            "Read one exact policy section (current version).",
            {
                "policy_id": {"type": "string", "description": "e.g. POL-DLP"},
                "section": {"type": "string", "description": "e.g. 4.2"},
            },
            ["policy_id", "section"],
        ),
        fn(
            "get_user_activity",
            "Recent activity features for the user in THIS event (7-day window).",
            {"days": {"type": "integer", "description": "Window in days, 1-30 (data covers 7)."}},
            ["days"],
        ),
        fn(
            "check_dlp_exception",
            "Look up an approved DLP exception for THIS user and THIS destination "
            "covering the file's sensitivity. Returns the record, or why none applies.",
            {},
            [],
        ),
        fn(
            "request_human_review",
            "Ask a human analyst to review this case.",
            {"reason": {"type": "string", "enum": list(REVIEW_REASONS)}},
            ["reason"],
        ),
    ]


TOOL_NAMES = tuple(t["function"]["name"] for t in tool_schemas())


class OfflinePlanner:
    """Deterministic stand-in (no model), labelled `offline-planner`: checks activity when the band
    is not NORMAL, checks for an exception when the destination is not approved, then proposes an
    outcome from the pack with a simple rule. It exercises the loop with no recordings."""

    name = "offline"
    model_id = "offline-planner"
    last_cached: bool | None = None

    def next_turn(self, messages: list[dict[str, Any]]) -> AgentTurn:
        pack = json.loads(next(m["content"] for m in messages if m["role"] == "user"))
        done = {json.loads(m["content"]).get("_tool") for m in messages if m.get("role") == "tool"}
        n = len([m for m in messages if m.get("role") == "tool"]) + 1
        if pack["behavior"]["band"] != "NORMAL" and "get_user_activity" not in done:
            return AgentTurn(tool_calls=[ParsedToolCall(f"c{n}", "get_user_activity", {"days": 7})])
        if (
            pack["destination"]["class"] != "approved_corporate"
            and "check_dlp_exception" not in done
        ):
            return AgentTurn(tool_calls=[ParsedToolCall(f"c{n}", "check_dlp_exception", {})])
        pol = pack["policy"]
        if pol["conflict"] or pack["classification"]["level"] is None:
            outcome = "HUMAN_REVIEW"
        elif pol["effect"] == "prohibited":
            outcome = "ESCALATE"
        elif (
            pack["destination"]["class"] == "approved_corporate"
            or pack["classification"]["level"] == "PUBLIC"
        ):
            outcome = "ALLOW"
        else:
            outcome = "WARN"
        findings = [{"text": c["text"], "evidence_id": c["evidence_id"]} for c in pol["claims"][:2]]
        return AgentTurn(
            final_text=json.dumps(
                {"proposed_outcome": outcome, "findings": findings, "missing_evidence": []}
            )
        )


def parse_final(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    body = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", body, re.DOTALL)
    if fenced:
        body = fenced.group(1)
    try:
        obj = json.loads(body)
    except ValueError:
        return None
    if not isinstance(obj, dict) or obj.get("proposed_outcome") not in OUTCOMES:
        return None
    if not isinstance(obj.get("findings", []), list):
        return None
    return obj


class DlpTools:
    """The 5 tools, bound to one event. Results carry an `_tool` key so planners can see which tool
    answered; evidence ids are assigned here."""

    def __init__(
        self,
        *,
        policy_tools,
        activity,
        exceptions,
        event,
        level_rank: int,
        ranks: dict,
        cfg: AgentConfig,
        faults: list[str],
    ) -> None:
        self.policy_tools = policy_tools
        self.activity = activity
        self.exceptions = exceptions
        self.event = event
        self.level_rank = level_rank
        self.ranks = ranks
        self.cfg = cfg
        self.faults = faults
        self.evidence: set[str] = set()
        self.verified_exception: dict[str, Any] | None = None
        self.review_reasons: list[str] = []

    def call(self, name: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any], str | None]:
        if name not in self.cfg.allowed_tools or name not in TOOL_NAMES:
            return False, {"_tool": "unlisted", "error": "unknown_tool"}, "unknown_tool"
        return getattr(self, f"_{name}")(args)

    def _policy(self, name: str, args: dict[str, Any]):
        ok, result, err = self.policy_tools.call(name, args)
        for r in result.get("results", []):
            self.evidence.add(r["evidence_id"])
        return ok, {"_tool": name, **result}, err

    def _search_policy(self, args):
        return self._policy("search_policy", args)

    def _get_policy_section(self, args):
        return self._policy("get_policy_section", args)

    def _get_user_activity(self, args):
        days = args.get("days")
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 30:
            return (
                False,
                {"_tool": "get_user_activity", "error": "invalid_arguments"},
                "invalid_arguments",
            )
        if "activity_tool_error" in self.faults:
            return (
                False,
                {"_tool": "get_user_activity", "error": "tool_unavailable"},
                "tool_unavailable",
            )
        try:
            row = self.activity.get_user_activity(self.event.user_id)
        except ContextError as exc:
            return False, {"_tool": "get_user_activity", "error": exc.kind}, exc.kind
        if "malformed_activity_result" in self.faults:
            row = {"recent_external_upload_count_7d": "many", "note": "<<corrupted>>"}
        bad = [k for k, v in row.items() if not isinstance(v, int | float) or isinstance(v, bool)]
        if bad:  # typed output validation: a malformed record is rejected, never passed on
            return (
                False,
                {"_tool": "get_user_activity", "error": "malformed_tool_result"},
                "malformed_tool_result",
            )
        self.evidence.add("ACTIVITY")
        return (
            True,
            {"_tool": "get_user_activity", "evidence_id": "ACTIVITY", "window_days": 7, **row},
            None,
        )

    def _check_dlp_exception(self, args):
        if args:
            return (
                False,
                {"_tool": "check_dlp_exception", "error": "invalid_arguments"},
                "invalid_arguments",
            )
        res = self.exceptions.check(
            self.event.user_id,
            self.event.destination.host,
            self.level_rank,
            self.ranks,
            self.event.timestamp,
        )
        if res["match"]:
            self.verified_exception = res["exception"]
            self.evidence.add("EXCEPTION")
            return True, {"_tool": "check_dlp_exception", "evidence_id": "EXCEPTION", **res}, None
        return True, {"_tool": "check_dlp_exception", **res}, None

    def _request_human_review(self, args):
        reason = args.get("reason")
        if reason not in REVIEW_REASONS:
            return (
                False,
                {"_tool": "request_human_review", "error": "invalid_arguments"},
                "invalid_arguments",
            )
        self.review_reasons.append(reason)
        return (
            True,
            {"_tool": "request_human_review", "status": "review_requested", "reason": reason},
            None,
        )


class DlpAgent:
    def __init__(self, planner, cfg: AgentConfig, system_prompt: str, *, backend: str) -> None:
        self.planner = planner
        self.cfg = cfg
        self.system = system_prompt
        self.backend = backend

    def run(self, pack: dict[str, Any], tools: DlpTools) -> AgentResult:
        tools.evidence |= {c["evidence_id"] for c in pack["policy"]["claims"]}
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": json.dumps(pack, sort_keys=True, ensure_ascii=False)},
        ]
        steps: list[dict[str, Any]] = []
        n_calls = failures = turns = tokens_in = tokens_out = 0
        stopped, final = "final_answer", None
        t0 = time.perf_counter()
        planner_kind = getattr(self.planner, "name", "unknown")
        with span("uc1.agent", dg__agent__name=self.cfg.name, dg__agent__planner=planner_kind) as s:
            while True:
                if turns >= self.cfg.max_turns:
                    stopped = "step_budget_exceeded"
                    break
                turns += 1
                with span("uc1.agent.planner", dg__agent__step=turns) as ps:
                    try:
                        turn = self.planner.next_turn(messages)
                    except AgentError as err:
                        ps.set(dg__llm__error_kind=err.kind)
                        stopped = f"planner_error:{err.kind}"
                        break
                    attrs = {"dg__agent__n_tool_calls": len(turn.tool_calls)}
                    for key, value in (
                        ("dg__llm__model_id", turn.model_id),
                        ("dg__tokens_in", turn.tokens_in),
                        ("dg__tokens_out", turn.tokens_out),
                    ):
                        if value is not None:
                            attrs[key] = value
                    replayed = getattr(self.planner, "last_cached", None)
                    if replayed is not None:
                        attrs["dg__llm__cached"] = bool(replayed)
                    ps.set(**attrs)
                tokens_in += turn.tokens_in or 0
                tokens_out += turn.tokens_out or 0
                if not turn.tool_calls:
                    final = turn.final_text
                    break
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": c.id,
                                "type": "function",
                                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                            }
                            for c in turn.tool_calls
                        ],
                    }
                )
                for c in turn.tool_calls:
                    n_calls += 1
                    if n_calls > self.cfg.max_tool_calls:
                        stopped = "step_budget_exceeded"
                        break
                    label = c.name if c.name in TOOL_NAMES else "unlisted"
                    with span("uc1.tool", dg__agent__step=n_calls, dg__agent__tool=label) as ts:
                        ok, result, err = tools.call(c.name, c.arguments)
                        ts.set(dg__agent__tool_ok=ok)
                        if err:
                            ts.set(dg__agent__tool_error=err)
                    failures = 0 if ok else failures + 1
                    shown = {
                        k: (v[:200] if isinstance(v, str) else v) for k, v in c.arguments.items()
                    }
                    steps.append(
                        {
                            "step": n_calls,
                            "tool": label,
                            "arguments": shown if label != "unlisted" else {},
                            "ok": ok,
                            "error": err,
                            "evidence_ids": [r["evidence_id"] for r in result.get("results", [])]
                            + ([result["evidence_id"]] if "evidence_id" in result else []),
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": c.id,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                    if failures >= self.cfg.max_consecutive_tool_failures:
                        stopped = "tool_failure"
                        break
                if stopped != "final_answer":
                    break
            s.set(
                dg__agent__tool_calls=min(n_calls, self.cfg.max_tool_calls),
                dg__agent__stopped_reason=stopped.split(":")[0],
            )
        trace = {
            "name": self.cfg.name,
            "backend": self.backend,
            "planner": getattr(self.planner, "model_id", type(self.planner).__name__),
            "turns": turns,
            "tool_calls": min(n_calls, self.cfg.max_tool_calls),
            "max_tool_calls": self.cfg.max_tool_calls,
            "steps": steps,
            "tokens_in": tokens_in or None,
            "tokens_out": tokens_out or None,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }
        out = parse_final(final) if stopped == "final_answer" else None
        if stopped == "final_answer" and out is None:
            stopped = "invalid_final_answer"
        findings = []
        for f in (out or {}).get("findings", [])[:8]:
            if isinstance(f, dict) and isinstance(f.get("text"), str):
                eid = str(f.get("evidence_id", ""))
                findings.append(
                    {"text": f["text"][:300], "evidence_id": eid, "verified": eid in tools.evidence}
                )
        missing = [
            str(x)[:120] for x in (out or {}).get("missing_evidence", [])[:5] if isinstance(x, str)
        ]
        return AgentResult(
            proposed_outcome=out["proposed_outcome"] if out else None,
            findings=findings,
            missing_evidence=missing,
            verified_exception=tools.verified_exception,
            review_requested=tools.review_reasons,
            stopped_reason=stopped,
            trace=trace,
        )
