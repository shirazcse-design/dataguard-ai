"""`dataguard-uc4` command-line interface (Python library + CLI; no JSON API yet)."""

from __future__ import annotations

import argparse
import json
import sys

from .config_loader import ConfigError, load_config


def _cmd_config_validate(args: argparse.Namespace) -> int:
    try:
        bundle = load_config(args.config_dir)
    except ConfigError as exc:
        print(f"CONFIG INVALID: {exc}", file=sys.stderr)
        return 2
    policy = bundle.policy
    summary = {
        "status": "ok",
        "config_dir": str(bundle.config_dir),
        "versions": bundle.versions(),
        "levels": policy.level_ids,
        "categories": policy.category_ids,
        "file_hashes": bundle.file_hashes,
    }
    print(json.dumps(summary, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dataguard-uc4", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    cfg = sub.add_parser("config", help="configuration commands")
    cfg_sub = cfg.add_subparsers(dest="config_command", required=True)
    validate = cfg_sub.add_parser("validate", help="validate taxonomy/high-risk/eval config")
    validate.add_argument("--config-dir", default=None)
    validate.set_defaults(func=_cmd_config_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
