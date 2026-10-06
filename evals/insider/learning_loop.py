"""UC2 controlled learning loop (P13): a governed, OFFLINE product-improvement loop. It is not
self-modification. The runtime never imports this module, never reads `config/insider/candidates/`,
and never rewrites its prompts, rubric, permissions, policies, labels or model.

    runs + telemetry + analyst feedback      (collect)
      -> failure / low-confidence mining     (mine)
      -> curated eval candidates             (NEEDS_HUMAN_LABEL; never added to the golden set here)
      -> candidate improvement               (a versioned candidate file, written by a person)
      -> offline regression + safety gate    (replay all 36 golden cases: baseline vs candidate)
      -> human approval                      (`learn approve`, recorded with a name and a reason)
      -> controlled deployment               (a reviewed change to config/insider/risk.v1.yaml)

Safety-sensitive patterns (an analyst wanting a high-risk outcome LOWERED) never produce an
automatic candidate: they become a human review item and an eval candidate.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "docs" / "uc2" / "results"
SOURCE_RUN = RESULTS / "agents-live-run3-full36.json"
SEED_FEEDBACK = REPO / "data" / "insider" / "feedback" / "analyst-feedback.seed.v1.jsonl"
DEMO_FEEDBACK = REPO / "var" / "demo" / "insider-reviews.jsonl"
CANDIDATES = REPO / "config" / "insider" / "candidates"
APPROVALS = REPO / "docs" / "uc2" / "learning" / "approvals.jsonl"
RUNTIME_RUBRIC = REPO / "config" / "insider" / "risk.v1.yaml"
SEVERITY = {"MONITOR": 0, "INVESTIGATE": 1, "ESCALATE": 2, "HUMAN_REVIEW": 2}
# Files the loop must never change (checked before and after every run).
PROTECTED = ("config/insider/risk.v1.yaml", "config/insider/agents.v1.yaml",
             "evals/insider/dataset/golden.v1.jsonl", "evals/insider/dataset/FROZEN.json")  # fmt: skip


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected_hashes() -> dict[str, str]:
    out = {p: sha(REPO / p) for p in PROTECTED}
    out.update(
        {f"prompts/uc2/{f.name}": sha(f) for f in sorted((REPO / "prompts" / "uc2").glob("*"))}
    )
    return out


# -- 1. collect ----------------------------------------------------------------------------------
def load_feedback(paths: tuple[Path, ...] = (SEED_FEEDBACK, DEMO_FEEDBACK)) -> list[dict[str, Any]]:
    rows = []
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rows.append(
                    {**r, "source": r.get("source") or ("seed" if p == SEED_FEEDBACK else "demo")}
                )
    return rows


def collect(run_path: Path = SOURCE_RUN, feedback: list[dict] | None = None) -> dict[str, Any]:
    run = json.loads(run_path.read_text(encoding="utf-8"))
    rows = run["architectures"]["full"]["rows"]
    obs = json.loads((RESULTS / "observability.json").read_text(encoding="utf-8"))
    return {"run": str(run_path.relative_to(REPO)), "verification": run.get("verification"),
            "rows": rows, "review_reasons": obs["telemetry"].get("review_reasons", {}),
            "feedback": load_feedback() if feedback is None else feedback}  # fmt: skip


# -- 2. mine -------------------------------------------------------------------------------------
def _low_confidence(row: dict[str, Any]) -> bool:
    for a in row.get("agents", []):
        out = a.get("output") or {}
        if a["name"].endswith("risk") and out.get("confidence") == "low":
            return True
    return False


def mine(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Group failure and low-confidence signals into patterns, keyed by the signal and the reason
    code that explains it. Labels are read, never changed."""
    by_case = {r["id"]: r for r in data["rows"]}
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in data["rows"]:
        review = [c for c in r["reason_codes"] if c.startswith("review:")] or [
            f"outcome:{r['outcome']}"
        ]
        if r["false_review"]:
            for c in review:
                groups[("false_review", c)].add(r["id"])
        elif not r["ok"] and SEVERITY[r["outcome"]] > SEVERITY[r["expected"]]:
            groups[("over_escalation", f"band:{r['band']}")].add(r["id"])
        elif not r["ok"]:
            groups[("under_call", f"band:{r['band']}")].add(r["id"])
        if r["agent_failures"] or r["budget_exceeded"] or r.get("early_stop"):
            groups[("agent_reliability", r.get("early_stop") or "agent_failure")].add(r["id"])
        if _low_confidence(r):
            groups[("low_confidence", "risk_agent")].add(r["id"])
    for f in data["feedback"]:
        if f["action"] in ("disagree_lower", "disagree_higher", "needs_more_information"):
            groups[(f"analyst_{f['action']}", f"outcome:{f['outcome']}")].add(f["case_id"])
    patterns = []
    for (signal, key), ids in sorted(groups.items()):
        cases = sorted(ids)
        outcomes = sorted({by_case[i]["outcome"] for i in cases if i in by_case})
        # any fix would LOWER a high-risk outcome (an over-escalation, or an analyst asking to lower
        # an escalation): never turned into an automatic candidate is never turned into an automatic candidate
        lowering_high_risk = signal == "analyst_disagree_lower" and key == "outcome:ESCALATE"
        patterns.append({"signal": signal, "key": key, "cases": cases, "n": len(cases), "outcomes": outcomes,
                         "safety_sensitive": signal in ("under_call", "over_escalation") or lowering_high_risk})  # fmt: skip
    return patterns


