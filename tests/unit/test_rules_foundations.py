"""Rules foundations: validators, masking, config, preprocessing and context."""

from __future__ import annotations

import base64
import json
import shutil

import pytest

from app.classification.config_loader import ConfigError, default_config_dir
from app.classification.schemas import Document
from rules.config import load_rules_config
from rules.context import ContextMatcher, term_regex
from rules.masking import excerpt_hash, mask_full, mask_keep_tail, phrase
from rules.preprocess import prepare
from rules.types import demote, stronger
from rules.validators import (
    aba_valid,
    card_network,
    char_classes,
    iban_valid,
    jwt_header_ok,
    luhn_valid,
    shannon_entropy,
    ssn_structure_valid,
)


# ---- validators -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("n", "ok"),
    [
        ("4111111111111111", True),
        ("4111111111111112", False),
        ("79927398713", True),
        ("abc", False),
        ("", False),
    ],
)
def test_luhn(n, ok):
    assert luhn_valid(n) is ok


@pytest.mark.parametrize(
    ("digits", "network"),
    [("4111111111111111", "visa"), ("5555555555554444", "mastercard"), ("2221000000000009", "mastercard"),
     ("378282246310005", "amex"), ("6011111111111117", "discover"), ("3530111333300000", "jcb"),
     ("1234567812345678", None), ("411111111111111", None), ("9999999999999999", None)],
)  # fmt: skip
def test_card_network_prefix_and_length(digits, network):
    assert card_network(digits) == network


def test_iban_validation():
    assert iban_valid("GB82WEST12345698765432") == (True, True)  # the documented example IBAN
    assert iban_valid("GB83WEST12345698765432") == (False, True)  # bad check digits
    assert (
        iban_valid("GB82WEST1234569876543") == (True, False)
        or iban_valid("GB82WEST1234569876543")[0] is False
    )
    assert iban_valid("not an iban") == (False, False)


def test_aba_routing_checksum():
    assert aba_valid("021000021") and aba_valid("011000015")
    assert not aba_valid("021000022") and not aba_valid("12345") and not aba_valid("abcdefghi")


def test_ssn_structure_is_shape_only_not_an_issuance_range_check():
    assert ssn_structure_valid("912", "34", "5678")  # 9xx areas are accepted on purpose
    assert not ssn_structure_valid("000", "12", "3456") and not ssn_structure_valid(
        "666", "12", "3456"
    )
    assert not ssn_structure_valid("123", "00", "3456") and not ssn_structure_valid(
        "123", "45", "0000"
    )


def test_entropy_and_char_classes():
    assert shannon_entropy("") == 0.0 and shannon_entropy("aaaa") == 0.0
    assert shannon_entropy("abcd") == pytest.approx(2.0)
    assert char_classes("abc") == 1 and char_classes("Abc1") == 3 and char_classes("Ab1!") == 4


def test_jwt_header_validation():
    head = base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).decode().rstrip("=")
    assert jwt_header_ok(f"{head}.payload.sig")
    bad = base64.urlsafe_b64encode(b'{"x":1}').decode().rstrip("=")
    assert (
        not jwt_header_ok(f"{bad}.p.s") and not jwt_header_ok("!!!.p.s") and not jwt_header_ok("")
    )


# ---- masking --------------------------------------------------------------------------------
def test_masking_never_exposes_the_value():
    assert mask_keep_tail("4111 1111 1111 1234", 4, "PAN:") == "PAN:**** **** **** 1234"
    assert mask_keep_tail("912-34-5678", 2) == "***-**-**78"
    m = mask_full("Copper!Falcon84", "SECRET")
    assert "Copper" not in m and "len=15" in m
    assert phrase("a  b\nc" * 50).endswith("…") and len(phrase("x" * 500)) == 60


def test_excerpt_hash_is_stable_and_not_the_value():
    assert excerpt_hash("secret") == excerpt_hash("secret") != excerpt_hash("Secret")
    assert "secret" not in excerpt_hash("secret")


def test_strength_helpers():
    assert (
        stronger("weak", "strong") == "strong" and stronger("definitive", "strong") == "definitive"
    )
    assert (
        demote("definitive") == "strong" and demote("strong") == "weak" and demote("weak") is None
    )


# ---- config ---------------------------------------------------------------------------------
def test_real_rules_config_loads_and_states_the_default(bundle):
    cfg, digest = load_rules_config(bundle.policy)
    assert cfg.standalone_default_level == "INTERNAL" and len(digest) == 64
    assert cfg.emit_min("PII") == "strong"
    text = (default_config_dir() / "rules/rules.v1.yaml").read_text()
    assert "NO RULE MATCH DOES NOT MEAN THE DOCUMENT IS PUBLIC" in text


def _copy_config(tmp_path):
    dst = tmp_path / "config"
    shutil.copytree(default_config_dir(), dst)
    return dst


def _edit(path, old, new):
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("standalone_default_level: INTERNAL", "standalone_default_level: SECRET", "unknown standalone_default_level"),
        ("taxonomy_version: 1.0.0", "taxonomy_version: 9.9.9", "taxonomy"),
        ("  default: strong", "  default: certain", "unknown strength"),
        ("emit_min_strength:\n  default: strong\n  overrides: {}", "emit_min_strength:\n  default: strong\n  overrides: {NOPE: weak}", "unknown category"),
        ("    CONFIDENTIAL: [confidential", "    SECRET_LEVEL: [confidential", "unknown level"),
    ],
)  # fmt: skip
def test_rules_config_rejects_inconsistent_values(bundle, tmp_path, old, new, message):
    d = _copy_config(tmp_path)
    _edit(d / "rules/rules.v1.yaml", old, new)
    with pytest.raises(ConfigError, match=message):
        load_rules_config(bundle.policy, d)


