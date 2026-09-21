# Gold-label review preparation (UC4)

> **Preliminary and AI-assisted; NOT human validation.** Dataset labels: **AI-generated synthetic dataset — pending human gold-label review.** This document proposes; it changes nothing. No gold label, taxonomy, schema, threshold, prompt, model configuration or the frozen hybrid configuration was modified, and the **locked test split was not read, scored or used.**

## Method and its limits

* Scope: every development-split document that a non-ML approach or the ML model disagreed with, plus all 107 dev documents. Train and calibration are reviewed only for the families where Rules disagreed (or, for one watch item, the same PUBLIC-without-marker pattern). LLM predictions exist for **dev only** (recorded); Rules and ML for all three development splits, and ML is in-sample on train.
* Each disagreement was read (content, gold, every approach's prediction, the LLM rationales, the applicable taxonomy definitions) and classified into one of four types. The adjudications live in `data/synthetic/uc4/review/adjudication_decisions.yaml`; a disagreement with no adjudication makes the generator fail.
* **Bias, twice over:** the reviewer is the same model family that authored the dataset, and saw the models' predictions before deciding. A human should review **blind** to the predictions. Agreement between approaches is weak evidence: the LLM prompt encodes the labeling guidelines that produced the gold.
* Counterfactual numbers below apply proposed labels **in memory only**. Changing labels after seeing model disagreement on dev inflates dev results by construction; they show the sensitivity of the headline to labels, not improved performance.

## Counts

| measure | value |
|---|---|
| samples reviewed (rows in the sheet) | 127 (calibration 10, dev 107, train 10) |
| dev documents reviewed | 107 of 107 |
| documents with any disagreement (excluding Rules abstention) | 115 |
| documents with a NON-ML disagreement | 37 |
| 1. likely synthetic gold-label error (documents / families) | 5 / 1 |
| 2. taxonomy ambiguity (documents / families) | 10 / 2 |
| 3. genuine model error (documents / families) | 95 / 19 |
| 4. insufficient information / requires human decision (documents / families) | 5 / 1 |
| documents with no disagreement | 12 |
| documents flagged taxonomy_ambiguity_flag (any type) | 27 |
| documents needing a human decision | 20 |

Disagreement of each approach with gold on the dev documents (level or categories; Rules abstentions are not disagreements):

| approach | dev documents disagreeing |
|---|---|
| rules | 5 |
| ml | 89 |
| llm_small | 11 |
| llm_mid | 5 |
| llm_large | 7 |
| hybrid | 5 |

## Family adjudications

| priority | family | split | type | taxonomy flag | label change proposed | human decision |
|---|---|---|---|---|---|---|
| P1 | `hn_public_api_docs_placeholder_keys` | dev | gold_label_error | True | True | True |
| P2 | `hn_business_case_study` | calibration | insufficient_information | True | False | True |
| P2 | `phi_prescription_record` | dev | taxonomy_ambiguity | True | False | True |
| P3 | `amb_customer_case_study_draft` | dev | taxonomy_ambiguity | True | False | True |
| P3 | `cred_ci_pipeline_token` | dev | genuine_model_error | False | False | False |
| P3 | `hn_blank_hr_form_template` | dev | genuine_model_error | False | False | False |
| P3 | `hn_confidential_word_menu` | train | genuine_model_error | False | False | False |
| P3 | `ip_research_notebook_novel_method` | dev | genuine_model_error | True | False | False |
| P3 | `phi_discharge_summary` | calibration | genuine_model_error | False | False | False |
| P3 | `phi_immunization_registry` | train | genuine_model_error | False | False | False |
| P3 | `ts_supplier_yield_terms` | dev | genuine_model_error | True | False | False |

Full rationales, every approach's prediction and the LLM rationales are in `data/synthetic/uc4/review/adjudication_sheet.csv`.

## The named family: `hn_public_api_docs_placeholder_keys`

5 documents (one template). Gold: PUBLIC, no categories, ambiguity flag false. Predictions: Rules abstain (default Internal); ML Highly Confidential + Credentials (a keyword false positive; the family is a decoy for Credentials); `small` PUBLIC on 4 and INTERNAL on 1; `mid`, `large` and the hybrid INTERNAL on all 5.

**Assessment: likely gold-label error under the project's own rules, with a real taxonomy ambiguity behind it.** The text is customer-facing developer documentation (a quickstart with placeholder and vendor-documented example keys), so no CREDENTIALS_SECRETS category applies and the categories are correctly empty. The open question is the LEVEL. Labeling guideline section 2 allows PUBLIC only if the document is "explicitly intended for, or already in, public release". Nothing in the text says it was released or is to be released (there is no publication date, no "published" or "public" marker, no PUBLIC label, no licence); "the vendor's own documentation" describes where other keys appear, not this file's status. Every other PUBLIC-gold family in the development splits carries an explicit public marker in its text (a publication date, "(published)", "announced publicly", "our public careers page", an MIT licence header, a PUBLIC label); this one has none. The same guideline's tie-break says that when adjacent levels are both defensible the gold is the HIGHER level, flagged ambiguous with the lower level recorded as an acceptable alternative. The gold (PUBLIC, ambiguity_flag false, no alternative) therefore departs from the project's own rule. The models agree it is uncertain: `small` answers PUBLIC on 4 of 5 identical templates and INTERNAL on the fifth, and `mid`, `large` and the hybrid answer INTERNAL on all 5 (large reports medium confidence, saying PUBLIC intent is not explicit).

## Exact proposed changes (NONE APPLIED; each needs your approval)

**A. Label change (recommended, follows labeling guideline section 2).** For the five documents of `hn_public_api_docs_placeholder_keys` (`uc4-00ac341854`, `uc4-02400f99b0`, `uc4-10e1c66f39`, `uc4-35ffba48e4`, `uc4-9d42fc85c9`):

| field | current | proposed |
|---|---|---|
| `gold_level` | PUBLIC | INTERNAL |
| `gold_categories` | [] | [] (unchanged) |
| `ambiguity_flag` | false | true |
| `acceptable_alternative_levels` | [] | [PUBLIC] |

*Alternative B (do not change the label):* keep PUBLIC and add an operational test to the taxonomy. The proposed wording, to append to the PUBLIC level description in `config/taxonomy/taxonomy.v1.yaml` **only if you choose B**: "Customer-facing product or developer documentation, and historical or teaching material about completed public events, is Public even without an explicit release marker." B would make the models' INTERNAL answers errors by policy. Under either option the same decision settles `hn_business_case_study` (calibration).

**B. Taxonomy clarification (no label change until decided).** In the PHI category description (`If a name is the only identifier, label PHI only; add PII when other direct identifiers also appear.`) append one of: (i) "A medical record number, employee id or similar record identifier counts as another direct identifier." (then `phi_prescription_record` becomes PHI + PII, 5 documents), or (ii) "A medical record number, employee id or similar record identifier does not count as another direct identifier." (then the gold stands and `small`/`large`'s PII additions are errors).

**C. Metadata-only (does not affect scoring; optional).** `amb_customer_case_study_draft`: add INTERNAL to `acceptable_alternative_levels` (currently [PUBLIC]) and decide whether the higher-level tie-break applies across a two-rank gap.

**D. No change proposed** for every other family: the gold is confirmed and the disagreement is a model error (Rules recall gaps, `small`'s over-classification, ML over-classification).

## Expected impact on evaluation (HYPOTHETICAL: labels applied in memory only)

Held-out dev, T1-T4 headline, family-level bootstrap intervals over the independent families:

| scenario | approach | level macro-F1 | category macro-F1 | HR precision | HR recall | HR FPR |
|---|---|---|---|---|---|---|
| S0 current gold | rules | 0.358 [0.222, 0.592] | 0.522 [0.333, 0.757] | 1.000 | 0.623 [0.333, 0.917] | 0.000 |
| S0 current gold | ml | 0.232 [0.134, 0.392] | 0.433 [0.257, 0.628] | 0.551 | 0.925 [0.745, 1.000] | 0.909 |
| S0 current gold | llm_small | 0.918 [0.787, 1.000] | 0.932 [0.881, 1.000] | 0.883 | 1.000 [1.000, 1.000] | 0.159 |
| S0 current gold | llm_mid | 0.870 [0.631, 1.000] | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] | 0.000 |
| S0 current gold | llm_large | 0.870 [0.631, 1.000] | 0.990 [0.958, 1.000] | 1.000 | 1.000 [1.000, 1.000] | 0.000 |
| S0 current gold | hybrid | 0.870 [0.631, 1.000] | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] | 0.000 |
| S1 apply the proposed label change | rules | 0.386 [0.247, 0.682] | 0.522 [0.333, 0.757] | 1.000 | 0.623 [0.333, 0.917] | 0.000 |
| S1 apply the proposed label change | ml | 0.270 [0.170, 0.379] | 0.433 [0.257, 0.628] | 0.551 | 0.925 [0.745, 1.000] | 0.909 |
| S1 apply the proposed label change | llm_small | 0.836 [0.644, 1.000] | 0.932 [0.881, 1.000] | 0.883 | 1.000 [1.000, 1.000] | 0.159 |
| S1 apply the proposed label change | llm_mid | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] | 0.000 |
| S1 apply the proposed label change | llm_large | 1.000 [1.000, 1.000] | 0.990 [0.958, 1.000] | 1.000 | 1.000 [1.000, 1.000] | 0.000 |
| S1 apply the proposed label change | hybrid | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] | 0.000 |
| S2 = S1 + MRN counts as an identifier | rules | 0.386 [0.247, 0.682] | 0.522 [0.333, 0.700] | 1.000 | 0.623 [0.333, 0.917] | 0.000 |
| S2 = S1 + MRN counts as an identifier | ml | 0.270 [0.170, 0.379] | 0.433 [0.257, 0.595] | 0.551 | 0.925 [0.745, 1.000] | 0.909 |
| S2 = S1 + MRN counts as an identifier | llm_small | 0.836 [0.644, 1.000] | 0.930 [0.875, 1.000] | 0.883 | 1.000 [1.000, 1.000] | 0.159 |
| S2 = S1 + MRN counts as an identifier | llm_mid | 1.000 [1.000, 1.000] | 0.977 [0.857, 1.000] | 1.000 | 1.000 [1.000, 1.000] | 0.000 |
| S2 = S1 + MRN counts as an identifier | llm_large | 1.000 [1.000, 1.000] | 0.987 [0.939, 1.000] | 1.000 | 1.000 [1.000, 1.000] | 0.000 |
| S2 = S1 + MRN counts as an identifier | hybrid | 1.000 [1.000, 1.000] | 0.977 [0.857, 1.000] | 1.000 | 1.000 [1.000, 1.000] | 0.000 |

