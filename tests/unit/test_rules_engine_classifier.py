"""Engine aggregation, the Classifier contract, harness integration and safety properties."""

from __future__ import annotations

import time

import pytest

from app.classification.interfaces import Classifier
from app.classification.schemas import ClassificationRequest, Document, ExistingLabel
from evals.classification.dataset.build import load_documents, load_manifest
from evals.classification.evaluate import evaluate
from rules import RulesEngine, load_rules_config
from rules.types import RulesResult
from tests.helpers import mkdoc

HC, C, INTERNAL = "HIGHLY_CONFIDENTIAL", "CONFIDENTIAL", "INTERNAL"


def request(text, filename="x.txt", labels=(), include_evidence=True, doc_id="d1"):
    ext = filename.rsplit(".", 1)[1] if "." in filename else ""
    req = ClassificationRequest(
        request_id="r1",
        document=Document(
            document_id=doc_id,
            content=text,
            filename=filename,
            extension=ext,
            existing_labels=[ExistingLabel(scheme=s, value=v) for s, v in labels],
        ),
    )
    req.options.include_evidence = include_evidence
    return req


# ---- engine aggregation ---------------------------------------------------------------------
def test_no_detection_at_all_is_no_signal_and_abstains(analyze):
    r = analyze("Nothing to see in this ordinary memo about lunch.")
    assert r.no_signal and r.level is None and r.categories == {} and r.detections == []


def test_weak_evidence_is_retained_but_asserts_nothing(analyze):
    r = analyze("Reference 912-34-5678 for the file.")
    assert not r.no_signal and r.categories == {} and r.level is None
    assert r.weak_only_categories == ["PII"] and len(r.detections) == 1


def test_level_is_the_highest_of_floors_hints_and_banners(analyze):
    # Confidential banner + a PII contact bulk (floor CONFIDENTIAL) -> CONFIDENTIAL;
    # add a government id (hint HC) -> HC.
    assert analyze("Confidential\nSSN: 912-34-5678").level == HC
    r = analyze("Confidential\nnotes")
    assert r.level == C and r.categories == {}


def test_categories_report_the_strongest_strength(analyze):
    r = analyze("card_number: 4539 1488 0343 6467\nref 4556737586899855\n")
    assert r.categories == {"FINANCIAL_PCI": "definitive"}


def test_emit_threshold_is_configurable(bundle, tmp_path):
    import shutil

    from app.classification.config_loader import default_config_dir

    d = tmp_path / "config"
    shutil.copytree(default_config_dir(), d)
    p = d / "rules/rules.v1.yaml"
    p.write_text(
        p.read_text().replace(
            "  default: strong\n  overrides: {}", "  default: definitive\n  overrides: {}"
        )
    )
    cfg, _ = load_rules_config(bundle.policy, d)
    eng = RulesEngine(cfg, bundle.policy)
    r = eng.analyze(Document(content="SSN: 912-34-5678", filename="a.txt", extension="txt"))
    assert r.categories == {} and r.weak_only_categories == ["PII"] and r.level is None


def test_engine_is_deterministic_and_ordered(analyze):
    text = "SSN: 912-34-5678\ncard_number: 4539 1488 0343 6467\npassword=Copper!Falcon84\n"
    a, b = analyze(text), analyze(text)
    key = lambda r: [(d.detector_id, d.start, d.strength) for d in r.detections]  # noqa: E731
    assert key(a) == key(b) and a.categories == b.categories and a.level == b.level


def test_oversize_input_is_truncated_and_flagged(analyze, rules_engine):
    cap = rules_engine.cfg.max_content_chars
    r = analyze("x" * (cap + 10))
    assert r.truncated
    # content beyond the cap is NOT scanned: a secret placed after it is missed (stated limitation)
    hidden = analyze("x" * (cap + 5) + "\npassword=Copper!Falcon84")
    assert hidden.truncated and hidden.categories == {}


def test_result_type_and_version(analyze):
    r = analyze("hello")
    assert isinstance(r, RulesResult) and r.ruleset_version and r.elapsed_ms >= 0


# ---- classifier contract --------------------------------------------------------------------
def test_rules_classifier_implements_the_interface(rules_classifier):
    assert isinstance(rules_classifier, Classifier) and rules_classifier.name == "rules"
    p = rules_classifier.params()
    assert p["standalone_default_level"] == INTERNAL and len(p["rules_config_sha256"]) == 64
    assert (
        "pii.ssn_like@1.0.0" in p["detectors"] and p["ruleset_version"] == rules_classifier.version
    )


