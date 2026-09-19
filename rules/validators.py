"""Structural validators (checksums and shape checks). Pure functions, no I/O."""

from __future__ import annotations

import base64
import json
import math
import re
from collections import Counter

# ---- payment cards ----------------------------------------------------------------------------
_CARD_NETWORKS: list[tuple[str, re.Pattern[str], tuple[int, ...]]] = [
    ("visa", re.compile(r"^4"), (13, 16, 19)),
    ("mastercard", re.compile(r"^(5[1-5]|22[2-9]|2[3-6]\d|27[01]|2720)"), (16,)),
    ("amex", re.compile(r"^3[47]"), (15,)),
    ("discover", re.compile(r"^(6011|65|64[4-9])"), (16, 17, 18, 19)),
    ("jcb", re.compile(r"^35(2[89]|[3-8]\d)"), (16, 17, 18, 19)),
    ("diners", re.compile(r"^(30[0-5]|36|38|39)"), (14, 15, 16, 17, 18, 19)),
    ("unionpay", re.compile(r"^62"), (16, 17, 18, 19)),
]


def luhn_valid(digits: str) -> bool:
    if not digits.isdigit():
        return False
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def card_network(digits: str) -> str | None:
    """Network name if the prefix and length are plausible for a payment card, else None."""
    for name, prefix, lengths in _CARD_NETWORKS:
        if prefix.match(digits) and len(digits) in lengths:
            return name
    return None


# ---- IBAN -------------------------------------------------------------------------------------
IBAN_LENGTHS = {
    "AD": 24,
    "AT": 20,
    "BE": 16,
    "BG": 22,
    "CH": 21,
    "CY": 28,
    "CZ": 24,
    "DE": 22,
    "DK": 18,
    "EE": 20,
    "ES": 24,
    "FI": 18,
    "FR": 27,
    "GB": 22,
    "GR": 27,
    "HR": 21,
    "HU": 28,
    "IE": 22,
    "IS": 26,
    "IT": 27,
    "LI": 21,
    "LT": 20,
    "LU": 20,
    "LV": 21,
    "MC": 27,
    "MT": 31,
    "NL": 18,
    "NO": 15,
    "PL": 28,
    "PT": 25,
    "RO": 24,
    "SE": 24,
    "SI": 19,
    "SK": 24,
}


def iban_valid(compact: str) -> tuple[bool, bool]:
    """Returns (mod-97 valid, country length known and matching)."""
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact):
        return False, False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(c, 36)) for c in rearranged)
    valid = int(numeric) % 97 == 1
    expected = IBAN_LENGTHS.get(compact[:2])
    return valid, expected is not None and expected == len(compact)


# ---- US bank routing (ABA) --------------------------------------------------------------------
def aba_valid(digits: str) -> bool:
    if not (digits.isdigit() and len(digits) == 9):
        return False
    d = [int(c) for c in digits]
    return (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 == 0


# ---- SSN-shaped values ------------------------------------------------------------------------
def ssn_structure_valid(area: str, group: str, serial: str) -> bool:
    """Shape check only: NOT an issuance-range check (synthetic values use 9xx areas)."""
    return area not in ("000", "666") and group != "00" and serial != "0000"


# ---- secrets ----------------------------------------------------------------------------------
def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def char_classes(value: str) -> int:
    return sum(
        [
            any(c.islower() for c in value),
            any(c.isupper() for c in value),
            any(c.isdigit() for c in value),
            any(not c.isalnum() for c in value),
        ]
    )


def jwt_header_ok(token: str) -> bool:
    """True if the first segment base64url-decodes to a JSON object with an `alg` field."""
    head = token.split(".", 1)[0]
    try:
        raw = base64.urlsafe_b64decode(head + "=" * (-len(head) % 4))
        obj = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return False
    return isinstance(obj, dict) and "alg" in obj
