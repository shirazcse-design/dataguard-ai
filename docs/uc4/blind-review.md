# Blind human-review package (gold-label validation)

> **AI-generated synthetic dataset — reviewed by one human (provenance per coordinator); second independent review pending.** Read-only preparation: nothing here changes a label, the taxonomy, schema v1.0, a prompt, a threshold, model selection or the frozen hybrid configuration. The locked test split is not read, scored or used; `data/synthetic/uc4/locked_test_access.jsonl` is 0 bytes.

## Purpose

After PR #8 the disputed labels need an *independent* human reading before anything is changed. The three-way comparison to run afterwards is **synthetic gold vs blind human label vs model predictions**. The blind reviewer must therefore not see the gold, any prediction, the proposed labels or the earlier adjudication.

## What the reviewer receives (`data/synthetic/uc4/review/blind/`, hand over only this directory)

| file | content |
|---|---|
| `blind_review_packet.md` | instructions, allowed labels, the taxonomy definitions and labeling rules **verbatim**, and the 33 documents (filename + text) |
| `blind_review_sheet.csv` | the response form: `sample_id`, `filename`, `content` and empty `human_level`, `human_categories`, `human_confidence`, `human_rationale`, `human_taxonomy_ambiguity`, `human_alternative_levels`, `human_insufficient_information`, `reviewer_id`, `review_date` |

The packet shows only filename and text (what the LLM and ML classifiers receive). Extension, embedded labels and metadata (`source_system`, `author_department`) are **not** shown; see decision D in the [decision brief](results/gold-review-decision-brief.md), because `source_system` is a public-release cue for one disputed family. Guidelines §§1–3 are included verbatim; §§4–7 (evidence spans, tiers, generator conventions, known limitations) are dataset-construction detail and are omitted.

## What the reviewer must NOT receive (`data/synthetic/uc4/review/blind_key/`)

`blind_review_key.csv` (role, family, gold labels per sample) and `blind_review_manifest.json` (seed, hashes, counts). Also not the rest of `review/` (adjudication sheet, decisions, family review sheet) or `docs/uc4/results/gold-review*.md`.

## Design

* **Review set (33 items):** all 21 documents of the four families whose adjudication needs a human decision (`hn_public_api_docs_placeholder_keys` 5, `phi_prescription_record` 5, `amb_customer_case_study_draft` 6, `hn_business_case_study` 5), plus **12 undisputed control documents** (3 per gold level, dev split, no adversarial T5 documents, one per family where possible). Controls exist so that being in the set is not itself a cue, and so reviewer calibration can be read on documents whose gold nobody questions. Ask for them to be dropped and the key already separates them.
* **Order:** shuffled deterministically (`sha256(seed:sample_id)`, seed `uc4-blind-review-v1`), so families are not clustered. Selection and order do not depend on library versions.
* **Leak audit:** the generator refuses to write a package whose reviewer-facing text contains a family id, an annotation note or a label/prediction column name (`leak_check`), and a test pins this.
* **Check a returned sheet:** `dataguard-uc4 review blind-check --sheet completed.csv` validates allowed values, required fields, unedited inputs and completeness. It does not judge whether a label is right.

## Comparing the returned results

```
dataguard-uc4 review blind-check   --sheet completed.csv
dataguard-uc4 review blind-compare --sheet completed.csv [--sheet second_reviewer.csv] [--out-dir DIR]
```

Writes `blind_review_comparison.md` and `blind_review_comparison.csv` (default `data/synthetic/uc4/review/blind_results/`; nothing else is written). Three-way: **synthetic gold vs blind human label vs each approach's prediction**.

* **Inputs are files only.** The returned sheet(s), the blind key, and the predictions *already executed* for the adjudication sheet. Nothing is re-run, no model is called, the dataset is not loaded, and the locked test split is not touched. The package is verified against its manifest first (key, reviewer sheet and adjudication artifacts must be the ones it was built from) and every sheet must pass `blind-check`; otherwise nothing is compared.
* **Per document (CSV):** gold, human label with confidence/ambiguity/insufficient flags, `level_human_eq_gold`, `cats_human_eq_gold`, a lenient level match (the human's level is in the gold's alternatives, or the gold's level is in the human's), and each approach's prediction with its pattern.
* **Patterns per document and approach**, on the level and the category set separately: `all agree`, `gold=human≠pred` (the human confirms the gold, the model is wrong), `human=pred≠gold` (the human sides with the model: evidence the gold may be wrong), `gold=pred≠human` (the human is the outlier), `all differ`. A Rules abstention or a document without a recorded LLM run is excluded, never treated as a label.
* **Report sections:** controls (reviewer calibration), disputed families (agreement, within-family consistency, ambiguity and insufficient-information flags), the two pattern tables, and the facts bearing on decisions A / B / C. With two or more sheets, inter-reviewer agreement and Cohen's kappa on level.
* **What it does not do:** no combined headline score, no bootstrap or re-scoring under human labels (that would be tuning on dev), and no label change. Counts only, with `SMALL_SAMPLE` on every table: the disputed set is four independent decisions.

