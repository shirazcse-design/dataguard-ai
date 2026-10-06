"""UC2's use of the shared bounded agent loop (app/agent/bounded.py): UC2's guilt / intent /
employment filter, its placeholder and its span names are the defaults here, so every UC2 agent
behaves exactly as before the loop was shared (2026-10-06)."""

from __future__ import annotations

import re
from typing import Any

from app.agent import bounded as _b
from app.agent.bounded import AgentRun, Tool, params, parse_json

__all__ = [
    "BANNED",
    "AgentRun",
    "BoundedAgent",
    "Tool",
    "params",
    "parse_json",
    "unsupported",
    "_scrub",
]

BANNED = re.compile(
    r"\b(malicious|maliciously|guilty|guilt|criminal|crime|thief|theft|steal|stealing|stole|"
    r"terminat(e|ed|ion)|fire[ds]?|firing|disciplin\w*|dismiss(al)?|sack(ed)?|traitor|"
    r"insider threat actor|bad actor|wrongdoing|fraudster)\b",
    re.IGNORECASE,
)
WITHHELD = "[withheld: unsupported conclusion]"


def unsupported(text: str) -> bool:
    return bool(BANNED.search(text or ""))


def _scrub(obj: Any, max_chars: int) -> tuple[Any, int]:
    """Cut free text to `max_chars` and withhold guilt / intent / employment wording."""
    return _b.scrub(obj, max_chars, unsupported, WITHHELD)


class BoundedAgent(_b.BoundedAgent):
    def __init__(self, *args: Any, span_name: str = "uc2.agent", **kw: Any) -> None:
        kw.setdefault("planner_span", "uc2.agent.planner")
        kw.setdefault("tool_span", "uc2.tool")
        kw.setdefault("output_filter", unsupported)
        kw.setdefault("withheld", WITHHELD)
        super().__init__(*args, span_name=span_name, **kw)
