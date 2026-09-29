"""`dataguard-policy`: the Data Security Policy Copilot (UC6) command line.

    dataguard-policy ingest                         corpus stats + fingerprint (no network)
    dataguard-policy embed record                   LIVE: record chunk + golden-query embeddings
    dataguard-policy eval retrieval [--embed-mode replay|offline]

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
