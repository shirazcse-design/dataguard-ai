"""The Policy Copilot pipeline: one function, configured per RAG level.

    input guard -> retrieval -> evidence injection scan -> evidence gate -> generation
                -> citation verification -> conflict resolution -> PolicyAnswer

Agentic RAG (`app/policy/agent.py`) replaces retrieval..generation with a bounded tool loop and
then goes through the SAME input guard and the SAME verification/conflict/outcome code
(`decide`), so the agent cannot produce an answer the non-agentic path would not accept.

`PolicyAnswer.stages` lists exactly the stages that ran, in order, with their outcome; a stage
that did not run is not listed (a short-circuit ends the list). Every decision about status,
citations and conflicts is made here from verifiable facts; the model contributes claims and
quotes, never the final status on its own.

Tracing: one `uc6.request` root span per question when a tracer is given, and one child span per
stage. Span attributes are ids, counts, scores, statuses and fixed-vocabulary reasons. Never the
question, policy text, quotes or claim text.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from guardrails.injection import InjectionScanner
from observability import span

from .config import LevelConfig, PolicyConfig
from .corpus import Corpus
from .generate import Generator, ModelOutput
from .guard import check_question, scan_chunk
from .retrieval import Hit, Retriever
from .schemas import Evidence, GuardrailEvent, LLMCall, PolicyAnswer, Review, Stage
from .verify import model_reported_conflict, verify_claims, version_conflicts


@dataclass
class Run:
    """Per-request state shared by the pipeline stages and the agent."""

    request_id: str
    level: str
    embedding_model_id: str | None
    started: float = field(default_factory=time.perf_counter)
    stages: list[Stage] = field(default_factory=list)
    review: Review = field(default_factory=Review)
    events: list[GuardrailEvent] = field(default_factory=list)

    def finish(self, status: str, mode: str, **kw: Any) -> PolicyAnswer:
        return PolicyAnswer(
            request_id=self.request_id,
            level=self.level,
            mode=mode,
            status=status,
            claims=kw.pop("claims", []),
            citations=kw.pop("citations", []),
            evidence=kw.pop("evidence", []),
            evidence_status=kw.pop("evidence_status", "none"),
            review=self.review,
            guardrail_events=self.events,
            stages=self.stages,
            embedding_model_id=self.embedding_model_id,
            latency_ms=round((time.perf_counter() - self.started) * 1000, 1),
            **kw,
        )


class PolicyCopilot:
    def __init__(
        self,
        corpus: Corpus,
        cfg: PolicyConfig,
        retriever: Retriever,
        generator: Generator,
        scanner: InjectionScanner,
        *,
        tracer: Any = None,
        salt: bytes = b"",
        id_factory: Callable[[], str] = lambda: secrets.token_hex(6),
    ) -> None:
        self.corpus = corpus
        self.cfg = cfg
        self.retriever = retriever
        self.generator = generator
        self.scanner = scanner
        self.tracer = tracer
        self.salt = salt
        self.new_id = id_factory
        self.agent: Any = None  # an AgentRunner (app/policy/agent.py), set by build_copilot

    # -- helpers -----------------------------------------------------------------------------
    def evidence_item(self, chunk_id: str, label: str, rank: int, score: float) -> Evidence:
        c = self.corpus.by_id[chunk_id]
        return Evidence(
            evidence_id=label,
            chunk_id=c.chunk_id,
            citation=c.citation,
            policy_id=c.policy_id,
            title=c.title,
            version=c.version,
            effective_date=c.effective_date,
            status=c.status,
            section=c.section,
            heading=c.heading,
            policy_owner=c.policy_owner,
            body=c.body,
            rank=rank,
            score=round(score, 4),
        )

    def evidence_from(self, hits: list[Hit]) -> list[Evidence]:
        return [
            self.evidence_item(h.chunk_id, f"E{n}", h.rank, h.evidence_score)
            for n, h in enumerate(hits, start=1)
        ]

    def _mode(self, llm_cached: bool | None) -> str:
        if self.generator.mode == "offline":
            return "offline"
        if llm_cached is True:
            return "replay"
        return self.generator.mode if llm_cached is None else "live"

    # -- the pipeline -------------------------------------------------------------------------
    def answer(self, question: str, level_name: str = "advanced") -> PolicyAnswer:
        level = self.cfg.levels[level_name]
        request_id = self.new_id()
        root = (
            self.tracer.trace(
                "uc6.request", dg__request_id=request_id, dg__policy__level=level_name
            )
            if self.tracer is not None
            else nullcontext()
        )
        with root as handle:
            result = self._answer(question, level_name, level, request_id)
            if handle is not None:
                handle.set(**_outcome_attrs(result))
            return result

    def _answer(
        self, question: str, level_name: str, level: LevelConfig, request_id: str
    ) -> PolicyAnswer:
        run = Run(request_id, level_name, self.retriever.embedding_model_id)

        blocked = self.input_guard(question, level, run)
        if blocked is not None:
            return blocked
        if level_name == "agentic":
            if self.agent is None:
                raise ValueError("the agentic level needs an agent runner (build_copilot)")
            return self.agent.run(question, level, run)

        # 2. retrieval ----------------------------------------------------------------------------
        t = time.perf_counter()
        with span("uc6.retrieve") as s:
            retrieval = self.retriever.retrieve(question, level)
            s.set(
                dg__policy__n_hits=len(retrieval.hits),
                dg__policy__dense_status=retrieval.trace.dense_status,
                dg__policy__stages=list(retrieval.trace.stages),
            )
        evidence = self.evidence_from(retrieval.hits)
        run.stages.append(
            Stage(
                name="retrieval",
                status="ok" if evidence else "failed",
                ms=_ms(t),
                detail={
                    "stages": retrieval.trace.stages,
                    "hits": len(evidence),
                    "dense_status": retrieval.trace.dense_status,
                },
            )
        )
        top = evidence[0].score if evidence else None
        mode = self._mode(None)

        # 3. evidence injection scan ------------------------------------------------------------
        used = self.scan_evidence(evidence, run) if level.chunk_injection_scan else evidence

        # 4. evidence gate ------------------------------------------------------------------------
        if not used and not level.evidence_gate:
            run.review.add("insufficient_evidence")
            return run.finish(
                "INSUFFICIENT_EVIDENCE", mode, evidence=evidence, retrieval=retrieval.trace,
                top_evidence_score=top,
            )  # fmt: skip
        if level.evidence_gate:
            best = max((e.score for e in used), default=0.0)
            passed = bool(used) and best >= self.cfg.gate.min_evidence_score
            run.stages.append(
                Stage(
                    name="evidence_gate",
                    status="ok" if passed else "short_circuit",
                    detail={
                        "best_score": round(best, 4),
                        "threshold": self.cfg.gate.min_evidence_score,
                    },
                )
            )
            if not passed:
                run.review.add("insufficient_evidence")
                return run.finish(
                    "INSUFFICIENT_EVIDENCE", mode, evidence=evidence, retrieval=retrieval.trace,
                    top_evidence_score=top,
                )  # fmt: skip

        # 5. generation ---------------------------------------------------------------------------
        t = time.perf_counter()
        with span("uc6.generate") as s:
            gen = self.generator.generate(question, used)
            s.set(**_llm_attrs(gen.call), dg__llm__status="ok" if gen.error is None else "error")
            if gen.error:
                s.set(dg__llm__error_kind=gen.error)
        run.stages.append(
            Stage(
                name="generation",
                status="ok" if gen.output else "failed",
                ms=_ms(t),
                detail={
                    "generator": self.generator.model_id,
                    "model_status": gen.output.status if gen.output else None,
                    "error": gen.error,
                    "evidence_sent": len(used),
                },
            )
        )
        mode = self._mode(gen.call.cached if gen.call else None)
        if gen.output is None:
            run.review.add("generation_unavailable")
            return run.finish(
                "UNAVAILABLE", mode, evidence=evidence, retrieval=retrieval.trace,
                top_evidence_score=top, llm=gen.call,
            )  # fmt: skip
        return self.decide(
            gen.output, used, evidence, level, run, mode=mode, top=top,
            retrieval=retrieval.trace, llm=gen.call,
        )  # fmt: skip

    # -- stages shared with the agent ----------------------------------------------------------
    def input_guard(self, question: str, level: LevelConfig, run: Run) -> PolicyAnswer | None:
        """Step 1. Returns a BLOCKED answer, or None when the question may proceed."""
        t = time.perf_counter()
        with span("uc6.input_guard") as s:
            verdict = check_question(question, self.cfg.guard, self.scanner, scan=level.input_guard)
            s.set(dg__policy__guard_ok=verdict.ok, dg__input__reason=verdict.reason or "ok")
        detail = {"scanned": level.input_guard, "chars": len(question)}
        if verdict.ok:
            run.stages.append(Stage(name="input_guard", status="ok", ms=_ms(t), detail=detail))
            return None
        run.stages.append(
            Stage(
                name="input_guard",
                status="blocked",
                ms=_ms(t),
                detail={**detail, "reason": verdict.reason, "rules": list(verdict.rules)},
            )
        )
        if verdict.reason == "prompt_injection":
            run.events.append(
                GuardrailEvent(
                    type="prompt_injection_suspected",
                    trigger=",".join(verdict.rules),
                    action="blocked",
                )
            )
            run.review.add("guardrail_injection")
        else:
            run.events.append(
                GuardrailEvent(
                    type="input_rejected", trigger=verdict.reason or "", action="blocked"
                )
            )
            run.review.add("input_rejected")
        return run.finish("BLOCKED", self._mode(None))

    def scan_evidence(
        self, evidence: list[Evidence], run: Run, *, record: bool = True
    ) -> list[Evidence]:
        """Step 3. Flags evidence carrying instruction-override text and returns the rest."""
        t = time.perf_counter()
        flagged = 0
        for e in evidence:
            rules = scan_chunk(e.body, self.scanner)
            if rules:
                e.flagged_injection = True
                flagged += 1
                run.events.append(
                    GuardrailEvent(
                        type="prompt_injection_in_evidence",
                        trigger=",".join(rules),
                        action="excluded_from_prompt",
                        chunk_id=e.chunk_id,
                    )
                )
        if flagged:
            run.review.add("evidence_injection")
        if record:
            run.stages.append(
                Stage(
                    name="evidence_scan",
                    status="ok",
                    ms=_ms(t),
                    detail={"scanned": len(evidence), "excluded": flagged},
                )
            )
        return [e for e in evidence if not e.flagged_injection]

    def decide(
        self,
        out: ModelOutput,
        used: list[Evidence],
        evidence: list[Evidence],
        level: LevelConfig,
        run: Run,
        *,
        mode: str,
        top: float | None,
        retrieval: Any = None,
        llm: LLMCall | None = None,
        agent: dict[str, Any] | None = None,
    ) -> PolicyAnswer:
        """Steps 6-8: citation verification, conflict resolution and the final status."""
        review = run.review
        # 6. citation verification ----------------------------------------------------------------
        t = time.perf_counter()
        with span("uc6.citation_verify") as s:
            claims = verify_claims(
                out,
                used,
                max_claims=self.cfg.generation.max_claims,
                max_quote_chars=self.cfg.generation.max_quote_chars,
            )
            n_fab = sum(c.drop_reason == "fabricated_evidence_id" for c in claims)
            n_bad = sum(not c.verified for c in claims)
            s.set(
                dg__policy__claims=len(claims),
                dg__policy__claims_unverified=n_bad,
                dg__policy__fabricated_citations=n_fab,
            )
        if level.enforce_citations:
            kept = [c for c in claims if c.verified]
            dropped = [c for c in claims if not c.verified]
        else:
            kept, dropped = claims, []
        run.stages.append(
            Stage(
                name="citation_verification",
                status="ok",
                ms=_ms(t),
                detail={
                    "claims": len(claims),
                    "verified": len(claims) - n_bad,
                    "fabricated": n_fab,
                    "enforced": level.enforce_citations,
                },
            )
        )

        # 7. conflicts ----------------------------------------------------------------------------
        t = time.perf_counter()
        conflicts = version_conflicts(used, self.corpus)
        for c in conflicts:
            if c.resolution == "resolved_by_metadata" and level.enforce_citations:
                losers = set(c.chunk_ids) - {
                    e.chunk_id for e in used if e.citation == c.authoritative
                }
                for claim in [k for k in kept if k.chunk_id in losers]:
                    kept.remove(claim)
                    dropped.append(claim.model_copy(update={"drop_reason": "superseded_version"}))
            elif c.resolution == "human_review":
                review.add("policy_conflict")
        cross = model_reported_conflict(out, used) if out.status == "CONFLICT" else None
        if cross is not None:
            conflicts.append(cross)
            review.add("policy_conflict")
        elif out.status == "CONFLICT" and not any(c.kind == "version" for c in conflicts):
            review.add("conflict_unverified")
        run.stages.append(
            Stage(
                name="conflict_check",
                status="ok",
                ms=_ms(t),
                detail={
                    "conflicts": len(conflicts),
                    "kinds": sorted({c.kind for c in conflicts}),
                    "needs_review": any(c.resolution == "human_review" for c in conflicts),
                },
            )
        )

        # 8. outcome ------------------------------------------------------------------------------
        if out.status == "INSUFFICIENT_EVIDENCE":
            kept, status = [], "INSUFFICIENT_EVIDENCE"
            review.add("insufficient_evidence")
        elif any(c.resolution == "human_review" for c in conflicts):
            status = "CONFLICT_REVIEW"
        elif not kept:
            status = "INSUFFICIENT_EVIDENCE"
            review.add("no_verified_claims")
        else:
            status = "ANSWERED"
        if level.enforce_citations and any(
            c.drop_reason not in ("superseded_version", "over_claim_limit") for c in dropped
        ):
            review.add("unverified_claims_removed")

        cited = {c.chunk_id for c in kept if c.verified}
        for e in evidence:
            e.cited = e.chunk_id in cited
        n_ok = sum(c.verified for c in kept)
        if not kept:
            evidence_status = "none"
        elif n_ok == len(kept):
            evidence_status = "grounded"
        else:
            evidence_status = "partially_grounded" if n_ok else "ungrounded"
        citations = list(dict.fromkeys(c.citation for c in kept if c.verified and c.citation))
        run.stages.append(Stage(name="result", status="ok", detail={"status": status}))
        return run.finish(
            status,
            mode,
            claims=kept,
            dropped_claims=dropped,
            citations=citations,
            evidence=evidence,
            conflicts=conflicts,
            conflict_note=out.conflict_note.strip()[:500] if cross is not None else None,
            evidence_status=evidence_status,
            top_evidence_score=top,
            retrieval=retrieval,
            llm=llm,
            agent=agent,
        )


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def _llm_attrs(call: Any) -> dict[str, Any]:
    if call is None:
        return {}
    attrs = {
        "dg__llm__model_id": call.model_id,
        "dg__llm__cached": call.cached,
        "dg__llm__attempts": call.attempts,
        "dg__latency_ms": call.latency_ms,
    }
    for key, value in (
        ("dg__llm__served_model", call.served_model),
        ("dg__tokens_in", call.tokens_in),
        ("dg__tokens_out", call.tokens_out),
    ):
        if value is not None:
            attrs[key] = value
    return attrs


def _outcome_attrs(a: PolicyAnswer) -> dict[str, Any]:
    """Root-span outcome. Lists of plain tokens (the redactor masks free text and long joined
    strings), citations as `POL-DLP:4.2` policy/section ids: identifiers, never policy text."""
    return {
        "dg__policy__status": a.status,
        "dg__policy__mode": a.mode,
        "dg__policy__n_claims": len(a.claims),
        "dg__policy__n_dropped": len(a.dropped_claims),
        "dg__policy__drop_reasons": sorted(
            {c.drop_reason for c in a.dropped_claims if c.drop_reason}
        ),
        "dg__policy__cited": [c.replace(" §", ":").replace(" v", "@") for c in a.citations],
        "dg__policy__evidence_status": a.evidence_status,
        "dg__policy__n_conflicts": len(a.conflicts),
        "dg__policy__n_evidence": len(a.evidence),
        "dg__outcome__review_required": a.review.required,
        "dg__outcome__review_reasons": list(a.review.reasons),
        "dg__guardrail__type": sorted({e.type for e in a.guardrail_events}),
        "dg__latency_ms": a.latency_ms,
    }
