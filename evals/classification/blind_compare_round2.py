"""Round 2 comparison: gold vs blind reviewer(s), and reviewer vs reviewer (read-only, counts only).

Round 2 covers every ambiguity-flagged family in the development splits plus the Round 1 disputed
families. It has no executed model predictions, so there are no model columns: the questions are "does an
independent reader agree with the gold?" and "do two independent readers agree with each other?". Nothing
is re-scored, no label changes, no model is called, the dataset is not loaded and the locked test split is
never touched. Inputs are the package (verified against its manifest) and the returned sheet(s).
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.classification.config_loader import ConfigBundle

from .blind_compare import (
    AI_REVIEW_BANNER,
    DATASET_BANNER,
    REVIEWER_KINDS,
    PackageError,
    _dist,
    _flag,
    _frac,
    _lab,
    _table,
    compare,
    inter_reviewer,
    load_package,
    read_reviewer,
)
from .blind_review import ROUND2
from .dataset.build import DEFAULT_DATA_DIR

FOCUS_FAMILY = "amb_aggregate_health_stats"


def _lenient(r: dict[str, Any]) -> bool:
    return bool(r["lenient_level_match"])


def _family_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["role"] == "review":
            out[r["family_id"]].append(r)
    return dict(sorted(out.items()))


def _reviewer_block(rows: list[dict[str, Any]], reviewer_id: str, date: str) -> list[str]:
    L: list[str] = []
    add = L.append
    controls = [r for r in rows if r["role"] == "control" and r["human"]]
    review = [r for r in rows if r["role"] == "review"]
    fams = _family_rows(rows)
    add(f"## Reviewer `{reviewer_id}` (last review date {date})")
    add("")
    add(
        f"* documents: {len(rows)} ({len(review)} review documents in {len(fams)} families, {len(controls)} controls)"
    )
    add(f"* confidence: {_dist([r['human_confidence'] for r in rows])}")
    add(
        f"* flagged taxonomy ambiguity: {sum(r['human_ambiguity'] for r in rows)}; insufficient information: "
        f"{sum(r['human_insufficient'] for r in rows)}; no level given: {sum(r['human'] is None for r in rows)}"
    )
    add("")
    lvl = sum(bool(r["level_human_eq_gold"]) for r in controls)
    cat = sum(bool(r["cats_human_eq_gold"]) for r in controls)
    add(f"### Controls (undisputed documents; their gold is AI-authored too){_flag(len(controls))}")
    add("")
    add(
        f"Level agreement {_frac(lvl, len(controls))}; category-set agreement {_frac(cat, len(controls))}."
    )
    bad = [r for r in controls if not (r["level_human_eq_gold"] and r["cats_human_eq_gold"])]
    if bad:
        add("")
        add(
            _table(
                ["sample", "family", "gold", "reviewer"],
                [
                    [r["sample_id"], f"`{r['family_id']}`", _lab(r["gold"]), _lab(r["human"])]
                    for r in bad
                ],
            )
        )
    add("")
    add(f"### Review families: reviewer vs gold{_flag(len(review))}")
    add("")
    add(
        _table(
            [
                "family",
                "docs",
                "gold",
                "gold alternatives",
                "reviewer labels",
                "level = gold",
                "lenient",
                "cats = gold",
                "ambiguity yes",
            ],
            [
                [
                    f"`{f}`",
                    len(rs),
                    _lab(rs[0]["gold"]),
                    ";".join(rs[0]["gold_alts"]) or "-",
                    _dist([_lab(r["human"]) for r in rs]),
                    _frac(sum(bool(r["level_human_eq_gold"]) for r in rs), len(rs)),
                    _frac(sum(_lenient(r) for r in rs), len(rs)),
                    _frac(sum(bool(r["cats_human_eq_gold"]) for r in rs), len(rs)),
                    _frac(sum(r["human_ambiguity"] for r in rs), len(rs)),
                ]
                for f, rs in fams.items()
            ],
        )
    )
    add("")
    return L


def _pair_block(a: list[dict[str, Any]], b: list[dict[str, Any]], ida: str, idb: str) -> list[str]:
    L: list[str] = []
    add = L.append
    ir = inter_reviewer(a, b)
    add(f"## Inter-reviewer agreement: `{ida}` vs `{idb}`{_flag(ir['n'])}")
    add("")
    k = ir["kappa_level"]
    add(
        f"* documents both labelled: {ir['n']}; level agreement {_frac(ir['level_agree'], ir['n'])} "
        f"(Cohen's kappa on level {'n/a' if k is None else f'{k:.2f}'}); category-set agreement {_frac(ir['cats_agree'], ir['n'])}"
    )
    add("")
    by_id_b = {r["sample_id"]: r for r in b}
    fam_a = _family_rows(a)
    rows = []
    for f, rs in fam_a.items():
        for ra in rs:
            rb = by_id_b[ra["sample_id"]]
            if not (ra["human"] and rb["human"]):
                continue
            g = ra["gold"]
            same = ra["human"] == rb["human"]
            verdict = (
                "both agree with gold"
                if same and ra["human"] == g
                else "both agree with each other, differ from gold"
                if same
                else "reviewers differ"
            )
            rows.append(
                [f"`{f}`", ra["sample_id"], _lab(g), _lab(ra["human"]), _lab(rb["human"]), verdict]
            )
    add("### Review documents")
    add("")
    add(_table(["family", "sample", "gold", ida, idb, "reading"], rows))
    add("")
    add(
        "`both agree with each other, differ from gold` rows are the candidates for a label discussion; `reviewers differ` rows show where the definitions do not settle the case."
    )
    add("")
    return L


def _focus_block(per_reviewer: list[list[dict[str, Any]]]) -> list[str]:
    L: list[str] = []
    add = L.append
    add(
        f"## Focus: `{FOCUS_FAMILY}` (de-identified aggregate patient statistics with small cell counts)"
    )
    add("")
    focus = next(
        (r for rows in per_reviewer for r in _family_rows(rows).get(FOCUS_FAMILY, [])), None
    )
    if focus is not None:
        alts = ";".join(focus["gold_alts"]) or "none"
        add(
            f"Gold (as of this run's key): {_lab(focus['gold'])}, acceptable alternatives {alts}. "
            "The frozen hybrid predicted INTERNAL for this family in the calibration check. "
            "The reviewers' own words, verbatim, so the policy owner can decide a small-cell rule:"
        )
        add("")
    for rows in per_reviewer:
        rs = _family_rows(rows).get(FOCUS_FAMILY, [])
        if not rs:
            continue
        add(f"**Reviewer `{rs[0]['reviewer_id']}`**")
        add("")
        add(
            _table(
                [
                    "sample",
                    "level",
                    "categories",
                    "confidence",
                    "ambiguity",
                    "alternative levels",
                    "rationale",
                ],
                [
                    [
                        r["sample_id"],
                        (r["human"] or ("(none)", ()))[0],
                        ";".join((r["human"] or ("", ()))[1]) or "none",
                        r["human_confidence"],
                        "yes" if r["human_ambiguity"] else "no",
                        ";".join(r["human_alts"]) or "-",
                        r["human_rationale"].replace("|", "/"),
                    ]
                    for r in rs
                ],
            )
        )
        add("")
    return L


def csv_text(all_rows: list[dict[str, Any]], reviewer_kind: str) -> str:
    cols = [
        "reviewer_id", "reviewer_kind", "review_order", "sample_id", "role", "family_id", "split",
        "gold_level", "gold_categories", "gold_acceptable_alternative_levels", "human_level",
        "human_categories", "human_confidence", "human_taxonomy_ambiguity", "human_alternative_levels",
        "human_insufficient_information", "human_rationale", "level_human_eq_gold",
        "cats_human_eq_gold", "lenient_level_match",
    ]  # fmt: skip

    def tf(v: bool | None) -> str:
        return "" if v is None else str(v).upper()

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for r in all_rows:
        h = r["human"]
        w.writerow(
            {
                "reviewer_id": r["reviewer_id"], "reviewer_kind": reviewer_kind,
                "review_order": r["review_order"], "sample_id": r["sample_id"], "role": r["role"],
                "family_id": r["family_id"], "split": r["split"], "gold_level": r["gold"][0],
                "gold_categories": ";".join(r["gold"][1]),
                "gold_acceptable_alternative_levels": ";".join(r["gold_alts"]),
                "human_level": h[0] if h else "", "human_categories": ";".join(h[1]) if h else "",
                "human_confidence": r["human_confidence"],
                "human_taxonomy_ambiguity": tf(r["human_ambiguity"]),
                "human_alternative_levels": ";".join(r["human_alts"]),
                "human_insufficient_information": tf(r["human_insufficient"]),
                "human_rationale": r["human_rationale"],
                "level_human_eq_gold": tf(r["level_human_eq_gold"]),
                "cats_human_eq_gold": tf(r["cats_human_eq_gold"]),
                "lenient_level_match": tf(r["lenient_level_match"]),
            }
        )  # fmt: skip
    return buf.getvalue()


def run_round2(
    completed_paths: list[Path],
    bundle: ConfigBundle,
    data_dir: Path | str = DEFAULT_DATA_DIR,
    reviewer_kind: str = "human",
    note: str | None = None,
) -> tuple[str, str]:
    """Return (report markdown, per-sample csv). Raises PackageError if anything cannot be trusted."""
    if reviewer_kind not in REVIEWER_KINDS:
        raise PackageError(f"reviewer_kind must be one of {REVIEWER_KINDS}, got {reviewer_kind!r}")
    package = load_package(data_dir, ROUND2)
    m = package["manifest"]
    reviewers, per_reviewer, hashes = [], [], {}
    for p in completed_paths:
        text = Path(p).read_text(encoding="utf-8")
        rv = read_reviewer(text, package, bundle)
        if rv["reviewer_id"] in {r["reviewer_id"] for r in reviewers}:
            raise PackageError(f"reviewer_id {rv['reviewer_id']!r} appears in more than one sheet")
        reviewers.append(rv)
        per_reviewer.append(compare(package, rv))
        hashes[Path(p).name] = hashlib.sha256(text.encode()).hexdigest()
    hashes[ROUND2.key_file] = m["key_sha256"]
    hashes[ROUND2.sheet_file] = m["reviewer_files_sha256"][ROUND2.sheet_file]

    L: list[str] = []
    add = L.append
    add(
        "# Round 2 blind review: gold vs blind reviewer(s)"
        + (" (AI review)" if reviewer_kind == "ai" else "")
    )
    add("")
    if reviewer_kind == "ai":
        add(f"> {AI_REVIEW_BANNER}.")
        add("")
    if note:
        add(
            f"> **Provenance note (recorded verbatim at the operator's request):** {' '.join(note.split())}"
        )
        add("")
    add(
        f"> **{DATASET_BANNER}.** Read-only comparison. No label, taxonomy, schema, threshold, prompt, model "
        "configuration or the frozen hybrid configuration was changed, and the **locked test split was not "
        "read, scored or used**. There is no combined headline score."
    )
    add("")
    add("## What this is and is not")
    add("")
    add(
        "* Inputs: the returned blind sheet(s) and the blind key. There are no model predictions here; nothing was re-run."
    )
    add(
        f"* Package: {m['n_items']} items ({m['n_review']} review documents in {len(m['review_families'])} families, "
        f"{m['n_controls']} controls), seed `{m['seed']}`, dataset sha256 `{m['dataset_sha256'][:16]}…`, taxonomy {m['taxonomy_version']}; "
        "integrity verified against the manifest. Development splits only."
    )
    add(
        "* Every table is **SMALL_SAMPLE** (fewer than 25 per cell), and documents inside a family are near-identical template instances: each family is ONE independent decision."
    )
    add(
        "* The reviewer applied the same taxonomy and guidelines that produced the gold: agreement shows consistent application of the rules, not that the rules are right. Where the reviewers or the gold disagree, a person with policy authority decides; this report only shows the facts."
    )
    add("")
    add("Input files (sha256):")
    add("")
    for name, h in sorted(hashes.items()):
        add(f"* `{name}`: `{h}`")
    add("")
    for rv, rows in zip(reviewers, per_reviewer, strict=True):
        L += _reviewer_block(rows, rv["reviewer_id"], rv["date"])
    if len(reviewers) >= 2:
        for i in range(len(reviewers)):
            for j in range(i + 1, len(reviewers)):
                L += _pair_block(
                    per_reviewer[i],
                    per_reviewer[j],
                    reviewers[i]["reviewer_id"],
                    reviewers[j]["reviewer_id"],
                )
    L += _focus_block(per_reviewer)
    add("## Limits")
    add("")
    add(
        "* One or two readers cannot separate a dataset problem from personal idiosyncrasy; three or more would."
    )
    add(
        "* The four ambiguous families of the locked test split are not in this package by design (the generator refuses locked-split documents); they need a separate, explicitly authorised post-hoc review."
    )
    add(
        "* Nothing here is a decision. A relabel after seeing model errors on a split is tuning on that split; the decision log must say what evidence each change rests on."
    )
    return "\n".join(L).rstrip() + "\n", csv_text(
        [r for rs in per_reviewer for r in rs], reviewer_kind
    )
