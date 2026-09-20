"""Hybrid classifier (Approach D): deterministic harness logic that routes each document through
Rules, an optional ML stage and LLM tiers, then fuses the accepted results.

Principles (architecture section 11, PRD 11.1):
* The harness, not a model, owns thresholds, escalation, conflict handling, review and floors.
* It never raises and never silently falls back to a lower sensitivity: a document that no stage can
  decide becomes `review_required` (with the fail-safe provisional label when one exists).
* It only composes the stages' public results; it reads no gold labels and no stage internals.
"""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Any

from guardrails.injection import InjectionScanner
from guardrails.input import InputGuardConfig, InputVerdict, check_document
from observability.instrument import outcome_attrs
from observability.trace import current_span, span

from .config_loader import ConfigBundle
from .fusion import StageResult, fuse
from .interfaces import Classifier
from .policy import TaxonomyPolicy
from .review import build_review
from .router import Acceptance, has_conflict, llm_accepted, ml_accepted, rules_sufficient
from .routing_config import RoutingConfig, VariantConfig, load_routing_config
from .schemas import (
    ClassificationRequest,
    ClassificationResult,
    GuardrailEvent,
    Routing,
    Telemetry,
    Versions,
)


class HybridClassifier:
    name = "hybrid"

    def __init__(
        self,
        policy: TaxonomyPolicy,
        routing: RoutingConfig,
        variant: str,
        *,
        routing_sha256: str,
        rules: Classifier | None = None,
        ml: Classifier | None = None,
        llms: dict[str, Classifier] | None = None,
        scanner: InjectionScanner | None = None,
        input_guard: InputGuardConfig | None = None,
    ) -> None:
        self.policy = policy
        self.variant = variant
        self.cfg: VariantConfig = routing.variant(variant)
        self.version = routing.routing_version
        self._sha = routing_sha256
        self._rules, self._ml, self._llms = rules, ml, dict(llms or {})
        self._scanner = scanner
        self._input_cfg = input_guard
        missing = [
            n
            for n, need, have in (
                ("rules", self.cfg.rules.enabled, rules),
                ("ml", self.cfg.ml.enabled, ml),
            )
            if need and have is None
        ]
        missing += [f"llm:{t}" for t in self.cfg.llm.tier_order if t not in self._llms]
        if missing:
            raise ValueError(f"variant {variant!r} needs stages that were not provided: {missing}")

    # -- interface -----------------------------------------------------------------------------
    def params(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "routing_version": self.version,
            "routing_config_sha256": self._sha,
            "variant": self.variant,
            "variant_config": self.cfg.model_dump(),
            "fit_splits": [],
            "calibration_splits": [],
            "stages": {},
        }
        if self.cfg.rules.enabled and self._rules is not None:
            out["stages"]["rules"] = {"name": self._rules.name, "version": self._rules.version}
        if self.cfg.ml.enabled and self._ml is not None:
            p = self._ml.params()
            out["fit_splits"] = p.get("fit_splits", [])
            out["calibration_splits"] = p.get("calibration_splits", [])
            out["stages"]["ml"] = {"model_id": p.get("model_id"), "version": self._ml.version}
        for tier in self.cfg.llm.tier_order:
            p = self._llms[tier].params()
            out["stages"][f"llm:{tier}"] = {
                k: p.get(k) for k in ("model_id", "prompt_version", "prompt_sha256", "api")
            }
            out["few_shot_doc_ids"] = p.get("few_shot_doc_ids", [])
        return out

    # -- one stage, never raising --------------------------------------------------------------
    def _call(
        self, label: str, clf: Classifier, request: ClassificationRequest, notes: list[str]
    ) -> ClassificationResult | None:
        with span(_stage_span(label), dg__stage=label) as sp:
            try:
                res = clf.classify(request)
            except Exception as exc:  # noqa: BLE001 - a broken stage must not kill the request
                notes.append(f"stage_error:{label}:{type(exc).__name__}")
                sp.fail(type(exc).__name__)
                return None
            if (
                res.request_id != request.request_id
                or res.content_hash != request.document.content_hash()
            ):
                notes.append(f"stage_error:{label}:contract_violation")
                sp.fail("contract_violation")
                return None
            sp.set(
                dg__outcome__status=res.status,
                dg__latency_ms=float(res.telemetry.latency_ms.get("total", 0.0)),
                dg__tokens_in=int(res.telemetry.tokens.get("prompt", 0)),
                dg__tokens_out=int(res.telemetry.tokens.get("completion", 0)),
            )
            if res.telemetry.est_cost_usd is not None:
                sp.set(dg__est_cost_usd=float(res.telemetry.est_cost_usd))
            return res

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        started = time.perf_counter()
        cfg, pol = self._effective(request.options), self.policy
        doc = request.document
        opts = request.options
        verdict = check_document(doc, self._input_cfg) if self._input_cfg else None
        if verdict is not None and not verdict.ok:
            return self._rejected(request, verdict)
        failed: list[str] = []  # configured stages that errored or were unavailable
        notes: list[str] = []  # compact trace of every routing decision
        events: list[GuardrailEvent] = []
        stages_run: list[str] = []
        latency: dict[str, float] = {}
        tokens: Counter[str] = Counter()
        costs: list[float] = []
        accepted: list[StageResult] = []
        seen: list[StageResult] = []  # every stage that produced a usable level (provisional label)
        review_codes: list[str] = []

        def note_stage(label: str, res: ClassificationResult | None) -> None:
            stages_run.append(label)
            if res is None:
                return
            latency[label] = float(res.telemetry.latency_ms.get("total", 0.0))
            tokens.update(res.telemetry.tokens)
            if res.telemetry.est_cost_usd is not None:
                costs.append(res.telemetry.est_cost_usd)

        flagged = False
        with span("S0.guardrails", dg__stage="guardrails") as s0:
            if verdict is not None and verdict.event() is not None:
                events.append(verdict.event())  # type: ignore[arg-type]
                s0.set(dg__input__reason="truncate")
            if self._scanner is not None:
                ev = self._scanner.event(self._scanner.scan(doc.content))
                if ev is not None:
                    events.append(ev)
                    flagged = True
            s0.set(dg__injection__flagged=flagged)

        # ---- S1 Rules ----------------------------------------------------------------------
        rules_ok = False
        rules_res: ClassificationResult | None = None
        if cfg.rules.enabled and self._rules is not None:
            rules_res = self._call("rules", self._rules, request, notes)
            note_stage("rules", rules_res)
            if rules_res is None or rules_res.status == "degraded":
                failed.append("rules")
            acc = rules_sufficient(rules_res, cfg.rules)
            notes.append(f"stage:rules:{acc.reason}")
            current_span().event("route", dg__stage="rules", dg__reason=acc.reason)
            if acc.accepted and rules_res is not None:
                rules_ok = True
                sr = StageResult("rules", "rules", rules_res)
                accepted.append(sr)
                seen.append(sr)
                if cfg.rules.short_circuit:
                    return self._assemble(
                        request, accepted, seen, [], rules_ok, None, stages_run, latency, tokens,
                        costs, notes, events, escalations=0, stop="rules_short_circuit",
                        short_circuited=True, started=started, failed=failed,
                    )  # fmt: skip

        # ---- S2 ML (optional) --------------------------------------------------------------
        if cfg.ml.enabled and self._ml is not None:
            ml_res = self._call("ml", self._ml, request, notes)
            note_stage("ml", ml_res)
            if ml_res is None:
                failed.append("ml")
            acc = ml_accepted(ml_res, cfg.ml)
            notes.append(f"stage:ml:{acc.reason}")
            current_span().event("route", dg__stage="ml", dg__reason=acc.reason)
            if acc.accepted and ml_res is not None:
                if (
                    rules_ok
                    and rules_res is not None
                    and has_conflict(ml_res, rules_res, pol, cfg.conflict.level_rank_gap)
                ):
                    notes.append("conflict:ml~rules")
                else:
                    sr = StageResult("ml", "ml", ml_res)
                    accepted.append(sr)
                    seen.append(sr)
                    if cfg.ml.short_circuit:
                        return self._assemble(
                            request, accepted, seen, [], rules_ok, None, stages_run, latency,
                            tokens, costs, notes, events, escalations=0, stop="ml_short_circuit",
                            short_circuited=True, started=started, failed=failed,
                        )  # fmt: skip

        # ---- S3 LLM tiers ------------------------------------------------------------------
        refs = [s for s in accepted if s.kind in ("rules", "ml")]
        accepted_llm: StageResult | None = None
        conflicted = budget_out = False
        escalations = calls = 0
        llm_results: list[tuple[str, ClassificationResult]] = []
        for tier in cfg.llm.tier_order:
            if calls >= cfg.budget.max_llm_calls or self._request_budget_hit(
                opts.budget, latency, costs, notes
            ):
                budget_out = True
                notes.append(f"budget_exhausted_before:llm:{tier}")
                break
            label = f"llm:{tier}"
            res = self._call(label, self._llms[tier], request, notes)
            calls += 1
            note_stage(label, res)
            if res is None or res.level is None:
                notes.append(f"stage:{label}:llm_no_level")
                failed.append(label)
                notes.extend(w for w in (res.warnings if res else []) if w.startswith("llm_"))
                escalations += 1
                continue
            llm_results.append((tier, res))
            seen.append(StageResult("llm", label, res))
            acc: Acceptance = llm_accepted(res, cfg.llm)
            notes.append(f"stage:{label}:{acc.reason}")
            current_span().event("route", dg__stage=label, dg__reason=acc.reason)
            if not acc.accepted:
                if acc.reason.startswith("llm_review:"):
                    review_codes += [c for c in acc.reason.split(":", 1)[1].split(",") if c]
                escalations += 1
                continue
            if any(has_conflict(res, r.result, pol, cfg.conflict.level_rank_gap) for r in refs):
                conflicted = True
                notes.append(f"conflict:{label}~{'+'.join(r.label for r in refs)}")
                current_span().event("route", dg__stage=label, dg__reason="conflict")
                escalations += 1
                continue
            accepted_llm = StageResult("llm", label, res)
            break

        if accepted_llm is not None:
            accepted.append(accepted_llm)
            review_codes = []  # an accepted, conflict-free answer resolves earlier doubts
        elif cfg.llm.tier_order:
            # nothing decided: say why, in a fixed order of importance
            if conflicted:
                review_codes.append("DETECTOR_CONFLICT")
            if budget_out:
                review_codes.append("BUDGET_EXHAUSTED")
            if not llm_results:
                review_codes.append("LLM_UNAVAILABLE")
            if not review_codes:
                review_codes.append("LOW_CONFIDENCE")
        elif not accepted:
            review_codes.append("LOW_CONFIDENCE")  # no LLM configured and nothing sufficient

        # ---- injection restriction: raise, never lower --------------------------------------
        raise_to: str | None = None
        if flagged and cfg.injection.restrict_downgrade and accepted_llm is not None and refs:
            ref_level = max((r.result.level.value for r in refs), key=pol.level_rank)  # type: ignore[union-attr]
            if pol.level_rank(accepted_llm.result.level.value) < pol.level_rank(ref_level):  # type: ignore[union-attr]
                raise_to = ref_level
                review_codes.append("INJECTION_DOWNGRADE_ATTEMPT")
                events.append(
                    GuardrailEvent(
                        type="injection_downgrade_blocked",
                        trigger="llm level below an accepted Rules/ML level on a flagged document",
                        action=f"level kept at {ref_level}; review requested",
                    )
                )

        if review_codes and accepted_llm is None:
            # fail-safe provisional label: the highest level any usable stage produced
            levels = [s.result.level.value for s in seen if s.result.level is not None]
            if levels:
                raise_to = max(levels, key=pol.level_rank)
        stop = (
            f"escalated_{accepted_llm.label}" if accepted_llm and escalations else
            (accepted_llm.label + "_accepted" if accepted_llm else "no_stage_accepted")
        )  # fmt: skip
        if not cfg.llm.tier_order and accepted and not review_codes:
            stop = "no_llm_configured"
        return self._assemble(
            request, accepted, seen, review_codes, rules_ok, raise_to, stages_run, latency, tokens,
            costs, notes, events, escalations=escalations, stop=stop, short_circuited=False,
            started=started, failed=failed,
        )  # fmt: skip

    # -- request options -----------------------------------------------------------------------
    def _effective(self, opts) -> VariantConfig:
        """The variant restricted by the request's options (mode, max LLM tier)."""
        cfg = self.cfg
        order = ["small", "mid", "large"]
        tiers = list(cfg.llm.tier_order)
        if opts.max_llm_tier == "none":
            tiers = []
        else:
            cap = order.index(opts.max_llm_tier)
            tiers = [t for t in tiers if order.index(t) <= cap]
        rules_on, ml_on = cfg.rules.enabled, cfg.ml.enabled
        if opts.mode == "rules":
            ml_on, tiers = False, []
        elif opts.mode == "ml":
            rules_on, tiers = False, []
        elif opts.mode == "llm":
            rules_on = ml_on = False
        if (
            tiers == cfg.llm.tier_order
            and rules_on == cfg.rules.enabled
            and ml_on == cfg.ml.enabled
        ):
            return cfg
        return cfg.model_copy(
            update={
                "rules": cfg.rules.model_copy(update={"enabled": rules_on}),
                "ml": cfg.ml.model_copy(update={"enabled": ml_on}),
                "llm": cfg.llm.model_copy(update={"tier_order": tiers}),
            }
        )

    @staticmethod
    def _request_budget_hit(budget, latency: dict[str, float], costs: list[float], notes) -> bool:
        """Per-request latency/cost caps, checked before each LLM call. A cost cap can only be
        enforced when a price exists (costs are estimated from configured prices; none is invented),
        except a cap of 0, which means no spend at all."""
        if budget.max_latency_ms is not None and sum(latency.values()) >= budget.max_latency_ms:
            return True
        cap = budget.max_cost_usd
        if cap is not None:
            if cap == 0 or (costs and sum(costs) >= cap):
                return True
            if not costs and "cost_cap_unenforceable:no_price" not in notes:
                notes.append("cost_cap_unenforceable:no_price")
        return False

    def _rejected(
        self, request: ClassificationRequest, verdict: InputVerdict
    ) -> ClassificationResult:
        doc = request.document
        try:
            digest = doc.content_hash()
        except UnicodeEncodeError:  # undecodable text cannot even be hashed
            digest = "0" * 64
        event = verdict.event()
        result = ClassificationResult(
            request_id=request.request_id,
            document_id=doc.document_id,
            content_hash=digest,
            status="rejected",
            versions=Versions(
                taxonomy=self.policy.taxonomy_version,
                high_risk_config=self.policy.high_risk_version,
                router_config=f"{self.version}:{self.variant}@{self._sha[:8]}",
                classifier=f"{self.name}@{self.version}",
            ),
            routing=Routing(stop_reason=f"input_rejected:{verdict.reason}"),
            guardrail_events=[event] if event else [],
            warnings=[f"input_rejected:{verdict.reason}"],
        )
        with span(
            "S0.guardrails", dg__stage="guardrails", dg__input__reason=verdict.reason or ""
        ) as s0:
            s0.set(**outcome_attrs(result))
        return result

    # -- result construction -------------------------------------------------------------------
    def _assemble(
        self,
        request: ClassificationRequest,
        accepted: list[StageResult],
        seen: list[StageResult],
        review_codes: list[str],
        rules_ok: bool,
        raise_to: str | None,
        stages_run: list[str],
        latency: dict[str, float],
        tokens: Counter[str],
        costs: list[float],
        notes: list[str],
        events: list[GuardrailEvent],
        *,
        escalations: int,
        stop: str,
        short_circuited: bool,
        started: float,
        failed: list[str],
    ) -> ClassificationResult:
        pol = self.policy
        doc = request.document
        decided = not review_codes
        basis = accepted if decided else _dedupe(seen + accepted)
        with span("S4.fusion", dg__stage="fusion") as s4:
            fused = fuse(pol, self.cfg.fusion, basis, rules_is_floor=rules_ok, raise_to=raise_to)
            s4.set(dg__notes_count=len(fused.notes) if fused else 0)
        include_ev = request.options.include_evidence
        if fused is not None:
            level = fused.level
            cats = fused.categories
            high_risk = pol.derive_high_risk(level.value, [c.id for c in cats])
            evidence = fused.evidence if include_ev else []
            if not include_ev:
                cats = [c.model_copy(update={"evidence_ids": []}) for c in cats]
            notes.extend(f"fusion:{n}" for n in fused.notes)
        else:
            level, cats, high_risk, evidence = None, [], None, []
        review = build_review(
            review_codes,
            provisional=fused is not None,
            provisional_high_risk=bool(high_risk and high_risk.value),
        )
        status = "review_required"
        if decided and fused is not None:
            # decided, but a configured stage errored or was unavailable: say so
            status = "degraded" if failed else "ok"
            if failed:
                notes.append("degraded:" + ",".join(dict.fromkeys(failed)))
        if status == "review_required" and not review.required:
            review = build_review(
                ["LLM_UNAVAILABLE"], provisional=False, provisional_high_risk=False
            )
        lat = {**latency, "total": float(sum(latency.values()))}
        llm_stage = next((s for s in reversed(basis) if s.kind == "llm"), None)
        rules_stage = next((s for s in basis if s.kind == "rules"), None)
        ml_stage = next((s for s in basis if s.kind == "ml"), None)
        versions = Versions(
            taxonomy=pol.taxonomy_version,
            high_risk_config=pol.high_risk_version,
            ruleset=rules_stage.result.versions.ruleset if rules_stage else None,
            ml_model=ml_stage.result.versions.ml_model if ml_stage else None,
            prompt=llm_stage.result.versions.prompt if llm_stage else None,
            llm_deployment=llm_stage.result.versions.llm_deployment if llm_stage else None,
            router_config=f"{self.version}:{self.variant}@{self._sha[:8]}",
            classifier=f"{self.name}@{self.version}",
        )
        stage_warnings = [
            f"stage_warning:{s.label}:{w}"
            for s in basis
            for w in s.result.warnings
            if w in ("truncated", "input_truncated")
        ]
        result = ClassificationResult(
            request_id=request.request_id,
            document_id=doc.document_id,
            content_hash=doc.content_hash(),
            status=status,  # type: ignore[arg-type]
            level=level,
            categories=cats,
            high_risk=high_risk,
            review=review,
            evidence=evidence,
            routing=Routing(
                stages_run=stages_run,
                stop_reason=stop,
                escalations=escalations,
                short_circuited=short_circuited,
                abstained=False,
            ),
            versions=versions,
            telemetry=Telemetry(
                latency_ms=lat,
                tokens=dict(tokens),
                est_cost_usd=sum(costs) if costs else None,
            ),
            guardrail_events=events,
            warnings=[*notes, *stage_warnings],
        )
        with span("S5.result", dg__stage="result") as s5:
            attrs = outcome_attrs(result)
            for k in ("dg.latency_ms", "dg.tokens_in", "dg.tokens_out"):
                attrs.pop(k)  # request totals belong on the root span, not on a stage span
            s5.set(**attrs, dg__failed_stages=list(dict.fromkeys(failed)))
        return result


