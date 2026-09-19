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
    from evals.classification.dataset.schema import SPLIT_NAMES
    from evals.classification.lock import LockedTestAuthorization

    # Label review needs every split; the sheet contains labels, never evaluation results.
    docs = load_documents(
        args.data_dir,
        splits=list(SPLIT_NAMES),
        locked_test_authorization=LockedTestAuthorization.now("dataset review-sheet"),
    )
    out = (
        Path(args.out)
        if args.out
        else Path(args.data_dir or DEFAULT_DATA_DIR) / "review/family_review_sheet.csv"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_review_sheet(docs), encoding="utf-8")
    print(f"wrote {out} ({len({d.family_id for d in docs})} families)")
    return 0


def _load_split_docs(args: argparse.Namespace, splits: list[str], authorization=None):
    from evals.classification.dataset.build import load_documents

    return load_documents(args.data_dir, splits=splits, locked_test_authorization=authorization)


def _resolve_splits(spec: str) -> list[str] | None:
    """`all` means the DEVELOPMENT splits only. Returns None if a name is unknown."""
    from evals.classification.dataset.schema import SPLIT_NAMES
    from evals.classification.lock import DEVELOPMENT_SPLITS

    splits = list(DEVELOPMENT_SPLITS) if spec == "all" else spec.split(",")
    return None if any(sp not in SPLIT_NAMES for sp in splits) else splits


def _authorize_locked_test(args: argparse.Namespace, splits: list[str]):
    """Return a LockedTestAuthorization if the locked split is requested AND the flag is given.

    Prints the warning banner when authorised; returns the sentinel "DENIED" if the split is
    requested without the flag.
    """
    from evals.classification.lock import (
        ALLOW_FLAG,
        BANNER,
        LOCKED_SPLIT,
        LockedTestAuthorization,
    )

    if LOCKED_SPLIT not in splits:
        return None
    if not args.allow_locked_test:
        print(
            f"ERROR: the '{LOCKED_SPLIT}' split is locked (report-only). Development commands use "
            f"train, calibration and dev. To read it deliberately, add {ALLOW_FLAG}.",
            file=sys.stderr,
        )
        return "DENIED"
    print(BANNER, file=sys.stderr)
    return LockedTestAuthorization.now()


def _build_classifier(args: argparse.Namespace, bundle, docs):
    from evals.classification.baselines import (
        MajorityClassifier,
        OracleClassifier,
        RandomClassifier,
    )

    policy = bundle.policy
    if args.classifier == "ml":
        from ml.classification import build_ml_classifier

        return build_ml_classifier(bundle, data_dir=args.data_dir, config_dir=args.config_dir)
    if args.classifier == "rules":
        from rules import build_rules_classifier

        return build_rules_classifier(bundle, args.config_dir)
    if args.classifier == "oracle":
        return OracleClassifier.from_docs(docs, policy)
    if args.classifier == "majority":
        return MajorityClassifier.from_docs(_load_split_docs(args, ["train"]), policy)
    return RandomClassifier(args.seed, policy)


def _cmd_eval_run(args: argparse.Namespace) -> int:
    from evals.classification.dataset.build import load_manifest
    from evals.classification.evaluate import evaluate
    from evals.classification.reporting import append_access_log, write_run

    bundle = load_config(args.config_dir)
    splits = _resolve_splits(args.split)
    if splits is None:
        print(
            f"unknown split in {args.split!r}; choose from train, calibration, dev, test "
            "(or 'all' = train,calibration,dev)",
            file=sys.stderr,
        )
        return 2
    authorization = _authorize_locked_test(args, splits)
    if authorization == "DENIED":
        return 2
    docs = _load_split_docs(args, splits, authorization)
    clf = _build_classifier(args, bundle, docs)
    cli_args = {k: v for k, v in vars(args).items() if k != "func"}
    result = evaluate(
        clf,
        docs,
        bundle,
        load_manifest(args.data_dir),
        cli_args=cli_args,
        locked_test_authorization=authorization,
    )
    out = write_run(result, args.runs_dir)
    if authorization is not None:
        log = append_access_log(result, args.access_log)
        print(f"locked-test access recorded in {log}", file=sys.stderr)
        if result.manifest["git"]["dirty"]:
            print(
                "WARNING: the working tree has uncommitted changes; the recorded git SHA does not "
                "fully identify the code that produced this result.",
                file=sys.stderr,
            )
    m = result.metrics["headline"]
    stats = m["confidence_intervals"]["statistics"]
    print(f"run_id: {result.run_id}")
    print(f"artifacts: {out}")
    print(f"dataset labels: {result.manifest['dataset']['label_status']}")
    print(f"independent families in the headline intervals: {m['confidence_intervals']['n_units']}")
    for label, key in [
        ("level macro-F1", "level_macro_f1"),
        ("category macro-F1", "category_macro_f1"),
        ("high-risk recall", "high_risk_recall"),
        ("high-risk precision", "high_risk_precision"),
        ("high-risk F1", "high_risk_f1"),
        ("high-risk FPR", "high_risk_false_positive_rate"),
    ]:
        s = stats[key]
        if s["point"] is None:
            print(f"  {label:20s} n/a")
        else:
            print(f"  {label:20s} {s['point']:.3f}  [{s['ci_low']:.3f}, {s['ci_high']:.3f}]")
    print(f"  {'review rate':20s} {m['metrics']['coverage']['review_rate']:.3f}")
    sm = m["small_sample_labels"]
    for axis in ("levels", "categories"):
        for label, n in sm[axis].items():
            noun = "level" if axis == "levels" else "category"
            print(f"  SMALL_SAMPLE: {noun} {label} has only {n} gold positives")
    return 0


