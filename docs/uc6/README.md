# Data Security Policy Copilot (UC6)

Grounded, cited Q&A over enterprise data-security policies, and the shared **Policy
Intelligence** capability for the other DataGuard agents (PRD 4.7).

> "Can an employee upload confidential customer information to a personal cloud-storage account?"
> → **ANSWERED**: no; 3 verified citations (Acceptable Use §5.3, Data Classification §3.2, §2.3),
> each quote checked against the retrieved policy text by the harness, not by the model.

It answers **only** from retrieved policy text, cites every claim, and treats "insufficient
evidence" as a valid answer. Current policies that conflict go to a human reviewer; an older
version that conflicts with a newer one is resolved from metadata.

## Status (2026-09-29)

| Area | State | Evidence |
|---|---|---|
| Synthetic corpus (8 policies + 1 superseded version + 1 poisoned draft), 74 section chunks | done | `data/policy_corpus/` |
| Keyword (BM25), embedding (`text-embedding-3-small`), hybrid (RRF), deterministic rerank | done | [`results/retrieval.md`](results/retrieval.md) |
| Grounded generation (`uc4-llm-medium`), citation verification, evidence gate, conflicts | done | [`results/answers.uc6-answer.v2.md`](results/answers.uc6-answer.v2.md) |
| Naive / Advanced / Agentic RAG on the same components, evaluated side by side | done | same |
| Small bounded agent (4 tools); live in Foundry Agent Service as `dataguard-policy-copilot` v5 | done | [`foundry-agent-setup.md`](foundry-agent-setup.md) |
| Application guardrails + Foundry guardrails, verified live (G1-G8) | done | [`results/guardrails-verification.md`](results/guardrails-verification.md) |
| Observability through the shared tracer; privacy audit; Foundry canary test | done | [`results/observability.md`](results/observability.md), [`results/observability-foundry-verification.md`](results/observability-foundry-verification.md) |
| Foundry Evaluations (deterministic agreement + AI-assisted quality) | done | [`results/foundry-evals.md`](results/foundry-evals.md) |
| Human review (reasons, demo-only decisions, never written to gold) | done | dashboard + `app/demo/policy.py` |
| Dashboard: Policy Copilot page + UC6 panels on the shared pages; REPLAY works without Azure | done | `dataguard-uc4 demo serve` |

Headline numbers are in [`results.md`](results.md). Not independently validated: one author wrote
the corpus, the golden set and the code (see [`responsible-ai.md`](responsible-ai.md)).

## Documents

| Document | What it covers |
|---|---|
| [`architecture.md`](architecture.md) | Components, the three RAG levels, the agent, structural controls, reuse of shared platform code |
| [`evaluation-plan.md`](evaluation-plan.md) | Golden set, retrieval vs answer vs agent metrics, deterministic / heuristic / judge labels |
| [`results.md`](results.md) | All results in one place, with caveats and the decisions they led to |
| [`responsible-ai.md`](responsible-ai.md) | Grounding, hallucination control, guardrail layers, privacy, human oversight, known gaps |
| [`interview-demo.md`](interview-demo.md) | How to run the demo, a 7-minute script, and answers to the likely interview questions |
| [`foundry-embeddings-setup.md`](foundry-embeddings-setup.md) | The embedding deployment (created manually) |
| [`foundry-agent-setup.md`](foundry-agent-setup.md) | The Foundry agent specification (v5 created at the owner's request) |
| [`foundry-guardrails-setup.md`](foundry-guardrails-setup.md) | Application vs Foundry guardrails, configuration, findings, decisions |
| [`foundry-observability-setup.md`](foundry-observability-setup.md) | Tracing, KQL, canary privacy test, alerts |
| [`foundry-evals-setup.md`](foundry-evals-setup.md) | Dataset files, evaluators, column mapping |

## Commands (`dataguard-policy`)

| Command | Network |
|---|---|
| `ingest` | none |
| `ask "question" [--level naive|advanced|agentic] [--agent-backend chat-completions|foundry-service] [--mode replay|offline|live]` | replay/offline: none |
| `eval retrieval` · `eval answers` · `obs report` · `eval foundry-export` | none (replay) |
| `embed record` · `answers record` · `diagnose` · `obs live-check` · `guardrails verify` · `agent register` · `eval foundry-run` | LIVE (the owner's credentials, entered at hidden prompts) |

Demo: `dataguard-uc4 demo serve --mode replay`, then open `http://127.0.0.1:8765/#policy`.

## Decisions log

| Date | Decision | By |
|---|---|---|
| 2026-09-28 | Plan approved with changes: dense embeddings in scope, reuse `uc4-llm-medium`, small agent, one dashboard page, ~2-day timebox, Foundry resources configured manually | product owner |
| 2026-09-28 | `text-embedding-3-small` deployed manually as `uc6-embed-small` | product owner |
| 2026-09-28 | **Option A:** keep the pre-registered retrieval settings and report them as measured (dense-only beat Advanced on Recall@5), with no tuning on the test set | product owner |
| 2026-09-29 | **Option 1:** prompt v2 narrows the conflict rule after v1 over-escalated S01/P01. Post-hoc; v1 results kept | product owner |
| 2026-09-29 | The owner asked Claude Code to create the Foundry agent (v5) and, later, the Foundry evaluations; guardrails and observability were configured manually | product owner |
| 2026-09-29 | Keep Foundry Agent Service content recording ON for the synthetic demo (it stores agent conversations); production guidance recorded | product owner |
| 2026-09-29 | Accept G5 (a violent request no guardrail layer stopped at Medium) for the demo; revisit before production | product owner |
