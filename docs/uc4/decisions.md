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

## Implementation decisions (Phase 5 - LLM classifier)

| # | Decision | Why |
|---|---|---|
| D5.1 | `llm-plan.md` was committed before any LLM code; the Azure blocker and the no-fabricated-results rule were stated in it. | Pre-registration; no model exists to measure yet. |
| D5.2 | No LLM accuracy, calibration, latency or cost number is reported. Tiers without a complete recorded run are "NOT RUN"/"INCOMPLETE" and show no results. The three-model benchmark is blocked on access and deployment names. | Approved: actual results only; open decision 4 (Azure access) was never resolved. |
| D5.3 | No model names in the repository: tiers name environment variables holding deployment names. Prices are null until a dated price is entered. | Approved: do not assume model names; never invent prices. |
| D5.4 | The Foundry adapter is stdlib HTTP, configuration-driven, and marked UNVERIFIED; tested only against a local fake server. Credentials never go over plain HTTP to a remote host. Entra auth uses an optional, unrequired SDK and an explicit scope. | Foundry details are unverified (architecture section 17). |
| D5.5 | Replay/record adapter keyed by `(prompt_version, model_id, input_hash)`; a miss is an error, never a fallback. | A silent fallback would fabricate a result. |
| D5.6 | Few-shot examples: a pre-registered rule over train only; the file stores ids and hashes; the loader and a test enforce train-only and hash integrity. | Prevents leakage of dev/calibration/test into the prompt. |
| D5.7 | The prompt's taxonomy section is generated from `taxonomy.v1.yaml`; embedded labels and metadata are not shown; the filename is. | One source of truth; labels are spoofable (same rule as ML). |
| D5.8 | Evidence quotes are verified against the text sent; unverified quotes are `inferred` and cap confidence; an unverified high-risk call requests review. Excerpts are masked. | Anti-hallucination and no raw sensitive values in results. |
| D5.9 | Confidence is `verbalized_bucket` only; reliability is reported from a measured table. | DEC-10. |
| D5.10 | Failures return `review_required` (`LLM_UNAVAILABLE`) with no level; there is one repair retry for malformed output and bounded retry only for transport errors. | Architecture section 19: never silently default to a lower sensitivity. |
| D5.11 | Output models are strict (no coercion). | A test showed pydantic accepted `"no"` as `false`. |
| D5.12 | The injection lexicon was developed on train; v1.0.1 added patterns for train misses. Held-out detection stayed low (calibration 1/5, dev 3/10) with no false positives. The "raise but not lower" rule is deferred to Phase 6. | Honest report; the rule needs Rules/ML outputs. |
| D5.13 | Harness 1.3.0: records carry verbalized buckets, evidence counts, guardrail types and tokens; an LLM few-shot note in run reports; `config validate` covers the LLM and guardrail configs. | Needed to report LLM metrics for any run. |
| D5.14 | Pre-LLM redaction was not implemented. | Its effect can only be measured with a real model. |
| D5.15 | The LLM path was NOT evaluated on the locked test split. | Not approved; the test split has never been evaluated. |

## Implementation decisions (Phase 5 - real deployments, added after credentials were provided)

| # | Decision | Why |
|---|---|---|
| D5.16 | The Foundry adapter follows the current Microsoft Foundry REST reference (v1 route, deployment in `model`, `api-key`); a project endpoint is reduced to its resource host; api-version is optional. Verified live on 2026-09-19. | The pre-registered adapter used an older per-deployment path and was explicitly unverified. |
| D5.17 | Per-tier `api` (`chat_completions` or `responses`) and `send_temperature` record what each deployment accepts (found by document-free probes): the `large` deployment supports only the Responses API; `small` and `large` reject a temperature. | Approved: temperature 0 only "where the deployment allows". |
| D5.18 | Deviations from the pre-registered values, made because of measured provider behaviour and not because of any dev result: output cap 700 to 4000 tokens (reasoning tokens count against it), timeout 10 s to 300 s. Latency against the PRD 10 s limit is reported, not hidden. | Otherwise calls fail and the benchmark measures timeouts, not capability. |
| D5.19 | The benchmark was recorded once on dev (107 documents per deployment) and committed under `data/llm_cache/` so it is replayable in CI without credentials; the official numbers come from a sequential replay. `large` was recorded with 8 concurrent workers. | Reproducibility without secrets; a sequential `large` run would have taken hours. |
| D5.20 | The prompt, few-shot set and guardrail were NOT changed after seeing any dev result. | Avoids tuning on dev. |
| D5.21 | The credentials were supplied in conversation, used only through environment variables from a private temp file outside the repository, never written to the repository, and should be rotated. | Secrets hygiene. |
| D5.22 | The locked test split was NOT used for any LLM run. | Not approved. |