## The metadata-shown variant

`data/synthetic/uc4/review/blind_metadata/` (reviewer) and `blind_metadata_key/` (key + manifest; not for the reviewer). It is a **separate package with the same 33 documents in a different order** (same set, different order seed), so a reviewer cannot rely on memory of positions and the two reviews can be compared sample by sample.

* **What is added:** one column/line, `source_metadata`, the document record's metadata map as `key=value; key=value` (for example `source_system=Public developer portal`; `author_department`, `source_path` where present). Extension is redundant with the filename; embedded labels are not shown because none of the 33 documents carries one.
* **What is not shown:** identical to the content-only package (no gold, predictions, proposals, adjudication). The same leak audit runs; a test pins that the content-only files still contain no `source_system`.
* **Framing:** the packet says metadata is evidence about where a document came from, never truth, and asks the reviewer to say how much weight it deserved.
* **Metadata correlates with the gold** (for example `Pharmacy system`, `Corporate website`, `Public developer portal`). That is the point of the variant, and it means this review is easier and less independent of the gold author's evidence than the content-only one. The classifiers never see it.
* **Who should review it:** ideally reviewers who have **not** seen the content-only package. If the same person does both, do the content-only pass first; their second pass is anchored on the first, and the report says so.

```
dataguard-uc4 review blind-package --variant metadata
dataguard-uc4 review blind-check   --variant metadata --sheet completed_metadata.csv
dataguard-uc4 review blind-compare --variant metadata --sheet completed_metadata.csv \
    [--content-sheet completed_content.csv]
```

Results default to `review/blind_results_metadata/`. With `--content-sheet` the report adds **"Effect of showing metadata"**: per disputed family, the human's labels in each variant, how many level/category labels changed, agreement with the gold in each variant, controls, and every changed document. It labels whether the two sheets are from the same reviewer (anchored) or different reviewers (person and metadata effects mixed). A content-only sheet is rejected by the metadata commands, and vice versa. The comparison reads only the returned sheets, the keys and the already-executed predictions; it never loads the dataset or the locked test split.

## When the reviewer is an AI model

```
dataguard-uc4 review blind-compare --sheet completed.csv --reviewer-kind ai
```

Labels the report ("AI REVIEW: NOT HUMAN VALIDATION") and adds a `reviewer_kind` column to the CSV, and
writes to `review/blind_results_ai/` so it never mixes with a human review's results. An AI sheet is a
second opinion only: it does not satisfy the human review requirement (A20), and it must not be used
to apply label changes A / B / C or to clear the MCP freeze criteria. The first such run is committed
under `data/synthetic/uc4/review/blind_results_ai/` (reviewer id in the sheet: `ChatGPT-GPT-5.6-Sol`).

## Round 2

A wider package (`review/blind_round2/`, variant `round2`) covers every ambiguity-flagged family in the
development splits plus the Round 1 disputed families, including `amb_aggregate_health_stats`. It is
compared with the gold and between reviewers only (no model columns). See
[`human-review-round2.md`](human-review-round2.md).

## Regenerate

```
dataguard-uc4 review blind-package                      # content-only
dataguard-uc4 review blind-package --variant metadata     # metadata shown
```

The committed package is tested to equal its generator's output, and the manifest records the sha256 of the (unchanged) adjudication artifacts, so a stale package or a changed adjudication sheet fails CI.

## Limits

* One reviewer is one opinion; two independent reviewers (and agreement between them) would be stronger.
* The reviewer applies the *same* taxonomy and guidelines that produced the gold, so agreement shows the rules are applied consistently, not that the rules are right.
* Within a disputed family the documents are near-identical template instances; the 21 disputed items are 4 independent decisions, not 21.
* The controls' gold is unreviewed AI-authored gold too; they calibrate the reviewer, they are not ground truth.
* A blind review that agrees with the gold does not remove the need for the locked-test decision; that has not been authorized.

## Not done (waiting for your decision)

No labels applied, no comparison run on real reviewer output (none exists yet), no re-evaluation, no locked-test evaluation.
