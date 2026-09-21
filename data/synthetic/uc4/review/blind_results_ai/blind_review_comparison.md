# AI review: gold vs blind reviewer vs model predictions

> **AI REVIEW: NOT HUMAN VALIDATION.** The blind sheet was completed by an AI model, not a person. Every 'human' column and word below means 'the blind reviewer', here an AI model (see `reviewer_id`). This is a second, independent AI opinion on the same taxonomy. It does NOT satisfy the human gold-label review requirement (decision A20), and it must not be used to apply label or taxonomy changes A / B / C or to clear the MCP freeze criteria.

> **AI-generated synthetic dataset — pending human gold-label review.** Read-only comparison. No label, taxonomy, schema v1.0, threshold, prompt, model configuration or the frozen hybrid configuration was changed, and the **locked test split was not read, scored or used**. There is no combined headline score.

## What this is and is not

* Inputs: the returned blind sheet(s), the blind key, and the predictions already executed for the adjudication sheet (nothing was re-run).
* Package: 33 items (21 disputed, 12 controls), seed `uc4-blind-review-v1`, dataset sha256 `bc86537c2cc23e5f…`, taxonomy 1.0.0; integrity verified against the manifest.
* Every table is **SMALL_SAMPLE** (fewer than 25 per cell), and the disputed set is 4 independent decisions, not 21: counts are shown, not rates with intervals.
* The reviewer is an AI model that applied the same taxonomy and guidelines that produced the gold: agreement shows consistent application of the rules, not that the rules are right, and a model can share a reading with the classifiers that a person would not. A human decision is required before any label or taxonomy change.
* One AI model is one opinion, and it was not run under controlled conditions (prompt, model version and any tool use are not recorded here); it cannot separate a dataset problem from that model's idiosyncrasy.

Input files (sha256):

* `blind_review_sheet.csv`: `f0ac66747acb699d316c1b6eadb8af0b9aeb5bb3c8c84ffc65cf620392218ac7`
* `review/blind/blind_review_sheet.csv`: `c3a6f6851b2cf1908e342007fa4d00f687471c6a781c7964f00586924e930d9e`
* `review/blind_key/blind_review_key.csv`: `d73b22083db2718078dd2ece3e28f26743751c3449e925cb09236d7fcc6a65ad`

## Reviewer `ChatGPT-GPT-5.6-Sol` (last review date 2026-09-20)

* documents: 33 (21 disputed in 4 families, 12 controls)
* human confidence: high ×22, medium ×11
* flagged taxonomy ambiguity: 11; flagged insufficient information: 0; no level given: 0

### Controls: human vs synthetic gold (SMALL_SAMPLE)

Undisputed documents; their gold is AI-authored too, so this is calibration of the reviewer (and a check on the gold), not ground truth. Level agreement 11/12; category-set agreement 11/12; lenient level match 11/12.

| sample | family | gold | human | confidence |
|---|---|---|---|---|
| uc4-fb63604243 | ts_supplier_yield_terms | HIGHLY_CONFIDENTIAL (TRADE_SECRET) | CONFIDENTIAL (no categories) | high |

### Disputed families: human vs synthetic gold (SMALL_SAMPLE)

Documents inside a family are near-identical template instances: each family is ONE independent decision.

| family | n | gold | human labels | level = gold | cats = gold | lenient level | ambiguity yes | insufficient | consistent |
|---|---|---|---|---|---|---|---|---|---|
| `amb_customer_case_study_draft` | 6 | CONFIDENTIAL (no categories) | CONFIDENTIAL (no categories) ×6 | 6/6 | 6/6 | 6/6 | 6/6 | 0/6 | yes |
| `hn_business_case_study` | 5 | PUBLIC (no categories) | INTERNAL (no categories) ×3, PUBLIC (no categories) ×2 | 2/5 | 5/5 | 5/5 | 5/5 | 0/5 | NO |
| `hn_public_api_docs_placeholder_keys` | 5 | PUBLIC (no categories) | PUBLIC (no categories) ×5 | 5/5 | 5/5 | 5/5 | 0/5 | 0/5 | yes |
| `phi_prescription_record` | 5 | HIGHLY_CONFIDENTIAL (PHI) | HIGHLY_CONFIDENTIAL (PHI) ×5 | 5/5 | 5/5 | 5/5 | 0/5 | 0/5 | yes |

