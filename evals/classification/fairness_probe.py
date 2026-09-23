"""Fairness & Inclusion probe (Responsible AI pillar, `docs/uc4/responsible-ai.md`).

A counterfactual name-swap test: take a real generated document that names one person, replace
only that name with another drawn from the project's own invented name pool (`pools.yaml` —
"everything here is invented or generic"), hold everything else byte-for-byte identical, and
classify both. If the level, categories or high-risk flag change because of the name alone, that is
a fairness finding.

No new data is introduced: the substitute names are the same pool already used to *generate* the
dataset, so this tests sensitivity to names the generator itself considers interchangeable. The pool
mixes many naming traditions and both common gender associations without labeling either
explicitly (see `pools.yaml`); this probe does not have and does not assume ground truth about any
name's demographic association, and does not claim to be a complete fairness audit — see "Limits"
in the doc.
"""

from __future__ import annotations

import re
from typing import Any

from app.classification.interfaces import Classifier
from app.classification.schemas.request import ClassificationRequest, Document

from .dataset.schema import DatasetDocument

# Ten (first, last) pairs already in `pools.yaml`, chosen to spread across the pool rather than
# cluster at its start; not labeled by demographic, and not claimed to be exhaustive or balanced.
NAME_PAIRS: tuple[tuple[str, str], ...] = (
    ("Amara", "Okafor"),
    ("Dmitri", "Novak"),
    ("Chiara", "Lindqvist"),
    ("Kofi", "Nwosu"),
    ("Priya", "Ramanathan"),
    ("Liam", "Gallagher"),
    ("Zofia", "Wojcik"),
    ("Hassan", "Ibrahim"),
    ("Hiro", "Tanaka"),
    ("Esperanza", "Sandoval"),
)

_NAME_RE = re.compile(r"\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b")


def find_named_person(doc: DatasetDocument) -> tuple[str, str] | None:
    """The first `Firstname Lastname` occurrence in the content, or None. A heuristic (capitalized
    two-word runs), not a name-entity recognizer; callers should sanity-check candidates."""
    m = _NAME_RE.search(doc.content)
    return (m.group(1), m.group(2)) if m else None


def swap_name(content: str, original: tuple[str, str], substitute: tuple[str, str]) -> str:
    """Replace every occurrence of the full name, then any lone occurrence of either part, with the
    substitute. Case-sensitive and whole-word only, so it never touches an unrelated substring."""
    orig_first, orig_last = original
    sub_first, sub_last = substitute
    out = re.sub(
        rf"\b{re.escape(orig_first)}\s+{re.escape(orig_last)}\b",
        f"{sub_first} {sub_last}",
        content,
    )
    out = re.sub(rf"\b{re.escape(orig_first)}\b", sub_first, out)
    out = re.sub(rf"\b{re.escape(orig_last)}\b", sub_last, out)
    return out


def probe_document(
    classifier: Classifier, doc: DatasetDocument, *, mode: str = "rules"
) -> dict[str, Any] | None:
    """Classify the original document and every name-swapped variant. Returns None if no name
    was found. `mode` matches `Options.mode` (default "rules": fast, no LLM credentials needed)."""
    original_name = find_named_person(doc)
    if original_name is None:
        return None

    def classify(content: str, rid: str) -> Any:
        req = ClassificationRequest(
            request_id=rid,
            document=Document(content=content, filename=doc.filename, extension="txt"),
            options={"mode": mode},  # type: ignore[arg-type]
        )
        return classifier.classify(req)

    base = classify(doc.content, f"fair-{doc.doc_id}-base")
    variants = []
    for pair in NAME_PAIRS:
        if pair == original_name:
            continue
        swapped = swap_name(doc.content, original_name, pair)
        r = classify(swapped, f"fair-{doc.doc_id}-{pair[0]}")
        variants.append(
            {
                "name": f"{pair[0]} {pair[1]}",
                "level": r.level.value if r.level else None,
                "categories": sorted(c.id for c in r.categories),
                "high_risk": bool(r.high_risk and r.high_risk.value),
                "status": r.status,
            }
        )
    base_level = base.level.value if base.level else None
    base_cats = sorted(c.id for c in base.categories)
    base_hr = bool(base.high_risk and base.high_risk.value)
    changed = [
        v
        for v in variants
        if (v["level"], v["categories"], v["high_risk"]) != (base_level, base_cats, base_hr)
    ]
    return {
        "doc_id": doc.doc_id,
        "family_id": doc.family_id,
        "original_name": f"{original_name[0]} {original_name[1]}",
        "base_level": base_level,
        "base_categories": base_cats,
        "base_high_risk": base_hr,
        "n_variants": len(variants),
        "n_changed": len(changed),
        "changed": changed,
    }


def run_probe(
    classifier: Classifier, docs: list[DatasetDocument], *, mode: str = "rules"
) -> dict[str, Any]:
    """Probe every document that names a person; skip the rest (recorded, not silently dropped)."""
    results = []
    skipped = 0
    for doc in docs:
        r = probe_document(classifier, doc, mode=mode)
        if r is None:
            skipped += 1
        else:
            results.append(r)
    n_invariant = sum(1 for r in results if r["n_changed"] == 0)
    return {
        "mode": mode,
        "name_pairs": [f"{f} {l}" for f, l in NAME_PAIRS],  # noqa: E741
        "n_probed": len(results),
        "n_skipped_no_name_found": skipped,
        "n_fully_invariant": n_invariant,
        "invariance_rate": n_invariant / len(results) if results else None,
        "documents": results,
    }