def test_abstention_reports_the_default_level_honestly(rules_classifier):
    res = rules_classifier.classify(request("An ordinary memo about the cafeteria."))
    assert res.status == "ok" and res.level.value == INTERNAL
    assert res.level.confidence.kind == "none" and res.level.decided_by == "rules"
    assert res.routing.abstained and res.routing.stop_reason == "rules_abstained_default_level"
    assert any(w.startswith("level_abstained:standalone_default=INTERNAL") for w in res.warnings)
    assert res.categories == [] and res.high_risk.value is False


def test_no_match_never_yields_public(rules_classifier):
    """NO RULE MATCH DOES NOT MEAN PUBLIC: even an explicit 'PUBLIC' label cannot produce Public."""
    res = rules_classifier.classify(
        request("FOR IMMEDIATE RELEASE: our new product", labels=[("banner", "PUBLIC")])
    )
    assert res.level.value == INTERNAL and res.routing.abstained


def test_decided_result_carries_rule_strength_evidence(rules_classifier):
    res = rules_classifier.classify(request("Social Security Number: 912-34-5678\n"))
    assert not res.routing.abstained and res.level.value == HC
    assert res.level.confidence.kind == "rule_strength" and res.level.confidence.raw == "strong"
    (cat,) = res.categories
    assert (
        cat.id == "PII"
        and cat.confidence.kind == "rule_strength"
        and cat.confidence.calibrated is False
    )
    ev = {e.evidence_id: e for e in res.evidence}
    assert cat.evidence_ids and all(i in ev for i in cat.evidence_ids)
    e = ev[cat.evidence_ids[0]]
    assert (
        e.detector.id == "pii.ssn_like" and e.detector.version == "1.0.0" and e.strength == "strong"
    )
    assert e.provenance == "observed" and e.type == "validator_passed" and e.locator.line == 1
    assert res.high_risk.value is True and res.versions.ruleset == rules_classifier.version
    assert res.versions.classifier == f"rules@{rules_classifier.version}"


def test_evidence_can_be_omitted(rules_classifier):
    res = rules_classifier.classify(request("SSN: 912-34-5678", include_evidence=False))
    assert res.evidence == [] and res.categories[0].evidence_ids == []


def test_suppressions_and_weak_signals_are_visible_as_warnings(rules_classifier):
    res = rules_classifier.classify(
        request("SSN: 123-45-6789 is a placeholder. Reference 912-34-5678 too.")
    )
    assert any(w.startswith("suppressed:pii.ssn_like:") for w in res.warnings)
    res2 = rules_classifier.classify(request("Reference 912-34-5678 for the file."))
    assert "weak_signals_only:PII" in res2.warnings


def test_telemetry_reports_latency(rules_classifier):
    res = rules_classifier.classify(request("hello"))
    assert (
        res.telemetry.latency_ms["rules"] >= 0
        and res.telemetry.latency_ms["total"] >= res.telemetry.latency_ms["rules"]
    )


def test_classifier_result_matches_request_identity(rules_classifier):
    req = request("hello", doc_id="doc-77")
    res = rules_classifier.classify(req)
    assert (
        res.request_id == "r1"
        and res.document_id == "doc-77"
        and res.content_hash == req.document.content_hash()
    )


# ---- no raw sensitive value in evidence -----------------------------------------------------
VALUE_DETECTORS = {
    "pii.ssn_like", "pii.passport_field", "pii.dob_field", "phi.mrn", "fin.card_pan", "fin.iban",
    "fin.us_bank_account", "cred.assignment", "cred.prefixed_token", "cred.jwt", "cred.bearer",
    "cred.url_password", "cred.aws_access_key",
}  # fmt: skip


def test_evidence_never_contains_the_raw_matched_value(rules_engine):
    """Over every development document: masked excerpts must not contain the raw match."""
    docs = load_documents()  # train + calibration + dev
    checked = 0
    for d in docs:
        res = rules_engine.analyze(d.to_request().document)
        for det in res.detections:
            if det.detector_id in VALUE_DETECTORS:
                raw = d.content[det.start : det.end]
                assert len(raw) >= 6
                assert raw not in det.masked_excerpt, (det.detector_id, d.family_id)
                assert det.excerpt_hash != raw
                checked += 1
    assert checked > 200  # the property was exercised on a substantial number of value detections


def test_all_evidence_excerpts_are_short_and_single_line(rules_engine):
    for d in load_documents(splits=["dev"]):
        for det in rules_engine.analyze(d.to_request().document).detections:
            assert len(det.masked_excerpt) <= 120 and "\n" not in det.masked_excerpt


# ---- harness integration --------------------------------------------------------------------
def test_rules_run_through_the_harness_with_abstention_and_no_failures(rules_classifier, bundle):
    docs = [d for d in load_documents(splits=["dev"])][:60]
    res = evaluate(rules_classifier, docs, bundle, load_manifest())
    cov = res.metrics["all_tiers"]["coverage"]
    assert cov["n_failed"] == 0 and cov["classifier_high_risk_mismatches"] == 0
    assert 0 < cov["n_abstained"] < cov["n_docs"] and cov["abstention_rate"] == pytest.approx(
        cov["n_abstained"] / cov["n_docs"]
    )
    assert res.manifest["classifier"]["params"]["standalone_default_level"] == INTERNAL
    assert res.manifest["dataset"]["evaluated_locked_test_split"] is False


