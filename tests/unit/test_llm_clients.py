"""Provider interface, mock/replay adapters, retry policy, and the (unverified) Foundry adapter
against a LOCAL fake server. Nothing here contacts a real service or says anything about a model."""

from __future__ import annotations

import contextlib
import json
import random
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.llm import FoundryClient, LLMError, LLMRequest, MockLLMClient, ReplayLLMClient
from app.llm.config import FoundryConfig, RetryConfig
from app.llm.retry import call_with_retry


def req(user="hello", version="p.v1", **kw) -> LLMRequest:
    base = dict(system="sys", user=user, json_schema={"type": "object"}, prompt_version=version,
                max_output_tokens=100, timeout_s=2.0)  # fmt: skip
    base.update(kw)
    return LLMRequest(**base)


# ---- request hashing ------------------------------------------------------------------------
def test_input_hash_depends_on_everything_that_shapes_the_input():
    base = req().input_hash()
    assert req().input_hash() == base
    for changed in (req(user="other"), req(system="other"), req(json_schema={"type": "array"}),
                    req(temperature=0.5), req(max_output_tokens=101)):  # fmt: skip
        assert changed.input_hash() != base


def test_input_hash_ignores_the_timeout():
    assert req(timeout_s=1.0).input_hash() == req(timeout_s=9.0).input_hash()


# ---- mock ------------------------------------------------------------------------------------
def test_mock_returns_scripted_responses_in_order_and_records_calls():
    m = MockLLMClient(["a", "b"])
    assert [m.complete_structured(req()).text, m.complete_structured(req()).text] == ["a", "b"]
    assert len(m.calls) == 2
    with pytest.raises(LLMError):
        m.complete_structured(req())


def test_mock_can_raise_scripted_errors():
    m = MockLLMClient([LLMError("timeout")])
    with pytest.raises(LLMError) as exc:
        m.complete_structured(req())
    assert exc.value.kind == "timeout"


# ---- replay ----------------------------------------------------------------------------------
def test_replay_miss_is_an_error_never_a_silent_answer(tmp_path):
    with pytest.raises(LLMError) as exc:
        ReplayLLMClient(tmp_path, "m1").complete_structured(req())
    assert exc.value.kind == "replay_miss"


def test_record_then_replay_round_trips_and_is_marked_cached(tmp_path):
    inner = MockLLMClient(["{}"], tokens=(11, 7), latency_ms=42.0)
    live = ReplayLLMClient(tmp_path, "m1", inner=inner).complete_structured(req())
    assert live.cached is False and len(inner.calls) == 1
    again = ReplayLLMClient(tmp_path, "m1").complete_structured(req())
    assert again.cached and again.text == "{}" and again.prompt_tokens == 11
    assert again.completion_tokens == 7 and again.latency_ms == 42.0
    assert len(inner.calls) == 1  # replay never calls the inner client twice


def test_replay_key_is_prompt_version_model_and_input(tmp_path):
    ReplayLLMClient(tmp_path, "m1", inner=MockLLMClient(["x"])).complete_structured(req())
    for other in (
        (ReplayLLMClient(tmp_path, "m2"), req()),  # other model
        (ReplayLLMClient(tmp_path, "m1"), req(version="p.v2")),  # other prompt version
        (ReplayLLMClient(tmp_path, "m1"), req(user="changed")),  # other input
    ):
        with pytest.raises(LLMError):
            other[0].complete_structured(other[1])


def test_corrupted_or_mismatched_cache_entry_is_a_miss(tmp_path):
    client = ReplayLLMClient(tmp_path, "m1", inner=MockLLMClient(["x"]))
    client.complete_structured(req())
    path = next(tmp_path.rglob("*.json"))
    data = json.loads(path.read_text())
    data["input_hash"] = "0" * 64
    path.write_text(json.dumps(data))
    with pytest.raises(LLMError) as exc:
        ReplayLLMClient(tmp_path, "m1").complete_structured(req())
    assert exc.value.kind == "replay_miss"


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "..", "x" * 200, "a b"])
def test_unsafe_model_ids_cannot_escape_the_cache_directory(tmp_path, bad):
    with pytest.raises(ValueError):
        ReplayLLMClient(tmp_path, bad)


# ---- retry -----------------------------------------------------------------------------------
RETRY = RetryConfig(max_attempts=3, base_delay_s=0.5, max_delay_s=4.0, jitter=0.0)


def flaky(errors, result="ok"):
    seq = list(errors)

    def fn():
        if seq:
            raise seq.pop(0)
        return result

    return fn