def _cmd_rules_analyze(args: argparse.Namespace) -> int:
    from pathlib import Path

    from evals.classification.rules_analysis import analyze, render
    from rules import build_rules_classifier

    bundle = load_config(args.config_dir)
    splits = _resolve_splits(args.split)
    if splits is None or "test" in splits:
        print("rules analyze works on train, calibration and dev only", file=sys.stderr)
        return 2
    docs = _load_split_docs(args, splits)
    clf = build_rules_classifier(bundle, args.config_dir)
    text = render(analyze(clf, docs, bundle.policy), f"Rules error analysis ({', '.join(splits)})")
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def _cmd_rules_report(args: argparse.Namespace) -> int:
    from pathlib import Path

    from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents, load_manifest
    from evals.classification.evaluate import git_info
    from evals.classification.lock import DEVELOPMENT_SPLITS
    from evals.classification.rules_report import build_rules_report
    from rules import build_rules_classifier

    bundle = load_config(args.config_dir)
    docs = load_documents(args.data_dir, splits=list(DEVELOPMENT_SPLITS))
    by_split = {s: [d for d in docs if d.split == s] for s in DEVELOPMENT_SPLITS}
    clf = build_rules_classifier(bundle, args.config_dir)
    text = build_rules_report(bundle, clf, by_split, load_manifest(args.data_dir), git_info())
    default_out = Path(DEFAULT_DATA_DIR).parents[2] / "docs/uc4/results/rules-baseline.md"
    out = Path(args.out) if args.out else default_out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return 0


def _cmd_ml_select(args: argparse.Namespace) -> int:
    from pathlib import Path

    from evals.classification.lock import DEVELOPMENT_SPLITS
    from ml.classification import load_ml_config
    from ml.classification.selection import cross_validate, select_c

    bundle = load_config(args.config_dir)
    cfg, _ = load_ml_config(bundle.policy, args.config_dir)
    from evals.classification.dataset.build import load_documents

    train = load_documents(args.data_dir, splits=["train"])
    assert all(d.split in DEVELOPMENT_SPLITS for d in train)
    rows = cross_validate(cfg, train, bundle.policy.level_ids, bundle.policy.category_ids)
    result = {
        "protocol": "grouped 5-fold CV on TRAIN only (folds group by scenario family)",
        "n_train_documents": len(train),
        "n_train_families": len({d.group_id for d in train}),
        "rows": rows,
        "selected": {
            "level_head_C": select_c(rows, "level_macro_f1"),
            "category_head_C": select_c(rows, "category_macro_f1"),
        },
    }
    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    print(text)
    return 0


def _cmd_ml_report(args: argparse.Namespace) -> int:
    from pathlib import Path

    from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents, load_manifest
    from evals.classification.evaluate import git_info
    from evals.classification.lock import DEVELOPMENT_SPLITS
    from evals.classification.ml_report import build_ml_report
    from ml.classification import build_ml_classifier

    bundle = load_config(args.config_dir)
    docs = load_documents(args.data_dir, splits=list(DEVELOPMENT_SPLITS))
    by_split = {s: [d for d in docs if d.split == s] for s in DEVELOPMENT_SPLITS}
    clf = build_ml_classifier(bundle, data_dir=args.data_dir, config_dir=args.config_dir)
    text = build_ml_report(bundle, clf, by_split, load_manifest(args.data_dir), git_info())
    default_out = Path(DEFAULT_DATA_DIR).parents[2] / "docs/uc4/results/ml-baseline.md"
    out = Path(args.out) if args.out else default_out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return 0


