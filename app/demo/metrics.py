"""Evaluation numbers for the demo dashboard, read from COMMITTED artifacts only.

Nothing here computes a metric. Run artifacts under `evals/classification/runs/` are git-ignored, so
the dashboard never depends on them; it reads:

* the headline table of `docs/uc4/completion-report.md` (frozen hybrid, labels as reviewed);
* the locked-test run report committed unedited inside `docs/uc4/results/hybrid-locked-test.md`
  (confusion matrix, per-level and per-category tables, high-risk). No test label changed in the
  relabel (`validation-rescore.md`), so these strict tables are still current. Calibration's
  committed report predates the relabel, so its tables are deliberately NOT exposed;
* `docs/uc4/results/agent-eval-dev.json` (the Batch Triage Agent's own evals);
* `config/eval/gates.v1.yaml` for the gate thresholds.

A parser that cannot find what it expects raises `ArtifactError` rather than guessing, and a unit
test cross-checks every parsed value against its source document.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
COMPLETION_REPORT = "docs/uc4/completion-report.md"
LOCKED_TEST_REPORT = "docs/uc4/results/hybrid-locked-test.md"
AGENT_EVAL = "docs/uc4/results/agent-eval-dev.json"
GATES = "config/eval/gates.v1.yaml"
RESPONSIBLE_AI = "docs/uc4/responsible-ai.md"

LEVELS = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_CONFIDENTIAL")
_INTERVAL = re.compile(r"^\s*([0-9.]+)\s*\[([0-9.]+),\s*([0-9.]+)\]\s*$")
_RATIO = re.compile(r"^\s*([0-9.]+)\s*(?:\((\d+)/(\d+)\))?\s*$")


class ArtifactError(RuntimeError):
    """A committed artifact is missing or no longer has the shape this loader reads."""


def _read(rel: str, root: Path = REPO) -> str:
    path = root / rel
    if not path.exists():
        raise ArtifactError(f"{rel} is missing")
    return path.read_text(encoding="utf-8")


def _table_after(text: str, marker: str, source: str) -> list[list[str]]:
    """The first pipe table after the line starting with `marker`, as rows of cells (the header
    row included, the |---| separator dropped)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(marker)), None)
    if start is None:
        raise ArtifactError(f"{source}: section {marker!r} not found")
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                rows.append(cells)
        elif rows:
            break
    if not rows:
        raise ArtifactError(f"{source}: no table after {marker!r}")
    return rows


def _interval(cell: str, source: str) -> dict[str, float]:
    m = _INTERVAL.match(cell)
    if not m:
        raise ArtifactError(f"{source}: expected 'value [lo, hi]', got {cell!r}")
    return {"value": float(m.group(1)), "lo": float(m.group(2)), "hi": float(m.group(3))}


def _ratio(cell: str, source: str) -> dict[str, Any]:
    m = _RATIO.match(cell)
    if not m:
        raise ArtifactError(f"{source}: expected a number (optionally 'n/d'), got {cell!r}")
    out: dict[str, Any] = {"value": float(m.group(1))}
    if m.group(2):
        out["numerator"], out["denominator"] = int(m.group(2)), int(m.group(3))
    return out


def gates(root: Path = REPO) -> dict[str, float]:
    data = yaml.safe_load(_read(GATES, root))
    return {k: float(v) for k, v in data["gates"].items()}


