"""Build/IO layer, CLI, and invariants over the real committed dataset."""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
from pathlib import Path

import pytest

from app.classification.cli import main
from app.classification.schemas import ClassificationRequest
from app.classification.schemas.common import sha256_text
from evals.classification.dataset import fakes
from evals.classification.dataset.build import (
    DEFAULT_DATA_DIR,
    DatasetIntegrityError,
    build_dataset,
    load_documents,
    load_manifest,
)
from evals.classification.dataset.report import REVIEW_COLUMNS, render_report, render_review_sheet
from evals.classification.dataset.schema import SPLIT_NAMES, TIERS
from evals.classification.lock import LockedTestAuthorization, LockedTestSplitError

# Dataset-STRUCTURE tests validate the dataset itself; they never tune anything on the test split.
AUTH = LockedTestAuthorization.now("test-suite: dataset structure validation")


def load_all(data_dir=None, **kw):
    return load_documents(data_dir, splits=list(SPLIT_NAMES), locked_test_authorization=AUTH, **kw)


@pytest.fixture(scope="module")
def docs():
    return load_all()


@pytest.fixture(scope="module")
def manifest():
    return load_manifest()


@pytest.fixture(scope="module")
def rebuilt(bundle):
    return build_dataset(bundle)


@pytest.fixture()
def data_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "data"
    shutil.copytree(DEFAULT_DATA_DIR, dst, ignore=shutil.ignore_patterns("spec", "review"))
    return dst


# ---- reproducibility and manifest integrity -------------------------------------------------
def test_committed_dataset_is_reproduced_byte_for_byte_from_spec_and_seed(rebuilt, manifest):
    for rel, text in rebuilt.files.items():
        assert (DEFAULT_DATA_DIR / rel).read_text(encoding="utf-8") == text, rel
    assert rebuilt.manifest["dataset_sha256"] == manifest["dataset_sha256"]


def test_manifest_hashes_match_files(manifest):
    for split in SPLIT_NAMES:
        meta = manifest["files"][split]
        text = (DEFAULT_DATA_DIR / meta["path"]).read_text(encoding="utf-8")
        assert sha256_text(text) == meta["sha256"]
        assert len(text.splitlines()) == meta["n_docs"]


def test_tampering_with_a_gold_label_is_detected(data_copy):
    path = data_copy / "docs/test.jsonl"
    text = path.read_text(encoding="utf-8")
    assert '"gold_level": "INTERNAL"' in text
    path.write_text(
        text.replace('"gold_level": "INTERNAL"', '"gold_level": "PUBLIC"', 1), encoding="utf-8"
    )
    with pytest.raises(DatasetIntegrityError, match="manifest hash"):
        load_all(data_copy)
    tampered = load_all(data_copy, verify=False)  # explicit opt-out still loads
    assert len(tampered) == len(load_all())


def test_dropping_a_document_is_detected(data_copy):
    path = data_copy / "docs/dev.jsonl"
    lines = path.read_text().splitlines(keepends=True)
    path.write_text("".join(lines[:-1]), encoding="utf-8")
    manifest = json.loads((data_copy / "manifest.json").read_text())
    manifest["files"]["dev"]["sha256"] = sha256_text(path.read_text())  # attacker fixes the hash
    (data_copy / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(DatasetIntegrityError, match="document count"):
        load_all(data_copy)


def test_default_loading_excludes_the_locked_test_split(docs):
    default = load_documents()
    assert {d.split for d in default} == {"train", "calibration", "dev"}
    assert len(default) == sum(d.split != "test" for d in docs)


def test_requesting_the_locked_split_without_authorisation_is_refused():
    with pytest.raises(LockedTestSplitError, match="locked"):
        load_documents(splits=["test"])
    with pytest.raises(LockedTestSplitError):
        load_documents(splits=["dev", "test"])
    test_only = load_documents(splits=["test"], locked_test_authorization=AUTH)
    assert test_only and {d.split for d in test_only} == {"test"}


# ---- structural invariants over the real data -----------------------------------------------
def test_dataset_meets_the_approved_shape(docs, manifest):
    assert 800 * 0.9 <= len(docs) <= 800 * 1.1  # "approximately 800"
    test_n = sum(d.split == "test" for d in docs)
    assert 200 * 0.85 <= test_n <= 200 * 1.2  # "approximately 200"
    assert manifest["n_documents"] == len(docs)


def test_integrity_report_is_clean(rebuilt):
    assert rebuilt.report.errors == []
    assert rebuilt.report.ok


def test_every_group_lives_in_exactly_one_split(docs):
    seen: dict[str, str] = {}
    for d in docs:
        assert seen.setdefault(d.group_id, d.split) == d.split, d.group_id


def test_test_split_has_every_tier_and_every_label(docs, bundle):
    test = [d for d in docs if d.split == "test"]
    assert {d.tier for d in test} == set(TIERS)
    assert {d.gold_level for d in test} == set(bundle.policy.level_ids)
    assert {c for d in test for c in d.gold_categories} == set(bundle.policy.category_ids)


def test_all_tiers_and_kinds_of_case_are_present(docs):
    """Approved: easy, semantic, ambiguous, hard-negative and adversarial cases all exist."""
    by_tier = {t: [d for d in docs if d.tier == t] for t in TIERS}
    assert all(by_tier[t] for t in TIERS)
    assert all(d.ambiguity_flag for d in by_tier["T3"])
    assert all(d.decoy_for for d in by_tier["T4"])
    assert all(d.adversarial_type for d in by_tier["T5"])
    assert any(len(d.gold_categories) > 1 for d in docs)  # multi-label documents exist
    assert any(not d.gold_categories for d in docs)  # and category-free documents


def test_gold_labels_are_valid_and_high_risk_matches_manifest(docs, bundle, manifest):
    policy = bundle.policy
    assert all(policy.label_errors(d.gold_level, d.gold_categories) == [] for d in docs)
    hr = sum(policy.derive_high_risk(d.gold_level, d.gold_categories).value for d in docs)
    assert hr == manifest["stats"]["overall"]["high_risk_docs"]


def test_doc_ids_are_opaque_and_unique(docs):
    ids = [d.doc_id for d in docs]
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"uc4-[0-9a-f]{10}", i) for i in ids)


