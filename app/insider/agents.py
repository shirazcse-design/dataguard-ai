"""The bounded agent loop shared by all UC2 agents (and the 1-/2-agent baselines).

Enforced HERE, in code, not by prompt (the same harness patterns as UC1 and UC4):
* tool allow-list: a name not in this agent's tool set -> `unknown_tool` (a failed step);
* typed arguments: each tool validates its own arguments -> `invalid_arguments`;
* budgets: `max_tool_calls`, `max_turns`, `max_consecutive_tool_failures`;
* structured output: the final answer must parse into this agent's schema (one repair attempt);
* unsupported conclusions: guilt / intent / employment wording in any free-text field is withheld
  and reported (`unsupported_conclusion`), never shown as the agent's finding;
* evidence ids are assigned by tools, never invented: a cited id that no tool returned is unknown.

Each agent gets a FRESH conversation: a system prompt plus ONE typed payload. No agent sees another
agent's conversation, and raw logs exist only in the tools of the agent that needs them.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agent.types import AgentError
from observability import span

BANNED = re.compile(
    r"\b(malicious|maliciously|guilty|guilt|criminal|crime|thief|theft|steal|stealing|stole|"
    r"terminat(e|ed|ion)|fire[ds]?|firing|disciplin\w*|dismiss(al)?|sack(ed)?|traitor|"
    r"insider threat actor|bad actor|wrongdoing|fraudster)\b",
    re.IGNORECASE,
)


def unsupported(text: str) -> bool:
    return bool(BANNED.search(text or ""))


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema, additionalProperties false
    fn: Callable[[dict[str, Any]], tuple[bool, dict[str, Any], str | None]]
    terminal: bool = False  # a successful call ends this agent's loop

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}  # fmt: skip


def params(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required or [],
        "additionalProperties": False,
    }


@dataclass
class AgentRun:
    name: str
    backend: str
    planner: str
    stopped_reason: str = "not_run"
    output: BaseModel | None = None
    terminal_result: dict[str, Any] | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    model_calls: int = 0
    ms: float = 0.0
    unsupported_conclusions: int = 0
    repairs: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)  # in memory only (isolation tests)

    @property
    def ok(self) -> bool:
        return self.output is not None or self.stopped_reason.startswith("terminal:")

    def view(self) -> dict[str, Any]:
        return {"name": self.name, "backend": self.backend, "planner": self.planner,
                "stopped_reason": self.stopped_reason, "turns": self.turns, "tool_calls": self.tool_calls,
                "model_calls": self.model_calls, "tokens_in": self.tokens_in or None,
                "tokens_out": self.tokens_out or None, "ms": round(self.ms, 1), "steps": self.steps,
                "unsupported_conclusions": self.unsupported_conclusions, "repairs": self.repairs,
                "output": self.output.model_dump() if self.output is not None else None}  # fmt: skip


def parse_json(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    body = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", body, re.DOTALL)
    if m:
        body = m.group(1)
    try:
        obj = json.loads(body)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _scrub(obj: Any, max_chars: int) -> tuple[Any, int]:
    """Cut free text to `max_chars` and withhold guilt / intent / employment wording."""
    hits = 0
    if isinstance(obj, str):
        if unsupported(obj):
            return "[withheld: unsupported conclusion]", 1
        return obj[:max_chars], 0
    if isinstance(obj, list):
        out = []
        for x in obj:
            y, h = _scrub(x, max_chars)
            out.append(y)
            hits += h
        return out, hits
    if isinstance(obj, dict):
        out_d = {}
        for k, v in obj.items():
            y, h = _scrub(v, max_chars)
            out_d[k] = y
            hits += h
        return out_d, hits
    return obj, 0


class BoundedAgent:
    def __init__(self, name: str, planner: Any, system: str, tools: list[Tool], *, max_turns: int,
                 max_tool_calls: int, max_failures: int, output_model: type[BaseModel] | None,
                 backend: str, max_chars: int = 400, span_name: str = "uc2.agent") -> None:  # fmt: skip
        self.name, self.planner, self.system = name, planner, system
        self.tools = {t.name: t for t in tools}
        self.max_turns, self.max_tool_calls, self.max_failures = (
            max_turns,
            max_tool_calls,
            max_failures,
        )
        self.output_model, self.backend, self.max_chars = output_model, backend, max_chars
        self.span_name = span_name

    def run(self, payload: dict[str, Any]) -> AgentRun:
        planner_name = getattr(self.planner, "model_id", type(self.planner).__name__)
        run = AgentRun(self.name, self.backend, planner_name)
        msgs: list[dict[str, Any]] = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": json.dumps(payload, sort_keys=True, ensure_ascii=False)},
        ]
        failures = 0
        t0 = time.perf_counter()
        with span(
            self.span_name,
            dg__agent__name=self.name,
            dg__agent__planner=getattr(self.planner, "name", "unknown"),
        ) as s:
            while True:
                if run.turns >= self.max_turns:
                    run.stopped_reason = "turn_budget_exceeded"
                    break
                run.turns += 1
                with span(
                    "uc2.agent.planner", dg__agent__name=self.name, dg__agent__step=run.turns
                ) as ps:
                    try:
                        turn = self.planner.next_turn(msgs)
                    except AgentError as err:
                        ps.set(dg__llm__error_kind=err.kind)
                        run.stopped_reason = f"planner_error:{err.kind}"
                        break
                    attrs: dict[str, Any] = {"dg__agent__n_tool_calls": len(turn.tool_calls)}
                    for key, value in (("dg__llm__model_id", turn.model_id), ("dg__tokens_in", turn.tokens_in),
                                       ("dg__tokens_out", turn.tokens_out)):  # fmt: skip
                        if value is not None:
                            attrs[key] = value
                    cached = getattr(self.planner, "last_cached", None)
                    if cached is not None:
                        attrs["dg__llm__cached"] = bool(cached)
                    ps.set(**attrs)
                if turn.model_id:
                    run.model_calls += 1
                run.tokens_in += turn.tokens_in or 0
                run.tokens_out += turn.tokens_out or 0
                if not turn.tool_calls:
                    if self._finish(run, turn.final_text, msgs):
                        break
                    continue
                msgs.append({"role": "assistant", "content": None, "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                    for c in turn.tool_calls]})  # fmt: skip
                stop = False
                for c in turn.tool_calls:
                    run.tool_calls += 1
                    if run.tool_calls > self.max_tool_calls:
                        run.stopped_reason = "tool_budget_exceeded"
                        stop = True
                        break
                    tool = self.tools.get(c.name)
                    label = c.name if tool else "unlisted"
                    with span(
                        "uc2.tool",
                        dg__agent__name=self.name,
                        dg__agent__step=run.tool_calls,
                        dg__agent__tool=label,
                    ) as ts:
                        if tool is None:
                            ok, result, err = False, {"error": "unknown_tool"}, "unknown_tool"
                        else:
                            ok, result, err = tool.fn(
                                c.arguments if isinstance(c.arguments, dict) else {}
                            )
                        ts.set(dg__agent__tool_ok=ok)
                        if err:
                            ts.set(dg__agent__tool_error=err)
                    failures = 0 if ok else failures + 1
                    run.steps.append({
                        "step": run.tool_calls, "tool": label, "ok": ok, "error": err,
                        "arguments": {k: (v[:120] if isinstance(v, str) else v) for k, v in (c.arguments or {}).items()}
                        if tool else {},
                        "evidence_ids": result.get("evidence_ids", []) if isinstance(result, dict) else [],
                    })  # fmt: skip
                    msgs.append(
                        {
                            "role": "tool",
                            "tool_call_id": c.id,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                    if ok and tool is not None and tool.terminal:
                        run.stopped_reason = f"terminal:{c.name}"
                        run.terminal_result = result
                        stop = True
                        break
                    if failures >= self.max_failures:
                        run.stopped_reason = "tool_failure"
                        stop = True
                        break
                if stop:
                    break
            s.set(dg__agent__tool_calls=min(run.tool_calls, self.max_tool_calls),
                  dg__agent__stopped_reason=run.stopped_reason.split(":")[0])  # fmt: skip
        run.ms = (time.perf_counter() - t0) * 1000
        run.messages = msgs
        return run

    def _finish(self, run: AgentRun, text: str | None, msgs: list[dict[str, Any]]) -> bool:
        """True = the loop ends (valid answer, or no repair left)."""
        obj = parse_json(text)
        error = None
        if obj is None or self.output_model is None:
            error = "not a JSON object" if obj is None else None
            if self.output_model is None and obj is not None:
                run.stopped_reason = "final_answer"
                return True
        else:
            obj, hits = _scrub(obj, self.max_chars)
            run.unsupported_conclusions += hits
            try:
                run.output = self.output_model.model_validate(obj)
                run.stopped_reason = "final_answer"
                return True
            except ValidationError as exc:
                error = "; ".join(
                    sorted({".".join(map(str, e["loc"])) or "root" for e in exc.errors()})
                )[:300]
        if run.repairs >= 1:
            run.stopped_reason = "invalid_final_answer"
            return True
        run.repairs += 1
        msgs.append({"role": "assistant", "content": text or ""})
        msgs.append(
            {
                "role": "user",
                "content": f"Your final answer did not match the required JSON schema ({error}). Reply with ONLY the corrected JSON object.",
            }
        )
        return False