def headline(root: Path = REPO) -> list[dict[str, Any]]:
    """The completion report's results table: one row per split, strict and lenient level F1
    with family-level bootstrap intervals, category F1 and high-risk recall."""
    rows = _table_after(
        _read(COMPLETION_REPORT, root), "## Results (frozen hybrid", COMPLETION_REPORT
    )
    header, body = rows[0], rows[1:]
    expected = ["", "strict level F1", "lenient level F1", "category F1", "high-risk recall"]
    if header != expected:
        raise ArtifactError(f"{COMPLETION_REPORT}: results header changed: {header}")
    out = []
    for cells in body:
        label = cells[0]
        split = label.split(" (")[0]
        note = label[len(split) :].strip().strip("()")
        out.append(
            {
                "split": split,
                "note": note,
                "strict_level_f1": _interval(cells[1], COMPLETION_REPORT),
                "lenient_level_f1": _interval(cells[2], COMPLETION_REPORT),
                "category_f1": _ratio(cells[3], COMPLETION_REPORT),
                "high_risk_recall": _ratio(cells[4], COMPLETION_REPORT),
            }
        )
    if [r["split"] for r in out] != ["locked test", "calibration", "dev"]:
        raise ArtifactError(f"{COMPLETION_REPORT}: unexpected splits {[r['split'] for r in out]}")
    return out


def locked_test_detail(root: Path = REPO) -> dict[str, Any]:
    """Strict tables of the single audited locked-test run, from its committed report."""
    text = _read(LOCKED_TEST_REPORT, root)
    src = LOCKED_TEST_REPORT
    cm_rows = _table_after(text, "Confusion matrix (rows = gold, columns = predicted)", src)
    columns = cm_rows[0][1:]
    if columns[: len(LEVELS)] != list(LEVELS):
        raise ArtifactError(f"{src}: confusion-matrix columns changed: {columns}")
    matrix = {r[0]: {c: int(v) for c, v in zip(columns, r[1:], strict=True)} for r in cm_rows[1:]}
    per_level = _level_table(text, src)
    per_category = [
        {"category": r[0], "precision": float(r[1]), "recall": float(r[2]), "f1": float(r[3]),
         "support": int(r[4])}
        for r in _table_after(text, "### Data categories", src)[1:]
    ]  # fmt: skip
    hr = _table_after(text, "### High-risk (derived", src)
    hr_row = dict(zip(hr[0], hr[1], strict=True))
    run = re.search(r"^## Evaluation run `([^`]+)`", text, re.M)
    deferred = re.search(r"^(\d+) documents were deferred", text, re.M)
    return {
        "run_id": run.group(1) if run else None,
        "confusion_matrix": {"columns": columns, "rows": matrix},
        "per_level": per_level,
        "per_category": per_category,
        "high_risk": {k: (int(v) if v.isdigit() else float(v)) for k, v in hr_row.items()},
        "deferred_to_review": int(deferred.group(1)) if deferred else None,
    }


def _level_table(text: str, src: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith("| level | precision"))
    except StopIteration:
        raise ArtifactError(f"{src}: per-level table not found") from None
    out = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        out.append(
            {"level": c[0], "precision": float(c[1]), "recall": float(c[2]), "f1": float(c[3]),
             "support": int(c[4])}
        )  # fmt: skip
    return out


def agent_eval(root: Path = REPO) -> dict[str, Any]:
    """The Batch Triage Agent's recorded dev-split evals. `hhh.helpful` IS the task-completion rate
    (`evals/classification/agent_eval.py`); `None` means not computed, never zero."""
    data = json.loads(_read(AGENT_EVAL, root))
    try:
        return {
            "n_documents": data["n_documents"],
            "task_completion": data["hhh"]["helpful"],
            "safety_invariant_compliance": data["safety_invariant_compliance"]["rate"],
            "safety_invariant_method": data["safety_invariant_compliance"]["method"],
            "hhh": data["hhh"],
            "apf": data["apf"],
            "planner": "offline deterministic planner (agent triage --agent-mode mock)",
        }
    except KeyError as exc:
        raise ArtifactError(f"{AGENT_EVAL}: missing {exc}") from None


