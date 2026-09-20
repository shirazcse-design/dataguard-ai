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
    try:
        from pathlib import Path

        from app.classification.routing_config import load_gates, load_routing_config
        from app.llm.config import load_llm_config
        from guardrails.injection import load_injection_config
        from guardrails.input import load_input_guard_config
        from ml.classification import load_ml_config
        from observability import load_observability_config
        from rules.config import load_rules_config

        llm_cfg, llm_sha = load_llm_config(policy, args.config_dir)
        guard_cfg, guard_sha = load_injection_config(args.config_dir)
        routing_cfg, routing_sha = load_routing_config(policy, args.config_dir)
        gates_cfg, _ = load_gates(args.config_dir)
        input_cfg, _ = load_input_guard_config(args.config_dir)
        obs_cfg, _ = load_observability_config(args.config_dir)
        ml_cfg, _ = load_ml_config(policy, args.config_dir)
        rules_cfg, _ = load_rules_config(policy, args.config_dir)
        root = Path(__file__).resolve().parents[2]
        for rel in (llm_cfg.prompt.file, llm_cfg.prompt.fewshot_file):
            if not (root / rel).exists():
                raise ConfigError(f"missing referenced file {rel}")
    except ConfigError as exc:
        print(f"CONFIG INVALID: {exc}", file=sys.stderr)
        return 2
    summary = {
        "status": "ok",
        "config_dir": str(bundle.config_dir),
        "versions": {
            **bundle.versions(),
            "llm": llm_cfg.llm_version,
            "prompt": llm_cfg.prompt.version,
            "injection_guardrail": guard_cfg.guardrail_version,
            "input_guardrail": input_cfg.guardrail_version,
            "routing": routing_cfg.routing_version,
            "gates": gates_cfg.gates_version,
            "observability": obs_cfg.observability_version,
            "ml": ml_cfg.ml_version,
            "ruleset": rules_cfg.ruleset_version,
        },
        "llm_config_sha256": llm_sha,
        "guardrail_config_sha256": guard_sha,
        "routing_config_sha256": routing_sha,
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
    if args.classifier == "hybrid":
        from app.classification.hybrid import build_hybrid_classifier

        return build_hybrid_classifier(
            bundle,
            variant=args.hybrid_variant,
            data_dir=args.data_dir,
            config_dir=args.config_dir,
            llm_mode=args.llm_mode,
            cache_dir=args.llm_cache_dir,
        )
    if args.classifier == "llm":
        from app.llm import build_llm_classifier

        return build_llm_classifier(
            bundle,
            tier=args.llm_tier,
            mode=args.llm_mode,
            model_id=args.llm_model_id,
            cache_dir=args.llm_cache_dir,
            data_dir=args.data_dir,
            config_dir=args.config_dir,
        )
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
    if args.trace_out:
        from pathlib import Path

        from observability import (
            JsonlSink,
            TracedClassifier,
            build_tracer,
            load_observability_config,
        )

        obs_cfg, _ = load_observability_config(args.config_dir)
        Path(args.trace_out).unlink(missing_ok=True)  # a fresh trace file per run
        tracer, salt = build_tracer(obs_cfg, [JsonlSink(args.trace_out)])
        clf = TracedClassifier(clf, tracer, salt)
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


def _parse_tier_models(pairs: list[str]) -> dict[str, str] | None:
    out: dict[str, str] = {}
    for item in pairs:
        tier, sep, model = item.partition("=")
        if not sep or tier not in ("small", "mid", "large") or not model:
            print(f"--tier expects small|mid|large=<model-id>, got {item!r}", file=sys.stderr)
            return None
        out[tier] = model
    return out


def _cmd_llm_report(args: argparse.Namespace) -> int:
    from pathlib import Path

    from app.llm import MockLLMClient, build_llm_classifier
    from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents, load_manifest
    from evals.classification.evaluate import evaluate, git_info
    from evals.classification.llm_report import build_llm_report
    from evals.classification.lock import DEVELOPMENT_SPLITS

    models = _parse_tier_models(args.tier)
    if models is None:
        return 2
    bundle = load_config(args.config_dir)
    docs = load_documents(args.data_dir, splits=list(DEVELOPMENT_SPLITS))
    by_split = {s: [d for d in docs if d.split == s] for s in DEVELOPMENT_SPLITS}
    manifest = load_manifest(args.data_dir)
    common = dict(data_dir=args.data_dir, config_dir=args.config_dir, cache_dir=args.llm_cache_dir)
    probe = build_llm_classifier(
        bundle, tier="small", mode="replay", client=MockLLMClient([]), **common
    )
    tiers = {
        t: build_llm_classifier(bundle, tier=t, mode="replay", model_id=m, **common)
        for t, m in models.items()
    }

    def rules_ml():
        from ml.classification import build_ml_classifier
        from rules import build_rules_classifier

        dev = by_split["dev"]
        rules = evaluate(build_rules_classifier(bundle, args.config_dir), dev, bundle, manifest)
        ml_clf = build_ml_classifier(bundle, data_dir=args.data_dir, config_dir=args.config_dir)
        ml = evaluate(ml_clf, dev, bundle, manifest)
        return rules, ml

    text = build_llm_report(
        bundle, probe, tiers, by_split, manifest, git_info(), rules_ml_factory=rules_ml
    )
    default_out = Path(DEFAULT_DATA_DIR).parents[2] / "docs/uc4/results/llm-baseline.md"
    out = Path(args.out) if args.out else default_out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return 0


def _cmd_hybrid_report(args: argparse.Namespace) -> int:
    from pathlib import Path

    from app.classification.hybrid import HybridClassifier, build_hybrid_classifier
    from app.classification.routing_config import load_gates, load_routing_config
    from app.llm import build_llm_classifier
    from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents, load_manifest
    from evals.classification.evaluate import git_info
    from evals.classification.hybrid_report import build_hybrid_report
    from evals.classification.lock import DEVELOPMENT_SPLITS
    from guardrails.injection import InjectionScanner, load_injection_config
    from ml.classification import build_ml_classifier
    from rules import build_rules_classifier

    bundle = load_config(args.config_dir)
    docs = load_documents(args.data_dir, splits=list(DEVELOPMENT_SPLITS))
    dev = [d for d in docs if d.split == "dev"]
    routing, sha = load_routing_config(bundle.policy, args.config_dir)
    gates, _ = load_gates(args.config_dir)
    data_dir = args.data_dir or DEFAULT_DATA_DIR
    comp = {
        "rules": build_rules_classifier(bundle, args.config_dir),
        "ml": build_ml_classifier(bundle, data_dir=args.data_dir, config_dir=args.config_dir),
        "llms": {
            t: build_llm_classifier(
                bundle, tier=t, mode="replay", data_dir=data_dir, config_dir=args.config_dir,
                cache_dir=args.llm_cache_dir,
            )
            for t in ("small", "mid", "large")
        },
        "scanner": InjectionScanner(load_injection_config(args.config_dir)[0]),
    }  # fmt: skip

    def make(name: str, components: dict | None = None) -> HybridClassifier:
        return build_hybrid_classifier(
            bundle, variant=name, data_dir=args.data_dir, config_dir=args.config_dir,
            cache_dir=args.llm_cache_dir, components=components or comp,
        )  # fmt: skip

    text = build_hybrid_report(
        bundle, routing, sha, gates, comp, dev, load_manifest(args.data_dir), git_info(), make
    )
    default_out = Path(DEFAULT_DATA_DIR).parents[2] / "docs/uc4/results/hybrid-baseline.md"
    out = Path(args.out) if args.out else default_out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return 0


def _cmd_obs_summarize(args: argparse.Namespace) -> int:
    from observability import read_jsonl, summarize

    spans = read_jsonl(args.spans)
    text = json.dumps(summarize(spans), indent=2, sort_keys=True)
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def _cmd_obs_audit(args: argparse.Namespace) -> int:
    """Privacy gate: exit 1 if any span output contains document text or a sensitive value."""
    from pathlib import Path

    from evals.classification.lock import DEVELOPMENT_SPLITS
    from observability import audit_spans

    splits = list(DEVELOPMENT_SPLITS) if args.split == "all" else args.split.split(",")
    if "test" in splits:
        print("the locked test split is not audited by this command", file=sys.stderr)
        return 2
    docs = _load_split_docs(args, splits)
    res = audit_spans(Path(args.spans).read_text(encoding="utf-8"), docs)
    print(
        f"documents {res.documents}; content windows {res.windows_checked}; evidence spans "
        f"{res.evidence_spans_checked}; filenames {res.filenames_checked}; "
        f"patterns {res.pattern_checks}"
    )
    if res.clean:
        print("PRIVACY AUDIT PASSED: no document text or sensitive value in the spans")
        return 0
    print(f"PRIVACY AUDIT FAILED: {len(res.leaks)} leak(s): {res.leaks[:10]}", file=sys.stderr)
    return 1


def _cmd_obs_report(args: argparse.Namespace) -> int:
    from pathlib import Path

    from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents
    from evals.classification.evaluate import git_info
    from evals.classification.lock import DEVELOPMENT_SPLITS
    from evals.classification.obs_report import build_obs_report

    bundle = load_config(args.config_dir)
    docs = load_documents(args.data_dir, splits=list(DEVELOPMENT_SPLITS))
    dev = [d for d in docs if d.split == "dev"]
    text = build_obs_report(bundle, dev, git_info())
    default_out = Path(DEFAULT_DATA_DIR).parents[2] / "docs/uc4/results/observability-baseline.md"
    out = Path(args.out) if args.out else default_out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return 0


def _cmd_obs_overhead(args: argparse.Namespace) -> int:
    """Measure (not assume) what tracing costs per request. Numbers vary run to run."""
    import time

    from app.classification.hybrid import build_hybrid_classifier
    from evals.classification.lock import DEVELOPMENT_SPLITS
    from observability import MemorySink, TracedClassifier, build_tracer, load_observability_config
    from rules import build_rules_classifier

    bundle = load_config(args.config_dir)
    dev = [d for d in _load_split_docs(args, list(DEVELOPMENT_SPLITS)) if d.split == "dev"]
    reqs = [d.to_request() for d in dev]
    cfg, _ = load_observability_config(args.config_dir)
    for label, clf in (
        ("rules only", build_rules_classifier(bundle, args.config_dir)),
        ("hybrid default (replayed LLM)", build_hybrid_classifier(bundle, variant="default")),
    ):
        tracer, salt = build_tracer(cfg, [MemorySink()])
        traced = TracedClassifier(clf, tracer, salt)
        best = {}
        for name, target in (("off", clf), ("on", traced)):
            times = []
            for _ in range(args.repeats):
                t0 = time.perf_counter()
                for r in reqs:
                    target.classify(r)
                times.append((time.perf_counter() - t0) / len(reqs) * 1e6)
            best[name] = min(times)
        print(
            f"{label}: {best['off']:.0f} us/request without tracing, {best['on']:.0f} us with; "
            f"overhead {best['on'] - best['off']:.0f} us/request (best of {args.repeats}, "
            f"{len(reqs)} documents)"
        )
    return 0


def _cmd_llm_fewshot(args: argparse.Namespace) -> int:
    """Regenerate the few-shot id file from the pre-registered rule (train only)."""
    from pathlib import Path

    from app.llm.config import load_llm_config
    from app.llm.fewshot import build_fewshot_file, write_fewshot_file

    bundle = load_config(args.config_dir)
    cfg, _ = load_llm_config(bundle.policy, args.config_dir)
    train = _load_split_docs(args, ["train"])
    fs = build_fewshot_file(train, bundle.policy.category_ids, cfg.seed)
    out = (
        Path(args.out)
        if args.out
        else Path(__file__).resolve().parents[2] / cfg.prompt.fewshot_file
    )
    write_fewshot_file(fs, out)
    print(f"wrote {out} ({len(fs.examples)} examples, all from train)")
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
        "--classifier",
        choices=["oracle", "majority", "random", "rules", "ml", "llm", "hybrid"],
        required=True,
    )
    run.add_argument(
        "--hybrid-variant",
        default=None,
        help="named variant from config/routing/routing.v1.yaml (default: the configured default)",
    )
    run.add_argument(
        "--trace-out",
        default=None,
        help="write one span per stage as JSONL (deny-by-default redaction; no document text)",
    )
    run.add_argument("--llm-tier", choices=["small", "mid", "large"], default="small")
    run.add_argument(
        "--llm-mode",
        choices=["replay", "record", "foundry"],
        default="replay",
        help="replay = recorded responses only (no network); record = call the provider and store "
        "the responses for replay; foundry = call the provider without storing",
    )
    run.add_argument("--llm-model-id", default=None, help="replay/record cache model id")
    run.add_argument("--llm-cache-dir", default=None, help="replay cache (default data/llm_cache)")
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

    llm = sub.add_parser("llm", help="LLM classifier commands (Approach C)")
    llm_sub = llm.add_subparsers(dest="llm_command", required=True)
    lrp = llm_sub.add_parser("report", help="write the LLM status/results (development splits)")
    lrp.add_argument(
        "--tier",
        action="append",
        default=[],
        metavar="TIER=MODEL_ID",
        help="replay a recorded run: small|mid|large=<model id in the cache> (repeatable)",
    )
    lrp.add_argument("--llm-cache-dir", default=None)
    lrp.add_argument("--out", default=None)
    lrp.add_argument("--config-dir", default=None)
    lrp.add_argument("--data-dir", default=None)
    lrp.set_defaults(func=_cmd_llm_report)
    obs = sub.add_parser("obs", help="observability commands")
    obs_sub = obs.add_subparsers(dest="obs_command", required=True)
    osum = obs_sub.add_parser("summarize", help="derived metrics from a span JSONL file")
    osum.add_argument("--spans", required=True)
    osum.add_argument("--out", default=None)
    osum.set_defaults(func=_cmd_obs_summarize)
    orp = obs_sub.add_parser("report", help="write the observability + failure-matrix results")
    orp.add_argument("--out", default=None)
    orp.add_argument("--config-dir", default=None)
    orp.add_argument("--data-dir", default=None)
    orp.set_defaults(func=_cmd_obs_report)
    oov = obs_sub.add_parser("overhead", help="measure the per-request cost of tracing")
    oov.add_argument("--repeats", type=int, default=5)
    oov.add_argument("--config-dir", default=None)
    oov.add_argument("--data-dir", default=None)
    oov.set_defaults(func=_cmd_obs_overhead)
    oaud = obs_sub.add_parser("audit", help="fail if a span file leaks document text")
    oaud.add_argument("--spans", required=True)
    oaud.add_argument("--split", default="dev", help="development splits to audit against")
    oaud.add_argument("--data-dir", default=None)
    oaud.set_defaults(func=_cmd_obs_audit)
    hyb = sub.add_parser("hybrid", help="hybrid routing commands (Approach D)")
    hyb_sub = hyb.add_subparsers(dest="hybrid_command", required=True)
    hrp = hyb_sub.add_parser("report", help="write the hybrid results (development split, replay)")
    hrp.add_argument("--llm-cache-dir", default=None)
    hrp.add_argument("--out", default=None)
    hrp.add_argument("--config-dir", default=None)
    hrp.add_argument("--data-dir", default=None)
    hrp.set_defaults(func=_cmd_hybrid_report)
    lfs = llm_sub.add_parser("fewshot", help="regenerate the few-shot id file (train only)")
    lfs.add_argument("--out", default=None)
    lfs.add_argument("--config-dir", default=None)
    lfs.add_argument("--data-dir", default=None)
    lfs.set_defaults(func=_cmd_llm_fewshot)

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
