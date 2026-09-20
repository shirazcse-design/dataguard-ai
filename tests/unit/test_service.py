"""The ClassificationService: startup validation, schema versions, never-raises, tracing, threads."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.classification.config_loader import ConfigError
from app.classification.hybrid import build_hybrid_classifier
from app.classification.schemas import ClassificationRequest, Document
from app.classification.service import (
    ClassificationService,
    load_service_config,
    parse_schema_version,
    schema_version_supported,
)
from app.llm import LLMError
from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents
from observability import audit_spans, read_jsonl

RECORD = "Employee record\nNational ID: 905-37-6209\nDate of birth: 01/27/1960\n"


@pytest.fixture(scope="module")
def off():
    return ClassificationService(llm_mode="off")


@pytest.fixture(scope="module")
def replay():
    return ClassificationService(llm_mode="replay")


@pytest.fixture(scope="module")
def dev():
    return sorted(
        (d for d in load_documents(DEFAULT_DATA_DIR, splits=["dev"]) if d.split == "dev"),
        key=lambda d: d.doc_id,
    )


def payload(content=RECORD, **over):
    body = {
        "request_id": "r1",
        "document": {"content": content, "filename": "employee_record.txt", "extension": "txt"},
    }
    body.update(over)
    return body


# ---- classification ---------------------------------------------------------------------------
def test_a_text_a_dict_a_request_and_a_json_string_all_give_the_same_answer(off):
    a = off.classify_text(RECORD, request_id="r1", filename="employee_record.txt")
    b = off.classify(payload())
    c = off.classify(
        ClassificationRequest(
            request_id="r1",
            document=Document(content=RECORD, filename="employee_record.txt", extension="txt"),
        )
    )
    d = off.classify(json.dumps(payload()))
    for r in (b, c, d):
        assert r.model_dump(exclude={"telemetry"}) == a.model_dump(exclude={"telemetry"})
    assert a.status == "ok" and a.level.value == "HIGHLY_CONFIDENTIAL" and a.high_risk.value is True


def test_the_service_returns_exactly_what_the_hybrid_returns(replay, dev, bundle):
    direct = build_hybrid_classifier(bundle, variant="default")
    for d in dev[:25]:
        req = d.to_request()
        assert replay.classify(req).model_dump(exclude={"telemetry"}) == direct.classify(
            req
        ).model_dump(exclude={"telemetry"})


def test_request_options_are_passed_through_to_the_router(off):
    assert off.classify_text(RECORD, mode="rules").routing.stages_run == ["rules"]
    assert off.classify_text(RECORD, include_evidence=False).evidence == []
    no_llm = off.classify_text(
        "Notes from the supplier call about next year's pricing.", max_llm_tier="none"
    )
    assert no_llm.level is None and no_llm.status == "review_required"


def test_llm_off_never_calls_a_model_and_says_so(off):
    r = off.classify_text("Notes from the supplier call about next year's pricing.")
    assert (
        "llm" not in " ".join(r.routing.stages_run) and r.level is None
    )  # Rules abstain: a review, not a default
    assert off.info()["stages"] == ["rules"] and off.info()["llm_mode"] == "off"


@pytest.mark.parametrize("hostile", [
    None, 42, 3.5, [], [1], {}, "", "not json", "{", b"bytes", {"request_id": 5}, {"request_id": "x"},
    {"request_id": "x", "document": None}, {"request_id": "x", "document": {"content": ["a"]}},
    {"request_id": "x", "document": {"content": "a", "filename": "a", "extension": "t"}, "options": {"mode": "evil"}},
    {"request_id": "x", "document": {"content": "a", "filename": "a", "extension": "t"}, "options": {"budget": {"max_cost_usd": -1}}},
    {"request_id": "x", "document": {"content": "a", "filename": "a", "extension": "t"}, "caller": "root"},
    {"request_id": "x" * 10000, "document": {"content": "a" * 10, "filename": "a", "extension": "t"}},
])  # fmt: skip
def test_no_input_can_make_classify_raise_and_every_result_is_valid(off, hostile):
    r = off.classify(hostile)  # type: ignore[arg-type]
    assert r.status in {"ok", "degraded", "review_required", "rejected", "error"}
    if r.status in ("rejected", "error"):
        assert r.level is None and r.high_risk is None and r.categories == []
    json.loads(r.model_dump_json())  # always serialisable


def test_error_and_rejection_messages_never_echo_the_document(off):
    r = off.classify(
        {
            "request_id": "x",
            "document": {"content": "SECRET-DOC-TEXT", "filename": ["bad"], "extension": "t"},
        }
    )
    assert r.status == "rejected" and "SECRET-DOC-TEXT" not in r.model_dump_json()


# ---- schema versions --------------------------------------------------------------------------
@pytest.mark.parametrize("value, ok", [("1.0", True), ("1.0", True), ("1.1", False), ("1.9", False), ("2.0", False), ("0.9", False),
                                      ("1", False), ("1.0.0", False), ("v1.0", False), ("", False), (None, False), (1.0, False), ("1.0 ", False)])  # fmt: skip
def test_schema_version_support_is_same_major_and_not_a_newer_minor(value, ok):
    assert schema_version_supported(value) is ok


def test_parse_schema_version():
    assert parse_schema_version("1.0") == (1, 0) and parse_schema_version("12.34") == (12, 34)
    assert parse_schema_version("x") is None and parse_schema_version(3) is None


@pytest.mark.parametrize("version", ["2.0", "1.1", "banana", "1", ""])
def test_a_request_for_an_unsupported_schema_version_is_rejected_not_parsed_leniently(off, version):
    r = off.classify(payload(schema_version=version))
    assert (
        r.status == "rejected"
        and r.level is None
        and r.warnings[0].startswith("unsupported_schema_version:")
    )
    assert r.request_id == "r1"
    if version == "banana":
        assert "banana" not in r.model_dump_json()  # an untrusted string is not echoed


def test_the_current_and_default_versions_are_accepted(off):
    assert off.classify(payload(schema_version="1.0")).status == "ok"
    assert off.classify(payload()).status == "ok"  # absent means the current version


def test_results_carry_the_service_schema_version(off):
    assert off.classify(payload()).schema_version == "1.0"


# ---- startup ----------------------------------------------------------------------------------
def test_service_config_loads_and_is_cross_checked(bundle, config_copy):
    cfg, sha = load_service_config(bundle)
    assert cfg.default_variant == "default" and cfg.llm_mode == "foundry" and len(sha) == 64
    p = config_copy / "service/service.v1.yaml"
    for old, new in [("default_variant: default", "default_variant: nope"), ("taxonomy_version: 1.0.0", "taxonomy_version: 9.9.9"),
                     ("llm_mode: foundry", "llm_mode: maybe"), ("max_batch_size: 100", "max_batch_size: 0")]:  # fmt: skip
        p.write_text(p.read_text().replace(old, new, 1))
        with pytest.raises(ConfigError):
            load_service_config(bundle, config_copy)
        p.write_text(p.read_text().replace(new, old, 1))


def test_the_service_refuses_to_start_on_an_invalid_config(config_copy):
    (config_copy / "routing/routing.v1.yaml").write_text("not: [valid")
    with pytest.raises(ConfigError):
        ClassificationService(config_dir=config_copy, llm_mode="off")


def test_live_mode_without_credentials_fails_at_startup_not_at_request_time(monkeypatch):
    for var in (
        "DATAGUARD_LLM_DEPLOYMENT_MID",
        "DATAGUARD_LLM_DEPLOYMENT_LARGE",
        "DATAGUARD_LLM_DEPLOYMENT_SMALL",
    ):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(LLMError) as exc:
        ClassificationService(llm_mode="foundry")
    assert exc.value.kind == "not_configured" and "DATAGUARD_LLM_DEPLOYMENT" in str(exc.value)


def test_an_unknown_variant_or_mode_is_refused(bundle):
    with pytest.raises(ConfigError, match="unknown routing variant"):
        ClassificationService(bundle, variant="nope", llm_mode="off")
    with pytest.raises(ConfigError):
        ClassificationService(bundle, llm_mode="maybe")


def test_tracing_enabled_without_a_path_is_refused(bundle, config_copy):
    p = config_copy / "service/service.v1.yaml"
    p.write_text(p.read_text().replace("enabled: false", "enabled: true", 1))
    with pytest.raises(ConfigError):
        ClassificationService(config_dir=config_copy, llm_mode="off")


# ---- batch, info, self-check -----------------------------------------------------------------
def test_classify_many_is_ordered_and_capped(off):
    out = off.classify_many([payload(request_id="a"), {"bad": 1}, payload(request_id="c")])
    assert [r.request_id for r in out] == ["a", "unknown", "c"] and [r.status for r in out] == [
        "ok",
        "rejected",
        "ok",
    ]
    with pytest.raises(ValueError, match="max_batch_size"):
        off.classify_many([payload()] * (off.config.max_batch_size + 1))


def test_info_reports_every_version_that_decides_a_result(off):
    info = off.info()
    assert info["schema_version"] == "1.0" and info["service_version"] == "1.0.0"
    assert (
        info["taxonomy"]
        and info["high_risk_config"]
        and info["routing"]["version"]
        and len(info["routing"]["sha256"]) == 64
    )
    assert "PUBLIC" in info["levels"] and "TRADE_SECRET" in info["categories"]
    assert "pending human gold-label review" in info["labels_are"]
    json.dumps(info)


def test_self_check_runs_end_to_end_without_a_model(off):
    assert off.self_check() == {"ok": True, "status": "review_required"}


def test_the_reject_helper_makes_a_valid_rejected_result_and_sanitises_the_id(off):
    r = off.reject("line-3", "invalid_json")
    assert (
        r.status == "rejected"
        and r.warnings == ["input_rejected:invalid_json"]
        and r.request_id == "line-3"
    )
    assert off.reject("Jane Roe SSN 905-37-6209", "oversize").request_id == "unknown"


# ---- concurrency ------------------------------------------------------------------------------
def test_concurrent_requests_give_the_same_results_as_sequential_ones(replay, dev):
    docs = dev[:40]
    seq = [replay.classify(d.to_request()).model_dump(exclude={"telemetry"}) for d in docs]
    with ThreadPoolExecutor(8) as ex:
        par = list(
            ex.map(
                lambda d: replay.classify(d.to_request()).model_dump(exclude={"telemetry"}), docs
            )
        )
    assert seq == par


# ---- tracing ----------------------------------------------------------------------------------
def test_a_traced_service_writes_spans_with_no_document_text_including_rejections(tmp_path, dev):
    trace = tmp_path / "spans.jsonl"
    svc = ClassificationService(llm_mode="replay", trace_path=trace)
    for d in dev:
        svc.classify(d.to_request())
    svc.classify(payload(schema_version="9.9"))
    svc.classify({"request_id": "bad"})
    svc.classify(payload(""))
    text = trace.read_text()
    spans = read_jsonl(trace)
    roots = [s for s in spans if s.name == "classify"]
    assert len(roots) == len(dev) + 3
    assert audit_spans(text, dev).clean
    assert any(
        s.attributes.get("dg.stop_reason", "").startswith("unsupported_schema_version")
        for s in roots
    )
    assert {"S1.rules", "S3.llm.mid"} <= {s.name for s in spans}


def test_tracing_does_not_change_results(tmp_path, dev):
    plain = ClassificationService(llm_mode="replay")
    traced = ClassificationService(llm_mode="replay", trace_path=tmp_path / "s.jsonl")
    for d in dev[:30]:
        req = d.to_request()
        assert plain.classify(req).model_dump(exclude={"telemetry"}) == traced.classify(
            req
        ).model_dump(exclude={"telemetry"})
