"""Azure AI Foundry chat-completions adapter (stdlib HTTP only).

**UNVERIFIED against a real endpoint.** It is written from general knowledge of chat-completion
HTTP APIs; endpoint, API version, URL shape, token parameter name and auth are configuration
(`config/llm/llm.v1.yaml` and environment variables), never code, and no model names are assumed.
It is exercised in tests only against a local fake server. Confirm against current Foundry
documentation before any real run (architecture plan section 17).

Safety: credentials come from the environment or a token provider, are never logged, and are never
sent over plain HTTP to a non-loopback host.
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlparse

from .config import FoundryConfig
from .types import LLMClient, LLMError, LLMRequest, LLMResponse

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class FoundryClient(LLMClient):
    name = "foundry"

    def __init__(
        self,
        cfg: FoundryConfig,
        deployment: str,
        *,
        env: Mapping[str, str] | None = None,
        token_provider: Callable[[], str] | None = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.cfg = cfg
        self.model_id = deployment
        self._env = env if env is not None else os.environ
        self._token_provider = token_provider
        self._opener = opener

    # -- configuration -------------------------------------------------------------------------
    def _required(self, var: str) -> str:
        value = self._env.get(var)
        if not value:
            raise LLMError("not_configured", f"environment variable {var} is not set")
        return value

    def _url(self) -> str:
        endpoint = self._required(self.cfg.endpoint_env).rstrip("/")
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" and parsed.hostname not in _LOOPBACK:
            raise LLMError("not_configured", "the endpoint must be https (or loopback for tests)")
        return self.cfg.url_template.format(
            endpoint=endpoint,
            deployment=self.model_id,
            api_version=self._required(self.cfg.api_version_env),
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.cfg.auth == "api_key":
            headers["api-key"] = self._required(self.cfg.api_key_env)
        else:
            provider = self._token_provider or self._default_token_provider()
            headers["Authorization"] = f"Bearer {provider()}"
        return headers

    def _default_token_provider(self) -> Callable[[], str]:
        try:  # optional dependency: not installed by default, never required for tests
            from azure.identity import DefaultAzureCredential  # type: ignore[import-not-found]
        except ImportError as exc:
            raise LLMError(
                "not_configured", "auth 'entra' needs the azure-identity package"
            ) from exc
        credential = DefaultAzureCredential()
        scope = self.cfg.entra_scope
        return lambda: credential.get_token(scope).token

    # -- request -------------------------------------------------------------------------------
    def _body(self, request: LLMRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": request.temperature,
            self.cfg.max_tokens_param: request.max_output_tokens,
        }
        if self.cfg.json_schema_response_format:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "schema": request.json_schema,
                    "strict": True,
                },
            }
        return body

    def complete_structured(self, request: LLMRequest) -> LLMResponse:
        url, headers = self._url(), self._headers()
        data = json.dumps(self._body(request)).encode("utf-8")
        http_request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        started = time.perf_counter()
        try:
            with self._opener(http_request, timeout=request.timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            raise self._map_http_error(err) from None
        except TimeoutError:
            raise LLMError("timeout", f"no response within {request.timeout_s}s") from None
        except urllib.error.URLError as err:
            if isinstance(err.reason, (TimeoutError, socket.timeout)):
                raise LLMError("timeout", f"no response within {request.timeout_s}s") from None
            raise LLMError("transport", type(err.reason).__name__) from None
        except (ValueError, UnicodeDecodeError):
            raise LLMError("transport", "response was not valid JSON") from None
        latency_ms = (time.perf_counter() - started) * 1000
        return self._parse(payload, latency_ms)

    def _parse(self, payload: Any, latency_ms: float) -> LLMResponse:
        try:
            choice = payload["choices"][0]
            if choice.get("finish_reason") == "content_filter":
                raise LLMError("content_filtered", "the provider filtered the response")
            text = choice["message"]["content"]
            if not isinstance(text, str):
                raise KeyError("content")
        except LLMError:
            raise
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LLMError("transport", "unexpected response shape") from None
        usage = payload.get("usage") or {}
        return LLMResponse(
            text=text,
            model_id=self.model_id,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_ms=latency_ms,
        )

    @staticmethod
    def _map_http_error(err: urllib.error.HTTPError) -> LLMError:
        code = err.code
        if code == 429:
            try:
                retry_after = float(err.headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                retry_after = None
            return LLMError("rate_limited", "HTTP 429", retry_after_s=retry_after)
        if code == 408:
            return LLMError("timeout", "HTTP 408")
        if code in (401, 403):
            return LLMError("auth", f"HTTP {code}")
        if code >= 500:
            return LLMError("transport", f"HTTP {code}")
        return LLMError("bad_request", f"HTTP {code}")
