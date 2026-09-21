"""Blind human-review package for the disputed gold labels (read-only; nothing here changes a label).

The reviewer-facing files carry ONLY: the sample id, the input (filename + content), the taxonomy
definitions, the labeling rules and the allowed labels, plus empty response fields. They never carry the
synthetic gold label, any approach's prediction, a proposed label, or the earlier adjudication. Which
documents are disputed (and their gold labels) live in a separate KEY file that the reviewer must not open.

The review set is the disputed families plus a few undisputed CONTROL documents, shuffled together with a
fixed seed, so that being in the set is not itself a cue. Only the DEVELOPMENT splits are loaded (explicit
split list); the locked test split is never read, scored or used.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.classification.config_loader import ConfigBundle

from .dataset.build import DEFAULT_DATA_DIR, load_documents, load_manifest
from .dataset.schema import DatasetDocument
from .gold_review import DECISIONS_FILE, REVIEW_SPLITS, load_decisions

BLIND_DIR = "review/blind"  # what the reviewer receives
KEY_DIR = "review/blind_key"  # what the reviewer must NOT see
SHEET_FILE = f"{BLIND_DIR}/blind_review_sheet.csv"
PACKET_FILE = f"{BLIND_DIR}/blind_review_packet.md"
KEY_FILE = f"{KEY_DIR}/blind_review_key.csv"
MANIFEST_FILE = f"{KEY_DIR}/blind_review_manifest.json"
GUIDELINES = Path(__file__).resolve().parents[2] / "docs/uc4/labeling-guidelines.md"

SEED = "uc4-blind-review-v1"
CONTROLS_PER_LEVEL = 3
CONFIDENCE = ("low", "medium", "high")
YES_NO = ("yes", "no")
NO_CATEGORY = "NONE"

INPUT_COLUMNS = ["sample_id", "filename", "content"]
RESPONSE_COLUMNS = [
    "human_level", "human_categories", "human_confidence", "human_rationale",
    "human_taxonomy_ambiguity", "human_alternative_levels", "human_insufficient_information",
    "reviewer_id", "review_date",
]  # fmt: skip
SHEET_COLUMNS = INPUT_COLUMNS + RESPONSE_COLUMNS


@dataclass(frozen=True)
class Variant:
    """One blind-review package. `content` shows filename + text only (what the classifiers receive);
    `metadata` also shows the document record's source metadata. Same items, same key semantics."""

    name: str
    blind_dir: str  # what the reviewer receives
    key_dir: str  # what the reviewer must NOT see
    input_columns: tuple[str, ...]
    order_seed: str
    shows_metadata: bool
    sheet_name: str
    packet_name: str

    @property
    def sheet_file(self) -> str:
        return f"{self.blind_dir}/{self.sheet_name}"

    @property
    def packet_file(self) -> str:
        return f"{self.blind_dir}/{self.packet_name}"

    @property
    def key_file(self) -> str:
        return f"{self.key_dir}/blind_review_key.csv"

    @property
    def manifest_file(self) -> str:
        return f"{self.key_dir}/blind_review_manifest.json"

    @property
    def sheet_columns(self) -> list[str]:
        return [*self.input_columns, *RESPONSE_COLUMNS]

    @property
    def default_results_dir(self) -> str:
        return (
            "review/blind_results"
            if self.name == "content"
            else f"review/blind_results_{self.name}"
        )


CONTENT = Variant(
    "content", BLIND_DIR, KEY_DIR, tuple(INPUT_COLUMNS), SEED, False,
    "blind_review_sheet.csv", "blind_review_packet.md",
)  # fmt: skip
METADATA = Variant(
    "metadata", "review/blind_metadata", "review/blind_metadata_key",
    ("sample_id", "filename", "source_metadata", "content"), "uc4-blind-review-v1-metadata-order", True,
    "blind_review_metadata_sheet.csv", "blind_review_metadata_packet.md",
)  # fmt: skip
VARIANTS = {v.name: v for v in (CONTENT, METADATA)}
KEY_COLUMNS = [
    "review_order", "sample_id", "role", "family_id", "split", "tier", "gold_level", "gold_categories",
    "gold_ambiguity_flag", "gold_acceptable_alternative_levels",
]  # fmt: skip
PRIOR_ARTIFACTS = [
    DECISIONS_FILE,
    "review/adjudication_sheet.csv",
    "review/adjudication_sheet.meta.json",
]
DATASET_BANNER = "AI-generated synthetic dataset — pending human gold-label review"


