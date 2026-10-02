"""`dataguard-dlp`: the UC1 Agentic DLP command line.

dataguard-dlp investigate <case-id | event.json> [--mode replay|offline|live] [--json]
dataguard-dlp eval [--mode replay|offline] [--agent-backend ...]
dataguard-dlp record [--ids D01,D11] [--agent-backend ...]     LIVE: record for replay
dataguard-dlp agent register [--tenant-id ...]                 LIVE: new Agent Service version
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .schemas import DLPEvent

REPO = Path(__file__).resolve().parents[2]


def _event(arg: str) -> DLPEvent:
    from evals.dlp.golden import load_golden

    if arg.endswith(".json"):
        return DLPEvent.model_validate_json(Path(arg).read_text(encoding="utf-8"))
    cases = {c.id: c for c in load_golden()[0]}
    if arg not in cases:
        raise SystemExit(f"unknown case {arg!r}")
    return cases[arg].event


def cmd_investigate(args) -> int:
    from .service import build_investigator

    inv = build_investigator(
        args.mode, agent_backend=args.agent_backend, tenant_id=args.tenant_id
    ).investigate(_event(args.case))
    if args.json:
        print(inv.model_dump_json(indent=1))
        return 0
    d = inv.decision
    print(
        f"[{inv.mode.upper()}] {inv.case_id}: {d.outcome} "
        f"(band {d.band}, score {d.score}, risk {d.risk_level})"
    )
    for s in inv.stages:
        print(f"  {s.name:15s} {s.status:8s} {json.dumps(s.detail)[:150]}")
    print("  reasons: " + "; ".join(d.reason_codes))
    if d.simulated_action:
        print(f"  proposed action: {d.simulated_action}")
    return 0


def _run(mode: str, backend: str, ids: list[str] | None, tenant_id: str | None):
    from evals.dlp.golden import load_golden
    from evals.dlp.metrics import score_case, summarise

    from .service import build_investigator

    cases, sha = load_golden()
    if ids:
        cases = [c for c in cases if c.id in ids]
    inv = build_investigator(mode, agent_backend=backend, tenant_id=tenant_id)
    rows = [score_case(c, inv.investigate(c.event)) for c in cases]
    planner = (
        getattr(inv.agent.planner, "model_id", type(inv.agent.planner).__name__)
        if inv.agent
        else None
    )
    return inv, rows, summarise(rows), sha, planner


def cmd_eval(args) -> int:
    from evals.dlp.report import write

    inv, rows, summary, sha, planner = _run(args.mode, args.agent_backend, None, args.tenant_id)
    report = {
        "provenance": {
            "mode": inv.mode,
            "agent_backend": args.agent_backend,
            "planner": planner,
            "cases": len(rows),
            "golden_sha256": sha,
            "rubric_version": inv.rubric.rubric_version,
            "mapping_version": inv.mapping.mapping_version,
            "config_hashes": inv.hashes,
        },
        "summary": summary,
        "cases": rows,
    }
    stem = "eval" if args.agent_backend == "chat-completions" else "eval-foundry-agent"
    if args.mode == "offline":
        stem += "-offline"
    md = write(report, stem)
    m = summary["metrics"]
    print(
        f"wrote {md.relative_to(REPO)}; "
        f"acceptable accuracy {m['acceptable_outcome_accuracy']['value']}, "
        f"critical FN {m['critical_false_negative_count']['value']}, outcomes {summary['outcomes']}"
    )
    return 0


def cmd_record(args) -> int:
    ids = [x.strip() for x in args.ids.split(",")] if args.ids else None
    _, rows, summary, _, _ = _run("record", args.agent_backend, ids, args.tenant_id)
    print(
        json.dumps(
            {
                "cases": len(rows),
                "outcomes": summary["outcomes"],
                "agent_stops": {
                    k: sum(r["agent_stopped"] == k for r in rows)
                    for k in sorted({r["agent_stopped"] for r in rows})
                },
                "policy_status": {
                    k: sum(r["policy_status"] == k for r in rows)
                    for k in sorted({r["policy_status"] for r in rows})
                },
                "agent_tokens_in": summary["system"]["agent_tokens_in"],
            },
            indent=1,
        )
    )
    return 0


def cmd_agent_register(args) -> int:
    """LIVE: create a new version of dataguard-dlp-investigator in Foundry Agent Service."""
    from .service import register_dlp_agent

    print(json.dumps(register_dlp_agent(args.tenant_id, args.rai_policy_id), indent=1))
    return 0


def cmd_guardrails_verify(args) -> int:
    """LIVE: GU1-GU7 through the app and straight to the Foundry agent (verdicts only)."""
    import os
    from datetime import date

    from app.agent.foundry_service import project_client, project_endpoint
    from evals.dlp import guardrails_verify as gv
    from evals.dlp.report import RESULTS as RESULTS_DIR

    from .config import load_dlp_configs
    from .service import AGENT_NAME_ENV

    dlp, *_ = load_dlp_configs()
    agent_name = os.environ.get(AGENT_NAME_ENV, "") or dlp.agent.name
    deployment = os.environ.get("DATAGUARD_LLM_DEPLOYMENT_MID", "") or "uc4-llm-medium"
    openai = project_client(project_endpoint(), tenant_id=args.tenant_id).get_openai_client()
    rows = gv.run(openai, deployment, agent_name)
    provenance = {
        "date": date.today().isoformat(),
        "agent": agent_name,
        "guardrail": args.guardrail,
        "uc4_uc6": "replay (recorded caches)",
        "agent_path": "live, Foundry Agent Service",
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "guardrails-verification.json").write_text(
        json.dumps({"provenance": provenance, "probes": rows}, indent=1) + "\n", encoding="utf-8"
    )
    (RESULTS_DIR / "guardrails-verification.md").write_text(
        gv.render(rows, provenance), encoding="utf-8"
    )
    print(gv.render(rows, provenance))
    return 0


def cmd_obs_report(args) -> int:
    """Traced golden run + privacy audit + telemetry summary (docs/uc1/results/observability.*)."""
    from evals.dlp.golden import load_golden
    from evals.dlp.observability_report import privacy_audit, render_markdown, run_traced, summarise
    from evals.dlp.report import RESULTS as RESULTS_DIR

    cases, _ = load_golden()
    spans, inv = run_traced(args.mode, cases)
    report = {
        "mode": args.mode,
        "privacy_audit": privacy_audit(spans, cases, inv),
        "telemetry": summarise(spans),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "observability.json").write_text(
        json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    (RESULTS_DIR / "observability.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"privacy audit clean: {report['privacy_audit']['clean']}; wrote observability.md")
    return 0 if report["privacy_audit"]["clean"] else 5


def cmd_obs_live_check(args) -> int:
    """LIVE: send UC1 investigations' spans to Application Insights. Two carry a unique CANARY
    token (in the justification, and in the destination host) so you can prove in the portal that
    neither reaches telemetry. The connection string comes from the environment or, with
    --from-project, from the Foundry project via Entra sign-in; it is held in memory only and never
    printed. UC4/UC6 replay from the recorded caches; the agent is the live Foundry agent."""
    import os
    import secrets

    from app.agent.foundry_service import (
        FoundryAgentServiceClient,
        project_client,
        project_endpoint,
    )
    from evals.dlp.golden import load_golden
    from observability import azure_monitor_sink, flush_azure_monitor, load_observability_config
    from observability.config import build_tracer

    from .config import load_dlp_configs
    from .service import AGENT_NAME_ENV, build_investigator

    obs_cfg, _ = load_observability_config()
    project = project_client(project_endpoint(), tenant_id=args.tenant_id)
    conn = os.environ.get(obs_cfg.azure_monitor.connection_string_env)
    if not conn and args.from_project:
        conn = project.telemetry.get_application_insights_connection_string()
    if not conn:
        print(f"error: {obs_cfg.azure_monitor.connection_string_env} is not set", file=sys.stderr)
        return 2
    tracer, _ = build_tracer(obs_cfg, [azure_monitor_sink(conn)])
    del conn
    dlp, *_ = load_dlp_configs()
    agent_name = os.environ.get(AGENT_NAME_ENV, "") or dlp.agent.name
    deployment = os.environ.get("DATAGUARD_LLM_DEPLOYMENT_MID", "") or "uc4-llm-medium"
    openai = project.get_openai_client()
    canary = f"CANARY{secrets.token_hex(4).upper()}"
    cases = {c.id: c for c in load_golden()[0]}
    host = cases["D10"].event.destination.model_copy(
        update={"host": f"files-{canary.lower()}.example"}
    )
    runs = [
        ("flagship", cases["D11"].event),
        ("canary_justification", cases["D11"].event.model_copy(update={
            "case_id": "OBS-CANARY-J",
            "user_justification": f"Sharing the shortlist with advisor {canary} before Monday."})),
        ("canary_host", cases["D10"].event.model_copy(
            update={"case_id": "OBS-CANARY-H", "destination": host})),
        ("injection", cases["D26"].event),
    ]  # fmt: skip
    out = []
    for label, event in runs:
        planner = FoundryAgentServiceClient(openai, deployment, agent_name=agent_name)
        inv = build_investigator(
            "replay", agent_backend="foundry-service", planner=planner, tracer=tracer
        )
        r = inv.investigate(event)
        out.append({"run": label, "case_id": r.case_id, "outcome": r.decision.outcome,
                    "agent_stopped": r.agent.stopped_reason if r.agent else None,
                    "has_canary": label.startswith("canary")})  # fmt: skip
    flushed = flush_azure_monitor()
    print(json.dumps({"canary": canary, "flushed": flushed, "runs": out}, indent=1))
    return 0


def cmd_foundry_eval(args) -> int:
    """Export the UC1 Foundry evaluation rows (replay); with --run, create the evaluations in
    Foundry (cloud Evals API) and wait for results. --run was done at the product owner's explicit
    request; see evals/dlp/foundry_evals.py."""
    from datetime import datetime

    from evals.dlp import foundry_evals as fe
    from evals.dlp.report import RESULTS as RESULTS_DIR

    outcomes, agent_rows, sha = fe.build_rows("replay")
    fe.write_jsonl(fe.DATA / "outcomes.jsonl", outcomes)
    fe.write_jsonl(fe.DATA / "agent.jsonl", agent_rows)
    print(
        f"wrote {len(outcomes)} outcome rows and {len(agent_rows)} agent rows (golden {sha[:12]})"
    )
    if not args.run:
        return 0
    from app.agent.foundry_service import project_client, project_endpoint

    client = project_client(project_endpoint(), tenant_id=args.tenant_id).get_openai_client()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out: dict = {"created": stamp, "judge": args.judge, "golden_sha256": sha}
    if args.which in ("outcomes", "both"):
        rows = fe.load_rows(fe.DATA / "outcomes.jsonl", fe.OUTCOME_FIELDS)
        p = fe.payload(fe.OUTCOMES_EVAL, fe.OUTCOME_FIELDS, fe.OUTCOME_CRITERIA, rows)
        res = fe.run_in_foundry(client, p, f"uc1-outcomes-{stamp}", timeout_s=args.timeout)
        out["outcomes"] = {**res, "local": fe.local_outcome_counts(rows)}
        print(json.dumps({"outcomes": out["outcomes"]}, indent=1, default=str), flush=True)
    if args.which == "outcomes":
        return _write_foundry_results(out, stamp, RESULTS_DIR)
    rows = fe.load_agent_rows(fe.DATA / "agent.jsonl")
    p = fe.agent_payload(fe.agent_criteria(args.judge), rows)
    res = fe.run_in_foundry(client, p, f"uc1-agent-quality-{stamp}", timeout_s=args.timeout)
    scores = fe.per_row_scores(client, res["eval_id"], res["run_id"])
    out["agent_quality"] = {**res, "summary": fe.summarise_scores(scores), "rows": scores}
    printable = {k: v for k, v in out["agent_quality"].items() if k != "rows"}
    print(json.dumps({"agent_quality": printable}, indent=1, default=str), flush=True)
    return _write_foundry_results(out, stamp, RESULTS_DIR)


def _write_foundry_results(out: dict, stamp: str, results_dir) -> int:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / f"foundry-evals-{stamp}.json").write_text(
        json.dumps(out, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    print(f"wrote docs/uc1/results/foundry-evals-{stamp}.json")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dataguard-dlp", description="UC1 Agentic DLP")
    sub = p.add_subparsers(dest="cmd", required=True)
    backend = {"choices": ("chat-completions", "foundry-service"), "default": "chat-completions"}
    i = sub.add_parser("investigate", help="investigate one golden case id or an event JSON file")
    i.add_argument("case")
    i.add_argument("--mode", choices=("replay", "offline", "live"), default="replay")
    i.add_argument("--agent-backend", **backend)
    i.add_argument("--tenant-id", default=None)
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=cmd_investigate)
    e = sub.add_parser("eval", help="evaluate the golden set (writes docs/uc1/results)")
    e.add_argument("--mode", choices=("replay", "offline"), default="replay")
    e.add_argument("--agent-backend", **backend)
    e.add_argument("--tenant-id", default=None)
    e.set_defaults(func=cmd_eval)
    r = sub.add_parser("record", help="LIVE: run golden cases and record every response for replay")
    r.add_argument("--ids", default="")
    r.add_argument("--agent-backend", **backend)
    r.add_argument("--tenant-id", default=None)
    r.set_defaults(func=cmd_record)
    ag = sub.add_parser("agent", help="Foundry Agent Service commands")
    ag = ag.add_subparsers(dest="sub", required=True)
    agr = ag.add_parser("register", help="LIVE: create a new version of dataguard-dlp-investigator")
    agr.add_argument("--tenant-id", default=None, help="Entra tenant id for the browser sign-in")
    agr.add_argument(
        "--rai-policy-id", default=None, help="full ARM id of an existing guardrail to attach"
    )
    agr.set_defaults(func=cmd_agent_register)
    gr = sub.add_parser("guardrails", help="guardrail commands")
    gr = gr.add_subparsers(dest="sub", required=True)
    grv = gr.add_parser("verify", help="LIVE: GU1-GU7 through the app and the Foundry agent")
    grv.add_argument("--tenant-id", default=None, help="Entra tenant id (agent sign-in)")
    grv.add_argument("--guardrail", default="uc1-dlp-investigator-guardrail")
    grv.set_defaults(func=cmd_guardrails_verify)
    fx = sub.add_parser("foundry-eval", help="export UC1 Foundry eval rows; --run creates them")
    fx.add_argument("--run", action="store_true", help="LIVE: create and run in Foundry")
    fx.add_argument("--judge", default="uc4-llm-medium")
    fx.add_argument("--which", choices=("outcomes", "agent", "both"), default="both")
    fx.add_argument("--timeout", type=float, default=1200.0)
    fx.add_argument("--tenant-id", default=None)
    fx.set_defaults(func=cmd_foundry_eval)
    obs = sub.add_parser("obs", help="observability commands")
    obs = obs.add_subparsers(dest="sub", required=True)
    orp = obs.add_parser("report", help="traced golden run + privacy audit + telemetry summary")
    orp.add_argument("--mode", choices=("replay", "offline"), default="replay")
    orp.set_defaults(func=cmd_obs_report)
    olc = obs.add_parser("live-check", help="LIVE: send UC1 spans (with a CANARY) to App Insights")
    olc.add_argument("--tenant-id", default=None)
    olc.add_argument(
        "--from-project",
        action="store_true",
        help="read the App Insights connection string from the Foundry project (Entra sign-in)",
    )
    olc.set_defaults(func=cmd_obs_live_check)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
