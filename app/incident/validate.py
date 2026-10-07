"""Report validation: every material claim must resolve to evidence the agent actually retrieved.

  OBSERVED_FACT / DETERMINISTIC_FINDING / POLICY_REQUIREMENT -> at least one cited id, all ids
      returned by a tool, and at least one cited item of that same claim type (a POLICY_REQUIREMENT
      must cite a verified UC6 policy item). A factual claim that cites only other kinds of evidence
      is RELABELLED as an AGENT_INFERENCE (it is the agent's reading, not an observed fact).
  AGENT_INFERENCE -> at least one cited id, all returned by a tool.
  UNKNOWN_OR_GAP  -> ids optional; it states what is NOT established.

Claims failing these rules are `unsupported` and excluded from the analyst's report. Claims whose
text asserts intent or guilt, or claims an action was taken, are withheld by the output filter
before they reach here (`withheld`). Both send the case to an analyst.
"""

from __future__ import annotations

import re
from typing import Any

from .schemas import IncidentReport
from .services import Ledger

WITHHELD = "[withheld: an intent, guilt or action claim the evidence cannot support]"
INTENT = re.compile(
    r"\b(malicious(ly)?|stole|stolen|steal(s|ing)?|theft|thief|guilty|intentional(ly)?|deliberate(ly)?|"
    r"insider threat|bad actor|wrongdoing|exfiltrated|committed (a|the) (breach|crime|offen[cs]e)|"
    r"(should|must) be (fired|terminated|dismissed|disciplined))\b",
    re.IGNORECASE,
)
ACTION = re.compile(
    r"\b(i|we)\s+(have\s+)?(disabled|revoked|deleted|quarantined|blocked|suspended|notified|terminated|locked|removed)\b"
    r"|\baccount\s+(has\s+been|was|is\s+now)\s+(disabled|suspended|locked)\b"
    r"|\b(access|permissions?)\s+(has|have)\s+been\s+(revoked|removed)\b"
    r"|\b(evidence|alert|files?)\s+(has|have)\s+been\s+deleted\b",
    re.IGNORECASE,
)


def output_filter(text: str) -> bool:
    """True = withhold this string (an unsupported intent/guilt conclusion, or an action claim)."""
    return bool(INTENT.search(text or "")) or bool(ACTION.search(text or ""))


def validate(report: IncidentReport | None, led: Ledger) -> dict[str, Any]:
    if report is None:
        return {"claims": [], "total": 0, "supported": 0, "unsupported": 0, "withheld": 0, "relabelled": 0,
                "unknown_ids": 0, "by_type": {}, "unsupported_claim_rate": None}  # fmt: skip
    out, unknown = [], 0
    for c in report.claims:
        ids = list(dict.fromkeys(c.evidence_ids))
        bad = [i for i in ids if i not in led.returned]
        unknown += len(bad)
        cited = [led.items[i] for i in ids if i in led.returned]
        status, claim_type = "supported", c.claim_type
        if c.text == WITHHELD:
            status = "withheld"
        elif bad:
            status = "unsupported"
        elif c.claim_type in ("OBSERVED_FACT", "DETERMINISTIC_FINDING", "POLICY_REQUIREMENT"):
            if (
                not cited
                or c.claim_type == "POLICY_REQUIREMENT"
                and not any(
                    i.source == "uc6" and i.claim_type == "POLICY_REQUIREMENT" for i in cited
                )
            ):
                status = "unsupported"
            elif not any(i.claim_type == c.claim_type for i in cited):
                status, claim_type = "relabelled", "AGENT_INFERENCE"
        elif c.claim_type == "AGENT_INFERENCE" and not cited:
            status = "unsupported"
        out.append({"topic": c.topic, "claim_type": claim_type, "declared_claim_type": c.claim_type, "text": c.text,
                    "evidence_ids": ids, "status": status})  # fmt: skip
    n = len(out)
    count = lambda s: sum(1 for x in out if x["status"] == s)  # noqa: E731
    by_type: dict[str, int] = {}
    for x in out:
        if x["status"] in ("supported", "relabelled"):
            by_type[x["claim_type"]] = by_type.get(x["claim_type"], 0) + 1
    top_bad = [i for i in report.evidence_ids if i not in led.returned]
    return {"claims": out, "total": n, "supported": count("supported"), "unsupported": count("unsupported"),
            "withheld": count("withheld"), "relabelled": count("relabelled"), "unknown_ids": unknown + len(top_bad),
            "by_type": by_type, "unsupported_claim_rate": round(count("unsupported") / n, 3) if n else None}  # fmt: skip