## Implementation decisions (Phase 6 - Hybrid routing)

| # | Decision | Why |
|---|---|---|
| D6.1 | `hybrid-plan.md` (design, the fixed variant grid, selection rule, gates, expectations) was committed before any router code. | Pre-registration. |
| D6.2 | The hybrid composes the stages only through their public result contract; a stage that raises or breaks the contract is skipped and recorded, never propagated. | Harness owns control flow (PRD 11.1); failures are values. |
| D6.3 | Nothing falls back to a low sensitivity: an undecidable document is `review_required`, with the highest level any usable stage produced as a provisional label, or no label. | Architecture section 19. |
| D6.4 | Rules sufficiency = decisive level at rule strength >= strong; a Rules abstention is never "Public". ML is accepted only with calibrated scores reliable on all axes. LLM acceptance uses the verbalized bucket as an ordinal, never as a probability. | Confidence contract (DEC-10). |
| D6.5 | Conflict = high-risk disagreement or a level gap of more than one rank; a conflict escalates to the next tier and, if it survives, goes to review. | Architecture section 11. |
| D6.6 | Fusion: union of categories with per-category provenance; level from the most authoritative semantic stage; Rules level and category floors are toggleable floors; injection restriction raises but never lowers; high-risk derived. | Architecture section 11; each toggle is a benchmarked variant. |
| D6.7 | No minimum precision was chosen. The recommended variant follows a pre-registered rule (recall, severe-under-classification and no-missing-prediction filters; dominance with 0.02 ties; then lowest tokens, latency, stages). The choice remains the product owner's. | A23. |
| D6.8 | Dev was used to select and to evaluate; the report says so beside every affected number. No locked-test run was made or authorised. | A21; avoids a false sense of validation. |
| D6.9 | Report latency is the sum of RECORDED LLM stage latencies; local Rules/ML time is excluded and rounding is applied to deterministic inputs only. | A first version let live microseconds decide a tie-break. |
| D6.10 | `llm_mid_only` is a pass-through sanity check, excluded from the recommendation; the call budget may be smaller than the tier count; the injection second opinion was not implemented. | See the clarifications in `hybrid-engine.md`. |
| D6.11 | Fault-injection scenarios (tiers unavailable) are part of the report, because real recorded outputs never trigger escalation, conflict or review on dev. | Safety paths must be shown to work. |
| D6.12 | Harness 1.4.0: records carry routing accounting (stop reason, stages run, escalations, review reasons, routing flags, per-stage latency). | Needed to report stage coverage and cost for any approach. |
| D6.13 | `replay_model_id` per LLM tier in `llm.v1.yaml` names the recorded benchmark (a deployment name, not a secret) so replay needs no flag. | CI and the hybrid replay without configuration. |

## Implementation decisions (Phase 7 - observability and failure hardening)

