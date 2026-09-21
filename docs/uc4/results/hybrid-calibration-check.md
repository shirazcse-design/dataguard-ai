# Hybrid: out-of-sample check on the calibration split

> **AI-generated synthetic dataset — pending human gold-label review.** The locked test split was **not** read (`locked_test_access.jsonl` is 0 bytes). Nothing was tuned, relabelled or re-configured for this run.

## What was run

The frozen hybrid variant `default` (Rules -> LLM mid -> LLM large only if needed) on the **calibration** split, live against the Foundry deployments and recorded for replay (`eval run --classifier hybrid --hybrid-variant default --split calibration --llm-mode record`), 2026-09-21. The variant, thresholds and prompts were chosen on **dev**; this split did not choose them (`fit_splits` and `calibration_splits` are empty for this variant), so unlike the dev results it is an out-of-sample check. It is still synthetic, template-generated, AI-labelled data, and the labels are unreviewed.

Recorded responses: 103 new `uc4-llm-medium` entries under `data/llm_cache/` (served model `gpt-5.4-2026-03-05`). The large tier was never called (every document was accepted at `mid`). 103 documents, 19 families; the headline covers 98 documents in 18 families (T1-T4).

## Result

| metric | calibration (this run) | dev (frozen variant, for comparison) |
|---|---|---|
| level macro-F1 | 0.867 [0.717, 1.000] | 0.870 [0.631, 1.000] |
| category macro-F1 | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] |
| high-risk recall | **0.891** [0.647, 1.000] (49 of 55) | 1.000 [1.000, 1.000] |
| high-risk precision | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] |
| high-risk false-positive rate | 0.000 | 0.000 |
| review rate | 0.000 | 0.000 |

Intervals are family-level bootstraps over 18 independent families. Against the PRD targets: level macro-F1 point PASS (>= 0.85) but the lower bound **FAILS** (0.717); category macro-F1 PASS / PASS; high-risk recall point **0.891 is below the 0.90 reference** (informational, never a gate on its own).

## What drives the misses (all 9 headline level errors are in two families)

| family | documents | gold | hybrid | note |
|---|---|---|---|---|
| `amb_aggregate_health_stats` | 6 | HIGHLY_CONFIDENTIAL (no categories; alternative CONFIDENTIAL) | INTERNAL | **All 6 high-risk misses.** Small-cell, de-identified patient statistics. The gold is a fail-safe tie-break to the higher level (guidelines section 2) of an ambiguous case; the taxonomy itself lists "aggregated, de-identified patient statistics" as a PHI counter-example. The hybrid put them **two ranks below the gold and below its acceptable alternative**. |
| `hn_business_case_study` | 3 of 5 | PUBLIC | INTERNAL | The same family as decision A (a historical merger teaching case). The reviewer sheet for it was itself inconsistent (INTERNAL x3, PUBLIC x2). |

Without the `amb_aggregate_health_stats` family, high-risk recall on this split is 49 of 49. The recall shortfall is therefore one ambiguous family, not spread across the data, but it is also exactly the failure a data-loss tool should not have: a re-identifiable health report classified Internal.

## What this does and does not show

* The dev result was optimistic in the expected way: level F1 is about the same, but high-risk recall dropped from 1.000 to 0.891 on data that did not choose the variant.
* **Not a conclusion about the labels.** Whether `amb_aggregate_health_stats` should be HIGHLY_CONFIDENTIAL, CONFIDENTIAL or INTERNAL is a policy question that has not been reviewed by a human (this family was not in the blind-review package). No label was changed in response to this result; changing it now would be tuning on a split we just looked at.
* No provider failures, no schema-invalid responses, no review escalations.
* This does not clear the eval-gates freeze criterion for the MCP adapter: the level-F1 lower bound still fails, and the high-risk recall point estimate is now below the reference.

## Generated run report (verbatim)

The remainder is the run's own `report.md`, unedited. The run artifacts themselves live under `evals/classification/runs/` (git-ignored); the recorded LLM responses are committed and replayable without credentials.

---

## Evaluation run `hybrid-calibration-20260921T070112Z-1c9a3cd5`

