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

## What to record for every document (columns of `blind_review_round2_sheet.csv`)

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

## Documents (28)

### Item 01 — `uc4-9d42fc85c9`

Filename: `authentication.md`

```text
# Quickstart

Create an API key in your dashboard, then export it:

    export API_KEY=YOUR_API_KEY_HERE

Example request:

    curl -H "Authorization: Bearer YOUR_API_KEY_HERE" https://api.pembertoncapital.example/v1/status

For AWS integration you will see keys like AKIAIOSFODNN7EXAMPLE and wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY in the vendor's own documentation; these are documented examples, not real credentials.
```

### Item 02 — `uc4-9a94d05821`

Filename: `Vendor_Invoice_Tidewater.pdf`

```text
INVOICE INV-2027-4294
From: Tidewater Energy
To: Oakhurst Semiconductors
Date: 2025-02-04
Services: consulting, 48 hours
Amount due: $11,667

Remit payment by bank transfer:
Routing: 551692569  Account: 50192561070
```

### Item 03 — `uc4-fb41e91266`

Filename: `Programme_Outcomes_Q3.docx`

```text
Harborview Health Center programme outcomes - Q3

Of 874 enrolled patients, 5 in the under-30 group had a hospital admission and 4 in the over-80 group discontinued treatment. In the Valencia site the smallest subgroup (n=4) showed the largest improvement.

Data are aggregated and contain no names or identifiers.
```

### Item 04 — `uc4-cfac4adf77`

Filename: `New_Hire_Form_2027.pdf`

```text
Mistral Payments - Employee Data Sheet (blank template, revision 2)
Legal name ............................
Tax identification no. ................
Birth date ............................
Residential address ...................
Bank details for payroll ..............
Please fill in every line and hand the form to Marisol Alvarez in HR. Do not email completed forms.
```

### Item 05 — `uc4-dd34b9e9fe`

Filename: `Merchant_Settlement_11.txt`

```text
Chargeback review - April 2028
case 3303: card 6011114419516482 amount $643 reason: fraud
case 6341: card 3528-0058-3206-1150 amount $554 reason: duplicate charge
case 5423: card 4111-1132-5849-7766 amount $632 reason: item not received
```

### Item 06 — `uc4-791425f3af`

Filename: `Lab_Notebook_11.docx`

```text
Research notebook - week 35

Today's experiment confirmed the idea we sketched last month: if the sensor's own noise is fed back as a calibration input, the device corrects itself without an external reference. Error dropped by about 66.6% in all 10 test runs. To our knowledge nobody has reported this approach.
Next: repeat on the second prototype, then talk to Willa Nakamura in Legal about protecting it before we present anywhere.
```

### Item 07 — `uc4-8a7180ec97`

Filename: `cleanup_tool.py`

```text
# helper for the weekly ops report
import csv
HOST = "db-12.corp.example"

def load_rows(path):
    with open(path) as f:
        return list(csv.DictReader(f))

def summarise(rows):
    return {"count": len(rows), "hosts": sorted({r["host"] for r in rows})}
```

### Item 08 — `uc4-4fb7c238e1`

Filename: `app_trace_46.txt`

```text
2026-01-12 09:14:02 INFO  request GET /v1/accounts status=200 user=svc-billing
2026-09-12 09:14:02 DEBUG auth header Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJzdmMtMTMyMyIsImRnIjoic3ludGhldGljIiwiZXhwIjoxOTAwMDAwMDAwfQ.I1uT8f2yF1fyu6B8yOxG5L6kTwxBcRIan85LieJFvqh
2027-11-24 09:14:03 INFO  request POST /v1/transfers status=202 user=svc-billing
```

### Item 09 — `uc4-8d31d6cc41`

Filename: `Vendor_Terms_2028.docx`

```text
Contract summary - Emberlight Cloud

Term: 5 years from June 2028. Annual fee: $3.8M. Payment: net 50 days. Termination for convenience with 71 days' notice. Liability capped at annual fees. Auto-renews unless notice is given 68 days before expiry.
```

