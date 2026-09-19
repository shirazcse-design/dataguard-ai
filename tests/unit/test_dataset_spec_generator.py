"""Spec loading, document schema and deterministic generation."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from evals.classification.dataset.generator import generate_family_docs
from evals.classification.dataset.schema import DatasetDocument, GoldEvidenceSpan
from evals.classification.dataset.spec import DEFAULT_SPEC_DIR, FamilySpec, load_spec
from evals.classification.dataset.template import TemplateError

FAMILIES = {
    "families": [
        {
            "family_id": "pii_test_family",
            "description": "test",
            "tier": "T1",
            "format": "csv_table",
            "gold_level": "CONFIDENTIAL",
            "gold_categories": ["PII"],
            "n_docs": 4,
            "filenames": ["contacts_«@year».csv"],
            "bodies": ["name,email\n⟦PII§«person»,«person.email»⟧\nrows: «@int:1-9»"],
            "metadata": {"source_system": "«source_system»"},
            "existing_labels": [{"scheme": "banner", "value": "«A|B»", "p": 1.0}],
        },
        {
            "family_id": "int_test_family",
            "description": "test",
            "tier": "T1",
            "format": "meeting_notes",
            "gold_level": "INTERNAL",
            "n_docs": 3,
            "filenames": ["notes.txt"],
            "bodies": ["Notes from «person» about the «product_noun» roadmap review."],
        },
    ]
}


@pytest.fixture()
def spec_dir(tmp_path: Path) -> Path:
    d = tmp_path / "spec"
    (d / "families").mkdir(parents=True)
    shutil.copy(DEFAULT_SPEC_DIR / "dataset_spec.yaml", d / "dataset_spec.yaml")
    shutil.copy(DEFAULT_SPEC_DIR / "pools.yaml", d / "pools.yaml")
    (d / "families" / "test.yaml").write_text(yaml.safe_dump(FAMILIES, allow_unicode=True))
    return d


def test_load_spec_and_hash_tracks_content(spec_dir):
    s1 = load_spec(spec_dir)
    assert [f.family_id for f in s1.families] == ["pii_test_family", "int_test_family"]
    assert load_spec(spec_dir).spec_hash == s1.spec_hash
    (spec_dir / "pools.yaml").write_text((spec_dir / "pools.yaml").read_text() + "\nextra: [a]\n")
    assert load_spec(spec_dir).spec_hash != s1.spec_hash


def test_duplicate_family_ids_rejected(spec_dir):
    dup = {"families": [FAMILIES["families"][0]]}
    (spec_dir / "families" / "dup.yaml").write_text(yaml.safe_dump(dup, allow_unicode=True))
    with pytest.raises(ValueError, match="duplicate family_id"):
        load_spec(spec_dir)


def test_split_fractions_must_sum_to_one(spec_dir):
    p = spec_dir / "dataset_spec.yaml"
    p.write_text(p.read_text().replace("train: 0.50", "train: 0.60"))
    with pytest.raises(ValueError, match="sum to 1"):
        load_spec(spec_dir)


def test_family_id_and_unknown_fields_are_validated():
    base = dict(FAMILIES["families"][1])
    with pytest.raises(ValueError, match="lower_snake_case"):
        FamilySpec.model_validate({**base, "family_id": "Bad-Id"})
    with pytest.raises(ValueError):
        FamilySpec.model_validate({**base, "unexpected": 1})


def _gen(spec, fid, split="train"):
    fam = next(f for f in spec.families if f.family_id == fid)
    return generate_family_docs(fam, spec, split, spec.dataset.taxonomy_version)


def test_generation_is_deterministic_and_independent_of_other_families(spec_dir):
    a = _gen(load_spec(spec_dir), "pii_test_family")
    assert [d.model_dump() for d in a] == [
        d.model_dump() for d in _gen(load_spec(spec_dir), "pii_test_family")
    ]
    # Removing the other family must not change this family's documents.
    only = {"families": [FAMILIES["families"][0]]}
    (spec_dir / "families" / "test.yaml").write_text(yaml.safe_dump(only, allow_unicode=True))
    assert [d.model_dump() for d in a] == [
        d.model_dump() for d in _gen(load_spec(spec_dir), "pii_test_family")
    ]


def test_documents_are_well_formed(spec_dir):
    spec = load_spec(spec_dir)
    docs = _gen(spec, "pii_test_family")
    assert len(docs) == 4 and len({d.doc_id for d in docs}) == 4
    for d in docs:
        assert d.doc_id.startswith("uc4-") and "pii" not in d.doc_id and "family" not in d.doc_id
        assert d.extension == "csv" and d.tier == "T1" and d.group_id == "pii_test_family"
        assert d.metadata["source_system"] and d.existing_labels[0].value in {"A", "B"}
        assert len(d.gold_evidence_spans) == 1
        sp = d.gold_evidence_spans[0]
        assert d.content[sp.char_start : sp.char_end] == sp.text and sp.label == "PII"
        assert "⟦" not in d.content and "«" not in d.content
        assert d.generator == "template-v1" and d.annotation_status == "unreviewed"


def test_group_id_overrides_family_id(spec_dir):
    fams = FAMILIES["families"][:1]
    fams[0] = {**fams[0], "group_id": "shared_group"}
    (spec_dir / "families" / "test.yaml").write_text(
        yaml.safe_dump({"families": fams}, allow_unicode=True)
    )
    assert {d.group_id for d in _gen(load_spec(spec_dir), "pii_test_family")} == {"shared_group"}


def test_unknown_slot_error_names_the_family(spec_dir):
    fams = [{**FAMILIES["families"][1], "bodies": ["hello «nosuchslot»"]}]
    (spec_dir / "families" / "test.yaml").write_text(
        yaml.safe_dump({"families": fams}, allow_unicode=True)
    )
    with pytest.raises(TemplateError, match="int_test_family"):
        _gen(load_spec(spec_dir), "int_test_family")


def test_evidence_markers_in_filename_are_rejected(spec_dir):
    fams = [{**FAMILIES["families"][1], "filenames": ["⟦PII§x⟧.txt"]}]
    (spec_dir / "families" / "test.yaml").write_text(
        yaml.safe_dump({"families": fams}, allow_unicode=True)
    )
    with pytest.raises(TemplateError, match="filename"):
        _gen(load_spec(spec_dir), "int_test_family")


def test_existing_label_probability_zero_effect_and_family_pools(spec_dir):
    fams = [
        {
            **FAMILIES["families"][1],
            "n_docs": 20,
            "pools": {"widget": ["w1", "w2"]},
            "bodies": ["The «widget» is fine and this note is long enough to be valid content."],
            "existing_labels": [{"scheme": "banner", "value": "X", "p": 0.5}],
        }
    ]
    (spec_dir / "families" / "test.yaml").write_text(
        yaml.safe_dump({"families": fams}, allow_unicode=True)
    )
    docs = _gen(load_spec(spec_dir), "int_test_family")
    present = sum(bool(d.existing_labels) for d in docs)
    assert 0 < present < 20  # some, not all
    assert all("w1" in d.content or "w2" in d.content for d in docs)


def _valid_doc(**kw):
    content = "Some synthetic document content that is long enough."
    from app.classification.schemas.common import sha256_text

    base = dict(
        doc_id="d1", split="train", tier="T1", family_id="f", group_id="f", generator="g", format="memo",
        filename="a.txt", extension="txt", content=content, gold_level="INTERNAL",
        taxonomy_version="1.0.0", content_hash=sha256_text(content),
    )  # fmt: skip
    base.update(kw)
    return DatasetDocument(**base)


def test_dataset_document_rejects_hash_mismatch_and_empty_span():
    with pytest.raises(ValueError, match="content_hash"):
        _valid_doc(content_hash="0" * 64)
    with pytest.raises(ValueError, match="non-empty"):
        GoldEvidenceSpan(label="PII", char_start=3, char_end=3, text="")


def test_to_request_hides_ground_truth_and_dataset_bookkeeping():
    doc = _valid_doc(gold_level="HIGHLY_CONFIDENTIAL", tier="T3", family_id="secret_family")
    req = doc.to_request()
    dumped = req.model_dump_json()
    for leaked in ("HIGHLY_CONFIDENTIAL", "T3", "secret_family", "gold_"):
        assert leaked not in dumped
    assert req.document.content == doc.content and req.document.document_id == "d1"


def test_every_authored_family_renders_and_has_exact_spans():
    """Regression net over the real spec: all templates resolve and spans slice correctly."""
    spec = load_spec()
    seen_ids: set[str] = set()
    for fam in spec.families:
        for d in generate_family_docs(fam, spec, "train", spec.dataset.taxonomy_version):
            assert d.doc_id not in seen_ids
            seen_ids.add(d.doc_id)
            for sp in d.gold_evidence_spans:
                assert d.content[sp.char_start : sp.char_end] == sp.text
    assert len(seen_ids) == sum(f.n_docs for f in spec.families)
