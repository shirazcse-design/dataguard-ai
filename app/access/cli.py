"""`dataguard-access`: UC3 AI Data Access Governance Agent.

data build                        regenerate data/access (deterministic)
investigate AR-002 [--mode ...]   one request end to end (replay by default)
eval [--mode ...] [--ids ...]     the frozen golden set (writes docs/uc3/results)
record [--ids ...] --label ...    LIVE: run and record agent turns for replay
agent export                      the Foundry agent's instructions, tool schemas and output schema
                                  as files for MANUAL creation in the portal (nothing is created)
obs report                        traced golden run + privacy audit (offline)
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
    o = sub.add_parser("obs").add_subparsers(dest="sub", required=True)
    orp = o.add_parser("report")
    orp.add_argument("--mode", choices=("offline", "replay"), default="replay")
    orp.set_defaults(func=cmd_obs_report)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
