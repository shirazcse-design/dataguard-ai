"""UC6 embeddings: Foundry client request/response handling (fake transport), record/replay cache,
replay-miss behaviour, and the offline embedder's labelling."""

from __future__ import annotations

import io
import json
import urllib.error

import numpy as np
import pytest

from app.llm.config import FoundryConfig
from app.policy.config import load_policy_config
from app.policy.embeddings import (
    OFFLINE_MODEL_ID,
    CachedEmbedder,
    EmbeddingError,
    FoundryEmbeddingClient,
    HashingEmbedder,
)

FOUNDRY = FoundryConfig(
    endpoint_env="EP", url_template="{endpoint}/openai/v1/chat/completions", auth="api_key",
    api_key_env="KEY", max_tokens_param="max_completion_tokens", json_schema_response_format=True,
)  # fmt: skip
ENV = {
    "EP": "https://res.services.ai.azure.com/api/projects/p",
    "KEY": "k",
    "DATAGUARD_EMBEDDING_DEPLOYMENT": "emb",
}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(captured, dims=3, status=None):
    def op(request, timeout):
        if status:
            raise urllib.error.HTTPError(request.full_url, status, "x", {}, None)
        body = json.loads(request.data)
        captured.append((request.full_url, dict(request.header_items()), body))
        data = [{"index": i, "embedding": [float(i + 1)] * dims} for i in range(len(body["input"]))]
        return _Resp(json.dumps({"data": data[::-1], "usage": {"prompt_tokens": 7}}).encode())

    return op


def _client(captured, **kw):
    cfg, _ = load_policy_config()
    return FoundryEmbeddingClient(FOUNDRY, cfg.embedding, env=ENV, opener=_opener(captured, **kw))


def test_foundry_request_shape_and_normalised_ordered_output():
    captured = []
    out = _client(captured).embed(["a", "b"])
    url, headers, body = captured[0]
    assert url == "https://res.services.ai.azure.com/openai/v1/embeddings"
    assert body == {"model": "emb", "input": ["a", "b"]}
    assert headers.get("Api-key") == "k"
    assert out.shape == (2, 3) and np.allclose(np.linalg.norm(out, axis=1), 1.0)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://res.cognitiveservices.azure.com",
        "https://res.cognitiveservices.azure.com/",
        "https://res.cognitiveservices.azure.com/openai/deployments/emb/embeddings?api-version=2023-05-15",
        "https://res.cognitiveservices.azure.com/openai/v1/",
    ],
)
def test_pasted_endpoint_forms_resolve_to_the_resource_host(endpoint):
    captured = []
    cfg, _ = load_policy_config()
    env = {**ENV, "EP": endpoint}
    FoundryEmbeddingClient(FOUNDRY, cfg.embedding, env=env, opener=_opener(captured)).embed(["x"])
    assert captured[0][0] == "https://res.cognitiveservices.azure.com/openai/v1/embeddings"


def test_missing_deployment_env_is_not_configured():
    cfg, _ = load_policy_config()
    with pytest.raises(EmbeddingError) as e:
        FoundryEmbeddingClient(FOUNDRY, cfg.embedding, env={"EP": "https://x", "KEY": "k"})
    assert e.value.kind == "not_configured"


@pytest.mark.parametrize(
    "status,kind", [(401, "auth"), (429, "rate_limited"), (500, "transport"), (404, "bad_request")]
)
def test_http_errors_map_to_kinds_without_text(status, kind):
    with pytest.raises(EmbeddingError) as e:
        _client([], status=status).embed(["secret text"])
    assert e.value.kind == kind and "secret text" not in str(e.value)


def test_plain_http_endpoint_is_refused():
    cfg, _ = load_policy_config()
    c = FoundryEmbeddingClient(
        FOUNDRY, cfg.embedding, env={**ENV, "EP": "http://evil.example"}, opener=_opener([])
    )
    with pytest.raises(EmbeddingError) as e:
        c.embed(["x"])
    assert e.value.kind == "not_configured"


def test_record_then_replay_without_network(tmp_path):
    captured = []
    rec = CachedEmbedder(tmp_path, "m1", inner=_client(captured))
    a = rec.embed(["alpha", "beta", "alpha"])
    assert rec.recorded == 2 and len(captured) == 1  # de-duplicated before the call
    replay = CachedEmbedder(tmp_path, "m1")
    assert np.allclose(replay.embed(["beta", "alpha"]), a[[1, 0]])
    assert replay.provenance["dimensions"] == 3


def test_cache_stores_no_text(tmp_path):
    CachedEmbedder(tmp_path, "m1", inner=_client([])).embed(["Confidential-Zebra-99"])
    blob = (tmp_path / "m1" / "index.json").read_text()
    assert "Zebra" not in blob


def test_replay_miss_is_an_error_not_a_substitute(tmp_path):
    CachedEmbedder(tmp_path, "m1", inner=_client([])).embed(["known"])
    with pytest.raises(EmbeddingError) as e:
        CachedEmbedder(tmp_path, "m1").embed(["known", "unknown"])
    assert e.value.kind == "replay_miss"


def test_offline_embedder_is_labelled_and_deterministic():
    h = HashingEmbedder()
    assert h.model_id == OFFLINE_MODEL_ID == "offline-hash"
    assert np.allclose(h.embed(["usb drive"]), h.embed(["usb drive"]))
