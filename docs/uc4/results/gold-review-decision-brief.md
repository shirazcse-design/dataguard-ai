# Gold-label review: decision brief (A / B / C)

> **AI-generated synthetic dataset — pending human gold-label review.** This brief presents options; it changes nothing. No gold label, taxonomy, schema v1.0, prompt, threshold, model selection or the frozen hybrid configuration was modified, and the **locked test split was not read, scored or used** (`locked_test_access.jsonl` is 0 bytes).
>
> Written by an AI assistant that also wrote the dataset and the earlier adjudication. Everything below is a proposal for a human to accept, reject or modify. Numbers are copied from the executed, hypothetical (in-memory) scenarios in [`gold-review.md`](gold-review.md); nothing here was re-scored or re-tuned.

## Read this first: a correction to the PR #8 analysis

While building the blind package I checked what each document carries besides its text. The dataset's `metadata.source_system` for the five `hn_public_api_docs_placeholder_keys` documents is **`Public developer portal`**, and for the five `hn_business_case_study` documents it is **`Public teaching materials`**. The PR #8 adjudication (and its Option A recommendation) argued from the text and the filename only, and stated that this family had no public-release marker, unlike every other PUBLIC-gold family. That statement is true of the *text* and false of the *document record*: the gold author's basis for PUBLIC is very likely this metadata field.

* The LLM and ML classifiers **never see metadata**, by pre-registered design (`config/llm/llm.v1.yaml`: `include_metadata: false`, "spoofable and NOT shown to the model"). So the models' INTERNAL answers are correct *content-only* answers; the 5 level errors that drive the 0.631 lower bound are an **information gap between the gold author's input and the classifier's input**, more than a classifier weakness or a plain label mistake.
* The PR #8 recommendation ("Option A, relabel to INTERNAL") therefore rested on an incomplete premise. **I no longer recommend applying it on that basis.** The adjudication artifacts from PR #8 are left unchanged, as instructed; this note supersedes their reasoning on this one point.
* Whether the labeling guidelines' rule "Public only if the document is explicitly intended for, or already in, public release" is met by a `source_system` of "Public developer portal" is a policy question for a human. The blind package deliberately shows filename and text only (like the models), so it measures *content-only* defensibility and cannot settle the metadata question. See "Decision D" below.

## A. `hn_public_api_docs_placeholder_keys` (and, by the same rule, `hn_business_case_study`)

| | |
|---|---|
| **Samples affected (dev, 5)** | `uc4-00ac341854`, `uc4-02400f99b0`, `uc4-10e1c66f39`, `uc4-35ffba48e4`, `uc4-9d42fc85c9` |
| **Related, calibration (5); no proposal, settled by the same rule** | `uc4-089c6dc891`, `uc4-210e112c7e`, `uc4-a278fd5603`, `uc4-e9ab29f565`, `uc4-fac2c886c3` |
| **Current gold** | level `PUBLIC`, categories `[]`, `ambiguity_flag` false, `acceptable_alternative_levels` `[]` |
| **What the text is** | customer-facing developer documentation (a quickstart with `YOUR_API_KEY_HERE` placeholders and vendor-documented example AWS keys); the calibration family is a 2007 merger teaching case with discussion questions |