def test_retry_recovers_from_transient_errors_with_exponential_backoff():
    sleeps: list[float] = []
    out, attempts = call_with_retry(
        flaky([LLMError("timeout"), LLMError("transport")]), RETRY, sleep=sleeps.append
    )
    assert out == "ok" and attempts == 3 and sleeps == [0.5, 1.0]


def test_retry_gives_up_after_max_attempts_and_reports_attempts():
    with pytest.raises(LLMError) as exc:
        call_with_retry(flaky([LLMError("timeout")] * 5), RETRY, sleep=lambda s: None)
    assert exc.value.attempts == 3


@pytest.mark.parametrize(
    "kind", ["auth", "bad_request", "content_filtered", "replay_miss", "not_configured"]
)
def test_non_transient_errors_are_never_retried(kind):
    calls = []

    def fn():
        calls.append(1)
        raise LLMError(kind)

    with pytest.raises(LLMError):
        call_with_retry(fn, RETRY, sleep=lambda s: pytest.fail("must not sleep"))
    assert len(calls) == 1


def test_retry_after_is_honoured_but_bounded_by_the_maximum_delay():
    sleeps: list[float] = []
    call_with_retry(
        flaky([LLMError("rate_limited", retry_after_s=3.0)]), RETRY, sleep=sleeps.append
    )
    assert sleeps == [3.0]
    sleeps.clear()
    call_with_retry(
        flaky([LLMError("rate_limited", retry_after_s=999)]), RETRY, sleep=sleeps.append
    )
    assert sleeps == [4.0]


def test_jitter_is_deterministic_for_a_seeded_rng_and_stays_within_bounds():
    cfg = RetryConfig(max_attempts=3, base_delay_s=1.0, max_delay_s=10.0, jitter=0.5)
    runs = []
    for _ in range(2):
        sleeps: list[float] = []
        call_with_retry(
            flaky([LLMError("timeout")] * 2), cfg, sleep=sleeps.append, rng=random.Random(7)
        )
        runs.append(sleeps)
    assert runs[0] == runs[1]
    assert 0.5 <= runs[0][0] <= 1.0 and 1.0 <= runs[0][1] <= 2.0


# ---- Foundry adapter against a local fake server ---------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    behaviour = {"status": 200, "body": None, "headers": {}, "delay": 0.0}
    seen: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        _Handler.seen.append(
            {
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": json.loads(self.rfile.read(length)),
            }
        )
        b = _Handler.behaviour
        if b["delay"]:
            import time

            time.sleep(b["delay"])
        payload = (
            b["body"]
            if b["body"] is not None
            else {
                "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5},
            }
        )
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(b["status"])
        for k, v in b["headers"].items():
            self.send_header(k, v)
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(raw)

    def log_message(self, *a):  # silence
        pass


@pytest.fixture()
def server():
    _Handler.seen = []
    _Handler.behaviour = {"status": 200, "body": None, "headers": {}, "delay": 0.0}
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_port}", _Handler
    httpd.shutdown()
    httpd.server_close()


def fcfg(**over) -> FoundryConfig:
    base = dict(
        endpoint_env="EP", api_version_env=None,
        url_template="{endpoint}/openai/v1/chat/completions",
        auth="api_key", api_key_env="KEY", entra_scope=None,
        max_tokens_param="max_completion_tokens", json_schema_response_format=True,
    )  # fmt: skip
    base.update(over)
    return FoundryConfig(**base)


def env(url, **extra):
    return {"EP": url, "AV": "v-test", "KEY": "sekrit-key-123", **extra}


