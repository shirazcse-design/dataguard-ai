"""F08-F09: budget and cost caps, and Rules-engine faults, through the real components."""

from __future__ import annotations

import pytest

from app.classification.schemas import Document
from app.llm.config import Price
from tests.failure_injection.conftest import chat_body, hybrid, llm_over_http, request

LOW = (200, chat_body("CONFIDENTIAL", ["SOURCE_CODE"], bucket="low"), {}, 0.0)  # forces escalation
GOOD = (200, chat_body("CONFIDENTIAL", ["SOURCE_CODE"]), {}, 0.0)


def two_tier(parts, a, b, *, cfg=None, **kw):
    return hybrid(
        parts,
        rules=parts.rules,
        llms={
            "mid": llm_over_http(parts, a.url, "mid", cfg=cfg),
            "large": llm_over_http(parts, b.url, "large", cfg=cfg),
        },
        **kw,
    )


# ---- F08: budget / cost cap -------------------------------------------------------------------
def test_row_F08_the_call_budget_stops_escalation_and_routes_to_review(
    two_providers, parts, plain_doc
):
    a, b = two_providers
    a.default, b.default = LOW, GOOD
    h = hybrid(
        parts, "tight", rules=parts.rules,
        llms={"mid": llm_over_http(parts, a.url, "mid"), "large": llm_over_http(parts, b.url, "large")},
        tight={"budget": {"max_llm_calls": 1}},
    )  # fmt: skip
    r = h.classify(request(plain_doc.content))
    assert len(a.hits) == 1 and b.hits == []  # the second tier was never called
    assert r.status == "review_required" and "BUDGET_EXHAUSTED" in r.review.reason_codes
    assert (
        r.level is not None and r.review.provisional
    )  # the first tier's answer is the provisional label


def test_row_F08_a_request_that_forbids_llms_makes_no_model_call(
    two_providers, parts, hr_doc, plain_doc
):
    a, b = two_providers
    a.default = b.default = GOOD
    h = two_tier(parts, a, b)
    decided = h.classify(request(hr_doc.content, max_llm_tier="none"))
    assert (
        decided.status == "ok"
        and decided.level.value == "HIGHLY_CONFIDENTIAL"
        and decided.routing.stages_run == ["rules"]
    )
    nothing = h.classify(request(plain_doc.content, max_llm_tier="none"))
    assert (
        nothing.status == "review_required" and nothing.level is None
    )  # Rules abstain: no label, never a default
    assert a.hits == [] and b.hits == []


def test_row_F08_the_tier_cap_filters_tiers_above_it_even_when_escalation_wants_them(
    two_providers, parts, plain_doc
):
    a, b = two_providers
    a.default, b.default = LOW, GOOD  # mid is unsure, so escalation would call large
    capped = two_tier(parts, a, b).classify(request(plain_doc.content, max_llm_tier="mid"))
    assert len(a.hits) == 1 and b.hits == []  # large is above the cap and was never called
    assert capped.status == "review_required" and "LOW_CONFIDENCE" in capped.review.reason_codes
    uncapped = two_tier(parts, a, b).classify(request(plain_doc.content))
    assert len(b.hits) == 1 and uncapped.status in (
        "ok",
        "degraded",
    )  # without the cap it does escalate


def test_row_F08_a_latency_budget_blocks_further_escalation(two_providers, parts, plain_doc):
    a, b = two_providers
    a.default, b.default = (
        (200, chat_body("CONFIDENTIAL", ["SOURCE_CODE"], bucket="low"), {}, 0.3),
        GOOD,
    )
    r = two_tier(parts, a, b).classify(request(plain_doc.content, budget={"max_latency_ms": 100}))
    assert (
        len(a.hits) == 1
        and b.hits == []
        and r.status == "review_required"
        and "BUDGET_EXHAUSTED" in r.review.reason_codes
    )


def test_row_F08_a_zero_cost_cap_means_no_spend_and_no_model_call(two_providers, parts, plain_doc):
    a, b = two_providers
    a.default = b.default = GOOD
    r = two_tier(parts, a, b).classify(request(plain_doc.content, budget={"max_cost_usd": 0}))
    assert (
        a.hits == []
        and b.hits == []
        and r.status == "review_required"
        and "BUDGET_EXHAUSTED" in r.review.reason_codes
    )
    assert r.level is None  # nothing could decide, so no label


def test_row_F08_a_priced_cost_cap_is_enforced_from_the_estimated_spend(
    two_providers, parts, plain_doc
):
    a, b = two_providers
    a.default, b.default = LOW, GOOD  # each call: 100 in + 20 out tokens
    price = Price(
        input_per_1k_usd=1.0, output_per_1k_usd=2.0, retrieved_on="2026-01-01"
    )  # $0.14 per call
    cfg = parts.cfg.model_copy(
        update={
            "tiers": {k: v.model_copy(update={"price": price}) for k, v in parts.cfg.tiers.items()}
        }
    )
    r = two_tier(parts, a, b, cfg=cfg).classify(
        request(plain_doc.content, budget={"max_cost_usd": 0.1})
    )
    assert (
        len(a.hits) == 1
        and b.hits == []
        and r.status == "review_required"
        and "BUDGET_EXHAUSTED" in r.review.reason_codes
    )
    assert r.telemetry.est_cost_usd == pytest.approx(0.14)


