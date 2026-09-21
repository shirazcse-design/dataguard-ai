"""Compare blind human labels with the synthetic gold and the models' predictions (read-only).

Three-way comparison per document: synthetic gold vs blind human label vs each approach's prediction.
Inputs are files only: the returned blind sheet(s), the blind key, and the ALREADY-EXECUTED predictions in the
committed adjudication sheet. Nothing is re-run and no model is called; the dataset is not loaded, and the
locked test split is never read, scored or used. No label, taxonomy, schema, threshold, prompt or
configuration is changed, and there is no combined headline score: only counts, patterns and agreement.

Before anything is compared, the package is verified against its manifest (the key, the reviewer sheet and
the adjudication artifacts must be the ones the package was built from) and every returned sheet must pass
`check_completed`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.classification.config_loader import ConfigBundle

from .blind_review import CONTENT, NO_CATEGORY, PRIOR_ARTIFACTS, Variant, check_completed
from .dataset.build import DEFAULT_DATA_DIR

ADJUDICATION_SHEET = "review/adjudication_sheet.csv"
APPROACHES = ["rules", "ml", "llm_small", "llm_mid", "llm_large", "hybrid"]
SMALL_SAMPLE_MIN = 25
PATTERNS = ["all agree", "gold=human≠pred", "human=pred≠gold", "gold=pred≠human", "all differ"]
DATASET_BANNER = "AI-generated synthetic dataset — pending human gold-label review"
REVIEWER_KINDS = ("human", "ai")
AI_REVIEW_BANNER = (
    "**AI REVIEW: NOT HUMAN VALIDATION.** The blind sheet was completed by an AI model, not a person. "
    "Every 'human' column and word below means 'the blind reviewer', here an AI model (see `reviewer_id`). "
    "This is a second, independent AI opinion on the same taxonomy. It does NOT satisfy the human gold-label "
    "review requirement (decision A20), and it must not be used to apply label or taxonomy changes A / B / C "
    "or to clear the MCP freeze criteria"
)
Label = tuple[str, tuple[str, ...]]  # (level, sorted categories)


class PackageError(ValueError):
    """The package or a returned sheet cannot be trusted; nothing is compared."""


# ---- predictions (parsed from the executed adjudication sheet) ----------------------------------------------
def parse_pred(cell: str) -> tuple[str, Label | None]:
    """('label', (level, cats)) | ('abstained', None) | ('unavailable', None).

    A Rules abstention is the default level, not a finding, so it is never compared as a label. Cells such as
    'not available (no recorded run for this split)' and 'NO LABEL (...)' carry no label either.
    """
    cell = cell.strip()
    if " | " not in cell:
        return "unavailable", None
    level, rest = cell.split(" | ", 1)
    if "[abstained" in rest:
        return "abstained", None
    cats = rest.split(" conf=")[0].strip()
    parts = [] if cats in ("", "-") else sorted(cats.split(";"))
    return "label", (level.strip(), tuple(parts))


def triple_pattern(gold: Label | str, human: Label | str, pred: Label | str) -> str:
    if gold == human == pred:
        return "all agree"
    if gold == human:
        return "gold=human≠pred"
    if human == pred:
        return "human=pred≠gold"
    if gold == pred:
        return "gold=pred≠human"
    return "all differ"


# ---- loading and verification -------------------------------------------------------------------------------
def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(text: str) -> list[dict[str, str]]:
    csv.field_size_limit(10**9)
    return list(csv.DictReader(io.StringIO(text)))


def load_package(data_dir: Path | str, variant: Variant = CONTENT) -> dict[str, Any]:
    """Load the key, the reviewer sheet and the executed predictions, verified against the manifest."""
    base = Path(data_dir)
    manifest = json.loads((base / variant.manifest_file).read_text(encoding="utf-8"))
    problems = []
    if _sha(base / variant.key_file) != manifest["key_sha256"]:
        problems.append("the blind key differs from the one the package was built with")
    if _sha(base / variant.sheet_file) != manifest["reviewer_files_sha256"][variant.sheet_file]:
        problems.append("the blind reviewer sheet differs from the one the package was built with")
    for rel in PRIOR_ARTIFACTS:
        if _sha(base / rel) != manifest["adjudication_artifacts_sha256"][rel]:
            problems.append(f"{rel} changed since the package was built")
    if problems:
        raise PackageError("package integrity check failed:\n  " + "\n  ".join(problems))
    key = _rows((base / variant.key_file).read_text(encoding="utf-8"))
    adjudication = {
        r["sample_id"]: r for r in _rows((base / ADJUDICATION_SHEET).read_text(encoding="utf-8"))
    }
    missing = [k["sample_id"] for k in key if k["sample_id"] not in adjudication]
    if missing:
        raise PackageError(f"no executed predictions for {missing}")
    return {
        "variant": variant,
        "manifest": manifest,
        "key": key,
        "adjudication": adjudication,
        "sheet_text": (base / variant.sheet_file).read_text(encoding="utf-8"),
    }


def _split(value: str) -> list[str]:
    return [p.strip() for p in value.split(";") if p.strip()]


def read_reviewer(completed: str, package: dict[str, Any], bundle: ConfigBundle) -> dict[str, Any]:
    """A validated returned sheet as {'reviewer_id', 'date', 'items': {sample_id: normalized answers}}."""
    errs = check_completed(completed, package["sheet_text"], bundle, package["variant"])
    if errs:
        raise PackageError("the returned sheet is not well-formed:\n  " + "\n  ".join(errs))
    rows = _rows(completed)
    ids = {r["reviewer_id"].strip() for r in rows}
    if len(ids) != 1:
        raise PackageError(
            f"a returned sheet must have exactly one reviewer_id, found {sorted(ids)}"
        )
    items = {}
    for r in rows:
        insufficient = r["human_insufficient_information"] == "yes"
        cats = (
            []
            if r["human_categories"].strip() == NO_CATEGORY
            else sorted(_split(r["human_categories"]))
        )
        items[r["sample_id"]] = {
            "level": r["human_level"] or None,
            "cats": tuple(cats),
            "confidence": r["human_confidence"],
            "ambiguity": r["human_taxonomy_ambiguity"] == "yes",
            "alts": _split(r["human_alternative_levels"]),
            "insufficient": insufficient,
            "rationale": r["human_rationale"].strip(),
            "date": r["review_date"],
        }
    return {
        "reviewer_id": ids.pop(),
        "date": max(i["date"] for i in items.values()),
        "items": items,
    }


# ---- the comparison -----------------------------------------------------------------------------------------
def compare(package: dict[str, Any], reviewer: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per document for one reviewer."""
    out = []
    for k in package["key"]:
        sid = k["sample_id"]
        h = reviewer["items"][sid]
        gold: Label = (k["gold_level"], tuple(sorted(_split(k["gold_categories"]))))
        gold_alts = _split(k["gold_acceptable_alternative_levels"])
        human: Label | None = None if h["level"] is None else (h["level"], h["cats"])
        row: dict[str, Any] = {
            "reviewer_id": reviewer["reviewer_id"],
            "variant": package["variant"].name,
            "review_order": int(k["review_order"]),
            "sample_id": sid,
            "role": k["role"],
            "family_id": k["family_id"],
            "split": k["split"],
            "gold": gold,
            "gold_alts": gold_alts,
            "human": human,
            "human_confidence": h["confidence"],
            "human_ambiguity": h["ambiguity"],
            "human_alts": h["alts"],
            "human_insufficient": h["insufficient"],
            "human_rationale": h["rationale"],
            "level_human_eq_gold": None if human is None else human[0] == gold[0],
            "cats_human_eq_gold": None if human is None else human[1] == gold[1],
            "lenient_level_match": None
            if human is None
            else (human[0] == gold[0] or human[0] in gold_alts or gold[0] in h["alts"]),
            "preds": {},
        }
        for a in APPROACHES:
            status, label = parse_pred(package["adjudication"][sid][f"pred_{a}"])
            entry: dict[str, Any] = {"status": status, "label": label}
            if label is not None and human is not None:
                entry["level_pattern"] = triple_pattern(gold[0], human[0], label[0])
                entry["cats_pattern"] = triple_pattern(gold[1], human[1], label[1])
            row["preds"][a] = entry
        out.append(row)
    return out


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[c] * cb[c] for c in set(a) | set(b)) / (n * n)
    return None if pe == 1 else (po - pe) / (1 - pe)


