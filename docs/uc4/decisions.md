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
