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
| [`service-plan.md`](service-plan.md) | Pre-registered Phase 8 plan (written before any Phase 8 code) |
| [`service-api.md`](service-api.md) | Python API, CLI, exit codes |
| [`result-schema.md`](result-schema.md) | Frozen result schema v1.0: fields, status semantics, invariants, failure semantics |
| [`schema/`](schema/CHANGELOG.md) | Frozen JSON Schemas, golden examples, compatibility policy |
| [`mcp-contract.md`](mcp-contract.md) | MCP contract for `classify_document` (documentation only; blocked) |
| [`results/service-baseline.md`](results/service-baseline.md) | Generated: schema digests, examples, exit codes, MCP freeze criteria |
| [`observability-plan.md`](observability-plan.md) | Pre-registered Phase 7 plan (written before any Phase 7 code) |
| [`observability-engine.md`](observability-engine.md) | Spans, redaction, privacy audit, hardening, failure matrix, what is unverified |
| [`results/observability-baseline.md`](results/observability-baseline.md) | Generated: derived metrics, privacy audit, failure matrix from an actual suite run |
| [`hybrid-plan.md`](hybrid-plan.md) | Pre-registered Hybrid routing plan (written before any router code) |
| [`hybrid-engine.md`](hybrid-engine.md) | Hybrid architecture, findings, recommendation, what was not done |
| [`results/hybrid-baseline.md`](results/hybrid-baseline.md) | Generated Hybrid results: all variants, gates, fault injection (dev, replayed) |
| [`results/llm-baseline.md`](results/llm-baseline.md) | Generated three-tier LLM results on dev (replayed from `data/llm_cache`), prompt audit, injection guard |

Later phases add: the evaluation-harness guide and generated results under `results/`.