def inter_reviewer(rows_a: list[dict[str, Any]], rows_b: list[dict[str, Any]]) -> dict[str, Any]:
    both = [(x, y) for x, y in zip(rows_a, rows_b, strict=True) if x["human"] and y["human"]]
    assert all(x["sample_id"] == y["sample_id"] for x, y in both)
    la = [x["human"][0] for x, _ in both]
    lb = [y["human"][0] for _, y in both]
    return {
        "n": len(both),
        "level_agree": sum(p == q for p, q in zip(la, lb, strict=True)),
        "cats_agree": sum(x["human"][1] == y["human"][1] for x, y in both),
        "kappa_level": cohen_kappa(la, lb),
        "disagreements": [
            (x["sample_id"], x["role"], x["human"], y["human"])
            for x, y in both
            if x["human"] != y["human"]
        ],
    }


# ---- rendering ----------------------------------------------------------------------------------------------
def _lab(label: Label | None) -> str:
    if label is None:
        return "(no label)"
    return f"{label[0]} ({';'.join(label[1]) or 'no categories'})"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _flag(n: int) -> str:
    return " (SMALL_SAMPLE)" if n < SMALL_SAMPLE_MIN else ""


def _frac(k: int, n: int) -> str:
    return f"{k}/{n}"


def _dist(items: list[str]) -> str:
    return ", ".join(f"{k} ×{v}" for k, v in Counter(items).most_common()) or "-"


