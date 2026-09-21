# UC4 labeling guidelines (taxonomy v1.0.0)

These rules define the **gold labels** for the synthetic UC4 dataset. They exist so a label can be
justified from written rules rather than from an annotator's intuition. The taxonomy itself
(`config/taxonomy/taxonomy.v1.yaml`) is the source of truth for definitions; this document adds the
decision procedure, tie-breakers and overlap rules.

> The level floors are **initial configurable DataGuard synthetic-policy defaults**, not universal
> security requirements. If the floors change, gold levels that were derived from them must be
> re-reviewed.

## 1. What is being labeled

A *document* is the pre-extracted text (v0.1 does not parse DOCX/PDF/XLSX), plus its filename,
extension, embedded labels and small metadata map. Every document receives:

* exactly **one Sensitivity Level**: `PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `HIGHLY_CONFIDENTIAL`;
* **zero or more Data Categories**: `PII`, `PHI`, `FINANCIAL_PCI`, `SOURCE_CODE`,
  `CREDENTIALS_SECRETS`, `INTELLECTUAL_PROPERTY`, `TRADE_SECRET`, `MA_CORP_STRATEGY`.

Labels describe **the content**, not what the document says about itself. A banner reading
`PUBLIC` on a document full of card numbers does not make it public, and a filename containing
"Confidential" does not make a lunch menu confidential. Embedded labels are recorded as
`existing_labels` metadata and are *evidence*, never truth.

## 2. Deciding the Sensitivity Level

Apply in order; stop at the first that fits.

1. **Highly Confidential** if disclosure could cause severe legal, financial, competitive or
   personal harm: regulated personal data (health data, payment data, government identifiers),
   live credentials, non-public M&A or board-level strategy, trade secrets. Also the default for any
   document whose categories imply a Highly Confidential floor (section 3).
2. **Confidential** if disclosure to an external party would plausibly harm the organization or
   individuals but is not severe: non-public source code, unpublished research/inventions,
   personal contact details without government identifiers, sensitive commercial detail.
3. **Public** only if the document is explicitly intended for, or already in, public release
   (announced press releases, public FAQs, public datasheets, published papers, open-source code).
4. **Internal** otherwise: ordinary business content with no sensitive category and no
   public-release intent.

**Floors.** The gold level is never below the highest `level_floor` of the document's gold
categories. It may be *above* the floor when aggravated: government identifiers, bulk records
(a table of many individuals), or credentials that look live raise a `PII` document from
`CONFIDENTIAL` to `HIGHLY_CONFIDENTIAL`.

**Tie-break (ambiguity).** If, after applying the above, a careful reviewer could reasonably choose
either of two adjacent levels, the gold label is the **higher** level (fail-safe), the document is
flagged `ambiguity_flag=true`, and the lower level is recorded in `acceptable_alternative_levels`.
Scoring uses the gold level; the alternative is retained so a later analysis can report lenient
scoring separately.

## 3. Deciding categories

A category applies **only when its positive definition is clearly met**. When it is debatable, omit
the category and explain in `annotation_notes` (the level tie-break above already provides the
protective bias). Apply *all* categories that clearly apply.

| Category | Applies when | Does NOT apply when |
|---|---|---|
| `PII` | The document identifies an individual through government IDs, personal contact details, date of birth, or private personal circumstances about a named person. | Only business contact details (name, title, work email); masked values (`XXX-XX-1234`); blank forms; ID-lookalike strings that are not personal identifiers (part numbers). |
| `PHI` | Health information (diagnosis, treatment, results, claims) is tied to an identifiable person. If a name is the *only* identifier, label `PHI` only; add `PII` when other direct identifiers (contact details, DOB, government IDs) also appear. | Public health content; aggregated / de-identified statistics; blank intake forms. |
| `FINANCIAL_PCI` | Full payment-card numbers, bank account/IBAN/routing numbers, or material non-public company financial results/forecasts. | Last-four-only receipts; published statements; departmental expense summaries; 16-digit IDs that fail card validation; documented test-card numbers in public docs. |
| `SOURCE_CODE` | Non-public organization-owned code, queries, or build/config logic. | Open-source code under a public license; public tutorial snippets. |
| `CREDENTIALS_SECRETS` | Live-looking passwords, keys, tokens, or connection strings with embedded secrets. | Placeholders (`YOUR_API_KEY_HERE`), environment-variable lookups, vendor-documented example keys, prose about password policy. |
| `INTELLECTUAL_PROPERTY` | Non-public invention disclosures, draft patent claims, unpublished research or design detail. | Published patents/papers; public product documentation. |
| `TRADE_SECRET` | Non-public know-how whose value depends on secrecy: proprietary processes/formulations, internal logic of proprietary algorithms, compiled customer/pricing intelligence. | Generic industry practice; published methods. |
| `MA_CORP_STRATEGY` | Non-public acquisitions, divestitures, investments, restructurings, major partnership negotiations, board-level strategic plans. | Announced deals; historical/public case studies; routine operating plans. |

### Overlap rules

* **Trade Secret vs. Intellectual Property.** IP describes *what was invented or created* (invention
  disclosure, draft claims, unpublished results). Trade Secret describes *how the organization
  secretly does something valuable* (process parameters, formulations, internal algorithm logic,
  compiled customer/pricing intelligence). A document can be both; label both only if each
  definition is independently met.
* **Trade Secret vs. M&A / Corporate Strategy.** Strategy concerns *what the company intends to do*
  (buy, sell, restructure, enter/exit). Trade Secret concerns *know-how*. A customer-margin list is
  Trade Secret; a plan to acquire a competitor is M&A.
* **Source Code vs. Credentials.** Code containing a live-looking secret is both. Code that merely
  *reads* a secret from the environment is `SOURCE_CODE` only.
* **PII vs. PHI vs. Financial.** Label each whose definition is met. A payment export with names and
  full card numbers is `FINANCIAL_PCI`; add `PII` when other personal identifiers appear (address,
  email, DOB), not merely because a name appears.

## 4. Evidence annotation

Every document with at least one gold category has at least one **gold evidence span**: the
character range (`char_start`, `char_end`) and exact text that establishes the category, together
with the category it supports. Spans are the shortest passage that carries the sensitivity: a full
card number, a key assignment line, or the one or two sentences that reveal an unannounced
acquisition. They are produced by the generator at render time and checked to equal
`content[start:end]`.

## 5. Tiers

| Tier | Meaning | Gold rule |
|---|---|---|
| T1 Easy | Sensitivity is signaled by explicit patterns or markers. | Follow sections 2-3. |
| T2 Semantic | Sensitivity is carried by meaning; the text contains **no** obvious identifiers/keys/card numbers (checked by a lint). | Follow sections 2-3. |
| T3 Ambiguous | A careful reviewer could disagree. | Section 2 tie-break; `ambiguity_flag=true`. |
| T4 Hard negative | Superficially resembles a higher-sensitivity class but is not (`decoy_for` lists what it resembles). | Gold contains none of the `decoy_for` categories/levels. |
| T5 Adversarial | Contains prompt-injection text aimed at the classifier. | Gold is determined by the **real content**; instructions inside the document are ignored. Reported separately from the headline. |

## 6. Synthetic-data conventions

* All people, companies, hosts and addresses are invented. Emails use reserved `.example` domains;
  phone numbers use the fictional `555-01xx` range.
* SSN-like values use area numbers 900-999 and group numbers 01-49, which are never issued as SSNs
  or ITINs. Rules detectors must therefore rely on format + context, **not** on issuance-range
  validation. The same digit format is deliberately reused in hard negatives (part numbers) so the
  format alone is not a label cue.
* Card numbers are Luhn-valid values built on well-known public test prefixes; IBANs use a fictional
  bank code with a valid mod-97 check.
* Secrets use an obviously fake vendor prefix (`dgsk_`, `dgtok_`) or vendor-documented example keys.
  No provider-format live keys and no PEM private-key blocks are generated (they can trip GitHub
  push protection).

## 7. Known limitations

* **Single annotator, not human-reviewed.** Every family and gold label was authored by an AI
  assistant following these guidelines. `annotation_status` is `unreviewed` for every document until
  a human reviewer signs off on a sample (see the dataset spec).
* **Template-generated.** Documents within a family share structure; splitting is done by family so
  test families are unseen, but the corpus is still far less varied than real enterprise data.
* **Synthetic-to-real gap.** Metrics on this dataset say nothing certain about real data.

## 8. Clarifications after the blind review (2026-09-20)

Recorded as decisions A26-A28 in [`decisions.md`](decisions.md). These are guideline clarifications,
**not** taxonomy configuration: the taxonomy text is part of the LLM prompt, and changing it would
invalidate the recorded LLM responses. Sections 1-3 (which a blind reviewer is shown) are unchanged.

* **Customer-facing product and developer documentation is Public** without an explicit marker in
  the text: it is "already in public release" under section 2 item 3 when the document is published
  material (for example a quickstart on a public developer portal). Placeholder keys and
  vendor-documented example keys in such a document do not make it `CREDENTIALS_SECRETS`.
* **A medical record number does not, by itself, count as "another direct identifier"** for the PHI
  rule. A name plus an MRN is PHI only; add PII only when a section 3 identifier appears (contact
  details, date of birth, government id).
* **Two-rank ties.** For a draft that is intended for publication but not yet approved, the gold is
  the higher plausible level (`CONFIDENTIAL`) and both `PUBLIC` and the level between them
  (`INTERNAL`) are acceptable alternatives. This extends the section 2 tie-break across a two-rank gap.
* **De-identified aggregate health statistics with small cells** (decision A30, 2026-09-21): no named or
  directly identifiable individual means PHI is not met (the taxonomy lists de-identified aggregate patient
  statistics as a PHI counter-example), but small cell counts (single digits) can allow re-identification,
  so the level is `CONFIDENTIAL`, with `HIGHLY_CONFIDENTIAL` and `INTERNAL` as acceptable alternatives.
* **Historical teaching case studies of completed deals** are `PUBLIC` in the gold but genuinely
  ambiguous (independent readers split between `PUBLIC` and `INTERNAL`), so `INTERNAL` is an acceptable
  alternative (decision A32).
* **The gold tie-break stays fail-safe** (the higher of two defensible levels); a **lenient view**, in which a
  predicted level inside the acceptable alternatives counts as correct, is reported beside the strict
  score (decision A31). The strict score is what the gates use.