# -- 3. curated eval candidates --------------------------------------------------------------------
def eval_candidates(patterns: list[dict[str, Any]], cases: dict[str, dict]) -> list[dict[str, Any]]:
    """One new-case PROPOSAL per pattern: a fresh variant to label, so a candidate fix is not judged
    only on the cases it was mined from. Never written into the golden set by this loop."""
    merged: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for p in patterns:  # one proposal per distinct set of seed cases
        merged[tuple(p["cases"])].append(f"{p['signal']}:{p['key']}")
    out = []
    for i, (ids, names) in enumerate(sorted(merged.items()), 1):
        seeds = [cases[c] for c in ids if c in cases]
        p = {"cases": list(ids)}
        out.append({"id": f"EC{i:02d}", "pattern": " + ".join(names), "seed_cases": list(ids),
                    "categories": sorted({s["category"] for s in seeds}),
                    "proposal": f"a fresh synthetic variant of {', '.join(p['cases'][:3])} (different user and day)",
                    "status": "NEEDS_HUMAN_LABEL"})  # fmt: skip
    return out


# -- 4/5. candidate improvement + offline regression and safety gate --------------------------------
def replay_outcomes(rubric: dict[str, Any]) -> list[dict[str, Any]]:
    from app.insider.service import build_investigator
    from evals.insider.agent_eval import load_golden, score_case

    inv = build_investigator("replay", architecture="full")
    inv.rubric = rubric  # the candidate is applied to THIS offline investigator only
    return [score_case(c, inv.investigate(c)) for c in load_golden()]


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    exp = [r for r in rows if r["hitl_expected"]]
    ret = [r for r in rows if r["hitl_returned"]]
    return {"acceptable": sum(r["ok"] for r in rows), "cases": len(rows),
            "critical_misses": sum(r["critical_miss"] for r in rows),
            "false_reviews": sum(r["false_review"] for r in rows),
            "hitl_recall": round(sum(r["hitl_hit"] for r in exp) / len(exp), 3) if exp else None,
            "hitl_precision": round(sum(r["hitl_hit"] for r in ret) / len(ret), 3) if ret else None,
            "unsupported_ids": sum(r["unsupported_ids"] for r in rows),
            "agent_failures": sum(r["agent_failures"] for r in rows)}  # fmt: skip


