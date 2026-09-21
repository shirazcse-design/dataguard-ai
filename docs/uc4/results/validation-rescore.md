# Validation re-score: frozen hybrid under the reviewed labels (strict and lenient)

> **AI-generated synthetic dataset — reviewed by one human (provenance per coordinator); second independent review pending.** Replays of recorded LLM responses (no provider call, no credentials). **Nothing was tuned:** the classifier, variant, thresholds, prompts and models are exactly those frozen before the locked-test run. Written 2026-09-21.

## What changed since the locked-test run, and what did not

| Changed (decisions A29-A32) | Not changed |
|---|---|
| `amb_aggregate_health_stats` (calibration): gold HIGHLY_CONFIDENTIAL -> **CONFIDENTIAL**, alternatives `[HIGHLY_CONFIDENTIAL, INTERNAL]` (A30) | The hybrid `default` variant, thresholds, prompts, models, recorded LLM responses |
| `hn_business_case_study` (calibration): gold stays PUBLIC, now **ambiguous with INTERNAL as an alternative** (A32) | Every train, dev and **locked test** label and document (byte-identical; the test split's family set is pinned by a test) |
| A **lenient level view** is reported beside the strict headline (A31; harness 1.5.0) | All strict headline definitions (levels, categories, high-risk) |
| Dataset label wording (A29) | The taxonomy text and every prompt |

Only the two calibration families' records changed in the dataset (`dataset_sha256` `284abad1247b53a3…`). While doing this I found that the seeded split search balances on each family's gold level, so relabelling **any** family silently reshuffled the whole split assignment, including the locked test split. The first regeneration did exactly that and was reverted before anything was evaluated. A family can now pin its split-balancing level (`split_gold_level`), and a test pins the locked split's family set.

## Results

Headline tiers T1-T4; intervals are family-level bootstraps. **Strict** = the headline as always; **lenient** = a predicted level equal to one of the document's acceptable alternatives counts as correct (levels only; categories and high-risk stay strict).

| split | level macro-F1, strict, old labels | level macro-F1, strict, **new labels** | level macro-F1, **lenient**, new labels | high-risk recall, old -> new | category macro-F1 |
|---|---|---|---|---|---|
| dev (18 families; chose the variant) | 0.870 [0.631, 1.000] | 0.870 [0.631, 1.000] | 0.870 [0.631, 1.000] (0 relaxed) | 1.000 -> 1.000 | 1.000 |
| calibration (18 families; out-of-sample) | 0.867 [0.717, 1.000] | 0.859 [0.685, 1.000] | **1.000** [1.000, 1.000] (9 relaxed) | 0.891 (49/55) -> **1.000** | 1.000 |
| locked test (37 families; post-hoc replay) | 0.884 [0.758, 0.980] | 0.884 [0.758, 0.980] (as expected: no test label changed) | **1.000** [1.000, 1.000] (19 relaxed) | 1.000 -> 1.000 | 0.997 |

* **Calibration high-risk recall 0.891 -> 1.000** is entirely the relabel of `amb_aggregate_health_stats`: those six documents are no longer high-risk (CONFIDENTIAL, no category). The model's answer did not change; the reference did (decision A30). Strict level F1 on calibration went **down** slightly (0.867 -> 0.859) because the family is still predicted INTERNAL, now one rank below a CONFIDENTIAL gold, and the three `hn_business_case_study` errors remain strict errors.
* **Test and calibration:** every remaining strict level error is a disagreement inside the gold's own acceptable alternatives (19 of 19 on test, 9 of 9 on calibration). All 19 test errors are gold CONFIDENTIAL predicted INTERNAL in the four ambiguous families whose alternatives (`[INTERNAL]`) were written at dataset creation on 2026-09-18, before any model ran and unchanged since, so the lenient view is not fitted to the test results.
* **Dev is different:** 5 strict errors remain and none is relaxed. All are `hn_public_api_docs_placeholder_keys`, gold PUBLIC predicted INTERNAL, with no alternative. Independent readers (the Round 1 sheets and the Round 2 reviewer) labelled that family PUBLIC, so these are model errors against the reviewed label, not a tie-break.

## Gates (PRD MVP targets; point / lower bound)

| | strict | lenient |
|---|---|---|
| level macro-F1 >= 0.85, **locked test** | PASS / **FAIL** (0.884 / 0.758) | PASS / PASS (1.000 / 1.000) |
| level macro-F1 >= 0.85, calibration | PASS / FAIL (0.859 / 0.685) | PASS / PASS |
| level macro-F1 >= 0.85, dev | PASS / FAIL (0.870 / 0.631) | PASS / FAIL (unchanged) |
| category macro-F1 >= 0.85 | PASS / PASS on all three | (not relaxed) |
| high-risk recall >= 0.90 (informational) | PASS / PASS on all three | (not relaxed) |

**The gate verdict is unchanged: the approved gates are defined on the strict metric, and the strict level lower bound fails on every split.** The lenient view shows where the shortfall lives (tie-break disagreements inside the gold's acceptable alternatives) but it does not pass the gate. The product owner subsequently **adopted the lenient level view as the gate** (decision A33, 2026-09-21); that is a decision, not a measurement, and the strict result stays as reported here.

## Collateral changes to report

* **The ML baseline moved on dev although no dev label changed** (level macro-F1 0.232 -> 0.215; high-risk recall 0.925 -> 0.830; precision 0.551 -> 0.524): the ML model is calibrated on the calibration split, whose labels changed. The hybrid variants that use ML (`ml_stage_*`) moved with it (for example `ml_stage_50` level macro-F1 0.651 -> 0.773). **The recommended `default` variant does not use ML; its dev numbers and the pre-registered recommendation are unchanged.** The affected generated reports and the ML/hybrid prose were updated.
* **The locked test split was read a second time** (a replay, `--allow-locked-test`, access-log entry 2 of 2, run `hybrid-test-20260921T212142Z-417b1770`). It is post-hoc and audited: the test labels and the recorded responses are unchanged, so the strict numbers are identical to the first run; the only new information is the lenient view. The split remains consumed.

## What this does and does not validate

* **Does:** the shortfall against the level gate on calibration and test is a tie-break disagreement, not misclassification: no severe under-classification, no high-risk miss on test or calibration under the reviewed labels, category quality near-perfect.
* **Does not:** independent validation. The labels are AI-authored and were reviewed by **one** person whose independence is the coordinator's statement; the lenient `1.000 [1.000, 1.000]` means no errors on a small, template-generated dataset, not proven quality; alternatives were authored by the same AI as the gold; the dev errors in `hn_public_api_docs_placeholder_keys` are genuine model errors that no view hides; nothing transfers to real data without validation.
