# UC4 synthetic dataset specification

This document describes how the UC4 benchmark dataset is built and what it can and cannot tell
you. All figures live in the generated [`dataset-report.md`](dataset-report.md); this page
deliberately does not repeat them.

## What it is

A fully synthetic corpus of pre-extracted text documents (v0.1 does not parse DOCX/PDF/XLSX; a
spreadsheet such as `Acquisition_Targets_2027.xlsx` is represented as its extracted tabular
text). Each document has a filename/extension, optional embedded labels, a small metadata map, and
ground-truth labels under the two-axis taxonomy (one Sensitivity Level, zero or more Data
Categories) plus gold evidence spans. Gold labels follow
[`labeling-guidelines.md`](labeling-guidelines.md).

It is used only to compare Rules, ML, LLM and Hybrid classification. No real personal, health,
financial or credential data is used anywhere.

## How it is generated

The dataset is a pure function of `data/synthetic/uc4/spec/` and the seed:

```
spec/dataset_spec.yaml   seed, sizes, thresholds
spec/pools.yaml          shared vocabulary (names, companies, diagnoses, injection fragments ...)
spec/families/*.yaml     one entry per content scenario ("family")
        |
        v   dataguard-uc4 dataset generate
docs/{train,calibration,dev,test}.jsonl   +   manifest.json
```

* **Families.** A family is one scenario (for example "HR record with SSN and DOB"). It defines
  gold labels, filename templates, one or more body templates, and metadata. Each document is a
  variation of its family produced by slot filling (names, amounts, identifiers) and choice
  alternatives.
* **Template language.** `«slot»`, `«slot#2»` (independent second draw), `«@generator»` (fake-data
  generator), `«a|b|c»` (choice) and `⟦LABEL§text⟧` (gold evidence span). Evidence offsets are
  computed at render time, so they are exact by construction.
* **Determinism.** Each document is generated from `(seed, family, index)` using only
  Mersenne-Twister `random()` as the primitive, with golden values pinned in tests. Regenerating
  reproduces every byte; CI and `dataset validate` verify that.
* **Opaque ids.** Document ids never encode family, tier or label.

## Tiers

| Tier | Meaning | Purpose |
|---|---|---|
| T1 Easy | Sensitivity signalled by explicit patterns or markers | Where rules should win on speed and cost |
| T2 Semantic | Sensitivity carried by meaning; a lint forbids obvious identifiers, emails, phones, key-like strings | Where rules should fail and ML/LLM should help |
| T3 Ambiguous | A careful reviewer could disagree; gold follows the fail-safe tie-break; lower level recorded as an alternative | Threshold and review-rate behaviour |
| T4 Hard negative | Looks like a higher class but is not; each family declares `decoy_for` | Stops the benchmark favouring high-recall detectors |
| T5 Adversarial | Prompt-injection text (downgrade, upgrade, exfiltration, role override, hidden markup, multilingual) | Guardrail testing; reported separately from the headline |

Metadata-conflict cases (an embedded `PUBLIC` label on sensitive content, a `STRICTLY CONFIDENTIAL`
banner on a lunch menu) exist so that embedded labels are treated as evidence, not truth.

## Splits

`train`, `calibration`, `dev` and a locked `test` split, assigned **by scenario family**: every
document of a family lands in one split, so a classifier never trains on the template family of a
test document. The assignment is a seeded search that balances split sizes, tier mix, level mix
and per-category positives.

| Split | Use |
|---|---|
| train | model training, few-shot pool, rule development |
| calibration | ML probability calibration only |
| dev | threshold tuning, prompt iteration, reliability tables |
| test (locked) | reporting only; never used for tuning |

## Integrity and leakage checks

`dataguard-uc4 dataset validate` regenerates the dataset and fails on any of:

* invalid or contradictory gold labels (unknown ids, level below a category floor);
* an evidence span whose text does not equal `content[start:end]`, or a gold category with no span;
* tier invariants (T3 flagged and explained, T4 has a decoy and never contains it, T5 contains a
  known injection fragment, T2 passes the pattern-free lint);
* exact-duplicate documents;
* a family spanning splits, or inconsistent group ids;
* an identical evidence value or sentence appearing in more than one split (identifiers and
  sentences are leakage; a span that is exactly a shared vocabulary term, such as a diagnosis, is
  exempt because domain vocabulary is legitimately shared);
* cross-split near-duplicates (word 5-gram Jaccard at or above 0.5);
* a test split with a missing tier, or with no positives for some label;
* the committed files differing from a fresh regeneration.

Labels below the minimum-positives threshold for their split are recorded as **small-sample
flags** in the manifest (reported, not fatal), per the approved plan.

Every check is proven to fire by a targeted mutation test.

## Synthetic-data conventions

Emails use reserved `.example` domains; phones use the fictional `555-01xx` range; SSN-like
values use area 900-999 and group 01-49 (never issued); cards are Luhn-valid on public test
prefixes; IBANs use a fictional bank code; secrets use a fake vendor prefix or vendor-documented
example keys; no PEM blocks and no provider-format live keys. Tests scan the committed data for
violations. Because SSN-like values are structurally invalid by construction, a rules detector
must rely on format and context, not on issuance-range validation, and the same digit format is
deliberately reused in T4 part numbers so it is not a label cue on its own.

## Known limitations (read before trusting any number)

1. **AI-authored, not human-reviewed.** The dataset's declared status is **"AI-generated synthetic
   dataset — pending human gold-label review"**; gold labels must not be presented as independently
   human-validated. Every family and gold label was written by an AI assistant following the labeling
   guidelines. Every document carries `annotation_status=unreviewed`. Use
   `dataguard-uc4 dataset review-sheet` to produce a one-document-per-family CSV for a human to
   confirm or correct labels.
2. **Template-generated.** Documents in a family are variations of one template. The effective
   sample size is the number of **families**, not documents, so document-level confidence
   intervals would be overconfident; the evaluation harness resamples by family.
3. **Single generator.** All text was written by one author and one template engine. A later LLM
   classifier from the same model family might be favoured by stylistic artifacts. Metrics are
   sliced by `generator` so this can be checked once a second generator exists.
4. **Level skew.** The corpus is weighted toward sensitive content: Highly Confidential documents
   are about half of the test split. Precision and accuracy therefore reflect these base rates,
   not production base rates, and a majority-class baseline is not near zero.
5. **Thin categories.** Several labels have fewer than the target 25 test positives (see the
   small-sample table in the report).
6. **Cosmetic incoherence.** Some generated names are mismatched (for example a "Software"
   company in a "battery materials" sector). This does not affect labels but is unrealistic.
7. **T2 lint scope.** The pattern-free lint guards against obvious identifiers only; it does not
   prove that no lexical cue exists.

## Regenerating and extending

```bash
dataguard-uc4 dataset generate      # writes docs/*.jsonl and manifest.json (fails on integrity errors)
dataguard-uc4 dataset validate      # regenerate + compare + integrity
dataguard-uc4 dataset report        # docs/uc4/dataset-report.md from the manifest
dataguard-uc4 dataset review-sheet  # data/synthetic/uc4/review/family_review_sheet.csv
```

To add a scenario, add a family to `spec/families/*.yaml`, regenerate, and commit the regenerated
data with the spec change. Changing any spec file changes `spec_hash`; `dataset validate` fails
until the data is regenerated.