def gate(base: list[dict], cand: list[dict], mined_from: list[str]) -> dict[str, Any]:
    """Every check must pass. A candidate can only be APPROVABLE, never promoted, here."""
    b, c = _summary(base), _summary(cand)
    bo = {r["id"]: r for r in base}
    changed = [{"id": r["id"], "category": r["category"], "expected": r["expected"],
                "before": bo[r["id"]]["outcome"], "after": r["outcome"], "ok_after": r["ok"]}
               for r in cand if r["outcome"] != bo[r["id"]]["outcome"]]  # fmt: skip
    lowered_high = [x for x in changed if x["before"] == "ESCALATE" and x["after"] != "ESCALATE"]
    adversarial_down = [x for x in changed if bo[x["id"]]["category"] in ("adversarial", "flagship")
                        and SEVERITY[x["after"]] < SEVERITY[x["before"]]]  # fmt: skip
    flagship = next(r for r in cand if r["category"] == "flagship")
    checks = {
        "no_critical_miss": c["critical_misses"] == 0,
        "acceptable_not_lower": c["acceptable"] >= b["acceptable"],
        "hitl_recall_not_lower": (c["hitl_recall"] or 0) >= (b["hitl_recall"] or 0),
        "flagship_escalates": flagship["outcome"] == "ESCALATE",
        "no_escalation_lowered": not lowered_high,
        "no_adversarial_case_lowered": not adversarial_down,
        "no_unsupported_evidence": c["unsupported_ids"] == 0,
        "no_new_agent_failures": c["agent_failures"] <= b["agent_failures"],
    }
    out_of_sample = [x for x in changed if x["id"] not in mined_from]
    return {"baseline": b, "candidate": c, "changed_cases": changed, "checks": checks,
            "verdict": "PASS" if all(checks.values()) else "FAIL",
            "in_sample_only": not out_of_sample,
            "caveat": "The candidate was mined from the golden set it is gated on: passing is necessary, not "
                      "sufficient. Label the eval candidates and re-run the gate on them before promotion."}  # fmt: skip


def load_candidate(path: Path) -> dict[str, Any]:
    if CANDIDATES not in path.resolve().parents:
        raise ValueError("candidates must live in config/insider/candidates/")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# -- orchestration ---------------------------------------------------------------------------------
def run(candidate_path: Path) -> dict[str, Any]:
    from app.insider.harness import load_rubric
    from evals.insider.agent_eval import load_golden

    before = protected_hashes()
    data = collect()
    patterns = mine(data)
    cases = {c["id"]: c for c in load_golden()}
    candidate = load_candidate(candidate_path)
    target = (candidate.get("learning_loop") or {}).get("targets")
    if not any(f"{p['signal']}:{p['key']}" == target for p in patterns):
        raise ValueError(
            f"the candidate must target a mined pattern (learning_loop.targets); got {target!r}"
        )
    if any(f"{p['signal']}:{p['key']}" == target and p["safety_sensitive"] for p in patterns):
        raise ValueError(
            "a safety-sensitive pattern goes to human review, not to an automatic candidate"
        )
    mined_from = next(p["cases"] for p in patterns if f"{p['signal']}:{p['key']}" == target)
    base = replay_outcomes(load_rubric())
    cand = replay_outcomes(candidate)
    g = gate(base, cand, mined_from)
    after = protected_hashes()
    if before != after:
        raise RuntimeError("the learning loop changed a protected file")  # never expected
    return {
        "source": {"run": data["run"], "verification": data["verification"],
                   "feedback": {s: sum(1 for f in data["feedback"] if f["source"] == s) for s in ("seed", "demo")}},
        "patterns": patterns,
        "eval_candidates": eval_candidates(patterns, cases),
        "human_review_items": [p for p in patterns if p["safety_sensitive"]],
        "candidate": {"path": str(candidate_path.relative_to(REPO)), "rubric_version": candidate["rubric_version"],
                      "kind": "harness rule", "sha256": sha(candidate_path), "targets": target, "mined_from": mined_from},
        "gate": g,
        "status": "AWAITING_HUMAN_APPROVAL" if g["verdict"] == "PASS" else "REJECTED_BY_GATE",
        "runtime_rubric": {"rubric_version": load_rubric()["rubric_version"], "unchanged": True},
        "protected_files_unchanged": True,
    }  # fmt: skip