def _stage_span(label: str) -> str:
    if label == "rules":
        return "S1.rules"
    if label == "ml":
        return "S2.ml"
    return "S3." + label.replace(":", ".")


def _dedupe(stages: list[StageResult]) -> list[StageResult]:
    seen: dict[str, StageResult] = {}
    for s in stages:
        seen.setdefault(s.label, s)
    return list(seen.values())


def build_hybrid_classifier(
    bundle: ConfigBundle,
    *,
    variant: str | None = None,
    data_dir: Path | str | None = None,
    config_dir: Path | str | None = None,
    llm_mode: str = "replay",
    cache_dir: Path | str | None = None,
    components: dict[str, Any] | None = None,
    no_llm: bool = False,
) -> HybridClassifier:
    """Build the hybrid for `variant` (default: the configured default variant).

    `components` may pass pre-built stages ({"rules": ..., "ml": ..., "llms": {...}}) so that many
    variants can share one trained ML model and one set of replay clients. Loads only development
    splits and never a locked-test authorisation.
    """
    from guardrails.injection import load_injection_config

    policy = bundle.policy
    routing, sha = load_routing_config(policy, config_dir)
    name = variant or routing.default_variant
    if no_llm:  # a runtime variant: the chosen one with every LLM tier removed
        base_over = dict(routing.variants[name])
        base_over["llm"] = {**base_over.get("llm", {}), "tier_order": []}
        name = f"{name}+no_llm"
        routing.variants[name] = base_over
    cfg = routing.variant(name)
    comp = dict(components or {})
    if cfg.rules.enabled and "rules" not in comp:
        from rules import build_rules_classifier

        comp["rules"] = build_rules_classifier(bundle, config_dir)
    if cfg.ml.enabled and "ml" not in comp:
        from ml.classification import build_ml_classifier

        comp["ml"] = build_ml_classifier(bundle, data_dir=data_dir, config_dir=config_dir)
    llms: dict[str, Classifier] = dict(comp.get("llms", {}))
    for tier in cfg.llm.tier_order:
        if tier not in llms:
            from app.llm import build_llm_classifier

            llms[tier] = build_llm_classifier(
                bundle,
                tier=tier,
                mode=llm_mode,
                data_dir=data_dir or _default_data_dir(),
                config_dir=config_dir,
                cache_dir=cache_dir,
            )
    input_guard = comp.get("input_guard")
    if input_guard is None:
        from guardrails.input import load_input_guard_config

        input_guard = load_input_guard_config(config_dir)[0]
    scanner = comp.get("scanner")
    if scanner is None:
        gcfg, _ = load_injection_config(config_dir)
        scanner = InjectionScanner(gcfg)
    return HybridClassifier(
        policy, routing, name, routing_sha256=sha, rules=comp.get("rules"), ml=comp.get("ml"),
        llms=llms, scanner=scanner, input_guard=input_guard,
    )  # fmt: skip


def _default_data_dir() -> Path:
    from evals.classification.dataset.build import DEFAULT_DATA_DIR

    return Path(DEFAULT_DATA_DIR)


__all__ = ["HybridClassifier", "build_hybrid_classifier"]