**Option A1 (the PR #8 proposal): relabel.** `INTERNAL`, categories `[]` (unchanged), `ambiguity_flag` true, `acceptable_alternative_levels` `[PUBLIC]`.
* Reason: nothing in the *text* says the file is released or intended for release.
* Rule supporting it: labeling guidelines §2 item 3 ("Public only if the document is explicitly intended for, or already in, public release") and the §2 tie-break (adjacent levels both defensible: gold is the higher, flagged ambiguous, lower level kept as an alternative); taxonomy `PUBLIC`: "Approved for, or already in, public release".
* Weakness (new): the document record's `source_system` says "Public developer portal", which is at least arguably "already in public release".

**Option A2: keep `PUBLIC` (no label change).** Optionally add a clause to the `PUBLIC` level (or to §2 item 3) stating that customer-facing product/developer documentation and historical or teaching material about completed public events is Public without a marker in the text, or that a public `source_system` counts as a release marker.
* Reason: the gold author's evidence (metadata) is consistent with the guideline; the models are wrong only because they are not given it.
* Rule supporting it: the same §2 item 3 ("already in public release"); the `CREDENTIALS_SECRETS` and `INTELLECTUAL_PROPERTY` counter-examples (placeholders, vendor-documented example keys, public product documentation) show the categories are correctly empty either way.
* Weakness: a text-only reader cannot reach PUBLIC; the taxonomy as written does not say metadata counts. Any clause added is a taxonomy change (needs your approval; not made).

**Expected evaluation impact** (dev, T1–T4 headline, family-level bootstrap over 18 independent families; hypothetical, labels applied in memory only). A2 = S0 (no change). A1 = S1. Categories and high-risk metrics do not move under A.

| approach | level macro-F1, A2 / current gold (S0) | level macro-F1, A1 relabel (S1) |
|---|---|---|
| hybrid (frozen `default`) | 0.870 [0.631, 1.000] | 1.000 [1.000, 1.000] |
| llm_mid | 0.870 [0.631, 1.000] | 1.000 [1.000, 1.000] |
| llm_large | 0.870 [0.631, 1.000] | 1.000 [1.000, 1.000] |
| llm_small | 0.918 [0.787, 1.000] | 0.836 [0.644, 1.000] |
| rules | 0.358 [0.222, 0.592] | 0.386 [0.247, 0.682] |
| ml | 0.232 [0.134, 0.392] | 0.270 [0.170, 0.379] |

Cautions: (1) all 5 of the hybrid's headline level errors are in this one family, so this single decision moves the 0.631 lower bound; (2) a `[1.000, 1.000]` interval means no errors on the remaining families of a small, template-generated dataset, not proven quality; (3) A1 would be a label change made after seeing the models disagree on dev, which is tuning on dev; the dev result would not be evidence for a gate; (4) PUBLIC gold support on dev is 2 families (1 after A1), so any level macro-F1 here stays fragile.

## B. `phi_prescription_record`: does a medical record number count as another direct identifier?

| | |
|---|---|
| **Samples affected (dev, 5)** | `uc4-19c5bd1307`, `uc4-4b84f54ee7`, `uc4-c2223643cf`, `uc4-c885ab8c28`, `uc4-de62c38c69` |
| **Current gold** | level `HIGHLY_CONFIDENTIAL`, categories `[PHI]` (annotation: "name is the only direct identifier so PHI only") |
| **Content** | a prescription record: patient name, an MRN, medication and indication |

**Option B(i): an MRN counts.** Gold becomes `HIGHLY_CONFIDENTIAL`, `[PHI, PII]`; append to the PHI description: "A medical record number, employee id or similar record identifier counts as another direct identifier."
* Rule for: the PHI rule ("add PII when other direct identifiers also appear"); ordinary usage treats an MRN as a direct patient identifier; the taxonomy's PHI positive example is a "lab results table keyed by medical record number".

**Option B(ii): an MRN does not count.** No label change; append the mirror clause ("does not count").
* Rule for: the `PII` definition lists "government IDs, personal contact details, dates of birth, or personal circumstances", none of which is an MRN; guidelines §3 PHI row names "(contact details, DOB, government IDs)" as the further identifiers. The gold follows the letter of the rule. `phi_workplace_injury_report` is PHI + PII only because of a personal phone number, which is consistent.

**Expected evaluation impact** (category macro-F1; level and high-risk unchanged because both readings are Highly Confidential; B(ii) = current gold). B(i) values are the S2 scenario (S2 = A1 + B(i); A does not change categories, so its category column equals B(i) alone):

| approach | category macro-F1, B(ii) / current | category macro-F1, B(i) |
|---|---|---|
| hybrid | 1.000 [1.000, 1.000] | 0.977 [0.857, 1.000] |
| llm_mid | 1.000 [1.000, 1.000] | 0.977 [0.857, 1.000] |
| llm_large | 0.990 [0.958, 1.000] | 0.987 [0.939, 1.000] |
| llm_small | 0.932 [0.881, 1.000] | 0.930 [0.875, 1.000] |
| rules | 0.522 [0.333, 0.757] | 0.522 [0.333, 0.700] |
| ml | 0.433 [0.257, 0.628] | 0.433 [0.257, 0.595] |

B(i) would make the hybrid "miss" PII on 5 documents. Same tuning-on-dev caution as A.

## C. `amb_customer_case_study_draft`: acceptable alternative levels (metadata only)

| | |
|---|---|
| **Samples affected (dev, 6)** | `uc4-1807887255`, `uc4-2472fae2da`, `uc4-4c7bd732c6`, `uc4-521a321e60`, `uc4-f01402e5c4`, `uc4-f79ebb3064` |
| **Current gold** | level `CONFIDENTIAL`, categories `[]`, `ambiguity_flag` true, `acceptable_alternative_levels` `[PUBLIC]` |
| **Proposed** | add `INTERNAL`: alternatives `[PUBLIC, INTERNAL]`; level, categories and flag unchanged |
| **Reason** | A customer story that is intended for publication but awaits customer approval can reasonably be Public (imminent release), Internal (ordinary marketing draft) or Confidential (unapproved customer figures). |
| **Rule** | guidelines §2 tie-break is stated for **adjacent** levels; PUBLIC to CONFIDENTIAL skips INTERNAL, and the annotation itself says "Public vs Confidential (Internal lies between)". Whether the tie-break applies across a two-rank gap is the policy question. |
| **Expected impact** | **None on strict scoring** (`acceptable_alternative_levels` is not used by the headline metrics); only a future lenient-scoring analysis would see it. No approach disagrees with the current gold, and agreement here is weak evidence because the LLM prompt encodes the same tie-break that produced the gold. |

## D. New question raised by the metadata finding

Should the blind review be repeated (or extended) **with** the `source_system` field shown? Today's package shows filename and text only, matching what the classifiers receive, so it answers "is this label defensible from the content?". A second pass with metadata would answer "is it defensible from the whole document record the gold author used?". Both are cheap; the second is a small generator flag, not built yet because you asked for content only. Separately, whether classifiers should ever receive `source_system` is a model-configuration question (`include_metadata`), which I have not touched.

## What I recommend (recommendations, not decisions)

1. **Apply nothing yet.** Run the blind review first (see [`../blind-review.md`](../blind-review.md)); the disputed documents are 21 of the 33 items.
2. Treat A as **withdrawn as a "gold error"** and reopen it as a policy question (D): does a public `source_system` count as a release marker? Settle that, then choose A1 or A2, and settle `hn_business_case_study` with it.
3. B and C are independent policy clarifications; B(ii) or C need no re-scoring, B(i) does.
4. Whatever is decided, apply it in a separate, audited step (dataset version bump, re-run of results), and take the locked-test decision only afterwards.