def approve(candidate_path: Path, approver: str, decision: str, reason: str) -> dict[str, Any]:
    """Record a HUMAN decision about a gated candidate. Approval does not deploy anything: promotion
    is a reviewed change to config/insider/risk.v1.yaml (controlled deployment)."""
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be approve or reject")
    if not approver.strip() or not reason.strip():
        raise ValueError("an approver name and a reason are required")
    report = json.loads((RESULTS / "learning-loop.json").read_text(encoding="utf-8"))
    cand = report["candidate"]
    if cand["path"] != str(candidate_path.resolve().relative_to(REPO)) or cand["sha256"] != sha(
        candidate_path
    ):
        raise ValueError(
            "this candidate is not the one the last gate run evaluated; run `learn run` first"
        )
    if decision == "approve" and report["gate"]["verdict"] != "PASS":
        raise ValueError("a candidate that failed the gate cannot be approved")
    from datetime import datetime

    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "candidate": cand["path"],
           "rubric_version": cand["rubric_version"], "sha256": cand["sha256"], "decision": decision,
           "approver": approver.strip(), "reason": reason.strip(), "gate_verdict": report["gate"]["verdict"],
           "deployed": False}  # fmt: skip
    APPROVALS.parent.mkdir(parents=True, exist_ok=True)
    with APPROVALS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def render(r: dict[str, Any]) -> str:
    g = r["gate"]
    lines = ["# UC2 controlled learning loop (generated)", "",
             "Generated by `dataguard-insider learn run`. OFFLINE: the runtime is never modified; nothing is promoted here.", "",
             f"* Source: `{r['source']['run']}` ({r['source']['verification']}); analyst feedback: "
             f"{r['source']['feedback']['seed']} seeded (synthetic, labelled), {r['source']['feedback']['demo']} from the demo.",
             f"* Runtime rubric: {r['runtime_rubric']['rubric_version']} (unchanged); protected files unchanged: {r['protected_files_unchanged']}.",
             "", "## Mined patterns", "", "| Signal | Key | Cases | Safety-sensitive |", "|---|---|---|---|"]  # fmt: skip
    lines += [f"| {p['signal']} | {p['key']} | {', '.join(p['cases'])} | {'yes' if p['safety_sensitive'] else 'no'} |"
              for p in r["patterns"]]  # fmt: skip
    lines += ["", "## Eval candidates (NEEDS_HUMAN_LABEL; not in the golden set)", ""]
    lines += [f"* **{e['id']}** {e['pattern']}: {e['proposal']}" for e in r["eval_candidates"]]
    lines += ["", f"## Candidate `{r['candidate']['rubric_version']}` ({r['candidate']['kind']})", "",
              f"`{r['candidate']['path']}`, targets `{r['candidate']['targets']}` (mined from {', '.join(r['candidate']['mined_from'])}).", "",
              "| Metric (36 cases, replay of live run 3) | Baseline | Candidate |", "|---|---|---|"]  # fmt: skip
    for k in (
        "acceptable",
        "critical_misses",
        "false_reviews",
        "hitl_recall",
        "hitl_precision",
        "unsupported_ids",
        "agent_failures",
    ):
        lines.append(f"| {k} | {g['baseline'][k]} | {g['candidate'][k]} |")
    lines += ["", "Changed cases: " + (", ".join(f"{x['id']} {x['before']} → {x['after']} (expected {x['expected']})"
                                                  for x in g["changed_cases"]) or "none"), "",
              "| Gate check | Result |", "|---|---|"]  # fmt: skip
    lines += [f"| {k} | {'PASS' if v else 'FAIL'} |" for k, v in g["checks"].items()]
    lines += [
        "",
        f"**Verdict: {g['verdict']}. Status: {r['status']}.**",
        "",
        f"Caveat: {g['caveat']}",
        "",
    ]
    if r["human_review_items"]:
        lines += ["## Human review items (no automatic candidate)", ""]
        lines += [
            f"* {p['signal']} ({p['key']}): {', '.join(p['cases'])}"
            for p in r["human_review_items"]
        ]
    return "\n".join(lines) + "\n"
