"""Split-integrity, label-consistency and near-duplicate/leakage checks.

Severity policy:
* ERROR   - the dataset is unsound (invalid/contradictory labels, leakage across splits, broken
            evidence spans, missing tiers in the test split). `dataset validate` fails.
* WARNING - a quality/size shortfall that must be *reported* but does not invalidate the data
            (for example a label below the minimum-positives threshold: small-sample flag).
"""

from __future__ import annotations

import re
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.classification.policy import TaxonomyPolicy

from .schema import SPLIT_NAMES, TIERS, DatasetDocument
from .spec import DatasetSpec

MIN_CONTENT_CHARS = 40
MAX_CONTENT_CHARS = 20_000
MIN_LEAK_SPAN_CHARS = 8

# QA lint ONLY (not a classifier): T2 "semantic" documents must contain none of these obvious
# identifiers, otherwise a pattern-based detector could solve them and the tier would be mislabeled.
T2_FORBIDDEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "ssn_like": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "long_digit_run": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "phone_like": re.compile(r"\(\d{3}\)\s?\d{3}-\d{4}"),
    "key_prefix": re.compile(r"\b(?:dgsk_|dgtok_|AKIA)[A-Za-z0-9_]{8,}"),
    "kv_secret": re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S+"),
    "iban_like": re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,}\b"),
    "mrn": re.compile(r"\bMRN-\d+"),
}


@dataclass
class IntegrityReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    small_sample_flags: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "small_sample_flags": self.small_sample_flags,
            "stats": self.stats,
        }


def _shingles(text: str, k: int) -> frozenset[int]:
    tokens = re.findall(r"\w+", text.lower())
    if len(tokens) < k:
        return frozenset({zlib.crc32(" ".join(tokens).encode())})
    return frozenset(
        zlib.crc32(" ".join(tokens[i : i + k]).encode()) for i in range(len(tokens) - k + 1)
    )


def _jaccard(a: frozenset[int], b: frozenset[int]) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def check_document(
    doc: DatasetDocument, policy: TaxonomyPolicy, injection_snippets: list[str]
) -> list[str]:
    """Per-document label/evidence/tier invariants. Returns error strings."""
    errs: list[str] = []
    tag = f"[{doc.family_id}/{doc.doc_id}]"
    levels, categories = set(policy.level_ids), set(policy.category_ids)

    for problem in policy.label_errors(doc.gold_level, doc.gold_categories):
        errs.append(f"{tag} gold label: {problem}")
    if doc.taxonomy_version != policy.taxonomy_version:
        errs.append(f"{tag} taxonomy_version {doc.taxonomy_version} != {policy.taxonomy_version}")
    if not MIN_CONTENT_CHARS <= len(doc.content) <= MAX_CONTENT_CHARS:
        errs.append(
            f"{tag} content length {len(doc.content)} outside "
            f"[{MIN_CONTENT_CHARS}, {MAX_CONTENT_CHARS}]"
        )

    # Ambiguity
    for alt in doc.acceptable_alternative_levels:
        if alt not in levels:
            errs.append(f"{tag} unknown alternative level {alt!r}")
        if alt == doc.gold_level:
            errs.append(f"{tag} alternative level equals the gold level")
    if doc.tier == "T3":
        if not doc.ambiguity_flag:
            errs.append(f"{tag} T3 documents must set ambiguity_flag")
        if not doc.annotation_notes:
            errs.append(f"{tag} T3 documents need annotation_notes explaining the ambiguity")
    elif doc.ambiguity_flag:
        errs.append(f"{tag} ambiguity_flag is only valid for tier T3")
    if doc.acceptable_alternative_levels and not doc.ambiguity_flag:
        errs.append(f"{tag} alternative levels require ambiguity_flag")

    # Hard negatives
    if doc.tier == "T4":
        if not doc.decoy_for:
            errs.append(f"{tag} T4 documents must declare decoy_for")
        for d in doc.decoy_for:
            if d not in levels | categories:
                errs.append(f"{tag} decoy_for has unknown id {d!r}")
            if d in doc.gold_categories or d == doc.gold_level:
                errs.append(f"{tag} gold label contains its own decoy {d!r}")
    elif doc.decoy_for:
        errs.append(f"{tag} decoy_for is only valid for tier T4")

    # Adversarial
    if doc.tier == "T5":
        if not doc.adversarial_type:
            errs.append(f"{tag} T5 documents must set adversarial_type")
        if not any(snippet in doc.content for snippet in injection_snippets):
            errs.append(f"{tag} T5 document contains no known injection snippet")
    elif doc.adversarial_type:
        errs.append(f"{tag} adversarial_type is only valid for tier T5")

    # Evidence spans
    covered: set[str] = set()
    for span in doc.gold_evidence_spans:
        if span.label not in levels | categories:
            errs.append(f"{tag} evidence span has unknown label {span.label!r}")
            continue
        if (
            span.char_end > len(doc.content)
            or doc.content[span.char_start : span.char_end] != span.text
        ):
            errs.append(
                f"{tag} evidence span text does not match "
                f"content[{span.char_start}:{span.char_end}]"
            )
        if span.label in categories:
            if span.label not in doc.gold_categories:
                errs.append(
                    f"{tag} evidence span supports category {span.label} not in gold labels"
                )
            covered.add(span.label)
    for cat in doc.gold_categories:
        if cat not in covered:
            errs.append(f"{tag} gold category {cat} has no evidence span")

    # T2 pattern-free QA lint
    if doc.tier == "T2":
        for name, pat in T2_FORBIDDEN_PATTERNS.items():
            if pat.search(doc.content) or pat.search(doc.filename):
                errs.append(f"{tag} T2 document matches obvious-identifier pattern '{name}'")
    return errs


