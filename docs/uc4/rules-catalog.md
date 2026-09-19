# UC4 Rules Engine: pre-registered detector catalog (ruleset 1.0.0)

**Status: written and committed BEFORE any rule was implemented or evaluated.** It fixes the intended
scope so the rules are not grown, one family at a time, to fit the synthetic dataset. Anything added
after the first evaluation is recorded in [`rules-changelog.md`](rules-changelog.md) with its reason.

## Purpose

Establish an honest **deterministic baseline** for UC4 and show where rules work well and where
semantic understanding is required. The goal is *not* for rules to beat ML or an LLM.

Disclosure: the dataset was authored by the same assistant that wrote these rules, so knowledge of
its content is a contamination risk. The catalog below is therefore restricted to detectors a DLP
engineer would write from the taxonomy and general practice, without reference to any one family.

## Principles

1. **No rule match does not mean Public.** Absence of evidence is *abstention*, never a finding of low
   sensitivity. For the standalone Rules benchmark ONLY, an abstained level is reported as the
   configurable default `INTERNAL`; in the future hybrid path, abstention escalates to ML/LLM.
2. **A rule may abstain**, per axis. Rules do not try to solve semantic problems.
3. **Strength is not a probability.** `definitive` (a validator passed and context agrees),
   `strong` (pattern + context, or an explicit structural marker), `weak` (a lexical hint).
   Only detections at or above a category's `emit_min_strength` (default `strong`) produce a
   category; weak evidence is retained for a later stage but never asserts a category or a level.
4. **Context matters, in both directions.** Positive context (nearby field names, table headers)
   promotes a bare pattern; negative context (example, sample, dummy, placeholder, test data,
   documentation, training, ...) suppresses or demotes it. Negative context always wins.
5. **Labels raise, never lower.** An embedded label or banner such as `STRICTLY CONFIDENTIAL` can
   raise the level floor. A `PUBLIC`/`INTERNAL` label is never treated as evidence of low
   sensitivity (labels go stale; a spoofable "public" marker must not downgrade).
6. **Never expose raw sensitive values.** Evidence carries a masked excerpt, a locator and a hash.
7. **No positive "public" detection.** Rules cannot establish that content is safe to publish.
8. **Bounded and safe.** Linear or tightly bounded regexes, a size cap, and measured latency against
   the PRD's <= 500 ms deterministic pre-check target.

## Detectors

| Category | Detector | Signal | Strength |
|---|---|---|---|
| PII | `pii.ssn_like` | 3-2-4 digits, structurally valid; positive context (ssn, social security, tax id, ...) or tabular header | strong (+ HC hint: government id); bare pattern weak; dummy values / negative context suppressed |
| PII | `pii.passport_field` | letter+7-9 digits next to "passport" | strong (+ HC hint) |
| PII | `pii.dob_field` | date next to "date of birth" / "dob" | strong |
| PII | `pii.field_labels` | "home/residential address", ... with a non-blank value | strong |
| PII | `pii.bulk_contact` | >= 3 distinct emails and >= 3 phone numbers | strong; emails or phones alone weak |
| PHI | `phi.mrn` | `MRN-` + digits | strong |
| PHI | `phi.hl7_pid` | HL7 `PID|` segment | strong |
| PHI | `phi.patient_clinical` | non-blank `Patient:` field + a clinical term | strong; otherwise weak |
| PHI | `phi.icd_context` | ICD-10 code next to diagnosis/dx/icd | strong |
| PHI | `phi.clinical_density` | >= 4 distinct clinical terms | weak |
| Financial/PCI | `fin.card_pan` | 13-19 digits, Luhn-valid, plausible network prefix | definitive with card context, else strong; published test PANs and negative context suppressed |
| Financial/PCI | `fin.iban` | country-length + mod-97 valid | definitive |
| Financial/PCI | `fin.us_bank_account` | ABA-checksummed routing number / labelled account number | strong |
| Financial/PCI | `fin.nonpublic_financials` | financial-results vocabulary + non-public/embargo vocabulary | strong; vocabulary alone weak |
| Credentials | `cred.assignment` | secret-named key with a literal, non-placeholder, non-environment value | strong / definitive (high entropy) |
| Credentials | `cred.prefixed_token` | vendor-style `prefix_...` token with entropy | strong |
| Credentials | `cred.jwt`, `cred.bearer`, `cred.url_password`, `cred.aws_access_key`, `cred.private_key_block` | structural credential formats | strong / definitive |
| Credentials | `cred.password_hash` | bcrypt-style hash | weak |
| Source Code | `code.structure` | code file extension + syntax density, not publicly licensed | strong; syntax without a code extension weak; OSS licence header suppresses |
| IP | `ip.markers` | "invention disclosure", "patent pending", draft claims, ... | strong; published-patent context suppresses |
| IP | `ip.novelty_language` | novelty/unpublished vocabulary | weak |
| Trade Secret | `ts.markers` | "trade secret" banner/label | strong |
| Trade Secret | `ts.secrecy_language` | proprietary-process / know-how vocabulary | weak |
| M&A | `ma.markers` | letter of intent, term sheet, due diligence, exclusivity period, ... | strong |
| M&A | `ma.transaction_secrecy` | transaction vocabulary + secrecy vocabulary, no announced/historical/instructional context | strong; vocabulary alone weak |
| (level) | `mark.banner` | "strictly/highly confidential", "restricted", "confidential" in a banner or embedded label | level floor, strong |
| (level) | `mark.filename` | "confidential/restricted/secret" in the filename | weak |

Level = the highest of (level floors of emitted categories, government-id hint, banner/label floors).
No emitted evidence at all means the level abstains.

## Explicit expectations (stated before evaluation)

Rules are expected to be **strong** on: card numbers, IBANs, labelled government IDs, MRN/HL7,
credential assignments and structured secrets, proprietary code files, and explicitly marked
documents (banners, "trade secret", LOI/term sheet, invention disclosure).

Rules are expected to be **weak or unable** on: semantic Trade Secret / IP / M&A / PII / PHI content
with no markers (narratives), ambiguous cases (T3), establishing that a document is Public, and
config-style files whose sensitivity is contextual (compose files, pipelines). Those are exactly the
gaps ML/LLM are meant to fill; leaving them unsolved is the point of the baseline.

## Evaluation protocol

* Iterate on **train**. Evaluate **dev** at checkpoints (each logged). **Calibration** is untouched
  until the final report. The **locked test split is not used** in this phase.
* After the first evaluation, only *defect fixes* (a rule that does not do what this catalog says)
  are allowed; anything that adds coverage for a specific observed failure is recorded in the
  changelog as a tuning change and called out in the results.
