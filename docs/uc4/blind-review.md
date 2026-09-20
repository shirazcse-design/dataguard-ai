# Blind human-review package (gold-label validation)

> **AI-generated synthetic dataset — pending human gold-label review.** Read-only preparation: nothing here changes a label, the taxonomy, schema v1.0, a prompt, a threshold, model selection or the frozen hybrid configuration. The locked test split is not read, scored or used; `data/synthetic/uc4/locked_test_access.jsonl` is 0 bytes.

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

## Regenerate

```
dataguard-uc4 review blind-package
```

The committed package is tested to equal its generator's output, and the manifest records the sha256 of the (unchanged) adjudication artifacts, so a stale package or a changed adjudication sheet fails CI.

## Limits

* One reviewer is one opinion; two independent reviewers (and agreement between them) would be stronger.
* The reviewer applies the *same* taxonomy and guidelines that produced the gold, so agreement shows the rules are applied consistently, not that the rules are right.
* Within a disputed family the documents are near-identical template instances; the 21 disputed items are 4 independent decisions, not 21.
* The controls' gold is unreviewed AI-authored gold too; they calibrate the reviewer, they are not ground truth.
* A blind review that agrees with the gold does not remove the need for the locked-test decision; that has not been authorized.

## Not done (waiting for your decision)

No labels applied, no comparison run, no re-evaluation, no locked-test evaluation.
