# UC4 working documentation

| Document | Purpose |
|---|---|
| [`../UC4_Technical_Architecture_and_Implementation_Plan.txt`](../UC4_Technical_Architecture_and_Implementation_Plan.txt) | Approved architecture and phased plan |
| [`decisions.md`](decisions.md) | Decision log: approved product decisions vs. implementation decisions |
| [`phase-0-foundations.md`](phase-0-foundations.md) | Phase 0: schemas, configuration, CI skeleton |
| [`labeling-guidelines.md`](labeling-guidelines.md) | How gold labels are decided (levels, categories, overlaps, tie-break) |
| [`dataset-spec.md`](dataset-spec.md) | How the synthetic dataset is built, checked, and its known limitations |
| [`dataset-report.md`](dataset-report.md) | Generated dataset statistics and integrity results |
| [`evaluation-harness.md`](evaluation-harness.md) | How the harness measures, its conventions, and how it is validated |
| [`results/harness-validation.md`](results/harness-validation.md) | Generated evidence that the harness measures correctly |
| [`rules-catalog.md`](rules-catalog.md) | Pre-registered Rules Engine detector catalog (written before implementation) |
| [`rules-engine.md`](rules-engine.md) | Rules Engine architecture, detectors, limitations |
| [`rules-changelog.md`](rules-changelog.md) | Every change after the first evaluation, classified |
| [`results/rules-baseline.md`](results/rules-baseline.md) | Generated Rules baseline results (development splits) |
| [`ml-plan.md`](ml-plan.md) | Pre-registered ML plan (written before any model was trained) |
| [`ml-engine.md`](ml-engine.md) | ML classifier architecture, protocol, findings, limits |
| [`results/ml-baseline.md`](results/ml-baseline.md) | Generated ML results vs Rules (development splits) |
| [`results/ml-cv.json`](results/ml-cv.json) | Grouped cross-validation used for model selection |
| [`llm-plan.md`](llm-plan.md) | Pre-registered LLM plan (written before any LLM code) |
| [`llm-engine.md`](llm-engine.md) | LLM classifier architecture, rules, what was and was not measured |
| [`results/gold-review.md`](results/gold-review.md) | Gold-label review preparation: adjudication summary, proposed changes (none applied), impact analysis |
| [`../../data/synthetic/uc4/review/adjudication_sheet.csv`](../../data/synthetic/uc4/review/adjudication_sheet.csv) | The auditable adjudication sheet (127 rows; human columns blank) |
| [`results/gold-review-decision-brief.md`](results/gold-review-decision-brief.md) | Decision brief for the proposed label/taxonomy changes (A/B/C), with a correction about `source_system` metadata; nothing applied |
| [`blind-review.md`](blind-review.md) | Blind human-review package: what the reviewer sees, the key kept apart, checks; nothing applied |
| [`responsible-ai.md`](responsible-ai.md) | Azure AI Foundry Evaluations (HHH/APF, scoped), Guardrails (Content Safety second opinion), and the Responsible AI pillar mapping, incl. the Fairness & Inclusion probe |
| [`completion-report.md`](completion-report.md) | **Start here for status:** what is complete, the evidence, and what is still open (and who can close it) |
| [`human-review-round2.md`](human-review-round2.md) | Coordinator's guide for the Round 2 independent human review (ambiguous + disputed families, incl. `amb_aggregate_health_stats`): who, what to hand over, how to compare, how to adjudicate |
| [`service-plan.md`](service-plan.md) | Pre-registered Phase 8 plan (written before any Phase 8 code) |
| [`service-api.md`](service-api.md) | Python API, CLI, exit codes |
| [`result-schema.md`](result-schema.md) | Frozen result schema v1.0: fields, status semantics, invariants, failure semantics |
| [`schema/`](schema/CHANGELOG.md) | Frozen JSON Schemas, golden examples, compatibility policy |
| [`mcp-contract.md`](mcp-contract.md) | MCP contract for `classify_document` and how the adapter (`mcp_adapter/`) implements it; built ahead of two unmet freeze criteria |
| [`results/service-baseline.md`](results/service-baseline.md) | Generated: schema digests, examples, exit codes, MCP freeze criteria |
| [`observability-plan.md`](observability-plan.md) | Pre-registered Phase 7 plan (written before any Phase 7 code) |
| [`observability-engine.md`](observability-engine.md) | Spans, redaction, privacy audit, hardening, failure matrix, what is unverified |
| [`results/observability-baseline.md`](results/observability-baseline.md) | Generated: derived metrics, privacy audit, failure matrix from an actual suite run |
| [`hybrid-plan.md`](hybrid-plan.md) | Pre-registered Hybrid routing plan (written before any router code) |
| [`hybrid-engine.md`](hybrid-engine.md) | Hybrid architecture, findings, recommendation, what was not done |
| [`results/hybrid-baseline.md`](results/hybrid-baseline.md) | Generated Hybrid results: all variants, gates, fault injection (dev, replayed) |
| [`results/hybrid-locked-test.md`](results/hybrid-locked-test.md) | The single audited locked-test evaluation of the frozen hybrid (report-only): level F1 0.884 [0.758, 0.980], category 0.997, high-risk recall 1.000 |
| [`results/hybrid-calibration-check.md`](results/hybrid-calibration-check.md) | Out-of-sample check of the frozen hybrid on the calibration split (live, recorded); high-risk recall 0.891, all misses in one ambiguous family |
| [`results/llm-baseline.md`](results/llm-baseline.md) | Generated three-tier LLM results on dev (replayed from `data/llm_cache`), prompt audit, injection guard |
| [`agent-plan.md`](agent-plan.md) | Pre-registered Batch Triage Agent plan (written before any agent code); decision A37 |
| [`agent-engine.md`](agent-engine.md) | Batch Triage Agent architecture, real dev-split results, findings (incl. a caught-and-fixed replay-cache bug), the agent's own HHH/APF |
| [`results/agent-triage-dev.json`](results/agent-triage-dev.json) | Generated: the full BatchTriageReport for the dev split (`--agent-mode mock`, `--llm-mode replay`) |
| [`results/agent-eval-dev.json`](results/agent-eval-dev.json) | Generated: task completion, tool-call accuracy, safety-invariant compliance, HHH/APF for the same run |