The PRD gates (level macro-F1 and category macro-F1 >= 0.85; high-risk recall >= 0.90) are informational on dev, and **dev chose the hybrid configuration**, so none of this is evidence for a gate.

## What drives the level macro-F1 lower bound of 0.631?

For the frozen `default` hybrid the level macro-F1 is 0.870 [0.631, 1.000] over 18 independent families.

| hybrid level error (headline) | gold | predicted | documents |
|---|---|---|---|
| `hn_public_api_docs_placeholder_keys` | PUBLIC | INTERNAL | 5 |

* **All 5 of the hybrid's level errors on the headline documents are in one family** (`hn_public_api_docs_placeholder_keys`, 5 documents). Every other level decision is correct.
* Leave-one-family-out, the hybrid's level macro-F1 ranges from 0.704 to 1.000: 1.000 without the named family, and 0.704 at worst without another (`pub_careers_faq`).
* **A second, independent fragility:** the PUBLIC class has gold support in only 2 dev families (`hn_public_api_docs_placeholder_keys`, `pub_careers_faq`), and in 1 after proposal A. Level macro-F1 averages over classes, so with so little PUBLIC support any level result on this split stays sensitive to that class whichever way the label is decided.
* The ambiguity cuts both ways: if the human decides that an MRN is an identifier (proposal B(i)), the hybrid's category macro-F1 falls from 1.000 [1.000, 1.000] to 0.977 [0.857, 1.000].