def csv_rows(
    all_rows: list[dict[str, Any]], reviewer_kind: str = "human"
) -> tuple[list[str], list[dict[str, str]]]:
    cols = ["reviewer_id", "variant", "review_order", "sample_id", "role", "family_id", "split", "gold_level",
            "gold_categories", "gold_acceptable_alternative_levels", "human_level", "human_categories",
            "human_confidence", "human_taxonomy_ambiguity", "human_alternative_levels",
            "human_insufficient_information", "human_rationale", "level_human_eq_gold",
            "cats_human_eq_gold", "lenient_level_match"]  # fmt: skip
    for a in APPROACHES:
        cols += [f"pred_{a}", f"level_pattern_{a}", f"cats_pattern_{a}"]
    if reviewer_kind != "human":
        cols.insert(
            1, "reviewer_kind"
        )  # only non-default kinds add the column: human output is unchanged

    def tf(v: bool | None) -> str:
        return "" if v is None else str(v).upper()

    out = []
    for r in all_rows:
        d = {
            "reviewer_id": r["reviewer_id"], "variant": r["variant"], "review_order": str(r["review_order"]),
            "sample_id": r["sample_id"], "role": r["role"], "family_id": r["family_id"], "split": r["split"],
            "gold_level": r["gold"][0], "gold_categories": ";".join(r["gold"][1]),
            "gold_acceptable_alternative_levels": ";".join(r["gold_alts"]),
            "human_level": r["human"][0] if r["human"] else "",
            "human_categories": ";".join(r["human"][1]) if r["human"] else "",
            "human_confidence": r["human_confidence"], "human_taxonomy_ambiguity": tf(r["human_ambiguity"]),
            "human_alternative_levels": ";".join(r["human_alts"]),
            "human_insufficient_information": tf(r["human_insufficient"]),
            "human_rationale": r["human_rationale"], "level_human_eq_gold": tf(r["level_human_eq_gold"]),
            "cats_human_eq_gold": tf(r["cats_human_eq_gold"]), "lenient_level_match": tf(r["lenient_level_match"]),
        }  # fmt: skip
        for a in APPROACHES:
            p = r["preds"][a]
            d[f"pred_{a}"] = _lab(p["label"]) if p["status"] == "label" else p["status"]
            d[f"level_pattern_{a}"] = p.get("level_pattern", "")
            d[f"cats_pattern_{a}"] = p.get("cats_pattern", "")
        if reviewer_kind != "human":
            d["reviewer_kind"] = reviewer_kind
        out.append(d)
    return cols, out


