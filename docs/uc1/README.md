# Agentic DLP & Sensitive Data Protection (UC1)

An end-to-end, agent-assisted Data Loss Prevention investigation. It **composes** the platform's
existing capabilities instead of rebuilding them:
* UC4 classifies the file;
* UC6 answers "what does policy say about this?" with verified citations;
* a small, bounded investigation agent fills the gaps;
* a versioned deterministic rubric decides.

> **The model proposes; the harness enforces.**

> *Flagship case (D11):* the privileged Corporate Development director uploads
> `Acquisition_Targets_2027.xlsx` (no label, no SSN- or card-style pattern; its sensitivity is
> semantic) to a personal Dropbox at 23:40.
>
> UC4 says **HIGHLY_CONFIDENTIAL / M&A strategy**. UC6's verified citations (Acceptable Use
> §5.3, Classification §3.2, with §2.4 defining Restricted) **prohibit** Restricted data in
> personal cloud storage. The agent confirms there is no approved
> exception and cites each fact to its source.
>
> The rubric scores **130 → ESCALATE**. A *simulated* "block the upload" proposal waits for an
> analyst's approval. Nothing is ever blocked automatically.

All data is synthetic (fictional "Harbourline Group" users, files, policies and partners).

## Status (2026-10-02)

| Area | State | Evidence |
|---|---|---|
| Pipeline: prechecks → UC4 → identity → behaviour → UC6 → agent → rubric → outcome | done | [`architecture.md`](architecture.md) |
| Agent: exactly 5 read-only tools, event-bound, budgets; prompt `uc1-agent.v2` | done | [`agent-and-tools.md`](agent-and-tools.md) |
| Versioned rubric 1.1.0, floors, review triggers, agent can raise never lower | done | [`risk-and-response.md`](risk-and-response.md) |
| Explicit UC4→UC6 integration contract (`mapping.v1.yaml`) | done | [`architecture.md`](architecture.md#4-the-uc4uc6-integration-contract) |
| MCP: `search_policy`, `get_user_profile`, `get_user_activity` on the existing server (opt-in) | done | [`agent-and-tools.md`](agent-and-tools.md#4-mcp) |
| 30-case golden set: replay evaluation, v1 vs v2 | done | [`evaluation.md`](evaluation.md), [`results/eval.md`](results/eval.md) |
| Foundry agent `dataguard-dlp-investigator` (v3), guardrail, observability, evaluations | done | the `foundry-*-setup.md` guides |
| Guardrails probed live (GU1-GU7) | done | [`results/guardrails-verification.md`](results/guardrails-verification.md) |
| Telemetry privacy audit (local) and live canary (App Insights) | done | [`results/observability.md`](results/observability.md), [`foundry-observability-setup.md`](foundry-observability-setup.md) |
| Dashboard: DLP Investigation page and UC1 panels on the shared pages | done | `dataguard-uc4 demo serve` |

Headline (replay, agent v2):
* acceptable-outcome accuracy **0.867**;
* high-risk recall **1.0**;
* **critical false negatives: 0**. No target was set in advance; this is the measured result;
* false-positive rate 0.286;
* Foundry judges: task adherence **28/30**, groundedness **30/30**.

These were measured on 30 cases written by one author, so they are indicative, not validated. See
[`evaluation.md`](evaluation.md).

## Documents

| Document | What it covers |
|---|---|
| [`product-brief.md`](product-brief.md) | The interview-oriented one-pager: problem, users, solution, results, decisions |
| [`architecture.md`](architecture.md) | Problem, personas, architecture, implementation, reuse of UC4 and UC6, integration contract |
| [`agent-and-tools.md`](agent-and-tools.md) | Agent design, tool boundaries, the evidence pack, MCP |
| [`risk-and-response.md`](risk-and-response.md) | Rubric, floors, review triggers, human-in-the-loop, simulated actions |
| [`evaluation.md`](evaluation.md) | Golden set, metrics, v1 → v2 results, Foundry Evaluations, misses |
| [`responsible-ai.md`](responsible-ai.md) | Guardrails, observability and privacy, human oversight, data |
| [`lessons-learned.md`](lessons-learned.md) | Challenges, lessons, trade-offs, limitations, next steps |
| [`interview-demo.md`](interview-demo.md) | How to run the demo, a 7-minute script, likely questions, tools and technologies |
| [`foundry-agent-setup.md`](foundry-agent-setup.md) | The Foundry agent specification and live check |
| [`foundry-guardrails-setup.md`](foundry-guardrails-setup.md) | Application and Foundry guardrails, verification findings |
| [`foundry-observability-setup.md`](foundry-observability-setup.md) | App Insights, canary test, sampling fix, KQL |
| [`foundry-evals-setup.md`](foundry-evals-setup.md) | How the Foundry evaluations are built and run |

## Commands (`dataguard-dlp`)

| Command | Network |
|---|---|
| `dataguard-dlp investigate D11 [--mode replay\|offline\|live] [--agent-backend chat-completions\|foundry-service] [--json]` | replay: none |
| `dataguard-dlp eval [--mode replay\|offline]` (writes `results/eval.{md,json}`) | none |
| `dataguard-dlp obs report` (privacy audit and telemetry summary) | none |
| `dataguard-dlp foundry-eval` (export rows) / `--run` (create the evaluations in Foundry) | `--run`: Entra |
| `dataguard-dlp record [--ids ...] [--agent-backend ...]` | LIVE (key) |
| `dataguard-dlp agent register [--rai-policy-id ...]` | LIVE (Entra) |
| `dataguard-dlp guardrails verify` | LIVE (Entra) |
| `dataguard-dlp obs live-check --from-project` | LIVE (Entra) |
| `DATAGUARD_MCP_CALLER_ID=dlp-investigation-agent dataguard-uc4-mcp --dlp-tools` | replay: none |

Every LIVE command reads credentials only from the environment or an Entra browser sign-in. No key
or connection string is stored in the repository.

## Code map

| Path | What |
|---|---|
| `app/dlp/pipeline.py` | `DLPInvestigator`: the stages, spans, evidence pack |
| `app/dlp/harness.py` | `decide()`: the deterministic rubric |
| `app/dlp/agent.py` | the 5 tools, the bounded agent loop, final-answer parsing |
| `app/dlp/integration.py` | UC4 classifier adapter, UC6 policy intelligence, effects |
| `app/dlp/context.py`, `prechecks.py` | identity, activity, exceptions, behaviour bands; DLP prechecks |
| `app/dlp/mcp_tools.py` | the opt-in MCP tools |
| `config/dlp/` | `dlp.v1.yaml`, `mapping.v1.yaml`, `risk.v1.yaml`, `mcp_tools.v1.yaml` |
| `prompts/uc1/` | `agent.v1.md` (kept), `agent.v2.md` (current) |
| `data/dlp/` | synthetic identities, activity, exceptions, the flagship document |
| `evals/dlp/` | golden set, metrics, reports, Foundry evals, guardrail and observability runners |
| `app/demo/dlp.py`, `app/demo/static/dlp.js` | the dashboard page |
