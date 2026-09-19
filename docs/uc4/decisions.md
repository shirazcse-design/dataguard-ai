# UC4 decision log

Legend: **[APPROVED]** = decided by the product owner. **[IMPL]** = implementation decision made
during build, open to review.

## Approved product decisions

| # | Decision |
|---|---|
| A1 | Two-axis taxonomy: Sensitivity Level (exactly one) x Data Categories (zero or more). |
| A2 | v0.1 = synthetic repository + classify-on-demand. No repository crawling. |
| A3 | High-risk = PII, PHI, Financial/PCI, Credentials/Secrets, Trade Secret, or level Highly Confidential. Configurable, not hard-coded. |
| A4 | Primary persona: Data Security / DLP Administrator; secondary: SOC / Insider Risk Analyst; tertiary: Security Manager / CISO. |
| A5 | No RAG in UC4 v0.1. Definitions live in version-controlled configuration. |
| A6 | UC4 is a shared classification service, not an autonomous agent. Hybrid routing is deterministic harness logic: Rules -> ML -> LLM when necessary -> Human Review. |
| A7 | `classify_document()` is exposed via MCP later. Not implemented now. |
| A8 | Build the evaluation harness before ML/LLM. Real experimental results only; never fabricate metrics. |
| A9 | Dataset includes easy, semantic, ambiguous and hard-negative cases so no approach is favored. |
| A10 | Explicit confidence contract; ML calibrated where appropriate; LLM self-report is not a calibrated probability. |
| A11 | Python 3.11+. |
| A12 | Level floors are initial, configurable DataGuard synthetic-policy defaults, not universal requirements. |
| A13 | ~800 synthetic documents, ~200 in the locked test set. Metrics: macro-F1 (level), macro-F1 (categories), high-risk recall as a separate metric, plus per-class/per-category P/R/F1. No combined headline score. |
| A14 | Azure AI Foundry for the LLM phase; do not block Phases 0-4 on it; do not assume model names/capabilities until verified. |
| A15 | Pre-extracted text only in v0.1. No DOCX/PDF/XLSX parsing. |
| A16 | Rules-only benchmark default when no signal is found is INTERNAL. "No rule match does not mean the document is Public." The hybrid production path abstains/escalates rather than assuming Public. |
| A17 | Interface: Python library + CLI first. JSON API only after the hybrid is working and evaluated. |
| A18 | Repository additions approved (config/, data/, rules/, prompts/, app/classification/, app/llm/, evals/classification/, guardrails/, observability/, tests/). |
| A19 | Out of scope this stage: RAG, MCP, autonomous agents, UI, crawling, parsing, self-learning, production deployment. |

## Implementation decisions (Phase 0)

| # | Decision | Why |
|---|---|---|
| D0.1 | Dev environment is Python 3.12.14 (standalone build fetched with `uv`); the project requires `>=3.11`; CI matrix covers 3.11 and 3.12. | The machine's system Python is 3.9.6, below the approved minimum. |
| D0.2 | Level/category ids are plain strings validated against the loaded taxonomy, not Python Enums. | Keeps the taxonomy configuration-driven so it can evolve (A1, A3, A12). |
| D0.3 | Added two confidence kinds beyond the plan's three: `uncalibrated_score` (raw model score before calibration) and `none` (oracle/baselines). | Avoids mislabeling an uncalibrated score as calibrated, and lets non-approaches emit a valid result. |
| D0.4 | Config discovery is relative to the repo root (editable install), overridable with `DATAGUARD_CONFIG_DIR`. | Simple for a repo-run tool at this stage; packaging config into a wheel is deferred. |
| D0.5 | Strict YAML loading rejects duplicate keys. | PyYAML silently keeps the last duplicate, which could hide a config error. |
| D0.6 | Work is on branch `uc4/phases-0-2`, not `main`. | `main` is the default branch; nothing is pushed. |
| D0.7 | `rules_default_level: INTERNAL` (A16) is recorded but not yet a config key. | It has no consumer until Phase 3 (Rules), which is not approved yet. |

## Implementation decisions (Phase 1)

