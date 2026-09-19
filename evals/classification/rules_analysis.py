"""Error analysis for the Rules Engine: which families fail, and which detector is responsible.

Runs the engine directly (so detector ids are visible) and joins with gold labels. Works on the
development splits only (the locked test split is refused by `load_documents`).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.classification.policy import TaxonomyPolicy
from rules.classifier import RulesClassifier
from rules.types import STRENGTH_RANK

from .dataset.schema import DatasetDocument


@dataclass
class FamilyErrors:
    family_id: str
    tier: str
    n_docs: int
    gold_level: str
    gold_categories: list[str]
    decoy_for: list[str]
    predicted_levels: Counter = field(default_factory=Counter)
    false_negatives: Counter = field(default_factory=Counter)  # category -> docs missed
    false_positives: Counter = field(default_factory=Counter)  # category -> docs wrongly flagged
    fp_detectors: Counter = field(default_factory=Counter)  # detector id -> docs
    fn_detectors_weak: Counter = field(
        default_factory=Counter
    )  # weak-only evidence for a missed category
    abstained: int = 0
    decoy_hits: int = 0
    high_risk_fn: int = 0
    high_risk_fp: int = 0
    suppressed: Counter = field(default_factory=Counter)


def analyze(
    clf: RulesClassifier, docs: list[DatasetDocument], policy: TaxonomyPolicy
) -> dict[str, FamilyErrors]:
    eng = clf.engine
    emit = eng.cfg.emit_min
    out: dict[str, FamilyErrors] = {}
    for d in sorted(docs, key=lambda x: x.doc_id):
        res = eng.analyze(d.to_request().document)
        fam = out.setdefault(
            d.family_id,
            FamilyErrors(
                d.family_id, d.tier, 0, d.gold_level, sorted(d.gold_categories), sorted(d.decoy_for)
            ),
        )
        fam.n_docs += 1
        level = res.level
        if level is None:
            fam.abstained += 1
            level = eng.cfg.standalone_default_level
        fam.predicted_levels[level] += 1
        pred = set(res.categories)
        gold = set(d.gold_categories)
        for c in gold - pred:
            fam.false_negatives[c] += 1
            weak = [x.detector_id for x in res.detections if x.value == c]
            for det in set(weak):
                fam.fn_detectors_weak[f"{c}:{det}"] += 1
        for c in pred - gold:
            fam.false_positives[c] += 1
            for x in res.detections:
                if x.value == c and STRENGTH_RANK[x.strength] >= STRENGTH_RANK[emit(c)]:
                    fam.fp_detectors[f"{c}:{x.detector_id}"] += 1
        fam.decoy_hits += bool(pred & set(d.decoy_for)) or level in d.decoy_for
        gh = policy.derive_high_risk(d.gold_level, d.gold_categories).value
        ph = policy.derive_high_risk(level, sorted(pred)).value
        fam.high_risk_fn += gh and not ph
        fam.high_risk_fp += ph and not gh
        for s in res.suppressions:
            fam.suppressed[f"{s.detector_id}:{s.reason.split(':')[0]}"] += 1
    return out


def render(errors: dict[str, FamilyErrors], title: str) -> str:
    L = [f"# {title}", ""]
    total_docs = sum(f.n_docs for f in errors.values())
    L.append(f"{total_docs} documents in {len(errors)} families.")
    L.append("")

    def block(name: str, rows: list[str]) -> None:
        L.append(f"## {name}")
        L.append("")
        L.extend(rows or ["_none_"])
        L.append("")

    fn_rows, fp_rows, hr_rows, decoy_rows, lvl_rows = [], [], [], [], []
    for f in sorted(errors.values(), key=lambda x: (x.tier, x.family_id)):
        tag = (
            f"`{f.family_id}` ({f.tier}, {f.n_docs} docs, gold {f.gold_level} {f.gold_categories})"
        )
        if f.false_negatives:
            weak = f" weak-evidence: {dict(f.fn_detectors_weak)}" if f.fn_detectors_weak else ""
            fn_rows.append(f"- {tag}: missed {dict(f.false_negatives)}{weak}")
        if f.false_positives:
            fp_rows.append(f"- {tag}: flagged {dict(f.false_positives)} via {dict(f.fp_detectors)}")
        if f.high_risk_fn:
            hr_rows.append(
                f"- {tag}: high-risk missed in {f.high_risk_fn}/{f.n_docs} "
                f"(pred {dict(f.predicted_levels)})"
            )
        if f.decoy_hits:
            decoy_rows.append(
                f"- {tag}: decoy hit {f.decoy_hits}/{f.n_docs} (decoy for {f.decoy_for})"
            )
        wrong = {k: v for k, v in f.predicted_levels.items() if k != f.gold_level}
        if wrong:
            lvl_rows.append(f"- {tag}: predicted {dict(f.predicted_levels)}")
    block("False negatives (categories missed)", fn_rows)
    block("False positives (categories wrongly asserted)", fp_rows)
    block("High-risk false negatives", hr_rows)
    block("Hard-negative decoy hits", decoy_rows)
    block("Level errors", lvl_rows)
    return "\n".join(L) + "\n"
