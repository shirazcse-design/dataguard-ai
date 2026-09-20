"""The input guard: configuration and decision order."""

from __future__ import annotations

import pytest

from app.classification.config_loader import ConfigError
from app.classification.schemas import Document
from guardrails.input import InputGuardConfig, check_document, load_input_guard_config

CFG = InputGuardConfig(
    guardrail_version="1.0.0",
    min_non_space_chars=1,
    soft_max_bytes=2000,
    hard_max_bytes=5000,
    max_replacement_char_ratio=0.3,
)


def doc(content, size=None):
    return Document(content=content, filename="a.txt", extension="txt", size_bytes=size)


def test_real_input_guard_config_loads():
    cfg, sha = load_input_guard_config()
    assert (
        len(sha) == 64 and cfg.hard_max_bytes >= cfg.soft_max_bytes and cfg.min_non_space_chars >= 1
    )


@pytest.mark.parametrize("old, new", [("hard_max_bytes: 5000000", "hard_max_bytes: 100"), ("guardrail_version: 1.0.0", "guardrail_version: x"),
                                      ("min_non_space_chars: 1", "min_non_space_chars: 0"), ("max_replacement_char_ratio: 0.3", "max_replacement_char_ratio: 2")])  # fmt: skip
def test_invalid_input_guard_config_is_refused(config_copy, old, new):
    p = config_copy / "guardrails/input.v1.yaml"
    p.write_text(p.read_text().replace(old, new, 1))
    with pytest.raises(ConfigError):
        load_input_guard_config(config_copy)


def test_decisions_and_their_order():
    assert check_document(doc("Normal text here."), CFG).ok
    assert check_document(doc(""), CFG).reason == "empty_content"
    assert check_document(doc(" \n\t "), CFG).reason == "empty_content"
    assert check_document(doc("x" * 5001), CFG).reason == "oversize"
    v = check_document(doc("x" * 2500), CFG)
    assert v.ok and v.truncate and v.event().type == "input_truncation"
    assert check_document(doc("x" * 5000), CFG).ok  # the limit itself is allowed
    assert (
        check_document(doc("\ud800" + "x" * 6000, size=1), CFG).reason == "undecodable_text"
    )  # undecodable wins over size
    assert check_document(doc("�" * 40 + "ok"), CFG).reason == "undecodable_text"
    assert check_document(
        doc("�" * 3 + "a" * 97), CFG
    ).ok  # a few replacement characters are tolerated


def test_events_never_carry_the_text():
    ev = check_document(doc("secret"), CFG.model_copy(update={"min_non_space_chars": 50})).event()
    assert ev.type == "input_rejected" and "secret" not in ev.model_dump_json()
    assert check_document(doc("fine"), CFG).event() is None