| # | Decision | Why |
|---|---|---|
| D7.1 | `observability-plan.md` was committed before any Phase 7 code and lists the gaps found by reading the code (G1-G5). | Pre-registration; the hardening was driven by an explicit gap list, not by what happened to pass. |
| D7.2 | Tracing is a no-op unless a trace is active; a test proves results are identical with tracing on and off. | Observability must not change behaviour. |
| D7.3 | Export is **deny by default**: only allow-listed `dg.*` keys leave the process; keys whose final segment names text (`content`, `quote`, `excerpt`, `rationale`, `prompt`, `filename`, ...) are refused at config load; identifier keys are shape-validated, not masked; other values are masked and truncated; exception messages are never recorded, only class names. | PRD 19: never log raw content or unmasked evidence. |
| D7.4 | Privacy is verified by an audit (`obs audit`, a CI gate) that scans every key and string value of a full dev run for 6-word windows of every document, every gold evidence span, filenames and sensitive-value patterns, and has a negative control proving it can fail. | Absence of leakage must be checkable. |
| D7.5 | Spans use an OpenTelemetry-compatible data model with JSONL and in-memory sinks and an optional SDK bridge (extra `otel`, tested with the in-memory exporter). **Azure Monitor/Foundry export is unverified** (no connection string); the glue is lazy, optional and labelled so. | Foundry tracing needs an Application Insights resource that does not exist here. |
| D7.6 | Closing G1: the Rules engine isolates each detector; a failure marks the result `degraded`; a degraded Rules result is never sufficient for the hybrid (no short-circuit, no floor). | Incomplete evidence must not decide. |
| D7.7 | Closing G2: an input guard (`config/guardrails/input.v1.yaml`) rejects empty, undecodable and oversize text, flags truncation above a soft limit, and `classify_safely` turns malformed payloads and classifier crashes into `rejected`/`error` results without ever raising or leaking text. | Architecture section 19. |
| D7.8 | Closing G3: the hybrid honours `options.mode`, `max_llm_tier` and `budget` (latency; cost only when a price exists; a cap of 0 means no spend). A cost cap with no price cannot be enforced and says so. | The options existed in the schema but were ignored. |
| D7.9 | Closing G4: a decided hybrid result is `degraded` when a configured stage errored or was unavailable. | The failure must be visible, not silent. |
| D7.10 | The failure-injection suite (`tests/failure_injection/`) has a test for every row of architecture section 19, run through the real classifiers, the real Foundry adapter and a local fake provider server; a meta-test fails if a row has no test and mirrors the architecture table. `obs report` builds the matrix from an actual run. | Exit criterion of Phase 7. |
| D7.11 | Bugs found by tests while building Phase 7 and fixed: the redactor first mangled the identifiers it must preserve and masked snake_case codes; guardrail events were emitted twice; the allow-list check first rejected safe keys such as `dg.content_hash`. | Recorded so the design history is honest. |
| D7.12 | Dashboards (DG-018), the service surface (Phase 8), Azure Monitor verification and any locked-test evaluation were not done. | Out of scope or blocked. |

## Implementation decisions (Phase 8 - service surface)

| # | Decision | Why |
|---|---|---|
| D8.1 | `service-plan.md` was committed before any Phase 8 code, including the MCP freeze criteria and the honest status of the one that is not met. | Pre-registration; the freeze cannot be declared ready by redefining the criteria. |
| D8.2 | **MCP is documented, not implemented.** No server, tool, adapter or scaffolding exists, and none will until the plan's criteria are met and approval is given. | The plan says to stop for approval before MCP; the eval-gates criterion is not met (level macro-F1's lower bound fails on dev; the locked test split is unread). |
| D8.3 | `ClassificationService` is the single entry point (Python API, CLI, future MCP): it validates all configuration at startup, refuses to start on any invalid config, unknown variant or missing live credentials, and never raises from `classify`. | Architecture section 19. |
| D8.4 | Schema v1.0 is frozen as JSON Schemas with a structural compatibility check (`schema check`, a CI gate): any difference fails CI; breaking changes need a major bump, additive ones a minor bump and a changelog entry. Golden examples exist for every status and are generated by the real service. | An external contract needs a machine-checked freeze. |
| D8.5 | Constraints were tightened BEFORE the freeze (bounded ids and lengths; `size_bytes` recomputed from the content). | Free before v1.0 is published, a breaking change afterwards. |
| D8.6 | A request whose `schema_version` has another major or a newer minor is `rejected` (`unsupported_schema_version`), never parsed leniently. | A field must never be silently dropped. |
| D8.7 | CLI exit codes: 0 valid result (including `review_required`), 3 rejected, 4 error, 2 usage/startup. Only result JSON goes to stdout; files are read with a cap; only the basename is used as a filename signal. | A review is a flag, not a block; hostile input must not exhaust memory or leak paths. |
| D8.8 | Tools accept only `content`, filename/extension, options and (adapter-supplied) caller in the documented MCP contract; `existing_labels` and `metadata` are not accepted; model-generated text in results is documented as untrusted. | Spoofable inputs; tool-output prompt-injection risk. |
| D8.9 | Bugs found by tests while building Phase 8 and fixed: a hostile over-long `request_id` made building the rejected result itself raise; an unknown `--variant` escaped as a traceback; the example generator's first filename (`example.txt`) triggered Rules' negative context. | Recorded so the history is honest. |
| D8.10 | No JSON/HTTP API, `document_id` resolution, authentication or rate limiting. | Optional in the plan / out of scope for a library + CLI first. |

