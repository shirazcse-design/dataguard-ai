"""`dataguard-incident`: UC5 Data Security Incident Investigation Agent.

data build                         regenerate data/incident (deterministic) and the golden file
investigate INC-001 [--mode ...]   one incident end to end (offline by default)
eval [--mode ...] [--ids ...]      the frozen golden set (writes docs/uc5/results)
record [--ids ...] --label ...     LIVE: run and record agent turns for replay
agent export                       the Foundry agent's instructions, tool schemas and I/O schemas as files
agent register                     LIVE (Entra): create a new version of the Foundry agent (additive;
                                   done at the product owner's explicit request, 2026-10-07)
guardrails verify                  the adversarial suite (offline: scripted compromised agent)
obs report                         traced golden run + privacy audit
foundry-eval [--run|--fetch]       write the Foundry evaluation rows (manual portal setup); --run creates
                                   them through the Evals API ONLY when the product owner asks; --fetch
                                   reads the newest runs' results (read-only)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "docs" / "uc5" / "results"
FOUNDRY_DIR = REPO / "docs" / "uc5" / "foundry"


def _write(stem: str, obj: dict, md: str) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{stem}.json").write_text(
        json.dumps(obj, indent=1, default=str) + "\n", encoding="utf-8"
    )
    (RESULTS / f"{stem}.md").write_text(md, encoding="utf-8")
    print(f"wrote docs/uc5/results/{stem}.md")


def cmd_data_build(args) -> int:
    from evals.incident import golden_build

    from . import synth

    for p in [*synth.write(), golden_build.write()]:
        print(f"wrote {p.relative_to(REPO)}")
    return 0


def cmd_investigate(args) -> int:
    from . import synth
    from .service import build_investigator

    inc = next((i for i in synth.load_incidents() if i["case_id"] == args.case), None)
    if inc is None:
        print(f"unknown case {args.case}", file=sys.stderr)
        return 2
    x = build_investigator(args.mode, backend=args.backend, tenant_id=args.tenant_id).investigate(
        inc
    )
    if args.json:
        print(json.dumps(x.final_report(), indent=1, default=str))
        return 0
    d = x.decision
    print(f"{inc['case_id']}: {inc['trigger']['summary']}")
    print(f"  agent recommended : {d.agent_recommendation} ({d.agent_effect})")
    print(f"  harness severity  : {d.severity} (floor {d.deterministic_severity}, rule {d.rule})")
    print(
        f"  incident status   : {d.incident_status}{'  [potential Sev 1: analyst to confirm]' if d.potential_sev1 else ''}"
    )
    print(f"  analyst review    : {d.review_status} {d.review_reasons}")
    print(
        f"  timeline events   : {len(x.facts.timeline)}; correlations {len(x.facts.correlations)}"
    )
    print(
        f"  report claims     : {x.validation['total']} ({x.validation['unsupported']} unsupported, {x.validation['withheld']} withheld)"
    )
    print("  remediation       : NONE_EXECUTED (an analyst owns every action)")
    return 0


def cmd_eval(args) -> int:
    from evals.incident.agent_eval import render, run

    ids = [i.strip() for i in args.ids.split(",") if i.strip()] or None
    report = run(args.mode, backend=args.backend, ids=ids, tenant_id=args.tenant_id)
    s = report["summary"]
    print(
        f"{report['verification']}: severity {s['severity_acceptable']}, review {s['review_correct']}, evidence "
        f"{s['evidence_completeness']}, unsupported claims {s['unsupported_claim_rate']}, floor violations {s['severity_floor_violations']}"
    )
    stem = (
        f"eval-{args.mode}"
        + ("-foundry" if args.backend == "foundry-service" else "")
        + (f"-{args.label}" if args.label else "")
    )
    _write(stem, report, render(report))
    return 0


def cmd_record(args) -> int:
    args.mode = "record"
    return cmd_eval(args)


def cmd_agent_export(args) -> int:
    """Files for creating the Foundry agent BY HAND in the portal (decision (e): manual setup)."""
    from .pipeline import load_agent_config
    from .schemas import CasePacket, IncidentReport
    from .service import tool_schemas

    cfg = load_agent_config()
    FOUNDRY_DIR.mkdir(parents=True, exist_ok=True)
    (FOUNDRY_DIR / "instructions.md").write_text(
        (REPO / cfg["prompt_file"]).read_text("utf-8"), encoding="utf-8"
    )
    (FOUNDRY_DIR / "tools").mkdir(exist_ok=True)
    for t in tool_schemas():
        f = t["function"]
        (FOUNDRY_DIR / "tools" / f"{f['name']}.json").write_text(
            json.dumps(f, indent=2) + "\n", encoding="utf-8"
        )
    (FOUNDRY_DIR / "input_schema.json").write_text(
        json.dumps(CasePacket.model_json_schema(), indent=2) + "\n", encoding="utf-8"
    )
    (FOUNDRY_DIR / "output_schema.json").write_text(
        json.dumps(IncidentReport.model_json_schema(), indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"wrote docs/uc5/foundry/: instructions.md, {len(tool_schemas())} tool schemas, input_schema.json, output_schema.json"
    )
    return 0


def cmd_agent_register(args) -> int:
    from .service import register_agent

    r = register_agent(args.tenant_id, args.rai_policy_id)
    print(json.dumps(r, indent=1))
    return 0 if r["tools_match"] and r["instructions_match"] else 1


def cmd_guardrails_verify(args) -> int:
    from datetime import date

    from evals.incident import guardrails_verify as gv

    rows = gv.run_offline()
    prov = {"date": date.today().isoformat(), "mode": "OFFLINE (scripted compromised agent; UC4 replay, UC6 offline)",
            "layer_tested": "application guardrails, harness, human review (Foundry guardrail: live run, later)"}  # fmt: skip
    _write("guardrails-offline", {"provenance": prov, "probes": rows}, gv.render(rows, prov))
    print(gv.render(rows, prov))
    return 0 if not any(r["unsafe"] for r in rows) else 1


def cmd_obs_report(args) -> int:
    from evals.incident.observability_report import render, run_traced

    report = run_traced(args.mode)
    _write("observability", report, render(report))
    print(f"privacy audit clean: {report['privacy_audit']['clean']}")
    return 0 if report["privacy_audit"]["clean"] else 1


def cmd_foundry_eval(args) -> int:
    from datetime import datetime

    from evals.incident import foundry_evals as fe

    outcomes, agents = fe.build_rows()
    fe.write_jsonl(fe.DATA / "outcomes.jsonl", outcomes)
    fe.write_jsonl(fe.DATA / "agent.jsonl", agents)
    local = fe.local_outcome_counts(fe.load_rows(fe.DATA / "outcomes.jsonl", fe.OUTCOME_FIELDS))
    print(
        f"wrote {len(outcomes)} outcome rows, {len(agents)} agent rows to data/incident/foundry_eval/"
    )
    print(
        "local pass counts: "
        + ", ".join(f"{k} {v['passed']}/{v['total']}" for k, v in local.items())
    )
    if not (args.run or args.fetch):
        return 0
    from app.agent.foundry_service import project_client, project_endpoint

    client = project_client(project_endpoint(), tenant_id=args.tenant_id).get_openai_client()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out = (
        fe.fetch_latest(client)
        if args.fetch
        else fe.run_all(client, args.judge, stamp, args.which, args.timeout)
    )
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"foundry-evals-{stamp}.json").write_text(
        json.dumps(out, indent=1, sort_keys=True, default=str) + "\n", "utf-8"
    )
    print(
        json.dumps(
            {
                k: {kk: vv for kk, vv in v.items() if kk != "rows"} if isinstance(v, dict) else v
                for k, v in out.items()
            },
            indent=1,
            default=str,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="dataguard-incident", description="UC5 Data Security Incident Investigation Agent"
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    backend = {"choices": ("chat-completions", "foundry-service"), "default": "chat-completions"}
    d = sub.add_parser("data").add_subparsers(dest="sub", required=True)
    d.add_parser("build").set_defaults(func=cmd_data_build)
    i = sub.add_parser("investigate", help="one incident end to end")
    i.add_argument("case")
    i.add_argument("--mode", choices=("offline", "replay", "live"), default="offline")
    i.add_argument("--backend", **backend)
    i.add_argument("--tenant-id", default=None)
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=cmd_investigate)
    e = sub.add_parser("eval", help="the frozen golden set")
    e.add_argument("--mode", choices=("offline", "replay"), default="offline")
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
    g = sub.add_parser("guardrails").add_subparsers(dest="sub", required=True)
    g.add_parser("verify", help="the adversarial suite (offline)").set_defaults(
        func=cmd_guardrails_verify
    )
    o = sub.add_parser("obs").add_subparsers(dest="sub", required=True)
    orp = o.add_parser("report")
    orp.add_argument("--mode", choices=("offline", "replay"), default="offline")
    orp.set_defaults(func=cmd_obs_report)
    fx = sub.add_parser(
        "foundry-eval", help="write the Foundry evaluation rows; --run creates them"
    )
    fx.add_argument("--run", action="store_true")
    fx.add_argument(
        "--fetch", action="store_true", help="READ-ONLY: collect the newest runs' results"
    )
    fx.add_argument("--which", choices=("outcomes", "agent", "all"), default="all")
    fx.add_argument("--judge", default="uc4-llm-medium")
    fx.add_argument("--timeout", type=float, default=900.0)
    fx.add_argument("--tenant-id", default=None)
    fx.set_defaults(func=cmd_foundry_eval)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
