# Blind classification review

> **AI-generated synthetic dataset — pending human gold-label review.** All people, companies, hosts and addresses in the documents are invented.

You are asked to label each document below **independently**, using only this file. You are not shown
any existing label, model output or earlier opinion, on purpose: your judgement is the measurement.

## Rules of the review

**What you are shown:** each document's filename and text, exactly as recorded. Other document fields
(extension, embedded labels, source or author metadata) are deliberately not part of this exercise, even
where the labeling rules below mention them.

1. Label from the document (its filename and text) and the definitions in this file only.
2. Do **not** look the sample ids up anywhere else (other files in this repository contain labels).
3. Do **not** use an AI model or a rule/pattern scanner to pre-label. Record your own reading.
4. If the definitions do not settle a case, say so (taxonomy ambiguity) instead of forcing certainty.
5. If the document itself does not give you enough to decide, say so (insufficient information).

## What to record for every document (columns of `blind_review_sheet.csv`)

| column | allowed values |
|---|---|
| `human_level` | one of `PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `HIGHLY_CONFIDENTIAL` (may be left blank ONLY if insufficient information is `yes`) |
| `human_categories` | zero or more of `PII`, `PHI`, `FINANCIAL_PCI`, `SOURCE_CODE`, `CREDENTIALS_SECRETS`, `INTELLECTUAL_PROPERTY`, `TRADE_SECRET`, `MA_CORP_STRATEGY`, separated by `;`; write `NONE` when no category applies |
| `human_confidence` | `low`, `medium`, `high` |
| `human_rationale` | one or two sentences, in your words: what in the document decided it, and which definition or rule you applied |
| `human_taxonomy_ambiguity` | `yes` if a careful reviewer could reasonably choose a different level or category set under these definitions; otherwise `no` |
| `human_alternative_levels` | if ambiguity is `yes` and a different LEVEL is also defensible, list it (`;`-separated); otherwise leave blank |
| `human_insufficient_information` | `yes` if the document does not give enough to decide; otherwise `no` |
| `reviewer_id`, `review_date` | your identifier and the date (YYYY-MM-DD) |

## Definitions (verbatim from the project taxonomy, version 1.0.0)

### Sensitivity levels (choose exactly one)

* **`PUBLIC`** (Public), rank 0: Approved for, or already in, public release. Disclosure causes no harm.
* **`INTERNAL`** (Internal), rank 1: General business information intended for employees. Limited harm if disclosed externally; contains no regulated, strategic or credential content.
* **`CONFIDENTIAL`** (Confidential), rank 2: Non-public business or personal information whose external disclosure could harm the organization or individuals. Need-to-know within the organization.
* **`HIGHLY_CONFIDENTIAL`** (Highly Confidential), rank 3: Regulated data, credentials, or strategically critical information whose disclosure could cause severe legal, financial, competitive or personal harm.

### Data categories (choose zero or more)

#### `PII` (PII)

Information that identifies, or can be used to identify, an individual: government IDs, personal contact details, dates of birth, or personal circumstances about a named person. Business contact details alone (name, title, work email) are NOT PII under this policy.

* Minimum level when this category applies (a configurable policy default): `CONFIDENTIAL`
* Positive examples: Employee record containing a national identification number and date of birth; Customer list with names, personal email addresses and home addresses; Manager notes about a named employee's private family or immigration circumstances
* Counter-examples (does NOT apply): Corporate directory listing name, job title and work email; Masked identifiers such as XXX-XX-1234; A blank onboarding form template with empty fields

#### `PHI` (PHI)

Individually identifiable health information: diagnoses, treatments, test results, or insurance and claim details linked to an identifiable person. If a name is the only identifier, label PHI only; add PII when other direct identifiers also appear.

* Minimum level when this category applies (a configurable policy default): `HIGHLY_CONFIDENTIAL`
* Positive examples: Clinic visit note naming the patient with diagnosis and prescribed medication; Lab results table keyed by medical record number
* Counter-examples (does NOT apply): Public health article about a condition; Aggregated, de-identified patient statistics; Blank patient intake form

#### `FINANCIAL_PCI` (Financial / PCI)

Payment-card and bank-account data (card numbers, IBANs, account and routing numbers) and material non-public company financial information such as unreleased earnings or forecasts.

* Minimum level when this category applies (a configurable policy default): `HIGHLY_CONFIDENTIAL`
* Positive examples: Payment export listing full card numbers; Wire instructions with a bank account number; Draft quarterly earnings narrative not yet released
* Counter-examples (does NOT apply): Receipt showing only the last four digits of a card; Published financial statements; Departmental expense summary; Sixteen-digit shipment or serial numbers that are not card numbers

#### `SOURCE_CODE` (Source Code)

Non-public source code, queries, and build or configuration logic owned by the organization. Publicly licensed open-source code is NOT Source Code under this policy.

* Minimum level when this category applies (a configurable policy default): `CONFIDENTIAL`
* Positive examples: Proprietary service module with an internal license header; Internal SQL migration scripts
* Counter-examples (does NOT apply): Open-source code published under a public license; Code snippets in a public tutorial

#### `CREDENTIALS_SECRETS` (Credentials / Secrets)

Live-looking authentication material: passwords, API keys, tokens, private keys, and connection strings with embedded secrets.

* Minimum level when this category applies (a configurable policy default): `HIGHLY_CONFIDENTIAL`
* Positive examples: .env file containing a database password and API key; Chat message pasting a service account password
* Counter-examples (does NOT apply): Placeholders such as YOUR_API_KEY_HERE; Code that reads a secret from an environment variable; Vendor-documented example keys; A description of the password-rotation policy

#### `INTELLECTUAL_PROPERTY` (Intellectual Property)

Non-public descriptions of inventions, designs, research results and creative works owned by the organization, such as invention disclosures, draft patent claims and unpublished research. If the content is a secret proprietary process, prefer Trade Secret (they may co-occur).

* Minimum level when this category applies (a configurable policy default): `CONFIDENTIAL`
* Positive examples: Invention disclosure form; Draft patent claims; Unpublished research notebook describing a novel method
* Counter-examples (does NOT apply): Published patent or journal paper; Public product documentation

#### `TRADE_SECRET` (Trade Secret)

Non-public know-how that derives value from secrecy: proprietary processes, formulations, internal logic of proprietary algorithms, and compiled customer or pricing intelligence.

* Minimum level when this category applies (a configurable policy default): `HIGHLY_CONFIDENTIAL`
* Positive examples: Manufacturing process parameters and tolerances; Compiled customer list with negotiated margins; Internal logic of a proprietary pricing algorithm
* Counter-examples (does NOT apply): Generic industry practice described in a textbook; Published method

#### `MA_CORP_STRATEGY` (M&A / Corporate Strategy)

Non-public mergers, acquisitions, divestitures, investments, restructurings, major partnership negotiations and board-level strategic plans.

* Minimum level when this category applies (a configurable policy default): `HIGHLY_CONFIDENTIAL`
* Positive examples: Spreadsheet of acquisition targets with rationale and valuation ranges; Board memo on an unannounced divestiture
* Counter-examples (does NOT apply): Press release about an already-announced deal; Historical or public business-school case study; Routine quarterly operating plan

## Labeling rules (verbatim excerpt of the project labeling guidelines)

### 1. What is being labeled

A *document* is the pre-extracted text (v0.1 does not parse DOCX/PDF/XLSX), plus its filename,
extension, embedded labels and small metadata map. Every document receives:

* exactly **one Sensitivity Level**: `PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `HIGHLY_CONFIDENTIAL`;
* **zero or more Data Categories**: `PII`, `PHI`, `FINANCIAL_PCI`, `SOURCE_CODE`,
  `CREDENTIALS_SECRETS`, `INTELLECTUAL_PROPERTY`, `TRADE_SECRET`, `MA_CORP_STRATEGY`.

