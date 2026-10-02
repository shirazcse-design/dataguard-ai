"""The UC1 investigation: mandatory deterministic stages, then the bounded agent, then the harness.

    event -> prechecks -> UC4 classification -> identity -> behaviour -> UC6 policy
          -> investigation agent (advisory) -> risk/response harness -> Investigation

`Investigation.stages` lists exactly what ran and how it ended. A failed mandatory stage never
stops the investigation silently: it is recorded and the harness routes the case to HUMAN_REVIEW.

Tracing (shared tracer, deny-by-default redactor): `uc1.investigation` per case with one child per
stage (`uc1.prechecks`, `uc1.classification`, `uc1.identity`, `uc1.behavior`,
`uc1.policy_retrieval` (UC6's own `uc6.*` spans nest under it), `uc1.agent` > `uc1.agent.planner`
/ `uc1.tool`, `uc1.risk_decision`, `uc1.hitl_decision`). Attributes are ids, codes, counts, bands
and outcomes only - never document text, the user's justification or policy text.
"""

from __future__ import annotations

import time
from contextlib import nullcontext
from typing import Any

from observability import span

from .agent import DlpAgent, DlpTools
from .context import ContextError
from .harness import decide
from .integration import (
    DocumentError,
    effective_level,
    policy_question,
)
from .prechecks import run_prechecks
from .schemas import DLPEvent, Investigation, PolicyContext, Stage


def _ms(t: float) -> float:
    return round((time.perf_counter() - t) * 1000, 2)


