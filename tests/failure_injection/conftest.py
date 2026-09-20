"""Shared fixtures: real components wired to a LOCAL fake provider server (never a real service)."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.classification.hybrid import HybridClassifier
from app.classification.routing_config import ROUTING_FILE, RoutingConfig
from app.classification.schemas import ClassificationRequest, Document
from app.llm import FoundryClient, LLMClassifier
from app.llm.config import load_llm_config
from app.llm.fewshot import load_fewshot
from app.llm.prompting import PromptBuilder
from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents
from evals.classification.lock import DEVELOPMENT_SPLITS
from guardrails.injection import InjectionScanner, load_injection_config
from guardrails.input import load_input_guard_config
from rules import build_rules_classifier

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def dev_docs():
    docs = load_documents(DEFAULT_DATA_DIR, splits=list(DEVELOPMENT_SPLITS))
    return sorted((d for d in docs if d.split == "dev"), key=lambda d: d.doc_id)


@pytest.fixture(scope="session")
def parts(bundle):
    cfg, sha = load_llm_config(bundle.policy)
    docs = load_documents(DEFAULT_DATA_DIR, splits=list(DEVELOPMENT_SPLITS))
    shots = load_fewshot(ROOT / cfg.prompt.fewshot_file, [d for d in docs if d.split == "train"])
    guard, gsha = load_injection_config()
    return SimpleNamespace(
        cfg=cfg, sha=sha, shots=shots, guard=guard, gsha=gsha, bundle=bundle,
        builder=PromptBuilder(cfg, bundle.taxonomy, shots, ROOT),
        input_guard=load_input_guard_config()[0],
        rules=build_rules_classifier(bundle),
    )  # fmt: skip


def request(content: str, rid: str = "r1", **opts) -> ClassificationRequest:
    body = {
        "request_id": rid,
        "document": Document(content=content, filename="a.txt", extension="txt"),
    }
    if opts:
        body["options"] = opts
    return ClassificationRequest(**body)


def chat_body(level, cats=(), quotes=(), *, bucket="high", insufficient=False) -> dict:
    """A chat-completions response carrying the classifier's JSON answer."""
    answer = {
        "level": level, "categories": list(cats),
        "evidence": [{"quote": q, "supports_axis": "category", "supports_value": cats[0]} for q in quotes],
        "rationale": "test", "level_confidence": bucket, "category_confidence": bucket,
        "insufficient_information": insufficient,
    }  # fmt: skip
    return {
        "model": "fake-served-model",
        "choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


def raw_body(text: str) -> dict:
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}], "usage": {}}


class FakeProvider:
    """A local HTTP server that plays a script: a list of (status, body, headers, delay_s)."""

    def __init__(self) -> None:
        self.script: list[tuple[int, dict, dict, float]] = []
        self.default: tuple[int, dict, dict, float] | None = None
        self.hits: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length", 0))
                outer.hits.append(json.loads(self.rfile.read(n)))
                status, body, headers, delay = (
                    outer.script.pop(0) if outer.script else outer.default
                )  # type: ignore[misc]
                if delay:
                    time.sleep(delay)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(json.dumps(body).encode())

            def log_message(self, *a):
                pass

        ThreadingHTTPServer.daemon_threads = True
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def provider():
    p = FakeProvider()
    yield p
    p.close()


@pytest.fixture()
def two_providers():
    a, b = FakeProvider(), FakeProvider()
    yield a, b
    a.close()
    b.close()


def llm_over_http(parts, url: str, tier: str = "mid", *, timeout_s: float | None = None,
                  sleeps: list | None = None, api: str = "chat_completions", cfg=None) -> LLMClassifier:  # fmt: skip
    """The REAL LLM classifier talking to the REAL Foundry adapter over a local socket."""
    cfg = cfg or parts.cfg
    if timeout_s is not None:
        cfg = cfg.model_copy(
            update={"generation": cfg.generation.model_copy(update={"timeout_s": timeout_s})}
        )
    env = {cfg.foundry.endpoint_env: url, cfg.foundry.api_key_env: "test-key-not-real"}
    client = FoundryClient(cfg.foundry, f"dep-{tier}", env=env, api=api)
    return LLMClassifier(
        client, cfg, parts.bundle.policy, parts.builder, InjectionScanner(parts.guard), tier=tier,
        config_sha256=parts.sha, guardrail_sha256=parts.gsha,
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
    )  # fmt: skip


def hybrid(
    parts, variant="default", *, rules=None, ml=None, llms=None, input_guard=True, **variants
) -> HybridClassifier:
    from app.classification.config_loader import default_config_dir, read_yaml

    data, digest = read_yaml(default_config_dir() / ROUTING_FILE)
    data["variants"] = {**data["variants"], **variants}
    return HybridClassifier(
        parts.bundle.policy, RoutingConfig.model_validate(data), variant, routing_sha256=digest,
        rules=rules, ml=ml, llms=llms or {}, scanner=InjectionScanner(parts.guard),
        input_guard=parts.input_guard if input_guard else None,
    )  # fmt: skip


@pytest.fixture(scope="session")
def hr_doc(parts, dev_docs):
    """A dev document that the real Rules stage decides at Highly Confidential."""
    from app.classification.router import rules_sufficient
    from app.classification.routing_config import RulesStage

    stage = RulesStage(enabled=True, short_circuit=False, min_level_strength="strong")
    for d in dev_docs:
        r = parts.rules.classify(d.to_request())
        if (
            rules_sufficient(r, stage).accepted
            and r.level.value == "HIGHLY_CONFIDENTIAL"
            and d.tier == "T1"
        ):
            return d
    raise AssertionError("no rules-decisive Highly Confidential dev document")


@pytest.fixture(scope="session")
def plain_doc(parts, dev_docs):
    """A dev document on which Rules abstain (an LLM has to decide)."""
    for d in dev_docs:
        if parts.rules.classify(d.to_request()).routing.abstained and d.tier == "T2":
            return d
    raise AssertionError("no rules-abstaining dev document")


def quote_of(doc, n: int = 40) -> str:
    """A verbatim quote from the document (exists in the text sent to the model)."""
    line = next(x for x in doc.content.splitlines() if len(x.strip()) >= 12)
    return line.strip()[:n]