def _order_key(doc_id: str, seed: str = SEED) -> str:
    return hashlib.sha256(f"{seed}:{doc_id}".encode()).hexdigest()


def disputed_families(data_dir: Path | str) -> list[str]:
    """Families whose adjudication requires a human decision (from the versioned decisions file)."""
    decisions, _ = load_decisions(data_dir)
    return sorted(f for f, d in decisions.families.items() if d.human_decision_required)


def select_items(
    docs: list[DatasetDocument],
    disputed: list[str],
    seed: str = SEED,
    controls_per_level: int = CONTROLS_PER_LEVEL,
    order_seed: str | None = None,
) -> list[dict[str, Any]]:
    """Every document of a disputed family + deterministic undisputed controls, shuffled with `order_seed`
    (default: `seed`). The SET is chosen with `seed` only, so variants that differ only in order share it.

    Controls come from the DEV split, exclude adversarial (T5) documents and the disputed families, and are
    stratified by gold level (one document per family first, then fill from already-used families).
    """
    assert all(d.split in REVIEW_SPLITS for d in docs), "the locked test split must never be loaded"
    disp = set(disputed)
    items = [{"doc": d, "role": "disputed"} for d in docs if d.family_id in disp]
    pool = [d for d in docs if d.split == "dev" and d.family_id not in disp and d.tier != "T5"]
    for level in sorted({d.gold_level for d in pool}):
        cand = sorted(
            (d for d in pool if d.gold_level == level), key=lambda d: _order_key(d.doc_id, seed)
        )
        chosen: list[DatasetDocument] = []
        seen: set[str] = set()
        for d in cand:  # one document per family first
            if d.family_id not in seen and len(chosen) < controls_per_level:
                chosen.append(d)
                seen.add(d.family_id)
        for d in cand:  # then fill up
            if len(chosen) < controls_per_level and d not in chosen:
                chosen.append(d)
        items += [{"doc": d, "role": "control"} for d in chosen]
    items.sort(key=lambda it: _order_key(it["doc"].doc_id, order_seed or seed))
    for i, it in enumerate(items, 1):
        it["order"] = i
    return items


def source_metadata(doc: DatasetDocument) -> str:
    """The document record's metadata map as `key=value; key=value` (sorted), for the metadata variant."""
    return "; ".join(f"{k}={v}" for k, v in sorted(doc.metadata.items()))


def sheet_rows(items: list[dict[str, Any]], variant: Variant = CONTENT) -> list[dict[str, str]]:
    """Reviewer-facing rows: id + input + EMPTY response fields. No label, prediction or note of any kind."""
    rows = []
    for it in items:
        d = it["doc"]
        inputs = {"sample_id": d.doc_id, "filename": d.filename, "content": d.content}
        if variant.shows_metadata:
            inputs["source_metadata"] = source_metadata(d)
        rows.append(
            {**{c: inputs[c] for c in variant.input_columns}, **{c: "" for c in RESPONSE_COLUMNS}}
        )
    return rows


def key_rows(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "review_order": str(it["order"]),
            "sample_id": it["doc"].doc_id,
            "role": it["role"],
            "family_id": it["doc"].family_id,
            "split": it["doc"].split,
            "tier": it["doc"].tier,
            "gold_level": it["doc"].gold_level,
            "gold_categories": ";".join(sorted(it["doc"].gold_categories)),
            "gold_ambiguity_flag": str(it["doc"].ambiguity_flag).upper(),
            "gold_acceptable_alternative_levels": ";".join(it["doc"].acceptable_alternative_levels),
        }
        for it in items
    ]


