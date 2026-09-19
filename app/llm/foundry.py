"""Azure AI Foundry chat-completions adapter (stdlib HTTP only).

Shape follows the current Microsoft Foundry REST reference (Azure OpenAI v1 route):
`POST {endpoint}/openai/v1/chat/completions`, deployment name in the body's `model` field, key in
the `api-key` header, `max_completion_tokens`, `response_format` json_schema. A Foundry PROJECT
endpoint (`https://<resource>.services.ai.azure.com/api/projects/<project>`) is reduced to its
resource host, which the docs list as an accepted base for `/openai/v1/`. Endpoint, URL template,
token parameter name and auth remain configuration (`config/llm/llm.v1.yaml` and environment
variables), and no model names are assumed. Tests use a local fake server; a live run records the
provider's `served_model` for provenance.

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
        api: str = "chat_completions",
        env: Mapping[str, str] | None = None,
        token_provider: Callable[[], str] | None = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.cfg = cfg
        self.model_id = deployment
        self.api = api
        self._env = env if env is not None else os.environ
        self._token_provider = token_provider
        self._opener = opener

    # -- configuration -------------------------------------------------------------------------
    def _required(self, var: str) -> str:
        value = self._env.get(var)
        if not value:
            raise LLMError("not_configured", f"environment variable {var} is not set")
        return value

    def _base(self) -> str:
        """The resource base URL. A project endpoint (`.../api/projects/<p>`) is cut back to the
        resource host; anything else is used as given."""
        endpoint = self._required(self.cfg.endpoint_env).rstrip("/")
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" and parsed.hostname not in _LOOPBACK:
            raise LLMError("not_configured", "the endpoint must be https (or loopback for tests)")
        cut = parsed.path.find("/api/projects")
        if cut >= 0:
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path[:cut]}"
        return endpoint

    def _url(self) -> str:
        template = (
            self.cfg.responses_url_template if self.api == "responses" else self.cfg.url_template
        )
        values = {"endpoint": self._base(), "deployment": self.model_id}
        if "{api_version}" in template:
            if not self.cfg.api_version_env:
                raise LLMError("not_configured", "url_template needs api_version_env")
            values["api_version"] = self._required(self.cfg.api_version_env)
        return template.format(**values)

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
        messages = [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ]
        if self.api == "responses":
            body: dict[str, Any] = {
                "model": self.model_id,
                "input": messages,
                "max_output_tokens": request.max_output_tokens,
            }
            if request.temperature is not None:
                body["temperature"] = request.temperature
            if self.cfg.json_schema_response_format:
                body["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": request.schema_name,
                        "schema": request.json_schema,
                        "strict": True,
                    }
                }
            return body
        body = {
            "model": self.model_id,  # the deployment name (v1 route)
            "messages": messages,
            self.cfg.max_tokens_param: request.max_output_tokens,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
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

    def _parse_responses(self, payload: Any, latency_ms: float) -> LLMResponse:
        try:
            if payload.get("status") == "incomplete":
                reason = (payload.get("incomplete_details") or {}).get("reason")
                if reason == "content_filter":
                    raise LLMError("content_filtered", "the provider filtered the response")
            texts = [
                part["text"]
                for item in payload["output"]
                if item.get("type") == "message"
                for part in item["content"]
                if part.get("type") == "output_text" and isinstance(part.get("text"), str)
            ]
        except LLMError:
            raise
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LLMError("transport", "unexpected response shape") from None
        usage = payload.get("usage") or {}
        served = payload.get("model")
        return LLMResponse(
            text="".join(texts),  # empty when the model ran out of tokens before answering
            model_id=self.model_id,
            served_model=served if isinstance(served, str) else None,
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
            latency_ms=latency_ms,
        )

    def _parse(self, payload: Any, latency_ms: float) -> LLMResponse:
        if self.api == "responses":
            return self._parse_responses(payload, latency_ms)
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
        served = payload.get("model")
        return LLMResponse(
            text=text,
            model_id=self.model_id,
            served_model=served if isinstance(served, str) else None,
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
