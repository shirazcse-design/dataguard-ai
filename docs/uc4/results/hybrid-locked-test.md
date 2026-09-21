# Hybrid: locked-test evaluation (report-only)

> **AI-generated synthetic dataset — pending human gold-label review.** This is the single audited evaluation of the frozen hybrid on the locked test split, run once on 2026-09-21. **Report-only: nothing here may be used to tune rules, thresholds, prompts, models or labels.** The access is recorded in `data/synthetic/uc4/locked_test_access.jsonl` (run `hybrid-test-20260921T071856Z-789a620e`).

## What was run and what was frozen

`eval run --classifier hybrid --hybrid-variant default --split test --allow-locked-test --llm-mode record`, live against the Foundry deployments (medium `gpt-5.4-2026-03-05`; large used for escalations). Frozen at commit `89bcdbe`: dataset `9442e354c3dd51dd…` (after decisions A26-A28), routing config `95768d41…` (identical to the calibration run), the `default` variant, prompt `classifier.v1`. Before the run the working tree was clean and the access log was empty. The run's own warning "working tree has uncommitted changes" refers to the recorded LLM responses it was writing under `data/llm_cache/`, which are committed with this report and replay without credentials.

Caveats that apply to every number below: the test split is **synthetic, template-generated and AI-labelled with unreviewed gold**; the sheet designated as the human review is identical to an AI sheet, so the human-review criterion is unmet; the headline rests on 37 independent families.

## Result (headline = tiers T1-T4: 212 documents, 37 families)

| metric | locked test [95% CI] | calibration (earlier check) | dev (chose the variant) |
|---|---|---|---|
| level macro-F1 | **0.884** [0.758, 0.980] | 0.867 [0.717, 1.000] | 0.870 [0.631, 1.000] |
| category macro-F1 | **0.997** [0.989, 1.000] | 1.000 | 1.000 |
| high-risk recall | **1.000** [1.000, 1.000] (111 of 111) | 0.891 (49 of 55) | 1.000 |
| high-risk precision | 1.000 | 1.000 | 1.000 |
| high-risk false-positive rate | 0.000 | 0.000 | 0.000 |
| review rate | 0.019 (4 of 212) | 0.000 | 0.000 |

Gates (PRD MVP targets; point estimate / lower bound): level macro-F1 **PASS / FAIL** (0.884 >= 0.85, but the lower bound 0.758 < 0.85); category macro-F1 **PASS / PASS**; high-risk recall **PASS / PASS** (informational, never sufficient alone).

## Where the errors are

* **All 19 headline level errors are one pattern in four ambiguous (T3) families**, each a gold `CONFIDENTIAL` predicted `INTERNAL`, one rank low: `amb_ticket_first_names_only` (6), `amb_unpublished_policy_draft` (5), `amb_acquisition_rumor_notes` (5), `amb_product_roadmap_high_level` (3). The gold for these is the fail-safe tie-break to the higher of two defensible levels (guidelines section 2), and the model picks the lower one. The same direction appeared on dev and calibration (`amb_aggregate_health_stats`, `hn_business_case_study`).
* **No severe under-classification** (none two or more ranks below gold) and **no high-risk miss** on this split.
* **One category-set error:** `ip_ts_algorithm_disclosure` predicted `TRADE_SECRET` and omitted `INTELLECTUAL_PROPERTY`.
* **4 documents routed to review**, all in `ip_ts_process_patent_pending` (`DETECTOR_CONFLICT`); they are also the only documents that escalated to the large tier, and were left for a human, as designed.
* **Adversarial (T5, 15 documents, reported separately, outside the headline):** every level was correct, and the injection guard flagged all 15.

## Latency

P50 2.1 s and P95 2.7 s per document, but **4 documents exceeded the PRD's 10 s tool-call limit**: the same four `ip_ts_process_patent_pending` documents that escalated to the large tier and were routed to review (84.6, 143.5, 206.3 and 244.8 s). This is the same limit the MCP adapter avoids by capping the LLM tier at `mid`; a call that escalates to `large` would not meet it.