**Conclusion (computed, not asserted).** The 0.631 lower bound is driven **primarily by one family whose PUBLIC label is disputable (a likely gold-label error with a genuine taxonomy ambiguity behind it), not by measured classifier weakness.** The classifier-performance problems are real but sit elsewhere: `small` over-classifies the IP research family and shows run-to-run inconsistency on identical templates, Rules miss Source Code in build files and PHI without clinical vocabulary, and ML over-classifies to Highly Confidential. Two cautions keep this from being a clean bill of health: (1) the interval is narrow after the change only because the hybrid makes no level error on the remaining 17 headline families, on a small, template-generated, AI-authored dataset that the LLM prompt was written against; (2) changing a label after seeing the models disagree is tuning on dev, so the confirmation must come from a blind human review and from data that did not choose the configuration.

## How a human should proceed

1. Review `adjudication_sheet.csv` **blind**: read `content`, the definitions and the original gold first; fill `human_final_*` and `human_comment`, and only then look at the predictions and the proposals.
2. Decide A/B (PUBLIC without a release marker), B (MRN), and C. Record `human_decision`, `human_reviewer`, `human_review_date` for each row.
3. Only after that, and only with your approval, would a separate, audited step apply changes (with a version bump of the dataset and a re-run of the results). That step has not been written or run.

## Provenance

* dataset sha256 `9442e354c3dd51dd...` (unchanged); decisions file sha256 `b3f22ebf5c3045cc...`; git `b7e94ac130fa` on `uc4/blind-review-metadata-variant` (dirty: True)
* splits loaded: train, calibration, dev. **The locked test split was not read.**
* regenerate with `dataguard-uc4 review build`
