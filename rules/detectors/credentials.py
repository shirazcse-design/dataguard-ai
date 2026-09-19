"""Credentials / secrets detectors.

The principle is a *literal secret value*: a secret-named key assigned a real-looking literal.
Placeholders, environment lookups, variable references and low-entropy words are not secrets, so
code that merely mentions `password` is not flagged.
"""

from __future__ import annotations

import re

from ..masking import mask_full
from ..types import DetectorOutput
from ..validators import char_classes, jwt_header_ok, shannon_entropy
from .base import Detector, ScanContext

HC = "HIGHLY_CONFIDENTIAL"
_NEG = ("placeholder", "test", "documentation")
_EXACT_PLACEHOLDERS = {"none", "null", "todo"}
_VARIABLE_REF = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")

_PREFIXED = re.compile(
    r"(?<![A-Za-z0-9_])[a-z][a-z0-9]{1,7}_(?:(?:live|test|prod)_)?[A-Za-z0-9]{24,}(?![A-Za-z0-9_])"
)
_JWT = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_BEARER = re.compile(r"(?i)authorization[ \t]*:[ \t]*bearer[ \t]+([A-Za-z0-9._~+/=-]{16,})")
_URL_PW = re.compile(
    r"(?i)(?<![A-Za-z0-9+.-])[a-z][a-z0-9+.-]{1,15}://[^\s:/@]{0,64}:([^\s@/]{4,80})@"
)
_AWS = re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])")
_PEM = re.compile(r"-----BEGIN [A-Z ]{0,20}PRIVATE KEY-----")
_BCRYPT = re.compile(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}")


def _plausible_secret(value: str) -> bool:
    """Character diversity beats raw entropy for human-chosen passwords (repeated words lower
    Shannon entropy although the value is still a plausible password)."""
    classes, entropy = char_classes(value), shannon_entropy(value)
    return (classes >= 3 and entropy >= 2.0) or (classes >= 2 and entropy >= 2.8)


def secret_value_problem(
    value: str, rest_of_line: str, placeholders: list[str], env_lookups: list[str]
) -> str | None:
    """Why `value` is NOT a literal secret (None means it looks like one)."""
    low = value.lower()
    if len(value) < 8:
        return "too_short"
    if low in _EXACT_PLACEHOLDERS or value.startswith(("<", "${", "%{", "{{")):
        return "placeholder"
    if any(p in low for p in placeholders if p not in _EXACT_PLACEHOLDERS):
        return "placeholder"
    rest = rest_of_line.lower()
    if any(t in rest for t in env_lookups):
        return "env_lookup"
    if _VARIABLE_REF.match(value) and not any(ch.isdigit() for ch in value):
        return "variable_reference"
    if not _plausible_secret(value):
        return "low_entropy"
    return None


class SecretAssignment(Detector):
    id, category = "cred.assignment", "CREDENTIALS_SECRETS"

    def setup(self) -> None:
        keys = "|".join(
            re.escape(k)
            for k in sorted(self.cfg.lexicons["secret_key_names"], key=len, reverse=True)
        )
        head = rf"(?<![A-Za-z0-9])([A-Za-z0-9_.-]{{0,30}}(?:{keys})[A-Za-z0-9_.-]{{0,20}})[\"'`]?"
        self._assign = re.compile(
            rf"(?i){head}[ \t]*(?:[:=]|=>|[ \t]+is[ \t]+)[ \t]*[\"'`]?([^\s\"'`,;)}}\]]{{6,120}})"
        )
        self._quoted = re.compile(rf"(?i){head}[ \t]+[`\"']([^\s`\"']{{6,120}})[`\"']")
        exact = "|".join(
            re.escape(k)
            for k in sorted(self.cfg.lexicons["secret_key_names_exact"], key=len, reverse=True)
        )
        self._assign_exact = re.compile(
            rf"(?i)(?<![A-Za-z0-9_.-])({exact})[\"'`]?[ \t]*(?:[:=]|=>)[ \t]*[\"'`]?"
            rf"([^\s\"'`,;)}}\]]{{6,120}})"
        )

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        ph = c.cfg.lexicons["placeholder_values"]
        env = c.cfg.lexicons["env_lookups"]
        doc_neg = m.doc_negative(d)
        seen: set[int] = set()
        for pattern in (self._assign, self._quoted, self._assign_exact):
            for hit in pattern.finditer(d.text):
                s, e = hit.start(2), hit.end(2)
                if s in seen:
                    continue
                seen.add(s)
                value = hit.group(2).rstrip(".")
                line_start, line_end = d.line_bounds(s)
                problem = secret_value_problem(value, d.text[line_start:line_end], ph, env)
                if problem:
                    out.suppressions.append(self.suppressed(problem, s, e))
                    continue
                neg = doc_neg or m.near_negative(d, s, e, _NEG)
                if neg:
                    out.suppressions.append(self.suppressed(neg, s, e))
                    continue
                strong_secret = len(value) >= 20 and shannon_entropy(value) >= 3.5
                out.detections.append(
                    self.found(
                        c,
                        strength="definitive" if strong_secret else "strong",
                        kind="pattern_match",
                        start=s,
                        end=e,
                        masked=mask_full(value, "SECRET"),
                        level_hint=HC,
                    )
                )
        return out