def check_dataset(
    docs: list[DatasetDocument],
    spec: DatasetSpec,
    policy: TaxonomyPolicy,
    injection_snippets: list[str],
) -> IntegrityReport:
    report = IntegrityReport()
    err, warn = report.errors, report.warnings

    for doc in docs:
        err.extend(check_document(doc, policy, injection_snippets))

    # Identity / duplication
    ids = [d.doc_id for d in docs]
    if len(set(ids)) != len(ids):
        err.append("duplicate doc_id values")
    by_hash: dict[str, list[DatasetDocument]] = defaultdict(list)
    for d in docs:
        by_hash[d.content_hash].append(d)
    for h, group in by_hash.items():
        if len(group) > 1:
            err.append(f"exact duplicate content ({h[:12]}) in {[g.doc_id for g in group][:4]}")

    # Group/family integrity
    group_splits: dict[str, set[str]] = defaultdict(set)
    family_group: dict[str, set[str]] = defaultdict(set)
    for d in docs:
        group_splits[d.group_id].add(d.split)
        family_group[d.family_id].add(d.group_id)
    for g, splits in group_splits.items():
        if len(splits) > 1:
            err.append(f"group {g} spans multiple splits {sorted(splits)} (template leakage)")
    for f, groups in family_group.items():
        if len(groups) > 1:
            err.append(f"family {f} has inconsistent group ids {sorted(groups)}")

    by_split: dict[str, list[DatasetDocument]] = {
        s: [d for d in docs if d.split == s] for s in SPLIT_NAMES
    }

    # Evidence-value leakage: identical sensitive values / evidence sentences across splits.
    span_splits: dict[str, set[str]] = defaultdict(set)
    for d in docs:
        for sp in d.gold_evidence_spans:
            if len(sp.text) >= MIN_LEAK_SPAN_CHARS:
                span_splits[sp.text].add(d.split)
    leaked = sorted(t for t, s in span_splits.items() if len(s) > 1)
    if leaked:
        err.append(
            f"{len(leaked)} evidence value(s) appear in more than one split, e.g. {leaked[:3]!r}"
        )
    report.stats["cross_split_shared_evidence_values"] = len(leaked)

    # Near-duplicate check across splits (Jaccard of word shingles).
    k = spec.near_duplicate.shingle_size
    sh = {d.doc_id: _shingles(d.content, k) for d in docs}
    worst = (0.0, "", "")
    n_pairs = 0
    over: list[tuple[float, str, str]] = []
    split_docs = [(s, by_split[s]) for s in SPLIT_NAMES]
    for i, (_, a_docs) in enumerate(split_docs):
        for _, b_docs in split_docs[i + 1 :]:
            for a in a_docs:
                for b in b_docs:
                    n_pairs += 1
                    j = _jaccard(sh[a.doc_id], sh[b.doc_id])
                    if j > worst[0]:
                        worst = (j, a.doc_id, b.doc_id)
                    if j >= spec.near_duplicate.cross_split_max_jaccard:
                        over.append((j, a.doc_id, b.doc_id))
    report.stats["cross_split_pairs_checked"] = n_pairs
    report.stats["cross_split_max_jaccard"] = round(worst[0], 4)
    report.stats["near_duplicate_threshold"] = spec.near_duplicate.cross_split_max_jaccard
    report.stats["cross_split_pairs_over_threshold"] = len(over)
    if over:
        over.sort(reverse=True)
        err.append(
            f"{len(over)} cross-split near-duplicate pair(s) >= "
            f"{spec.near_duplicate.cross_split_max_jaccard}; "
            f"worst {over[0][0]:.3f} ({over[0][1]} vs {over[0][2]})"
        )

    # Within-family similarity (informational: templates are expected to be similar).
    fam_docs: dict[str, list[DatasetDocument]] = defaultdict(list)
    for d in docs:
        fam_docs[d.family_id].append(d)
    sims: list[float] = []
    for members in fam_docs.values():
        for i in range(min(len(members), 6)):
            for j in range(i + 1, min(len(members), 6)):
                sims.append(_jaccard(sh[members[i].doc_id], sh[members[j].doc_id]))
    if sims:
        report.stats["within_family_mean_jaccard"] = round(sum(sims) / len(sims), 4)
        report.stats["within_family_max_jaccard"] = round(max(sims), 4)

    # Split composition
    for tier in TIERS:
        if not any(d.tier == tier for d in by_split["test"]):
            err.append(f"test split has no {tier} documents")
        for s in ("train", "calibration", "dev"):
            if not any(d.tier == tier for d in by_split[s]):
                warn.append(f"{s} split has no {tier} documents")

    labels = [("level", lv) for lv in policy.level_ids] + [
        ("category", c) for c in policy.category_ids
    ]
    for split in SPLIT_NAMES:
        n_min = spec.min_positives_per_label[split]
        for axis, label in labels:
            if axis == "level":
                n = sum(1 for d in by_split[split] if d.gold_level == label)
            else:
                n = sum(1 for d in by_split[split] if label in d.gold_categories)
            if n == 0 and split == "test":
                err.append(f"test split has no positives for {axis} {label} (macro-F1 undefined)")
            if n < n_min:
                report.small_sample_flags.append(
                    {
                        "split": split,
                        "axis": axis,
                        "label": label,
                        "positives": n,
                        "min_required": n_min,
                    }
                )

    total = len(docs)
    target_total = spec.target_total_docs
    if abs(total - target_total) / target_total > 0.10:
        warn.append(f"total documents {total} deviates >10% from target {target_total}")
    for split in SPLIT_NAMES:
        target = spec.split_fractions.as_dict()[split] * total
        actual = len(by_split[split])
        if target and abs(actual - target) / target > 0.15:
            warn.append(f"{split} split has {actual} docs; target {target:.0f} (>15% deviation)")
    report.stats["n_documents"] = total
    return report
