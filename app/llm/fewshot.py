"""Few-shot examples: a fixed, versioned list of TRAIN documents.

The selection rule is pre-registered in `docs/uc4/llm-plan.md` and implemented in `select_fewshot`;
`prompts/uc4/fewshot.v1.json` stores only ids and hashes (content is read from the train split), and
`load_fewshot` refuses any example that is not a train document with the recorded hash.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.classification.schemas.common import StrictModel
from evals.classification.dataset.schema import DatasetDocument

FEWSHOT_VERSION = "fewshot.v1"
MAX_EXAMPLE_CHARS = 600
QUOTE_CHARS = 160
SELECTION_RULE = (
    "From train: per category (taxonomy order) the alphabetically-first family whose T1 documents "
    "are single-category and <= 600 characters (first doc_id); the alphabetically-first PUBLIC and "
    "INTERNAL T1 families; two T4 hard negatives from different families with different decoy_for; "
    "one multi-category T1 document. Order is by sha256(seed:doc_id)."
)


class FewShotEntry(StrictModel):
    doc_id: str
    family_id: str
    tier: str
    content_hash: str
    role: str


class FewShotFile(StrictModel):
    version: str
    selection_rule: str
    seed: int
    examples: list[FewShotEntry]


def _first_per_family(docs: list[DatasetDocument]) -> list[DatasetDocument]:
    seen: dict[str, DatasetDocument] = {}
    for d in sorted(docs, key=lambda d: (d.family_id, d.doc_id)):
        seen.setdefault(d.family_id, d)
    return [seen[f] for f in sorted(seen)]


def select_fewshot(
    train_docs: list[DatasetDocument], category_ids: list[str], seed: int
) -> list[tuple[DatasetDocument, str]]:
    """Apply the pre-registered rule. Returns (document, role) in prompt order."""
    train = [d for d in train_docs if d.split == "train"]
    short_t1 = [d for d in train if d.tier == "T1" and len(d.content) <= MAX_EXAMPLE_CHARS]
    chosen: list[tuple[DatasetDocument, str]] = []
    for cat in category_ids:
        cands = [d for d in short_t1 if d.gold_categories == [cat]]
        # a family qualifies only if every one of its T1 documents is single-category `cat`
        ok = {
            f
            for f in {d.family_id for d in cands}
            if all(x.gold_categories == [cat] for x in train if x.family_id == f and x.tier == "T1")
        }
        pool = _first_per_family([d for d in cands if d.family_id in ok])
        if pool:
            chosen.append((pool[0], f"category:{cat}"))
    for level in ("PUBLIC", "INTERNAL"):
        pool = _first_per_family(
            [d for d in short_t1 if d.gold_level == level and not d.gold_categories]
        )
        if pool:
            chosen.append((pool[0], f"level:{level}"))
    t4 = _first_per_family(
        [d for d in train if d.tier == "T4" and len(d.content) <= MAX_EXAMPLE_CHARS]
    )
    if t4:
        chosen.append((t4[0], "hard_negative"))
        for d in t4[1:]:
            if sorted(d.decoy_for) != sorted(t4[0].decoy_for):
                chosen.append((d, "hard_negative"))
                break
    multi = _first_per_family([d for d in short_t1 if len(d.gold_categories) > 1])
    if multi:
        chosen.append((multi[0], "multi_category"))

    def order(item: tuple[DatasetDocument, str]) -> str:
        return hashlib.sha256(f"{seed}:{item[0].doc_id}".encode()).hexdigest()

    return sorted(chosen, key=order)


def build_fewshot_file(
    train_docs: list[DatasetDocument], category_ids: list[str], seed: int
) -> FewShotFile:
    picked = select_fewshot(train_docs, category_ids, seed)
    return FewShotFile(
        version=FEWSHOT_VERSION,
        selection_rule=SELECTION_RULE,
        seed=seed,
        examples=[
            FewShotEntry(
                doc_id=d.doc_id,
                family_id=d.family_id,
                tier=d.tier,
                content_hash=d.content_hash,
                role=role,
            )
            for d, role in picked
        ],
    )


def write_fewshot_file(fs: FewShotFile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fs.model_dump(), indent=2) + "\n", encoding="utf-8")


def load_fewshot(path: Path, train_docs: list[DatasetDocument]) -> list[DatasetDocument]:
    """Load the example documents; every one must be a train document with the recorded hash."""
    fs = FewShotFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    by_id = {d.doc_id: d for d in train_docs}
    out: list[DatasetDocument] = []
    for e in fs.examples:
        d = by_id.get(e.doc_id)
        if d is None or d.split != "train":
            raise ValueError(f"few-shot example {e.doc_id} is not a train document")
        if d.content_hash != e.content_hash:
            raise ValueError(f"few-shot example {e.doc_id} changed since the file was written")
        out.append(d)
    return out


def _clip(text: str, limit: int = QUOTE_CHARS) -> str:
    one = " ".join(text.split())
    if len(one) <= limit:
        return one
    cut = one[:limit].rsplit(" ", 1)[0]
    return cut or one[:limit]


def render_expected(doc: DatasetDocument, names: dict[str, str]) -> dict[str, Any]:
    """The expected JSON answer for an example, built only from gold labels and annotations."""
    evidence = []
    for span in doc.gold_evidence_spans[:2]:
        if span.label in doc.gold_categories:
            evidence.append(
                {
                    "quote": _clip(span.text),
                    "supports_axis": "category",
                    "supports_value": span.label,
                }
            )
    if doc.gold_categories:
        listed = ", ".join(names[c] for c in doc.gold_categories)
        rationale = doc.annotation_notes or f"The text contains {listed} content, quoted above."
    else:
        rationale = doc.annotation_notes or (
            "Ordinary business content with no sensitive data of any category."
        )
    return {
        "level": doc.gold_level,
        "categories": sorted(doc.gold_categories),
        "evidence": evidence,
        "rationale": rationale,
        "level_confidence": "high",
        "category_confidence": "high",
        "insufficient_information": False,
    }