_FORBIDDEN_REQUEST_KEYS = {
    "gold_level", "gold_categories", "gold_evidence_spans", "tier", "family_id", "group_id",
    "split", "ambiguity_flag", "acceptable_alternative_levels", "decoy_for", "adversarial_type",
    "annotation_notes", "annotation_status", "generator", "format",
}  # fmt: skip


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def test_requests_are_valid_and_expose_no_ground_truth_or_bookkeeping(docs):
    for d in docs:
        req = d.to_request()
        assert isinstance(req, ClassificationRequest)
        assert not (set(_keys(req.model_dump())) & _FORBIDDEN_REQUEST_KEYS)
        dumped = req.model_dump_json()
        assert d.family_id not in dumped and d.group_id not in dumped
        assert req.document.content == d.content and req.document.document_id == d.doc_id


def test_metadata_conflict_cases_exist(docs):
    conflicted = [
        d
        for d in docs
        if any(lab.value == "PUBLIC" for lab in d.existing_labels) and d.gold_level != "PUBLIC"
    ]
    assert len(conflicted) >= 10 and {d.gold_level for d in conflicted} == {"HIGHLY_CONFIDENTIAL"}
    benign_banner = [
        d for d in docs if d.tier == "T4" and d.existing_labels and d.gold_level == "INTERNAL"
    ]
    assert benign_banner  # a 'STRICTLY CONFIDENTIAL' banner on an ordinary document


# ---- synthetic-data safety: nothing here may look like a real person, card or credential -----
_EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
_PHONE = re.compile(r"\(\d{3}\) (\d{3})-(\d{4})")
_SSN = re.compile(r"\b(\d{3})-(\d{2})-(\d{4})\b")
_DIGIT_RUN = re.compile(r"\b(?:\d[ -]?){15,16}\b")
_PUBLIC_TEST_PANS = {
    "4111111111111111", "5555555555554444", "378282246310005",
    "4242424242424242", "4000000000000002",
}  # fmt: skip
_ALLOWED_SSN_LIKE = {"123-45-6789"}  # the canonical dummy used in the awareness document


def test_emails_use_only_reserved_example_domains(docs):
    for d in docs:
        for m in _EMAIL.finditer(d.content):
            assert m.group(1).endswith(".example") or m.group(1) == "example.com", (
                d.family_id,
                m.group(0),
            )


def test_phone_numbers_use_the_fictional_555_01xx_range(docs):
    for d in docs:
        for m in _PHONE.finditer(d.content):
            assert m.group(1) == "555" and m.group(2).startswith("01"), (d.family_id, m.group(0))


def test_ssn_like_values_are_never_issuable(docs):
    for d in docs:
        for m in _SSN.finditer(d.content):
            value = m.group(0)
            if value in _ALLOWED_SSN_LIKE:
                continue
            assert 900 <= int(m.group(1)) <= 999 and 1 <= int(m.group(2)) <= 49, (
                d.family_id,
                value,
            )


def test_card_like_numbers_are_test_prefix_cards_or_non_luhn_lookalikes(docs):
    test_prefixes = tuple(p for p, _ in fakes.CARD_PREFIXES)
    for d in docs:
        for m in _DIGIT_RUN.finditer(d.content):
            digits = re.sub(r"\D", "", m.group(0))
            if fakes.luhn_valid(digits):
                assert digits in _PUBLIC_TEST_PANS or digits.startswith(test_prefixes), (
                    d.family_id,
                    digits,
                )


