# UC4 supervised ML classifier (Approach B)

> Dataset labels: **AI-generated synthetic dataset — pending human gold-label review.** Every number
> below comes from an executed run of `dataguard-uc4 ml report`; see
> [`results/ml-baseline.md`](results/ml-baseline.md). The protocol was pre-registered in
> [`ml-plan.md`](ml-plan.md) before any model was trained.

## What it is

A classical supervised classifier that plugs into the same `Classifier` interface and harness as the
Rules Engine (`--classifier ml`). It is a measured baseline, not a production component.

```
content + filename ──► TF-IDF word (1-2) ┐
                       TF-IDF char_wb (3-5) ├─► level head: multinomial LR ──► Platt ──► argmax
                       TF-IDF filename    ┘   category heads: 8 x one-vs-rest LR ─► Platt ─► p >= threshold
```

| Piece | File | Notes |
|---|---|---|
| Config | `config/ml/ml.v1.yaml`, `ml/classification/config.py` | Validated; hash recorded in the model id. No operating point is set (`category_thresholds: {}`). |
| Features | `ml/classification/features.py` | Embedded labels and rule outputs are **not** features. Filename block can be disabled (ablation). |
| Model | `ml/classification/model.py` | Fits heads, calibrates, predicts, explains. |
| Calibration | `ml/classification/calibration.py` | Platt scaling; falls back to an explicitly **uncalibrated** score when there are < 5 positives or negatives. |
| Selection | `ml/classification/selection.py` | Grouped (by family) CV; `C` per head; leakage diagnostic. |
| Classifier | `ml/classification/classifier.py` | Loads **only** development splits, never requests locked-test access. |
| Report | `evals/classification/ml_report.py` | Generates `results/ml-baseline.md`. |

## Data discipline

* Fit on `train`; calibrate on `calibration`; evaluate on `dev`. The locked test split is never loaded
  (`build_ml_classifier` only reads `DEVELOPMENT_SPLITS`).
* `C` was chosen by grouped 5-fold CV on train (`results/ml-cv.json`). Dev was evaluated once for the
  selected configuration; nothing was tuned afterwards.
* `params()["fit_splits"]` / `["calibration_splits"]` drive an in-sample warning in reports so train
  and calibration numbers cannot be mistaken for held-out ones.

## Output contract

* Level = argmax of calibrated probabilities; confidence kind `calibrated_probability` with a
  `calibration_ref`, or `uncalibrated_score` when calibration data was too thin.
* Categories asserted at calibrated probability >= 0.5; `scores` carries the probabilities for the
  calibration metrics and threshold sweep.
* Evidence is `feature_attribution` (provenance **inferred**): short alphabetic words only, never
  digits or `@`, so raw identifiers cannot leak.
* `high_risk` is derived from `config/taxonomy/high_risk.v1.yaml`, never predicted.
* The ML classifier never abstains (`routing.abstained = False`); deferral is a Hybrid decision.

## Findings (development split; see the results file for every number)

1. **It memorises templates.** Fitted on train it scores 1.0; random K-fold on train scores 0.98 /
   1.00 (level / category macro-F1); grouped K-fold scores 0.31 / 0.14. Documents in a family share a
   template, so only the grouped figure is honest, and only 76 families are available.
2. **Held-out dev is weak and over-flags.** Level macro-F1 0.232, category macro-F1 0.433. It
   predicts Highly Confidential for 89 of 97 headline documents, so high-risk recall (0.925) is
   accompanied by a false-positive rate of 0.909 and precision of 0.551. **That recall must not be
   read as success** (approved decision A23).
3. **Complementary to Rules, not better.** ML wins on Intellectual Property (0.92 vs 0.00) and Source
   Code (0.95 vs 0.67) and catches documents where Rules abstain; Rules win on Credentials (1.0 vs
   0.11), Financial/PCI and M&A (0.84 vs 0). Neither finds Trade Secret or PII on dev.
4. **Calibration is decent where measurable, but it cannot fix a biased ranker.** Category ECE 0.009,
   level ECE 0.058, fitted on ~100 documents.
5. **The filename block did not help.** The content-only variant is slightly better on every dev
   metric (level 0.259 vs 0.232, category 0.457 vs 0.433, high-risk FPR 0.795 vs 0.909), all well
   within the intervals. The pre-registered suspicion that filenames inflate results was therefore
   not confirmed; if anything the filename block adds noise here.
6. **Latency** is a few milliseconds per document after training.

Pre-registered expectations that were **not** met: ML did not beat Rules on M&A or Trade Secret.

## Limits

Synthetic, template-generated, AI-authored labels not yet human reviewed; 76 training families and 18
dev families, so intervals are wide; Rules were developed while looking at dev and ML was not, so the
comparison flatters Rules; calibration was fitted on ~100 documents; nothing here transfers to real
data. The locked test split has never been evaluated.

## Reproduce

```
dataguard-uc4 ml select --out docs/uc4/results/ml-cv.json   # grouped CV on train
dataguard-uc4 eval run --classifier ml --split dev
dataguard-uc4 ml report --out docs/uc4/results/ml-baseline.md
```