## Approved after the Phase 8 review (product owner)

| # | Decision | Implementation |
|---|---|---|
| A25 | Build the MCP adapter for `classify_document` (2026-09-20). This lifts the "stop for approval before MCP" gate only; it does not lift the other two unblock conditions (audited confirmation on data that did not choose the configuration; human gold-label review), which remain open and are reported as NOT satisfied. | `mcp_adapter/`, `config/mcp/mcp.v1.yaml`, `docs/uc4/mcp-contract.md` |

## Implementation decisions (MCP adapter)

| # | Decision | Why |
|---|---|---|
| D9.1 | The package is `mcp_adapter/`, not `mcp/` as the plan sketched. | A top-level `mcp` package would shadow the MCP SDK (`import mcp`). |
| D9.2 | The adapter core does not import the SDK; only `server.py` does, behind the optional `mcp` extra. | The policy stays testable without the SDK, and the SDK (currently 2.x, a breaking rewrite of 1.x) is a replaceable transport. |
| D9.3 | Deny by default: an empty or missing allowlist entry means no access; startup refuses an unlisted identity. | PRD 10.1 allowlist and per-agent permissions. |
| D9.4 | Spoofable or unresolvable inputs (`metadata`, `existing_labels`, `caller`, `schema_version`, `document_id`) are rejected rather than silently dropped. | A field must never be silently ignored (same principle as D8.6). |
| D9.5 | Evidence is off by default and only available to callers whose policy allows it. | Excerpts and rationales are model text from an untrusted document. |
| D9.6 | The shipped `example-agent` policy caps the LLM tier at `mid`. | `large` takes ~78 s against the PRD's 10 s tool-call limit. |
| D9.7 | The tool runs the synchronous service in a worker thread. | Live LLM calls take seconds; the protocol loop must not block. |
| D9.8 | The server is verified end to end in-process and once over a real stdio subprocess; no network transport, authentication, rate limiting or `document_id` store. | Out of scope for v0.1 (D8.10). |
| D9.9 | The dashboard is a static, script-free HTML file generated from `summarize(spans)`, not a service. | It needs no infrastructure, cannot leak text (spans carry none), and every value is escaped. A live shared dashboard (DG-018) remains platform work. |

## Gold-label decisions A / B / C (product owner, 2026-09-20)

Evidence: the blind-review sheet the product owner designated as the human review. **That sheet is identical to an earlier AI-completed sheet on all 33 documents (level, categories, confidence, flags and rationale text), so it adds no evidence independent of the AI review** (recorded verbatim in `review/blind_results/blind_review_comparison.md`). The dataset therefore stays "AI-generated synthetic dataset — pending human gold-label review" (A20); these decisions do not change that status.

| # | Decision | Implementation |
|---|---|---|
| A26 | **Decision A:** keep `PUBLIC` for `hn_public_api_docs_placeholder_keys` (and the same rule for `hn_business_case_study`); no label change. Clarify that published customer-facing documentation is Public. | Guidelines section 8; **no label, taxonomy or prompt change.** The hybrid's 5 headline level errors remain, and its level macro-F1 lower bound stays 0.631. |
| A27 | **Decision B:** an MRN does not count as another direct identifier; `phi_prescription_record` stays `[PHI]`. | Guidelines section 8; no label change. |
| A28 | **Decision C:** add `INTERNAL` to the acceptable alternative levels of `amb_customer_case_study_draft`: `[PUBLIC, INTERNAL]`. | `t3_ambiguous.yaml`; dataset regenerated (`dataset_sha256` `bc86537c2cc23e5f…` -> `9442e354c3dd51dd…`, only the 6 dev documents' alternatives and the manifest changed); blind key and manifest regenerated (reviewer-facing files byte-identical). **No effect on strict scoring.** |

Not done: no taxonomy config edit (it would invalidate the recorded LLM cache); the AI-review comparison under `blind_results_ai/` was computed against the previous dataset hash and is kept as the historical record.
| D9.10 | The out-of-sample check ran the frozen `default` hybrid on the calibration split, recorded live; no label, config, threshold or prompt was changed in response to its result. | Changing anything now would be tuning on a split just inspected. The `amb_aggregate_health_stats` label question (gold HIGHLY_CONFIDENTIAL, taxonomy lists de-identified aggregate stats as a PHI counter-example) is left for a human review. |