Labels describe **the content**, not what the document says about itself. A banner reading
`PUBLIC` on a document full of card numbers does not make it public, and a filename containing
"Confidential" does not make a lunch menu confidential. Embedded labels are recorded as
`existing_labels` metadata and are *evidence*, never truth.

### 2. Deciding the Sensitivity Level

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

### 3. Deciding categories

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

#### Overlap rules

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

## Documents (33)

### Item 01 — `uc4-bfadb3a0f0`

Filename: `.gitlab-ci.yml`

```text
name: deploy-Indigo
on: push
jobs:
  build:
    steps:
      - run: make build && ./scripts/package.sh --target North America
      - run: curl -H "Authorization: Bearer dgtok_8b562c4a9d1a2bb819945bacb6988c90" https://deploy.corp.example/api/release
```

### Item 02 — `uc4-fb63604243`

Filename: `Supplier_Terms_Summary.docx`

```text
Sourcing notes (internal)

We secured an exclusive, multi-year supply of the key input at roughly 24.7% below market by co-investing in the supplier's second line. In return they will not sell that grade to our competitors. The yield curve we negotiated is what protects our margin.
Procurement leads only; do not forward to suppliers.
```

### Item 03 — `uc4-1807887255`

Filename: `Case_Study_Draft_Jadewater.docx`

