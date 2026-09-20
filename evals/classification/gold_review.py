"""Gold-label review preparation: an auditable adjudication sheet and impact analysis.

Read-only with respect to the dataset: nothing here changes a gold label, the taxonomy, schema,
thresholds, prompts or any configuration. Only the DEVELOPMENT splits (train, calibration, dev) are
loaded, through an explicit split list; the locked test split is never read, scored or used.

Predictions are EXECUTED (Rules, ML, replayed LLM tiers, the default hybrid). The reviewer's
adjudications come from a versioned YAML file (`adjudication_decisions.yaml`); a disagreement with no
adjudication is an error, so nothing can be silently skipped. Counterfactual metrics apply proposed
labels IN MEMORY ONLY and are labelled hypothetical.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field

from app.classification.config_loader import ConfigBundle
from app.classification.schemas.common import StrictModel
from app.classification.service import ClassificationService
from app.llm import build_llm_classifier
from ml.classification import build_ml_classifier
from rules import build_rules_classifier

from .dataset.build import DEFAULT_DATA_DIR, load_documents, load_manifest
from .dataset.schema import DatasetDocument
from .evaluate import evaluate
from .reporting import _ci, _f, _table

REVIEW_SPLITS = ["train", "calibration", "dev"]  # never "test"
APPROACHES = ["rules", "ml", "llm_small", "llm_mid", "llm_large", "hybrid"]
NON_ML = ["rules", "llm_small", "llm_mid", "llm_large", "hybrid"]
DecisionType = Literal[
    "gold_label_error", "taxonomy_ambiguity", "genuine_model_error", "insufficient_information"
]
TYPE_CODE = {
    "gold_label_error": "1",
    "taxonomy_ambiguity": "2",
    "genuine_model_error": "3",
    "insufficient_information": "4",
}
DECISIONS_FILE = "review/adjudication_decisions.yaml"
SHEET_FILE = "review/adjudication_sheet.csv"
META_FILE = "review/adjudication_sheet.meta.json"

COLUMNS = [
    "sample_id", "split", "tier", "family_id", "filename", "content",
    "original_gold_level", "original_gold_categories", "original_ambiguity_flag",
    "original_acceptable_alternative_levels", "annotation_notes",
    "pred_rules", "pred_ml", "pred_llm_small", "pred_llm_mid", "pred_llm_large", "pred_hybrid",
    "approaches_disagreeing_with_gold", "llm_rationale_small", "llm_rationale_mid", "llm_rationale_large",
    "disagreement_type", "disagreement_type_code", "taxonomy_ambiguity_flag", "taxonomy_definitions_applicable",
    "proposed_human_gold_level", "proposed_human_gold_categories", "proposed_ambiguity_flag",
    "proposed_acceptable_alternative_levels", "label_change_proposed", "alternative_gold_if_taxonomy_clarified",
    "reviewer_rationale", "recommended_action", "human_decision_required", "priority", "reviewer",
    "human_decision", "human_final_gold_level", "human_final_gold_categories", "human_final_ambiguity_flag",
    "human_final_acceptable_alternative_levels", "human_reviewer", "human_review_date", "human_comment",
]  # fmt: skip


class FamilyDecision(StrictModel):
    priority: Literal["P1", "P2", "P3"]
    applies_to: Literal["all", "disagreeing"]
    disagreement_type: DecisionType
    taxonomy_ambiguity_flag: bool
    label_change_proposed: bool
    proposed_gold_level: str
    proposed_gold_categories: list[str] = Field(default_factory=list)
    proposed_ambiguity_flag: bool | None = None
    proposed_acceptable_alternative_levels: list[str] | None = None
    alternative_gold_if_taxonomy_clarified: str | None = None
    human_decision_required: bool
    reviewer_rationale: str
    recommended_action: str


class DecisionsFile(StrictModel):
    version: int
    reviewer: str
    families: dict[str, FamilyDecision]


def load_decisions(data_dir: Path | str) -> tuple[DecisionsFile, str]:
    path = Path(data_dir) / DECISIONS_FILE
    raw = path.read_bytes()
    return DecisionsFile.model_validate(yaml.safe_load(raw)), hashlib.sha256(raw).hexdigest()


def _fmt(p: dict[str, Any]) -> str:
    if p["level"] is None:
        return f"NO LABEL ({p['status']})"
    cats = ";".join(p["categories"]) or "-"
    tag = " [abstained: default level, not a finding]" if p.get("abstained") else ""
    raw = p.get("conf")
    try:  # a model probability: two decimals, so the sheet does not depend on library float noise
        raw = f"{float(raw):.2f}"
    except (TypeError, ValueError):
        pass  # a rule strength or a verbalized bucket
    conf = f" conf={raw}" if raw not in (None, "None") else ""
    return f"{p['level']} | {cats}{conf}{tag}"


def _disagrees(doc: DatasetDocument, key: str, p: dict[str, Any]) -> bool:
    if p["level"] is None or (key == "rules" and p.get("abstained")):
        return False  # an abstention or an absent label is not a disagreement
    return p["level"] != doc.gold_level or p["categories"] != sorted(doc.gold_categories)


def collect_predictions(bundle: ConfigBundle, docs: list[DatasetDocument], data_dir: str) -> dict:
    """Execute every approach on the (development) documents. LLM tiers exist for dev only (replay)."""
    assert all(d.split in REVIEW_SPLITS for d in docs), "the locked test split must never be loaded"
    rules = build_rules_classifier(bundle)
    ml = build_ml_classifier(bundle, data_dir=data_dir)
    tiers = {
        t: build_llm_classifier(bundle, tier=t, mode="replay", data_dir=data_dir)
        for t in ("small", "mid", "large")
    }
    svc = ClassificationService(llm_mode="replay", data_dir=data_dir)

    def brief(r) -> dict[str, Any]:
        if r.level is None:
            return {
                "status": r.status,
                "level": None,
                "categories": [],
                "abstained": r.routing.abstained,
            }
        return {"status": r.status, "level": r.level.value, "categories": sorted(c.id for c in r.categories),
                "abstained": r.routing.abstained, "conf": str(r.level.confidence.raw)}  # fmt: skip

    out: dict[str, dict[str, Any]] = {}
    for d in docs:
        req = d.to_request()
        row: dict[str, Any] = {"rules": brief(rules.classify(req)), "ml": brief(ml.classify(req))}
        if d.split == "dev":
            for t, clf in tiers.items():
                row[f"llm_{t}"] = brief(clf.classify(req))
                resp = clf.client.complete_structured(clf._request(clf.builder.build(req.document)))  # noqa: SLF001
                row[f"llm_{t}"]["rationale"] = json.loads(resp.text).get("rationale", "")
            row["hybrid"] = brief(svc.classify(req))
        out[d.doc_id] = row
    return out


def _definitions(bundle: ConfigBundle, levels: set[str], cats: set[str]) -> str:
    tax = bundle.taxonomy
    parts = [f"{lv.id}: {' '.join(lv.description.split())}" for lv in tax.levels if lv.id in levels]
    for c in tax.categories:
        if c.id in cats:
            neg = f" Does NOT apply: {'; '.join(c.counter_examples)}." if c.counter_examples else ""
            parts.append(f"{c.id} (floor {c.level_floor}): {' '.join(c.description.split())}{neg}")
    return " || ".join(parts)


def build_rows(
    bundle: ConfigBundle, docs: list[DatasetDocument], preds: dict, decisions: DecisionsFile
) -> list[dict[str, Any]]:
    fam_docs = defaultdict(list)
    for d in docs:
        fam_docs[d.family_id].append(d)
    missing = sorted(set(decisions.families) - set(fam_docs))
    if missing:
        raise ValueError(f"adjudicated families not found in the development splits: {missing}")
    rows: list[dict[str, Any]] = []
    unadjudicated: list[str] = []
    order = {s: i for i, s in enumerate(REVIEW_SPLITS)}
    for d in sorted(docs, key=lambda x: (order[x.split], x.family_id, x.doc_id)):
        p = preds[d.doc_id]
        dec = decisions.families.get(d.family_id)
        if d.split != "dev" and dec is None:
            continue  # train/calibration are reviewed only for adjudicated families
        bad = [k for k in APPROACHES if k in p and _disagrees(d, k, p[k])]
        non_ml_bad = [k for k in bad if k != "ml"]
        row: dict[str, Any] = {
            "sample_id": d.doc_id, "split": d.split, "tier": d.tier, "family_id": d.family_id,
            "filename": d.filename, "content": d.content,
            "original_gold_level": d.gold_level, "original_gold_categories": ";".join(sorted(d.gold_categories)),
            "original_ambiguity_flag": d.ambiguity_flag,
            "original_acceptable_alternative_levels": ";".join(d.acceptable_alternative_levels),
            "annotation_notes": d.annotation_notes,
            "approaches_disagreeing_with_gold": ";".join(bad),
            "reviewer": decisions.reviewer, "priority": "", "human_decision_required": False,
            "taxonomy_ambiguity_flag": False, "label_change_proposed": False,
            "alternative_gold_if_taxonomy_clarified": "",
        }  # fmt: skip
        for k in APPROACHES:
            row[f"pred_{k}"] = (
                _fmt(p[k]) if k in p else "not available (no recorded run for this split)"
            )
        for t in ("small", "mid", "large"):
            row[f"llm_rationale_{t}"] = p.get(f"llm_{t}", {}).get("rationale", "")
        if dec is not None and (dec.applies_to == "all" or non_ml_bad):
            row.update({
                "disagreement_type": dec.disagreement_type, "disagreement_type_code": TYPE_CODE[dec.disagreement_type],
                "taxonomy_ambiguity_flag": dec.taxonomy_ambiguity_flag,
                "proposed_human_gold_level": dec.proposed_gold_level,
                "proposed_human_gold_categories": ";".join(sorted(dec.proposed_gold_categories)),
                "proposed_ambiguity_flag": "" if dec.proposed_ambiguity_flag is None else dec.proposed_ambiguity_flag,
                "proposed_acceptable_alternative_levels": ";".join(dec.proposed_acceptable_alternative_levels or []),
                "label_change_proposed": dec.label_change_proposed,
                "alternative_gold_if_taxonomy_clarified": dec.alternative_gold_if_taxonomy_clarified or "",
                "reviewer_rationale": " ".join(dec.reviewer_rationale.split()),
                "recommended_action": " ".join(dec.recommended_action.split()),
                "human_decision_required": dec.human_decision_required, "priority": dec.priority,
            })  # fmt: skip
        elif bad == ["ml"]:
            row.update({
                "disagreement_type": "genuine_model_error", "disagreement_type_code": "3",
                "proposed_human_gold_level": d.gold_level, "proposed_human_gold_categories": ";".join(sorted(d.gold_categories)),
                "reviewer_rationale": "Only the supervised ML model disagrees with the gold label. It is known to over-classify to Highly Confidential and to omit categories (see the ML results); no other approach, and no reading of the taxonomy, supports its answer.",
                "recommended_action": "No label change.", "priority": "P4",
            })  # fmt: skip
        elif non_ml_bad:
            unadjudicated.append(f"{d.family_id}:{d.doc_id}:{non_ml_bad}")
            continue
        else:
            row.update({
                "disagreement_type": "none", "disagreement_type_code": "",
                "proposed_human_gold_level": d.gold_level, "proposed_human_gold_categories": ";".join(sorted(d.gold_categories)),
                "reviewer_rationale": "No approach disagrees with the gold label. Agreement is weak evidence of correctness: the LLM prompt encodes the labeling guidelines that produced the gold, and the dataset is AI-authored.",
                "recommended_action": "None; still subject to the blind human review of the whole sheet.", "priority": "",
            })  # fmt: skip
        levels = {d.gold_level, row["proposed_human_gold_level"]}
        cats = set(d.gold_categories) | set(row["proposed_human_gold_categories"].split(";")) - {""}
        for k in APPROACHES:
            if k in p and p[k]["level"] is not None:
                levels.add(p[k]["level"]) if k != "ml" else None
                cats |= set(p[k]["categories"]) if k != "ml" else set()
        row["taxonomy_definitions_applicable"] = _definitions(bundle, levels, cats)
        for col in COLUMNS:
            row.setdefault(col, "")
        rows.append(row)
    if unadjudicated:
        raise ValueError(
            f"disagreements with no adjudication (add them to the decisions file): {unadjudicated[:8]}"
        )
    return rows


def rows_to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n", extrasaction="raise")
    w.writeheader()
    for r in rows:
        w.writerow(
            {c: ("TRUE" if r[c] is True else "FALSE" if r[c] is False else r[c]) for c in COLUMNS}
        )
    return buf.getvalue()


# ---- hypothetical impact (in memory only) ----------------------------------------------------------
def with_gold(
    docs: list[DatasetDocument], changes: dict[str, dict[str, Any]]
) -> list[DatasetDocument]:
    """Copies of the documents with proposed gold applied. The originals and the files are untouched."""
    return [
        d.model_copy(update=changes[d.family_id]) if d.family_id in changes else d for d in docs
    ]


def scenario_changes(decisions: DecisionsFile) -> dict[str, dict[str, dict[str, Any]]]:
    s1 = {
        f: {
            "gold_level": dec.proposed_gold_level,
            "gold_categories": list(dec.proposed_gold_categories),
        }
        for f, dec in decisions.families.items()
        if dec.label_change_proposed
    }
    s2 = {
        **s1,
        "phi_prescription_record": {
            "gold_level": "HIGHLY_CONFIDENTIAL",
            "gold_categories": ["PHI", "PII"],
        },
    }
    return {
        "S0 current gold": {},
        "S1 apply the proposed label change": s1,
        "S2 = S1 + MRN counts as an identifier": s2,
    }


def build_review(
    bundle: ConfigBundle, git: dict[str, Any], data_dir: str = str(DEFAULT_DATA_DIR)
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    decisions, dec_sha = load_decisions(data_dir)
    docs = load_documents(data_dir, splits=REVIEW_SPLITS)
    preds = collect_predictions(bundle, docs, data_dir)
    rows = build_rows(bundle, docs, preds, decisions)
    dev = [d for d in docs if d.split == "dev"]
    manifest = load_manifest(data_dir)

    classifiers = {
        "rules": build_rules_classifier(bundle),
        "ml": build_ml_classifier(bundle, data_dir=data_dir),
        **{
            f"llm_{t}": build_llm_classifier(bundle, tier=t, mode="replay", data_dir=data_dir)
            for t in ("small", "mid", "large")
        },
        "hybrid": ClassificationService(llm_mode="replay", data_dir=data_dir)._classifier,  # noqa: SLF001
    }
    scen: dict[str, dict[str, Any]] = {}
    for name, changes in scenario_changes(decisions).items():
        ds = with_gold(dev, changes)
        scen[name] = {}
        for a, clf in classifiers.items():
            r = evaluate(clf, ds, bundle, manifest)
            st = r.metrics["headline"]["confidence_intervals"]["statistics"]
            hm = r.metrics["headline"]["metrics"]
            scen[name][a] = {"level": st["level_macro_f1"], "category": st["category_macro_f1"],
                             "hr_p": hm["high_risk"]["precision"], "hr_r": st["high_risk_recall"],
                             "hr_fpr": hm["high_risk"]["false_positive_rate"],
                             "n_families": r.metrics["headline"]["confidence_intervals"]["n_units"]}  # fmt: skip
    # leave-one-family-out sensitivity of the hybrid's level macro-F1 (headline families only)
    lofo: dict[str, float] = {}
    headline = sorted({d.family_id for d in dev if d.tier in bundle.eval.headline_tiers})
    for fam in headline:
        r = evaluate(
            classifiers["hybrid"], [d for d in dev if d.family_id != fam], bundle, manifest
        )
        lofo[fam] = r.metrics["headline"]["confidence_intervals"]["statistics"]["level_macro_f1"][
            "point"
        ]
    hyb_errors = Counter(
        (d.family_id, d.gold_level, preds[d.doc_id]["hybrid"]["level"])
        for d in dev
        if d.tier in bundle.eval.headline_tiers
        and preds[d.doc_id]["hybrid"]["level"] != d.gold_level
    )
    meta = {
        "git_commit": git.get("commit"), "git_branch": git.get("branch"), "git_dirty": git.get("dirty"),
        "dataset_sha256": manifest.get("dataset_sha256"), "decisions_sha256": dec_sha,
        "splits_loaded": REVIEW_SPLITS, "locked_test_split_read": False,
        "rows": len(rows), "labels_changed": False,
    }  # fmt: skip
    report = render_report(bundle, rows, decisions, scen, lofo, hyb_errors, meta, preds, dev)
    return rows, report, meta


def render_report(bundle, rows, decisions, scen, lofo, hyb_errors, meta, preds, dev) -> str:
    L: list[str] = []
    add = L.append
    dev_rows = [r for r in rows if r["split"] == "dev"]
    by_type = Counter(r["disagreement_type"] for r in rows)
    fam_type: dict[str, str] = {}
    for r in rows:
        if r["disagreement_type"] != "none":
            fam_type.setdefault(r["family_id"], r["disagreement_type"])
    fam_counts = Counter(fam_type.values())
    s0, s1, s2 = (scen[k] for k in scen)
    add("# Gold-label review preparation (UC4)")
    add("")
    add(
        "> **Preliminary and AI-assisted; NOT human validation.** Dataset labels: **AI-generated synthetic dataset — pending human gold-label review.** This document proposes; it changes nothing. No gold label, taxonomy, schema, threshold, prompt, model configuration or the frozen hybrid configuration was modified, and the **locked test split was not read, scored or used.**"
    )
    add("")
    add("## Method and its limits")
    add("")
    add(
        "* Scope: every development-split document that a non-ML approach or the ML model disagreed with, plus all 107 dev documents. Train and calibration are reviewed only for the families where Rules disagreed (or, for one watch item, the same PUBLIC-without-marker pattern). LLM predictions exist for **dev only** (recorded); Rules and ML for all three development splits, and ML is in-sample on train."
    )
    add(
        "* Each disagreement was read (content, gold, every approach's prediction, the LLM rationales, the applicable taxonomy definitions) and classified into one of four types. The adjudications live in `data/synthetic/uc4/review/adjudication_decisions.yaml`; a disagreement with no adjudication makes the generator fail."
    )
    add(
        "* **Bias, twice over:** the reviewer is the same model family that authored the dataset, and saw the models' predictions before deciding. A human should review **blind** to the predictions. Agreement between approaches is weak evidence: the LLM prompt encodes the labeling guidelines that produced the gold."
    )
    add(
        "* Counterfactual numbers below apply proposed labels **in memory only**. Changing labels after seeing model disagreement on dev inflates dev results by construction; they show the sensitivity of the headline to labels, not improved performance."
    )
    add("")
    add("## Counts")
    add("")
    by_split = Counter(r["split"] for r in rows)
    add(_table(["measure", "value"], [
        ["samples reviewed (rows in the sheet)", f"{len(rows)} ({', '.join(f'{k} {v}' for k, v in sorted(by_split.items()))})"],
        ["dev documents reviewed", f"{len(dev_rows)} of {len(dev)}"],
        ["documents with any disagreement (excluding Rules abstention)", sum(bool(r["approaches_disagreeing_with_gold"]) for r in rows)],
        ["documents with a NON-ML disagreement", sum(bool(set(r["approaches_disagreeing_with_gold"].split(";")) - {"ml", ""}) for r in rows)],
        ["1. likely synthetic gold-label error (documents / families)", f"{by_type['gold_label_error']} / {fam_counts['gold_label_error']}"],
        ["2. taxonomy ambiguity (documents / families)", f"{by_type['taxonomy_ambiguity']} / {fam_counts['taxonomy_ambiguity']}"],
        ["3. genuine model error (documents / families)", f"{by_type['genuine_model_error']} / {fam_counts['genuine_model_error']}"],
        ["4. insufficient information / requires human decision (documents / families)", f"{by_type['insufficient_information']} / {fam_counts['insufficient_information']}"],
        ["documents with no disagreement", by_type["none"]],
        ["documents flagged taxonomy_ambiguity_flag (any type)", sum(r["taxonomy_ambiguity_flag"] is True for r in rows)],
        ["documents needing a human decision", sum(r["human_decision_required"] is True for r in rows)],
    ]))  # fmt: skip
    add("")
    add(
        "Disagreement of each approach with gold on the dev documents (level or categories; Rules abstentions are not disagreements):"
    )
    add("")
    add(
        _table(
            ["approach", "dev documents disagreeing"],
            [
                [k, sum(k in r["approaches_disagreeing_with_gold"].split(";") for r in dev_rows)]
                for k in APPROACHES
            ],
        )
    )
    add("")
    add("## Family adjudications")
    add("")
    add(_table(["priority", "family", "split", "type", "taxonomy flag", "label change proposed", "human decision"], [
        [d.priority, f"`{f}`", next((r["split"] for r in rows if r["family_id"] == f), "?"), d.disagreement_type, d.taxonomy_ambiguity_flag, d.label_change_proposed, d.human_decision_required]
        for f, d in sorted(decisions.families.items(), key=lambda kv: (kv[1].priority, kv[0]))
    ]))  # fmt: skip
    add("")
    add(
        "Full rationales, every approach's prediction and the LLM rationales are in `data/synthetic/uc4/review/adjudication_sheet.csv`."
    )
    add("")
    add("## The named family: `hn_public_api_docs_placeholder_keys`")
    add("")
    fam = [r for r in rows if r["family_id"] == "hn_public_api_docs_placeholder_keys"]
    add(
        f"{len(fam)} documents (one template). Gold: PUBLIC, no categories, ambiguity flag false. Predictions: Rules abstain (default Internal); ML Highly Confidential + Credentials (a keyword false positive; the family is a decoy for Credentials); `small` PUBLIC on 4 and INTERNAL on 1; `mid`, `large` and the hybrid INTERNAL on all 5."
    )
    add("")
    add(
        "**Assessment: likely gold-label error under the project's own rules, with a real taxonomy ambiguity behind it.** "
        + " ".join(
            decisions.families["hn_public_api_docs_placeholder_keys"].reviewer_rationale.split()
        )
    )
    add("")
    add("## Exact proposed changes (NONE APPLIED; each needs your approval)")
    add("")
    add(
        "**A. Label change (recommended, follows labeling guideline section 2).** For the five documents of `hn_public_api_docs_placeholder_keys` ("
        + ", ".join(f"`{r['sample_id']}`" for r in fam)
        + "):"
    )
    add("")
    add("| field | current | proposed |")
    add("|---|---|---|")
    add("| `gold_level` | PUBLIC | INTERNAL |")
    add("| `gold_categories` | [] | [] (unchanged) |")
    add("| `ambiguity_flag` | false | true |")
    add("| `acceptable_alternative_levels` | [] | [PUBLIC] |")
    add("")
    add(
        '*Alternative B (do not change the label):* keep PUBLIC and add an operational test to the taxonomy. The proposed wording, to append to the PUBLIC level description in `config/taxonomy/taxonomy.v1.yaml` **only if you choose B**: "Customer-facing product or developer documentation, and historical or teaching material about completed public events, is Public even without an explicit release marker." B would make the models\' INTERNAL answers errors by policy. Under either option the same decision settles `hn_business_case_study` (calibration).'
    )
    add("")
    add(
        '**B. Taxonomy clarification (no label change until decided).** In the PHI category description (`If a name is the only identifier, label PHI only; add PII when other direct identifiers also appear.`) append one of: (i) "A medical record number, employee id or similar record identifier counts as another direct identifier." (then `phi_prescription_record` becomes PHI + PII, 5 documents), or (ii) "A medical record number, employee id or similar record identifier does not count as another direct identifier." (then the gold stands and `small`/`large`\'s PII additions are errors).'
    )
    add("")
    add(
        "**C. Metadata-only (does not affect scoring; optional).** `amb_customer_case_study_draft`: add INTERNAL to `acceptable_alternative_levels` (currently [PUBLIC]) and decide whether the higher-level tie-break applies across a two-rank gap."
    )
    add("")
    add(
        "**D. No change proposed** for every other family: the gold is confirmed and the disagreement is a model error (Rules recall gaps, `small`'s over-classification, ML over-classification)."
    )
    add("")
    add("## Expected impact on evaluation (HYPOTHETICAL: labels applied in memory only)")
    add("")
    add(
        "Held-out dev, T1-T4 headline, family-level bootstrap intervals over the independent families:"
    )
    add("")
    rows_tab = []
    for sname, sc in scen.items():
        for a in APPROACHES:
            m = sc[a]
            rows_tab.append(
                [
                    sname,
                    a,
                    _ci(m["level"]),
                    _ci(m["category"]),
                    _f(m["hr_p"]),
                    _ci(m["hr_r"]),
                    _f(m["hr_fpr"]),
                ]
            )
    add(
        _table(
            [
                "scenario",
                "approach",
                "level macro-F1",
                "category macro-F1",
                "HR precision",
                "HR recall",
                "HR FPR",
            ],
            rows_tab,
        )
    )
    add("")
    add(
        "The PRD gates (level macro-F1 and category macro-F1 >= 0.85; high-risk recall >= 0.90) are informational on dev, and **dev chose the hybrid configuration**, so none of this is evidence for a gate."
    )
    add("")
    h0, h1, h2 = s0["hybrid"], s1["hybrid"], s2["hybrid"]
    add("## What drives the level macro-F1 lower bound of 0.631?")
    add("")
    add(
        f"For the frozen `default` hybrid the level macro-F1 is {_ci(h0['level'])} over {h0['n_families']} independent families."
    )
    add("")
    add(
        _table(
            ["hybrid level error (headline)", "gold", "predicted", "documents"],
            [[f"`{f}`", g, p, n] for (f, g, p), n in sorted(hyb_errors.items())],
        )
    )
    add("")
    total = sum(hyb_errors.values())
    add(
        f"* **All {total} of the hybrid's level errors on the headline documents are in one family** (`hn_public_api_docs_placeholder_keys`, 5 documents). Every other level decision is correct."
    )
    worst_fam, worst_v = min(
        ((f, v) for f, v in lofo.items() if f != "hn_public_api_docs_placeholder_keys"),
        key=lambda kv: kv[1],
    )
    named_v = lofo["hn_public_api_docs_placeholder_keys"]
    add(
        f"* Leave-one-family-out, the hybrid's level macro-F1 ranges from {_f(min(lofo.values()))} to {_f(max(lofo.values()))}: {_f(named_v)} without the named family, and {_f(worst_v)} at worst without another (`{worst_fam}`)."
    )
    head_tiers = set(bundle.eval.headline_tiers)
    public_now = sorted(
        {d.family_id for d in dev if d.gold_level == "PUBLIC" and d.tier in head_tiers}
    )
    moved = {
        f
        for f, dec in decisions.families.items()
        if dec.label_change_proposed and dec.proposed_gold_level != "PUBLIC"
    }
    public_after = [f for f in public_now if f not in moved]
    add(
        f"* **A second, independent fragility:** the PUBLIC class has gold support in only {len(public_now)} dev families ({', '.join(f'`{f}`' for f in public_now)}), and in {len(public_after)} after proposal A. Level macro-F1 averages over classes, so with so little PUBLIC support any level result on this split stays sensitive to that class whichever way the label is decided."
    )
    add(
        f"* The ambiguity cuts both ways: if the human decides that an MRN is an identifier (proposal B(i)), the hybrid's category macro-F1 falls from {_ci(h1['category'])} to {_ci(h2['category'])}."
    )
    add("")
    add(
        "**Conclusion (computed, not asserted).** The 0.631 lower bound is driven **primarily by one family whose PUBLIC label is disputable (a likely gold-label error with a genuine taxonomy ambiguity behind it), not by measured classifier weakness.** The classifier-performance problems are real but sit elsewhere: `small` over-classifies the IP research family and shows run-to-run inconsistency on identical templates, Rules miss Source Code in build files and PHI without clinical vocabulary, and ML over-classifies to Highly Confidential. Two cautions keep this from being a clean bill of health: (1) the interval is narrow after the change only because the hybrid makes no level error on the remaining 17 headline families, on a small, template-generated, AI-authored dataset that the LLM prompt was written against; (2) changing a label after seeing the models disagree is tuning on dev, so the confirmation must come from a blind human review and from data that did not choose the configuration."
    )
    add("")
    add("## How a human should proceed")
    add("")
    add(
        "1. Review `adjudication_sheet.csv` **blind**: read `content`, the definitions and the original gold first; fill `human_final_*` and `human_comment`, and only then look at the predictions and the proposals."
    )
    add(
        "2. Decide A/B (PUBLIC without a release marker), B (MRN), and C. Record `human_decision`, `human_reviewer`, `human_review_date` for each row."
    )
    add(
        "3. Only after that, and only with your approval, would a separate, audited step apply changes (with a version bump of the dataset and a re-run of the results). That step has not been written or run."
    )
    add("")
    add("## Provenance")
    add("")
    add(
        f"* dataset sha256 `{(meta['dataset_sha256'] or '')[:16]}...` (unchanged); decisions file sha256 `{meta['decisions_sha256'][:16]}...`; git `{(meta['git_commit'] or 'unknown')[:12]}` on `{meta['git_branch']}` (dirty: {meta['git_dirty']})"
    )
    add("* splits loaded: train, calibration, dev. **The locked test split was not read.**")
    add("* regenerate with `dataguard-uc4 review build`")
    add("")
    return "\n".join(L)
