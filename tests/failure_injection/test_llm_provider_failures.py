"""F01-F03: provider failures, malformed output and unverifiable evidence, end to end.

The real Foundry adapter and the real LLM classifier talk to a LOCAL fake server; nothing here says
anything about a real model or service.
"""

from __future__ import annotations

import json

import pytest

from tests.failure_injection.conftest import (
    chat_body,
    hybrid,
    llm_over_http,
    quote_of,
    raw_body,
    request,
)

OK = (200, {}, {}, 0.0)
R429 = (429, {"error": "slow down"}, {"Retry-After": "2"}, 0.0)


def status(code):
    return (code, {"error": {"message": "SECRET-DOC-TEXT should never surface"}}, {}, 0.0)


# ---- F01: timeout / 429 -----------------------------------------------------------------------
def test_row_F01_a_429_is_retried_with_backoff_and_then_succeeds(provider, parts, plain_doc):
    sleeps: list[float] = []
    provider.script = [
        R429,
        (200, chat_body("CONFIDENTIAL", ["SOURCE_CODE"], bucket="high"), {}, 0.0),
    ]
    r = llm_over_http(parts, provider.url, sleeps=sleeps).classify(request(plain_doc.content))
    assert r.status == "ok" and r.level.value == "CONFIDENTIAL" and len(provider.hits) == 2
    assert (
        len(sleeps) == 1 and 0 < sleeps[0] <= parts.cfg.retry.max_delay_s
    )  # bounded, honours Retry-After


def test_row_F01_each_retry_is_visible_in_the_trace_without_any_provider_text(
    provider, parts, plain_doc
):
    from observability import MemorySink, build_tracer, load_observability_config

    sink = MemorySink()
    tracer, _ = build_tracer(load_observability_config()[0], [sink], deterministic_ids=True)
    provider.script = [R429, R429, (200, chat_body("CONFIDENTIAL", ["SOURCE_CODE"]), {}, 0.0)]
    with tracer.trace("classify"):
        llm_over_http(parts, provider.url).classify(request(plain_doc.content))
    call = next(s for s in sink.spans if s.name == "llm.call")
    retries = [e for e in call.events if e.name == "retry"]
    assert [e.attributes["dg.llm.retry_kind"] for e in retries] == ["rate_limited", "rate_limited"]
    assert [e.attributes["dg.llm.attempts"] for e in retries] == [1, 2] and call.attributes[
        "dg.llm.attempts"
    ] == 3
    assert "slow down" not in call.model_dump_json()


def test_row_F01_persistent_429_is_bounded_and_ends_in_review_with_no_level(
    provider, parts, plain_doc
):
    sleeps: list[float] = []
    provider.default = R429
    r = llm_over_http(parts, provider.url, sleeps=sleeps).classify(request(plain_doc.content))
    assert (
        len(provider.hits) == parts.cfg.retry.max_attempts
        and len(sleeps) == parts.cfg.retry.max_attempts - 1
    )
    assert (
        r.status == "review_required"
        and r.level is None
        and r.review.reason_codes == ["LLM_UNAVAILABLE"]
    )
    assert "llm_error:rate_limited" in r.warnings and all(
        s <= parts.cfg.retry.max_delay_s for s in sleeps
    )


def test_row_F01_a_timeout_is_retried_a_bounded_number_of_times(provider, parts, plain_doc):
    provider.default = (200, chat_body("INTERNAL"), {}, 0.6)  # slower than the 0.2 s client timeout
    r = llm_over_http(parts, provider.url, timeout_s=0.2).classify(request(plain_doc.content))
    assert r.status == "review_required" and "llm_error:timeout" in r.warnings
    assert len(provider.hits) == parts.cfg.retry.max_attempts


@pytest.mark.parametrize("code, kind", [(401, "auth"), (403, "auth"), (400, "bad_request")])
def test_row_F01_non_transient_errors_are_not_retried_and_leak_nothing(
    provider, parts, plain_doc, code, kind
):
    provider.default = status(code)
    r = llm_over_http(parts, provider.url).classify(request(plain_doc.content))
    assert len(provider.hits) == 1 and f"llm_error:{kind}" in r.warnings
    assert "SECRET-DOC-TEXT" not in r.model_dump_json()