def test_rules_are_deterministic_across_runs(rules_classifier, bundle):
    docs = load_documents(splits=["dev"])
    a = evaluate(rules_classifier, docs, bundle, load_manifest())
    b = evaluate(rules_classifier, docs, bundle, load_manifest())
    assert a.fingerprint == b.fingerprint


# ---- performance (PRD 5.1: deterministic pre-check <= 500 ms) ---------------------------------
def test_typical_documents_are_far_below_the_latency_budget(rules_engine):
    docs = load_documents(splits=["train"])
    times = []
    for d in docs[:200]:
        t = time.perf_counter()
        rules_engine.analyze(d.to_request().document)
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    assert times[int(len(times) * 0.95)] < 50, "P95 is expected to be a few milliseconds"
    assert times[-1] < 500


ADVERSARIAL = {
    "digit_run": "1" * 200_000,
    "spaced_digits": "1 " * 100_000,
    "dashed_digits": "12-" * 66_000,
    "at_signs": "a@" * 100_000,
    "repeated_password_key": "password=" * 22_000,
    "long_open_parens": "(" * 200_000,
    "long_quotes": '"' * 200_000,
    "many_short_code_lines": "x = 1;\n" * 28_000,
    "blank_lines": "\n" * 200_000,
    "iban_like": "GB82 " + "ABCD " * 40_000,
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_adversarial_inputs_stay_within_the_pre_check_budget(rules_engine, name):
    """No catastrophic backtracking: even hostile 200 KB inputs finish under the 500 ms target
    (they are truncated to the scan cap first)."""
    doc = Document(content=ADVERSARIAL[name], filename="hostile.py", extension="py")
    worst = 0.0
    for _ in range(2):
        t = time.perf_counter()
        rules_engine.analyze(doc)
        worst = max(worst, (time.perf_counter() - t) * 1000)
    assert worst < 500, f"{name} took {worst:.0f} ms"


# ---- hard negatives from the dataset (regression, development splits only) -------------------
HARD_NEGATIVE_FAMILIES = {
    "hn_awareness_doc_explains_ssn": "PII",
    "hn_test_card_numbers_doc": "FINANCIAL_PCI",
    "hn_announced_acquisition_news": "MA_CORP_STRATEGY",
    "hn_code_reads_env_secrets": "CREDENTIALS_SECRETS",
    "hn_public_api_docs_placeholder_keys": "CREDENTIALS_SECRETS",
    "hn_masked_card_receipt": "FINANCIAL_PCI",
    "hn_sku_lookalike_ssn": "PII",
    "hn_tracking_numbers_lookalike_card": "FINANCIAL_PCI",
    "hn_blank_hr_form_template": "PII",
    "hn_open_source_code": "SOURCE_CODE",
    "hn_synthetic_test_fixtures": "PII",
    "hn_business_case_study": "MA_CORP_STRATEGY",
}


def test_named_hard_negatives_do_not_trigger_their_decoy_category(rules_engine):
    """Documentation of an SSN format, fake/test payment data, an already-public acquisition, and code
    naming a `password` variable must not be treated as sensitive (checked on every development
    document of these families)."""
    seen: dict[str, int] = {}
    for d in load_documents():
        decoy = HARD_NEGATIVE_FAMILIES.get(d.family_id)
        if decoy is None:
            continue
        res = rules_engine.analyze(d.to_request().document)
        assert decoy not in res.categories, f"{d.family_id} wrongly asserted {decoy}"
        seen[d.family_id] = seen.get(d.family_id, 0) + 1
    assert len(seen) >= 6, f"only {sorted(seen)} were available in the development splits"


def test_rules_engine_never_crashes_on_any_development_document(rules_engine, bundle):
    valid = set(bundle.policy.category_ids)
    for d in load_documents():
        res = rules_engine.analyze(d.to_request().document)
        assert set(res.categories) <= valid
        assert res.level is None or res.level in bundle.policy.level_ids
        if res.level:
            floor = bundle.policy.floor_level_for(res.categories)
            assert floor is None or bundle.policy.level_rank(res.level) >= bundle.policy.level_rank(
                floor
            )


def test_rules_never_predict_public(rules_engine):
    """Rules have no positive 'public' detector, so PUBLIC is never produced by evidence."""
    for d in load_documents():
        assert rules_engine.analyze(d.to_request().document).level != "PUBLIC"


def test_helper_documents_still_build():
    assert mkdoc("z").doc_id == "z"