def status(root: Path = REPO) -> dict[str, Any]:
    """The honest v0.1 status panel, derived from the completion report's own tables: an open item
    that is not struck through is still open."""
    text = _read(COMPLETION_REPORT, root)
    criteria = {r[0]: r[1] for r in _table_after(text, "| Completion criterion", COMPLETION_REPORT)}
    open_items = _table_after(text, "## What is still open", COMPLETION_REPORT)[1:]

    def criterion(prefix: str) -> str:
        for k, v in criteria.items():
            if k.startswith(prefix):
                return v
        raise ArtifactError(f"{COMPLETION_REPORT}: criterion {prefix!r} not found")

    def item_open(fragment: str) -> bool:
        for r in open_items:
            if fragment in r[1]:
                return not r[1].lstrip().startswith("~~")
        raise ArtifactError(f"{COMPLETION_REPORT}: open item {fragment!r} not found")

    second_review_open = item_open("second independent human review")
    split_consumed = item_open("locked test split is consumed")
    strict = next(r for r in headline(root) if r["split"] == "locked test")["strict_level_f1"]
    gate = gates(root)["level_macro_f1"]
    return {
        "implemented": criterion("The approved v0.1 scope").startswith("**Met"),
        "evaluated": criterion("Evaluated honestly").startswith("**Met"),
        "human_review": "one reviewer"
        if "one reviewer" in criterion("Gold labels reviewed")
        else None,
        "second_independent_review": "pending" if second_review_open else "done",
        "independent_validation": "pending" if (second_review_open or split_consumed) else "met",
        "level_gate": gate,
        "locked_test_strict_lower_bound": strict["lo"],
        "strict_gate_met": strict["lo"] >= gate,
        "lenient_gate_met": next(r for r in headline(root) if r["split"] == "locked test")[
            "lenient_level_f1"
        ]["lo"]
        >= gate,
        "open_items": [_open_item(r[1]) for r in open_items],
    }


def foundry_evals(root: Path = REPO) -> dict[str, Any]:
    """The live Foundry Evaluations result (decision D9.36), as recorded in `responsible-ai.md`:
    per-criterion pass counts graded by Foundry's deterministic string_check over the dev split."""
    text = _read(RESPONSIBLE_AI, root)
    m = re.search(r"\*\*Live result \((\d{4}-\d{2}-\d{2}), dev split\):\*\*(.+?)\n\n", text, re.S)
    if not m:
        raise ArtifactError(f"{RESPONSIBLE_AI}: the Foundry live-result paragraph was not found")
    body = " ".join(m.group(2).split())
    classifier, _, agent = body.partition("; agent")
    pairs = re.findall(r"`(\w+)` (\d+)/(\d+)", classifier)
    agent_names = re.findall(r"`(\w+)`", agent)
    agent_ratio = re.search(r"(\d+)/(\d+) each", agent)
    if not pairs or not agent_names or not agent_ratio:
        raise ArtifactError(f"{RESPONSIBLE_AI}: the Foundry live-result paragraph changed shape")
    n, d = int(agent_ratio.group(1)), int(agent_ratio.group(2))
    return {
        "date": m.group(1),
        "split": "dev (all tiers)",
        "grader": "Foundry string_check (deterministic, no judge model)",
        "classifier": [{"criterion": c, "passed": int(p), "total": int(t)} for c, p, t in pairs],
        "agent": [{"criterion": c, "passed": n, "total": d} for c in agent_names],
        "matched_local": "matched the local numbers exactly" in body,
    }


def _open_item(cell: str) -> dict[str, Any]:
    closed = cell.lstrip().startswith("~~")
    text = re.sub(r"[*~`]", "", cell).strip()
    item, _, note = text.partition(" Closed ")
    return {
        "item": item.strip(),
        "closed": closed,
        "note": f"Closed {note}".strip() if note else None,
    }


def all_metrics(root: Path = REPO) -> dict[str, Any]:
    return {
        "data_class": "RECORDED",
        "sources": [COMPLETION_REPORT, LOCKED_TEST_REPORT, AGENT_EVAL, GATES, RESPONSIBLE_AI],
        "gates": gates(root),
        "headline": headline(root),
        "locked_test": locked_test_detail(root),
        "agent": agent_eval(root),
        "status": status(root),
        "foundry_evals": foundry_evals(root),
    }
