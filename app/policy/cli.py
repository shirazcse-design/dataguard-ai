"""`dataguard-policy`: the Data Security Policy Copilot (UC6) command line.

    dataguard-policy ingest                         corpus stats + fingerprint (no network)
    dataguard-policy embed record                   LIVE: record chunk + golden-query embeddings
    dataguard-policy eval retrieval [--embed-mode replay|offline]
    dataguard-policy ask "question" [--level naive|advanced|agentic] [--mode replay|offline|live]
    dataguard-policy answers record [--levels naive,advanced,agentic]   LIVE: record answers
    dataguard-policy eval answers [--mode replay|offline]

`--embed-mode`: `replay` (default) uses recorded vectors only and needs no Azure; `offline` uses the
non-semantic hashing embedder and says so in every output; `record` (embed command only) calls the
embedding deployment you created in Foundry and stores the vectors for replay.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.classification.config_loader import ConfigError, default_config_dir, read_yaml
from app.llm.config import FoundryConfig

from .config import PolicyConfig, load_policy_config
from .corpus import Corpus, CorpusError, load_corpus
from .embeddings import CachedEmbedder, EmbeddingError, FoundryEmbeddingClient, HashingEmbedder
from .query import process_query

REPO = Path(__file__).resolve().parents[2]


def _load() -> tuple[PolicyConfig, str, Corpus]:
    cfg, digest = load_policy_config()
    return cfg, digest, load_corpus(REPO / cfg.corpus.dir, cfg.chunking)


def _foundry_config() -> FoundryConfig:
    data, _ = read_yaml(default_config_dir() / "llm" / "llm.v1.yaml")
    return FoundryConfig.model_validate(data["foundry"])


def build_embedder(cfg: PolicyConfig, mode: str):
    if mode == "offline":
        return HashingEmbedder()
    inner = None
    if mode == "record":
        inner = FoundryEmbeddingClient(_foundry_config(), cfg.embedding)
    return CachedEmbedder(
        REPO / cfg.embedding.cache_dir,
        cfg.embedding.replay_model_id,
        cfg.embedding.dimensions,
        inner=inner,
    )


def query_texts(cfg: PolicyConfig, questions: list[str]) -> list[str]:
    """Every query text any retrieval variant embeds: the raw question and its processed form."""
    out: list[str] = []
    for q in questions:
        for enabled in (False, True):
            text = process_query(q, cfg.query.expansions, enabled).text
            if text not in out:
                out.append(text)
    return out


def cmd_ingest(_: argparse.Namespace) -> int:
    cfg, digest, corpus = _load()
    by_status: dict[str, int] = {}
    for c in corpus.chunks:
        by_status[c.status] = by_status.get(c.status, 0) + 1
    words = [len(c.body.split()) for c in corpus.chunks]
    print(
        json.dumps(
            {
                "documents": len(corpus.docs),
                "policies": sorted({d.policy_id for d in corpus.docs}),
                "chunks": len(corpus.chunks),
                "chunks_by_status": by_status,
                "words_per_chunk": {"min": min(words), "max": max(words)},
                "corpus_fingerprint": corpus.fingerprint(),
                "config_sha256": digest,
            },
            indent=1,
        )
    )
    return 0


def cmd_embed_record(_: argparse.Namespace) -> int:
    from evals.policy.golden import load_golden

    cfg, _, corpus = _load()
    items, _ = load_golden()
    embedder = build_embedder(cfg, "record")
    texts = [c.index_text for c in corpus.chunks] + query_texts(cfg, [i.question for i in items])
    embedder.embed(texts)
    prov = embedder.provenance
    print(
        json.dumps(
            {
                "recorded_new": embedder.recorded,
                "total_texts": len(texts),
                "dimensions": prov.get("dimensions"),
                "cache_model_id": embedder.model_id,
                "corpus_fingerprint": corpus.fingerprint(),
                "tokens_used": getattr(embedder.inner, "tokens_used", None),
            },
            indent=1,
        )
    )
    return 0


def cmd_eval_retrieval(args: argparse.Namespace) -> int:
    from evals.policy.golden import check_against_corpus, load_golden
    from evals.policy.report import write_report
    from evals.policy.retrieval_eval import evaluate_variant, variants

    from .retrieval import Retriever

    cfg, digest, corpus = _load()
    items, golden_sha = load_golden()
    problems = check_against_corpus(items, corpus)
    if problems:
        print("golden set does not match the corpus:\n" + "\n".join(problems), file=sys.stderr)
        return 2
    retriever = Retriever(corpus, cfg, build_embedder(cfg, args.embed_mode))
    if retriever.index_status != "ok":
        print(
            f"dense index unavailable ({retriever.index_status}); run `dataguard-policy embed "
            "record` after the embedding deployment exists, or use --embed-mode offline",
            file=sys.stderr,
        )
        return 2
    report = {
        "provenance": {
            "embed_mode": args.embed_mode,
            "embedding_model_id": retriever.embedding_model_id,
            "golden_items": len(items),
            "scored_items": sum(1 for i in items if i.expected_sections),
            "golden_sha256": golden_sha,
            "corpus_fingerprint": corpus.fingerprint(),
            "chunks": len(corpus.chunks),
            "config_sha256": digest,
        },
        "variants": {
            name: evaluate_variant(retriever, items, level) for name, level in variants(cfg).items()
        },
    }
    if args.no_write:
        overall = {n: v["summary"]["overall"] for n, v in report["variants"].items()}
        print(json.dumps(overall, indent=1))
        return 0
    js, md = write_report(report)
    print(f"wrote {md.relative_to(REPO)} and {js.relative_to(REPO)}")
    return 0


def cmd_eval_answers(args: argparse.Namespace) -> int:
    from evals.policy.answer_eval import evaluate_level
    from evals.policy.golden import load_golden
    from evals.policy.report import write_answers_report

    from .service import build_copilot

    cfg, digest, corpus = _load()
    items, golden_sha = load_golden()
    copilot = build_copilot(args.mode)
    levels = [lv.strip() for lv in args.levels.split(",") if lv.strip()]
    report = {
        "provenance": {
            "mode": args.mode,
            "generator": copilot.generator.model_id,
            "embedding_model_id": copilot.retriever.embedding_model_id,
            "golden_items": len(items),
            "golden_sha256": golden_sha,
            "corpus_fingerprint": corpus.fingerprint(),
            "config_sha256": digest,
            "prompt_version": cfg.generation.prompt_version,
        },
        "levels": {lv: evaluate_level(copilot, items, lv) for lv in levels},
    }
    if args.no_write:
        out = {lv: v["summary"] for lv, v in report["levels"].items()}
        print(json.dumps(out, indent=1))
        return 0
    js, md = write_answers_report(report)
    print(f"wrote {md.relative_to(REPO)} and {js.relative_to(REPO)}")
    return 0


def cmd_obs_report(args: argparse.Namespace) -> int:
    from evals.policy.golden import load_golden
    from evals.policy.observability_report import (
        privacy_audit,
        render_markdown,
        run_traced,
        summarise,
    )
    from evals.policy.report import RESULTS_DIR

    items, _ = load_golden()
    levels = [lv.strip() for lv in args.levels.split(",") if lv.strip()]
    spans, copilot = run_traced(args.mode, levels, items)
    report = {
        "mode": args.mode,
        "levels": levels,
        "privacy_audit": privacy_audit(spans, items, copilot.corpus),
        "telemetry": summarise(spans),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "observability.json").write_text(
        json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    (RESULTS_DIR / "observability.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"privacy audit clean: {report['privacy_audit']['clean']}; wrote observability.md")
    return 0 if report["privacy_audit"]["clean"] else 5


def cmd_agent_register(args: argparse.Namespace) -> int:
    """LIVE: create a new version of dataguard-policy-copilot in Foundry Agent Service."""
    from .service import load_llm_config, register_policy_agent

    cfg, _, _ = _load()
    print(json.dumps(register_policy_agent(cfg, load_llm_config(), args.tenant_id), indent=1))
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from .service import build_copilot

    answer = build_copilot(
        args.mode, agent_backend=args.agent_backend, tenant_id=args.tenant_id
    ).answer(args.question, args.level)
    if args.json:
        print(answer.model_dump_json(indent=1))
        return 0
    print(f"[{answer.mode.upper()}] level={answer.level} status={answer.status}")
    for c in answer.claims:
        mark = "verified" if c.verified else "UNVERIFIED"
        print(f"- {c.text}  [{c.citation or c.evidence_id}, {mark}]")
    for c in answer.dropped_claims:
        print(f"  dropped ({c.drop_reason}): cited {c.evidence_id}")
    for k in answer.conflicts:
        print(f"conflict ({k.kind}, {k.resolution}): {', '.join(k.citations)}")
    if answer.review.required:
        print(f"human review: {', '.join(answer.review.reasons)}")
    if answer.agent:
        for st in answer.agent["steps"]:
            print(f"  agent step {st['step']}: {st['tool']} ok={st['ok']} -> {st['evidence_ids']}")
        print(f"  agent stopped: {answer.agent['stopped_reason']}")
    print("stages: " + " -> ".join(f"{s.name}:{s.status}" for s in answer.stages))
    return 0


def cmd_answers_record(args: argparse.Namespace) -> int:
    """Answer every golden question at each level in `record` mode. Prints counts only: no
    question, policy or answer text."""
    from evals.policy.golden import load_golden

    from .service import build_copilot

    levels = [lv.strip() for lv in args.levels.split(",") if lv.strip()]
    items, _ = load_golden()
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",")}
        items = [i for i in items if i.id in wanted]
    copilot = build_copilot("record", agent_backend=args.agent_backend, tenant_id=args.tenant_id)
    summary: dict[str, dict] = {}
    for level in levels:
        counts: dict[str, int] = {}
        stats = {"llm_calls": 0, "replayed": 0, "tokens_in": 0, "tokens_out": 0, "errors": {}}
        for item in items:
            a = copilot.answer(item.question, level)
            counts[a.status] = counts.get(a.status, 0) + 1
            if args.ids:  # a named subset: per-item outcome (ids, statuses and citations only)
                stats.setdefault("items", {})[item.id] = {
                    "status": a.status,
                    "citations": a.citations,
                    "tools": [st["tool"] for st in (a.agent or {}).get("steps", [])],
                    "dense": sorted(set((a.agent or {}).get("search_dense_status", []))),
                }
            if a.llm is not None:
                stats["llm_calls"] += 1
                stats["replayed"] += int(a.llm.cached)
                stats["tokens_in"] += a.llm.tokens_in or 0
                stats["tokens_out"] += a.llm.tokens_out or 0
            gen = next((s for s in a.stages if s.name == "generation"), None)
            kind = gen.detail.get("error") if gen is not None else None
            if a.agent is not None:
                stats["llm_calls"] += a.agent["turns"]
                stats["tokens_in"] += a.agent["tokens_in"] or 0
                stats["tokens_out"] += a.agent["tokens_out"] or 0
                if a.agent["stopped_reason"] != "final_answer":
                    kind = a.agent["stopped_reason"]
            if kind:
                stats["errors"][kind] = stats["errors"].get(kind, 0) + 1
        summary[level] = {"statuses": counts, **stats}
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


def cmd_diagnose(_: argparse.Namespace) -> int:
    """LIVE: three fixed, synthetic probes against the generation deployment, printing the HTTP
    status and the provider's error code/message (UC4's client deliberately discards them). The
    probes contain only fixed text and the synthetic corpus, never a user question."""
    import urllib.error
    import urllib.request

    from app.llm.types import LLMRequest

    from .generate import JSON_SCHEMA
    from .service import build_copilot

    cfg, _, _ = _load()
    copilot = build_copilot("record")  # constructs the live Foundry client behind the cache
    gen = copilot.generator
    foundry = gen.client.inner  # type: ignore[attr-defined]
    s01 = (
        "Can an employee upload confidential customer information to a personal cloud-storage "
        "account?"
    )
    hits = copilot.retriever.retrieve(s01, cfg.levels["advanced"]).hits
    probes = {
        "1_plain_chat": LLMRequest(
            system="Reply with the word OK.",
            user="Say OK.",
            json_schema={},
            prompt_version="probe",
            temperature=gen.temperature,
            max_output_tokens=200,
            timeout_s=60,
        ),
        "2_uc6_schema_trivial": LLMRequest(
            system="Return INSUFFICIENT_EVIDENCE with no claims.",
            user="No evidence.",
            schema_name="policy_answer",
            json_schema=JSON_SCHEMA,
            prompt_version="probe",
            temperature=gen.temperature,
            max_output_tokens=2000,
            timeout_s=60,
        ),
        "3_uc6_real_request_S01": gen.request(s01, copilot.evidence_from(hits)),
    }
    report: dict[str, object] = {
        "endpoint_host": foundry._base(),
        "deployment": foundry.model_id,
        "api": foundry.api,
        "temperature_sent": gen.temperature,
        "llm_config_tier": cfg.generation.tier,
    }
    for name, req in probes.items():
        body = foundry._body(req)
        if not req.json_schema:
            body.pop("response_format", None)
            body.pop("text", None)
        http = urllib.request.Request(
            foundry._url(),
            data=json.dumps(body).encode(),
            headers=foundry._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(http, timeout=req.timeout_s) as resp:
                payload = json.loads(resp.read().decode())
                report[name] = {"http": resp.status, "served_model": payload.get("model")}
        except urllib.error.HTTPError as err:
            try:
                detail = json.loads(err.read().decode()).get("error", {})
            except (ValueError, UnicodeDecodeError, AttributeError):
                detail = {}
            report[name] = {
                "http": err.code,
                "error_code": detail.get("code") if isinstance(detail, dict) else None,
                "error_param": detail.get("param") if isinstance(detail, dict) else None,
                "error_message": (
                    str(detail.get("message"))[:300] if isinstance(detail, dict) else None
                ),
            }
        except OSError as err:
            report[name] = {"transport_error": type(err).__name__}
    print(json.dumps(report, indent=1))
    ok = all(
        isinstance(v, dict) and v.get("http") == 200 for k, v in report.items() if k[0].isdigit()
    )
    return 0 if ok else 4


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dataguard-policy", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest", help="parse + chunk the corpus and print stats").set_defaults(
        func=cmd_ingest
    )
    emb = sub.add_parser("embed", help="embedding commands")
    emb = emb.add_subparsers(dest="sub", required=True)
    emb.add_parser(
        "record", help="LIVE: embed chunks + golden queries with the Foundry deployment"
    ).set_defaults(func=cmd_embed_record)
    ev = sub.add_parser("eval", help="evaluation commands")
    ev = ev.add_subparsers(dest="sub", required=True)
    er = ev.add_parser("retrieval", help="retrieval metrics per variant (deterministic)")
    er.add_argument("--embed-mode", choices=("replay", "offline"), default="replay")
    er.add_argument("--no-write", action="store_true", help="print a summary; write no files")
    er.set_defaults(func=cmd_eval_retrieval)
    ea = ev.add_parser("answers", help="answer-level metrics per level (replay by default)")
    ea.add_argument("--mode", choices=("replay", "offline"), default="replay")
    ea.add_argument("--levels", default="naive,advanced,agentic")
    ea.add_argument("--no-write", action="store_true", help="print a summary; write no files")
    ea.set_defaults(func=cmd_eval_answers)
    ask = sub.add_parser("ask", help="answer one question (prints policy text locally)")
    ask.add_argument("question")
    ask.add_argument("--level", choices=("naive", "advanced", "agentic"), default="advanced")
    ask.add_argument("--mode", choices=("replay", "offline", "live"), default="replay")
    ask.add_argument("--json", action="store_true", help="print the full PolicyAnswer")
    ask.add_argument(
        "--agent-backend", choices=("chat-completions", "foundry-service"),
        default="chat-completions",
    )  # fmt: skip
    ask.add_argument("--tenant-id", default=None, help="Entra tenant id (foundry-service sign-in)")
    ask.set_defaults(func=cmd_ask)
    ans = sub.add_parser("answers", help="answer recording commands")
    ans = ans.add_subparsers(dest="sub", required=True)
    rec = ans.add_parser("record", help="LIVE: record golden-set answers for replay")
    rec.add_argument("--levels", default="naive,advanced,agentic")
    rec.add_argument("--ids", default="", help="comma-separated golden ids (default: all)")
    rec.add_argument(
        "--agent-backend", choices=("chat-completions", "foundry-service"),
        default="chat-completions",
    )  # fmt: skip
    rec.add_argument("--tenant-id", default=None, help="Entra tenant id (foundry-service sign-in)")
    rec.set_defaults(func=cmd_answers_record)
    ag = sub.add_parser("agent", help="Foundry Agent Service commands")
    ag = ag.add_subparsers(dest="sub", required=True)
    agr = ag.add_parser("register", help="LIVE: create a new version of dataguard-policy-copilot")
    agr.add_argument("--tenant-id", default=None, help="Entra tenant id for the browser sign-in")
    agr.set_defaults(func=cmd_agent_register)
    obs = sub.add_parser("obs", help="observability commands")
    obs = obs.add_subparsers(dest="sub", required=True)
    orp = obs.add_parser("report", help="traced golden run + privacy audit + telemetry summary")
    orp.add_argument("--mode", choices=("replay", "offline"), default="replay")
    orp.add_argument("--levels", default="naive,advanced,agentic")
    orp.set_defaults(func=cmd_obs_report)
    sub.add_parser(
        "diagnose", help="LIVE: probe the generation deployment and print provider errors"
    ).set_defaults(func=cmd_diagnose)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, CorpusError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except EmbeddingError as exc:
        print(f"embedding error: {exc.kind}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
