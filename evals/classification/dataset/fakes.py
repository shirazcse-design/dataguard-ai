"""Fake-data generators for synthetic documents.

Conventions (see docs/uc4/labeling-guidelines.md, section 6):
* SSN-like values use area 900-999 and group 01-49: never issued as SSNs or ITINs.
* Card numbers are Luhn-valid on public test prefixes. IBANs use a fictional bank code
  (mod-97 valid).
* Emails use reserved `.example` domains; phones use the fictional 555-01xx range.
* Secrets use an obviously fake vendor prefix, or vendor-documented example keys. No provider-format
  live keys and no PEM private-key blocks are generated.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable

from .rng import DetRandom

_BASE62 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_HEX = "0123456789abcdef"
_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip

CARD_PREFIXES = [("411111", 16), ("555555", 16), ("378282", 15), ("601111", 16), ("352800", 16)]
ICD10 = ["E11.9", "I10", "J45.909", "F32.1", "M54.5", "N18.3", "C50.911", "K21.9", "G43.909"]
AWS_EXAMPLE_KEY = "AKIAIOSFODNN7EXAMPLE"  # documented example value (not a real credential)
AWS_EXAMPLE_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"  # documented example value


def digits(rng: DetRandom, n: int) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def luhn_valid(number: str) -> bool:
    total, alt = 0, False
    for ch in reversed(number):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _luhn_check_digit(partial: str) -> str:
    for d in range(10):
        if luhn_valid(partial + str(d)):
            return str(d)
    raise AssertionError("unreachable: a Luhn check digit always exists")


def make_card(rng: DetRandom, fmt: str = "") -> str:
    prefix, length = rng.choice(CARD_PREFIXES)
    body = prefix + digits(rng, length - len(prefix) - 1)
    number = body + _luhn_check_digit(body)
    fmt = fmt or rng.choice(["plain", "spaces", "dashes"])
    if fmt == "plain":
        return number
    sep = " " if fmt == "spaces" else "-"
    groups = [4, 6, 5] if length == 15 else [4, 4, 4, 4]
    out, i = [], 0
    for g in groups:
        out.append(number[i : i + g])
        i += g
    return sep.join(out)


def make_tracking16(rng: DetRandom) -> str:
    """Sixteen digits that FAIL the Luhn check (look like a card number but are not)."""
    while True:
        n = digits(rng, 16)
        if n[0] != "0" and not luhn_valid(n):
            return n


def make_ssn(rng: DetRandom) -> str:
    return f"{rng.randint(900, 999)}-{rng.randint(1, 49):02d}-{rng.randint(1, 9999):04d}"


def _iban_check(country: str, bban: str) -> str:
    rearranged = bban + country + "00"
    numeric = "".join(str(int(c, 36)) for c in rearranged)
    return f"{98 - int(numeric) % 97:02d}"


def make_iban(rng: DetRandom) -> str:
    bban = "DGFK" + digits(rng, 6) + digits(rng, 8)
    iban = "GB" + _iban_check("GB", bban) + bban
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


def make_routing(rng: DetRandom) -> str:
    d = [rng.randint(0, 9) for _ in range(8)]
    total = 3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5])
    d9 = (10 - total % 10) % 10
    return "".join(map(str, [*d, d9]))


def _fmt_date(y: int, m: int, d: int, style: str) -> str:
    if style == "us":
        return f"{m:02d}/{d:02d}/{y}"
    if style == "long":
        return f"{_MONTHS[m - 1]} {d}, {y}"
    return f"{y}-{m:02d}-{d:02d}"


def make_date(rng: DetRandom, arg: str = "") -> str:
    style, _, rest = arg.partition(":")
    lo, hi = (2025, 2027) if not rest else (int(rest.split("-")[0]), int(rest.split("-")[1]))
    return _fmt_date(rng.randint(lo, hi), rng.randint(1, 12), rng.randint(1, 28), style or "iso")


def make_dob(rng: DetRandom, arg: str = "") -> str:
    return _fmt_date(rng.randint(1950, 2004), rng.randint(1, 12), rng.randint(1, 28), arg or "iso")


def _range(arg: str, default: tuple[int, int]) -> tuple[int, int]:
    if not arg:
        return default
    lo, _, hi = arg.partition("-")
    return int(lo), int(hi)


def make_usd(rng: DetRandom, arg: str = "") -> str:
    lo, hi = _range(arg, (1_000, 100_000))
    return f"${rng.randint(lo, hi):,}"


def make_usdm(rng: DetRandom, arg: str = "") -> str:
    """Millions with one decimal, e.g. $42.5M; a billion or more renders as $1.9B.

    The argument is a range in tenths of millions.
    """
    lo, hi = _range(arg, (10, 900))
    millions = rng.randint(lo, hi) / 10
    if millions >= 1000:
        return f"${millions / 1000:.1f}B"
    return f"${millions:.1f}M"


def make_usdb(rng: DetRandom, arg: str = "") -> str:
    lo, hi = _range(arg, (5, 40))
    return f"${rng.randint(lo, hi) / 10:.1f}B"


def make_pct(rng: DetRandom, arg: str = "") -> str:
    lo, hi = _range(arg, (1, 60))
    return f"{rng.randint(lo * 10, hi * 10) / 10:.1f}%"


def make_int(rng: DetRandom, arg: str = "") -> str:
    lo, hi = _range(arg, (1, 999))
    return str(rng.randint(lo, hi))


def make_jwt(rng: DetRandom) -> str:
    def b64(obj: object) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    header = b64({"alg": "HS256", "typ": "JWT"})
    payload = b64({"sub": f"svc-{digits(rng, 4)}", "dg": "synthetic", "exp": 1_900_000_000})
    sig = "".join(rng.choice(_BASE62) for _ in range(43))
    return f"{header}.{payload}.{sig}"


def make_password(rng: DetRandom) -> str:
    words = [
        "Copper",
        "Falcon",
        "Lantern",
        "Harbor",
        "Cinder",
        "Meadow",
        "Quartz",
        "Willow",
        "Ember",
    ]
    sym = rng.choice(["!", "#", "$", "%", "&", "*"])
    return f"{rng.choice(words)}{sym}{rng.choice(words)}{rng.randint(10, 99)}"


def make_phone(rng: DetRandom) -> str:
    return f"({rng.randint(201, 989)}) 555-01{rng.randint(0, 99):02d}"


def make_internal_host(rng: DetRandom) -> str:
    svc = rng.choice(["db", "api", "cache", "queue", "auth", "billing", "etl", "search"])
    return f"{svc}-{rng.randint(1, 24):02d}.corp.example"


Gen = Callable[[DetRandom, str], str]

GENERATORS: dict[str, Gen] = {
    "ssn": lambda r, a: make_ssn(r),
    "ssn_masked": lambda r, a: f"XXX-XX-{r.randint(1, 9999):04d}",
    "card": lambda r, a: make_card(r, a),
    "card_masked": lambda r, a: f"**** **** **** {r.randint(0, 9999):04d}",
    "tracking16": lambda r, a: make_tracking16(r),
    "cvv": lambda r, a: digits(r, 3),
    "expiry": lambda r, a: f"{r.randint(1, 12):02d}/{r.randint(27, 31)}",
    "iban": lambda r, a: make_iban(r),
    "routing": lambda r, a: make_routing(r),
    "acct": lambda r, a: digits(r, r.randint(10, 12)),
    "passport": lambda r, a: r.choice(list("XYZQ")) + digits(r, 8),
    "phone": lambda r, a: make_phone(r),
    "dob": lambda r, a: make_dob(r, a),
    "date": lambda r, a: make_date(r, a),
    "year": lambda r, a: str(r.randint(2026, 2028)),
    "usd": lambda r, a: make_usd(r, a),
    "usdm": lambda r, a: make_usdm(r, a),
    "usdb": lambda r, a: make_usdb(r, a),
    "pct": lambda r, a: make_pct(r, a),
    "int": lambda r, a: make_int(r, a),
    "mrn": lambda r, a: "MRN-" + digits(r, 7),
    "icd": lambda r, a: r.choice(ICD10),
    "claim": lambda r, a: "CLM-" + digits(r, 8),
    "apikey": lambda r, a: "dgsk_live_" + "".join(r.choice(_BASE62) for _ in range(24)),
    "token": lambda r, a: "dgtok_" + "".join(r.choice(_HEX) for _ in range(32)),
    "jwt": lambda r, a: make_jwt(r),
    "password": lambda r, a: make_password(r),
    "aws_example_key": lambda r, a: AWS_EXAMPLE_KEY,
    "aws_example_secret": lambda r, a: AWS_EXAMPLE_SECRET,
    "hex": lambda r, a: "".join(r.choice(_HEX) for _ in range(int(a or 16))),
    "alnum": lambda r, a: "".join(r.choice(_BASE62) for _ in range(int(a or 12))),
    "uuid": lambda r, a: "-".join(
        "".join(r.choice(_HEX) for _ in range(n)) for n in (8, 4, 4, 4, 12)
    ),
    "host": lambda r, a: make_internal_host(r),
    "ip_internal": lambda r, a: f"10.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(1, 254)}",
    "ip_doc": lambda r, a: f"203.0.113.{r.randint(1, 254)}",
    "invoice": lambda r, a: f"INV-{r.randint(2025, 2027)}-{r.randint(1, 9999):04d}",
    "ticket": lambda r, a: f"TCK-{r.randint(10000, 99999)}",
    "po": lambda r, a: f"PO-{r.randint(100000, 999999)}",
    "empid": lambda r, a: f"E{r.randint(10000, 99999)}",
}
