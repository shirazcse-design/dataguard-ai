"""Responsible AI and Observability content for the demo, read from COMMITTED documents and config.

Like `metrics.py`, nothing here computes or restates a number: tables are parsed from
`docs/uc4/responsible-ai.md`, `docs/uc4/observability-engine.md`,
`docs/uc4/results/observability-baseline.md` and `docs/uc4/completion-report.md`, and a shape change
raises `ArtifactError`. Each guardrail's status is derived from what the documents say today (for
example, the Content Safety second opinion is "fake-server tested" for as long as the completion
report's live-verification item is open), never asserted by the page.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .metrics import COMPLETION_REPORT, REPO, RESPONSIBLE_AI, ArtifactError, _read, _table_after

OBS_ENGINE = "docs/uc4/observability-engine.md"
OBS_BASELINE = "docs/uc4/results/observability-baseline.md"
OBS_CONFIG = "config/observability/observability.v1.yaml"
DECISIONS = "docs/uc4/decisions.md"


def _rows(
    text: str, marker: str, src: str, header: list[str] | None = None
) -> list[dict[str, str]]:
    rows = _table_after(text, marker, src)
    head = rows[0]
    if header is not None and head != header:
        raise ArtifactError(f"{src}: table after {marker!r} changed: {head}")
    return [{k: _clean(v) for k, v in zip(head, r, strict=False)} for r in rows[1:]]


def _clean(cell: str) -> str:
    return re.sub(r"\*\*|`", "", cell).strip()


# ---- Responsible AI ----------------------------------------------------------------------------
def responsible_ai(root: Path = REPO) -> dict[str, Any]:
    text = _read(RESPONSIBLE_AI, root)
    hhh = _rows(text, "### HHH", RESPONSIBLE_AI)
    apf = _rows(text, "### APF", RESPONSIBLE_AI)
    pillars = _rows(text, "## 4. Responsible AI", RESPONSIBLE_AI)
    return {
        "classifier_hhh": [
            {"pillar": r["Pillar"], "measures": _clean(r["Scoped formula for this service"]),
             "result": _clean(r["Real result (dev, replayed)"])}
            for r in hhh
        ],
        "classifier_apf": [
            {"dimension": r["Dimension"], "measures": _clean(r["Scoped formula for this service"]),
             "result": _clean(r["Real result (dev, replayed, with a trace file)"])}
            for r in apf
        ],
        "pillars": [
            {"pillar": r["Pillar"], "requirement": _clean(r["PRD requirement"]),
             "implementation": _clean(r["Implementation"]), "evidence": _clean(r["Evidence"])}
            for r in pillars
        ],
        "fairness": fairness(root),
        "guardrails": guardrails(root),
    }  # fmt: skip


def fairness(root: Path = REPO) -> dict[str, Any]:
    """The counterfactual name-swap probe's actual scope, as the document states it."""
    text = " ".join(_read(RESPONSIBLE_AI, root).split())
    m = re.search(
        r"on (\d+) documents that name a person, across development splits, (\w+)-mode", text
    )
    names = re.search(r"under any of (\d+) substitute names", text)
    skipped = re.search(r"(\d+) skipped documents", text)
    if not (m and names and skipped):
        raise ArtifactError(f"{RESPONSIBLE_AI}: the fairness probe's scope statement changed")
    return {
        "documents": int(m.group(1)),
        "mode": m.group(2),
        "substitute_names": int(names.group(1)),
        "skipped": int(skipped.group(1)),
        "result": "classification unchanged for every document under every substitute name",
        "not_claimed": [
            "that the ML or LLM stages are fair (only rules mode has been run)",
            "that the gold labels are unbiased",
            "any demographic association of the invented names",
        ],
    }


def _open_item(text: str, fragment: str) -> bool:
    for r in _table_after(text, "## What is still open", COMPLETION_REPORT)[1:]:
        if fragment in r[1]:
            return not r[1].lstrip().startswith("~~")
    raise ArtifactError(f"{COMPLETION_REPORT}: open item {fragment!r} not found")