## What this does and does not show

* It is the honest, first evaluation on data that neither chose the variant nor was looked at before. The picture is consistent with dev and calibration: high-risk recall is strong here (calibration's 0.891 does not recur), category quality is near-perfect, and level macro-F1 sits at about 0.87-0.88 with a lower bound that does not clear 0.85.
* The level shortfall is concentrated in ambiguous families where the gold itself is a policy tie-break. Whether those golds are right is a **human policy question** that no result here can answer. Changing them (or the model) after seeing this split would be tuning on the test set, so nothing was changed.
* **MCP freeze criteria:** the audited confirmation on data that did not choose the configuration is now **done**. The eval-gates criterion is still **NOT MET** (level lower bound 0.758), and the human gold-label review is still **not satisfied**.
* Not evaluated here: cost (no prices supplied), real data, or any documents outside the synthetic families.

## Generated run report (verbatim)

The remainder is the run's own `report.md`, unedited. Run artifacts stay under `evals/classification/runs/` (git-ignored); the recorded responses are committed.

---

## Evaluation run `hybrid-test-20260921T071856Z-789a620e`

> **LOCKED TEST SPLIT EVALUATED - REPORT-ONLY.** Authorised via `--allow-locked-test` at 2026-09-21T07:18:56.746991+00:00. These results must not be used to tune rules, thresholds, prompts or models.

> Every number below was computed by this run from the classifier's actual outputs. There is no combined headline score; each metric stands alone.

## Provenance

* **dataset labels: AI-generated synthetic dataset — pending human gold-label review** (not independently human-validated)
* classifier: `hybrid` v1.0.0 params `{"calibration_splits": [], "few_shot_doc_ids": ["uc4-815df87cff", "uc4-6557052089", "uc4-208535806a", "uc4-310a0dd682", "uc4-12ec39f4fd", "uc4-10f4c1c40d", "uc4-23bdd468ad", "uc4-0f7af28770", "uc4-11cc75b15f", "uc4-290d3a979d", "uc4-07a2a15e56", "uc4-23efa45a39", "uc4-24f4890ccf"], "fit_splits": [], "routing_config_sha256": "95768d4123c3ccb1c8c942e0bbf6d3e1a3e9375b1c7f6486fc43fbdb5b122ea3", "routing_version": "1.0.0", "stages": {"llm:large": {"api": "responses", "model_id": "uc4-llm-large", "prompt_sha256": "44404a014cc703c8aafca7c7452f1c9fedfa9499f1b9c4a3ff270ebd98dca8df", "prompt_version": "classifier.v1"}, "llm:mid": {"api": "chat_completions", "model_id": "uc4-llm-medium", "prompt_sha256": "44404a014cc703c8aafca7c7452f1c9fedfa9499f1b9c4a3ff270ebd98dca8df", "prompt_version": "classifier.v1"}, "rules": {"name": "rules", "version": "1.0.3"}}, "variant": "default", "variant_config": {"budget": {"max_llm_calls": 3}, "conflict": {"level_rank_gap": 2}, "fusion": {"category_floors": true, "rules_floor": true}, "injection": {"restrict_downgrade": true}, "llm": {"accept_abstention": false, "min_confidence": "medium", "tier_order": ["mid", "large"]}, "ml": {"enabled": false, "short_circuit": true, "tau": 0.7}, "rules": {"enabled": true, "min_level_strength": "strong", "short_circuit": false}}}`
* dataset: `dataguard-uc4-synthetic` v1.0.0 sha256 `9442e354c3dd51dd...`
* splits evaluated: test (227 documents, 40 families)
* git: `89bcdbe08d47` on `uc4/blind-review-metadata-variant` (dirty: True)
* config versions: {'taxonomy': '1.0.0', 'high_risk': '1.0.0', 'eval': '1.0.0'}
* metrics fingerprint: `789a620ea0c70950155b102ef8d98e48435672176db2950a696c58b1531c6bd7`

## Coverage and failures

| documents | with prediction | failed | review required | review rate | auto-decided | abstained (rate) |
|---|---|---|---|---|---|---|
| 227 | 227 | 0 | 4 | 0.018 | 223 | 0 (0.000) |

## Headline metrics (tiers T1, T2, T3, T4; 212 documents, 37 families)

Intervals are 95% percentile bootstrap intervals from 1000 resamples (seed 20260918) of **groups**. **They rest on 37 independent families** (212 documents), because documents within a family are variations of one template and are correlated. Read the intervals, not just the point estimates.

| metric | value [95% CI] |
|---|---|
| Sensitivity level: macro-F1 | 0.884 [0.758, 0.980] |
| Data categories: macro-F1 | 0.997 [0.989, 1.000] |
| High-risk: recall | 1.000 [1.000, 1.000] |
| High-risk: precision | 1.000 [1.000, 1.000] |
| High-risk: F1 | 1.000 [1.000, 1.000] |
| High-risk: false-positive rate | 0.000 [0.000, 0.000] |
| Review rate (headline documents) | 0.019 |

Initial safety-oriented reference: high-risk recall >= 0.90; observed 1.000 (at or above the reference). **This is informational, not a pass/fail gate:** recall alone does not determine success, and no minimum precision or false-positive threshold has been chosen yet. That operating point will be selected on the development set; never tune against the locked test split.

Macro convention: macro = unweighted mean over labels with gold support > 0 in the evaluated subset; an undefined precision of a supported label counts as 0.

## Sample-size warnings (SMALL_SAMPLE: fewer than 25 gold positives)

| axis | label | gold positives (headline subset) | warning |
|---|---|---|---|
| level | PUBLIC | 22 | SMALL_SAMPLE |
| category | PII | 23 | SMALL_SAMPLE |
| category | PHI | 18 | SMALL_SAMPLE |
| category | FINANCIAL_PCI | 18 | SMALL_SAMPLE |
| category | SOURCE_CODE | 18 | SMALL_SAMPLE |
| category | CREDENTIALS_SECRETS | 23 | SMALL_SAMPLE |
| category | INTELLECTUAL_PROPERTY | 23 | SMALL_SAMPLE |
| category | TRADE_SECRET | 24 | SMALL_SAMPLE |
| category | MA_CORP_STRATEGY | 23 | SMALL_SAMPLE |

Per-label counts are preserved in the `support` columns of the tables below.

### Sensitivity level

Confusion matrix (rows = gold, columns = predicted):

| gold \ predicted | PUBLIC | INTERNAL | CONFIDENTIAL | HIGHLY_CONFIDENTIAL | NO_PREDICTION |
|---|---|---|---|---|---|
| PUBLIC | 22 | 0 | 0 | 0 | 0 |
| INTERNAL | 0 | 26 | 0 | 0 | 0 |
| CONFIDENTIAL | 0 | 19 | 39 | 0 | 0 |
| HIGHLY_CONFIDENTIAL | 0 | 0 | 0 | 106 | 0 |

| level | precision | recall | F1 | support | predicted | warning |
|---|---|---|---|---|---|---|
| PUBLIC | 1.000 | 1.000 | 1.000 | 22 | 22 | SMALL_SAMPLE |
| INTERNAL | 0.578 | 1.000 | 0.732 | 26 | 45 |  |
| CONFIDENTIAL | 1.000 | 0.672 | 0.804 | 58 | 39 |  |
| HIGHLY_CONFIDENTIAL | 1.000 | 1.000 | 1.000 | 106 | 106 |  |

macro P/R/F1: 0.894 / 0.918 / 0.884; micro F1 0.910; accuracy 0.910. Under-classification 0.090 (severe 0.000), over-classification 0.000.

### Data categories

| category | precision | recall | F1 | support | TP | FP | FN | TN | warning |
|---|---|---|---|---|---|---|---|---|---|
| PII | 1.000 | 1.000 | 1.000 | 23 | 23 | 0 | 0 | 189 | SMALL_SAMPLE |
| PHI | 1.000 | 1.000 | 1.000 | 18 | 18 | 0 | 0 | 194 | SMALL_SAMPLE |
| FINANCIAL_PCI | 1.000 | 1.000 | 1.000 | 18 | 18 | 0 | 0 | 194 | SMALL_SAMPLE |
| SOURCE_CODE | 1.000 | 1.000 | 1.000 | 18 | 18 | 0 | 0 | 194 | SMALL_SAMPLE |
| CREDENTIALS_SECRETS | 1.000 | 1.000 | 1.000 | 23 | 23 | 0 | 0 | 189 | SMALL_SAMPLE |
| INTELLECTUAL_PROPERTY | 1.000 | 0.957 | 0.978 | 23 | 22 | 0 | 1 | 189 | SMALL_SAMPLE |
| TRADE_SECRET | 1.000 | 1.000 | 1.000 | 24 | 24 | 0 | 0 | 188 | SMALL_SAMPLE |
| MA_CORP_STRATEGY | 1.000 | 1.000 | 1.000 | 23 | 23 | 0 | 0 | 189 | SMALL_SAMPLE |

macro P/R/F1: 1.000 / 0.995 / 0.997; micro F1 0.997; exact-match 0.995.

### High-risk (derived from the configured definition)

| TP | FP | FN | TN | precision | recall | F1 | FPR | prevalence |
|---|---|---|---|---|---|---|---|---|
| 111 | 0 | 0 | 101 | 1.000 | 1.000 | 1.000 | 0.000 | 0.524 |

### Deferrals to human review

4 documents were deferred. Primary metrics score deferred documents on their provisional label. Alternative views:

| view | level macro-F1 | category macro-F1 | high-risk recall | documents |
|---|---|---|---|---|
| auto_only | 0.884 | 0.997 | 1.000 | 208 |
| deferred_as_errors | 0.879 | 0.973 | 0.964 | 212 |
| deferred_resolved_by_perfect_reviewer_HYPOTHETICAL | 0.884 | 0.997 | 1.000 | 212 |

The perfect-reviewer row is HYPOTHETICAL and is not a measured result.

## Hard negatives (T4)

25 hard-negative documents in 5 families. **Decoy-hit rate 0.000** (a decoy hit = the prediction contains the category or level the document merely resembles); any false-positive category 0.000; predicted high-risk although not 0.000.

## Slices

Computed over all tiers (so T5 appears here). Macro-F1 covers only labels with support in the slice; slices with fewer documents than the threshold are marked SMALL_SAMPLE.

**by tier**

| tier | docs | families | level macro-F1 | category macro-F1 | high-risk recall | high-risk precision | high-risk FPR | warning |
|---|---|---|---|---|---|---|---|---|
| T1 | 97 | 17 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 |  |
| T2 | 66 | 11 | 1.000 | 0.994 | 1.000 | 1.000 | 0.000 |  |
| T3 | 24 | 4 | 0.345 | n/a | n/a | n/a | 0.000 | SMALL_SAMPLE |
| T4 | 25 | 5 | 1.000 | n/a | n/a | n/a | 0.000 |  |
| T5 | 15 | 3 | 1.000 | 0.938 | 1.000 | 1.000 | 0.000 | SMALL_SAMPLE |

**by format**

| format | docs | families | level macro-F1 | category macro-F1 | high-risk recall | high-risk precision | high-risk FPR | warning |
|---|---|---|---|---|---|---|---|---|
| agenda | 5 | 1 | 1.000 | n/a | n/a | n/a | 0.000 | SMALL_SAMPLE |
| clinical_note | 23 | 4 | 1.000 | 1.000 | 1.000 | 1.000 | n/a | SMALL_SAMPLE |
| config_file | 17 | 3 | 1.000 | 1.000 | 1.000 | 1.000 | n/a | SMALL_SAMPLE |
| csv_table | 17 | 3 | 1.000 | 0.950 | 1.000 | 1.000 | n/a | SMALL_SAMPLE |
| design_doc | 6 | 1 | 1.000 | 1.000 | n/a | n/a | 0.000 | SMALL_SAMPLE |
| form | 6 | 1 | 1.000 | 1.000 | 1.000 | 1.000 | n/a | SMALL_SAMPLE |
| how_to | 6 | 1 | 1.000 | n/a | n/a | n/a | 0.000 | SMALL_SAMPLE |
| legal_doc | 5 | 1 | 1.000 | n/a | n/a | n/a | 0.000 | SMALL_SAMPLE |
| legal_draft | 5 | 1 | 1.000 | 1.000 | 1.000 | 1.000 | n/a | SMALL_SAMPLE |
| meeting_notes | 5 | 1 | 1.000 | n/a | n/a | n/a | 0.000 | SMALL_SAMPLE |
| memo | 18 | 3 | 0.833 | 1.000 | 1.000 | 1.000 | 0.000 | SMALL_SAMPLE |
| notes | 29 | 5 | 0.853 | 1.000 | 1.000 | 1.000 | 0.000 |  |
| policy_doc | 6 | 1 | 0.286 | n/a | n/a | n/a | 0.000 | SMALL_SAMPLE |
| press_release | 6 | 1 | 1.000 | n/a | n/a | n/a | 0.000 | SMALL_SAMPLE |
| report | 15 | 3 | 1.000 | n/a | n/a | n/a | 0.000 | SMALL_SAMPLE |
| review | 6 | 1 | 1.000 | 1.000 | n/a | n/a | 0.000 | SMALL_SAMPLE |
| source_code | 17 | 3 | 1.000 | 1.000 | n/a | n/a | 0.000 | SMALL_SAMPLE |
| technical_doc | 12 | 2 | 1.000 | 0.978 | 1.000 | 1.000 | n/a | SMALL_SAMPLE |
| ticket | 11 | 2 | 0.625 | 1.000 | 1.000 | 1.000 | 0.000 | SMALL_SAMPLE |
| web_page | 6 | 1 | 1.000 | n/a | n/a | n/a | 0.000 | SMALL_SAMPLE |
| wiki_page | 6 | 1 | 1.000 | 1.000 | 1.000 | 1.000 | n/a | SMALL_SAMPLE |

**by generator**

| generator | docs | families | level macro-F1 | category macro-F1 | high-risk recall | high-risk precision | high-risk FPR | warning |
|---|---|---|---|---|---|---|---|---|
| template-v1 | 227 | 40 | 0.889 | 0.993 | 1.000 | 1.000 | 0.000 |  |

**by ambiguity_flag**

| ambiguity_flag | docs | families | level macro-F1 | category macro-F1 | high-risk recall | high-risk precision | high-risk FPR | warning |
|---|---|---|---|---|---|---|---|---|
| False | 203 | 36 | 1.000 | 0.993 | 1.000 | 1.000 | 0.000 |  |
| True | 24 | 4 | 0.345 | n/a | n/a | n/a | 0.000 | SMALL_SAMPLE |

## Latency (wall clock per document; not part of the reproducible fingerprint)

| n | mean ms | p50 ms | p95 ms | max ms | total ms |
|---|---|---|---|---|---|
| 227 | 5040.886 | 2090.271 | 2716.387 | 244752.010 | 1144281.164 |

## Caveats

* Dataset labels: AI-generated synthetic dataset — pending human gold-label review. Nothing here is human-validated.
* Documents in a family are variations of one template; treat families as the sample size.
* Precision and accuracy depend on this dataset's class balance, not production base rates.
