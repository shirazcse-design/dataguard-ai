# UC2 agents and tools

Config: `config/insider/agents.v1.yaml` (budgets are **enforced in code**, not requested in the
prompt). Prompts: `prompts/uc2/`. Loop: `app/insider/agents.py` (`BoundedAgent`). Tools:
`app/insider/case.py`. All agents use `uc4-llm-medium`.

## 1. The four agents

| Agent | Prompt | Turns / tools | Tools (allow-list) | Output |
|---|---|---|---|---|
| **Orchestrator** `dataguard-insider-orchestrator` | `uc2-orchestrator.v2` | 10 / 10; delegations: behavior 1, investigation 2, risk 1 | `delegate_behavior`, `delegate_investigation`, `get_identity_context`, `check_data_sensitivity`, `check_policy`, `request_human_review`, `request_risk_assessment` (terminal) | A plan executed through tools; ends by requesting the risk assessment |
| **Behavior** `dataguard-insider-behavior` | `uc2-behavior.v1` | 4 / 3 | `get_behavior_profile`, `get_activity_series`, `get_statistical_baseline_result` | `BehaviorFinding`: summary, baseline comparison, temporal observations (cited), gaps |
| **Investigator** `dataguard-insider-investigator` | `uc2-investigator.v2` | 8 / 8; ≤ 50 log events per call | `search_security_logs`, `check_approval`, `classify_case_files`, `search_policy`, `get_access_context`, `get_permissions`, `request_human_review` | `InvestigationFinding`: timeline, observed facts, data and policy findings, conflicting and missing evidence |
| **Risk** `dataguard-insider-risk` | `uc2-risk.v1` | 2 / **0** | none | `RiskRecommendation`: outcome, reason codes, rationale, evidence ids, confidence, uncertainty |

> The Risk Agent synthesizes structured evidence into a recommendation. It has no operational
> tools and cannot enforce an outcome. The deterministic risk harness remains authoritative.

Baselines for the architecture experiment (app-side only, not Foundry agents):
* `single`: 14 turns, 12 tools, all 11 read-only tools.
* `lean_orchestrator`: 10 turns, 9 tools, plus `delegate_investigation` (2).

## 2. What the loop enforces (`BoundedAgent`)

* **Allow-list:** a tool not on the agent's list is rejected (`unknown_tool`) and never executed.
* **Typed arguments:** each tool has a JSON schema; invalid arguments are refused
  (`invalid_arguments`). The subject and date are bound by the case, not chosen by the model.
* **Budgets:** turns, tool calls, delegations and consecutive tool failures. The orchestrator stops
  gracefully when its budget runs out (`orchestrator_stopped_early`), and the risk assessment still
  runs on what was gathered.
* **Structured output:** the final answer must validate against its schema, with one repair attempt.
  Otherwise the run is `invalid_final_answer`, and the harness treats it as an agent failure.
* **Guilt / intent / employment wording is withheld.** A filter (`BANNED`: malicious, guilty,
  theft, termination, disciplinary and similar) scrubs agent outputs. Any hit is counted as an
  `unsupported_conclusion`, and the case goes to human review.
* **Evidence ids:** findings must cite ids the case actually produced (AS, SERIES, STAT, IDN, D1…,
  P1…, L-…, APR, XFER). Unsupported ids are measured (0 in run 3).

## 3. Context isolation and untrusted text

* **Fresh context per agent.** A specialist receives only its brief (case id, subject, date,
  question, allowed window), never the orchestrator's conversation.
* **The orchestrator and risk agent never see raw log text.** They get structured findings.
  Measured: 0 canary and 0 untrusted-text leaks to either, across 36 live cases.
* **Log free text is untrusted.** `search_security_logs` covers the case day ± 7 days. Text that
  looks like an instruction is replaced with `[withheld: instruction-like text detected]` before any
  agent sees it, and a `prompt_injection_suspected` guardrail event is recorded.
* **Agents never see document text.** UC4 returns a level, categories, a confidence and an
  injection flag per file.

## 4. Delegation

The orchestrator's `delegate_*` tools are executed **by DataGuard**, not by Foundry's
connected-agents feature. The pipeline runs the specialist (Foundry agent or app planner) with its
own allow-list and budget, validates its output, and returns a compact result. This keeps one audit
trail, one budget authority and one place to enforce isolation.

## 5. Foundry

The four agents are registered in Foundry Agent Service with their instructions, the
`uc4-llm-medium` deployment, function-tool **definitions** (the tools still execute in DataGuard)
and the `uc2-insider-risk-guardrail`:

| Agent | Version |
|---|---|
| Orchestrator | v3 |
| Behavior | v2 |
| Investigator | v3 |
| Risk | v2 |

Registration is additive (`create_version`). See [`foundry-agents-setup.md`](foundry-agents-setup.md),
generated from the code and kept in sync by a test.

## 6. MCP

The existing DataGuard MCP server exposes four **read-only** UC2 tools behind `--insider-tools`:

| Tool | What it returns |
|---|---|
| `get_behavior_profile` | Baseline and anomaly profile |
| `search_security_logs` | Scanned log events, within a 9-day window |
| `get_permissions` | Granted permissions |
| `get_access_context` | Role family and privilege |

Each is deny-by-default per caller (`insider-risk-agent` only; `config/insider/mcp_tools.v1.yaml`).
Not exposed: any action, risk scoring, or the delegation tools. The server refuses duplicate tool
names across registries, so UC2 can't shadow UC4's `classify_document`.

## 7. Prompt history

| Prompt | Change | Why |
|---|---|---|
| `uc2-orchestrator.v2` | Budget 7 → 10; graceful stop | Live run 1: 12 of 36 cases exhausted the budget |
| `uc2-investigator.v2` | Defines "conflicting evidence" | Live run 2: supporting findings were reported as conflicts and over-triggered review |

v1 prompts are kept. Investigator v2 was declared the final iteration before run 3. Nothing was
tuned after it.
