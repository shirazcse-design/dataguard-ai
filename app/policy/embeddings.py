"""Embedding clients: Foundry (live), a record/replay cache, and an OFFLINE hashing embedder.

* `FoundryEmbeddingClient` calls `POST {resource}/openai/v1/embeddings` on the deployment YOU
  created manually in Foundry (docs/uc6/foundry-embeddings-setup.md). It reuses the UC4
  `FoundryClient`'s endpoint normalisation, https-only rule, auth headers and HTTP error mapping.
* `CachedEmbedder` makes a recorded run replayable without Azure. Vectors are keyed by a hash of
  (model id, dimensions, text); the cache stores hashes and vectors only, never text. A replay miss
  is an error (`EmbeddingError("replay_miss")`), never a silent substitute.
* `HashingEmbedder` is NOT a semantic model: feature-hashed word/character n-grams. It exists so
  unit tests and an explicitly chosen `--embed-mode offline` run work with no credentials; every
  report that uses it says so (`model_id = "offline-hash"`).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import numpy as np

from app.llm.config import FoundryConfig
from app.llm.foundry import FoundryClient
from app.llm.types import LLMError

from .config import EmbeddingConfig

OFFLINE_MODEL_ID = "offline-hash"
CACHE_SCHEMA = 1


class EmbeddingError(Exception):
    """An embedding failure. Messages never contain the embedded text."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


class Embedder(Protocol):
    model_id: str

    def embed(self, texts: list[str]) -> np.ndarray:
        """L2-normalised float32 vectors, one row per text."""
        ...