`consistent` = the reviewer gave every document of the family the same label.

### Gold vs human vs prediction: sensitivity level (disputed documents) (SMALL_SAMPLE)

Counts of documents per pattern; only documents where the approach produced a label and the human gave a level are counted (Rules abstentions and calibration documents without recorded LLM runs are excluded).

| family | approach | n | all agree | gold=human≠pred | human=pred≠gold | gold=pred≠human | all differ |
|---|---|---|---|---|---|---|---|
| `amb_customer_case_study_draft` | ml | 6 | 0 | 6 | 0 | 0 | 0 |
| `amb_customer_case_study_draft` | llm_small | 6 | 6 | 0 | 0 | 0 | 0 |
| `amb_customer_case_study_draft` | llm_mid | 6 | 6 | 0 | 0 | 0 | 0 |
| `amb_customer_case_study_draft` | llm_large | 6 | 6 | 0 | 0 | 0 | 0 |
| `amb_customer_case_study_draft` | hybrid | 6 | 6 | 0 | 0 | 0 | 0 |
| `hn_business_case_study` | ml | 5 | 0 | 2 | 0 | 0 | 3 |
| `hn_public_api_docs_placeholder_keys` | ml | 5 | 0 | 5 | 0 | 0 | 0 |
| `hn_public_api_docs_placeholder_keys` | llm_small | 5 | 4 | 1 | 0 | 0 | 0 |
| `hn_public_api_docs_placeholder_keys` | llm_mid | 5 | 0 | 5 | 0 | 0 | 0 |
| `hn_public_api_docs_placeholder_keys` | llm_large | 5 | 0 | 5 | 0 | 0 | 0 |
| `hn_public_api_docs_placeholder_keys` | hybrid | 5 | 0 | 5 | 0 | 0 | 0 |
| `phi_prescription_record` | rules | 5 | 5 | 0 | 0 | 0 | 0 |
| `phi_prescription_record` | ml | 5 | 5 | 0 | 0 | 0 | 0 |
| `phi_prescription_record` | llm_small | 5 | 5 | 0 | 0 | 0 | 0 |
| `phi_prescription_record` | llm_mid | 5 | 5 | 0 | 0 | 0 | 0 |
| `phi_prescription_record` | llm_large | 5 | 5 | 0 | 0 | 0 | 0 |
| `phi_prescription_record` | hybrid | 5 | 5 | 0 | 0 | 0 | 0 |

### Gold vs human vs prediction: category set (disputed documents) (SMALL_SAMPLE)

Counts of documents per pattern; only documents where the approach produced a label and the human gave a level are counted (Rules abstentions and calibration documents without recorded LLM runs are excluded).

| family | approach | n | all agree | gold=human≠pred | human=pred≠gold | gold=pred≠human | all differ |
|---|---|---|---|---|---|---|---|
| `amb_customer_case_study_draft` | ml | 6 | 6 | 0 | 0 | 0 | 0 |
| `amb_customer_case_study_draft` | llm_small | 6 | 6 | 0 | 0 | 0 | 0 |
| `amb_customer_case_study_draft` | llm_mid | 6 | 6 | 0 | 0 | 0 | 0 |
| `amb_customer_case_study_draft` | llm_large | 6 | 6 | 0 | 0 | 0 | 0 |
| `amb_customer_case_study_draft` | hybrid | 6 | 6 | 0 | 0 | 0 | 0 |
| `hn_business_case_study` | ml | 5 | 5 | 0 | 0 | 0 | 0 |
| `hn_public_api_docs_placeholder_keys` | ml | 5 | 0 | 5 | 0 | 0 | 0 |
| `hn_public_api_docs_placeholder_keys` | llm_small | 5 | 5 | 0 | 0 | 0 | 0 |
| `hn_public_api_docs_placeholder_keys` | llm_mid | 5 | 5 | 0 | 0 | 0 | 0 |
| `hn_public_api_docs_placeholder_keys` | llm_large | 5 | 5 | 0 | 0 | 0 | 0 |
| `hn_public_api_docs_placeholder_keys` | hybrid | 5 | 5 | 0 | 0 | 0 | 0 |
| `phi_prescription_record` | rules | 5 | 5 | 0 | 0 | 0 | 0 |
| `phi_prescription_record` | ml | 5 | 5 | 0 | 0 | 0 | 0 |
| `phi_prescription_record` | llm_small | 5 | 3 | 2 | 0 | 0 | 0 |
| `phi_prescription_record` | llm_mid | 5 | 5 | 0 | 0 | 0 | 0 |
| `phi_prescription_record` | llm_large | 5 | 3 | 2 | 0 | 0 | 0 |
| `phi_prescription_record` | hybrid | 5 | 5 | 0 | 0 | 0 | 0 |

