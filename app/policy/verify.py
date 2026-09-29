"""Citation verification and deterministic conflict resolution.

Citation verification (per claim, all DETERMINISTIC):
1. The cited evidence id must be one of the labels sent for THIS request; otherwise the citation
   was fabricated (`fabricated_evidence_id`). The harness, not the model, then fills in the policy
   id, section and version from the chunk's metadata.
2. The quote must appear in that chunk's section text, exactly or after collapsing whitespace
   (UC4's `verify_quote`), otherwise `quote_not_in_evidence`.
3. Every number in the claim sentence must appear in the cited section text, otherwise
   `number_not_in_evidence`. This catches the classic grounded-looking hallucination, a correct
   quote with a wrong figure in the sentence ("retained for 10 years" citing a 7-year section).
   It is a heuristic: it cannot judge whether the rest of the sentence follows from the quote,
   which is left to an LLM/human groundedness judge (docs/uc6/evaluation-plan.md).

Conflicts are never resolved by the model:
* Two versions of one policy in the evidence: the version whose metadata says `current` (and
  whose `supersedes` names the other) is authoritative; claims citing the superseded version are
  removed from the answer, and the older text is still shown as superseded evidence.
* A conflict the model reports between different policies: verified to name real evidence from
  at least two policies, then sent to human review. Neither side is preferred.
"""

from __future__ import annotations

import re

from guardrails.output import verify_quote

from .corpus import Corpus
from .generate import ModelOutput
from .schemas import Claim, Conflict, Evidence

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return set(_NUMBER.findall(text))


def verify_claims(
    output: ModelOutput, evidence: list[Evidence], *, max_claims: int, max_quote_chars: int
) -> list[Claim]:
    by_label = {e.evidence_id: e for e in evidence}
    claims: list[Claim] = []
    for n, raw in enumerate(output.claims):
        ev = by_label.get(raw.evidence_id.strip())
        reason = None
        if n >= max_claims:
            reason = "over_claim_limit"
        elif ev is None:
            reason = "fabricated_evidence_id"
        elif len(raw.quote) > max_quote_chars or not verify_quote(raw.quote, ev.body)[0]:
            reason = "quote_not_in_evidence"
        elif not _numbers(raw.text) <= _numbers(ev.body):
            reason = "number_not_in_evidence"
        claims.append(
            Claim(
                text=raw.text.strip(),
                evidence_id=raw.evidence_id,
                chunk_id=ev.chunk_id if ev else None,
                citation=ev.citation if ev else None,
                quote=raw.quote,
                verified=reason is None,
                drop_reason=reason,
            )
        )
    return claims


def version_conflicts(evidence: list[Evidence], corpus: Corpus) -> list[Conflict]:
    """Same policy section present in more than one version: resolved by metadata when exactly one
    of them is `current` and its lineage supersedes the others; otherwise human review."""
    by_section: dict[tuple[str, str], list[Evidence]] = {}
    for e in evidence:
        by_section.setdefault((e.policy_id, e.section), []).append(e)
    out: list[Conflict] = []
    for (policy_id, _), group in sorted(by_section.items()):
        if len({e.version for e in group}) < 2:
            continue
        current = [e for e in group if e.status == "current"]
        versions = {d.version: d for d in corpus.versions_of(policy_id)}
        resolved = len(current) == 1 and all(
            _supersedes(current[0].version, e.version, versions)
            for e in group
            if e is not current[0]
        )
        out.append(
            Conflict(
                kind="version",
                chunk_ids=[e.chunk_id for e in group],
                citations=[e.citation for e in group],
                resolution="resolved_by_metadata" if resolved else "human_review",
                authoritative=current[0].citation if resolved else None,
                note=(
                    f"{current[0].citation} (v{current[0].version}, effective "
                    f"{current[0].effective_date}) supersedes the older version"
                    if resolved
                    else "policy versions disagree and metadata does not establish which applies"
                ),
            )
        )
    return out


def _supersedes(newer: str, older: str, versions: dict) -> bool:
    """Follow the `supersedes` chain from `newer` down to `older`."""
    seen: set[str] = set()
    v = versions.get(newer)
    while v is not None and v.supersedes and v.supersedes not in seen:
        if v.supersedes == older:
            return True
        seen.add(v.supersedes)
        v = versions.get(v.supersedes)
    return False


def model_reported_conflict(output: ModelOutput, evidence: list[Evidence]) -> Conflict | None:
    """The model's CONFLICT, checked against the evidence sent. Returns a cross-policy conflict for
    human review, or None when the ids name fewer than two real policies (a single-policy version
    disagreement is handled by `version_conflicts`, from metadata)."""
    by_label = {e.evidence_id: e for e in evidence}
    named = [by_label[i.strip()] for i in output.conflict_evidence_ids if i.strip() in by_label]
    if len({e.policy_id for e in named}) < 2:
        return None
    named = list({e.chunk_id: e for e in named}.values())
    return Conflict(
        kind="cross_policy",
        chunk_ids=[e.chunk_id for e in named],
        citations=[e.citation for e in named],
        resolution="human_review",
        note="current policies from different owners disagree; no metadata makes one authoritative",
    )
