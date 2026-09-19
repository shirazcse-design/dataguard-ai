# Rules Engine changelog and tuning log

Purpose: make every change made **after the first evaluation** visible, with its reason and its
classification, so the results cannot be read as if the rules had been written blind. The detector
catalog ([`rules-catalog.md`](rules-catalog.md)) was committed before any rule existed.

Change classes:

* **Defect fix**: a rule does not do what the pre-registered catalog says it should.
* **Tuning**: coverage or a threshold changed because of an *observed failure*. This is the kind of
  change that risks fitting the synthetic dataset, so each one is called out and justified.

Protocol: errors are inspected on **train** only. **dev** is evaluated at checkpoints. **calibration**
is untouched until the final report. The **locked test split is not used** in Phase 3.

Disclosure: the assistant that wrote the rules also authored the dataset.

---

## Checkpoint 1 - ruleset 1.0.0 (initial implementation)

All numbers below are from executed runs (`eval run --classifier rules`), headline tiers T1-T4.

| split | headline docs / families | level macro-F1 | category macro-F1 | high-risk P / R / F1 / FPR | abstained | T4 decoy-hit |
|---|---|---|---|---|---|---|
| train | 391 / 71 | 0.393 | 0.706 | 0.962 / 0.602 / 0.741 / 0.028 | 231 of 416 (0.555) | 0.111 |
| dev | 97 / 18 | 0.358 | 0.522 | 1.000 / 0.623 / 0.767 / 0.000 | 66 of 107 (0.617) | 0.000 |

Train error inspection found:

* **Zero false-positive categories.** One hard-negative failure: the `STRICTLY CONFIDENTIAL` lunch
  menu (the engine honors an explicit banner; see "design gaps").
* **Defects / gaps triaged as changeable:**
  1. `ma.markers` was suppressed on a genuine letter of intent because the announced-deal lexicon
     contained "shares of" (meant for "*Shares of X rose*"), which also occurs in "acquire all
     outstanding *shares of* ...". **Defect fix.**
  2. `pass = <value>` (INI style) was not recognised: `pass` was not in the key list. **Tuning.**
  3. `password: Willow#Willow22` was rejected as low entropy: repeated words depress Shannon
     entropy although the value has four character classes. Entropy discriminates human-chosen
     passwords poorly. **Tuning.**
* **Design gaps deliberately left unsolved** (consistent with the pre-registered expectations):
  Trade Secret and Intellectual Property narratives, PHI/PII narratives with no identifiers, T2
  M&A and financial documents without explicit markers, proprietary code pasted into chat, compose
  / pipeline YAML, immunization-registry style PHI without an MRN/patient field, bulk contact lists
  with emails but no phones, and *label reliance* (banner on a benign document).

## Changes after checkpoint 1

Ruleset moved 1.0.0 -> **1.0.1**. Three changes, all made because of failures seen on **train**:

| # | Class | Change | Why |
|---|---|---|---|
| C1 | Defect fix | Removed "shares of" from `mna_announced_terms` | It suppressed `ma.markers` on genuine deal documents ("acquire all outstanding shares of ..."); the term contradicts the catalog's intent (announced / historical context). |
| C2 | Tuning | Added whole-word key aliases `pass` and `pw` (`secret_key_names_exact`) | `pass = <value>` is a common INI-style password key; whole-word matching avoids `compass = ...`. Observed failure: 3 of 6 documents in one train family. |
| C3 | Tuning | Secret-value plausibility: accept `>= 3` character classes with entropy `>= 2.0` (previously required entropy `>= 2.8`) | Repeated words lower Shannon entropy although the value is still a plausible password; character diversity is the better discriminator. |

## Checkpoint 2 - ruleset 1.0.1

| split | headline docs / families | level macro-F1 | category macro-F1 | high-risk P / R / F1 / FPR | abstained | T4 decoy-hit |
|---|---|---|---|---|---|---|
| train | 391 / 71 | 0.398 | 0.736 | 0.963 / 0.621 / 0.755 / 0.028 | 227 of 416 (0.546) | 0.111 |
| dev | 97 / 18 | 0.358 | 0.522 | 1.000 / 0.623 / 0.767 / 0.000 | 66 of 107 (0.617) | 0.000 |

* Train: Credentials F1 rose to 1.000, M&A F1 from 0.474 to 0.651, category macro-F1 from 0.706 to 0.736.
* **Dev is bit-for-bit unchanged** (identical metrics fingerprint): none of the three changes affected a
  dev document, so the improvements are not visible on dev and cannot be attributed to dev fitting.
  The tuning changes therefore raised *train* scores; the train figures are development-contaminated
  and should be read with that in mind.