def test_row_F08_a_cost_cap_without_any_price_cannot_be_enforced_and_says_so(
    two_providers, parts, plain_doc
):
    a, b = two_providers
    a.default = b.default = GOOD
    r = two_tier(parts, a, b).classify(request(plain_doc.content, budget={"max_cost_usd": 5.0}))
    assert (
        "cost_cap_unenforceable:no_price" in r.warnings
        and len(a.hits) == 1
        and r.status in ("ok", "degraded")
    )


@pytest.mark.parametrize(
    "mode, expect_llm, expect_rules",
    [("rules", False, True), ("llm", True, False), ("hybrid", True, True)],
)
def test_row_F08_the_request_mode_restricts_which_stages_run(
    two_providers, parts, plain_doc, mode, expect_llm, expect_rules
):
    a, b = two_providers
    a.default = b.default = GOOD
    r = two_tier(parts, a, b).classify(request(plain_doc.content, mode=mode))
    assert bool(a.hits) is expect_llm and ("rules" in r.routing.stages_run) is expect_rules


# ---- F09: Rules engine error ------------------------------------------------------------------
def firing_detector(parts, doc):
    res = parts.rules.engine.analyze(
        Document(content=doc.content, filename="a.txt", extension="txt")
    )
    det_id = res.detections[0].detector_id
    return next(d for d in parts.rules.engine.detectors if d.id == det_id), res


def test_row_F09_one_broken_detector_is_isolated_and_marks_the_result_degraded(
    parts, hr_doc, monkeypatch
):
    det, baseline = firing_detector(parts, hr_doc)
    monkeypatch.setattr(
        det,
        "detect",
        lambda scan: (_ for _ in ()).throw(RuntimeError("leak: " + hr_doc.content[:30])),
    )
    r = parts.rules.classify(request(hr_doc.content))
    assert r.status == "degraded" and f"detector_error:{det.id}:RuntimeError" in r.warnings
    assert hr_doc.content[:30] not in r.model_dump_json()  # the exception message never surfaces
    others = {(d.detector_id, d.start) for d in baseline.detections if d.detector_id != det.id}
    now = {(e.detector.id, e.locator.char_start) for e in r.evidence if e.detector}
    assert others <= now  # every other detector still ran


def test_row_F09_a_broken_detector_that_did_not_fire_still_marks_the_result_degraded(
    parts, hr_doc, monkeypatch
):
    fired = {
        d.detector_id
        for d in parts.rules.engine.analyze(
            Document(content=hr_doc.content, filename="a", extension="t")
        ).detections
    }
    idle = next(d for d in parts.rules.engine.detectors if d.id not in fired)
    monkeypatch.setattr(idle, "detect", lambda scan: (_ for _ in ()).throw(ValueError("x")))
    r = parts.rules.classify(request(hr_doc.content))
    assert (
        r.status == "degraded" and r.level.value == "HIGHLY_CONFIDENTIAL"
    )  # same finding, flagged


def test_row_F09_every_detector_failing_abstains_and_is_degraded_never_a_public_finding(
    parts, hr_doc, monkeypatch
):
    for d in parts.rules.engine.detectors:
        monkeypatch.setattr(d, "detect", lambda scan: (_ for _ in ()).throw(RuntimeError("down")))
    r = parts.rules.classify(request(hr_doc.content))
    assert r.status == "degraded" and r.routing.abstained and r.level.confidence.kind == "none"
    assert len([w for w in r.warnings if w.startswith("detector_error:")]) == len(
        parts.rules.engine.detectors
    )


def test_row_F09_a_degraded_rules_result_never_short_circuits_and_the_llm_decides(
    provider, parts, hr_doc, monkeypatch
):
    det, _ = firing_detector(parts, hr_doc)
    monkeypatch.setattr(det, "detect", lambda scan: (_ for _ in ()).throw(RuntimeError("x")))
    provider.default = (200, chat_body("HIGHLY_CONFIDENTIAL", ["PII"]), {}, 0.0)
    h = hybrid(
        parts,
        "sc",
        rules=parts.rules,
        llms={
            "mid": llm_over_http(parts, provider.url),
            "large": llm_over_http(parts, provider.url, "large"),
        },
        sc={"rules": {"short_circuit": True}},
    )
    r = h.classify(request(hr_doc.content))
    assert len(provider.hits) == 1 and not r.routing.short_circuited  # the LLM was consulted
    assert (
        r.status == "degraded"
        and "stage:rules:rules_degraded" in r.warnings
        and "degraded:rules" in r.warnings
    )


def test_row_F09_a_rules_stage_that_raises_outright_is_skipped(provider, parts, plain_doc):
    class Boom:
        name, version = "rules", "0"

        def classify(self, req):
            raise MemoryError("oom")

        def params(self):
            return {}

    provider.default = GOOD
    h = hybrid(
        parts,
        rules=Boom(),
        llms={
            "mid": llm_over_http(parts, provider.url),
            "large": llm_over_http(parts, provider.url, "large"),
        },
    )
    r = h.classify(request(plain_doc.content))
    assert (
        r.status == "degraded"
        and "stage_error:rules:MemoryError" in r.warnings
        and r.level is not None
    )
