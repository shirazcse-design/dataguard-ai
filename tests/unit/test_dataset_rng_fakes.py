"""Deterministic RNG and fake-data generators."""

from __future__ import annotations

import base64
import json
import re

import pytest

from evals.classification.dataset import fakes
from evals.classification.dataset.rng import DetRandom, seed_from


def test_seed_and_sequence_are_pinned_across_python_versions():
    """Golden values: if these change, generated datasets would silently change too."""
    assert seed_from("a", 1) == 3110932526201151071
    r = DetRandom("golden")
    assert [r.randint(0, 1000) for _ in range(6)] == [330, 247, 831, 375, 792, 517]


def test_same_seed_same_stream_different_seed_differs():
    a, b, c = DetRandom("x", 1), DetRandom("x", 1), DetRandom("x", 2)
    seq_a = [a.random() for _ in range(20)]
    assert seq_a == [b.random() for _ in range(20)]
    assert seq_a != [c.random() for _ in range(20)]


def test_randint_is_inclusive_and_covers_range():
    r = DetRandom("range")
    values = {r.randint(3, 7) for _ in range(500)}
    assert values == {3, 4, 5, 6, 7}
    assert DetRandom("one").randint(5, 5) == 5
    with pytest.raises(ValueError):
        DetRandom("bad").randint(2, 1)


def test_choice_shuffle_sample():
    r = DetRandom("ops")
    items = list(range(10))
    r.shuffle(items)
    assert sorted(items) == list(range(10))
    assert items != list(range(10))
    sample = r.sample(range(50), 8)
    assert len(sample) == len(set(sample)) == 8
    assert r.choice(["a"]) == "a"
    with pytest.raises(ValueError):
        r.choice([])
    with pytest.raises(ValueError):
        r.sample([1, 2], 3)


def test_chance_extremes():
    r = DetRandom("p")
    assert not any(r.chance(0.0) for _ in range(100))
    assert all(r.chance(1.0) for _ in range(100))


@pytest.fixture()
def rng():
    return DetRandom("fakes")


def test_cards_are_luhn_valid_in_every_format(rng):
    for fmt in ("plain", "spaces", "dashes", ""):
        for _ in range(60):
            digits = re.sub(r"\D", "", fakes.make_card(rng, fmt))
            assert fakes.luhn_valid(digits) and len(digits) in (15, 16)


def test_luhn_reference_values():
    assert fakes.luhn_valid("4111111111111111")  # well-known public test PAN
    assert not fakes.luhn_valid("4111111111111112")


def test_tracking16_looks_like_a_card_but_fails_luhn(rng):
    for _ in range(100):
        n = fakes.make_tracking16(rng)
        assert len(n) == 16 and n.isdigit() and not fakes.luhn_valid(n)


def test_ssn_like_values_are_never_issuable(rng):
    for _ in range(300):
        area, group, serial = fakes.make_ssn(rng).split("-")
        assert 900 <= int(area) <= 999 and 1 <= int(group) <= 49 and int(serial) >= 1


def test_iban_uses_fictional_bank_and_is_mod97_valid(rng):
    for _ in range(50):
        iban = fakes.make_iban(rng).replace(" ", "")
        assert iban.startswith("GB") and iban[4:8] == "DGFK" and len(iban) == 22
        numeric = "".join(str(int(c, 36)) for c in iban[4:] + iban[:4])
        assert int(numeric) % 97 == 1


def test_routing_number_checksum(rng):
    for _ in range(100):
        d = [int(c) for c in fakes.make_routing(rng)]
        assert len(d) == 9
        assert (
            3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])
        ) % 10 == 0


def test_phone_uses_fictional_555_01xx_range(rng):
    for _ in range(100):
        assert re.fullmatch(r"\(\d{3}\) 555-01\d{2}", fakes.make_phone(rng))


def test_secrets_use_obviously_fake_prefixes_and_no_pem(rng):
    key = fakes.GENERATORS["apikey"](rng, "")
    tok = fakes.GENERATORS["token"](rng, "")
    assert key.startswith("dgsk_live_") and len(key) == len("dgsk_live_") + 24
    assert tok.startswith("dgtok_") and len(tok) == 6 + 32
    header, payload, sig = fakes.make_jwt(rng).split(".")
    assert json.loads(base64.urlsafe_b64decode(header + "==")) == {"alg": "HS256", "typ": "JWT"}
    assert json.loads(base64.urlsafe_b64decode(payload + "=="))["dg"] == "synthetic"
    assert len(sig) == 43
    for name, gen in fakes.GENERATORS.items():
        assert "BEGIN" not in gen(rng, "") or name == "x", f"{name} must not emit PEM blocks"


def test_documented_example_keys_are_the_vendor_examples():
    assert fakes.AWS_EXAMPLE_KEY == "AKIAIOSFODNN7EXAMPLE"
    assert fakes.AWS_EXAMPLE_SECRET.endswith("EXAMPLEKEY")


def test_dates_money_and_ranges(rng):
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", fakes.make_date(rng))
    assert re.fullmatch(r"\d{2}/\d{2}/\d{4}", fakes.make_date(rng, "us"))
    assert re.fullmatch(r"[A-Z][a-z]+ \d{1,2}, \d{4}", fakes.make_date(rng, "long"))
    assert re.fullmatch(r"\$\d{1,3}(,\d{3})*", fakes.make_usd(rng, "1000-9000000"))
    assert re.fullmatch(r"\$\d+\.\dM", fakes.make_usdm(rng))
    assert re.fullmatch(r"\$\d+\.\dB", fakes.make_usdb(rng))
    for _ in range(200):
        assert 5 <= int(fakes.make_int(rng, "5-9")) <= 9
        assert 1950 <= int(fakes.make_dob(rng)[:4]) <= 2004


def test_generators_are_deterministic():
    a = [gen(DetRandom("det"), "") for gen in fakes.GENERATORS.values()]
    b = [gen(DetRandom("det"), "") for gen in fakes.GENERATORS.values()]
    assert a == b
