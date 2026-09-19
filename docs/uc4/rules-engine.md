# UC4 Rules Engine (Approach A)

The deterministic Rules/Heuristics baseline. Its purpose is **not** to beat ML or an LLM: it is to
establish an honest baseline and to show where deterministic signals suffice and where semantic
understanding is required.

* Pre-registered scope: [`rules-catalog.md`](rules-catalog.md) (committed before any rule existed)
* Every post-first-run change: [`rules-changelog.md`](rules-changelog.md)
* Generated results: [`results/rules-baseline.md`](results/rules-baseline.md)

## Principles

1. **No rule match does not mean Public.** Where rules find no decisive level evidence they
   **abstain**. For the standalone benchmark *only*, an abstained level is scored as the
   configurable default `INTERNAL` (`standalone_default_level`), the result is marked
   `routing.abstained`, and the level confidence kind is `none`. Rules never output `PUBLIC`.
   In the future hybrid path abstention escalates to ML/LLM instead of assuming anything.
2. **A rule may abstain**, and rules do not try to solve semantic problems.
3. **Strength is a tier, not a probability:** `definitive` (validator passed and context agrees),
   `strong` (pattern + context, or an explicit structural marker), `weak` (a lexical hint).
   Only detections at or above `emit_min_strength` (default `strong`) assert a category or a level;
   weak evidence is retained in the result but asserts nothing.
4. **Negative context always wins.** Placeholder / test / documentation vocabulary near a match, in
   the document header zone, or in the filename suppresses a detection; known dummy values and
   published test numbers are suppressed outright. Every suppression is recorded with its reason.
5. **Labels raise, never lower.** An embedded label or banner can raise the level floor. `PUBLIC`
   and `INTERNAL` labels are not evidence of low sensitivity (labels go stale; a spoofable "public"
   marker must not downgrade content).
6. **Never expose raw sensitive values:** evidence carries a masked excerpt, a hash, a locator and
   the detector id/version.

## Architecture

```
ClassificationRequest
   -> prepare()            line index, tabular-header row, filename tokens, size cap (100k chars)
   -> 31 detectors         one module per data category + markings; fixed order (deterministic)
        each returns: detections (strength, masked evidence)  +  suppressions (with reasons)
   -> RulesEngine.analyze  assert categories >= emit threshold; derive level or ABSTAIN
   -> RulesClassifier      standard ClassificationResult (rule_strength confidence, evidence,
                           abstained flag, high_risk derived from config, versions, telemetry)
```

| Path | Role |
|---|---|
| `config/rules/rules.v1.yaml` | lexicons, negative-context terms, known dummy values, label mapping, thresholds, default level |
| `rules/config.py` | validated schema + loader (cross-checked against the taxonomy) |
| `rules/preprocess.py`, `context.py` | document preparation; positive/negative context |
| `rules/validators.py`, `masking.py` | Luhn, IBAN mod-97, ABA, JWT, entropy; value masking |
| `rules/detectors/*.py` | `pii`, `phi`, `financial`, `credentials`, `source_code`, `ip_ts_ma`, `markings` |
| `rules/engine.py`, `classifier.py` | aggregation and the `Classifier` adapter |
| `evals/classification/rules_analysis.py`, `rules_report.py` | error analysis and the generated results |

## Detectors implemented

| Category | Detectors (strength) |
|---|---|
| PII | `pii.ssn_like` (strong with ID context or tabular header, else weak; dummy values and part-number contexts suppressed), `pii.passport_field`, `pii.dob_field`, `pii.field_labels` (non-blank values only), `pii.bulk_contact` (strong only with >= 3 emails **and** >= 3 phones) |
| PHI | `phi.mrn`, `phi.hl7_pid`, `phi.patient_clinical` (strong only with clinical vocabulary), `phi.icd_context`, `phi.clinical_density` (weak) |
| Financial / PCI | `fin.card_pan` (Luhn + network prefix; definitive with card context; published test PANs and test context suppressed), `fin.iban` (mod-97 + country length), `fin.us_bank_account` (ABA checksum / labelled account), `fin.nonpublic_financials` (results vocabulary + embargo language, "published" context demotes) |
| Credentials | `cred.assignment` (secret-named key with a literal value; environment lookups, placeholders, variable references and low-diversity words suppressed), `cred.prefixed_token`, `cred.jwt`, `cred.bearer`, `cred.url_password`, `cred.aws_access_key` (documented example key suppressed), `cred.private_key_block`, `cred.password_hash` (weak) |
| Source Code | `code.structure` (code extension + per-line syntax density; open-source licence suppresses) |
| IP | `ip.markers` (published-patent context suppresses), `ip.novelty_language` (weak) |
| Trade Secret | `ts.markers` (a *banner* or label only), `ts.secrecy_language` (weak) |
| M&A | `ma.markers` (LOI, term sheet, ... ; announced/historical/instructional context suppresses), `ma.transaction_secrecy` (strong only with secrecy vocabulary and no announced/historical context) |
| Level | `mark.banner` (short line that is mostly the marking, or an embedded label), `mark.filename` (weak) |

**Hard negatives handled by design:** documentation describing an SSN format (dummy values and
"placeholder" context), fake/test payment data (published test PANs, test/sandbox context, Luhn and
prefix checks), already-public acquisitions (announced/historical context), code that merely names
a `password` variable (a literal, non-placeholder, non-environment value is required),
open-source code (licence header), part numbers shaped like SSNs, sixteen-digit tracking numbers
that fail Luhn, masked card receipts, blank form templates, and declared test fixtures.

## Performance

Measured, not assumed (see the results document): the harness records wall-clock latency per
document and reports P50/P95/max against the PRD's 500 ms deterministic pre-check target.
Adversarial 200 KB inputs (long digit runs, repeated `password=`, ...) are exercised by unit tests;
the worst case measured was 732 ms before the scan cap and 213 ms after lowering it to 100,000
characters (changelog C4). Content beyond the cap is **not scanned**, a stated limitation.

## Known limitations (by design)

* Semantic Trade Secret / IP / M&A / PII / PHI content without explicit markers is out of reach.
* Rules cannot establish that a document is Public: PUBLIC recall is zero by construction.
* Label reliance: a `STRICTLY CONFIDENTIAL` banner on a benign document raises the level.
* Config-style files (compose, pipelines, kubeconfig-like YAML) have no reliable Source Code signal.
* Emails-only contact lists are weak evidence; a two-column table is not treated as tabular.
* A keyword rule can be steered by hostile text if the keyword is accepted anywhere in the body;
  markers are therefore restricted to banners/labels, but vocabulary rules remain manipulable.
* The rules were written by the assistant that authored the dataset: development-set numbers are
  optimistic relative to unseen data.

## Running

```bash
dataguard-uc4 eval run --classifier rules --split dev          # development splits only
dataguard-uc4 rules analyze --split train --out analysis.md    # per-family FN/FP with detector ids
dataguard-uc4 rules report                                     # regenerate results/rules-baseline.md
```

The locked test split is refused by all of these unless `--allow-locked-test` is given to
`eval run` (audited, report-only); it has **not** been used for the Rules Engine.