def _normalise(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


class FoundryEmbeddingClient(FoundryClient):
    name = "foundry-embeddings"

    def __init__(
        self,
        foundry: FoundryConfig,
        cfg: EmbeddingConfig,
        *,
        env: Mapping[str, str] | None = None,
        token_provider: Callable[[], str] | None = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        env = env if env is not None else os.environ
        deployment = env.get(cfg.deployment_env)
        if not deployment:
            raise EmbeddingError("not_configured", f"{cfg.deployment_env} is not set")
        super().__init__(foundry, deployment, env=env, token_provider=token_provider, opener=opener)
        self.ecfg = cfg
        self.tokens_used = 0

    def _base(self) -> str:
        """The resource host. Beyond the UC4 rule (a project endpoint is cut back), a pasted
        deployment Target URI (`.../openai/deployments/<d>/embeddings?api-version=...`) is cut
        back too: the portal shows that full URI next to the key, so it is what gets copied."""
        parsed = urlparse(super()._base())
        cut = parsed.path.find("/openai")
        path = parsed.path[:cut] if cut >= 0 else parsed.path
        return f"{parsed.scheme}://{parsed.netloc}{path}".rstrip("/")

    def embed(self, texts: list[str]) -> np.ndarray:
        rows: list[list[float]] = []
        for i in range(0, len(texts), self.ecfg.batch_size):
            rows.extend(self._batch(texts[i : i + self.ecfg.batch_size]))
        return _normalise(np.array(rows))

    def _batch(self, texts: list[str]) -> list[list[float]]:
        try:
            url = self.ecfg.url_template.format(endpoint=self._base(), deployment=self.model_id)
            headers = self._headers()
        except LLMError as err:
            raise EmbeddingError(err.kind, str(err)) from None
        body: dict[str, Any] = {"model": self.model_id, "input": texts}
        if self.ecfg.dimensions:
            body["dimensions"] = self.ecfg.dimensions
        request = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with self._opener(request, timeout=self.ecfg.timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            mapped = self._map_http_error(err)
            raise EmbeddingError(mapped.kind, str(mapped)) from None
        except TimeoutError:
            raise EmbeddingError("timeout", f"no response within {self.ecfg.timeout_s}s") from None
        except urllib.error.URLError as err:
            raise EmbeddingError("transport", type(err.reason).__name__) from None
        except (ValueError, UnicodeDecodeError):
            raise EmbeddingError("transport", "response was not valid JSON") from None
        try:
            data = sorted(payload["data"], key=lambda d: d["index"])
            vectors = [d["embedding"] for d in data]
        except (KeyError, TypeError):
            raise EmbeddingError("transport", "unexpected response shape") from None
        if len(vectors) != len(texts) or len({len(v) for v in vectors}) != 1:
            raise EmbeddingError("transport", "wrong number or size of embeddings returned")
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        tokens = usage.get("prompt_tokens")
        if isinstance(tokens, int):
            self.tokens_used += tokens
        return vectors


class HashingEmbedder:
    """Offline, deterministic, NOT semantic. For tests and explicitly offline runs only."""

    model_id = OFFLINE_MODEL_ID

    def __init__(self, dims: int = 512) -> None:
        from sklearn.feature_extraction.text import HashingVectorizer

        self._vec = HashingVectorizer(
            n_features=dims, analyzer="char_wb", ngram_range=(3, 4), alternate_sign=False, norm=None
        )

    def embed(self, texts: list[str]) -> np.ndarray:
        return _normalise(self._vec.transform([t.lower() for t in texts]).toarray())


def text_key(model_id: str, dimensions: int | None, text: str) -> str:
    return hashlib.sha256(f"{model_id}\n{dimensions}\n{text}".encode()).hexdigest()


class CachedEmbedder:
    """Replay (`inner=None`) or record (`inner` set: misses are embedded live and stored).

    Layout: `<cache_dir>/<model_id>/vectors.npy` (float32 rows) + `index.json` (hash -> row,
    plus provenance). No text is stored.
    """

    def __init__(
        self,
        cache_dir: Path | str,
        model_id: str,
        dimensions: int | None = None,
        *,
        inner: Embedder | None = None,
    ) -> None:
        self.dir = Path(cache_dir) / model_id
        self.model_id = model_id
        self.dimensions = dimensions
        self.inner = inner
        self.recorded = 0
        self._index: dict[str, int] = {}
        self._vectors: np.ndarray | None = None
        self._meta: dict[str, Any] = {}
        idx = self.dir / "index.json"
        if idx.exists():
            data = json.loads(idx.read_text(encoding="utf-8"))
            if data.get("schema") != CACHE_SCHEMA or data.get("model_id") != model_id:
                raise EmbeddingError("replay_miss", "embedding cache does not match its model id")
            self._index = data["rows"]
            self._meta = {k: v for k, v in data.items() if k != "rows"}
            self._vectors = np.load(self.dir / "vectors.npy")

    def has(self, text: str) -> bool:
        return text_key(self.model_id, self.dimensions, text) in self._index

    def embed(self, texts: list[str]) -> np.ndarray:
        keys = [text_key(self.model_id, self.dimensions, t) for t in texts]
        missing = {k: t for k, t in zip(keys, texts, strict=True) if k not in self._index}
        if missing:
            if self.inner is None:
                raise EmbeddingError("replay_miss", f"{len(missing)} text(s) not recorded")
            self._record(list(missing.values()), list(missing.keys()))
        assert self._vectors is not None
        return self._vectors[[self._index[k] for k in keys]]

    def _record(self, texts: list[str], keys: list[str]) -> None:
        assert self.inner is not None
        started = time.perf_counter()
        new = self.inner.embed(texts)
        base = 0 if self._vectors is None else len(self._vectors)
        if self._vectors is not None and new.shape[1] != self._vectors.shape[1]:
            raise EmbeddingError("bad_request", "dimension changed; use a new cache model id")
        self._vectors = new if self._vectors is None else np.vstack([self._vectors, new])
        for n, key in enumerate(keys):  # unique: `embed` de-duplicates before recording
            self._index[key] = base + n
        self.recorded += len(texts)
        self.dir.mkdir(parents=True, exist_ok=True)
        np.save(self.dir / "vectors.npy", self._vectors)
        self._meta = {
            "schema": CACHE_SCHEMA,
            "model_id": self.model_id,
            "dimensions": int(self._vectors.shape[1]),
            "recorded_from": getattr(self.inner, "name", type(self.inner).__name__),
            "served_deployment": getattr(self.inner, "model_id", None),
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "last_batch_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        (self.dir / "index.json").write_text(
            json.dumps({**self._meta, "rows": self._index}, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @property
    def provenance(self) -> dict[str, Any]:
        return dict(self._meta)