> Every number below was computed by this run from the classifier's actual outputs. There is no combined headline score; each metric stands alone.

## Provenance

* **dataset labels: AI-generated synthetic dataset — pending human gold-label review** (not independently human-validated)
* classifier: `hybrid` v1.0.0 params `{"calibration_splits": [], "few_shot_doc_ids": ["uc4-815df87cff", "uc4-6557052089", "uc4-208535806a", "uc4-310a0dd682", "uc4-12ec39f4fd", "uc4-10f4c1c40d", "uc4-23bdd468ad", "uc4-0f7af28770", "uc4-11cc75b15f", "uc4-290d3a979d", "uc4-07a2a15e56", "uc4-23efa45a39", "uc4-24f4890ccf"], "fit_splits": [], "routing_config_sha256": "95768d4123c3ccb1c8c942e0bbf6d3e1a3e9375b1c7f6486fc43fbdb5b122ea3", "routing_version": "1.0.0", "stages": {"llm:large": {"api": "responses", "model_id": "uc4-llm-large", "prompt_sha256": "44404a014cc703c8aafca7c7452f1c9fedfa9499f1b9c4a3ff270ebd98dca8df", "prompt_version": "classifier.v1"}, "llm:mid": {"api": "chat_completions", "model_id": "uc4-llm-medium", "prompt_sha256": "44404a014cc703c8aafca7c7452f1c9fedfa9499f1b9c4a3ff270ebd98dca8df", "prompt_version": "classifier.v1"}, "rules": {"name": "rules", "version": "1.0.3"}}, "variant": "default", "variant_config": {"budget": {"max_llm_calls": 3}, "conflict": {"level_rank_gap": 2}, "fusion": {"category_floors": true, "rules_floor": true}, "injection": {"restrict_downgrade": true}, "llm": {"accept_abstention": false, "min_confidence": "medium", "tier_order": ["mid", "large"]}, "ml": {"enabled": false, "short_circuit": true, "tau": 0.7}, "rules": {"enabled": true, "min_level_strength": "strong", "short_circuit": false}}}`
* dataset: `dataguard-uc4-synthetic` v1.0.0 sha256 `9442e354c3dd51dd...`
* splits evaluated: calibration (103 documents, 19 families)
* git: `a75970fbd9db` on `uc4/blind-review-metadata-variant` (dirty: True)
* config versions: {'taxonomy': '1.0.0', 'high_risk': '1.0.0', 'eval': '1.0.0'}
* metrics fingerprint: `1c9a3cd5a552693f183734e434577b066536262b66f170cd76525c60bbc85d25`

## Coverage and failures

| documents | with prediction | failed | review required | review rate | auto-decided | abstained (rate) |
|---|---|---|---|---|---|---|
| 103 | 103 | 0 | 0 | 0.000 | 103 | 0 (0.000) |

## Headline metrics (tiers T1, T2, T3, T4; 98 documents, 18 families)

Intervals are 95% percentile bootstrap intervals from 1000 resamples (seed 20260918) of **groups**. **They rest on 18 independent families** (98 documents), because documents within a family are variations of one template and are correlated. Read the intervals, not just the point estimates.

| metric | value [95% CI] |
|---|---|
| Sensitivity level: macro-F1 | 0.867 [0.717, 1.000] |
| Data categories: macro-F1 | 1.000 [1.000, 1.000] |
| High-risk: recall | 0.891 [0.647, 1.000] |
| High-risk: precision | 1.000 [1.000, 1.000] |
| High-risk: F1 | 0.942 [0.786, 1.000] |
| High-risk: false-positive rate | 0.000 [0.000, 0.000] |
| Review rate (headline documents) | 0.000 |

Initial safety-oriented reference: high-risk recall >= 0.90; observed 0.891 (below the reference). **This is informational, not a pass/fail gate:** recall alone does not determine success, and no minimum precision or false-positive threshold has been chosen yet. That operating point will be selected on the development set; never tune against the locked test split.

Macro convention: macro = unweighted mean over labels with gold support > 0 in the evaluated subset; an undefined precision of a supported label counts as 0.

## Sample-size warnings (SMALL_SAMPLE: fewer than 25 gold positives)