### Item 10 — `uc4-205126d683`

Filename: `Programme_Outcomes_Q4.docx`

```text
Meadow Pediatrics programme outcomes - Q4

Of 825 enrolled patients, 6 in the under-30 group had a hospital admission and 6 in the over-80 group discontinued treatment. In the Ottawa site the smallest subgroup (n=3) showed the largest improvement.

Data are aggregated and contain no names or identifiers.
```

### Item 11 — `uc4-14696d668c`

Filename: `Cohort_Summary_2028.pdf`

```text
Riverside Family Clinic programme outcomes - Q1

Of 722 enrolled patients, 3 in the under-30 group had a hospital admission and 7 in the over-80 group discontinued treatment. In the Kraków site the smallest subgroup (n=3) showed the largest improvement.

Data are aggregated and contain no names or identifiers.
```

### Item 12 — `uc4-19c5bd1307`

Filename: `Pharmacy_Log_November.csv`

```text
PRESCRIPTION RECORD
Patient: Theo Bianchi  (MRN-9952462)
Filled: 2026-12-26
Medication: sertraline 50 mg - 18 day supply, indication generalized anxiety disorder
Pharmacist: Anneliese Oduya
```

### Item 13 — `uc4-c8334a8e38`

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

### Item 14 — `uc4-6454537dc7`

Filename: `Weekly_Notes_Facilities.docx`

```text
Facilities team sync - Thursday

Present: Yusuf Mbeki, Rohan Choudhury, Yusuf Montgomery

Progress
- Finished the quarterly report template
- Updated the onboarding checklist
- Fixed the broken link on the internal wiki

Blockers
- Waiting on a review of the vendor evaluation checklist

Next week: plan the team offsite in Porto, schedule the process retrospective.
```

### Item 15 — `uc4-66c7774ba3`

Filename: `1on1_Notes_Oluwaseun.docx`

```text
One-to-one notes - Oluwaseun Hargrove (Legal)

Oluwaseun told me in confidence that they are going through a divorce and are also waiting on the outcome of an immigration application, which is why they have asked to work from Leeds for a few months.
I said I would keep this between us and HR. Agreed a lighter workload until December.
```

### Item 16 — `uc4-633f3b10fe`

Filename: `db_client.py`

```text
// Internal - Inkwell Aerospace
const apiKey = process.env.PAYMENTS_API_KEY;
if (!apiKey) {
  throw new Error("PAYMENTS_API_KEY is not set");
}
const client = new PaymentsClient({ apiKey });
```

### Item 17 — `uc4-ecb3c33352`

Filename: `Careers_FAQ.html`

```text
Join Alderbrook Networks

We are hiring a Architect in Data Science to work with a small team on fleet tracker products. You will collaborate with customers, ship features and help shape our roadmap.

What we offer: flexible working, learning budget, and a friendly team in Lyon.
How to apply: send a short note and your resume through our public careers page.
```

### Item 18 — `uc4-97a4c07fb4`

Filename: `New_Hire_Form_2026.pdf`

```text
Umberton Foods - Employee Data Sheet (blank template, revision 5)
Legal name ............................
Tax identification no. ................
Birth date ............................
Residential address ...................
Bank details for payroll ..............
Please fill in every line and hand the form to Leila Haddad in HR. Do not email completed forms.
```

### Item 19 — `uc4-de0bb4eb1c`

Filename: `Postmortem_177.docx`

```text
Postmortem - partial outage on Wednesday

Summary: the primary database host auth-01.corp.example ran out of disk after a batch job wrote unexpected temporary files. Failover to db-02.corp.example took 34 minutes because the replica lagged.
Impact: about 9.0% of requests failed for 85 minutes.
Actions: add disk alerts, cap batch temp usage, rehearse failover quarterly.
```

### Item 20 — `uc4-2154faf7ae`

Filename: `Join_Foxglove.txt`

