# DataGuard AI

Agentic Data Security & Insider Risk Platform. The product source of truth is
[`docs/DataGuard_AI_PRD.docx`](docs/DataGuard_AI_PRD.docx).

## Current focus: Use Case 4 - Sensitive Data Discovery & Classification

UC4 is built as a **shared classification service** (not an autonomous agent) that benchmarks
rules, supervised ML, LLM semantic classification and a deterministic hybrid router on a
two-axis taxonomy (Sensitivity Level x Data Categories).

* Approved architecture and plan: [`docs/UC4_Technical_Architecture_and_Implementation_Plan.txt`](docs/UC4_Technical_Architecture_and_Implementation_Plan.txt)
* UC4 working docs, decisions and results: [`docs/uc4/`](docs/uc4/)

### Status

| Phase | Scope | State |
|---|---|---|
| 0 | Foundations: schemas, configuration, CI skeleton | done |
| 1 | Synthetic dataset | done |
| 2 | Evaluation harness | done |
| 3 | Rules Engine (deterministic baseline) | done; merged (development splits only) |
| 4 | Supervised ML classifier | done; merged (development splits only) |
| 5 | LLM classifier | done; merged (benchmarked on dev against three Foundry deployments, recorded and replayable) |
| 6 | Hybrid routing (router, fusion, review, variants, gates) | done; evaluated on dev (replayed), calibration (live, out-of-sample) and, once, the locked test split |
| 7 | Observability and failure hardening (spans, redaction, privacy gate, failure-injection suite) | done; merged (development split only) |
| 8 | Service surface: Python API + CLI, frozen result schema v1.0, MCP contract | done |
| 9 | MCP adapter (`mcp_adapter/`, `dataguard-uc4-mcp`) and an offline observability dashboard | done; the adapter is **not release-ready** (see below) |

### Where UC4 stands (2026-09-21)

See the [completion report](docs/uc4/completion-report.md). The v0.1 scope is implemented, tested and
evaluated on every split. It is **not independently validated**:

* **Labels:** reviewed by **one** human (Round 2; provenance per the coordinator), so the dataset label
  reads "reviewed by one human; second independent review pending".
* **Locked-test result (one audited run):** level macro-F1 0.884 [0.758, 0.980], category macro-F1 0.997,
  high-risk recall 1.000 (111 of 111). The level gate **passes on the point estimate and fails on the
  lower confidence bound** (0.758 against 0.85). A lenient view (a level inside the gold's acceptable
  alternatives counts as correct) is 1.000 on calibration and test, but the approved gates are strict.
  See [`docs/uc4/results/validation-rescore.md`](docs/uc4/results/validation-rescore.md). The split is consumed.
* **The MCP adapter is a development surface.** Freeze criteria: audited confirmation done; human review
  accepted (one reviewer); eval gates not met on the strict metric.
* **Not done:** Azure Monitor export (needs an Application Insights connection string), a shared live
  dashboard, and the optional prompt-injection second opinion.

Out of scope for this stage: RAG, autonomous agents, UI, repository crawling, document parsing,
self-learning, production deployment.

## Development

Python 3.11+ is required.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mcp]"   # "mcp" is only needed for the MCP server

pytest                       # unit tests
ruff check . && ruff format --check .
dataguard-uc4 config validate
```

All data in this repository is synthetic. **Dataset labels: AI-generated synthetic dataset — reviewed by one human (provenance per coordinator); second independent review pending** (not independently human-validated). No real personal, health, financial or credential data is
used anywhere.