| axis | label | gold positives (headline subset) | warning |
|---|---|---|---|
| level | PUBLIC | 10 | SMALL_SAMPLE |
| level | INTERNAL | 11 | SMALL_SAMPLE |
| category | PII | 11 | SMALL_SAMPLE |
| category | PHI | 10 | SMALL_SAMPLE |
| category | FINANCIAL_PCI | 11 | SMALL_SAMPLE |
| category | SOURCE_CODE | 11 | SMALL_SAMPLE |
| category | CREDENTIALS_SECRETS | 5 | SMALL_SAMPLE |
| category | INTELLECTUAL_PROPERTY | 11 | SMALL_SAMPLE |
| category | TRADE_SECRET | 10 | SMALL_SAMPLE |
| category | MA_CORP_STRATEGY | 7 | SMALL_SAMPLE |

Per-label counts are preserved in the `support` columns of the tables below.

### Sensitivity level

Confusion matrix (rows = gold, columns = predicted):

| gold \ predicted | PUBLIC | INTERNAL | CONFIDENTIAL | HIGHLY_CONFIDENTIAL | NO_PREDICTION |
|---|---|---|---|---|---|
| PUBLIC | 7 | 3 | 0 | 0 | 0 |
| INTERNAL | 0 | 11 | 0 | 0 | 0 |
| CONFIDENTIAL | 0 | 0 | 28 | 0 | 0 |
| HIGHLY_CONFIDENTIAL | 0 | 6 | 0 | 43 | 0 |

| level | precision | recall | F1 | support | predicted | warning |
|---|---|---|---|---|---|---|
| PUBLIC | 1.000 | 0.700 | 0.824 | 10 | 7 | SMALL_SAMPLE |
| INTERNAL | 0.550 | 1.000 | 0.710 | 11 | 20 | SMALL_SAMPLE |
| CONFIDENTIAL | 1.000 | 1.000 | 1.000 | 28 | 28 |  |
| HIGHLY_CONFIDENTIAL | 1.000 | 0.878 | 0.935 | 49 | 43 |  |

macro P/R/F1: 0.887 / 0.894 / 0.867; micro F1 0.908; accuracy 0.908. Under-classification 0.061 (severe 0.061), over-classification 0.031.

### Data categories

| category | precision | recall | F1 | support | TP | FP | FN | TN | warning |
|---|---|---|---|---|---|---|---|---|---|
| PII | 1.000 | 1.000 | 1.000 | 11 | 11 | 0 | 0 | 87 | SMALL_SAMPLE |
| PHI | 1.000 | 1.000 | 1.000 | 10 | 10 | 0 | 0 | 88 | SMALL_SAMPLE |
| FINANCIAL_PCI | 1.000 | 1.000 | 1.000 | 11 | 11 | 0 | 0 | 87 | SMALL_SAMPLE |
| SOURCE_CODE | 1.000 | 1.000 | 1.000 | 11 | 11 | 0 | 0 | 87 | SMALL_SAMPLE |
| CREDENTIALS_SECRETS | 1.000 | 1.000 | 1.000 | 5 | 5 | 0 | 0 | 93 | SMALL_SAMPLE |
| INTELLECTUAL_PROPERTY | 1.000 | 1.000 | 1.000 | 11 | 11 | 0 | 0 | 87 | SMALL_SAMPLE |
| TRADE_SECRET | 1.000 | 1.000 | 1.000 | 10 | 10 | 0 | 0 | 88 | SMALL_SAMPLE |
| MA_CORP_STRATEGY | 1.000 | 1.000 | 1.000 | 7 | 7 | 0 | 0 | 91 | SMALL_SAMPLE |

macro P/R/F1: 1.000 / 1.000 / 1.000; micro F1 1.000; exact-match 1.000.

### High-risk (derived from the configured definition)

| TP | FP | FN | TN | precision | recall | F1 | FPR | prevalence |
|---|---|---|---|---|---|---|---|---|
| 49 | 0 | 6 | 43 | 1.000 | 0.891 | 0.942 | 0.000 | 0.561 |

### Deferrals to human review

No documents were deferred; all scoring views are identical.

## Hard negatives (T4)

