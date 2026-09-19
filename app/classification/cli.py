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


def _cmd_dataset_generate(args: argparse.Namespace) -> int:
    from evals.classification.dataset.build import build_dataset, write_dataset

    bundle = load_config(args.config_dir)
    result = build_dataset(bundle, args.spec_dir)
    if not result.report.ok:
        print("DATASET INTEGRITY ERRORS (nothing written):", file=sys.stderr)
        for e in result.report.errors:
            print(f"  - {e}", file=sys.stderr)
        return 3
    out = write_dataset(result, args.out_dir)
    n = result.manifest["n_documents"]
    print(
        f"wrote {n} documents to {out} (dataset_sha256={result.manifest['dataset_sha256'][:16]}...)"
    )
    for w in result.report.warnings:
        print(f"  warning: {w}")
    flags = result.report.small_sample_flags
    if flags:
        print(f"  {len(flags)} small-sample flag(s) recorded in manifest")
    return 0


def _cmd_dataset_validate(args: argparse.Namespace) -> int:
    from evals.classification.dataset.build import (
        DatasetIntegrityError,
        build_dataset,
        load_manifest,
    )

    bundle = load_config(args.config_dir)
    try:
        result = build_dataset(bundle, args.spec_dir)
        committed = load_manifest(args.data_dir)
    except (OSError, ValueError, DatasetIntegrityError) as exc:
        print(f"DATASET INVALID: {exc}", file=sys.stderr)
        return 3
    problems = list(result.report.errors)
    # Reproducibility: regenerating from spec + seed must reproduce the committed files exactly.
    if committed["dataset_sha256"] != result.manifest["dataset_sha256"]:
        problems.append(
            "regenerated dataset differs from the committed dataset (stale or non-deterministic)"
        )
    if committed["spec_hash"] != result.manifest["spec_hash"]:
        problems.append("spec changed since the dataset was generated; re-run `dataset generate`")
    if problems:
        print("DATASET INVALID:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 3
    print(
        json.dumps(
            {
                "status": "ok",
                "n_documents": result.manifest["n_documents"],
                "dataset_sha256": result.manifest["dataset_sha256"],
                "integrity": result.report.to_dict()["stats"],
            },
            indent=2,
        )
    )
    return 0


def _cmd_dataset_stats(args: argparse.Namespace) -> int:
    from evals.classification.dataset.build import load_manifest

    print(json.dumps(load_manifest(args.data_dir)["stats"], indent=2))
    return 0


def _cmd_dataset_report(args: argparse.Namespace) -> int:
    from pathlib import Path

    from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_manifest
    from evals.classification.dataset.report import render_report

    bundle = load_config(args.config_dir)
    text = render_report(
        load_manifest(args.data_dir), bundle.policy.level_ids, bundle.policy.category_ids
    )
    out = (
        Path(args.out)
        if args.out
        else Path(DEFAULT_DATA_DIR).parents[2] / "docs/uc4/dataset-report.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return 0


def _cmd_dataset_review_sheet(args: argparse.Namespace) -> int:
    from pathlib import Path

    from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents
    from evals.classification.dataset.report import render_review_sheet

    docs = load_documents(args.data_dir)
    out = (
        Path(args.out)
        if args.out
        else Path(args.data_dir or DEFAULT_DATA_DIR) / "review/family_review_sheet.csv"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_review_sheet(docs), encoding="utf-8")
    print(f"wrote {out} ({len({d.family_id for d in docs})} families)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dataguard-uc4", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    cfg = sub.add_parser("config", help="configuration commands")
    cfg_sub = cfg.add_subparsers(dest="config_command", required=True)
    validate = cfg_sub.add_parser("validate", help="validate taxonomy/high-risk/eval config")
    validate.add_argument("--config-dir", default=None)
    validate.set_defaults(func=_cmd_config_validate)

    ds = sub.add_parser("dataset", help="synthetic dataset commands")
    ds_sub = ds.add_subparsers(dest="dataset_command", required=True)
    for name, func, helptext in [
        ("generate", _cmd_dataset_generate, "generate the dataset from spec + seed"),
        ("validate", _cmd_dataset_validate, "regenerate and verify integrity + reproducibility"),
        ("stats", _cmd_dataset_stats, "print statistics from the committed manifest"),
        ("report", _cmd_dataset_report, "write the generated dataset report (markdown)"),
        (
            "review-sheet",
            _cmd_dataset_review_sheet,
            "write a one-doc-per-family CSV for human review",
        ),
    ]:
        sp = ds_sub.add_parser(name, help=helptext)
        sp.add_argument("--config-dir", default=None)
        sp.add_argument("--spec-dir", default=None)
        sp.add_argument("--data-dir", default=None)
        if name == "generate":
            sp.add_argument("--out-dir", default=None)
        if name in ("report", "review-sheet"):
            sp.add_argument("--out", default=None)
        sp.set_defaults(func=func)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