def guardrails(root: Path = REPO) -> list[dict[str, Any]]:
    report = _read(COMPLETION_REPORT, root)
    decisions = _read(DECISIONS, root)
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    cs_open = _open_item(report, "Azure AI Content Safety second opinion")
    a38 = "CustomContentFilter412" in decisions
    cs_evidence = (
        "code-complete, tested against a local fake server; not yet run against a real resource"
        if cs_open
        else "live-verified (completion report)"
    )
    return [
        _g("Input", "Input guard (size, encoding)", "guardrails/input.py", "implemented",
           "unit tests; oversize and undecodable input rejected"),
        _g("Input", "S0 prompt-injection scan (event only)", "guardrails/injection.py",
           "implemented", "adversarial T5 documents; shown live on Agent Triage"),
        _g("Output", "Evidence verification (exact substring)", "guardrails/output.py",
           "implemented", "unverified LLM quotes are marked inferred, never observed"),
        _g("Privacy", "Span redaction + privacy audit", "observability/redaction.py",
           "ci_gate" if "obs audit" in ci else "implemented",
           "deny-by-default dg.* allow-list; the audit fails CI on any leaked text"),
        _g("Behavioral", "Agent: tool allow-list, step budget, never-downgrade",
           "app/agent/loop.py", "implemented",
           "adversarial unit tests (D9.34: classify_document bound to the original text)"),
        _g("Behavioral", "MCP: deny-by-default callers, per-caller caps", "config/mcp/mcp.v1.yaml",
           "implemented", "unit tests"),
        _g("Foundry", "Foundry content filter (Prompt Shields) on all 3 deployments",
           "Foundry portal: CustomContentFilter412",
           "portal_configured" if a38 else "unknown",
           "decision A38; verified by screenshot review, no API check"),
        _g("Foundry", "Azure AI Content Safety second opinion (Prompt Shields, Groundedness)",
           "guardrails/azure_content_safety.py",
           "fake_server_tested" if cs_open else "live_verified", cs_evidence),
        _g("Foundry", "Per-request Content Safety blocking", "not built", "not_applicable",
           "deliberately not built (decision A35): audit-time only"),
    ]  # fmt: skip


def _g(layer: str, name: str, where: str, status: str, evidence: str) -> dict[str, str]:
    return {"layer": layer, "name": name, "where": where, "status": status, "evidence": evidence}


def _verdict(status: str) -> str:
    """The strongest verification the status text itself records."""
    low = status.lower()
    if "live-verified" in low:
        return "Live-verified in Foundry"
    if "portal-confirmed" in low:
        return "Portal-confirmed"
    if "sdk-confirmed" in low:
        return "SDK-confirmed only"
    return "Not verified"


# ---- Observability -----------------------------------------------------------------------------
def observability(root: Path = REPO) -> dict[str, Any]:
    engine = _read(OBS_ENGINE, root)
    baseline = _read(OBS_BASELINE, root)
    wanted = ("Azure Monitor export", "Foundry Trace view rendering", "Batch Triage Agent tracing")
    foundry = []
    for line in engine.splitlines():
        for key in wanted:
            if line.startswith(f"| {key} |"):
                text = _clean(line.split("|")[2])
                foundry.append({"concern": key, "status": text, "verdict": _verdict(text)})
    if len(foundry) != len(wanted):
        raise ArtifactError(f"{OBS_ENGINE}: Foundry tracing status rows changed")
    stale = "is NOT verified" in baseline
    cfg = yaml.safe_load(_read(OBS_CONFIG, root))
    return {
        "foundry_status": foundry,
        "allow_listed_keys": len(cfg["allowed_attributes"]),
        "baseline": {
            "stage_latency": _rows(baseline, "## Derived metrics", OBS_BASELINE,
                                   ["stage span", "spans", "errors", "P50", "P95"]),
            "measures": _rows(baseline, "LLM stage latency is the RECORDED", OBS_BASELINE,
                              ["measure", "value"]),
            "privacy_audit": _rows(baseline, "## Privacy audit", OBS_BASELINE, ["check", "result"]),
            "failure_matrix": _rows(baseline, "## Failure matrix", OBS_BASELINE),
            "header_predates_foundry_verification": stale,
        },
    }  # fmt: skip