| # | Decision | Why |
|---|---|---|
| D1.1 | The dataset is generated from a spec + seed by a deterministic template engine; no external LLM is used to write data. | Reproducibility, zero cost, no Azure dependency (A14), and no provider-specific stylistic bias baked into the benchmark. Cost: less linguistic variety. |
| D1.2 | Splits are assigned by scenario family (group), not by document. | Prevents template leakage across splits. Consequence: the effective sample size is the number of families. |
| D1.3 | Small-sample shortfalls are recorded as manifest flags, not build errors. | Matches the approved plan ("report with a small-sample flag"). Hard errors are reserved for unsound data (leakage, invalid labels, missing test coverage). |
| D1.4 | Gold labels follow a fail-safe tie-break: on a level tie the higher level is gold and the lower is an `acceptable_alternative_level`; contested categories are omitted. | Deterministic, protective of high-impact data, and documented in the labeling guidelines. |
| D1.5 | Generated size is 853 documents (target ~800) with 227 in the test split (target ~200); 155 families. | Additional families were added after the first build showed thin test support (for example 16 Credentials positives) and level imbalance. The result is +6.6% over target. |
| D1.6 | Evidence spans that are exactly a shared vocabulary term are exempt from the cross-split leak check. | Domain vocabulary (for example diagnoses) is legitimately shared; identifiers and sentences are not. |
| D1.7 | SSN-like test values use area 900-999 / group 01-49; the same digit format is reused in T4 part numbers. | Never issuable, and the format alone is not a label cue. Rules detectors must use format + context. |
| D1.8 | T5 (adversarial) documents are reported separately and excluded from the headline metrics. | As approved in the architecture plan. |
| D1.9 | A one-document-per-family review sheet is provided for human label review. | All labels are AI-authored; reviewing one rendered document per family covers every family. |
| D1.10 | Open-source code is `PUBLIC` with no category (Source Code means non-public, organization-owned code). | Keeps the Source Code category consistent with its level floor. |

## Implementation decisions (Phase 2)

| # | Decision | Why |
|---|---|---|
| D2.1 | Confusion structures come from scikit-learn; P/R/F1 are derived from the counts so undefined values are `null`, not 0. Macro averages cover labels with gold support; an undefined precision of a supported label counts as 0. | Honest reporting of empty denominators while matching scikit-learn's convention where it is defined. |
| D2.2 | A missing prediction is a miss (level: `NO_PREDICTION` column and severe under-classification; categories: empty set; high-risk: negative). Failures are never dropped. Exception messages are not recorded, only the class. | No silent optimism; messages could contain document text. |
| D2.3 | `high_risk` is always re-derived by the harness from `high_risk.v1.yaml`; the classifier's own field is checked and disagreements are flagged. | Keeps A3 (configurable, never predicted directly) enforceable in evaluation. |
| D2.4 | A level below a category floor is measured as an error, not rejected as invalid. | Floor consistency is a classifier-quality question. |
| D2.5 | Confidence intervals resample scenario families by default (`bootstrap.unit: group`, new defaulted field in `eval.v1.yaml`, config version unchanged). | Documents in a family are correlated; document-level intervals are overconfident. |
| D2.6 | Headline = tiers T1-T4; T5 is its own slice. Slices by tier, format, generator and ambiguity. | As approved in the architecture plan. |
| D2.7 | Deferred documents are scored on their provisional label; three alternative views are computed (auto-only, deferred-as-errors, hypothetical perfect reviewer, labeled as hypothetical). | The plan promised all views; only synthetic deferring classifiers exercise them so far. |
| D2.8 | The metrics fingerprint excludes latency and cost and rounds floats to 12 places. | Latency varies run to run; last-digit float noise must not break reproducibility. |
| D2.9 | The dataset manifest records only the taxonomy and high-risk config hashes, not the eval config. | Found when editing eval settings invalidated the dataset manifest; the dataset does not depend on them. |
| D2.10 | The validation suite includes a "lying classifier" check. | Mutation testing showed a harness that trusts a classifier's `high_risk` escaped the original checks. |
| D2.11 | Run artifacts are git-ignored under `evals/classification/runs/`; only `docs/uc4/results/` is committed. | Runs contain per-document predictions and timestamps and are reproducible from the manifest. |
| D2.12 | ~~The CLI defaults to `dev`; `--split test` prints a notice (convention only).~~ **Superseded by A21.** | See below. |
| D2.13 | `rules_default_level: INTERNAL` (A16) is still not a config key: there is no consumer until Phase 3. | Avoids dead configuration. |
| D2.14 | Verified on Python 3.11.16 and 3.12.14; the dataset regenerates byte-identically on both. | CI covers 3.11 and 3.12 but could not be executed locally. |

## Approved after the Phase 0-2 review (product owner)

| # | Decision | Implementation |
|---|---|---|
| A20 | The dataset is "AI-generated synthetic dataset — pending human gold-label review" and must never be presented as independently human-validated. Human review is required before final benchmark results are trustworthy but does not block Phase 3. | `label_status` in `dataset_spec.yaml`, copied to the manifest, dataset report, run manifests and every report. |
| A21 | Hard test-split protection: normal commands operate only on train/calibration/dev; the locked test split needs `--allow-locked-test`, prints a warning, and produces an audited manifest. | `evals/classification/lock.py`; enforced in `load_documents`, `evaluate`, the CLI; tracked access log. |
| A22 | Keep 853 documents / 227 test; do not add examples to reach a count. Labels with < 25 positives report `SMALL_SAMPLE`; preserve per-label counts. | `SMALL_SAMPLE` flags and per-label `support` in every report. |
| A23 | Keep high-risk recall >= 0.90 as the initial reference, but never alone: also report precision, FPR, F1 and review rate. No minimum precision yet; select the operating point on dev after Rules and ML. No threshold tuning on the locked test set. | Reports show all five; the 0.90 reference is informational only (`reference_targets`). |
| A24 | Family-level bootstrap is the default; every CI report states the number of independent families. | Stated in the report header and in `n_units`. |

