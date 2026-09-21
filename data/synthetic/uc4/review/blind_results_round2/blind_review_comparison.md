# Round 2 blind review: gold vs blind reviewer(s)

> **Provenance note (recorded verbatim at the operator's request):** Reviewer id 'Rahul'; the sheet is data/synthetic/uc4/review/returned/blind_review_round2_sheet.csv, uploaded by the coordinator on 2026-09-21 (received as blind_review_round2_sheet_Rahul_approved.csv; per the coordinator, 'approved' means the reviewer approved a draft he prepared himself). Checks by the analyst: the sheet is well-formed; no rationale is a duplicate of any earlier sheet (highest similarity 0.86, none >= 0.9; the Round 1 duplicates were 1.00). Whether the reviewer worked independently and without AI help is the coordinator's statement and cannot be verified from the file.

> **AI-generated synthetic dataset — reviewed by one human (provenance per coordinator); second independent review pending.** Read-only comparison. No label, taxonomy, schema, threshold, prompt, model configuration or the frozen hybrid configuration was changed, and the **locked test split was not read, scored or used**. There is no combined headline score.

## What this is and is not

* Inputs: the returned blind sheet(s) and the blind key. There are no model predictions here; nothing was re-run.
* Package: 28 items (16 review documents in 14 families, 12 controls), seed `uc4-blind-review-v1-round2`, dataset sha256 `284abad1247b53a3…`, taxonomy 1.0.0; integrity verified against the manifest. Development splits only.
* Every table is **SMALL_SAMPLE** (fewer than 25 per cell), and documents inside a family are near-identical template instances: each family is ONE independent decision.
* The reviewer applied the same taxonomy and guidelines that produced the gold: agreement shows consistent application of the rules, not that the rules are right. Where the reviewers or the gold disagree, a person with policy authority decides; this report only shows the facts.

Input files (sha256):

* `blind_review_round2_sheet.csv`: `7f78bde2dfcaa4248a29cec4745406550e4b646c5a189e40d05e894078b60f64`
* `review/blind_round2/blind_review_round2_sheet.csv`: `d9f0ef04506dd98b03dfae9ba9427c53dff429a65957c4e96f4cc18c77c7a7a9`
* `review/blind_round2_key/blind_review_key.csv`: `e01944c46adfb422151aff1cb24ab231adaa0e08ee1edbbb492371de43604a21`

## Reviewer `Rahul` (last review date 2026-09-21)

* documents: 28 (16 review documents in 14 families, 12 controls)
* confidence: high ×17, medium ×11
* flagged taxonomy ambiguity: 8; insufficient information: 0; no level given: 0

### Controls (undisputed documents; their gold is AI-authored too) (SMALL_SAMPLE)

Level agreement 12/12; category-set agreement 12/12.

### Review families: reviewer vs gold (SMALL_SAMPLE)

| family | docs | gold | gold alternatives | reviewer labels | level = gold | lenient | cats = gold | ambiguity yes |
|---|---|---|---|---|---|---|---|---|
| `amb_aggregate_health_stats` | 3 | CONFIDENTIAL (no categories) | HIGHLY_CONFIDENTIAL;INTERNAL | INTERNAL (no categories) ×3 | 0/3 | 3/3 | 3/3 | 0/3 |
| `amb_board_minutes_routine` | 1 | HIGHLY_CONFIDENTIAL (no categories) | CONFIDENTIAL | CONFIDENTIAL (no categories) ×1 | 0/1 | 1/1 | 1/1 | 1/1 |
| `amb_conference_talk_draft` | 1 | CONFIDENTIAL (INTELLECTUAL_PROPERTY) | INTERNAL | CONFIDENTIAL (INTELLECTUAL_PROPERTY) ×1 | 1/1 | 1/1 | 1/1 | 1/1 |
| `amb_customer_case_study_draft` | 1 | CONFIDENTIAL (no categories) | PUBLIC;INTERNAL | CONFIDENTIAL (no categories) ×1 | 1/1 | 1/1 | 1/1 | 1/1 |
| `amb_employee_directory` | 1 | CONFIDENTIAL (no categories) | INTERNAL | INTERNAL (no categories) ×1 | 0/1 | 1/1 | 1/1 | 0/1 |
| `amb_hashed_password_dump` | 1 | HIGHLY_CONFIDENTIAL (no categories) | CONFIDENTIAL | CONFIDENTIAL (no categories) ×1 | 0/1 | 1/1 | 1/1 | 1/1 |
| `amb_incident_postmortem_internal` | 1 | CONFIDENTIAL (no categories) | INTERNAL | CONFIDENTIAL (no categories) ×1 | 1/1 | 1/1 | 1/1 | 1/1 |
| `amb_internal_tooling_source` | 1 | CONFIDENTIAL (SOURCE_CODE) | INTERNAL | CONFIDENTIAL (SOURCE_CODE) ×1 | 1/1 | 1/1 | 1/1 | 1/1 |
| `amb_research_paper_draft` | 1 | CONFIDENTIAL (INTELLECTUAL_PROPERTY) | INTERNAL | CONFIDENTIAL (INTELLECTUAL_PROPERTY) ×1 | 1/1 | 1/1 | 1/1 | 0/1 |
| `amb_salary_bands` | 1 | CONFIDENTIAL (no categories) | INTERNAL | CONFIDENTIAL (no categories) ×1 | 1/1 | 1/1 | 1/1 | 1/1 |
| `amb_vendor_contract_summary` | 1 | CONFIDENTIAL (no categories) | INTERNAL | CONFIDENTIAL (no categories) ×1 | 1/1 | 1/1 | 1/1 | 0/1 |
| `hn_business_case_study` | 1 | PUBLIC (no categories) | INTERNAL | INTERNAL (no categories) ×1 | 0/1 | 1/1 | 1/1 | 1/1 |
| `hn_public_api_docs_placeholder_keys` | 1 | PUBLIC (no categories) | - | PUBLIC (no categories) ×1 | 1/1 | 1/1 | 1/1 | 0/1 |
| `phi_prescription_record` | 1 | HIGHLY_CONFIDENTIAL (PHI) | - | HIGHLY_CONFIDENTIAL (PHI) ×1 | 1/1 | 1/1 | 1/1 | 0/1 |

## Focus: `amb_aggregate_health_stats` (de-identified aggregate patient statistics with small cell counts)

Gold (as of this run's key): CONFIDENTIAL (no categories), acceptable alternatives HIGHLY_CONFIDENTIAL;INTERNAL. The frozen hybrid predicted INTERNAL for this family in the calibration check. The reviewers' own words, verbatim, so the policy owner can decide a small-cell rule:

**Reviewer `Rahul`**

| sample | level | categories | confidence | ambiguity | alternative levels | rationale |
|---|---|---|---|---|---|---|
| uc4-fb41e91266 | INTERNAL | none | medium | no | - | The health outcomes are aggregated and explicitly contain no identifiers, so PHI does not apply; no public-release intent is stated. |
| uc4-205126d683 | INTERNAL | none | medium | no | - | The document explicitly describes aggregated, non-identifiable health outcomes, so PHI does not apply and Internal is the default. |
| uc4-14696d668c | INTERNAL | none | medium | no | - | The clinic statistics are aggregated and explicitly de-identified; without public-release intent, Internal is appropriate. |

## Limits

* One or two readers cannot separate a dataset problem from personal idiosyncrasy; three or more would.
* The four ambiguous families of the locked test split are not in this package by design (the generator refuses locked-split documents); they need a separate, explicitly authorised post-hoc review.
* Nothing here is a decision. A relabel after seeing model errors on a split is tuning on that split; the decision log must say what evidence each change rests on.
