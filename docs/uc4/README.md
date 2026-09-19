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
| [`results/llm-baseline.md`](results/llm-baseline.md) | Generated three-tier LLM results on dev (replayed from `data/llm_cache`), prompt audit, injection guard |

Later phases add: the evaluation-harness guide and generated results under `results/`.