def to_csv(cols: list[str], rows: list[dict[str, str]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _reviewer_section(rows: list[dict[str, Any]], reviewer_id: str, date: str) -> list[str]:
    L: list[str] = []
    add = L.append
    controls = [r for r in rows if r["role"] == "control"]
    disputed = [r for r in rows if r["role"] == "disputed"]
    labelled = [r for r in rows if r["human"]]
    add(f"## Reviewer `{reviewer_id}` (last review date {date})")
    add("")
    add(
        f"* documents: {len(rows)} ({len(disputed)} disputed in {len({r['family_id'] for r in disputed})} families, {len(controls)} controls)"
    )
    add(f"* human confidence: {_dist([r['human_confidence'] for r in rows])}")
    add(
        f"* flagged taxonomy ambiguity: {sum(r['human_ambiguity'] for r in rows)}; flagged insufficient information: {sum(r['human_insufficient'] for r in rows)}; no level given: {len(rows) - len(labelled)}"
    )
    add("")

    # controls: reviewer calibration
    add(f"### Controls: human vs synthetic gold{_flag(len(controls))}")
    add("")
    cl = [r for r in controls if r["human"]]
    add(
        f"Undisputed documents; their gold is AI-authored too, so this is calibration of the reviewer (and a check on the gold), not ground truth. Level agreement {_frac(sum(r['level_human_eq_gold'] for r in cl), len(cl))}; category-set agreement {_frac(sum(r['cats_human_eq_gold'] for r in cl), len(cl))}; lenient level match {_frac(sum(r['lenient_level_match'] for r in cl), len(cl))}."
    )
    bad = [r for r in cl if not (r["level_human_eq_gold"] and r["cats_human_eq_gold"])]
    if bad:
        add("")
        add(_table(["sample", "family", "gold", "human", "confidence"],
                   [[r["sample_id"], r["family_id"], _lab(r["gold"]), _lab(r["human"]), r["human_confidence"]] for r in bad]))  # fmt: skip
    else:
        add("")
        add("Every labelled control matches the gold on level and categories.")
    add("")

    # disputed families
    fams = sorted({r["family_id"] for r in disputed})
    add(f"### Disputed families: human vs synthetic gold{_flag(len(disputed))}")
    add("")
    add(
        "Documents inside a family are near-identical template instances: each family is ONE independent decision."
    )
    add("")
    hdr = ["family", "n", "gold", "human labels", "level = gold", "cats = gold", "lenient level", "ambiguity yes", "insufficient", "consistent"]  # fmt: skip
    trs = []
    for f in fams:
        fr = [r for r in disputed if r["family_id"] == f]
        fl = [r for r in fr if r["human"]]
        labs = [_lab(r["human"]) for r in fr]
        trs.append([f"`{f}`", len(fr), _lab(fr[0]["gold"]), _dist(labs),
                    _frac(sum(r["level_human_eq_gold"] for r in fl), len(fl)),
                    _frac(sum(r["cats_human_eq_gold"] for r in fl), len(fl)),
                    _frac(sum(r["lenient_level_match"] for r in fl), len(fl)),
                    _frac(sum(r["human_ambiguity"] for r in fr), len(fr)),
                    _frac(sum(r["human_insufficient"] for r in fr), len(fr)),
                    "yes" if len(set(labs)) == 1 else "NO"])  # fmt: skip
    add(_table(hdr, trs))
    add("")
    add("`consistent` = the reviewer gave every document of the family the same label.")
    add("")

    # three-way patterns
    for key, what in (("level_pattern", "sensitivity level"), ("cats_pattern", "category set")):
        add(f"### Gold vs human vs prediction: {what} (disputed documents){_flag(len(disputed))}")
        add("")
        add(
            "Counts of documents per pattern; only documents where the approach produced a label and the human gave a level are counted (Rules abstentions and calibration documents without recorded LLM runs are excluded)."
        )
        add("")
        trs = []
        for f in fams:
            for a in APPROACHES:
                ps = [r["preds"][a].get(key) for r in disputed if r["family_id"] == f]
                ps = [p for p in ps if p]
                if not ps:
                    continue
                c = Counter(ps)
                trs.append([f"`{f}`", a, len(ps), *[c.get(p, 0) for p in PATTERNS]])
        add(_table(["family", "approach", "n", *PATTERNS], trs))
        add("")
    add(
        "How to read the patterns: `gold=human≠pred` = the human confirms the gold and the model is wrong against both; `human=pred≠gold` = the human sides with the model, evidence the gold may be wrong (or that the model and the human share a reading the gold author did not); `gold=pred≠human` = the human is the outlier; `all differ` = three different labels."
    )
    add("")

    # decision evidence
    add(f"### Evidence for decisions A / B / C (facts only, not a decision){_flag(len(disputed))}")
    add("")
    for title, fam, focus in (
        (
            "A. PUBLIC vs INTERNAL without a release marker in the text",
            "hn_public_api_docs_placeholder_keys",
            "level",
        ),
        ("A (follow-on). same rule, calibration family", "hn_business_case_study", "level"),
        (
            "B. does an MRN count as another direct identifier (PII)?",
            "phi_prescription_record",
            "cats",
        ),
        (
            "C. alternative levels for a draft customer story",
            "amb_customer_case_study_draft",
            "level",
        ),
    ):
        fr = [r for r in disputed if r["family_id"] == fam]
        if not fr:
            continue
        add(f"**{title}** (`{fam}`, {len(fr)} documents, one independent decision)")
        add("")
        add(
            f"* synthetic gold: {_lab(fr[0]['gold'])}; gold alternatives: {', '.join(fr[0]['gold_alts']) or 'none'}"
        )
        add(f"* human labels: {_dist([_lab(r['human']) for r in fr])}")
        if focus == "cats":
            add(
                f"* documents where the human added PII: {sum(bool(r['human']) and 'PII' in r['human'][1] for r in fr)} of {len(fr)}"
            )
        alts = Counter(a for r in fr for a in r["human_alts"])
        add(
            f"* human ambiguity flagged: {sum(r['human_ambiguity'] for r in fr)} of {len(fr)}; alternative levels named: {_dist(list(alts.elements()))}; insufficient information: {sum(r['human_insufficient'] for r in fr)} of {len(fr)}"
        )
        rats = sorted({r["human_rationale"] for r in fr})
        add(
            "* rationale(s): "
            + " / ".join(f"“{x}”" for x in rats[:3])
            + (" …" if len(rats) > 3 else "")
        )
        add("")
    return L


def render_report(
    package: dict[str, Any],
    reviewers: list[dict[str, Any]],
    per_reviewer: list[list[dict[str, Any]]],
    input_hashes: dict[str, str],
    extra_sections: list[list[str]] | None = None,
    reviewer_kind: str = "human",
) -> str:
    m = package["manifest"]
    meta_variant = package["variant"].shows_metadata
    L: list[str] = []
    add = L.append
    ai = reviewer_kind == "ai"
    kind_title = "AI review" if ai else "Blind review"
    add(
        f"# {kind_title} (source metadata shown): gold vs blind reviewer vs model predictions"
        if meta_variant and ai
        else f"# {kind_title}: gold vs blind reviewer vs model predictions"
        if ai
        else "# Blind review (source metadata shown): gold vs human vs model predictions"
        if meta_variant
        else "# Blind review: gold vs human vs model predictions"
    )
    add("")
    if ai:
        add(f"> {AI_REVIEW_BANNER}.")
        add("")
    add(
        f"> **{DATASET_BANNER}.** Read-only comparison. No label, taxonomy, schema v1.0, threshold, prompt, model configuration or the frozen hybrid configuration was changed, and the **locked test split was not read, scored or used**. There is no combined headline score."
    )
    add("")
    add("## What this is and is not")
    add("")
    add(
        "* Inputs: the returned blind sheet(s), the blind key, and the predictions already executed for the adjudication sheet (nothing was re-run)."
    )
    add(
        f"* Package: {m['n_items']} items ({m['n_disputed']} disputed, {m['n_controls']} controls), seed `{m['seed']}`, dataset sha256 `{m['dataset_sha256'][:16]}…`, taxonomy {m['taxonomy_version']}; integrity verified against the manifest."
    )
    if meta_variant:
        add(
            "* **Variant: the reviewer was shown the document record's source metadata** (for example `source_system`) in addition to filename and text. The LLM and ML classifiers never receive metadata (`include_metadata: false`), so a human/model difference here can reflect that information gap rather than a reading difference."
        )
    add(
        "* Every table is **SMALL_SAMPLE** (fewer than 25 per cell), and the disputed set is 4 independent decisions, not 21: counts are shown, not rates with intervals."
    )
    if ai:
        add(
            "* The reviewer is an AI model that applied the same taxonomy and guidelines that produced the gold: agreement shows consistent application of the rules, not that the rules are right, and a model can share a reading with the classifiers that a person would not. A human decision is required before any label or taxonomy change."
        )
        add(
            "* One AI model is one opinion, and it was not run under controlled conditions (prompt, model version and any tool use are not recorded here); it cannot separate a dataset problem from that model's idiosyncrasy."
        )
    else:
        add(
            "* The human applied the same taxonomy and guidelines that produced the gold: agreement shows consistent application of the rules, not that the rules are right. A human decision is required before any label or taxonomy change."
        )
        add(
            "* One human is one opinion; a single reviewer cannot separate a dataset problem from a reviewer idiosyncrasy."
        )
    add("")
    add("Input files (sha256):")
    add("")
    for name, h in sorted(input_hashes.items()):
        add(f"* `{name}`: `{h}`")
    add("")
    for rv, rows in zip(reviewers, per_reviewer, strict=True):
        L += _reviewer_section(rows, rv["reviewer_id"], rv["date"])
    if len(reviewers) >= 2:
        add("## Inter-reviewer agreement")
        add("")
        for i in range(len(reviewers)):
            for j in range(i + 1, len(reviewers)):
                r = inter_reviewer(per_reviewer[i], per_reviewer[j])
                k = "n/a" if r["kappa_level"] is None else f"{r['kappa_level']:.2f}"
                add(
                    f"**`{reviewers[i]['reviewer_id']}` vs `{reviewers[j]['reviewer_id']}`** ({r['n']} documents labelled by both{_flag(r['n'])}): level agreement {_frac(r['level_agree'], r['n'])}, category-set agreement {_frac(r['cats_agree'], r['n'])}, Cohen's kappa on level {k}."
                )
                add("")
                if r["disagreements"]:
                    add(
                        _table(
                            ["sample", "role", "first", "second"],
                            [[s, role, _lab(x), _lab(y)] for s, role, x, y in r["disagreements"]],
                        )
                    )
                    add("")
    for sec in extra_sections or []:
        L += sec
    return "\n".join(L).rstrip() + "\n"


def paired_section(
    content_rows: list[dict[str, Any]],
    meta_rows: list[dict[str, Any]],
    content_id: str,
    meta_id: str,
) -> list[str]:
    """What changed between a content-only review and a metadata-shown review of the same documents."""
    L: list[str] = []
    add = L.append
    same = content_id == meta_id
    c_by = {r["sample_id"]: r for r in content_rows}
    pairs = [(c_by[m["sample_id"]], m) for m in meta_rows if m["sample_id"] in c_by]
    both = [(c, m) for c, m in pairs if c["human"] and m["human"]]
    add(
        f"## Effect of showing metadata: `{content_id}` (content only) vs `{meta_id}` (metadata shown)"
    )
    add("")
    add(
        f"{'The same reviewer' if same else 'Different reviewers'}; {len(both)} of {len(pairs)} documents were labelled in both variants{_flag(len(both))}. "
        + (
            "A second pass by the same reviewer is anchored on the first (they may remember it), so read a change as evidence, not as a controlled effect."
            if same
            else "The difference mixes the effect of metadata with the difference between two people."
        )
    )
    add("")
    disputed = sorted({c["family_id"] for c, _ in both if c["role"] == "disputed"})
    trs = []
    for f in disputed:
        fr = [(c, m) for c, m in both if c["family_id"] == f]
        trs.append(
            [
                f"`{f}`",
                len(fr),
                _lab(fr[0][0]["gold"]),
                _dist([_lab(c["human"]) for c, _ in fr]),
                _dist([_lab(m["human"]) for _, m in fr]),
                _frac(sum(c["human"][0] != m["human"][0] for c, m in fr), len(fr)),
                _frac(sum(c["human"][1] != m["human"][1] for c, m in fr), len(fr)),
                _frac(sum(bool(c["level_human_eq_gold"]) for c, _ in fr), len(fr)),
                _frac(sum(bool(m["level_human_eq_gold"]) for _, m in fr), len(fr)),
            ]
        )
    add(
        _table(
            [
                "family",
                "n",
                "gold",
                "content only",
                "metadata shown",
                "level changed",
                "cats changed",
                "level = gold (content only)",
                "level = gold (metadata shown)",
            ],
            trs,
        )
    )
    add("")
    ctl = [(c, m) for c, m in both if c["role"] == "control"]
    add(
        f"Controls{_flag(len(ctl))}: level changed {_frac(sum(c['human'][0] != m['human'][0] for c, m in ctl), len(ctl))}, category set changed {_frac(sum(c['human'][1] != m['human'][1] for c, m in ctl), len(ctl))}."
    )
    changed = [(c, m) for c, m in both if c["human"] != m["human"]]
    add("")
    if changed:
        add(
            _table(
                ["sample", "role", "family", "gold", "content only", "metadata shown"],
                [
                    [
                        c["sample_id"],
                        c["role"],
                        f"`{c['family_id']}`",
                        _lab(c["gold"]),
                        _lab(c["human"]),
                        _lab(m["human"]),
                    ]
                    for c, m in changed
                ],
            )
        )
    else:
        add("No document's label changed when the metadata was shown.")
    add("")
    return L


def run(
    completed_paths: list[Path],
    bundle: ConfigBundle,
    data_dir: Path | str = DEFAULT_DATA_DIR,
    variant: Variant = CONTENT,
    content_sheets: list[Path] | None = None,
    reviewer_kind: str = "human",
) -> tuple[str, str]:
    """Return (report markdown, per-sample csv). Raises PackageError if anything cannot be trusted.

    `content_sheets` (only with the metadata variant) adds the paired "effect of showing metadata" section.
    """
    if reviewer_kind not in REVIEWER_KINDS:
        raise PackageError(f"reviewer_kind must be one of {REVIEWER_KINDS}, got {reviewer_kind!r}")
    if content_sheets and not variant.shows_metadata:
        raise PackageError("content-only sheets can be paired only with the metadata variant")
    package = load_package(data_dir, variant)
    reviewers, per_reviewer, hashes = [], [], {}
    for p in completed_paths:
        text = Path(p).read_text(encoding="utf-8")
        rv = read_reviewer(text, package, bundle)
        if rv["reviewer_id"] in {r["reviewer_id"] for r in reviewers}:
            raise PackageError(f"reviewer_id {rv['reviewer_id']!r} appears in more than one sheet")
        reviewers.append(rv)
        per_reviewer.append(compare(package, rv))
        hashes[Path(p).name] = hashlib.sha256(text.encode()).hexdigest()
    hashes[variant.key_file] = package["manifest"]["key_sha256"]
    hashes[variant.sheet_file] = package["manifest"]["reviewer_files_sha256"][variant.sheet_file]
    extra: list[list[str]] = []
    if content_sheets:
        cpkg = load_package(data_dir, CONTENT)
        seen_ids: set[str] = set()
        for p in content_sheets:
            text = Path(p).read_text(encoding="utf-8")
            crv = read_reviewer(text, cpkg, bundle)
            if crv["reviewer_id"] in seen_ids:
                raise PackageError(
                    f"reviewer_id {crv['reviewer_id']!r} appears in more than one content-only sheet"
                )
            seen_ids.add(crv["reviewer_id"])
            crows = compare(cpkg, crv)
            hashes[f"(content-only) {Path(p).name}"] = hashlib.sha256(text.encode()).hexdigest()
            hashes[CONTENT.key_file] = cpkg["manifest"]["key_sha256"]
            for rv, rows in zip(reviewers, per_reviewer, strict=True):
                extra.append(paired_section(crows, rows, crv["reviewer_id"], rv["reviewer_id"]))
    cols, rows = csv_rows([r for rs in per_reviewer for r in rs], reviewer_kind)
    report = render_report(package, reviewers, per_reviewer, hashes, extra, reviewer_kind)
    return report, to_csv(cols, rows)
