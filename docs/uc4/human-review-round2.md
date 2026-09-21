# Round 2 human review: coordinator's guide

> **AI-generated synthetic dataset — reviewed by one human (provenance per coordinator); second independent review pending.** This guide is for whoever runs the review. It is **not** for the reviewers: they receive only `data/synthetic/uc4/review/blind_round2/`. Nothing in the package changes a label; a label changes only through a recorded decision after adjudication.

## Why a Round 2

Round 1 (`blind-review.md`) covered four disputed families, and its returned sheet turned out to be identical to an AI-completed sheet, so **no independent human has reviewed any gold label yet**. Round 2 widens the scope to the families where the gold is a policy tie-break and where the classifiers' errors sit, and it includes `amb_aggregate_health_stats` (all 6 calibration high-risk misses), which was never in a blind package.

## What is in the package

28 documents: **16 review documents** in 14 families plus **12 undisputed controls**, shuffled with a fixed seed. One document per family, except `amb_aggregate_health_stats` (3, because their small-cell counts differ). Development splits only (train, calibration, dev): the locked test split is not read.

* `amb_aggregate_health_stats`
* `amb_board_minutes_routine`
* `amb_conference_talk_draft`
* `amb_customer_case_study_draft`
* `amb_employee_directory`
* `amb_hashed_password_dump`
* `amb_incident_postmortem_internal`
* `amb_internal_tooling_source`
* `amb_research_paper_draft`
* `amb_salary_bands`
* `amb_vendor_contract_summary`
* `hn_business_case_study`
* `hn_public_api_docs_placeholder_keys`
* `phi_prescription_record`

The four ambiguous families of the locked test split are **not** included (`amb_ticket_first_names_only`, `amb_unpublished_policy_draft`, `amb_acquisition_rumor_notes`, `amb_product_roadmap_high_level`). The generator refuses locked-split documents by design; they need a separate, explicitly authorised, clearly labelled post-hoc package.

## Choose the reviewers

* **Two people**, not one: two allow an agreement check (Cohen's kappa). Three or more are better still.
* Independent of the dataset's authorship; ideally DLP, privacy or compliance experience.
* **No AI model and no pattern scanner**, no looking at any gold, prediction or earlier sheet, and **no discussion between reviewers until both have submitted**.
* About 1 to 1.5 hours each.

## Hand over exactly this

`data/synthetic/uc4/review/blind_round2/` (2 files: `blind_review_round2_packet.md` and `blind_review_round2_sheet.csv`). Send a copy of that folder only.

**Never hand over or expose:** `review/blind_round2_key/` (key and manifest), the rest of `review/`, `docs/uc4/results/`, or `docs/uc4/labeling-guidelines.md` section 8 (the post-Round-1 clarifications, which would steer the reviewers; the packet deliberately shows sections 1 to 3 only).

## Getting the sheets back

1. Each reviewer fills in `blind_review_round2_sheet.csv` (their own `reviewer_id`, a YYYY-MM-DD date) and returns it.
2. Save each under `data/synthetic/uc4/review/returned/` (for example `round2.<reviewer>.csv`) and record its provenance in that folder's `README.md`.
3. **Never save a returned sheet into `blind_round2/`** (that folder must stay blank; the package-integrity tests fail if it changes), and do not upload completed sheets into the package folder through the GitHub web UI.

## Check and compare

```
dataguard-uc4 review blind-check   --variant round2 --sheet returned/round2.a.csv
dataguard-uc4 review blind-check   --variant round2 --sheet returned/round2.b.csv
dataguard-uc4 review blind-compare --variant round2 --sheet returned/round2.a.csv --sheet returned/round2.b.csv
```

`blind-check` validates format only. `blind-compare` writes `review/blind_results_round2/blind_review_comparison.{md,csv}` (nothing else is written): per-reviewer agreement with the gold, controls, a per-document table with a plain-language reading (`both agree with gold`, `both agree with each other, differ from gold`, `reviewers differ`), inter-reviewer agreement with kappa, and the reviewers' own rationales for `amb_aggregate_health_stats`. There are no model columns (no predictions were executed for these documents), and no combined score. If a sheet was AI-completed, add `--reviewer-kind ai`; if a sheet's provenance needs recording, add `--note "..."`.

## Adjudicate (a person with policy authority, not the analyst)

1. Where both reviewers agree with the gold: no change.
2. Where both agree with each other and differ from the gold, or where they differ: the policy owner decides using the labeling guidelines, and records the decision in `decisions.md` with the evidence it rests on.
3. `amb_aggregate_health_stats` needs an explicit small-cell rule. The candidates are HIGHLY_CONFIDENTIAL (current gold), CONFIDENTIAL (the gold's alternative) and INTERNAL (the frozen hybrid's answer). Statistical offices often suppress cell counts below 11; that is a convention to weigh, not a rule of this project.
4. A change is made in the family spec, the dataset is regenerated, and the tests are run. **A relabel after seeing model errors on a split is tuning on that split**; say so in the decision and re-score under both label sets rather than replacing the old numbers.

## The dataset label

After the first reviewer's sheet the product owner accepted one reviewer as sufficient for recording the
review (decision A29), so the label reads **"reviewed by one human (provenance per coordinator); second
independent review pending"**. It drops "second independent review pending" only after a second
independent human has reviewed the labels and every disagreement is adjudicated and recorded. A suggested
bar for that second review (a decision for the product owner, not a project rule): Cohen's kappa of at
least 0.8 on level for the non-ambiguous documents, and every disagreement with the gold either changed or
explained in `decisions.md`.

## Regenerating the package

```
dataguard-uc4 review blind-package --variant round2
```

The committed package is tested to equal its generator's output, and the manifest records the hashes of the reviewer files and the key.
