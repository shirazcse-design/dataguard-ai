"""Template engine for synthetic documents.

Syntax (chosen so code samples containing braces, angle brackets or `%` need no escaping):

* ``«name»``            slot filled from a pool, or a computed slot (``person``, ``company`` ...).
* ``«name#2»``          an independent second draw (distinct from earlier draws of the same slot).
* ``«person.first»``    attribute of a memoised slot (``first``, ``last``, ``email``, ``workemail``,
                        ``domain``, ``short``).
* ``«@ssn»`` / ``«@usd:1000-9000»``  builtin fake-data generator (see `fakes.GENERATORS`).
* ``«a|b|c»``           choose one alternative (empty alternatives allowed: ``«Reminder: |»``).
* ``⟦LABEL§text⟧``      gold evidence span supporting taxonomy id LABEL; markers are removed and
                        character offsets recorded in the final text.

A slot used twice in one document (same key) yields the same value; use ``#n`` for a new draw.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import fakes
from .rng import DetRandom

_TOKEN_RE = re.compile(r"«([^«»]*)»")
_SPAN_RE = re.compile(r"⟦([A-Z][A-Z0-9_]*)§(.*?)⟧", re.DOTALL)
_MAX_PASSES = 25
_REROLLS = 25
_ATTRS = {"first", "last", "email", "workemail", "domain", "short"}
# "Filler" generators draw a FRESH value at every occurrence unless the slot carries an explicit
# tag (e.g. ``«@usd:1-9#a»``), in which case the tagged value is reused. Identity-like generators
# (ssn, card, ...) are always memoised so an entity stays consistent within a document.
FRESH_GENERATORS = {"int", "usd", "usdm", "usdb", "pct", "date"}


class TemplateError(ValueError):
    """Raised for malformed templates or unknown slots."""


@dataclass(frozen=True)
class EvidenceSpan:
    label: str
    char_start: int
    char_end: int
    text: str


@dataclass(frozen=True)
class Rendered:
    text: str
    spans: list[EvidenceSpan]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


class RenderContext:
    def __init__(self, rng: DetRandom, pools: dict[str, list[str]]) -> None:
        self.rng = rng
        self.pools = pools
        self._memo: dict[str, str] = {}
        self._drawn: dict[str, set[str]] = {}

    # -- drawing ------------------------------------------------------------------------------
    def _draw_unique(self, base: str, produce) -> str:
        seen = self._drawn.setdefault(base, set())
        value = produce()
        for _ in range(_REROLLS):
            if value not in seen:
                break
            value = produce()
        seen.add(value)
        return value

    def _produce(self, base: str):
        """Return a zero-arg producer for `base` (a pool name, computed slot, or @generator)."""
        if base.startswith("@"):
            name, _, arg = base[1:].partition(":")
            gen = fakes.GENERATORS.get(name)
            if gen is None:
                raise TemplateError(f"unknown generator @{name}")
            return lambda: gen(self.rng, arg)
        if base == "person":
            return lambda: f"{self._pool('first_name')} {self._pool('last_name')}"
        if base == "first":
            return lambda: self._pool("first_name")
        if base == "last":
            return lambda: self._pool("last_name")
        if base == "company":
            return lambda: f"{self._pool('company_prefix')} {self._pool('company_suffix')}"
        if base == "codename":
            return lambda: f"Project {self._pool('codeword')}"
        if base in self.pools:
            return lambda: self._pool(base)
        raise TemplateError(f"unknown slot «{base}» (no pool, computed slot or generator)")

    def _pool(self, name: str) -> str:
        pool = self.pools.get(name)
        if not pool:
            raise TemplateError(f"unknown or empty pool {name!r}")
        return self.rng.choice(pool)

    # -- resolution ---------------------------------------------------------------------------
    def slot(self, key: str) -> str:
        key = key.strip()
        if key.startswith("@") and "#" not in key and key[1:].partition(":")[0] in FRESH_GENERATORS:
            return self._produce(key)()
        head, dot, attr = key.partition(".") if not key.startswith("@") else (key, "", "")
        if key in self._memo:
            return self._memo[key]
        if dot:
            if attr not in _ATTRS:
                raise TemplateError(f"unknown slot attribute .{attr} in «{key}»")
            value = self._attribute(head, attr)
        else:
            base = head.split("#", 1)[0]
            value = self._draw_unique(base, self._produce(base))
        self._memo[key] = value
        return value

    def _attribute(self, head: str, attr: str) -> str:
        base = head.split("#", 1)[0]
        parent = self.slot(head)
        if base == "person":
            first, _, last = parent.partition(" ")
            if attr == "first":
                return first
            if attr == "last":
                return last
            if attr == "email":
                return f"{_slug(first)}.{_slug(last)}@{self._pool_domain()}"
            if attr == "workemail":
                return f"{_slug(first)}.{_slug(last)}@{self._company_domain()}"
        if base == "company":
            prefix = parent.split(" ", 1)[0]
            if attr == "domain":
                return f"{_slug(parent)}.example"
            if attr == "short":
                return prefix
        raise TemplateError(f"attribute .{attr} is not valid for slot «{head}»")

    def _pool_domain(self) -> str:
        return self._pool("personal_domain")

    def _company_domain(self) -> str:
        return f"{_slug(self.slot('company'))}.example"

    def choose(self, body: str) -> str:
        return self.rng.choice(body.split("|"))


def expand(template: str, ctx: RenderContext) -> str:
    """Expand all `«...»` tokens (innermost first) until none remain."""
    text = template
    for _ in range(_MAX_PASSES):
        if "«" not in text:
            break

        def repl(m: re.Match[str]) -> str:
            body = m.group(1)
            return ctx.choose(body) if "|" in body else ctx.slot(body)

        new = _TOKEN_RE.sub(repl, text)
        if new == text:
            break
        text = new
    if "«" in text or "»" in text:
        raise TemplateError("unbalanced or over-nested «» tokens in template")
    return text


def extract_spans(text: str) -> Rendered:
    """Strip `⟦LABEL§...⟧` markers, returning clean text and offset-correct evidence spans."""
    out: list[str] = []
    spans: list[EvidenceSpan] = []
    pos = 0
    cursor = 0  # length of `out` text so far
    for m in _SPAN_RE.finditer(text):
        before = text[pos : m.start()]
        out.append(before)
        cursor += len(before)
        inner = m.group(2)
        spans.append(EvidenceSpan(m.group(1), cursor, cursor + len(inner), inner))
        out.append(inner)
        cursor += len(inner)
        pos = m.end()
    out.append(text[pos:])
    clean = "".join(out)
    if "⟦" in clean or "⟧" in clean or "§" in clean:
        raise TemplateError("malformed evidence marker (nested, unterminated, or missing label)")
    return Rendered(clean, spans)


def render(template: str, rng: DetRandom, pools: dict[str, list[str]]) -> Rendered:
    """Render one template into text + gold evidence spans."""
    ctx = RenderContext(rng, pools)
    return extract_spans(expand(template, ctx))


def render_many(
    templates: dict[str, str], rng: DetRandom, pools: dict[str, list[str]]
) -> dict[str, Rendered]:
    """Render several named templates in ONE context so shared slots stay consistent
    (e.g. a filename and a body that refer to the same company or person)."""
    ctx = RenderContext(rng, pools)
    return {name: extract_spans(expand(t, ctx)) for name, t in templates.items()}


def referenced_slots(template: str) -> set[str]:
    """Static analysis: the slot keys (not choices) a template references. For validation."""
    keys: set[str] = set()
    for m in re.finditer(r"«([^«»]*)»", template):
        body = m.group(1)
        if "|" not in body:
            keys.add(body.strip())
    return keys
