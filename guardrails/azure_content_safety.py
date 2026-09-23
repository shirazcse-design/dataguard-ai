"""Azure AI Content Safety adapter (stdlib HTTP only): Prompt Shields and Groundedness Detection.

Both are a SECOND OPINION alongside the custom, tested-in-CI guardrails (the S0 injection scanner
and the exact-substring evidence verifier) - never their replacement. This is deliberately an
evaluation/audit-time tool (`dataguard-uc4 guardrails ...`), not a per-request runtime guardrail:
adding a live external call to the production classify path would add a new failure mode, latency
and cost with no measured justification yet (see decisions.md D-content-safety-1). It closes the
"injection second opinion" gap noted as not implemented in `docs/uc4/hybrid-engine.md`.

REST shapes (verified against Microsoft Learn, 2026-09-22):
  POST {endpoint}/contentsafety/text:shieldPrompt?api-version=2024-09-01
    body {"userPrompt": str, "documents": [str]}
    -> {"userPromptAnalysis": {"attackDetected": bool},
        "documentsAnalysis": [{"attackDetected": bool}]}
  POST {endpoint}/contentsafety/text:detectGroundedness?api-version=2024-02-15-preview
    body {"domain": "Generic"|"Medical", "task": "QnA"|"Summarization", "text": str,
          "groundingSources": [str]}
    -> {"ungroundedDetected": bool, "ungroundedPercentage": float, "ungroundedDetails": [...]}

Safety: the key comes from the environment, is never logged, and is never sent over plain HTTP to
a non-loopback host (same convention as `app/llm/foundry.py`). Tests use a local fake server; no
unit test contacts a real Content Safety resource.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import Field, ValidationError

from app.classification.config_loader import ConfigError, default_config_dir, read_yaml
from app.classification.schemas.common import SEMVER_RE, StrictModel

CONTENT_SAFETY_FILE = "guardrails/azure_content_safety.v1.yaml"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class PromptShieldsCfg(StrictModel):
    api_version: str
    timeout_s: float = Field(gt=0)


class GroundednessCfg(StrictModel):
    api_version: str
    timeout_s: float = Field(gt=0)
    domain: str
    task: str


class ContentSafetyConfig(StrictModel):
    azure_content_safety_version: str
    endpoint_env: str
    api_key_env: str
    prompt_shields: PromptShieldsCfg
    groundedness: GroundednessCfg


def load_content_safety_config(
    config_dir: Path | str | None = None,
) -> tuple[ContentSafetyConfig, str]:
    base = Path(config_dir) if config_dir is not None else default_config_dir()
    path = base / CONTENT_SAFETY_FILE
    data, digest = read_yaml(path)
    try:
        cfg = ContentSafetyConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc
    if not SEMVER_RE.match(cfg.azure_content_safety_version):
        raise ConfigError(f"{path}: azure_content_safety_version must be a semantic version")
    return cfg, digest


class ContentSafetyError(Exception):
    """A guardrail second opinion could not be obtained. Callers must treat this as `unknown`, not
    as a pass - never silently treat an unreachable second opinion as agreement."""

    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind = kind


class ContentSafetyClient:
    def __init__(
        self,
        cfg: ContentSafetyConfig,
        *,
        env: Mapping[str, str] | None = None,
        opener: Any = urllib.request.urlopen,
    ) -> None:
        self.cfg = cfg
        self._env = env if env is not None else os.environ
        self._opener = opener

    def _required(self, var: str) -> str:
        value = self._env.get(var)
        if not value:
            raise ContentSafetyError("not_configured", f"environment variable {var} is not set")
        return value

    def _base(self) -> str:
        endpoint = self._required(self.cfg.endpoint_env).rstrip("/")
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" and parsed.hostname not in _LOOPBACK:
            raise ContentSafetyError(
                "not_configured", "the endpoint must be https (or loopback for tests)"
            )
        return endpoint

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Key": self._required(self.cfg.api_key_env),
        }

    def _post(
        self, path: str, api_version: str, body: dict[str, Any], timeout_s: float
    ) -> dict[str, Any]:
        url = f"{self._base()}/contentsafety/{path}?api-version={api_version}"
        data = json.dumps(body).encode("utf-8")
        headers = self._headers()
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with self._opener(request, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            raise ContentSafetyError("http_error", f"HTTP {err.code}") from None
        except TimeoutError:
            raise ContentSafetyError("timeout", f"no response within {timeout_s}s") from None
        except urllib.error.URLError as err:
            reason = err.reason
            if isinstance(reason, TimeoutError):
                raise ContentSafetyError("timeout", f"no response within {timeout_s}s") from None
            raise ContentSafetyError("transport", type(reason).__name__) from None
        except (ValueError, UnicodeDecodeError):
            raise ContentSafetyError("transport", "response was not valid JSON") from None

    def shield_prompt(self, *, documents: list[str], user_prompt: str = "") -> dict[str, Any]:
        """Input-guardrail second opinion. `documents` holds the untrusted document text being
        classified (UC4 has no end-user prompt; `user_prompt` stays empty by default)."""
        cfg = self.cfg.prompt_shields
        payload = self._post(
            "text:shieldPrompt",
            cfg.api_version,
            {"userPrompt": user_prompt, "documents": documents},
            cfg.timeout_s,
        )
        doc_flags = [bool(d.get("attackDetected")) for d in payload.get("documentsAnalysis", [])]
        prompt_flag = bool((payload.get("userPromptAnalysis") or {}).get("attackDetected"))
        return {"attack_detected": prompt_flag or any(doc_flags), "raw": payload}

    def detect_groundedness(self, *, text: str, grounding_sources: list[str]) -> dict[str, Any]:
        """Output-guardrail second opinion. `text` is one LLM evidence excerpt or rationale claim;
        `grounding_sources` is the document text it is claimed to come from."""
        cfg = self.cfg.groundedness
        payload = self._post(
            "text:detectGroundedness",
            cfg.api_version,
            {
                "domain": cfg.domain,
                "task": cfg.task,
                "text": text,
                "groundingSources": grounding_sources,
            },
            cfg.timeout_s,
        )
        return {
            "ungrounded_detected": bool(payload.get("ungroundedDetected")),
            "ungrounded_percentage": payload.get("ungroundedPercentage"),
            "raw": payload,
        }
