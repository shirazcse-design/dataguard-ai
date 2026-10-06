"""The interview demo's local HTTP server (stdlib only; no web framework).

It binds to a loopback address only and serves a fixed set of routes: the static page and a few
JSON endpoints that call EXISTING interfaces. There is no filesystem path handling (the static files
are a fixed map), and every response carries `no-store` and a restrictive Content-Security-Policy.

Modes are chosen at startup and never switched silently:

* ``replay`` - no network: recorded LLM responses and the offline deterministic agent planner.
* ``live``   - real Foundry calls where a page runs the classifier or agent.

`mode_label` is the ONLY place a mode becomes the badge text the page shows, so a replay run cannot
be labelled LIVE (unit-tested). Secrets are never read here; readiness reports only whether each
environment variable NAME is set.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any

from . import docs_view, examples, metrics
from .access import AccessSession
from .agent import AgentSession
from .classify import ClassifySession, DemoError
from .dlp import DlpSession
from .insider import InsiderSession
from .policy import PolicySession
from .review import DEFAULT_PATH, ReviewStore

MODES = ("replay", "live")
MAX_BODY = 8 * 1024 * 1024  # the service's own guard rejects documents over 5 MB; this caps JSON
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

# Names only: readiness shows whether each is set, never a value.
LIVE_CLASSIFIER_ENV = (
    "DATAGUARD_FOUNDRY_ENDPOINT",
    "DATAGUARD_FOUNDRY_API_KEY",
    "DATAGUARD_LLM_DEPLOYMENT_SMALL",
    "DATAGUARD_LLM_DEPLOYMENT_MID",
    "DATAGUARD_LLM_DEPLOYMENT_LARGE",
)
LIVE_AGENT_ENV = ("DATAGUARD_FOUNDRY_PROJECT_ENDPOINT", "DATAGUARD_LLM_DEPLOYMENT_MID")

_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/static/policy.js": ("policy.js", "text/javascript; charset=utf-8"),
    "/static/dlp.js": ("dlp.js", "text/javascript; charset=utf-8"),
    "/static/insider.js": ("insider.js", "text/javascript; charset=utf-8"),
    "/static/access.js": ("access.js", "text/javascript; charset=utf-8"),
}
_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
}


def mode_label(mode: str) -> str:
    """The badge text for a mode. Anything that is not exactly ``live`` is REPLAY."""
    return "LIVE" if mode == "live" else "REPLAY"


@dataclass
class DemoApp:
    mode: str = "replay"
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    _metrics: dict[str, Any] | None = None
    review_path: Path | str = DEFAULT_PATH
    _classifier: ClassifySession | None = None
    _agent: AgentSession | None = None
    _review: ReviewStore | None = None
    _policy: PolicySession | None = None
    policy_review_path: Path | str | None = None
    _dlp: DlpSession | None = None
    dlp_review_path: Path | str | None = None
    _insider: InsiderSession | None = None
    insider_review_path: Path | str | None = None
    _access: AccessSession | None = None
    access_review_path: Path | str | None = None
    _init_lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")

    def envelope(self, data: Any, **extra: Any) -> dict[str, Any]:
        return {"mode": self.mode, "mode_label": mode_label(self.mode), **extra, "data": data}

    def status(self) -> dict[str, Any]:
        return self.envelope(
            {
                "modes": {
                    "replay": "No network: recorded LLM responses and the offline agent planner.",
                    "live": "Real Foundry calls for classification and the agent planner.",
                },
                "live_readiness": {
                    "classifier": {k: bool(self.env.get(k)) for k in LIVE_CLASSIFIER_ENV},
                    "agent": {k: bool(self.env.get(k)) for k in LIVE_AGENT_ENV},
                },
            }
        )

    def data_class(self) -> str:
        """What a classification or agent run on this server is: LIVE only in live mode."""
        return "LIVE" if mode_label(self.mode) == "LIVE" else "REPLAY"

    def classifier(self) -> ClassifySession:
        with self._init_lock:
            if self._classifier is None:
                self._classifier = ClassifySession(self.mode)
            return self._classifier

    def agent_session(self) -> AgentSession:
        classify = self.classifier()
        with self._init_lock:
            if self._agent is None:
                self._agent = AgentSession(self.mode, classify, self.env)
            return self._agent

    def review_store(self) -> ReviewStore:
        with self._init_lock:
            if self._review is None:
                self._review = ReviewStore(self.review_path)
            return self._review

    def agent_describe(self) -> dict[str, Any]:
        return self.envelope(self.agent_session().describe(), data_class=self.data_class())

    def agent_run(self, body: dict[str, Any]) -> dict[str, Any]:
        view = self.agent_session().run(str(body.get("doc", "")))
        return self.envelope(view, data_class=self.data_class())

    def review_queue(self) -> dict[str, Any]:
        store = self.review_store()
        return self.envelope(
            {"items": store.queue(), "stored_at": store.display_path}, data_class="DEMO-ONLY STATE"
        )

    def review_seed(self, body: dict[str, Any]) -> dict[str, Any]:
        """Two REAL classifications with the LLM tiers off; the fail-safe path queues them."""
        added = []
        store = self.review_store()
        for key in ("review", "confidential"):
            if key in store.seeded:
                continue  # each fail-safe case is added once per session
            payload = self.classifier().classify({"example": key, "llm_tiers": "off"})
            ex = examples.BY_KEY[key]
            title = ex.title if ex.llm_tiers == "off" else f"{ex.title} (LLM tiers off)"
            if store.add_from_classify(payload, title):
                store.seeded.add(key)
                added.append(payload["request_id"])
        return self.envelope({"added": added}, data_class=self.data_class())

    def review_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.envelope(self.review_store().decide(body), data_class="DEMO-ONLY STATE")

    def rai(self) -> dict[str, Any]:
        data = docs_view.responsible_ai()
        data["agent"] = metrics.agent_eval()
        return self.envelope(data, data_class="STATIC DOCUMENTATION")

    def observability(self) -> dict[str, Any]:
        data = docs_view.observability()
        traces = []
        for t in list(self.classifier().recent)[-12:][::-1]:
            spans = t["spans"]
            if not spans:
                continue
            t0 = min(s["start_ns"] for s in spans)
            traces.append(
                {
                    "request_id": t["request_id"],
                    "root": next(
                        (s["name"] for s in spans if not s.get("parent")), spans[0]["name"]
                    ),
                    "spans": [
                        {**s, "offset_ms": (s["start_ns"] - t0) / 1e6}
                        for s in sorted(spans, key=lambda s: s["start_ns"])
                    ],
                }
            )
        data["session"] = {"data_class": self.data_class(), "traces": traces}
        return self.envelope(data, data_class="MIXED: see each panel")

    def examples(self) -> dict[str, Any]:
        return self.envelope(examples.listing(), data_class="SYNTHETIC DEV-SPLIT DOCUMENTS")

    def classify(self, body: dict[str, Any]) -> dict[str, Any]:
        payload = self.classifier().classify(body)
        ex = examples.BY_KEY.get(body.get("example") or "")
        title = ex.title if ex else (body.get("filename") or "pasted text")
        payload["queued_for_review"] = self.review_store().add_from_classify(payload, title)
        return self.envelope(payload, data_class=self.data_class())

    def classify_upload(self, body: dict[str, Any]) -> dict[str, Any]:
        name, data = body.get("name"), body.get("data_base64")
        if not isinstance(name, str) or not isinstance(data, str):
            raise DemoError("an upload needs a file name and base64 data")
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise DemoError("the upload is not valid base64") from None
        out = self.classifier().classify_upload(name[:200], raw, body.get("llm_tiers") or "default")
        out["queued_for_review"] = self.review_store().add_from_classify(out, name[:200])
        return self.envelope(out, data_class=self.data_class())

    # -- Data Security Policy Copilot (UC6) ----------------------------------------------------
    def policy_session(self) -> PolicySession:
        with self._init_lock:
            if self._policy is None:
                kw = {"review_path": self.policy_review_path} if self.policy_review_path else {}
                self._policy = PolicySession(self.mode, self.env, **kw)
            return self._policy

    def policy_describe(self) -> dict[str, Any]:
        return self.envelope(self.policy_session().describe(), data_class="RECORDED + SYNTHETIC")

    def policy_ask(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.envelope(self.policy_session().ask(body), data_class=self.data_class())

    def policy_review(self) -> dict[str, Any]:
        return self.envelope(self.policy_session().review_queue(), data_class="DEMO-ONLY STATE")

    def policy_review_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.envelope(
            self.policy_session().review_decide(body), data_class="DEMO-ONLY STATE"
        )

    # -- Agentic DLP (UC1) ----------------------------------------------------------------------
    def dlp_session(self) -> DlpSession:
        with self._init_lock:
            if self._dlp is None:
                kw = {"review_path": self.dlp_review_path} if self.dlp_review_path else {}
                self._dlp = DlpSession(self.mode, self.env, **kw)
            return self._dlp

    def dlp_describe(self) -> dict[str, Any]:
        return self.envelope(self.dlp_session().describe(), data_class="RECORDED + SYNTHETIC")

    def dlp_investigate(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.envelope(self.dlp_session().investigate(body), data_class=self.data_class())

    def dlp_review(self) -> dict[str, Any]:
        return self.envelope(self.dlp_session().review_queue(), data_class="DEMO-ONLY STATE")

    def dlp_review_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.envelope(self.dlp_session().review_decide(body), data_class="DEMO-ONLY STATE")

    # -- Insider Risk Investigation (UC2) -------------------------------------------------------
    def insider_session(self) -> InsiderSession:
        with self._init_lock:
            if self._insider is None:
                kw = {"review_path": self.insider_review_path} if self.insider_review_path else {}
                self._insider = InsiderSession(self.mode, self.env, **kw)
            return self._insider

    def insider_describe(self) -> dict[str, Any]:
        return self.envelope(self.insider_session().describe(), data_class="RECORDED + SYNTHETIC")

    def insider_investigate(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.envelope(self.insider_session().investigate(body), data_class=self.data_class())

    def insider_review(self) -> dict[str, Any]:
        return self.envelope(self.insider_session().review_queue(), data_class="DEMO-ONLY STATE")

    def insider_review_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.envelope(
            self.insider_session().review_decide(body), data_class="DEMO-ONLY STATE"
        )

    # -- Access Governance (UC3) --------------------------------------------------------------
    def access_session(self) -> AccessSession:
        with self._init_lock:
            if self._access is None:
                kw = {"review_path": self.access_review_path} if self.access_review_path else {}
                self._access = AccessSession(self.mode, self.env, **kw)
            return self._access

    def access_describe(self) -> dict[str, Any]:
        return self.envelope(self.access_session().describe(), data_class="RECORDED + SYNTHETIC")

    def access_investigate(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.envelope(self.access_session().investigate(body), data_class=self.data_class())

    def access_review(self) -> dict[str, Any]:
        return self.envelope(self.access_session().review_queue(), data_class="DEMO-ONLY STATE")

    def access_review_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.envelope(
            self.access_session().review_decide(body), data_class="DEMO-ONLY STATE"
        )

    def metrics(self) -> dict[str, Any]:
        if self._metrics is None:
            self._metrics = metrics.all_metrics()
        return self.envelope(self._metrics, data_class="RECORDED")


def _static(name: str) -> bytes:
    return resources.files("app.demo").joinpath("static", name).read_bytes()


def make_handler(app: DemoApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "DataGuardDemo"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in _HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

        def do_GET(self) -> None:  # noqa: N802 - stdlib name
            path = self.path.split("?", 1)[0]
            if path in _STATIC:
                name, ctype = _STATIC[path]
                self._send(HTTPStatus.OK, _static(name), ctype)
                return
            routes = {
                "/api/status": app.status,
                "/api/metrics": app.metrics,
                "/api/examples": app.examples,
                "/api/agent": app.agent_describe,
                "/api/review": app.review_queue,
                "/api/rai": app.rai,
                "/api/observability": app.observability,
                "/api/policy": app.policy_describe,
                "/api/policy/review": app.policy_review,
                "/api/dlp": app.dlp_describe,
                "/api/dlp/review": app.dlp_review,
                "/api/insider": app.insider_describe,
                "/api/insider/review": app.insider_review,
                "/api/access": app.access_describe,
                "/api/access/review": app.access_review,
            }
            if path in routes:
                try:
                    self._json(HTTPStatus.OK, routes[path]())
                except metrics.ArtifactError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, app.envelope(None, error=str(exc)))
                return
            self._json(HTTPStatus.NOT_FOUND, app.envelope(None, error="not found"))

        def do_POST(self) -> None:  # noqa: N802 - stdlib name
            path = self.path.split("?", 1)[0]
            routes = {
                "/api/classify": app.classify,
                "/api/classify-upload": app.classify_upload,
                "/api/agent/run": app.agent_run,
                "/api/review/seed": app.review_seed,
                "/api/review/decide": app.review_decide,
                "/api/policy/ask": app.policy_ask,
                "/api/policy/review/decide": app.policy_review_decide,
                "/api/dlp/investigate": app.dlp_investigate,
                "/api/dlp/review/decide": app.dlp_review_decide,
                "/api/insider/investigate": app.insider_investigate,
                "/api/insider/review/decide": app.insider_review_decide,
                "/api/access/investigate": app.access_investigate,
                "/api/access/review/decide": app.access_review_decide,
            }
            if path not in routes:
                self._json(HTTPStatus.NOT_FOUND, app.envelope(None, error="not found"))
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self._json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE, app.envelope(None, error="too large")
                )
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, app.envelope(None, error="body must be JSON"))
                return
            try:
                self._json(HTTPStatus.OK, routes[path](body))
            except DemoError as exc:
                self._json(exc.status, app.envelope(None, error=str(exc)))

    return Handler


def build_server(app: DemoApp, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    if host not in LOOPBACK:
        raise ValueError("the demo server binds to a loopback address only")
    return ThreadingHTTPServer((host, port), make_handler(app))