How to read the patterns: `gold=human≠pred` = the human confirms the gold and the model is wrong against both; `human=pred≠gold` = the human sides with the model, evidence the gold may be wrong (or that the model and the human share a reading the gold author did not); `gold=pred≠human` = the human is the outlier; `all differ` = three different labels.

### Evidence for decisions A / B / C (facts only, not a decision) (SMALL_SAMPLE)

**A. PUBLIC vs INTERNAL without a release marker in the text** (`hn_public_api_docs_placeholder_keys`, 5 documents, one independent decision)

* synthetic gold: PUBLIC (no categories); gold alternatives: none
* human labels: PUBLIC (no categories) ×5
* human ambiguity flagged: 0 of 5; alternative levels named: -; insufficient information: 0 of 5
* rationale(s): “The API quickstart uses placeholders and documented example credentials, not live secrets, and is presented as public product documentation.” / “The SDK quickstart uses placeholders and documented example keys rather than live credentials, and is presented as public-facing documentation.” / “The authentication quickstart contains only placeholders and documented example keys, which the taxonomy excludes from Credentials / Secrets, and is public-style documentation.” …

**A (follow-on). same rule, calibration family** (`hn_business_case_study`, 5 documents, one independent decision)

* synthetic gold: PUBLIC (no categories); gold alternatives: none
* human labels: INTERNAL (no categories) ×3, PUBLIC (no categories) ×2
* human ambiguity flagged: 5 of 5; alternative levels named: PUBLIC ×3, INTERNAL ×2; insufficient information: 0 of 5
* rationale(s): “The document is framed as a historical teaching case about a completed 2010 merger, which matches the taxonomy's historical/public case-study counterexample for M&A.” / “The filename and content frame this as a historical teaching case about a completed 2006 merger, matching the historical/public case-study counterexample for M&A.” / “This is a historical case study about a completed 2007 merger, so M&A / Corporate Strategy does not apply. The text does not explicitly establish public-release status, so Internal is the default.” …

**B. does an MRN count as another direct identifier (PII)?** (`phi_prescription_record`, 5 documents, one independent decision)

* synthetic gold: HIGHLY_CONFIDENTIAL (PHI); gold alternatives: none
* human labels: HIGHLY_CONFIDENTIAL (PHI) ×5
* documents where the human added PII: 0 of 5
* human ambiguity flagged: 0 of 5; alternative levels named: -; insufficient information: 0 of 5
* rationale(s): “The pharmacy record links a named patient/MRN to medication and diagnosis. This is PHI and therefore Highly Confidential.” / “The pharmacy record links a named patient/MRN to medication and diagnosis. This meets PHI and carries a Highly Confidential floor.” / “The prescription record links an identifiable patient/MRN to medication and diagnosis. This is individually identifiable health information and therefore PHI.”

**C. alternative levels for a draft customer story** (`amb_customer_case_study_draft`, 6 documents, one independent decision)

* synthetic gold: CONFIDENTIAL (no categories); gold alternatives: PUBLIC
* human labels: CONFIDENTIAL (no categories) ×6
* human ambiguity flagged: 6 of 6; alternative levels named: INTERNAL ×6; insufficient information: 0 of 6
* rationale(s): “The customer story is a draft awaiting approval and therefore is not yet approved for public release. No defined sensitive-data category clearly applies.” / “The customer story is a draft awaiting customer approval and is not yet approved for public release. No defined category clearly applies.” / “The customer story is a draft awaiting customer approval, so it is not yet approved for public release. No defined category clearly applies.” …
