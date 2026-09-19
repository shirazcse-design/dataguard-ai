# UC4 supervised ML classifier (Approach B): pre-registered plan

**Status: committed BEFORE any model is trained or evaluated.** It fixes design, model-selection and
evaluation rules so results cannot be shaped after the fact. Deviations go in the results document.

## Purpose

Measure what a classical supervised model adds over deterministic rules, especially on the semantic
gap the Rules baseline exposed (Trade Secret, IP, M&A, narrative PII/PHI, and the Public / Internal /
Confidential boundary). It is not built to win; it is built to be measured honestly.

## Design

* **Two heads on one feature space** (PRD FR-03; architecture plan section 9):
  * Sensitivity level: multinomial logistic regression (4 classes).
  * Data categories: one-vs-rest logistic regression (8 independent labels).
* **Features (fixed):** TF-IDF word n-grams (1-2) over the content + TF-IDF character n-grams
  (char_wb 3-5) over the content + a separate TF-IDF block over filename tokens. **No rule outputs are
  used as features** (keeps A and B independent; a stacked variant is a later hybrid experiment) and
  **embedded labels are not used** (spoofable; label handling belongs to the fusion stage).
* **Fitting data:** `train` only. **Calibration data:** `calibration` only. **Evaluation:** `dev`.
  The locked test split is never loaded.
* **Calibration:** Platt (sigmoid) scaling fitted per head on the calibration split, transforming the
  decision score into a probability (multiclass: per-class one-vs-rest sigmoids, renormalised). If a
  label has fewer than 5 calibration positives or 5 negatives, that label is NOT calibrated and its
  confidence kind is `uncalibrated_score` (never mislabeled as calibrated).
* **Decisions:** level = argmax calibrated probability. A category is asserted at calibrated
  probability >= 0.5 (config default for every category). **No operating point is chosen in this
  phase** (approved: selected on dev after Rules and ML are both evaluated); the report shows a
  threshold sweep on dev for that later decision.
* **Evidence:** feature-attribution evidence (inferred, never observed) restricted to alphabetic
  word features of at most 25 characters (no digits, no `@`), so raw identifiers cannot leak.
* **Reproducibility:** fixed seed; deterministic solvers; training takes seconds, so it is retrained
  from the config each time rather than committing binaries. A model card records data hashes,
  config hash, hyperparameters, CV results and calibration statistics.

## Model selection (train only)

* Grouped 5-fold cross-validation on `train`, grouping by scenario family (`group_id`) so a family
  never spans folds.
* Grid (fixed in advance): regularisation `C` in {0.3, 1, 3, 10}, `class_weight = balanced`.
* Each head is selected independently: the level head by cross-validated level macro-F1; the
  category head by cross-validated category macro-F1 (uncalibrated decision > 0). Ties go to the
  smaller `C` (more regularisation).
* `dev` is evaluated **once** for the selected configuration, then reported; no tuning follows it.

## Evaluation (development splits only)

Same harness and headline as Rules (tiers T1-T4, family-level intervals, T5 as a slice), plus:
calibration (ECE, Brier, reliability tables), a per-tier and per-category comparison with the frozen
Rules 1.0.3 on the same dev documents, a rules-abstained complementarity analysis, a dev threshold
sweep, and a **filename ablation** (a model without the filename block) because filenames are
template-patterned in this dataset and could inflate results. Results on `train` (in-sample) and
`calibration` (used to fit the calibrators) are shown only with explicit warnings.

## Expectations stated in advance

* ML should beat Rules on the semantic categories (Trade Secret, IP, M&A) and on PUBLIC/CONFIDENTIAL
  level recall, because it can use vocabulary and style rather than only markers.
* ML should be **worse than Rules on precision** for identifier-driven categories (Financial/PCI,
  Credentials), where Rules were near-perfect, and will produce false positives that Rules did not.
* Because documents in a family share a template and this corpus is small (76 training families),
  cross-family generalisation is the real test; expect a large gap between cross-validated / in-sample
  numbers and dev, and wide dev intervals (18 dev families).
* Calibration on the calibration split (98-103 documents, 7-12 positives for some categories) will
  be noisy; reliability numbers are indicative only.
* The filename block probably helps on this synthetic data more than it would on real data.

## Limits stated in advance

Synthetic, AI-authored, template-generated, not human-reviewed. Class balance skews sensitive. Nothing
here transfers to real data without validation.