def test_no_real_provider_secret_formats_or_pem_blocks(docs):
    bad = [
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
        re.compile(r"\bsk_live_[A-Za-z0-9]{10,}"),
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    ]
    for d in docs:
        for pat in bad:
            assert not pat.search(d.content), (d.family_id, pat.pattern)
        for m in re.finditer(r"\bAKIA[0-9A-Z]{16}\b", d.content):
            assert m.group(0) == fakes.AWS_EXAMPLE_KEY  # only the vendor-documented example


def test_ibans_use_the_fictional_bank_code(docs):
    for d in docs:
        for m in re.finditer(r"\bGB\d{2} ?(?:[A-Z0-9]{4} ?){3,}[A-Z0-9]{0,4}", d.content):
            assert "DGFK" in m.group(0).replace(" ", "")[4:8], (d.family_id, m.group(0))


# ---- report and review sheet ----------------------------------------------------------------
def test_report_numbers_come_from_the_manifest(manifest, bundle):
    text = render_report(manifest, bundle.policy.level_ids, bundle.policy.category_ids)
    assert f"**{manifest['n_documents']}**" in text and manifest["dataset_sha256"] in text
    for split in SPLIT_NAMES:
        assert f"| {split} | {manifest['stats']['by_split'][split]['n_docs']} |" in text
    assert "pending human gold-label review" in text
    assert "NOT independently human-validated" in text


def test_review_sheet_has_one_row_per_family(docs):
    text = render_review_sheet(docs)
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == REVIEW_COLUMNS
    assert len(rows) - 1 == len({d.family_id for d in docs})
    assert text == render_review_sheet(list(reversed(docs)))  # deterministic, order-independent
    ids = [r[0] for r in rows[1:]]
    assert ids == sorted(ids)


# ---- CLI ------------------------------------------------------------------------------------
def test_cli_generate_matches_committed_files(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["dataset", "generate", "--out-dir", str(out)]) == 0
    assert "wrote" in capsys.readouterr().out
    for split in SPLIT_NAMES:
        assert (out / f"docs/{split}.jsonl").read_text() == (
            DEFAULT_DATA_DIR / f"docs/{split}.jsonl"
        ).read_text()


def test_cli_validate_and_stats_and_report(tmp_path, capsys):
    assert main(["dataset", "validate"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert main(["dataset", "stats"]) == 0
    assert "by_split" in json.loads(capsys.readouterr().out)
    out = tmp_path / "report.md"
    assert main(["dataset", "report", "--out", str(out)]) == 0
    assert out.read_text().startswith("# UC4 synthetic dataset report")
    sheet = tmp_path / "sheet.csv"
    assert main(["dataset", "review-sheet", "--out", str(sheet)]) == 0
    assert sheet.read_text().splitlines()[0].startswith("family_id,tier,split")


def test_cli_validate_fails_when_committed_data_is_stale(data_copy, capsys):
    manifest = json.loads((data_copy / "manifest.json").read_text())
    manifest["dataset_sha256"] = "0" * 64
    (data_copy / "manifest.json").write_text(json.dumps(manifest))
    assert main(["dataset", "validate", "--data-dir", str(data_copy)]) == 3
    assert "differs from the committed dataset" in capsys.readouterr().err


def test_cli_generate_refuses_to_write_when_integrity_fails(tmp_path, capsys):
    from evals.classification.dataset.spec import DEFAULT_SPEC_DIR

    spec = tmp_path / "spec"
    shutil.copytree(DEFAULT_SPEC_DIR, spec)
    fam = spec / "families/t1_levels.yaml"
    # Break the test split's tier coverage by removing every family except one.
    fam.write_text(fam.read_text().split("  - family_id: pub_careers_faq")[0])
    for extra in (spec / "families").glob("*.yaml"):
        if extra.name != "t1_levels.yaml":
            extra.unlink()
    out = tmp_path / "out"
    assert main(["dataset", "generate", "--spec-dir", str(spec), "--out-dir", str(out)]) == 3
    assert not out.exists()  # nothing written on integrity errors
    assert "INTEGRITY ERRORS" in capsys.readouterr().err


LABEL_STATUS = "AI-generated synthetic dataset — pending human gold-label review"


def test_dataset_is_marked_pending_human_review_everywhere(docs, manifest):
    """Approved: never represent the gold labels as independently human-validated."""
    assert manifest["label_status"] == LABEL_STATUS
    assert manifest["annotation_status"] == {"unreviewed": len(docs)}
    assert all(d.annotation_status == "unreviewed" for d in docs)