Implementation notes: `validate-harness` now defaults to the development splits (it previously included
test); the oracle interval check was corrected to expect FPR to collapse to 0 rather than 1.

## Implementation decisions (Phase 3 - Rules Engine)

| # | Decision | Why |
|---|---|---|
| D3.1 | The detector catalog was pre-registered (`rules-catalog.md`) and committed before any rule was written; every later change is classified and logged (`rules-changelog.md`). | The rules author also authored the dataset; pre-registration and a change log limit and expose overfitting. |
| D3.2 | Protocol: errors inspected on train; dev at checkpoints; calibration untouched until the final report; the locked test split is not used. | Keeps a clean-ish holdout. The ruleset was frozen at 1.0.3 before the final report. |
| D3.3 | Standalone abstention default is the config key `standalone_default_level: INTERNAL`; abstention is surfaced as `routing.abstained`, level confidence `none`, and an `abstained` field on records. Rules never output PUBLIC. | A16: no rule match does not mean Public. |
| D3.4 | Labels raise, never lower; PUBLIC/INTERNAL labels are ignored as evidence. | Stale/spoofable labels must not downgrade content. |
| D3.5 | No positive "public" detector (e.g. press-release or licence markers do not set the level to PUBLIC). | Spoofable; the pre-registered catalog excludes it. Open-source licences only suppress `SOURCE_CODE`. |
| D3.6 | Strength is a tier; only `strong`+ assert a category or level (`emit_min_strength`, configurable). Weak evidence is kept, asserting nothing. | Rule strength is not a probability; weak hints belong to a later stage. |
| D3.7 | Regexes and validators live in code; lexicons, thresholds and dummy values live in version-controlled config. Ruleset version is bumped on any change. | Definitions in configuration (A5) without embedding regex escaping in YAML. |
| D3.8 | Evidence excerpts are masked/short; a test proves no raw matched value appears in evidence across all development documents. | No unnecessary exposure of sensitive values. |
| D3.9 | Scan cap lowered to 100,000 characters after measuring a 732 ms adversarial case; content beyond it is not scanned. | Meet the PRD 500 ms pre-check target for hostile input; stated limitation. |
| D3.10 | Harness additions: `abstained` and `decoy_for` on records, abstention rate, and a hard-negative block (decoy-hit rate). | Needed to report abstention, coverage and hard-negative failures for any approach. |
| D3.11 | The Rules Engine was NOT evaluated on the locked test split. | The user approved train/dev development only; a single audited report-only test run can be requested once the ruleset is frozen. |

## Implementation decisions (Phase 4 - supervised ML)

| # | Decision | Why |
|---|---|---|
| D4.1 | The plan (`ml-plan.md`) was committed before any model was trained; deviations are reported, not hidden. | Limits shaping the protocol after seeing results. |
| D4.2 | Fit on train, calibrate on calibration, evaluate on dev; the ML loader reads development splits only and never passes locked-test authorization. | A21. |
| D4.3 | Features are TF-IDF word + char + filename. No rule outputs and no embedded labels. | Keeps Rules and ML independent (stacking is a later, explicit Hybrid experiment); labels are spoofable. |
| D4.4 | `C` chosen per head by grouped (by family) CV on train; ties go to the smaller `C`. Dev evaluated once for the selected config. | Random folds leak templates (measured: 0.98 vs 0.31). |
| D4.5 | Platt calibration on the calibration split; below 5 positives or negatives the score is labeled `uncalibrated_score`. | The confidence contract forbids calling an unfitted score calibrated. |
| D4.6 | Category threshold is 0.5 for all categories; no operating point is chosen. The report shows a dev sweep. | A23: selected later on dev after Rules and ML are both evaluated. |
| D4.7 | Evidence is inferred `feature_attribution`, restricted to short alphabetic words. | Attribution is not observed evidence, and must not leak identifiers. |
| D4.8 | The harness gained `Scores`, calibration metrics (ECE, Brier, reliability), a threshold sweep and an in-sample warning (`HARNESS_VERSION 1.2.0`). | Needed to report calibration honestly for any approach. |
| D4.9 | The report states that ML high-risk recall is inflated by over-flagging and that the ML-vs-Rules comparison flatters Rules. | A23 and the no-single-headline rule. |
| D4.10 | The ML model was NOT evaluated on the locked test split. | Not approved; the test split has never been evaluated. |