```text
Join Foxglove Devices

We are hiring a Engineer in Legal to work with a small team on fleet tracker products. You will collaborate with customers, ship features and help shape our roadmap.

What we offer: flexible working, learning budget, and a friendly team in Valencia.
How to apply: send a short note and your resume through our public careers page.
```

### Item 21 — `uc4-fac2c886c3`

Filename: `Case_Study_Merger_Horizon.pdf`

```text
Case study: the Emberlight Cloud-Elmsworth Mobility merger

In 2016, Emberlight Cloud acquired Elmsworth Mobility in one of the largest deals in the precision components industry. The case examines how the acquirer financed the purchase, how integration unfolded, and why the combined firm struggled with culture clashes in its first years.

Discussion questions: Was the price justified? What would you have done differently in integration?
```

### Item 22 — `uc4-f01402e5c4`

Filename: `Customer_Story_2028.docx`

```text
DRAFT customer story - awaiting customer approval

Quillon Holdings reduced downtime by 26.1% after adopting our edge gateway. "We expected a long rollout, but the team was live in weeks," said Gwendolyn Halvorsen, Senior Analyst at Quillon Holdings.
Marketing will publish once the customer signs off on the wording and the figures.
```

### Item 23 — `uc4-da9e1d43b1`

Filename: `Employee_Directory_2027.xlsx`

```text
name,title,department,work_email,office
Sabine Pellegrino,Senior Analyst,Sales,sabine.pellegrino@bellhavenhealth.example,Denver
Hassan Sokolov,Specialist,Data Science,hassan.sokolov@bellhavenhealth.example,Denver
Marcus Ramanathan,Architect,Operations,marcus.ramanathan@bellhavenhealth.example,Bristol
Pavel Jovanovic,Staff Engineer,Human Resources,pavel.jovanovic@bellhavenhealth.example,Bristol
```

### Item 24 — `uc4-31b9634466`

Filename: `legacy_accounts_72.csv`

```text
username,password_hash
Bruno_80,$2b$12$ozFhYBahu2ZBxTamq3fOsGlAbFlbTgGMKN8B0Tye4Skau0Gt3CcQV
Keiko_68,$2b$12$ndDP6MpFdZkFnNCMAe9jM4K9iYxMspYf9xbjIAgBaGra3rkwTA5S4
Jasmine_77,$2b$12$aDUWrsO7eoSLhMwLJ7zwKxK4aRipBkJIH5Zwd4kKvuzPpcvF7G3xn
```

### Item 25 — `uc4-27c61a57f0`

Filename: `Talk_Draft_Seattle.docx`

```text
Talk outline for the Seattle conference

We will present our approach to on-device calibration, including the feedback structure and the accuracy gains we measured across 21 units.
Legal has cleared a general description; the detailed algorithm is not to be shown. Slides due December.
```

### Item 26 — `uc4-bea9a6819d`

Filename: `Manuscript_Ironclad.docx`

```text
Draft manuscript - not yet submitted

Abstract. We propose a self-calibrating estimator for low-cost sensors and show that it reduces drift by 41.7% on a benchmark of 37 devices. The method requires no reference standard and runs on commodity hardware.
We plan to submit to a peer-reviewed venue once co-authors have approved the text.
```

### Item 27 — `uc4-5317e7d5e6`

Filename: `Board_Minutes_July.docx`

```text
Minutes of the board meeting

Present: all directors and Rosalind Castellano, secretary.
1. Minutes of the previous meeting approved.
2. The CEO reported on progress against the annual plan and the competitive landscape in Nordics.
3. The committee chairs gave their updates. The audit committee noted no material issues.
4. Directors discussed long-term priorities and agreed to revisit strategy at the next meeting.
5. Any other business: none.
```

### Item 28 — `uc4-4aaea9fc3f`

Filename: `Compensation_Bands_2026.xlsx`

```text
Compensation bands - 2026

Level 3: $69,680 to $80,014
Level 4: $97,798 to $113,262
Level 5: $101,335 to $153,766

Managers may share the band for their own team's roles when hiring.
```