```text
DRAFT customer story - awaiting customer approval

Jadewater Pharma reduced downtime by 34.4% after adopting our water-quality probe. "We expected a long rollout, but the team was live in weeks," said Noor Larsen, Analyst at Jadewater Pharma.
Marketing will publish once the customer signs off on the wording and the figures.
```

### Item 04 — `uc4-dd34b9e9fe`

Filename: `Merchant_Settlement_11.txt`

```text
Chargeback review - April 2028
case 3303: card 6011114419516482 amount $643 reason: fraud
case 6341: card 3528-0058-3206-1150 amount $554 reason: duplicate charge
case 5423: card 4111-1132-5849-7766 amount $632 reason: item not received
```

### Item 05 — `uc4-acc01a7faa`

Filename: `Weekly_Notes_Customer Success.docx`

```text
Customer Success team sync - Monday

Present: Ulrich Yamada, Pavel Fairchild, Elena Tremblay

Progress
- Finished the quarterly report template
- Updated the onboarding checklist
- Fixed the broken link on the internal wiki

Blockers
- Waiting on a review of the vendor evaluation checklist

Next week: plan the team offsite in Singapore, schedule the process retrospective.
```

### Item 06 — `uc4-9e0dbf644f`

Filename: `payments_gateway.py`

```text
# Copyright 2028 Blackfern Semiconductors. Internal.
import os

def get_connection():
    host = os.environ["DB_HOST"]
    user = os.environ["DB_USER"]
    password = os.environ["DB_PASSWORD"]  # injected by the secret manager at runtime
    return connect(host=host, user=user, password=password)
```

### Item 07 — `uc4-95ec10ed1c`

Filename: `New_Hire_Form_2026.pdf`

```text
NEW HIRE ONBOARDING FORM (TEMPLATE)
Full name: ______________________
Social Security Number: ______________________
Date of birth: ______________________
Home address: ______________________
Emergency contact: ______________________
Complete this form and return it to Katarina Castellano in Human Resources on your first day (2027-01-18).
```

### Item 08 — `uc4-fcfbdb789f`

Filename: `Manuscript_Fjord.docx`

```text
Draft manuscript - not yet submitted

Abstract. We propose a self-calibrating estimator for low-cost sensors and show that it reduces drift by 21.8% on a benchmark of 9 devices. The method requires no reference standard and runs on commodity hardware.
We plan to submit to a peer-reviewed venue once co-authors have approved the text.
```

### Item 09 — `uc4-f01402e5c4`

Filename: `Customer_Story_2028.docx`

```text
DRAFT customer story - awaiting customer approval

Quillon Holdings reduced downtime by 26.1% after adopting our edge gateway. "We expected a long rollout, but the team was live in weeks," said Gwendolyn Halvorsen, Senior Analyst at Quillon Holdings.
Marketing will publish once the customer signs off on the wording and the figures.
```

### Item 10 — `uc4-3a62ad2249`

Filename: `Lab_Notebook_5.docx`

```text
Research notebook - week 12

Today's experiment confirmed the idea we sketched last month: if the sensor's own noise is fed back as a calibration input, the device corrects itself without an external reference. Error dropped by about 66.7% in all 30 test runs. To our knowledge nobody has reported this approach.
Next: repeat on the second prototype, then talk to Amara Alvarez in Legal about protecting it before we present anywhere.
```

### Item 11 — `uc4-e9ab29f565`

Filename: `Teaching_Case_Larkspur.pdf`

```text
Case study: the Emberlight Capital-Larkspur Foods merger

In 2010, Emberlight Capital acquired Larkspur Foods in one of the largest deals in the precision components industry. The case examines how the acquirer financed the purchase, how integration unfolded, and why the combined firm struggled with culture clashes in its first years.

Discussion questions: Was the price justified? What would you have done differently in integration?
```

### Item 12 — `uc4-089c6dc891`

Filename: `Case_Study_Merger_Lighthouse.pdf`

```text
Case study: the Nettlefield Capital-Whitcombe Foods merger

In 2007, Nettlefield Capital acquired Whitcombe Foods in one of the largest deals in the diagnostic devices industry. The case examines how the acquirer financed the purchase, how integration unfolded, and why the combined firm struggled with culture clashes in its first years.

Discussion questions: Was the price justified? What would you have done differently in integration?
```

### Item 13 — `uc4-b16d04f2c2`

Filename: `Weekly_Notes_Operations.docx`

```text
Operations team sync - Tuesday

Present: Leila Underwood, Vikram Yamada, Uma Eriksen

Progress
- Finished the quarterly report template
- Updated the onboarding checklist
- Fixed the broken link on the internal wiki

Blockers
- Waiting on a review of the vendor evaluation checklist

Next week: plan the team offsite in Lisbon, schedule the process retrospective.
```

