"""Agentic RAG: a small, bounded tool-using agent (`dataguard-policy-copilot`).

    question -> input guard -> [planner turn -> allow-listed tool calls]* -> final JSON
             -> the SAME citation verification / conflict / outcome code as Advanced RAG

The agent decides WHETHER and WHAT to retrieve (it can refine a search, read an exact section, check
policy versions or ask for human review). It does not decide what counts as an answer. Structural
controls, enforced here rather than asked for in the prompt:

* **Tool allow-list.** Only the four configured tools run; any other name is rejected with
  `unknown_tool` and counts as a failed step. There are no write tools. The agent cannot modify
  policies or evidence.
* **Arguments validated.** Wrong types or lengths are rejected with `invalid_arguments`, and a
  review reason must come from a fixed vocabulary.
* **Step budget.** At most `max_tool_calls` tool calls and `max_turns` planner turns. Exceeding
  either stops the run with no answer and a `step_budget_exceeded` review.
* **Repeated tool failure.** `max_consecutive_tool_failures` failures in a row stop the run with a
  `tool_failure` review.
* **Evidence comes only from tools.** Evidence ids (E1, E2, ...) are assigned by the harness to text
  the tools returned. A final claim citing anything else is a fabricated citation and is dropped by
  `PolicyCopilot.decide`, and a final answer with no verified claim is INSUFFICIENT_EVIDENCE.
* **Retrieved text is scanned.** Evidence carrying instruction-override text is withheld from the
  agent (it sees only that an item was excluded) and raises a guardrail event.
* **The agent can add review, never remove it.** `request_human_review` adds a review reason; a
  conflict or failed verification adds one whether or not the agent asked.

Planners (all implement UC4's `AgentLLMClient.next_turn`):
* `ReplayAgentClient`: recorded turns keyed by a hash of the whole conversation; with `inner` set it
  records misses from a live planner.
* UC4's `FoundryAgentClient`: chat-completions tool calling on `uc4-llm-medium` (live).
* `OfflinePlanner`: deterministic, labelled `offline-planner`. It searches once and quotes the top
  evidence. It exists to exercise the loop with no model and no recordings.

Tracing: `uc6.agent` per run, `uc6.agent.planner` per planner turn and `uc6.tool` per tool call.
Attributes are step numbers, allow-listed tool names, ok/error kinds, counts and token usage.
Never queries, arguments, tool results or planner text.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agent.types import AgentError, AgentLLMClient, AgentTurn, ParsedToolCall
from observability import span

from .config import AgentConfig, LevelConfig
from .generate import ModelOutput
from .schemas import Evidence, PolicyAnswer, Stage

REVIEW_REASONS = ("insufficient_evidence", "policy_conflict", "ambiguous_question", "other")
_POLICY_ID = re.compile(r"^POL-[A-Z0-9-]{1,20}$")
_SECTION = re.compile(r"^\d{1,2}(?:\.\d{1,2}){0,2}$")
CACHE_SCHEMA = 1


def tool_schemas() -> list[dict[str, Any]]:
    """OpenAI/Azure-compatible function definitions (also in docs/uc6/foundry-agent-setup.md)."""

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
            "Search the approved data-security policy corpus. Returns up to 5 policy sections, "
            "each with an evidence id, citation, status and text.",
            {"query": {"type": "string", "description": "What to look for, in plain words."}},
            ["query"],
        ),
        fn(
            "get_policy_section",
            "Read one exact policy section by policy id and section number (the current version).",
            {
                "policy_id": {"type": "string", "description": "e.g. POL-IR"},
                "section": {"type": "string", "description": "e.g. 2 or 4.2"},
            },
            ["policy_id", "section"],
        ),
        fn(
            "lookup_policy_metadata",
            "List the versions of a policy with status (current/superseded/draft), effective date, "
            "owner and which version each supersedes. Returns no policy text.",
            {"policy_id": {"type": "string", "description": "e.g. POL-RET"}},
            ["policy_id"],
        ),
        fn(
            "request_human_review",
            "Ask a human policy owner to review this question. Use when evidence is insufficient "
            "or current policies conflict for the question.",
            {"reason": {"type": "string", "enum": list(REVIEW_REASONS)}},
            ["reason"],
        ),
    ]


TOOL_NAMES = tuple(t["function"]["name"] for t in tool_schemas())


# -- planners --------------------------------------------------------------------------------------
class ReplayAgentClient:
    """Record/replay for planner turns. The key covers the model id, prompt version, tool
    definitions and the full message list, so any change to evidence or prompt is a miss (an
    `AgentError("replay_miss")`, never a substitute turn)."""

    name = "replay"

    def __init__(
        self,
        cache_dir: Path | str,
        model_id: str,
        prompt_version: str,
        tools: list[dict[str, Any]],
        *,
        inner: AgentLLMClient | None = None,
    ) -> None:
        self.dir = Path(cache_dir) / model_id / prompt_version
        self.model_id = model_id
        self.prompt_version = prompt_version
        self.tools = tools
        self.inner = inner
        self.last_cached: bool | None = None

    def _key(self, messages: list[dict[str, Any]]) -> str:
        blob = json.dumps(
            {
                "model": self.model_id,
                "prompt": self.prompt_version,
                "tools": self.tools,
                "messages": messages,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def next_turn(self, messages: list[dict[str, Any]]) -> AgentTurn:
        key = self._key(messages)
        path = self.dir / f"{key}.json"
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            if d.get("schema") != CACHE_SCHEMA or d.get("key") != key:
                raise AgentError("replay_miss", "cache entry does not match its key")
            self.last_cached = True
            return AgentTurn(
                tool_calls=[ParsedToolCall(**c) for c in d["tool_calls"]],
                final_text=d["final_text"],
                model_id=self.model_id,
                served_model=d.get("served_model"),
                tokens_in=d.get("tokens_in"),
                tokens_out=d.get("tokens_out"),
            )
        if self.inner is None:
            raise AgentError("replay_miss", "no recorded planner turn for this conversation")
        turn = self.inner.next_turn(messages)
        self.last_cached = False
        self.dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": CACHE_SCHEMA,
                    "key": key,
                    "tool_calls": [c.__dict__ for c in turn.tool_calls],
                    "final_text": turn.final_text,
                    "served_model": turn.served_model,
                    "tokens_in": turn.tokens_in,
                    "tokens_out": turn.tokens_out,
                    "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                },
                indent=1,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return turn


class OfflinePlanner:
    """Deterministic stand-in (no model): search once with the question, then quote the first
    sentence of the top two results. Labelled everywhere as `offline-planner`."""

    name = "offline"
    model_id = "offline-planner"
    last_cached: bool | None = None

    def next_turn(self, messages: list[dict[str, Any]]) -> AgentTurn:
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if not tool_msgs:
            question = next(m["content"] for m in messages if m["role"] == "user")
            return AgentTurn(
                tool_calls=[ParsedToolCall("call_1", "search_policy", {"query": question})]
            )
        results = json.loads(tool_msgs[-1]["content"]).get("results", [])
        claims = []
        for r in [r for r in results if r.get("text")][:2]:
            sentence = re.split(r"(?<=[.!?])\s+", r["text"].strip())[0]
            claims.append({"text": sentence, "evidence_id": r["evidence_id"], "quote": sentence})
        final = {
            "status": "ANSWERED" if claims else "INSUFFICIENT_EVIDENCE",
            "claims": claims,
            "conflict_evidence_ids": [],
            "conflict_note": "",
        }
        return AgentTurn(final_text=json.dumps(final))


# -- tools -----------------------------------------------------------------------------------------
class PolicyTools:
    """The four tools, bound to one run. Evidence ids are assigned here, in order of first
    appearance, and the evidence map is the only source the final answer may cite."""

    def __init__(self, copilot: Any, level: LevelConfig, run: Any, cfg: AgentConfig) -> None:
        self.copilot = copilot
        self.level = level
        self.run = run
        self.cfg = cfg
        self.evidence: dict[str, Evidence] = {}  # chunk_id -> Evidence (in order)
        self.review_reasons: list[str] = []
        self.searches: list[dict[str, Any]] = []

    def _label(self, chunk_id: str, rank: int, score: float) -> Evidence:
        if chunk_id not in self.evidence:
            e = self.copilot.evidence_item(chunk_id, f"E{len(self.evidence) + 1}", rank, score)
            self.copilot.scan_evidence([e], self.run, record=False)
            self.evidence[chunk_id] = e
        return self.evidence[chunk_id]

    @staticmethod
    def _public(e: Evidence) -> dict[str, Any]:
        if e.flagged_injection:
            return {
                "evidence_id": e.evidence_id,
                "citation": e.citation,
                "excluded": "withheld: contains instruction-like text",
            }
        return {
            "evidence_id": e.evidence_id,
            "citation": e.citation,
            "policy": e.title,
            "section": f"{e.section} {e.heading}",
            "version": e.version,
            "status": e.status,
            "effective_date": e.effective_date,
            "text": e.body,
        }

    def call(self, name: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any], str | None]:
        """`(ok, result, error_kind)`."""
        if name not in self.cfg.allowed_tools or name not in TOOL_NAMES:
            return False, {"error": "unknown_tool"}, "unknown_tool"
        try:
            return getattr(self, f"_{name}")(args)
        except _InvalidArgs as exc:
            return False, {"error": "invalid_arguments", "detail": str(exc)}, "invalid_arguments"

    def _search_policy(self, args: dict[str, Any]):
        query = _string(args, "query", 3, 300)
        result = self.copilot.retriever.retrieve(query, self.level, top_k=self.cfg.search_top_k)
        items = [self._label(h.chunk_id, h.rank, h.evidence_score) for h in result.hits]
        self.searches.append(
            {"stages": result.trace.stages, "dense_status": result.trace.dense_status}
        )
        return True, {"results": [self._public(e) for e in items]}, None

    def _get_policy_section(self, args: dict[str, Any]):
        policy_id = _string(args, "policy_id", 5, 30).upper()
        section = _string(args, "section", 1, 10).lstrip("§ ").rstrip(".")
        if not _POLICY_ID.match(policy_id) or not _SECTION.match(section):
            raise _InvalidArgs("policy_id or section is malformed")
        matches = [
            c
            for c in self.copilot.corpus.chunks
            if c.policy_id == policy_id
            and c.section == section
            and c.status in self.copilot.cfg.corpus.eligible_statuses
        ]
        current = [c for c in matches if c.status == "current"] or matches
        if not current:
            return False, {"error": "unknown_section"}, "unknown_section"
        return (
            True,
            {"results": [self._public(self._label(c.chunk_id, 0, 1.0)) for c in current]},
            None,
        )

    def _lookup_policy_metadata(self, args: dict[str, Any]):
        policy_id = _string(args, "policy_id", 5, 30).upper()
        versions = self.copilot.corpus.versions_of(policy_id)
        if not versions:
            return False, {"error": "unknown_policy"}, "unknown_policy"
        return (
            True,
            {
                "versions": [
                    {
                        "version": v.version,
                        "status": v.status,
                        "effective_date": v.effective_date,
                        "supersedes": v.supersedes,
                        "policy_owner": v.policy_owner,
                        "title": v.title,
                    }
                    for v in versions
                ]
            },
            None,
        )

    def _request_human_review(self, args: dict[str, Any]):
        reason = _string(args, "reason", 2, 40)
        if reason not in REVIEW_REASONS:
            raise _InvalidArgs(f"reason must be one of {list(REVIEW_REASONS)}")
        self.review_reasons.append(reason)
        return True, {"status": "review_requested", "reason": reason}, None


class _InvalidArgs(Exception):
    pass


def _string(args: dict[str, Any], key: str, lo: int, hi: int) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not lo <= len(value.strip()) <= hi:
        raise _InvalidArgs(f"{key} must be a string of {lo}-{hi} characters")
    return value.strip()


def parse_final(text: str | None) -> ModelOutput | None:
    if not text:
        return None
    body = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", body, re.DOTALL)
    if fenced:
        body = fenced.group(1)
    try:
        return ModelOutput.model_validate(json.loads(body))
    except (ValueError, ValidationError):
        return None


# -- the loop ------------------------------------------------------------------------------------
class AgentRunner:
    def __init__(
        self,
        copilot: Any,
        planner: AgentLLMClient,
        cfg: AgentConfig,
        system_prompt: str,
        *,
        mode: str,
    ) -> None:
        self.copilot = copilot
        self.planner = planner
        self.cfg = cfg
        self.system = system_prompt
        self.mode = mode  # replay | live | offline (what the planner is configured as)

    def run(self, question: str, level: LevelConfig, run: Any) -> PolicyAnswer:
        tools = PolicyTools(self.copilot, level, run, self.cfg)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": question},
        ]
        steps: list[dict[str, Any]] = []
        n_calls = failures = turns = tokens_in = tokens_out = 0
        cached: list[bool] = []
        stopped, final = "final_answer", None
        t0 = time.perf_counter()
        with span("uc6.agent", dg__agent__name=self.cfg.name) as agent_span:
            while True:
                if turns >= self.cfg.max_turns:
                    stopped = "step_budget_exceeded"
                    break
                turns += 1
                with span("uc6.agent.planner", dg__agent__step=turns) as ps:
                    try:
                        turn = self.planner.next_turn(messages)
                    except AgentError as err:
                        ps.set(dg__llm__error_kind=err.kind)
                        stopped = f"planner_error:{err.kind}"
                        break
                    ps.set(**_turn_attrs(turn))
                if getattr(self.planner, "last_cached", None) is not None:
                    cached.append(bool(self.planner.last_cached))
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
                    with span("uc6.tool", dg__agent__step=n_calls, dg__agent__tool=label) as ts:
                        ok, result, err = tools.call(c.name, c.arguments)
                        ts.set(dg__agent__tool_ok=ok)
                        if err:
                            ts.set(dg__agent__tool_error=err)
                    failures = 0 if ok else failures + 1
                    steps.append(_step_record(n_calls, label, c.arguments, ok, err, result))
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
            agent_span.set(
                dg__agent__tool_calls=min(n_calls, self.cfg.max_tool_calls),
                dg__agent__stopped_reason=stopped.split(":")[0],
            )

        evidence = list(tools.evidence.values())
        used = [e for e in evidence if not e.flagged_injection]
        # Label by what actually happened: every turn replayed -> replay; any live turn -> live.
        mode = ("replay" if all(cached) else "live") if cached else self.mode
        trace = {
            "name": self.cfg.name,
            "planner": getattr(self.planner, "model_id", type(self.planner).__name__),
            "turns": turns,
            "tool_calls": min(n_calls, self.cfg.max_tool_calls),
            "max_tool_calls": self.cfg.max_tool_calls,
            "stopped_reason": stopped,
            "steps": steps,
            "agent_review_reasons": tools.review_reasons,
            "tokens_in": tokens_in or None,
            "tokens_out": tokens_out or None,
        }
        run.stages.append(
            Stage(
                name="agent",
                status="ok" if stopped == "final_answer" else "failed",
                ms=round((time.perf_counter() - t0) * 1000, 3),
                detail={
                    "turns": turns,
                    "tool_calls": trace["tool_calls"],
                    "stopped_reason": stopped,
                    "evidence_items": len(evidence),
                },
            )
        )
        for reason in tools.review_reasons:
            run.review.add("agent_requested" if reason != "policy_conflict" else "policy_conflict")
        top = max((e.score for e in used), default=None)

        if stopped != "final_answer":
            reason = {
                "step_budget_exceeded": "step_budget_exceeded",
                "tool_failure": "tool_failure",
            }
            run.review.add(reason.get(stopped, "generation_unavailable"))  # type: ignore[arg-type]
            status = (
                "UNAVAILABLE" if stopped.startswith("planner_error") else "INSUFFICIENT_EVIDENCE"
            )
            return run.finish(status, mode, evidence=evidence, top_evidence_score=top, agent=trace)
        out = parse_final(final)
        if out is None:
            run.review.add("generation_unavailable")
            trace["stopped_reason"] = "invalid_final_answer"
            return run.finish(
                "UNAVAILABLE", mode, evidence=evidence, top_evidence_score=top, agent=trace
            )
        return self.copilot.decide(out, used, evidence, level, run, mode=mode, top=top, agent=trace)


def _step_record(
    n: int, tool: str, args: dict[str, Any], ok: bool, err: str | None, result: dict[str, Any]
) -> dict[str, Any]:
    """One agent step for the UI's agent trace (NOT exported to telemetry: `args` may echo the
    user's question)."""
    shown = (
        {k: (v[:200] if isinstance(v, str) else v) for k, v in args.items()}
        if tool != "unlisted"
        else {}
    )
    return {
        "step": n,
        "tool": tool,
        "arguments": shown,
        "ok": ok,
        "error": err,
        "evidence_ids": [r["evidence_id"] for r in result.get("results", [])],
        "withheld": sum(1 for r in result.get("results", []) if r.get("excluded")),
    }


def _turn_attrs(turn: AgentTurn) -> dict[str, Any]:
    attrs: dict[str, Any] = {"dg__agent__n_tool_calls": len(turn.tool_calls)}
    for key, value in (
        ("dg__llm__model_id", turn.model_id),
        ("dg__llm__served_model", turn.served_model),
        ("dg__tokens_in", turn.tokens_in),
        ("dg__tokens_out", turn.tokens_out),
    ):
        if value is not None:
            attrs[key] = value
    return attrs