class PrefixedToken(Detector):
    """Vendor-style `prefix_<random>` tokens (for example `sk_live_...`)."""

    id, category = "cred.prefixed_token", "CREDENTIALS_SECRETS"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        for hit in _PREFIXED.finditer(d.text):
            s, e = hit.span()
            tok = hit.group(0)
            body = tok.split("_", 1)[1]
            if not (any(ch.isdigit() for ch in body) and any(ch.isalpha() for ch in body)):
                continue
            if shannon_entropy(body) < 3.0:
                continue
            neg = m.doc_negative(d) or m.near_negative(d, s, e, _NEG)
            if neg:
                out.suppressions.append(self.suppressed(neg, s, e))
                continue
            strength = "weak" if "_test_" in tok else "strong"
            out.detections.append(
                self.found(
                    c,
                    strength=strength,
                    kind="pattern_match",
                    start=s,
                    end=e,
                    masked=mask_full(tok, "TOKEN"),
                    level_hint=HC if strength == "strong" else None,
                )
            )
        return out


class Jwt(Detector):
    id, category = "cred.jwt", "CREDENTIALS_SECRETS"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        for hit in _JWT.finditer(d.text):
            s, e = hit.span()
            neg = m.doc_negative(d) or m.near_negative(d, s, e, _NEG)
            if neg:
                out.suppressions.append(self.suppressed(neg, s, e))
                continue
            ok = jwt_header_ok(hit.group(0))
            out.detections.append(
                self.found(
                    c,
                    strength="definitive" if ok else "weak",
                    kind="validator_passed" if ok else "pattern_match",
                    start=s,
                    end=e,
                    masked=mask_full(hit.group(0), "JWT"),
                    level_hint=HC if ok else None,
                )
            )
        return out


class Bearer(Detector):
    id, category = "cred.bearer", "CREDENTIALS_SECRETS"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        ph = c.cfg.lexicons["placeholder_values"]
        for hit in _BEARER.finditer(d.text):
            value = hit.group(1)
            s, e = hit.start(1), hit.end(1)
            if value.startswith("eyJ"):
                continue  # reported by the JWT detector
            if secret_value_problem(value, "", ph, []) in ("placeholder", "too_short"):
                out.suppressions.append(self.suppressed("placeholder", s, e))
                continue
            neg = m.doc_negative(d) or m.near_negative(d, s, e, _NEG)
            if neg:
                out.suppressions.append(self.suppressed(neg, s, e))
                continue
            out.detections.append(
                self.found(
                    c,
                    strength="strong",
                    kind="pattern_match",
                    start=s,
                    end=e,
                    masked=mask_full(value, "BEARER"),
                    level_hint=HC,
                )
            )
        return out


class UrlPassword(Detector):
    id, category = "cred.url_password", "CREDENTIALS_SECRETS"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d, m = DetectorOutput(), c.doc, c.matcher
        ph = c.cfg.lexicons["placeholder_values"]
        for hit in _URL_PW.finditer(d.text):
            value = hit.group(1)
            s, e = hit.start(1), hit.end(1)
            low = value.lower()
            if (
                low in {"password", "pass", "pwd", "secret"}
                or secret_value_problem(value, "", ph, []) == "placeholder"
            ):
                out.suppressions.append(self.suppressed("placeholder", s, e))
                continue
            neg = m.doc_negative(d) or m.near_negative(d, s, e, _NEG)
            if neg:
                out.suppressions.append(self.suppressed(neg, s, e))
                continue
            out.detections.append(
                self.found(
                    c,
                    strength="strong",
                    kind="pattern_match",
                    start=s,
                    end=e,
                    masked=mask_full(value, "URL-PASSWORD"),
                    level_hint=HC,
                )
            )
        return out


class AwsAccessKey(Detector):
    id, category = "cred.aws_access_key", "CREDENTIALS_SECRETS"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d = DetectorOutput(), c.doc
        dummy = set(c.cfg.context.known_dummy.aws_access_key)
        for hit in _AWS.finditer(d.text):
            s, e = hit.span()
            if hit.group(0) in dummy or "EXAMPLE" in hit.group(0):
                out.suppressions.append(self.suppressed("documented_example_key", s, e))
                continue
            out.detections.append(
                self.found(
                    c,
                    strength="definitive",
                    kind="pattern_match",
                    start=s,
                    end=e,
                    masked=mask_full(hit.group(0), "AWS-KEY"),
                    level_hint=HC,
                )
            )
        return out


class PrivateKeyBlock(Detector):
    id, category = "cred.private_key_block", "CREDENTIALS_SECRETS"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d = DetectorOutput(), c.doc
        hit = _PEM.search(d.text)
        if hit:
            out.detections.append(
                self.found(
                    c,
                    strength="definitive",
                    kind="pattern_match",
                    start=hit.start(),
                    end=hit.end(),
                    masked="[PRIVATE-KEY-BLOCK]",
                    level_hint=HC,
                )
            )
        return out


class PasswordHash(Detector):
    """A bcrypt-style hash: credential material, but not a directly usable secret -> weak."""

    id, category = "cred.password_hash", "CREDENTIALS_SECRETS"

    def detect(self, c: ScanContext) -> DetectorOutput:
        out, d = DetectorOutput(), c.doc
        hit = _BCRYPT.search(d.text)
        if hit:
            out.detections.append(
                self.found(
                    c,
                    strength="weak",
                    kind="pattern_match",
                    start=hit.start(),
                    end=hit.end(),
                    masked="[BCRYPT-HASH]",
                )
            )
        return out