def test_row_F01_a_failing_tier_escalates_to_the_next_tier_which_decides(
    two_providers, parts, plain_doc
):
    mid_srv, large_srv = two_providers
    mid_srv.default = R429
    large_srv.script = [(200, chat_body("CONFIDENTIAL", ["SOURCE_CODE"]), {}, 0.0)]
    h = hybrid(
        parts,
        rules=parts.rules,
        llms={
            "mid": llm_over_http(parts, mid_srv.url, "mid"),
            "large": llm_over_http(parts, large_srv.url, "large"),
        },
    )
    r = h.classify(request(plain_doc.content))
    assert (
        r.status == "degraded" and r.level.value == "CONFIDENTIAL" and r.routing.escalations == 1
    )  # decided, but flagged
    assert "degraded:llm:mid" in r.warnings and len(mid_srv.hits) == 3 and len(large_srv.hits) == 1


def test_row_F01_when_every_tier_is_down_the_result_is_review_with_the_rules_provisional_label(
    two_providers, parts, hr_doc
):
    a, b = two_providers
    a.default = b.default = R429
    h = hybrid(
        parts,
        rules=parts.rules,
        llms={
            "mid": llm_over_http(parts, a.url, "mid"),
            "large": llm_over_http(parts, b.url, "large"),
        },
    )
    r = h.classify(request(hr_doc.content))
    assert (
        r.status == "review_required"
        and r.review.reason_codes == ["LLM_UNAVAILABLE"]
        and r.review.provisional
    )
    assert r.level.value == "HIGHLY_CONFIDENTIAL" and r.review.priority == 1  # never a low default
    assert len(a.hits) == len(b.hits) == 3


def test_row_F01_when_every_tier_is_down_and_rules_abstain_there_is_no_label_at_all(
    two_providers, parts, plain_doc
):
    a, b = two_providers
    a.default = b.default = status(503)
    h = hybrid(
        parts,
        rules=parts.rules,
        llms={
            "mid": llm_over_http(parts, a.url, "mid"),
            "large": llm_over_http(parts, b.url, "large"),
        },
    )
    r = h.classify(request(plain_doc.content))
    assert (
        r.status == "review_required"
        and r.level is None
        and r.high_risk is None
        and r.categories == []
    )


# ---- F02: malformed JSON ----------------------------------------------------------------------
def test_row_F02_one_repair_retry_then_success(provider, parts, plain_doc):
    provider.script = [
        (200, raw_body("Sure! The level is confidential."), {}, 0.0),
        (200, chat_body("CONFIDENTIAL", ["SOURCE_CODE"]), {}, 0.0),
    ]
    r = llm_over_http(parts, provider.url).classify(request(plain_doc.content))
    assert r.status == "ok" and len(provider.hits) == 2
    assert (
        "not valid (not_json)" in provider.hits[1]["messages"][1]["content"]
    )  # the repair request says why
    assert "not valid" not in provider.hits[0]["messages"][1]["content"]


def test_row_F02_still_malformed_after_the_repair_retry_the_llm_result_is_discarded(
    provider, parts, plain_doc
):
    provider.default = (200, raw_body("not json at all"), {}, 0.0)
    r = llm_over_http(parts, provider.url).classify(request(plain_doc.content))
    assert len(provider.hits) == 2  # exactly one repair retry, never a loop
    assert (
        r.status == "review_required"
        and r.level is None
        and "llm_output_invalid:not_json" in r.warnings
    )


@pytest.mark.parametrize("bad", [
    json.dumps({"level": "TOP_SECRET", "categories": [], "evidence": [], "rationale": "x", "level_confidence": "high", "category_confidence": "high", "insufficient_information": False}),
    json.dumps({"level": "PUBLIC", "categories": ["GOSSIP"], "evidence": [], "rationale": "x", "level_confidence": "high", "category_confidence": "high", "insufficient_information": False}),
    json.dumps({"level": "PUBLIC", "categories": [], "evidence": [], "rationale": "x", "level_confidence": "high", "category_confidence": "high", "insufficient_information": "no"}),
    json.dumps({"level": "PUBLIC"}),
    json.dumps([1, 2, 3]),
    "",
])  # fmt: skip
def test_row_F02_output_that_breaks_the_schema_is_never_used(provider, parts, plain_doc, bad):
    provider.default = (200, raw_body(bad), {}, 0.0)
    r = llm_over_http(parts, provider.url).classify(request(plain_doc.content))
    assert r.level is None and r.status == "review_required"