### Item 14 — `uc4-fac2c886c3`

Filename: `Case_Study_Merger_Horizon.pdf`

```text
Case study: the Emberlight Cloud-Elmsworth Mobility merger

In 2016, Emberlight Cloud acquired Elmsworth Mobility in one of the largest deals in the precision components industry. The case examines how the acquirer financed the purchase, how integration unfolded, and why the combined firm struggled with culture clashes in its first years.

Discussion questions: Was the price justified? What would you have done differently in integration?
```

### Item 15 — `uc4-00ac341854`

Filename: `README_sdk.md`

```text
# Quickstart

Create an API key in your dashboard, then export it:

    export API_KEY=YOUR_API_KEY_HERE

Example request:

    curl -H "Authorization: Bearer YOUR_API_KEY_HERE" https://api.nettlefieldrobotics.example/v1/status

For AWS integration you will see keys like AKIAIOSFODNN7EXAMPLE and wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY in the vendor's own documentation; these are documented examples, not real credentials.
```

### Item 16 — `uc4-2154faf7ae`

Filename: `Join_Foxglove.txt`

```text
Join Foxglove Devices

We are hiring a Engineer in Legal to work with a small team on fleet tracker products. You will collaborate with customers, ship features and help shape our roadmap.

What we offer: flexible working, learning budget, and a friendly team in Valencia.
How to apply: send a short note and your resume through our public careers page.
```

### Item 17 — `uc4-4b84f54ee7`

Filename: `Rx_Record_25439.txt`

```text
PRESCRIPTION RECORD
Patient: Keiko Ashworth  (MRN-7473774)
Filled: 2027-12-01
Medication: metformin 500 mg - 62 day supply, indication stage 3 chronic kidney disease
Pharmacist: Nikhil Pellegrino
```

### Item 18 — `uc4-2472fae2da`

Filename: `Customer_Story_2028.docx`

```text
DRAFT customer story - awaiting customer approval

Dunmore Systems reduced downtime by 34.3% after adopting our scheduling app. "We expected a long rollout, but the team was live in weeks," said Liam Gallagher, Architect at Dunmore Systems.
Marketing will publish once the customer signs off on the wording and the figures.
```

### Item 19 — `uc4-c8334a8e38`

Filename: `Hiring_FAQ_2026.md`

```text
Careers at Yewtree Retail - Frequently Asked Questions

Q: Where are your offices?
A: We have offices in Austin, Kraków and Calgary, and many roles can be done remotely.

Q: How long does the hiring process take?
A: Most candidates hear from us within 4 business days of applying, and the full process typically takes 2 weeks.

Q: Do you sponsor work visas?
A: Yes, for many engineering and research roles. Ask your recruiter for details.

Apply at www.yewtreeretail.example/careers. We are an equal opportunity employer.
```

### Item 20 — `uc4-19c5bd1307`

Filename: `Pharmacy_Log_November.csv`

```text
PRESCRIPTION RECORD
Patient: Theo Bianchi  (MRN-9952462)
Filled: 2026-12-26
Medication: sertraline 50 mg - 18 day supply, indication generalized anxiety disorder
Pharmacist: Anneliese Oduya
```

### Item 21 — `uc4-de62c38c69`

Filename: `Pharmacy_Log_February.csv`

```text
PRESCRIPTION RECORD
Patient: Felix Iglesias  (MRN-0984219)
Filled: 2025-09-07
Medication: ibuprofen 400 mg - 46 day supply, indication essential hypertension
Pharmacist: Joaquin Delacroix
```

### Item 22 — `uc4-c2223643cf`

Filename: `Pharmacy_Log_December.csv`

```text
PRESCRIPTION RECORD
Patient: Theo Hoffmann  (MRN-1711661)
Filled: 2027-03-19
Medication: amlodipine 5 mg - 20 day supply, indication rheumatoid arthritis
Pharmacist: Paloma Brennan
```

### Item 23 — `uc4-59fba52cb2`

Filename: `Careers_FAQ.html`

```text
Careers at Pinecrest Labs - Frequently Asked Questions

Q: Where are your offices?
A: We have offices in Porto, Minneapolis and Bergen, and many roles can be done remotely.

Q: How long does the hiring process take?
A: Most candidates hear from us within 9 business days of applying, and the full process typically takes 2 weeks.

Q: Do you sponsor work visas?
A: Yes, for many engineering and research roles. Ask your recruiter for details.

Apply at www.pinecrestlabs.example/careers. We are an equal opportunity employer.
```

