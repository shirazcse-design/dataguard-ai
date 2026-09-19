"""Standalone LLM classifier (Approach C) implementing the common `Classifier` interface.

The classification path never raises and never silently falls back to a lower sensitivity:
* transport failure after bounded retries, or malformed output after the repair retry, returns
  `review_required` with `LLM_UNAVAILABLE` and NO level;
* empty input is `rejected`;
* the model's answer is untrusted data: it is schema-validated, its evidence quotes are verified
  against the text sent, and its self-reported confidence is only ever a `verbalized_bucket`.
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any

from app.classification.policy import TaxonomyPolicy
from app.classification.schemas import (
    CategoryPrediction,
    ClassificationRequest,
    ClassificationResult,
    Confidence,
    Evidence,
    GuardrailEvent,
    LevelPrediction,
    Locator,
    ReviewDecision,
    Routing,
    Supports,
    Telemetry,
    Versions,
)
from guardrails.injection import InjectionScanner
from guardrails.output import (
    LLMOutput,
    OutputValidationError,
    build_json_schema,
    cap_bucket,
    mask_excerpt,
    parse_llm_output,
    verify_quote,
)

from .config import LLMConfig
from .prompting import BuiltPrompt, PromptBuilder
from .retry import call_with_retry
from .types import LLMClient, LLMError, LLMRequest, LLMResponse

REPAIR_NOTE = (
    "\n\nYour previous reply was not valid ({reason}). Reply again with ONLY one JSON object that "
    "exactly matches the schema, with labels from the taxonomy and no other text."
)


def _bucket(b: str) -> Confidence:
    return Confidence(kind="verbalized_bucket", raw=b)


class LLMClassifier:
    name = "llm"

    def __init__(
        self,
        client: LLMClient,
        cfg: LLMConfig,
        policy: TaxonomyPolicy,
        builder: PromptBuilder,
        scanner: InjectionScanner,
        *,
        tier: str,
        config_sha256: str,
        guardrail_sha256: str,
        sleep=time.sleep,
    ) -> None:
        self.client = client
        self.cfg = cfg
        self.policy = policy
        self.builder = builder
        self.scanner = scanner
        self.tier = tier
        self.version = cfg.llm_version
        self._sha = config_sha256
        self._guard_sha = guardrail_sha256
        self._sleep = sleep
        self._rng = random.Random(cfg.seed)
        self._schema = build_json_schema(policy.level_ids, policy.category_ids)

    # -- interface -----------------------------------------------------------------------------
    def params(self) -> dict[str, Any]:
        return {
            "llm_version": self.cfg.llm_version,
            "llm_config_sha256": self._sha,
            "tier": self.tier,
            "client": self.client.name,
            "model_id": self.client.model_id,
            "prompt_version": self.builder.version,
            "prompt_sha256": self.builder.prompt_sha256,
            "few_shot_doc_ids": self.builder.fewshot_ids,
            "few_shot_splits": ["train"],
            "fit_splits": [],
            "calibration_splits": [],
            "guardrail_version": self.scanner.version,
            "guardrail_config_sha256": self._guard_sha,
            "temperature": (
                self.cfg.generation.temperature
                if self.cfg.tiers[self.tier].send_temperature
                else None
            ),
            "api": self.cfg.tiers[self.tier].api,
            "max_output_tokens": self.cfg.generation.max_output_tokens,
            "schema_repair_retries": self.cfg.generation.schema_repair_retries,
            "max_input_chars": self.cfg.input.max_input_chars,
            "seed": self.cfg.seed,
        }

    def _request(self, prompt: BuiltPrompt, user_suffix: str = "") -> LLMRequest:
        g = self.cfg.generation
        return LLMRequest(
            system=prompt.system,
            user=prompt.user + user_suffix,
            json_schema=self._schema,
            prompt_version=self.builder.version,
            temperature=g.temperature if self.cfg.tiers[self.tier].send_temperature else None,
            max_output_tokens=g.max_output_tokens,
            timeout_s=g.timeout_s,
        )

    def _cost(self, tokens: dict[str, int]) -> float | None:
        price = self.cfg.tiers[self.tier].price
        if not price.configured or not tokens:
            return None
        return (
            tokens.get("prompt", 0) / 1000 * price.input_per_1k_usd  # type: ignore[operator]
            + tokens.get("completion", 0) / 1000 * price.output_per_1k_usd  # type: ignore[operator]
        )

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        started = time.perf_counter()
        doc = request.document
        base: dict[str, Any] = {
            "request_id": request.request_id,
            "document_id": doc.document_id,
            "content_hash": doc.content_hash(),
        }
        versions = Versions(
            taxonomy=self.policy.taxonomy_version,
            high_risk_config=self.policy.high_risk_version,
            prompt=f"{self.builder.version}@{self.builder.prompt_sha256[:8]}",
            llm_deployment=self.client.model_id,
            classifier=f"{self.name}@{self.version}",
        )
        if not doc.content.strip():
            return ClassificationResult(
                **base,
                status="rejected",
                versions=versions,
                warnings=["empty_content"],
                routing=Routing(stop_reason="empty_content"),
            )

        events: list[GuardrailEvent] = []
        injected = self.scanner.event(self.scanner.scan(doc.content))
        if injected:
            events.append(injected)
        prompt = self.builder.build(doc)
        warnings = ["truncated"] if prompt.truncated else []

        tokens: dict[str, int] = {}
        llm_ms = 0.0
        out: LLMOutput | None = None
        failure: str | None = None
        suffix = ""
        for _ in range(self.cfg.generation.schema_repair_retries + 1):
            try:
                resp, _ = call_with_retry(
                    lambda s=suffix: self.client.complete_structured(self._request(prompt, s)),
                    self.cfg.retry,
                    sleep=self._sleep,
                    rng=self._rng,
                )
            except LLMError as err:
                failure = f"llm_error:{err.kind}"
                break
            llm_ms += resp.latency_ms
            self._add_tokens(tokens, resp)
            try:
                out = parse_llm_output(
                    resp.text,
                    self.policy.level_ids,
                    self.policy.category_ids,
                    max_quotes=self.cfg.evidence.max_quotes,
                )
                break
            except OutputValidationError as bad:
                failure = f"llm_output_invalid:{bad.reason}"
                suffix = REPAIR_NOTE.format(reason=bad.reason)
        telemetry = Telemetry(
            latency_ms={"llm": llm_ms, "total": llm_ms + (time.perf_counter() - started) * 1000},
            tokens=tokens,
            est_cost_usd=self._cost(tokens),
        )
        routing = Routing(stages_run=["llm"])
        if out is None:
            return ClassificationResult(
                **base,
                status="review_required",
                review=ReviewDecision(required=True, reason_codes=["LLM_UNAVAILABLE"]),
                versions=versions,
                telemetry=telemetry,
                guardrail_events=events,
                warnings=[*warnings, failure or "llm_unavailable"],
                routing=routing.model_copy(update={"stop_reason": "llm_unavailable"}),
            )
        return self._build_result(
            request, base, out, prompt, versions, telemetry, events, warnings, routing
        )

    @staticmethod
    def _add_tokens(tokens: dict[str, int], resp: LLMResponse) -> None:
        if resp.prompt_tokens is not None:
            tokens["prompt"] = tokens.get("prompt", 0) + resp.prompt_tokens
        if resp.completion_tokens is not None:
            tokens["completion"] = tokens.get("completion", 0) + resp.completion_tokens

    # -- result construction -------------------------------------------------------------------
    def _build_result(
        self, request, base, out, prompt, versions, telemetry, events, warnings, routing
    ) -> ClassificationResult:
        ev_cfg = self.cfg.evidence
        evidence: list[Evidence] = []
        level_ev: list[str] = []
        cat_ev: dict[str, list[str]] = {}
        n_unverified = 0
        if request.options.include_evidence:
            for q in out.evidence:
                ok, start, end = verify_quote(q.quote[: ev_cfg.max_quote_chars], prompt.sent_text)
                n_unverified += not ok
                ev_id = f"e{len(evidence) + 1}"
                raw = q.quote[: ev_cfg.max_quote_chars]
                evidence.append(
                    Evidence(
                        evidence_id=ev_id,
                        source="llm",
                        supports=Supports(axis=q.supports_axis, value=q.supports_value),
                        type="llm_excerpt",
                        locator=Locator(char_start=start, char_end=end) if ok else None,
                        excerpt=mask_excerpt(raw, ev_cfg.excerpt_chars),
                        excerpt_hash=_sha(raw),
                        strength="n/a",
                        verified=ok,
                        provenance="observed" if ok else "inferred",
                    )
                )
                if q.supports_axis == "level":
                    level_ev.append(ev_id)
                else:
                    cat_ev.setdefault(q.supports_value, []).append(ev_id)
            if out.rationale.strip():
                ev_id = f"e{len(evidence) + 1}"
                evidence.append(
                    Evidence(
                        evidence_id=ev_id,
                        source="llm",
                        supports=Supports(axis="level", value=out.level),
                        type="llm_rationale",
                        excerpt=mask_excerpt(out.rationale, 500),
                        provenance="inferred",
                    )
                )
                level_ev.append(ev_id)

        level_b, cat_b = out.level_confidence, out.category_confidence
        n_quotes = len(out.evidence) if request.options.include_evidence else 0
        if n_quotes and n_unverified:
            cap = "low" if n_unverified == n_quotes else "medium"
            level_b, cat_b = cap_bucket(level_b, cap), cap_bucket(cat_b, cap)
            events.append(
                GuardrailEvent(
                    type="evidence_unverified",
                    trigger=f"{n_unverified}/{n_quotes} quotes not found in the input",
                    action=f"confidence capped at {cap}",
                )
            )

        cats = sorted(out.categories)
        derived = self.policy.derive_high_risk(out.level, cats)
        reasons: list[str] = []
        if n_unverified and derived.value:
            reasons.append("EVIDENCE_UNVERIFIED")
        if prompt.truncated and level_b == "low":
            reasons.append("TRUNCATED_LOW_CONF")
        review = (
            ReviewDecision(required=True, reason_codes=reasons, provisional=True)
            if reasons
            else ReviewDecision()
        )
        return ClassificationResult(
            **base,
            status="review_required" if reasons else "ok",
            level=LevelPrediction(value=out.level, confidence=_bucket(level_b), decided_by="llm"),
            categories=[
                CategoryPrediction(
                    id=c,
                    confidence=_bucket(cat_b),
                    decided_by="llm",
                    evidence_ids=cat_ev.get(c, []),
                )
                for c in cats
            ],
            high_risk=derived,
            review=review,
            evidence=evidence,
            routing=routing.model_copy(
                update={
                    "stop_reason": "llm_decision",
                    "abstained": out.insufficient_information,
                }
            ),
            versions=versions,
            telemetry=telemetry,
            guardrail_events=events,
            warnings=warnings,
        )


def _sha(text: str) -> str:
    from app.classification.schemas.common import sha256_text

    return sha256_text(text)


def build_llm_classifier(
    bundle,
    *,
    tier: str,
    mode: str,
    data_dir: Path | str,
    config_dir: Path | str | None = None,
    model_id: str | None = None,
    cache_dir: Path | str | None = None,
    client: LLMClient | None = None,
    repo_root: Path | None = None,
) -> LLMClassifier:
    """Construct the LLM classifier. Loads ONLY development splits (for few-shot examples).

    mode: `replay` (recorded responses only), `record` (live provider, stored for replay),
    `foundry` (live provider, not stored). `client` overrides the provider for tests.
    """
    import os

    from evals.classification.dataset.build import load_documents
    from evals.classification.lock import DEVELOPMENT_SPLITS
    from guardrails.injection import InjectionScanner, load_injection_config

    from .config import load_llm_config
    from .fewshot import load_fewshot
    from .foundry import FoundryClient
    from .replay import ReplayLLMClient

    policy = bundle.policy
    cfg, sha = load_llm_config(policy, config_dir)
    if tier not in cfg.tiers:
        raise ValueError(f"unknown tier {tier!r}")
    root = repo_root or Path(__file__).resolve().parents[2]
    docs = load_documents(data_dir, splits=list(DEVELOPMENT_SPLITS))
    shots = load_fewshot(root / cfg.prompt.fewshot_file, [d for d in docs if d.split == "train"])
    builder = PromptBuilder(cfg, bundle.taxonomy, shots, root)
    guard_cfg, guard_sha = load_injection_config(config_dir)

    def live() -> LLMClient:
        deployment = os.environ.get(cfg.tiers[tier].deployment_env, "")
        if not deployment:
            raise LLMError(
                "not_configured",
                f"environment variable {cfg.tiers[tier].deployment_env} is not set",
            )
        return FoundryClient(cfg.foundry, deployment, api=cfg.tiers[tier].api)

    if client is None:
        if mode == "foundry":
            client = live()
        elif mode in ("replay", "record"):
            mid = model_id or os.environ.get(cfg.tiers[tier].deployment_env, "")
            if not mid:
                raise ValueError(
                    f"{mode} mode needs --llm-model-id (or {cfg.tiers[tier].deployment_env})"
                )
            client = ReplayLLMClient(
                Path(cache_dir) if cache_dir else root / cfg.cache.dir,
                mid,
                inner=live() if mode == "record" else None,
            )
        else:
            raise ValueError(f"unknown llm mode {mode!r}")
    return LLMClassifier(
        client, cfg, policy, builder, InjectionScanner(guard_cfg),
        tier=tier, config_sha256=sha, guardrail_sha256=guard_sha,
    )  # fmt: skip
