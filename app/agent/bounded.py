"""The bounded agent loop, shared by UC2 (four agents and two baselines) and UC3 (one agent).

Enforced HERE, in code, not by prompt (the same harness patterns as UC1 and UC4):
* tool allow-list: a name not in this agent's tool set -> `unknown_tool` (a failed step);
* typed arguments: each tool validates its own arguments -> `invalid_arguments`;
* budgets: `max_tool_calls`, `max_turns`, `max_consecutive_tool_failures`;
* structured output: the final answer must parse into this agent's schema (one repair attempt);
* an optional OUTPUT FILTER: free text it matches is withheld and counted (UC2 withholds guilt /
  intent / employment wording; UC3 withholds claims of having changed access);
* evidence ids are assigned by tools, never invented: a cited id that no tool returned is unknown.

Tool-call arguments are written into the conversation in CANONICAL form (sorted keys): the replay
cache stores them sorted, so a live model's key order must not change the replay key (found when a
multi-argument tool call made a live UC2 run unreplayable, 2026-10-04).

Each agent gets a FRESH conversation: a system prompt plus ONE typed payload.

Moved here from app/insider/agents.py (2026-10-06, UC3) without behaviour change: UC2 passes its
filter, placeholder and span names explicitly, so its spans and replay keys are byte-identical.
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
    budget_finish: bool = False  # the tool budget was reached and the agent was asked to answer
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


def scrub(obj: Any, max_chars: int, output_filter: Callable[[str], bool] | None = None,
          withheld: str = "[withheld]") -> tuple[Any, int]:  # fmt: skip
    """Cut free text to `max_chars`; replace any string the filter matches with `withheld`."""
    hits = 0
    if isinstance(obj, str):
        if output_filter is not None and output_filter(obj):
            return withheld, 1
        return obj[:max_chars], 0
    if isinstance(obj, list):
        out = []
        for x in obj:
            y, h = scrub(x, max_chars, output_filter, withheld)
            out.append(y)
            hits += h
        return out, hits
    if isinstance(obj, dict):
        out_d = {}
        for k, v in obj.items():
            y, h = scrub(v, max_chars, output_filter, withheld)
            out_d[k] = y
            hits += h
        return out_d, hits
    return obj, 0


BUDGET_REACHED = ("Tool budget reached: no more tool calls are possible. Reply now with ONLY the required JSON "
                  "object, using the evidence you already have; list anything you could not check under missing_evidence.")  # fmt: skip


class BoundedAgent:
    def __init__(self, name: str, planner: Any, system: str, tools: list[Tool], *, max_turns: int,
                 max_tool_calls: int, max_failures: int, output_model: type[BaseModel] | None,
                 backend: str, max_chars: int = 400, span_name: str = "agent",
                 planner_span: str = "agent.planner", tool_span: str = "agent.tool",
                 output_filter: Callable[[str], bool] | None = None,
                 withheld: str = "[withheld]", finish_on_budget: bool = False) -> None:  # fmt: skip
        self.name, self.planner, self.system = name, planner, system
        self.tools = {t.name: t for t in tools}
        self.max_turns, self.max_tool_calls, self.max_failures = (
            max_turns,
            max_tool_calls,
            max_failures,
        )
        self.output_model, self.backend, self.max_chars = output_model, backend, max_chars
        self.span_name, self.planner_span, self.tool_span = span_name, planner_span, tool_span
        self.output_filter, self.withheld = output_filter, withheld
        # Opt-in (UC3): when the tool budget is reached, answer the unexecuted calls of that batch with
        # "not executed" and give the agent ONE more turn, without tools, to answer from the evidence
        # it has. Off by default, so UC2's agents behave exactly as before.
        self.finish_on_budget = finish_on_budget

    def run(self, payload: dict[str, Any]) -> AgentRun:
        planner_name = getattr(self.planner, "model_id", type(self.planner).__name__)
        run = AgentRun(self.name, self.backend, planner_name)
        msgs: list[dict[str, Any]] = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": json.dumps(payload, sort_keys=True, ensure_ascii=False)},
        ]
        failures = 0
        finishing = False
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
                    self.planner_span, dg__agent__name=self.name, dg__agent__step=run.turns
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
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments, sort_keys=True)}}
                    for c in turn.tool_calls]})  # fmt: skip
                if finishing:  # it was told to answer and asked for tools again
                    run.stopped_reason = "tool_budget_exceeded"
                    break
                stop = False
                for i, c in enumerate(turn.tool_calls):
                    run.tool_calls += 1
                    if run.tool_calls > self.max_tool_calls:
                        if self.finish_on_budget:
                            run.tool_calls -= 1  # count only what ran
                            for p in turn.tool_calls[i:]:
                                msgs.append({"role": "tool", "tool_call_id": p.id,
                                             "content": json.dumps({"error": "not_executed", "detail": "tool budget reached"})})  # fmt: skip
                            msgs.append({"role": "user", "content": BUDGET_REACHED})
                            run.budget_finish = True
                            finishing = True
                            break
                        run.stopped_reason = "tool_budget_exceeded"
                        stop = True
                        break
                    tool = self.tools.get(c.name)
                    label = c.name if tool else "unlisted"
                    with span(
                        self.tool_span,
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
            obj, hits = scrub(obj, self.max_chars, self.output_filter, self.withheld)
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