def test_foundry_request_shape_headers_and_response_parsing(server):
    url, h = server
    r = FoundryClient(fcfg(), "my-deployment", env=env(url)).complete_structured(
        req(user="the doc")
    )
    assert r.text == '{"ok": true}' and r.prompt_tokens == 12 and r.completion_tokens == 5
    assert r.model_id == "my-deployment" and r.cached is False and r.latency_ms >= 0
    seen = h.seen[0]
    assert seen["path"] == "/openai/v1/chat/completions"
    assert seen["body"]["model"] == "my-deployment"
    assert seen["headers"]["api-key"] == "sekrit-key-123"
    body = seen["body"]
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "the doc"},
    ]
    assert body["temperature"] == 0.0 and body["max_completion_tokens"] == 100
    rf = body["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == {"type": "object"}


def test_foundry_token_parameter_name_and_response_format_are_configurable(server):
    url, h = server
    cfg = fcfg(max_tokens_param="max_tokens", json_schema_response_format=False)
    FoundryClient(cfg, "d", env=env(url)).complete_structured(req())
    body = h.seen[0]["body"]
    assert (
        body["max_tokens"] == 100
        and "max_completion_tokens" not in body
        and "response_format" not in body
    )


@pytest.mark.parametrize(
    "status, kind, retryable",
    [(429, "rate_limited", True), (500, "transport", True), (503, "transport", True),
     (408, "timeout", True), (401, "auth", False), (403, "auth", False), (400, "bad_request", False)],
)  # fmt: skip
def test_foundry_maps_http_errors_to_error_kinds(server, status, kind, retryable):
    url, h = server
    h.behaviour = {
        "status": status,
        "body": {"error": {"message": "SECRET-DOC-TEXT"}},
        "headers": {},
        "delay": 0.0,
    }
    with pytest.raises(LLMError) as exc:
        FoundryClient(fcfg(), "d", env=env(url)).complete_structured(req())
    assert exc.value.kind == kind and exc.value.retryable is retryable
    assert "SECRET-DOC-TEXT" not in str(exc.value) and "sekrit-key-123" not in str(exc.value)


def test_foundry_reads_retry_after_from_a_429(server):
    url, h = server
    h.behaviour = {"status": 429, "body": {}, "headers": {"Retry-After": "7"}, "delay": 0.0}
    with pytest.raises(LLMError) as exc:
        FoundryClient(fcfg(), "d", env=env(url)).complete_structured(req())
    assert exc.value.retry_after_s == 7.0


def test_foundry_times_out(server):
    url, h = server
    h.behaviour = {"status": 200, "body": None, "headers": {}, "delay": 1.0}
    with pytest.raises(LLMError) as exc:
        FoundryClient(fcfg(), "d", env=env(url)).complete_structured(req(timeout_s=0.2))
    assert exc.value.kind == "timeout" and exc.value.retryable


@pytest.mark.parametrize(
    "body", [b"not json", {"choices": []}, {"choices": [{"message": {}}]}, {"nope": 1},
             {"choices": [{"message": {"content": 5}}]}],
)  # fmt: skip
def test_foundry_rejects_unexpected_response_shapes(server, body):
    url, h = server
    h.behaviour = {"status": 200, "body": body, "headers": {}, "delay": 0.0}
    with pytest.raises(LLMError) as exc:
        FoundryClient(fcfg(), "d", env=env(url)).complete_structured(req())
    assert exc.value.kind == "transport"


def test_foundry_reports_content_filtering(server):
    url, h = server
    h.behaviour = {"status": 200, "headers": {}, "delay": 0.0,
                   "body": {"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]}}  # fmt: skip
    with pytest.raises(LLMError) as exc:
        FoundryClient(fcfg(), "d", env=env(url)).complete_structured(req())
    assert exc.value.kind == "content_filtered" and not exc.value.retryable


def test_foundry_connection_failure_is_a_transport_error():
    with pytest.raises(LLMError) as exc:
        FoundryClient(fcfg(), "d", env=env("http://127.0.0.1:1")).complete_structured(req())
    assert exc.value.kind in ("transport", "timeout")


@pytest.mark.parametrize("missing", ["EP", "KEY"])
def test_foundry_fails_clearly_when_configuration_is_missing(server, missing):
    url, _ = server
    e = env(url)
    del e[missing]
    with pytest.raises(LLMError) as exc:
        FoundryClient(fcfg(), "d", env=e).complete_structured(req())
    assert exc.value.kind == "not_configured" and missing in str(exc.value)


def test_foundry_refuses_to_send_credentials_over_plain_http_to_a_remote_host():
    called = []
    client = FoundryClient(
        fcfg(), "d", env=env("http://example.com"), opener=lambda *a, **k: called.append(1)
    )
    with pytest.raises(LLMError) as exc:
        client.complete_structured(req())
    assert exc.value.kind == "not_configured" and not called


def test_foundry_entra_auth_uses_a_bearer_token_from_the_provider(server):
    url, h = server
    cfg = fcfg(auth="entra", entra_scope="scope-under-test")
    FoundryClient(cfg, "d", env=env(url), token_provider=lambda: "tok-123").complete_structured(
        req()
    )
    headers = h.seen[0]["headers"]
    assert headers["authorization"] == "Bearer tok-123" and "api-key" not in headers


def test_entra_requires_an_explicit_scope():
    with pytest.raises(ValueError):
        fcfg(auth="entra", entra_scope=None)


def test_entra_without_the_optional_sdk_fails_clearly(server, monkeypatch):
    url, _ = server
    import builtins

    real = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("azure"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(LLMError) as exc:
        FoundryClient(fcfg(auth="entra", entra_scope="s"), "d", env=env(url)).complete_structured(
            req()
        )
    assert exc.value.kind == "not_configured"


def test_http_error_object_is_a_urlerror_subclass_we_handle():
    # documents the assumption behind the adapter's exception ordering
    assert issubclass(urllib.error.HTTPError, urllib.error.URLError)


def test_a_project_endpoint_is_reduced_to_the_resource_host(server):
    url, h = server
    project = url + "/api/projects/my-project"
    FoundryClient(fcfg(), "d", env=env(project)).complete_structured(req())
    assert h.seen[0]["path"] == "/openai/v1/chat/completions"  # no /api/projects prefix


def test_a_legacy_template_with_an_api_version_still_works_and_requires_it(server):
    url, h = server
    cfg = fcfg(
        api_version_env="AV",
        url_template="{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}",
    )
    FoundryClient(cfg, "dep", env=env(url, AV="v-test")).complete_structured(req())
    assert h.seen[0]["path"] == "/openai/deployments/dep/chat/completions?api-version=v-test"
    with pytest.raises(LLMError) as exc:
        FoundryClient(
            cfg, "dep", env={k: v for k, v in env(url).items() if k != "AV"}
        ).complete_structured(req())
    assert exc.value.kind == "not_configured" and "AV" in str(exc.value)


def test_the_served_model_is_captured_for_provenance_and_survives_replay(server, tmp_path):
    url, h = server
    h.behaviour = {"status": 200, "headers": {}, "delay": 0.0,
                   "body": {"model": "provider-reported-name", "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}}  # fmt: skip
    live = ReplayLLMClient(tmp_path, "m", inner=FoundryClient(fcfg(), "d", env=env(url)))
    assert live.complete_structured(req()).served_model == "provider-reported-name"
    assert (
        ReplayLLMClient(tmp_path, "m").complete_structured(req()).served_model
        == "provider-reported-name"
    )


RESPONSES_OK = {
    "model": "served-x", "status": "completed",
    "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": '{"ok": true}'}]},
    ],
    "usage": {"input_tokens": 42, "output_tokens": 48, "total_tokens": 90},
}  # fmt: skip


def test_responses_api_request_shape_and_parsing(server):
    url, h = server
    h.behaviour = {"status": 200, "body": RESPONSES_OK, "headers": {}, "delay": 0.0}
    r = FoundryClient(fcfg(), "dep", api="responses", env=env(url)).complete_structured(
        req(temperature=None)
    )
    assert r.text == '{"ok": true}' and r.prompt_tokens == 42 and r.completion_tokens == 48
    assert r.served_model == "served-x"
    seen = h.seen[0]
    assert seen["path"] == "/openai/v1/responses"
    body = seen["body"]
    assert body["model"] == "dep" and body["max_output_tokens"] == 100 and "temperature" not in body
    assert body["input"][0] == {"role": "system", "content": "sys"}
    fmt = body["text"]["format"]
    assert (
        fmt["type"] == "json_schema"
        and fmt["strict"] is True
        and fmt["schema"] == {"type": "object"}
    )
    assert "messages" not in body and "response_format" not in body


def test_temperature_is_sent_only_when_requested(server):
    url, h = server
    FoundryClient(fcfg(), "d", env=env(url)).complete_structured(req(temperature=None))
    assert "temperature" not in h.seen[0]["body"]
    FoundryClient(fcfg(), "d", env=env(url)).complete_structured(req(temperature=0.0))
    assert h.seen[1]["body"]["temperature"] == 0.0


def test_responses_api_incomplete_without_an_answer_yields_empty_text_and_content_filter_is_an_error(
    server,
):
    url, h = server
    out_of_tokens = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                     "output": [{"type": "reasoning"}], "usage": {"input_tokens": 5, "output_tokens": 100}}  # fmt: skip
    h.behaviour = {"status": 200, "body": out_of_tokens, "headers": {}, "delay": 0.0}
    r = FoundryClient(fcfg(), "d", api="responses", env=env(url)).complete_structured(req())
    assert r.text == "" and r.completion_tokens == 100
    filtered = {
        "status": "incomplete",
        "incomplete_details": {"reason": "content_filter"},
        "output": [],
    }
    h.behaviour = {"status": 200, "body": filtered, "headers": {}, "delay": 0.0}
    with pytest.raises(LLMError) as exc:
        FoundryClient(fcfg(), "d", api="responses", env=env(url)).complete_structured(req())
    assert exc.value.kind == "content_filtered"


@pytest.mark.parametrize("body", [{"nope": 1}, {"output": [{"type": "message"}]}, {"output": 5}])
def test_responses_api_rejects_unexpected_shapes(server, body):
    url, h = server
    h.behaviour = {"status": 200, "body": body, "headers": {}, "delay": 0.0}
    with pytest.raises(LLMError) as exc:
        FoundryClient(fcfg(), "d", api="responses", env=env(url)).complete_structured(req())
    assert exc.value.kind == "transport"