def test_rules_config_requires_every_lexicon_and_no_empty_ones(bundle, tmp_path):
    d = _copy_config(tmp_path)
    _edit(d / "rules/rules.v1.yaml", "  ts_markers: [", "  renamed_ts_markers: [")
    with pytest.raises(ConfigError, match="missing lexicons"):
        load_rules_config(bundle.policy, d)
    d2 = _copy_config(tmp_path / "b")
    _edit(
        d2 / "rules/rules.v1.yaml",
        '  ts_markers: ["trade secret", "trade-secret"]',
        "  ts_markers: []",
    )
    with pytest.raises(ConfigError, match="empty lexicons"):
        load_rules_config(bundle.policy, d2)


def test_every_lexicon_referenced_by_a_detector_exists_and_none_is_unused(bundle):
    import pathlib
    import re

    cfg, _ = load_rules_config(bundle.policy)
    used = set()
    for p in pathlib.Path("rules").rglob("*.py"):
        text = p.read_text()
        used |= set(re.findall(r'(?:near_positive|distinct_terms)\([^)]*?"([a-z_]+)"', text))
        used |= set(re.findall(r'lexicons\["([a-z_]+)"\]', text))
    assert used <= set(cfg.lexicons), sorted(used - set(cfg.lexicons))
    assert set(cfg.lexicons) <= used, sorted(set(cfg.lexicons) - used)


# ---- preprocessing and context ---------------------------------------------------------------
def doc(text, filename="x.txt"):
    return Document(content=text, filename=filename, extension=filename.rsplit(".", 1)[-1])


def test_line_index_and_bounds(bundle):
    cfg, _ = load_rules_config(bundle.policy)
    d = prepare(doc("aa\nbbb\ncc"), cfg.max_content_chars)
    assert [d.line_of(p) for p in (0, 2, 3, 7)] == [1, 1, 2, 3]
    assert d.line_bounds(4) == (3, 6) and d.first_lines(2) == ["aa", "bbb"]


def test_table_header_is_detected_only_for_delimited_first_lines(bundle):
    cfg, _ = load_rules_config(bundle.policy)
    assert prepare(doc("name,ssn,phone\nA,1,2"), 1000).header_line == "name,ssn,phone"
    assert prepare(doc("just a sentence, with one comma\nnext"), 1000).header_line == ""
    assert prepare(doc("| a | b | c |\n|---|---|---|"), 1000).header_line.startswith("| a")


def test_oversize_input_is_truncated_and_flagged(bundle):
    d = prepare(doc("x" * 500), 100)
    assert d.truncated and len(d.text) == 100 and not prepare(doc("x" * 50), 100).truncated


def test_term_matching_uses_boundaries_inflections_and_ignores_reserved_domains():
    rx = term_regex(["example", "acquire", "e.g."])
    assert (
        rx.search("an example here")
        and rx.search("they acquired it")
        and rx.search("this, e.g. that")
    )
    assert not rx.search("counterexample") and not rx.search("user@example.com")
    assert not rx.search("person@corp.example"), (
        "the reserved .example TLD is not the word 'example'"
    )


def test_positive_context_same_line_header_and_never_other_lines(bundle):
    cfg, _ = load_rules_config(bundle.policy)
    m = ContextMatcher(cfg)

    def ctx(text, header=True):
        d = prepare(doc(text), 10000)
        pos = d.text.index("912")
        return m.near_positive(d, pos, pos + 11, "pii_id_keywords", header=header)

    assert ctx("SSN: 912-34-5678") == "ssn"  # same line
    assert ctx("name,ssn,note\nAna,912-34-5678,x\n") == "header:ssn"  # tabular header row
    assert ctx("name,ssn,note\nAna,912-34-5678,x\n", header=False) is None
    assert (
        ctx("SSN mentioned here\n912-34-5678") is None
    )  # a keyword on ANOTHER line does not count


def test_negative_context_window_is_same_line_only(bundle):
    cfg, _ = load_rules_config(bundle.policy)
    m = ContextMatcher(cfg)
    d = prepare(doc("this is a sample value 123\nreal value 456"), 10000)
    a, b = d.text.index("123"), d.text.index("456")
    assert m.near_negative(d, a, a + 3, ("placeholder",)) == "placeholder:sample"
    assert m.near_negative(d, b, b + 3, ("placeholder",)) is None


def test_document_level_negative_context_from_header_and_filename(bundle):
    cfg, _ = load_rules_config(bundle.policy)
    m = ContextMatcher(cfg)
    assert m.doc_negative(
        prepare(doc("# TEST FIXTURES - fully synthetic data\nrow"), 10000)
    ).startswith("header_test")
    assert (
        m.doc_negative(prepare(doc("ordinary text", "sample_data.csv"), 10000)) == "filename:sample"
    )
    assert m.doc_negative(prepare(doc("ordinary text", "report.csv"), 10000)) is None