10 hard-negative documents in 2 families. **Decoy-hit rate 0.000** (a decoy hit = the prediction contains the category or level the document merely resembles); any false-positive category 0.000; predicted high-risk although not 0.000.

## Slices

Computed over all tiers (so T5 appears here). Macro-F1 covers only labels with support in the slice; slices with fewer documents than the threshold are marked SMALL_SAMPLE.

**by tier**

| tier | docs | families | level macro-F1 | category macro-F1 | high-risk recall | high-risk precision | high-risk FPR | warning |
|---|---|---|---|---|---|---|---|---|
| T1 | 51 | 10 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 |  |
| T2 | 25 | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 |  |
| T3 | 12 | 2 | 0.500 | 1.000 | 0.000 | n/a | 0.000 | SMALL_SAMPLE |
| T4 | 10 | 2 | 0.670 | n/a | n/a | n/a | 0.000 | SMALL_SAMPLE |
| T5 | 5 | 1 | 1.000 | 1.000 | 1.000 | 1.000 | n/a | SMALL_SAMPLE |

**by format**

| format | docs | families | level macro-F1 | category macro-F1 | high-risk recall | high-risk precision | high-risk FPR | warning |
|---|---|---|---|---|---|---|---|---|
| blog_post | 5 | 1 | 1.000 | n/a | n/a | n/a | 0.000 | SMALL_SAMPLE |
| clinical_note | 5 | 1 | 1.000 | 1.000 | 1.000 | 1.000 | n/a | SMALL_SAMPLE |
| config_file | 10 | 2 | 1.000 | 1.000 | 1.000 | 1.000 | n/a | SMALL_SAMPLE |
| csv_table | 17 | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | SMALL_SAMPLE |
| draft | 12 | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | SMALL_SAMPLE |
| legal_draft | 5 | 1 | 1.000 | 1.000 | n/a | n/a | 0.000 | SMALL_SAMPLE |
| meeting_notes | 6 | 1 | 1.000 | n/a | n/a | n/a | 0.000 | SMALL_SAMPLE |
| message | 5 | 1 | 1.000 | 1.000 | 1.000 | 1.000 | n/a | SMALL_SAMPLE |
| notes | 6 | 1 | 1.000 | 1.000 | 1.000 | 1.000 | n/a | SMALL_SAMPLE |
| report | 11 | 2 | 0.286 | n/a | 0.000 | n/a | 0.000 | SMALL_SAMPLE |
| source_code | 11 | 2 | 1.000 | 1.000 | n/a | n/a | 0.000 | SMALL_SAMPLE |
| statement | 5 | 1 | 1.000 | 1.000 | 1.000 | 1.000 | n/a | SMALL_SAMPLE |
| technical_doc | 5 | 1 | 1.000 | 1.000 | 1.000 | 1.000 | n/a | SMALL_SAMPLE |

**by generator**

| generator | docs | families | level macro-F1 | category macro-F1 | high-risk recall | high-risk precision | high-risk FPR | warning |
|---|---|---|---|---|---|---|---|---|
| template-v1 | 103 | 19 | 0.869 | 1.000 | 0.900 | 1.000 | 0.000 |  |

**by ambiguity_flag**

| ambiguity_flag | docs | families | level macro-F1 | category macro-F1 | high-risk recall | high-risk precision | high-risk FPR | warning |
|---|---|---|---|---|---|---|---|---|
| False | 91 | 17 | 0.926 | 1.000 | 1.000 | 1.000 | 0.000 |  |
| True | 12 | 2 | 0.500 | 1.000 | 0.000 | n/a | 0.000 | SMALL_SAMPLE |

## Latency (wall clock per document; not part of the reproducible fingerprint)

| n | mean ms | p50 ms | p95 ms | max ms | total ms |
|---|---|---|---|---|---|
| 103 | 2147.444 | 2150.884 | 2686.869 | 3142.110 | 221186.733 |

## Caveats

* Dataset labels: AI-generated synthetic dataset — pending human gold-label review. Nothing here is human-validated.
* Documents in a family are variations of one template; treat families as the sample size.
* Precision and accuracy depend on this dataset's class balance, not production base rates.
