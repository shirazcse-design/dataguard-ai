"""`dataguard-access`: UC3 AI Data Access Governance Agent.

data build                        regenerate data/access (deterministic)
investigate AR-002 [--mode ...]   one request end to end (replay by default)
eval [--mode ...] [--ids ...]     the frozen golden set (writes docs/uc3/results)
record [--ids ...] --label ...    LIVE: run and record agent turns for replay
agent export                      the Foundry agent's instructions, tool schemas and output schema
                                  as files for MANUAL creation in the portal (nothing is created)
obs report                        traced golden run + privacy audit (offline)
obs live-check                    LIVE: 3 requests via the Foundry agent -> Application Insights
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "docs" / "uc3" / "results"
FOUNDRY_DIR = REPO / "docs" / "uc3" / "foundry"


def cmd_data_build(args) -> int:
    from . import synth

    for p in synth.write():
        print(f"wrote {p.relative_to(REPO)}")
    return 0


def cmd_investigate(args) -> int:
    from . import synth
    from .service import build_governor

    req = next((r for r in synth.REQUESTS if r["request_id"] == args.request), None)
    if req is None:
        print(f"unknown request {args.request}", file=sys.stderr)
        return 2
    x = build_governor(args.mode, backend=args.backend, tenant_id=args.tenant_id).decide(req)
    d = x.decision
    if args.json:
        print(json.dumps({"decision": d.model_dump(), "recommendation": x.recommendation.model_dump() if x.recommendation else None,
                          "facts": x.facts.view(), "agent": x.run.view() if x.run else None}, indent=1, default=str))  # fmt: skip
        return 0
    print(
        f"{req['request_id']}: {req['entitlement_id']} for {req['duration_days']} days ({req.get('purpose_category')})"
    )
    print(f"  agent recommended : {d.agent_recommendation} ({d.agent_effect})")
    print(f"  harness decided   : {d.outcome}  [{d.reason_codes[0]}]")
    if d.alternative:
        print(
            f"  alternative       : {d.alternative.entitlement_id} for {d.alternative.duration_days:g} days"
        )
    print(
        f"  human approval    : {'required' if d.hitl_required else 'not required'} {d.hitl_reasons}"
    )
    print(f"  provisioned       : {d.provisioned} (nothing is ever granted)")
    return 0


def _write(report: dict, stem: str) -> None:
    from evals.access.agent_eval import render

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{stem}.json").write_text(
        json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8"
    )
    (RESULTS / f"{stem}.md").write_text(render(report), encoding="utf-8")
    print(f"wrote docs/uc3/results/{stem}.md")


def cmd_eval(args) -> int:
    from evals.access.agent_eval import run

    ids = [i.strip() for i in args.ids.split(",") if i.strip()] or None
    report = run(args.mode, backend=args.backend, ids=ids, tenant_id=args.tenant_id)
    s = report["summary"]
    print(f"{report['verification']}: acceptable {s['outcome_accuracy_acceptable']}, unsafe approvals {s['unsafe_approvals']}, "
          f"floors lowered {s['floors_lowered']}, agent acceptable {s['agent_recommendation_acceptable']}")  # fmt: skip
    stem = (
        f"eval-{args.mode}"
        + ("-foundry" if args.backend == "foundry-service" else "")
        + (f"-{args.label}" if args.label else "")
    )
    _write(report, stem)
    return 0


def cmd_record(args) -> int:
    args.mode = "record"
    return cmd_eval(args)


def cmd_agent_export(args) -> int:
    """Files for creating the Foundry agent BY HAND (decision: Foundry resources are manual)."""
    from .pipeline import load_agent_config
    from .schemas import AccessRecommendation
    from .service import tool_schemas

    cfg = load_agent_config()
    FOUNDRY_DIR.mkdir(parents=True, exist_ok=True)
    (FOUNDRY_DIR / "instructions.md").write_text(
        (REPO / cfg["prompt_file"]).read_text("utf-8"), encoding="utf-8"
    )
    for t in tool_schemas():
        f = t["function"]
        (FOUNDRY_DIR / "tools").mkdir(exist_ok=True)
        (FOUNDRY_DIR / "tools" / f"{f['name']}.json").write_text(
            json.dumps(f, indent=2) + "\n", encoding="utf-8"
        )
    (FOUNDRY_DIR / "output_schema.json").write_text(
        json.dumps(AccessRecommendation.model_json_schema(), indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"wrote docs/uc3/foundry/: instructions.md, {len(tool_schemas())} tool schemas, output_schema.json"
    )
    return 0


def cmd_agent_register(args) -> int:
    """LIVE (Entra sign-in): create a new version of the Foundry agent, then read it back."""
    from .service import register_agent

    r = register_agent(args.tenant_id, args.rai_policy_id)
    print(json.dumps(r, indent=1))
    return 0 if r["tools_match"] and r["instructions_match"] else 1


def cmd_guardrails_verify(args) -> int:
    """LIVE: the six adversarial probes through the Foundry agent (verdicts only)."""
    import os
    from datetime import date

    from app.agent.foundry_service import project_client, project_endpoint
    from evals.access import guardrails_verify as gv

    from .pipeline import load_agent_config

    name = os.environ.get("DATAGUARD_ACCESS_AGENT", "") or load_agent_config()["name"]
    openai = project_client(project_endpoint(), tenant_id=args.tenant_id).get_openai_client()
    rows = gv.run(
        openai, os.environ.get("DATAGUARD_LLM_DEPLOYMENT_MID", "") or "uc4-llm-medium", name
    )
    prov = {
        "date": date.today().isoformat(),
        "agent": f"{name} (latest version)",
        "guardrail": args.guardrail,
        "uc4_uc6": "replay",
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "guardrails-verification.json").write_text(
        json.dumps({"provenance": prov, "probes": rows}, indent=1, default=str) + "\n", "utf-8"
    )
    (RESULTS / "guardrails-verification.md").write_text(gv.render(rows, prov), encoding="utf-8")
    print(gv.render(rows, prov))
    return 0


def cmd_obs_report(args) -> int:
    from evals.access.observability_report import render, run_traced

    report = run_traced(args.mode)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "observability.json").write_text(
        json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8"
    )
    (RESULTS / "observability.md").write_text(render(report), encoding="utf-8")
    print(
        f"privacy audit clean: {report['privacy_audit']['clean']}; wrote docs/uc3/results/observability.md"
    )
    return 0 if report["privacy_audit"]["clean"] else 1


def cmd_obs_live_check(args) -> int:
    """LIVE: three requests through the Foundry agent with spans exported to Application Insights. A
    fresh CANARY rides in the flagship's justification as a ticket reference (not an instruction), so a
    search in App Insights can prove whether DataGuard's or Foundry's telemetry stores request text.
    UC4 and UC6 replay from the recorded caches; the connection string comes from the Foundry project
    via Entra sign-in and is held in memory only, never printed."""
    import secrets

    from app.agent.foundry_service import project_client, project_endpoint
    from observability import azure_monitor_sink, flush_azure_monitor, load_observability_config
    from observability.config import build_tracer

    from . import synth
    from .service import build_governor
    from .services import build_services, pseudonym

    obs_cfg, _ = load_observability_config()
    project = project_client(project_endpoint(), tenant_id=args.tenant_id)
    conn = project.telemetry.get_application_insights_connection_string()
    tracer, _ = build_tracer(obs_cfg, [azure_monitor_sink(conn)])
    del conn
    canary = f"CANARY{secrets.token_hex(4).upper()}"
    reqs = {r["request_id"]: r for r in synth.REQUESTS}
    flag = dict(reqs["AR-002"])
    flag["request_id"] = "AR-990"  # the flagship plus a canary
    flag["justification"] = f"{flag['justification']} Ticket reference {canary}."
    gov = build_governor(
        "live",
        backend="foundry-service",
        tenant_id=args.tenant_id,
        tracer=tracer,
        svc=build_services("replay"),
    )  # only the agent is live; UC4/UC6 replay
    out = []
    for req in (reqs["AR-001"], flag, reqs["AR-015"]):
        x = gov.decide(req)
        out.append({"request_id": req["request_id"], "outcome": x.decision.outcome, "subject": pseudonym(req["user_id"]),
                    "agent_stop": x.run.stopped_reason if x.run else None})  # fmt: skip
    print(json.dumps({"canary": canary, "flushed": flush_azure_monitor(), "runs": out}, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="dataguard-access", description="UC3 AI Data Access Governance Agent"
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    backend = {"choices": ("chat-completions", "foundry-service"), "default": "chat-completions"}
    d = sub.add_parser("data").add_subparsers(dest="sub", required=True)
    d.add_parser("build").set_defaults(func=cmd_data_build)
    i = sub.add_parser("investigate", help="one request end to end")
    i.add_argument("request")
    i.add_argument("--mode", choices=("offline", "replay", "live"), default="replay")
    i.add_argument("--backend", **backend)
    i.add_argument("--tenant-id", default=None)
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=cmd_investigate)
    e = sub.add_parser("eval", help="the frozen golden set")
    e.add_argument("--mode", choices=("offline", "replay"), default="replay")
    e.add_argument("--backend", **backend)
    e.add_argument("--ids", default="")
    e.add_argument("--label", default="")
    e.add_argument("--tenant-id", default=None)
    e.set_defaults(func=cmd_eval)
    r = sub.add_parser("record", help="LIVE: run and record agent turns for replay")
    r.add_argument("--backend", **backend)
    r.add_argument("--ids", default="")
    r.add_argument("--label", required=True)
    r.add_argument("--tenant-id", default=None)
    r.set_defaults(func=cmd_record)
    a = sub.add_parser("agent").add_subparsers(dest="sub", required=True)
    a.add_parser("export", help="files for creating the Foundry agent by hand").set_defaults(
        func=cmd_agent_export
    )
    ar = a.add_parser("register", help="LIVE: create a new version of the Foundry agent (additive)")
    ar.add_argument("--tenant-id", default=None)
    ar.add_argument("--rai-policy-id", default=None, help="full ARM id of an existing guardrail")
    ar.set_defaults(func=cmd_agent_register)
    gr = sub.add_parser("guardrails").add_subparsers(dest="sub", required=True)
    grv = gr.add_parser("verify", help="LIVE: the six adversarial probes through the Foundry agent")
    grv.add_argument("--tenant-id", default=None)
    grv.add_argument("--guardrail", default="uc3-access-governance-guardrail")
    grv.set_defaults(func=cmd_guardrails_verify)
    o = sub.add_parser("obs").add_subparsers(dest="sub", required=True)
    orp = o.add_parser("report")
    orp.add_argument("--mode", choices=("offline", "replay"), default="replay")
    orp.set_defaults(func=cmd_obs_report)
    olc = o.add_parser("live-check", help="LIVE: 3 requests via the Foundry agent -> App Insights")
    olc.add_argument("--tenant-id", default=None)
    olc.set_defaults(func=cmd_obs_live_check)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