class DLPInvestigator:
    def __init__(
        self,
        *,
        dlp,
        mapping,
        rubric,
        hashes,
        documents,
        classifier,
        rules_engine,
        identities,
        activity,
        exceptions,
        behavior,
        policy,
        copilot,
        agent: DlpAgent | None,
        scanner,
        mode: str,
        tracer: Any = None,
    ) -> None:
        self.dlp, self.mapping, self.rubric, self.hashes = dlp, mapping, rubric, hashes
        self.documents, self.classifier, self.rules_engine = documents, classifier, rules_engine
        self.identities, self.activity, self.exceptions = identities, activity, exceptions
        self.behavior, self.policy, self.copilot, self.agent = behavior, policy, copilot, agent
        self.scanner, self.mode, self.tracer = scanner, mode, tracer

    def investigate(self, event: DLPEvent) -> Investigation:
        root = (
            self.tracer.trace("uc1.investigation", dg__dlp__case_id=event.case_id)
            if self.tracer is not None
            else nullcontext()
        )
        with root as handle:
            inv = self._investigate(event)
            if handle is not None:
                d = inv.decision
                handle.set(
                    dg__dlp__outcome=d.outcome,
                    dg__dlp__band=d.band,
                    dg__dlp__score=d.score,
                    dg__dlp__risk_level=d.risk_level,
                    dg__outcome__review_required=d.human_review_required,
                    dg__dlp__reason_codes=[
                        r.split(":")[0] + ":" + r.split(":")[1] for r in d.reason_codes
                    ][:20],
                    dg__dlp__simulated_faults=list(event.simulate_fault),
                    dg__policy__mode=inv.mode,
                    dg__latency_ms=inv.latency_ms,
                )
            return inv

    def _investigate(self, event: DLPEvent) -> Investigation:
        t_all = time.perf_counter()
        stages: list[Stage] = []
        failures: list[str] = []
        events: list[dict[str, str]] = []
        faults = list(event.simulate_fault)

        # 1. deterministic pre-checks
        t = time.perf_counter()
        document = None
        with span("uc1.prechecks") as s:
            try:
                document = self.documents.get(event.document_ref)
            except DocumentError:
                failures.append("document")
            known = self.identities.known_destinations(event.user_id)
            if document is not None:
                pre = run_prechecks(event, document, self.dlp, self.rules_engine, known)
            else:
                from .prechecks import classify_destination

                dest = classify_destination(
                    event.destination.host, event.destination.account_type, self.dlp
                )
                from .schemas import Prechecks

                pre = Prechecks(
                    destination_class=dest,
                    destination_known_to_user=None,
                    existing_label=event.existing_label,
                    pattern_categories={},
                    pattern_level=None,
                    findings=[f"destination:{dest}", "document:unavailable"],
                )
            s.set(
                dg__dlp__destination_class=pre.destination_class,
                dg__dlp__n_findings=len(pre.findings),
            )
        stages.append(
            Stage(
                name="prechecks",
                status="ok" if document else "failed",
                ms=_ms(t),
                detail={"findings": pre.findings},
            )
        )
        dest = pre.destination_class

        # 2. UC4 classification
        t = time.perf_counter()
        classification = None
        with span("uc1.classification") as s:
            if document is not None:
                classification = self.classifier.classify(
                    document,
                    f"uc1-{event.case_id}",
                    semantic_unavailable="semantic_tier_unavailable" in faults,
                )
                s.set(
                    dg__outcome__status=classification.status,
                    dg__outcome__level=classification.level or "none",
                    dg__outcome__n_categories=len(classification.categories),
                )
        if classification is not None and classification.injection_flagged:
            events.append(
                {
                    "type": "prompt_injection_suspected",
                    "source": "document",
                    "action": "treated_as_data",
                }
            )
        stages.append(
            Stage(
                name="classification",
                status="ok" if classification and classification.ok else "failed",
                ms=_ms(t),
                detail={
                    "status": classification.status if classification else "not_run",
                    "level": classification.level if classification else None,
                    "stages_run": classification.stages_run if classification else [],
                },
            )
        )
        level = (
            effective_level(classification, pre.existing_label, self.mapping)
            if classification
            else None
        )
        if classification is not None and (not classification.ok or classification.review_required):
            level = None  # an uncertain classification is not upgraded by labels: a human decides

        # 3. identity
        t = time.perf_counter()
        identity = None
        with span("uc1.identity") as s:
            try:
                if "identity_unavailable" in faults:
                    raise ContextError("identity_unavailable")
                identity = self.identities.get_user_profile(event.user_id)
            except ContextError as exc:
                failures.append("identity")
                s.set(dg__error__type=exc.kind)
        stages.append(
            Stage(
                name="identity",
                status="ok" if identity else "failed",
                ms=_ms(t),
                detail={
                    "employment_type": identity.employment_type if identity else None,
                    "privilege_level": identity.privilege_level if identity else None,
                },
            )
        )

        # 4. behaviour
        t = time.perf_counter()
        behavior = None
        with span("uc1.behavior") as s:
            try:
                behavior = self.behavior.assess(event.user_id, event, identity)
                s.set(
                    dg__dlp__behavior_band=behavior.band, dg__dlp__n_signals=len(behavior.signals)
                )
            except ContextError as exc:
                failures.append("behavior")
                s.set(dg__error__type=exc.kind)
        stages.append(
            Stage(
                name="behavior",
                status="ok" if behavior else "failed",
                ms=_ms(t),
                detail={
                    "band": behavior.band if behavior else None,
                    "signals": behavior.signals if behavior else [],
                    "provider": behavior.provider if behavior else None,
                },
            )
        )

        # 5. UC6 policy intelligence
        t = time.perf_counter()
        policy: PolicyContext | None = None
        with span("uc1.policy_retrieval") as s:
            if level is None:
                stages.append(
                    Stage(name="policy", status="skipped", detail={"reason": "no_confident_level"})
                )
            else:
                cats = classification.categories if classification else []
                question = policy_question(level, cats, dest, self.dlp, self.mapping)
                if "policy_unavailable" in faults:
                    policy = PolicyContext(question=question, status="UNAVAILABLE")
                    failures.append("policy")
                else:
                    policy = self.policy.assess(question, dest, level, self.mapping)
                    if policy.status == "UNAVAILABLE":
                        failures.append("policy")
                s.set(
                    dg__policy__status=policy.status,
                    dg__dlp__policy_effect=policy.effect,
                    dg__policy__cited=[c.replace(" §", ":") for c in policy.cited_sections][:10],
                )
                stages.append(
                    Stage(
                        name="policy",
                        status="failed" if "policy" in failures else "ok",
                        ms=_ms(t),
                        detail={
                            "status": policy.status,
                            "effect": policy.effect,
                            "citations": policy.citations,
                            "conflict": policy.conflict,
                        },
                    )
                )

        # 6. justification guard + investigation agent
        justification = None
        injection = bool(classification and classification.injection_flagged)
        if event.user_justification:
            text = event.user_justification[: self.dlp.agent.max_justification_chars]
            rules = sorted({f.rule_id for f in self.scanner.scan(text)})
            if rules:
                injection = True
                justification = "[withheld: instruction-like text detected]"
                events.append(
                    {
                        "type": "prompt_injection_suspected",
                        "source": "user_justification",
                        "action": "withheld_from_agent",
                        "trigger": ",".join(rules),
                    }
                )
            else:
                justification = text
        agent_result = None
        if self.agent is not None:
            t = time.perf_counter()
            pack = self._pack(
                event, pre, classification, level, identity, behavior, policy, justification
            )
            tools = DlpTools(
                policy_tools=self._policy_tools(event),
                activity=self.activity,
                exceptions=self.exceptions,
                event=event,
                level_rank=self.mapping.rank(level),
                ranks={k: v.rank for k, v in self.mapping.levels.items()},
                cfg=self.dlp.agent,
                faults=faults,
            )
            agent_result = self.agent.run(pack, tools)
            stages.append(
                Stage(
                    name="agent",
                    status="ok" if agent_result.stopped_reason == "final_answer" else "failed",
                    ms=_ms(t),
                    detail={
                        "stopped_reason": agent_result.stopped_reason,
                        "tool_calls": agent_result.trace.get("tool_calls"),
                        "proposed_outcome": agent_result.proposed_outcome,
                    },
                )
            )

        # 7. deterministic decision + HITL
        t = time.perf_counter()
        with span("uc1.risk_decision") as s:
            decision = decide(
                rubric=self.rubric,
                mapping=self.mapping,
                destination=dest,
                level=level,
                classification=classification,
                identity=identity,
                behavior=behavior,
                policy=policy,
                agent=agent_result,
                stage_failures=failures,
                injection=injection,
            )
            s.set(
                dg__dlp__score=decision.score,
                dg__dlp__band=decision.band,
                dg__dlp__outcome=decision.outcome,
            )
        with span("uc1.hitl_decision") as s:
            s.set(dg__outcome__review_required=decision.human_review_required)
        stages.append(
            Stage(
                name="decision",
                status="ok",
                ms=_ms(t),
                detail={
                    "score": decision.score,
                    "band": decision.band,
                    "outcome": decision.outcome,
                    "human_review_required": decision.human_review_required,
                },
            )
        )
        return Investigation(
            case_id=event.case_id,
            mode=self.mode,
            event=event,
            simulated_faults=faults,
            stages=stages,
            prechecks=pre,
            classification=classification,
            identity=identity,
            behavior=behavior,
            policy=policy,
            agent=agent_result,
            decision=decision,
            guardrail_events=events,
            latency_ms=round((time.perf_counter() - t_all) * 1000, 1),
        )

    # -- helpers
    def _policy_tools(self, event: DLPEvent):
        from app.policy.agent import PolicyTools
        from app.policy.pipeline import Run

        run = Run(f"uc1-{event.case_id}", "agentic", self.copilot.retriever.embedding_model_id)
        return PolicyTools(
            self.copilot, self.copilot.cfg.levels["agentic"], run, self.copilot.cfg.agent
        )

    def _pack(
        self, event, pre, classification, level, identity, behavior, policy, justification
    ) -> dict:
        """The agent's evidence pack: facts and ids only. Never the document text."""
        term = self.mapping.levels[level].policy_term if level else None
        return {
            "event": {"action": event.action, "timestamp": event.timestamp},
            "destination": {
                "class": pre.destination_class,
                "host": event.destination.host,
                "account_type": event.destination.account_type,
                "known_to_user": pre.destination_known_to_user,
            },
            "prechecks": pre.findings,
            "classification": {
                "level": level,
                "policy_term": term,
                "categories": classification.categories if classification else [],
                "confidence": classification.confidence if classification else None,
                "review_required": classification.review_required if classification else True,
                "injection_flagged_in_document": classification.injection_flagged
                if classification
                else False,
            },
            "user": (identity.model_dump(exclude={"manager"}) if identity else None),
            "behavior": {
                "band": behavior.band if behavior else "UNKNOWN",
                "signals": behavior.signals if behavior else [],
            },
            "policy": {
                "question": policy.question if policy else None,
                "status": policy.status if policy else "NOT_RUN",
                "effect": policy.effect if policy else "unknown",
                "conflict": policy.conflict if policy else False,
                "claims": [
                    {"evidence_id": f"P{i}", **c}
                    for i, c in enumerate(policy.claims if policy else [], start=1)
                ],
            },
            "user_justification": justification,
        }