def test_row_F02_the_router_then_uses_the_remaining_evidence_never_the_discarded_output(
    two_providers, parts, hr_doc
):
    a, b = two_providers
    a.default = b.default = (200, raw_body("garbage"), {}, 0.0)
    h = hybrid(
        parts,
        rules=parts.rules,
        llms={
            "mid": llm_over_http(parts, a.url, "mid"),
            "large": llm_over_http(parts, b.url, "large"),
        },
    )
    r = h.classify(request(hr_doc.content))
    assert (
        r.status == "review_required" and r.level.value == "HIGHLY_CONFIDENTIAL"
    )  # from Rules, provisional
    assert "llm_output_invalid:not_json" in r.warnings


# ---- F03: evidence fails verification ---------------------------------------------------------
def test_row_F03_a_fabricated_quote_is_unverified_capped_and_reviewed_when_high_risk(
    provider, parts, plain_doc
):
    provider.default = (
        200,
        chat_body("HIGHLY_CONFIDENTIAL", ["PHI"], ["a sentence the document never contained"]),
        {},
        0.0,
    )
    r = llm_over_http(parts, provider.url).classify(request(plain_doc.content))
    ev = next(e for e in r.evidence if e.type == "llm_excerpt")
    assert ev.verified is False and ev.provenance == "inferred" and ev.locator is None
    assert r.level.confidence.raw == "low" and r.categories[0].confidence.raw == "low"
    assert (
        r.status == "review_required"
        and r.review.reason_codes == ["EVIDENCE_UNVERIFIED"]
        and r.review.provisional
    )


def test_row_F03_an_unverified_call_that_is_not_high_risk_is_only_capped(
    provider, parts, plain_doc
):
    provider.default = (
        200,
        chat_body("CONFIDENTIAL", ["SOURCE_CODE"], ["invented quote here"]),
        {},
        0.0,
    )
    r = llm_over_http(parts, provider.url).classify(request(plain_doc.content))
    assert r.status == "ok" and r.level.confidence.raw == "low"


def test_row_F03_the_router_escalates_from_unverified_evidence_to_a_verified_answer(
    two_providers, parts, plain_doc
):
    a, b = two_providers
    a.default = (200, chat_body("HIGHLY_CONFIDENTIAL", ["PHI"], ["invented quote here"]), {}, 0.0)
    b.default = (200, chat_body("HIGHLY_CONFIDENTIAL", ["PHI"], [quote_of(plain_doc)]), {}, 0.0)
    h = hybrid(
        parts,
        rules=parts.rules,
        llms={
            "mid": llm_over_http(parts, a.url, "mid"),
            "large": llm_over_http(parts, b.url, "large"),
        },
    )
    r = h.classify(request(plain_doc.content))
    assert (
        r.status == "ok"
        and r.routing.escalations == 1
        and r.routing.stop_reason == "escalated_llm:large"
    )
    assert any(e.verified for e in r.evidence if e.type == "llm_excerpt")


def test_row_F03_when_no_tier_can_verify_the_result_is_a_review_with_the_failsafe_label(
    two_providers, parts, plain_doc
):
    a, b = two_providers
    a.default = b.default = (
        200,
        chat_body("HIGHLY_CONFIDENTIAL", ["PHI"], ["invented quote here"]),
        {},
        0.0,
    )
    h = hybrid(
        parts,
        rules=parts.rules,
        llms={
            "mid": llm_over_http(parts, a.url, "mid"),
            "large": llm_over_http(parts, b.url, "large"),
        },
    )
    r = h.classify(request(plain_doc.content))
    assert r.status == "review_required" and "EVIDENCE_UNVERIFIED" in r.review.reason_codes
    assert (
        r.level.value == "HIGHLY_CONFIDENTIAL" and r.review.provisional and r.review.priority == 1
    )
