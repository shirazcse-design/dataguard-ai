# UC4 evaluation harness

The harness measures any classifier that implements `app.classification.interfaces.Classifier`
against the synthetic dataset, so Rules, ML, LLM and Hybrid approaches will all be scored by the
same code. It exists before any of them so the benchmark cannot be shaped around one approach.

Results in this repository are only ever produced by executing the harness; see
[`results/harness-validation.md`](results/harness-validation.md) for the evidence that the harness
itself measures correctly.

## Running it

```bash
dataguard-uc4 eval run --classifier oracle|majority|random --split dev        # default split: dev
dataguard-uc4 eval run --classifier random --split dev,calibration --seed 7
dataguard-uc4 eval validate-harness                                            # writes docs/uc4/results/
```

Each run writes `evals/classification/runs/<run_id>/` (git-ignored):
`run_manifest.json`, `metrics.json`, `predictions.jsonl` (one record per document) and
`report.md`. Only curated summaries are committed.

The three built-in classifiers are **sanity baselines** used to validate the harness (Oracle
returns gold, Majority predicts the training majority, Random is seeded chance). They are not
approaches to be compared. Real classifiers plug in through the same interface.

## What is measured

Three metric families, each reported on its own. There is **no combined headline score**.

| Family | Reported |
|---|---|
| Sensitivity level (single label, ordinal) | 4x4 confusion matrix plus a `NO_PREDICTION` column; per-class precision/recall/F1/support; macro and micro P/R/F1; accuracy; under-, severe-under- and over-classification rates |
| Data categories (multilabel) | per-category TP/FP/FN/TN, precision/recall/F1/support and false-positive rate; macro and micro P/R/F1; exact-match ratio; documents with a false-positive category |
| High-risk (derived from `config/taxonomy/high_risk.v1.yaml`) | TP/FP/FN/TN, **recall (reported on its own)**, precision, F1, false-positive rate, prevalence |

Also captured: coverage (documents, failures, deferrals), wall-clock latency (mean, p50, p95, max),
and any classifier-reported cost.

### Conventions (they change the numbers, so they are written down)

* **Undefined is `null`, not 0.** A precision with no predictions, or a recall with no positives,
  is reported as `n/a`.
* **Macro averages cover labels with gold support > 0** in the evaluated subset. An undefined
  precision of a *supported* label counts as 0 in the average (scikit-learn's `zero_division=0`).
  The convention string is stored next to the numbers.
* **A missing prediction is a miss, never a skipped document.** Level: counted in the
  `NO_PREDICTION` column (and as severe under-classification). Categories: empty set (false
  negatives). High-risk: predicted negative.
* **High-risk is always re-derived** by the harness from the configured definition; a classifier's
  own `high_risk` field is checked and any disagreement is counted and flagged.
* **A level below a category floor is measured, not rejected.** Only labels outside the taxonomy
  make a prediction unusable.
* **Exception messages are never recorded**, only the exception class, because a message could
  contain document text.

### Headline vs. slices

The headline covers tiers T1-T4. T5 (adversarial / prompt injection) is reported separately as its
own slice. Slices are computed over all tiers by `tier`, `format`, `generator` and
`ambiguity_flag`; slices below the small-sample threshold are marked.

### Confidence intervals

95% percentile bootstrap, 1000 resamples, seeded. **Scenario families are resampled, not
documents**, because documents in a family are variations of one template. This matters: in the
validation results the Majority baseline's level macro-F1 on `dev` has a wide interval because
`dev` contains only 18 families in the headline view. `bootstrap.unit` in `config/eval/eval.v1.yaml`
selects `group` (default) or `document`; a test shows that document-level resampling is markedly
narrower on correlated data, i.e. overconfident.

### Deferrals to human review

Primary metrics score a deferred (`review_required`) document on its provisional label. Three
alternative views are also computed when any document is deferred: `auto_only`,
`deferred_as_errors`, and `deferred_resolved_by_perfect_reviewer_HYPOTHETICAL` (labeled as a
hypothetical, never as a result). No current classifier defers; the behaviour is exercised by a
synthetic deferring classifier.

### Reproducibility

`run_manifest.json` records the git commit and dirty flag, dataset sha256 and spec hash,
taxonomy/high-risk/eval config hashes, classifier name/version/parameters, seeds and library
versions. `metrics_fingerprint` is the SHA-256 of all deterministic metrics (latency and cost
excluded, floats rounded to 12 places): the same dataset, config, classifier and seed reproduce it
exactly. The dataset was also verified to regenerate byte-identically on Python 3.11 and 3.12.

## Test-split discipline

The `test` split is for reporting only. The CLI defaults to `dev`; evaluating `test` requires an
explicit `--split` and prints a notice, and run manifests record whether the locked split was
touched. This is a convention, not a hard lock: tuning on the test split is prevented by process
and review, not by code.

## How the harness is validated

See [`results/harness-validation.md`](results/harness-validation.md). In short:

* **Oracle** must score exactly 1 on every metric, with a purely diagonal confusion matrix and
  bootstrap intervals that collapse to [1, 1].
* **Majority** must match expectations computed by separate, naive code from raw gold labels and
  the config lists (it shares no metrics code with the harness). That calculator is itself checked
  against a hand-worked example.
* **Random** must land within 4.5 binomial standard deviations of the analytic chance rate.
* **Robustness**: exceptions become recorded failures, deferral views behave, latency is captured,
  identical runs give identical fingerprints, and a classifier that misreports `high_risk` is
  caught.
* **Mutation testing.** Sabotaging the metric code (off-by-one F1 denominator, scaled macro-F1,
  swapped FP/FN, recall using the wrong count, headline including T5, ...) made the validation
  fail every time. One initially escaped (a harness that trusts a classifier's own `high_risk`);
  that gap was closed by adding the "lying classifier" check.

## Reading results correctly

* **High-risk recall alone is easy to game.** The Majority baseline (always Highly Confidential)
  scores recall 1.0 because everything becomes high-risk; only its precision (about 0.52) and
  false-positive rate (1.0) expose it. Always read recall together with precision and FPR.
* **Prevalence.** Roughly half the test documents are high-risk, so precision and accuracy reflect
  this dataset's balance, not production base rates.
* **Effective sample size is the number of families.** Look at the intervals, not the point
  estimates alone.
* **Synthetic and AI-authored.** Nothing here transfers to real data without further validation.

## Not built yet (out of scope for this stage)

Calibration metrics (ECE, reliability tables; needed once ML/LLM emit confidences), cost modelling
(token pricing), pass/fail release gates against the PRD thresholds, and CI evaluation of real
classifiers. These arrive with Phases 4-6.

## Adding a classifier

Implement `name`, `version`, `classify(request) -> ClassificationResult` and `params()`. Return a
valid result for every request; express failures through `status`, never by raising (a raised
exception is recorded as a failure, but the classification path is meant to be total). Then run it
through `evaluate()` exactly as the baselines are.
