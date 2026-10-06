# Insider Risk Investigation Agent (UC2)

Insider-risk triage that keeps every part in its lane:
* an **Isolation Forest** scores each user-day against that user's own history;
* **four bounded agents** gather and explain the evidence;
* a **versioned deterministic rubric** decides MONITOR / INVESTIGATE / ESCALATE / HUMAN_REVIEW;
* **an analyst** decides what happens next. No tool can act.

> **ML detects the signal. Agents investigate the signal. Deterministic controls govern the
> outcome. Humans retain authority.**

> *Flagship case (I13):* a low-volume product manager (u-2043, 2026-08-25) downloads 262 files
> (78 sensitive, 34 after hours), touches new repositories, and uploads 2.4 GB to a personal cloud.
>
> The Isolation Forest scores the day **0.7996, HIGH_ANOMALY**, driven by the upload volume and the
> download count. UC4 classifies the files as **HIGHLY_CONFIDENTIAL** (credentials, financial and
> source code). UC6 cites the policy that **prohibits** Restricted data in personal cloud storage.
>
> The rubric scores **115 → ESCALATE**, and the floor would have forced ESCALATE anyway. The analyst
> sees why in one screen. Nothing says the employee is malicious: **an anomaly is not evidence of
> intent.**

All data is synthetic: 60 name-free users in 7 role families, 90 days of activity, fictional
repositories, hosts and policies.

## Status (2026-10-05)

Labels: **IMPLEMENTED** (code exists) · **TESTED** (unit tests) · **REPLAY-VERIFIED** (recorded
runs reproduce exactly offline) · **LIVE-VERIFIED** (measured against Foundry) ·
**DOCUMENTED / FUTURE** (not built).

| Area | State | Evidence |
|---|---|---|
| Synthetic data, behavioural features, Isolation Forest vs statistical baseline | IMPLEMENTED, TESTED | [`ml-design.md`](ml-design.md), [`results/ml-eval.md`](results/ml-eval.md) |
| Four bounded agents (orchestrator, behavior, investigator, risk) with hard budgets | IMPLEMENTED, TESTED, LIVE-VERIFIED | [`agent-and-tools.md`](agent-and-tools.md) |
| Deterministic risk rubric 1.2.0: floors, approval ceiling, role-context gating, review triggers | IMPLEMENTED, TESTED | [`risk-and-response.md`](risk-and-response.md) |
| 36-case golden set, frozen; 1 vs 2 vs 4 agents | LIVE-VERIFIED (run 3), REPLAY-VERIFIED | [`evaluation.md`](evaluation.md) |
| Foundry: 4 agents, guardrail, observability, evaluations | LIVE-VERIFIED | the `foundry-*-setup.md` guides |
| Guardrails probed live (GA1-GA15) | LIVE-VERIFIED | [`results/guardrails-verification.md`](results/guardrails-verification.md) |
| Telemetry privacy audit (local) and live canary (App Insights) | REPLAY-VERIFIED / LIVE-VERIFIED | [`results/observability.md`](results/observability.md), [`foundry-observability-setup.md`](foundry-observability-setup.md) |
| Controlled learning loop (mine → candidate → gate → human approval) | IMPLEMENTED, TESTED | [`learning-loop.md`](learning-loop.md) |
| Dashboard page and UC2 panels on the shared pages | IMPLEMENTED, TESTED | [`interview-demo.md`](interview-demo.md) |
| Harmonising UC1's notice-period input with UC2; production identity and log sources | DOCUMENTED / FUTURE | [`lessons-learned.md`](lessons-learned.md) |

Headline (full 4-agent system, all 36 cases, live run 3, replay reproduces it exactly):
* **critical misses: 0** (no expected INVESTIGATE / ESCALATE case returned MONITOR);
* acceptable-outcome accuracy **0.861** (five misses, all cautious, all kept);
* **0** unsupported evidence ids, **0** canary or untrusted-text leaks, **0** unlisted tool calls;
* 7.39 model calls and about 19.4K tokens per case; monetary cost `NOT_ESTIMATED`.