def to_csv(rows: list[dict[str, str]], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _guideline_excerpt() -> str:
    """Sections 1-3 of the labeling guidelines, verbatim (the decision procedure and the category rules).

    Sections 4-7 (evidence spans, tiers, generator conventions, known limitations) are dataset-construction
    details, and the tier table names the hard-negative design, so they are not shown to a blind reviewer.
    """
    text = GUIDELINES.read_text(encoding="utf-8")
    m = re.search(r"^## 1\..*?(?=^## 4\.)", text, flags=re.S | re.M)
    if not m:
        raise ValueError("labeling-guidelines.md no longer has sections 1-3 in the expected shape")
    # nest the excerpt under the packet's own "##" heading
    return re.sub(r"^(#+ )", r"#\1", m.group(0).strip(), flags=re.M)


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def taxonomy_markdown(bundle: ConfigBundle) -> str:
    """The taxonomy definitions verbatim from the versioned configuration (nothing re-worded)."""
    tax = bundle.taxonomy
    out = ["### Sensitivity levels (choose exactly one)", ""]
    for lv in sorted(tax.levels, key=lambda x: x.rank):
        out.append(
            f"* **`{lv.id}`** ({lv.name}), rank {lv.rank}: {' '.join(lv.description.split())}"
        )
    out += ["", "### Data categories (choose zero or more)", ""]
    for c in tax.categories:
        out.append(f"#### `{c.id}` ({c.name})")
        out.append("")
        out.append(f"{' '.join(c.description.split())}")
        out.append("")
        out.append(
            f"* Minimum level when this category applies (a configurable policy default): `{c.level_floor}`"
        )
        out.append("* Positive examples: " + "; ".join(c.positive_examples))
        out.append("* Counter-examples (does NOT apply): " + "; ".join(c.counter_examples))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def packet_markdown(
    bundle: ConfigBundle, items: list[dict[str, Any]], variant: Variant = CONTENT
) -> str:
    levels = [lv.id for lv in sorted(bundle.taxonomy.levels, key=lambda x: x.rank)]
    cats = [c.id for c in bundle.taxonomy.categories]
    L: list[str] = []
    add = L.append
    add(
        "# Blind classification review"
        + (" (with source metadata)" if variant.shows_metadata else "")
    )
    add("")
    add(
        f"> **{DATASET_BANNER}.** All people, companies, hosts and addresses in the documents are invented."
    )
    add("")
    add(
        "You are asked to label each document below **independently**, using only this file. You are not shown"
    )
    if variant.shows_metadata:
        add(
            "any classification label, model output or earlier opinion, on purpose: your judgement is the measurement."
        )
    else:
        add(
            "any existing label, model output or earlier opinion, on purpose: your judgement is the measurement."
        )
    add("")
    add("## Rules of the review")
    add("")
    if variant.shows_metadata:
        add(
            "**What you are shown:** each document's filename, its text, and the **source metadata recorded with it**"
        )
        add(
            "(for example the system it came from), exactly as recorded. Metadata is evidence about where a document came"
        )
        add(
            "from, never truth: the labeling rules below still say labels describe the content. Judge how much weight it"
        )
        add(
            "deserves and say so in your rationale. Extension and embedded labels are not part of this exercise (none of these"
        )
        add("documents carries an embedded label).")
        add("")
        add(
            "1. Label from the document (its filename, text and source metadata) and the definitions in this file only."
        )
    else:
        add(
            "**What you are shown:** each document's filename and text, exactly as recorded. Other document fields"
        )
        add(
            "(extension, embedded labels, source or author metadata) are deliberately not part of this exercise, even"
        )
        add("where the labeling rules below mention them.")
        add("")
        add(
            "1. Label from the document (its filename and text) and the definitions in this file only."
        )
    add(
        "2. Do **not** look the sample ids up anywhere else (other files in this repository contain labels)."
    )
    add(
        "3. Do **not** use an AI model or a rule/pattern scanner to pre-label. Record your own reading."
    )
    add(
        "4. If the definitions do not settle a case, say so (taxonomy ambiguity) instead of forcing certainty."
    )
    add(
        "5. If the document itself does not give you enough to decide, say so (insufficient information)."
    )
    add("")
    add(f"## What to record for every document (columns of `{variant.sheet_name}`)")
    add("")
    add("| column | allowed values |")
    add("|---|---|")
    add(
        f"| `human_level` | one of {', '.join(f'`{x}`' for x in levels)} (may be left blank ONLY if insufficient information is `yes`) |"
    )
    add(
        f"| `human_categories` | zero or more of {', '.join(f'`{x}`' for x in cats)}, separated by `;`; write `{NO_CATEGORY}` when no category applies |"
    )
    add(f"| `human_confidence` | {', '.join(f'`{x}`' for x in CONFIDENCE)} |")
    add(
        "| `human_rationale` | one or two sentences, in your words: what in the document decided it, and which definition or rule you applied |"
    )
    add(
        "| `human_taxonomy_ambiguity` | `yes` if a careful reviewer could reasonably choose a different level or category set under these definitions; otherwise `no` |"
    )
    add(
        "| `human_alternative_levels` | if ambiguity is `yes` and a different LEVEL is also defensible, list it (`;`-separated); otherwise leave blank |"
    )
    add(
        "| `human_insufficient_information` | `yes` if the document does not give enough to decide; otherwise `no` |"
    )
    add("| `reviewer_id`, `review_date` | your identifier and the date (YYYY-MM-DD) |")
    add("")
    add(
        "## Definitions (verbatim from the project taxonomy, version "
        + str(bundle.taxonomy.taxonomy_version)
        + ")"
    )
    add("")
    add(taxonomy_markdown(bundle).rstrip())
    add("")
    add("## Labeling rules (verbatim excerpt of the project labeling guidelines)")
    add("")
    add(_guideline_excerpt())
    add("")
    add(f"## Documents ({len(items)})")
    add("")
    for it in items:
        d = it["doc"]
        f = _fence(d.content)
        add(f"### Item {it['order']:02d} — `{d.doc_id}`")
        add("")
        add(f"Filename: `{d.filename}`")
        add("")
        if variant.shows_metadata:
            add(f"Source metadata: `{source_metadata(d) or '(none recorded)'}`")
            add("")
        add(f"{f}text")
        add(d.content.rstrip("\n"))
        add(f)
        add("")
    return "\n".join(L).rstrip() + "\n"


def leak_check(
    reviewer_files: dict[str, str], docs: list[DatasetDocument], bundle: ConfigBundle
) -> list[str]:
    """Fail-closed audit of the reviewer-facing text: no family id, annotation note, or label-bearing column.

    (Level and category names legitimately appear in the definitions; what must not appear is anything that
    ties a label or a model to a particular sample.)
    """
    bad: list[str] = []
    fams = {d.family_id for d in docs}
    notes = {d.annotation_notes.strip() for d in docs if d.annotation_notes.strip()}
    forbidden_words = (
        "original_gold",
        "pred_",
        "proposed",
        "adjudicat",
        "disagree",
        "llm_",
        "hybrid",
        "decoy",
    )
    for name, text in reviewer_files.items():
        low = text.lower()
        bad += [f"{name}: family id {f!r}" for f in sorted(fams) if f in text]
        bad += [f"{name}: annotation note {n[:40]!r}" for n in sorted(notes) if n in text]
        bad += [f"{name}: forbidden token {w!r}" for w in forbidden_words if w in low]
    return bad


def build_package(
    bundle: ConfigBundle,
    git: dict[str, Any],
    data_dir: str = str(DEFAULT_DATA_DIR),
    variant: Variant = CONTENT,
):
    """Return (files, manifest). `files` maps a data-dir-relative path to its text."""
    docs = load_documents(data_dir, splits=REVIEW_SPLITS)
    disputed = disputed_families(data_dir)
    items = select_items(docs, disputed, order_seed=variant.order_seed)
    sheet = to_csv(sheet_rows(items, variant), variant.sheet_columns)
    packet = packet_markdown(bundle, items, variant)
    problems = leak_check({variant.sheet_file: sheet, variant.packet_file: packet}, docs, bundle)
    if problems:
        raise ValueError("the blind package leaks answer information:\n  " + "\n  ".join(problems))
    key = to_csv(key_rows(items), KEY_COLUMNS)
    manifest_src = load_manifest(data_dir)
    _, dec_sha = load_decisions(data_dir)
    from collections import Counter

    roles = Counter(it["role"] for it in items)
    manifest = {
        "purpose": "blind human review of the disputed gold labels; reviewer must not open this directory",
        "dataset_banner": DATASET_BANNER,
        "seed": SEED,
        "controls_per_level": CONTROLS_PER_LEVEL,
        "n_items": len(items),
        "n_disputed": roles["disputed"],
        "n_controls": roles["control"],
        "disputed_families": disputed,
        "splits_loaded": REVIEW_SPLITS,
        "locked_test_split_read": False,
        "labels_changed": False,
        "dataset_sha256": manifest_src.get("dataset_sha256"),
        "decisions_sha256": dec_sha,
        "decisions_file": DECISIONS_FILE,
        "adjudication_artifacts_sha256": {  # unchanged inputs to the later gold-vs-human-vs-model comparison
            rel: hashlib.sha256((Path(data_dir) / rel).read_bytes()).hexdigest()
            for rel in PRIOR_ARTIFACTS
        },
        "taxonomy_version": str(bundle.taxonomy.taxonomy_version),
        "reviewer_files_sha256": {
            variant.sheet_file: hashlib.sha256(sheet.encode()).hexdigest(),
            variant.packet_file: hashlib.sha256(packet.encode()).hexdigest(),
        },
        "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
        "leak_check": "passed",
        "git_commit": git.get("commit"),
        "git_branch": git.get("branch"),
        "git_dirty": git.get("dirty"),
    }
    if variant.shows_metadata:
        manifest["variant"] = variant.name
        manifest["shows_metadata"] = True
        manifest["order_seed"] = variant.order_seed
        manifest["purpose"] = (
            "blind human review WITH source metadata shown; same items as the content-only package, "
            "different order; reviewer must not open this directory"
        )
    return {variant.sheet_file: sheet, variant.packet_file: packet, variant.key_file: key}, manifest


# ---- checking a completed sheet ------------------------------------------------------------------------
def check_completed(
    completed_csv: str, original_csv: str, bundle: ConfigBundle, variant: Variant = CONTENT
) -> list[str]:
    """Problems in a returned sheet (empty list = well-formed). Does not judge whether a label is right."""
    levels = {lv.id for lv in bundle.taxonomy.levels}
    cats = {c.id for c in bundle.taxonomy.categories}
    orig = {r["sample_id"]: r for r in csv.DictReader(io.StringIO(original_csv))}
    reader = csv.DictReader(io.StringIO(completed_csv))
    if reader.fieldnames != variant.sheet_columns:
        return [f"columns differ from the package: {reader.fieldnames}"]
    errs: list[str] = []
    seen: set[str] = set()
    for i, r in enumerate(reader, 2):
        sid = r["sample_id"]
        tag = f"row {i} ({sid})"
        if sid not in orig:
            errs.append(f"{tag}: unknown sample id")
            continue
        if sid in seen:
            errs.append(f"{tag}: duplicated")
        seen.add(sid)
        if any(r[c] != orig[sid][c] for c in variant.input_columns):
            errs.append(f"{tag}: input text was edited")
        insufficient = r["human_insufficient_information"]
        if insufficient not in YES_NO:
            errs.append(f"{tag}: human_insufficient_information must be yes or no")
        if r["human_level"] not in levels and not (
            r["human_level"] == "" and insufficient == "yes"
        ):
            errs.append(f"{tag}: human_level must be one of {sorted(levels)}")
        c = r["human_categories"].strip()
        parts = [p.strip() for p in c.split(";")] if c else []
        if (
            not parts
            or (NO_CATEGORY in parts and len(parts) > 1)
            or any(p != NO_CATEGORY and p not in cats for p in parts)
        ):
            errs.append(
                f"{tag}: human_categories must be {NO_CATEGORY} or ';'-separated taxonomy categories"
            )
        if r["human_confidence"] not in CONFIDENCE:
            errs.append(f"{tag}: human_confidence must be one of {CONFIDENCE}")
        if not r["human_rationale"].strip():
            errs.append(f"{tag}: human_rationale is empty")
        if r["human_taxonomy_ambiguity"] not in YES_NO:
            errs.append(f"{tag}: human_taxonomy_ambiguity must be yes or no")
        alts = [a.strip() for a in r["human_alternative_levels"].split(";") if a.strip()]
        if any(a not in levels for a in alts):
            errs.append(f"{tag}: human_alternative_levels must be taxonomy levels")
        if alts and r["human_taxonomy_ambiguity"] != "yes":
            errs.append(f"{tag}: alternative levels given but taxonomy ambiguity is not yes")
        if not r["reviewer_id"].strip() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["review_date"]):
            errs.append(f"{tag}: reviewer_id and a YYYY-MM-DD review_date are required")
    errs += [f"missing sample {s}" for s in sorted(set(orig) - seen)]
    return errs
