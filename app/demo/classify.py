"""Classify + Decision Trace for the demo: thin wrappers over the EXISTING `ClassificationService`.

Nothing here classifies. `ClassifySession.classify` builds a normal v1 request, calls the real
service, and returns three things:

* ``result``  - the frozen `ClassificationResult`, exactly as the service returned it;
* ``summary`` - a display view of that result (`summarize`), which only relabels fields and never
  turns a failure into a success;
* ``trace``   - the decision trace (`decision_trace`), derived from the request's own redacted
  spans, `result.routing`/`warnings`/`telemetry`, and the active routing variant's config, so a
  stage is shown as executed only if it actually ran, and as "not in this variant" when the config
  disables it.

Replay honesty: in replay, an LLM stage's latency is the RECORDED latency of the original call, and
new text misses the recording (`llm_error:replay_miss`); both are stated in the payload.
"""

from __future__ import annotations

import tempfile
import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from app.classification.routing_config import load_routing_config
from app.classification.service import ClassificationService
from observability import MemorySink, Span

from . import examples

MAX_FILENAME = 200
LEVEL_ORDER = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_CONFIDENTIAL")
SUCCESS = ("ok", "degraded")
REVIEW = ("review_required",)
FAILURE = ("rejected", "error")


class DemoError(Exception):
    """A demo request that cannot be served (bad input, or a mode that failed to start)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ---- the display summary ---------------------------------------------------------------------
def summarize(result: dict[str, Any]) -> dict[str, Any]:
    """A display view of a frozen result. `outcome` is derived from `status` ONLY, so a rejected or
    errored result can never be shown as a success, whatever else it carries."""
    status = result["status"]
    outcome = (
        "success" if status in SUCCESS else "review" if status in REVIEW else "failure"
    )  # an unknown status is a failure, never a success
    level = result.get("level") or None
    review = result.get("review") or {}
    return {
        "status": status,
        "outcome": outcome,
        "degraded": status == "degraded",
        "level": level["value"] if level else None,
        "level_decided_by": level["decided_by"] if level else None,
        "level_confidence": _confidence(level["confidence"]) if level else None,
        "provisional": bool(review.get("provisional")),
        "categories": [
            {
                "id": c["id"],
                "decided_by": c.get("decided_by"),
                "confidence": _confidence(c.get("confidence")),
                "evidence_ids": c.get("evidence_ids", []),
            }
            for c in result.get("categories", [])
        ],
        "high_risk": (result.get("high_risk") or {}).get("value"),
        "high_risk_reasons": (result.get("high_risk") or {}).get("reasons", []),
        "review_required": bool(review.get("required")),
        "review_reasons": review.get("reason_codes", []),
        "review_priority": review.get("priority"),
        "failure_reason": (result.get("warnings") or [None])[0] if outcome == "failure" else None,
        "guardrail_events": result.get("guardrail_events", []),
        "warnings": result.get("warnings", []),
    }


def _confidence(conf: dict[str, Any] | None) -> dict[str, Any] | None:
    if not conf:
        return None
    return {
        "kind": conf.get("kind"),
        "raw": conf.get("raw"),
        "calibrated": bool(conf.get("calibrated")),
    }


# ---- the decision trace ----------------------------------------------------------------------
def decision_trace(
    result: dict[str, Any],
    spans: list[Span],
    variant: Any,
    *,
    llm_mode: str,
    llm_disabled_by_request: bool,
) -> dict[str, Any]:
    """Stages in the order the hybrid router actually considers them, each marked with what really
    happened. Executed = a span or `routing.stages_run` says it ran; nothing is inferred."""
    routing = result.get("routing") or {}
    ran = set(routing.get("stages_run", []))
    warnings = result.get("warnings", [])
    latency = (result.get("telemetry") or {}).get("latency_ms", {})
    names = {s.name for s in spans}
    llm_calls = {
        s.attributes.get("dg.llm.tier"): s.attributes for s in spans if s.name == "llm.call"
    }

    def outcome(stage: str) -> str | None:
        prefix = f"stage:{stage}:"
        hits = [w[len(prefix) :] for w in warnings if w.startswith(prefix)]
        return hits[-1] if hits else None

    stages: list[dict[str, Any]] = []
    guard = list(result.get("guardrail_events", []))
    stages.append(
        _stage(
            "S0",
            "Input guard + injection scan",
            "executed" if "S0.guardrails" in names else "not_recorded",
            f"{len(guard)} guardrail event(s)" if guard else "no guardrail event",
            note=NOTE_S0,
        )
    )
    rules_state = "executed" if "rules" in ran else _absent(variant.rules.enabled)
    stages.append(
        _stage(
            "S1",
            "Rules",
            rules_state,
            RULES_OUTCOME.get(outcome("rules"), outcome("rules")),
            note=NOTE_RULES_SC_ON if variant.rules.short_circuit else NOTE_RULES_SC_OFF,
            latency_ms=latency.get("rules"),
        )
    )
    ml_detail = outcome("ml") or (
        "disabled in this routing variant" if not variant.ml.enabled else "not reached"
    )
    ml_state = "executed" if "ml" in ran else _absent(variant.ml.enabled)
    stages.append(_stage("S2", "ML classifier", ml_state, ml_detail, latency_ms=latency.get("ml")))
    accepted_before = False
    for tier in variant.llm.tier_order:
        label = f"llm:{tier}"
        call = llm_calls.get(tier, {})
        if llm_disabled_by_request:
            state, detail = "disabled_by_request", LLM_OFF_DETAIL
        elif label in ran:
            out = outcome(label)
            state = "failed" if out == "llm_no_level" else "executed"
            detail = LLM_OUTCOME.get(out, out)
        elif accepted_before:
            state, detail = "not_needed", "an earlier tier's verdict was accepted"
        else:
            state, detail = "skipped", "not reached"
        replayed = llm_mode == "replay"
        stages.append(
            _stage(
                "S3",
                f"LLM tier: {tier}",
                state,
                detail,
                latency_ms=latency.get(label),
                latency_note=REPLAY_LATENCY if replayed and state == "executed" else None,
                model=call.get("dg.llm.model_id"),
                served_model=call.get("dg.llm.served_model"),
                replayed=bool(call.get("dg.llm.cached")) if call else None,
                tokens=({"in": call.get("dg.tokens_in"), "out": call.get("dg.tokens_out")}
                        if call else None),
                replay_miss=(replayed and state == "failed"
                             and "llm_error:replay_miss" in warnings),
            )
        )  # fmt: skip
        if state == "executed":
            accepted_before = True
    fusion_detail = "union of categories; level from the most authoritative stage"
    if variant.fusion.rules_floor:
        fusion_detail += "; Rules level is a floor"
    if variant.fusion.category_floors:
        fusion_detail += "; category floors apply"
    fusion_state = "executed" if "S4.fusion" in names else "not_recorded"
    stages.append(_stage("S4", "Fusion", fusion_state, fusion_detail))
    review = result.get("review") or {}
    if review.get("required"):
        s5 = ("escalated", ", ".join(review.get("reason_codes", [])) or "escalated")
    else:
        s5 = ("executed", "no review needed")
    stages.append(_stage("S5", "Review decision", *s5, note=NOTE_S5))
    return {
        "stages": stages,
        "stop_reason": routing.get("stop_reason"),
        "escalations": routing.get("escalations", 0),
        "stages_run": routing.get("stages_run", []),
        "total_latency_ms": latency.get("total"),
        "variant": {
            "rules_short_circuit": variant.rules.short_circuit,
            "ml_enabled": variant.ml.enabled,
            "llm_tier_order": list(variant.llm.tier_order),
            "llm_min_confidence": variant.llm.min_confidence,
        },
        "spans": [_span_view(s) for s in spans],
    }


NOTE_S0 = "Event only: a suspected injection is flagged and restricts downgrades; never lowers."
NOTE_RULES_SC_OFF = (
    "Short-circuit OFF in this variant: a sufficient Rules result does not skip the LLM."
)
NOTE_RULES_SC_ON = "Short-circuit ON: a sufficient Rules result may skip the LLM."
NOTE_S5 = "A missing level is never PUBLIC: an undecidable document goes to review."
RULES_OUTCOME = {"rules_sufficient": "decisive level found", "rules_abstained": "no decisive level"}
LLM_OUTCOME = {"llm_accepted": "verdict accepted", "llm_no_level": "no usable verdict; escalated"}
LLM_OFF_DETAIL = "LLM tiers turned off for this request (max_llm_tier: none)"
REPLAY_LATENCY = "recorded latency of the original live call (replay)"


def _absent(enabled: bool) -> str:
    return "skipped" if enabled else "not_in_variant"


def _stage(sid: str, name: str, state: str, detail: str | None, **extra: Any) -> dict[str, Any]:
    return {"id": sid, "name": name, "state": state, "detail": detail, **extra}


def _span_view(s: Span) -> dict[str, Any]:
    return {
        "name": s.name,
        "status": s.status,
        "duration_ms": (s.end_ns - s.start_ns) / 1e6,
        "start_ns": s.start_ns,
        "parent": s.parent_span_id,
        "id": s.span_id,
        "attributes": dict(s.attributes),  # already redacted by the service's Redactor
    }


# ---- the session -----------------------------------------------------------------------------
class ClassifySession:
    """One `ClassificationService` for the server's lifetime, traced into a MemorySink. In replay
    the LLM stage replays recorded responses; in live it calls Foundry (credentials from the
    environment, read only by the existing adapters)."""

    def __init__(self, mode: str, *, keep_traces: int = 50) -> None:
        self.llm_mode = "replay" if mode == "replay" else "foundry"
        self._sink = MemorySink()
        self._dir = tempfile.mkdtemp(prefix="dataguard-demo-")
        self.error: str | None = None
        self.service: ClassificationService | None = None
        # One request at a time: the service call and the span collection for it are atomic.
        self._lock = threading.RLock()
        self.recent: deque[dict[str, Any]] = deque(maxlen=keep_traces)
        try:
            self.service = ClassificationService(
                llm_mode=self.llm_mode,
                trace_path=Path(self._dir) / "spans.jsonl",
                extra_sinks=[self._sink],
            )
            routing, _ = load_routing_config(self.service.bundle.policy)
            self.variant_name = self.service.variant
            self.variant = routing.variant(self.variant_name)
        except Exception as exc:  # noqa: BLE001 - a mode that cannot start is reported, not hidden
            self.error = f"{type(exc).__name__}: {exc}"

    def classify(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return self._classify(body)

    def _classify(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.service is None:
            raise DemoError(f"the classifier did not start in this mode: {self.error}", status=503)
        svc = self.service
        rid = f"demo-{uuid.uuid4().hex[:12]}"
        example_key = body.get("example")
        llm_tiers = body.get("llm_tiers")
        if example_key is not None:
            ex = examples.BY_KEY.get(example_key)
            if ex is None:
                raise DemoError("unknown example")
            doc = examples.document(ex)
            content, filename = doc.content, doc.filename
            llm_tiers = llm_tiers or ex.llm_tiers  # an example carries its own setting
        else:
            content, filename = body.get("text"), body.get("filename") or "pasted.txt"
            if not isinstance(content, str):
                raise DemoError("text must be a string")
            if not isinstance(filename, str) or len(filename) > MAX_FILENAME:
                raise DemoError("filename must be a string of at most 200 characters")
        llm_tiers = llm_tiers or "default"
        if llm_tiers not in ("default", "off"):
            raise DemoError("llm_tiers must be 'default' or 'off'")
        options: dict[str, Any] = {"mode": "hybrid"}
        if llm_tiers == "off":
            options["max_llm_tier"] = "none"
        if len(content.encode("utf-8")) > svc.max_document_bytes:
            result = svc.reject(rid, "oversize")
        else:
            result = svc.classify(
                {
                    "request_id": rid,
                    "document": {
                        "content": content,
                        "filename": filename,
                        "extension": filename.rsplit(".", 1)[-1] if "." in filename else "txt",
                    },
                    "options": options,
                    "caller": {"caller_id": "interview-demo", "purpose": "demo"},
                }
            )
        return self._package(rid, result, llm_tiers == "off", example_key)

    def classify_upload(self, name: str, data: bytes, llm_tiers: str = "default") -> dict[str, Any]:
        """A file upload, decoded exactly as the CLI does: oversize and non-UTF-8 are rejected by
        the service's own `reject`, never guessed at."""
        if self.service is None:
            raise DemoError(f"the classifier did not start in this mode: {self.error}", status=503)
        with self._lock:
            return self._upload(name, data, llm_tiers)

    def _upload(self, name: str, data: bytes, llm_tiers: str) -> dict[str, Any]:
        assert self.service is not None
        rid = f"demo-{uuid.uuid4().hex[:12]}"
        if len(data) > self.service.max_document_bytes:
            return self._package(rid, self.service.reject(rid, "oversize"), False, None)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return self._package(rid, self.service.reject(rid, "undecodable_text"), False, None)
        return self._classify(
            {"text": text, "filename": name or "upload.txt", "llm_tiers": llm_tiers}
        )

    def _package(
        self, rid: str, result: Any, llm_off: bool, example_key: str | None
    ) -> dict[str, Any]:
        spans = self._take_spans(rid)
        res = result.model_dump(mode="json")
        payload = {
            "request_id": rid,
            "example": example_key,
            "llm_mode": self.llm_mode,
            "result": res,
            "summary": summarize(res),
            "trace": decision_trace(
                res, spans, self.variant, llm_mode=self.llm_mode, llm_disabled_by_request=llm_off
            ),
            "variant": self.variant_name,
            "replay_miss": "llm_error:replay_miss" in res.get("warnings", []),
        }
        self.recent.append({"request_id": rid, "spans": payload["trace"]["spans"]})
        return payload

    def _take_spans(self, rid: str) -> list[Span]:
        """This request's spans (called under `self._lock`, so no other request is writing)."""
        spans = list(self._sink.spans)
        root = next(
            (s for s in spans if s.name == "classify" and s.attributes.get("dg.request_id") == rid),
            None,
        )
        if root is None:
            return []
        mine = [s for s in spans if s.trace_id == root.trace_id]
        self._sink.spans[:] = [s for s in spans if s.trace_id != root.trace_id]
        return sorted(mine, key=lambda s: s.start_ns)
