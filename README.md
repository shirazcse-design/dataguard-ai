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
| 1 | Synthetic dataset | in progress |
| 2 | Evaluation harness | pending |
| 3+ | Rules, ML, LLM, Hybrid | **not started; requires explicit approval** |

Out of scope for this stage: RAG, MCP, autonomous agents, UI, repository crawling, document
parsing, self-learning, production deployment.

## Development

Python 3.11+ is required.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                       # unit tests
ruff check . && ruff format --check .
dataguard-uc4 config validate
```

All data in this repository is synthetic. No real personal, health, financial or credential data is
used anywhere.
