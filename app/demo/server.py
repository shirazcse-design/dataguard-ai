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

import json
import os
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any

from . import metrics

MODES = ("replay", "live")
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
            routes = {"/api/status": app.status, "/api/metrics": app.metrics}
            if path in routes:
                try:
                    self._json(HTTPStatus.OK, routes[path]())
                except metrics.ArtifactError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, app.envelope(None, error=str(exc)))
                return
            self._json(HTTPStatus.NOT_FOUND, app.envelope(None, error="not found"))

    return Handler


def build_server(app: DemoApp, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    if host not in LOOPBACK:
        raise ValueError("the demo server binds to a loopback address only")
    return ThreadingHTTPServer((host, port), make_handler(app))
