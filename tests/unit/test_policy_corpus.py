"""UC6 corpus ingestion: metadata, section chunking, deterministic ids, malformed-file refusal."""

from __future__ import annotations

import pytest

from app.policy.config import ChunkingConfig, load_policy_config
from app.policy.corpus import CorpusError, chunk_document, load_corpus

CFG = ChunkingConfig(max_words=180, overlap_words=30, contextual_header=True)

DOC = """---
policy_id: POL-TST
title: Test Policy
version: "1.0"
effective_date: "2025-01-01"
status: current
supersedes: null
policy_owner: Tester
source: synthetic/test
---

# Test Policy

## 1. Purpose

Why this exists.

## 2. Rules

### 2.1 First rule

Do the first thing.
"""


def _write(tmp_path, text, name="p.md"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_real_corpus_loads_with_metadata_on_every_chunk():
    cfg, _ = load_policy_config()
    corpus = load_corpus(cfg.corpus.dir, cfg.chunking)
    assert len(corpus.docs) == 10
    assert {d.policy_id for d in corpus.docs} >= {
        "POL-CLS", "POL-DLP", "POL-AUP", "POL-ACC", "POL-TPS", "POL-RET", "POL-IR", "POL-SEC",
    }  # fmt: skip
    for c in corpus.chunks:
        assert c.policy_id and c.title and c.version and c.effective_date and c.section
        assert c.policy_owner and c.source and c.body
    assert {c.status for c in corpus.chunks} == {"current", "superseded", "draft"}


def test_two_retention_versions_coexist_and_the_newer_supersedes():
    cfg, _ = load_policy_config()
    corpus = load_corpus(cfg.corpus.dir, cfg.chunking)
    versions = corpus.versions_of("POL-RET")
    assert [v.version for v in versions] == ["1.0", "2.0"]
    assert versions[1].supersedes == "1.0" and versions[0].status == "superseded"
    assert corpus.by_id["POL-RET@1.0#2.1"].citation == "POL-RET v1.0 §2.1"
    assert corpus.by_id["POL-RET@2.0#2.1"].citation == "POL-RET §2.1"


def test_sections_become_chunks_and_headings_without_text_do_not(tmp_path):
    meta, chunks = chunk_document(_write(tmp_path, DOC), CFG)
    assert meta.policy_id == "POL-TST"
    assert [c.chunk_id for c in chunks] == ["POL-TST@1.0#1", "POL-TST@1.0#2.1"]
    assert chunks[1].heading == "First rule" and chunks[1].body == "Do the first thing."
    assert chunks[1].index_text.startswith("Test Policy §2.1 First rule. ")


def test_contextual_header_can_be_disabled(tmp_path):
    cfg = CFG.model_copy(update={"contextual_header": False})
    _, chunks = chunk_document(_write(tmp_path, DOC), cfg)
    assert all(c.index_text == c.body for c in chunks)


def test_long_sections_split_into_overlapping_parts_with_the_same_section(tmp_path):
    long_body = " ".join(f"w{i}" for i in range(100))
    text = DOC.replace("Do the first thing.", long_body)
    cfg = ChunkingConfig(max_words=40, overlap_words=10, contextual_header=False)
    _, chunks = chunk_document(_write(tmp_path, text), cfg)
    parts = [c for c in chunks if c.section == "2.1"]
    assert [c.chunk_id for c in parts] == [f"POL-TST@1.0#2.1/p{i}" for i in (1, 2, 3)]
    assert parts[0].body.split()[-10:] == parts[1].body.split()[:10]  # overlap
    assert parts[-1].body.split()[-1] == "w99"  # nothing lost
    assert {c.section_key for c in parts} == {"POL-TST@1.0#2.1"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace("---\npolicy_id", "policy_id", 1),  # no front matter
        lambda t: t.replace("policy_id: POL-TST", "policy_id: bad id"),
        lambda t: t.replace('effective_date: "2025-01-01"', 'effective_date: "Jan 2025"'),
        lambda t: t.replace("status: current", "status: approved"),
        lambda t: t.replace("## 2. Rules", "## 1. Rules"),  # duplicate section
    ],
)
def test_malformed_documents_are_refused_not_skipped(tmp_path, mutate):
    with pytest.raises(CorpusError):
        chunk_document(_write(tmp_path, mutate(DOC)), CFG)


def test_duplicate_policy_version_is_refused(tmp_path):
    _write(tmp_path, DOC, "a.md")
    _write(tmp_path, DOC.replace("## 1. Purpose", "## 9. Purpose"), "b.md")
    with pytest.raises(CorpusError):
        load_corpus(tmp_path, CFG)


def test_fingerprint_changes_when_text_changes(tmp_path):
    _write(tmp_path, DOC)
    a = load_corpus(tmp_path, CFG).fingerprint()
    _write(tmp_path, DOC.replace("first thing", "second thing"))
    assert load_corpus(tmp_path, CFG).fingerprint() != a
