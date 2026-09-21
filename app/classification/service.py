"""`ClassificationService`: the Python API over the hybrid classifier.

One entry point that
* validates ALL configuration at startup and refuses to start on any invalid config (architecture
  section 19), including missing LLM credentials in live mode;
* wraps the hybrid in the never-raises boundary, so every call returns a valid
  `ClassificationResult` (`rejected` / `error` results carry no sensitivity and no text);
* rejects requests written for an unsupported schema version instead of parsing them leniently;
* optionally traces every request (deny-by-default redaction; a trace file never contains text).

Results are RECOMMENDATIONS: the service flags and never blocks or remediates.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator

from guardrails.input import InputGuardConfig, load_input_guard_config
from observability import (
    JsonlSink,
    Tracer,
    build_tracer,
    load_observability_config,
    outcome_attrs,
    request_attrs,
)
from observability.instrument import emit_guardrail_events

from .boundary import NO_HASH, bare_result, classify_safely
from .config_loader import ConfigBundle, ConfigError, default_config_dir, load_config, read_yaml
from .hybrid import HybridClassifier, build_hybrid_classifier
from .routing_config import load_routing_config
from .schemas import ClassificationRequest, ClassificationResult, GuardrailEvent
from .schemas.common import SCHEMA_VERSION, SEMVER_RE, StrictModel

SERVICE_FILE = "service/service.v1.yaml"
_VERSION_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})$")
LlmMode = Literal["foundry", "replay", "record", "off"]
DATASET_LABEL = (
    "AI-generated synthetic dataset — reviewed by one human (provenance per coordinator); "
    "second independent review pending"
)


class TraceCfg(StrictModel):
    enabled: bool


class ServiceConfig(StrictModel):
    service_version: str
    taxonomy_version: str
    default_variant: str
    llm_mode: LlmMode
    max_batch_size: int = Field(ge=1, le=10_000)
    trace: TraceCfg

    @field_validator("service_version", "taxonomy_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError(f"must be a semantic version, got {v!r}")
        return v


def load_service_config(
    bundle: ConfigBundle, config_dir: Path | str | None = None
) -> tuple[ServiceConfig, str]:
    base = Path(config_dir) if config_dir is not None else default_config_dir()
    path = base / SERVICE_FILE
    data, digest = read_yaml(path)
    try:
        cfg = ServiceConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc
    if cfg.taxonomy_version != bundle.taxonomy.taxonomy_version:
        raise ConfigError(
            f"{path}: written against taxonomy {cfg.taxonomy_version} but loaded taxonomy is "
            f"{bundle.taxonomy.taxonomy_version}"
        )
    routing, _ = load_routing_config(bundle.policy, config_dir)
    if cfg.default_variant not in routing.variants:
        raise ConfigError(
            f"{path}: default_variant {cfg.default_variant!r} is not a routing variant"
        )
    return cfg, digest


def parse_schema_version(value: Any) -> tuple[int, int] | None:
    """`"1.0"` -> (1, 0); anything malformed -> None."""
    if not isinstance(value, str):
        return None
    m = _VERSION_RE.match(value)
    return (int(m.group(1)), int(m.group(2))) if m else None


def supported_schema_version() -> tuple[int, int]:
    major, minor = SCHEMA_VERSION.split(".")
    return int(major), int(minor)


def schema_version_supported(value: Any) -> bool:
    """Same major, and not a newer minor than this service understands."""
    got = parse_schema_version(value)
    if got is None:
        return False
    sup = supported_schema_version()
    return got[0] == sup[0] and got[1] <= sup[1]


class ClassificationService:
    """Thread-safe: classification holds no per-request state on the service."""

    def __init__(
        self,
        bundle: ConfigBundle | None = None,
        *,
        config_dir: Path | str | None = None,
        data_dir: Path | str | None = None,
        variant: str | None = None,
        llm_mode: str | None = None,
        cache_dir: Path | str | None = None,
        trace_path: Path | str | None = None,
        salt: bytes | None = None,
    ) -> None:
        self.bundle = bundle or load_config(config_dir)
        self.config, self._config_sha = load_service_config(self.bundle, config_dir)
        self.llm_mode: str = llm_mode or self.config.llm_mode
        if self.llm_mode not in ("foundry", "replay", "record", "off"):
            raise ConfigError(f"unknown llm_mode {self.llm_mode!r}")
        self.variant = variant or self.config.default_variant
        known = load_routing_config(self.bundle.policy, config_dir)[0].variants
        if self.variant not in known:
            raise ConfigError(
                f"unknown routing variant {self.variant!r}; choose from {sorted(known)}"
            )
        self._guard: InputGuardConfig = load_input_guard_config(config_dir)[0]
        self._classifier: HybridClassifier = build_hybrid_classifier(
            self.bundle,
            variant=self.variant,
            data_dir=data_dir,
            config_dir=config_dir,
            llm_mode="replay" if self.llm_mode == "off" else self.llm_mode,
            cache_dir=cache_dir,
            no_llm=self.llm_mode == "off",
        )
        self._tracer: Tracer | None = None
        self._salt = b""
        if trace_path is not None or self.config.trace.enabled:
            if trace_path is None:
                raise ConfigError("trace.enabled is true but no trace path was given")
            obs_cfg, _ = load_observability_config(config_dir)
            self._tracer, salt_env = build_tracer(obs_cfg, [JsonlSink(trace_path)])
            self._salt = salt if salt is not None else salt_env
        self._lock = threading.Lock()
        self._served = 0

    # ---- classification -----------------------------------------------------------------------
    def classify(
        self, payload: ClassificationRequest | dict[str, Any] | str
    ) -> ClassificationResult:
        """Classify one request. NEVER raises: bad input becomes a `rejected` result."""
        payload = self._coerce(payload)
        version_problem = self._check_version(payload)
        if version_problem is not None:
            return self._traced_reject(payload, version_problem)
        if self._tracer is None:
            result = classify_safely(self._classifier, payload, guard=self._guard)
        else:
            result = self._classify_traced(payload)
        with self._lock:
            self._served += 1
        return result

    def classify_text(
        self,
        content: str,
        *,
        filename: str = "document.txt",
        extension: str | None = None,
        request_id: str | None = None,
        **options: Any,
    ) -> ClassificationResult:
        """Convenience for one text: `options` are the request options (mode, max_llm_tier, ...)."""
        ext = (
            extension
            if extension is not None
            else (filename.rsplit(".", 1)[-1] if "." in filename else "txt")
        )
        payload: dict[str, Any] = {
            "request_id": request_id or f"req-{self._next_id()}",
            "document": {"content": content, "filename": filename, "extension": ext},
        }
        if options:
            payload["options"] = options
        return self.classify(payload)

    def classify_many(self, payloads: Iterable[Any]) -> list[ClassificationResult]:
        items = list(payloads)
        if len(items) > self.config.max_batch_size:
            raise ValueError(
                f"batch of {len(items)} exceeds max_batch_size {self.config.max_batch_size}"
            )
        return [self.classify(p) for p in items]

    @property
    def max_document_bytes(self) -> int:
        return self._guard.hard_max_bytes

    def reject(self, request_id: str, reason: str) -> ClassificationResult:
        """A `rejected` result for input that could not even become a request (an oversize or
        undecodable file, an unparseable JSON line). Carries no sensitivity and no text."""
        code = f"input_rejected:{reason}"
        event = GuardrailEvent(type="input_rejected", trigger=reason, action="rejected")
        rid = request_id if re.fullmatch(r"[A-Za-z0-9._:\-]{1,80}", request_id) else "unknown"
        result = bare_result(rid, NO_HASH, "rejected", code, event)
        if self._tracer is not None:
            with self._tracer.trace("classify", dg__stop_reason=code) as root:
                root.set(**outcome_attrs(result))
                emit_guardrail_events(result)
        return result

    # ---- introspection ------------------------------------------------------------------------
    def info(self) -> dict[str, Any]:
        """Versions of everything that decides a result (also present per result in `versions`)."""
        p = self._classifier.params()
        routing, rsha = load_routing_config(self.bundle.policy, self.bundle.config_dir)
        return {
            "service_version": self.config.service_version,
            "schema_version": SCHEMA_VERSION,
            "taxonomy": self.bundle.policy.taxonomy_version,
            "high_risk_config": self.bundle.policy.high_risk_version,
            "routing": {
                "version": routing.routing_version,
                "variant": self.variant,
                "sha256": rsha,
            },
            "llm_mode": self.llm_mode,
            "stages": sorted(p["stages"]),
            "input_guardrail": self._guard.guardrail_version,
            "tracing": self._tracer is not None,
            "levels": self.bundle.policy.level_ids,
            "categories": self.bundle.policy.category_ids,
            "requests_served": self._served,
            "labels_are": DATASET_LABEL,
        }

    def self_check(self) -> dict[str, Any]:
        """A tiny end-to-end call in rules-only mode: proves wiring without any model."""
        r = self.classify_text(
            "Self-check: quarterly cafeteria menu for next week.",
            request_id="self-check",
            mode="rules",
        )
        return {"ok": r.status in ("ok", "degraded", "review_required"), "status": r.status}

    # ---- internals ----------------------------------------------------------------------------
    def _next_id(self) -> int:
        with self._lock:
            self._served += 1
            return self._served

    @staticmethod
    def _coerce(payload: Any) -> Any:
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except ValueError:
                return {}  # not JSON: classify_safely turns it into a rejected result
        return payload

    @staticmethod
    def _check_version(payload: Any) -> str | None:
        raw = (
            payload.schema_version
            if isinstance(payload, ClassificationRequest)
            else (
                payload.get("schema_version", SCHEMA_VERSION)
                if isinstance(payload, dict)
                else SCHEMA_VERSION
            )
        )
        if schema_version_supported(raw):
            return None
        shown = raw if isinstance(raw, str) and _VERSION_RE.match(raw) else "malformed"
        return f"unsupported_schema_version:{shown}"

    def _reject(self, payload: Any, code: str) -> ClassificationResult:
        rid = getattr(payload, "request_id", None)
        if rid is None and isinstance(payload, dict):
            rid = payload.get("request_id")
        return bare_result(
            rid if isinstance(rid, str) and rid else "unknown",
            NO_HASH,
            "rejected",
            code,
            GuardrailEvent(type="input_rejected", trigger="schema_version", action="rejected"),
        )

    def _traced_reject(self, payload: Any, code: str) -> ClassificationResult:
        result = self._reject(payload, code)
        if self._tracer is not None:
            with self._tracer.trace("classify", dg__stop_reason=code) as root:
                root.set(**outcome_attrs(result))
                emit_guardrail_events(result)
        return result

    def _classify_traced(self, payload: Any) -> ClassificationResult:
        assert self._tracer is not None
        attrs: dict[str, Any] = {"dg.classifier": "hybrid", "dg.variant": self.variant}
        try:
            req = (
                payload
                if isinstance(payload, ClassificationRequest)
                else ClassificationRequest.model_validate(payload)
            )
            attrs.update(request_attrs(req, self._salt))
        except Exception:  # noqa: BLE001 - tracing must never change the outcome
            pass
        with self._tracer.trace("classify", **attrs) as root:
            result = classify_safely(self._classifier, payload, guard=self._guard)
            root.set(**outcome_attrs(result))
            emit_guardrail_events(result)
            if result.status == "error":
                root.fail("error_result")
            return result
