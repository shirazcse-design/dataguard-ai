"""The demo dashboard's local server: loopback only, fixed routes, the mode label, and no secrets.

The server runs in a background thread on an ephemeral port; no test contacts anything external.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from app.demo.server import LIVE_AGENT_ENV, LIVE_CLASSIFIER_ENV, DemoApp, build_server, mode_label

SECRET = "sk-super-secret-value-123"


@pytest.fixture()
def serve():
    servers = []

    def start(app: DemoApp) -> str:
        srv = build_server(app, port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


# ---- the mode label --------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["replay", "REPLAY", "Live", "", "live ", None])
def test_anything_but_exactly_live_is_labelled_replay(mode):
    assert mode_label(mode) == "REPLAY"


def test_live_is_labelled_live():
    assert mode_label("live") == "LIVE"


def test_an_unknown_mode_is_refused_at_startup():
    with pytest.raises(ValueError):
        DemoApp(mode="production")


def test_every_api_response_carries_the_servers_own_mode_label(serve):
    base = serve(DemoApp(mode="replay", env={}))
    for path in ("/api/status", "/api/metrics", "/api/nope"):
        _, _, body = get(base + path)
        payload = json.loads(body)
        assert payload["mode"] == "replay" and payload["mode_label"] == "REPLAY"


# ---- routes, headers, binding ----------------------------------------------------------------
def test_static_page_and_assets_are_served_with_security_headers(serve):
    base = serve(DemoApp(env={}))
    for path, ctype in (("/", "text/html"), ("/static/app.js", "text/javascript"),
                        ("/static/styles.css", "text/css")):  # fmt: skip
        status, headers, body = get(base + path)
        assert status == 200 and headers["Content-Type"].startswith(ctype) and body
        assert headers["Cache-Control"] == "no-store"
        assert "default-src 'self'" in headers["Content-Security-Policy"]
        assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize(
    "path",
    ["/static/../server.py", "/static/metrics.py", "/../../pyproject.toml", "/api/../api/status/x"],
)
def test_only_the_fixed_routes_exist(serve, path):
    base = serve(DemoApp(env={}))
    status, _, _ = get(base + path)
    assert status == 404


def test_the_server_refuses_a_non_loopback_address():
    with pytest.raises(ValueError, match="loopback"):
        build_server(DemoApp(env={}), host="0.0.0.0", port=0)


def test_metrics_endpoint_serves_the_approved_artifacts_as_recorded_data(serve):
    base = serve(DemoApp(env={}))
    status, _, body = get(base + "/api/metrics")
    payload = json.loads(body)
    assert status == 200 and payload["data_class"] == "RECORDED"
    locked = next(r for r in payload["data"]["headline"] if r["split"] == "locked test")
    assert locked["strict_level_f1"]["value"] == 0.884
    assert "docs/uc4/completion-report.md" in payload["data"]["sources"]


# ---- secrets ---------------------------------------------------------------------------------
def test_readiness_reports_only_whether_each_variable_is_set_never_its_value(serve):
    env = {name: SECRET for name in (*LIVE_CLASSIFIER_ENV, *LIVE_AGENT_ENV)}
    env["DATAGUARD_AZURE_MONITOR_CONNECTION_STRING"] = "InstrumentationKey=" + SECRET
    base = serve(DemoApp(mode="live", env=env))
    for path in ("/api/status", "/api/metrics", "/"):
        _, _, body = get(base + path)
        assert SECRET not in body.decode("utf-8")
    readiness = json.loads(get(base + "/api/status")[2])["data"]["live_readiness"]
    assert all(v is True for v in readiness["classifier"].values())
    assert set(readiness["classifier"]) == set(LIVE_CLASSIFIER_ENV)