def _cmd_eval_validate(args: argparse.Namespace) -> int:
    from pathlib import Path

    import numpy
    import sklearn

    from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents, load_manifest
    from evals.classification.evaluate import git_info
    from evals.classification.lock import DEVELOPMENT_SPLITS, LOCKED_SPLIT
    from evals.classification.validation import (
        render_validation_report,
        validate_harness,
        validation_to_json,
    )

    bundle = load_config(args.config_dir)
    splits = list(DEVELOPMENT_SPLITS) + ([LOCKED_SPLIT] if args.allow_locked_test else [])
    authorization = _authorize_locked_test(args, splits)
    docs = load_documents(args.data_dir, splits=splits, locked_test_authorization=authorization)
    by_split = {s: [d for d in docs if d.split == s] for s in splits}
    manifest = load_manifest(args.data_dir)
    result = validate_harness(by_split, bundle, manifest, locked_test_authorization=authorization)
    env = {
        **git_info(),
        "python": sys.version.split()[0],
        "numpy": numpy.__version__,
        "scikit_learn": sklearn.__version__,
        "splits": splits,
    }
    default_out = Path(DEFAULT_DATA_DIR).parents[2] / "docs/uc4/results"
    out_dir = Path(args.out_dir) if args.out_dir else default_out
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "harness-validation.md").write_text(
        render_validation_report(result, manifest, env), encoding="utf-8"
    )
    (out_dir / "harness-validation.json").write_text(
        json.dumps(validation_to_json(result), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    passed = sum(c.passed for c in result.checks)
    print(f"harness validation: {passed}/{len(result.checks)} checks passed -> {out_dir}")
    for c in result.checks:
        if not c.passed:
            print(
                f"  FAIL {c.suite} [{c.split}] {c.name}: "
                f"expected {c.expected} observed {c.observed}",
                file=sys.stderr,
            )
    return 0 if result.ok else 4


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

    ev = sub.add_parser("eval", help="evaluation harness commands")
    ev_sub = ev.add_subparsers(dest="eval_command", required=True)
    run = ev_sub.add_parser("run", help="evaluate a sanity classifier on dataset splits")
    run.add_argument(
        "--classifier", choices=["oracle", "majority", "random", "rules", "ml"], required=True
    )
    run.add_argument(
        "--split",
        default="dev",
        help="comma-separated splits from train,calibration,dev; 'all' = those three. The locked "
        "'test' split additionally requires --allow-locked-test (default: dev)",
    )
    run.add_argument(
        "--allow-locked-test",
        action="store_true",
        help="explicitly authorise reading the locked test split (report-only; audited)",
    )
    run.add_argument(
        "--access-log", default=None, help="locked-test access log (default: tracked audit file)"
    )
    run.add_argument("--seed", type=int, default=20260918, help="seed for the random baseline")
    run.add_argument("--runs-dir", default=None)
    run.add_argument("--config-dir", default=None)
    run.add_argument("--data-dir", default=None)
    run.set_defaults(func=_cmd_eval_run)
    rules = sub.add_parser("rules", help="Rules Engine commands")
    rules_sub = rules.add_subparsers(dest="rules_command", required=True)
    an = rules_sub.add_parser("analyze", help="per-family error analysis on development splits")
    an.add_argument("--split", default="train")
    an.add_argument("--out", default=None)
    an.add_argument("--config-dir", default=None)
    an.add_argument("--data-dir", default=None)
    an.set_defaults(func=_cmd_rules_analyze)
    ml = sub.add_parser("ml", help="supervised ML classifier commands")
    ml_sub = ml.add_subparsers(dest="ml_command", required=True)
    sel = ml_sub.add_parser("select", help="grouped CV hyperparameter selection on train")
    sel.add_argument("--out", default=None)
    sel.add_argument("--config-dir", default=None)
    sel.add_argument("--data-dir", default=None)
    sel.set_defaults(func=_cmd_ml_select)
    mrp = ml_sub.add_parser("report", help="write the ML baseline results (development splits)")
    mrp.add_argument("--out", default=None)
    mrp.add_argument("--config-dir", default=None)
    mrp.add_argument("--data-dir", default=None)
    mrp.set_defaults(func=_cmd_ml_report)

    rp = rules_sub.add_parser(
        "report", help="write the Rules baseline results (development splits)"
    )
    rp.add_argument("--out", default=None)
    rp.add_argument("--config-dir", default=None)
    rp.add_argument("--data-dir", default=None)
    rp.set_defaults(func=_cmd_rules_report)

    val = ev_sub.add_parser(
        "validate-harness", help="validate the harness with oracle/majority/random"
    )
    val.add_argument("--out-dir", default=None)
    val.add_argument(
        "--allow-locked-test",
        action="store_true",
        help="also validate the harness on the locked test split (sanity baselines only)",
    )
    val.add_argument("--config-dir", default=None)
    val.add_argument("--data-dir", default=None)
    val.set_defaults(func=_cmd_eval_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