### Item 24 — `uc4-a278fd5603`

Filename: `Teaching_Case_Westerly.pdf`

```text
Case study: the Pinecrest Industries-Westerly Holdings merger

In 2006, Pinecrest Industries acquired Westerly Holdings in one of the largest deals in the precision components industry. The case examines how the acquirer financed the purchase, how integration unfolded, and why the combined firm struggled with culture clashes in its first years.

Discussion questions: Was the price justified? What would you have done differently in integration?
```

### Item 25 — `uc4-9d42fc85c9`

Filename: `authentication.md`

```text
# Quickstart

Create an API key in your dashboard, then export it:

    export API_KEY=YOUR_API_KEY_HERE

Example request:

    curl -H "Authorization: Bearer YOUR_API_KEY_HERE" https://api.pembertoncapital.example/v1/status

For AWS integration you will see keys like AKIAIOSFODNN7EXAMPLE and wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY in the vendor's own documentation; these are documented examples, not real credentials.
```

### Item 26 — `uc4-10e1c66f39`

Filename: `README_sdk.md`

```text
# Quickstart

Create an API key in your dashboard, then export it:

    export API_KEY=YOUR_API_KEY_HERE

Example request:

    curl -H "Authorization: Bearer YOUR_API_KEY_HERE" https://api.ashgrovesemiconductors.example/v1/status

For AWS integration you will see keys like AKIAIOSFODNN7EXAMPLE and wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY in the vendor's own documentation; these are documented examples, not real credentials.
```

### Item 27 — `uc4-35ffba48e4`

Filename: `authentication.md`

```text
# Quickstart

Create an API key in your dashboard, then export it:

    export API_KEY=YOUR_API_KEY_HERE

Example request:

    curl -H "Authorization: Bearer YOUR_API_KEY_HERE" https://api.thornfieldcapital.example/v1/status

For AWS integration you will see keys like AKIAIOSFODNN7EXAMPLE and wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY in the vendor's own documentation; these are documented examples, not real credentials.
```

### Item 28 — `uc4-521a321e60`

Filename: `Customer_Story_2027.docx`

```text
DRAFT customer story - awaiting customer approval

Silverpine Devices reduced downtime by 28.5% after adopting our billing portal. "We expected a long rollout, but the team was live in weeks," said Joaquin Oduya, Lead at Silverpine Devices.
Marketing will publish once the customer signs off on the wording and the figures.
```

### Item 29 — `uc4-02400f99b0`

Filename: `api_quickstart.md`

```text
# Quickstart

Create an API key in your dashboard, then export it:

    export API_KEY=YOUR_API_KEY_HERE

Example request:

    curl -H "Authorization: Bearer YOUR_API_KEY_HERE" https://api.valemontpayments.example/v1/status

For AWS integration you will see keys like AKIAIOSFODNN7EXAMPLE and wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY in the vendor's own documentation; these are documented examples, not real credentials.
```

### Item 30 — `uc4-f79ebb3064`

Filename: `Customer_Story_2026.docx`

```text
DRAFT customer story - awaiting customer approval

Bellhaven Software reduced downtime by 22.6% after adopting our insulin pump controller. "We expected a long rollout, but the team was live in weeks," said Liam Grigoryan, Manager at Bellhaven Software.
Marketing will publish once the customer signs off on the wording and the figures.
```

### Item 31 — `uc4-c885ab8c28`

Filename: `Pharmacy_Log_April.csv`

```text
PRESCRIPTION RECORD
Patient: Olivia Ibrahim  (MRN-3845359)
Filled: 2025-10-15
Medication: sumatriptan 50 mg - 66 day supply, indication moderate persistent asthma
Pharmacist: Rosalind Halvorsen
```

### Item 32 — `uc4-210e112c7e`

Filename: `Case_Study_Merger_Lantern.pdf`

```text
Case study: the Pinecrest Biosciences-Larkspur Energy merger

In 2014, Pinecrest Biosciences acquired Larkspur Energy in one of the largest deals in the industrial sensors industry. The case examines how the acquirer financed the purchase, how integration unfolded, and why the combined firm struggled with culture clashes in its first years.

Discussion questions: Was the price justified? What would you have done differently in integration?
```

### Item 33 — `uc4-4c7bd732c6`

Filename: `Case_Study_Draft_Redwater.docx`

```text
DRAFT customer story - awaiting customer approval

Redwater Biosciences reduced downtime by 39.7% after adopting our battery pack. "We expected a long rollout, but the team was live in weeks," said Priya Espinoza, Senior Analyst at Redwater Biosciences.
Marketing will publish once the customer signs off on the wording and the figures.
```