On the 12-case live sample, a **single agent** matched or beat the multi-agent systems on outcome
accuracy at about 40% of the model calls. See [`evaluation.md`](evaluation.md#3-one-two-or-four-agents).
36 cases written by one author: indicative, not validated.

## Documents

| Document | What it covers |
|---|---|
| [`product-brief.md`](product-brief.md) | The interview one-pager: problem, users, solution, results, decisions |
| [`architecture.md`](architecture.md) | Problem, personas, architecture, reuse of UC1 / UC4 / UC6, data flow |
| [`ml-design.md`](ml-design.md) | Synthetic data, features, Isolation Forest, statistical baseline, ML evaluation |
| [`agent-and-tools.md`](agent-and-tools.md) | The four agents, tool allow-lists, budgets, delegation, context isolation, MCP |
| [`risk-and-response.md`](risk-and-response.md) | Rubric 1.2.0, floors, ceiling, review triggers, human-in-the-loop |
| [`evaluation.md`](evaluation.md) | Golden set, metrics, live runs 1-3, architecture comparison, Foundry Evaluations, misses |
| [`responsible-ai.md`](responsible-ai.md) | Inputs excluded, anomaly ≠ intent, fairness by role family, guardrails, privacy, oversight |
| [`learning-loop.md`](learning-loop.md) | The governed feedback loop and its first candidate |
| [`lessons-learned.md`](lessons-learned.md) | Challenges, lessons, trade-offs, limitations, next steps |
| [`interview-demo.md`](interview-demo.md) | How to run the demo, an 8-minute script, likely questions |
| [`foundry-agents-setup.md`](foundry-agents-setup.md) | The four Foundry agents (generated specification) |
| [`foundry-guardrails-setup.md`](foundry-guardrails-setup.md) | The guardrail and the live probe findings |
| [`foundry-observability-setup.md`](foundry-observability-setup.md) | Span tree, App Insights, canary finding, KQL |
| [`foundry-evals-setup.md`](foundry-evals-setup.md) | The three Foundry evaluations and the input-fidelity lesson |

## Commands (`dataguard-insider`)

| Command | Network |
|---|---|
| `data build` / `ml eval` | none |
| `investigate I13 [--mode replay\|offline\|live] [--arch full\|lean\|single] [--backend chat-completions\|foundry-service]` | replay: none |
| `eval [--mode replay\|offline] [--arch ...] [--ids ...]` | none |
| `obs report` (privacy audit and telemetry summary) | none |
| `learn run` / `learn approve --candidate ... --approver ... --decision ... --reason ...` | none |
| `foundry-eval` (export rows) / `--run` (create the evaluations in Foundry) | `--run`: Entra |
| `record [--arch ...] [--ids ...] [--backend ...] --label ...` | LIVE (key or Entra) |
| `agent schemas` / `agent register [--roles ...] [--rai-policy-id ...]` | register: LIVE (Entra) |
| `guardrails verify` / `obs live-check` | LIVE (Entra) |
| `DATAGUARD_MCP_CALLER_ID=insider-risk-agent dataguard-uc4-mcp --insider-tools` | none |

Every LIVE command reads credentials only from the environment or an Entra browser sign-in. No key
or connection string is stored in the repository.

## Code map

| Path | What |
|---|---|
| `ml/insider/` | `synth.py` (synthetic users, scenarios, activity), `features.py`, `detectors.py` |
| `app/insider/services.py` | Deterministic services: behaviour, identity, logs (with the untrusted-text scanner), approvals, UC4 data, UC6 policy |
| `app/insider/agents.py` | `BoundedAgent`: allow-list, typed arguments, budgets, repair, guilt-word filter |
| `app/insider/case.py` | Case context and the tools each agent may call |
| `app/insider/pipeline.py` | `InsiderInvestigator`: single / lean / full architectures, delegation, spans |
| `app/insider/harness.py` | `decide()`: the deterministic rubric |
| `app/insider/service.py`, `cli.py`, `mcp_tools.py` | Wiring, Foundry registration, CLI, MCP tools |
| `config/insider/` | `insider.v1.yaml`, `agents.v1.yaml`, `risk.v1.yaml`, `mcp_tools.v1.yaml`, `candidates/` |
| `prompts/uc2/` | Agent prompts (orchestrator v2 and investigator v2 current; v1 kept) |
| `evals/insider/` | Golden set, agent eval, ML eval, Foundry evals, guardrails, observability, learning loop |
| `app/demo/insider.py`, `app/demo/static/insider.js` | The dashboard page |
