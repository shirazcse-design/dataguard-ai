# UC2 architecture

## 1. Problem and personas

Insider risk is a triage problem: many unusual user-days, very few that matter, and each needing a
multi-source investigation before a person can decide. UC2 automates the **evidence work**, not the
**judgement**.

| Persona | Needs | What UC2 gives them |
|---|---|---|
| Insider-risk analyst | Fewer, explained cases | A ranked outcome, the anomaly drivers, a cited timeline, policy and data context, and review reasons |
| Security lead | Consistency, auditability, cost control | A versioned rubric, reason codes on every case, spans and token counts per agent |
| Employee | Not to be flagged for normal work or judged by a model | Per-user baselines; no protected or HR inputs; "anomaly ≠ intent" in every summary |
| Privacy / legal / HR partner | No automated employment decisions | No action tools; analyst decisions are recorded; no HR data in scoring |

## 2. The flow

```
activity (synthetic logs) ──► features (per-user, day-type-aware robust z) ──► Isolation Forest
                                                                                 │ score, band,
                                                                                 ▼ contributions
                                                              CasePacket (alert + authoritative anomaly)
                                                                                 │
                      ┌──────────────────────── orchestrator agent ─────────────┴───────────┐
                      │ delegate_behavior   get_identity_context   check_data_sensitivity   │
                      │ delegate_investigation   check_policy   request_human_review        │
                      ▼                                                    request_risk_assessment
             behavior agent                  investigation agent                         │
   profile · series · statistical       logs (scanned) · approvals ·                     ▼
   baseline (read-only)                 UC4 files · UC6 policy · access          risk agent (no tools)
                      └──────────── findings (ids only cited) ───────────────► recommendation
                                                                                         │
                                                         deterministic rubric (risk.v1.yaml 1.2.0)
                                                                                         ▼
                                                 MONITOR · INVESTIGATE · ESCALATE · HUMAN_REVIEW
                                                                                         │
                                                                     analyst review (no action exists)
```

* The **Isolation Forest result is authoritative.** Agents receive it and may explain it, but if a
  behaviour finding reports a different score or band, the authoritative values are restored and
  the event is logged (`anomaly_score_modified`).
* **Delegation is executed by DataGuard.** The orchestrator calls a delegation tool; the pipeline
  runs the specialist with a **fresh context** (only its brief) and returns structured findings.
  Specialists never see the orchestrator's conversation, and the orchestrator and risk agent never
  see raw log text.
* **The risk agent synthesizes, it doesn't act.** It has no operational tools and cannot enforce an
  outcome. The deterministic rubric remains authoritative.
* **Every stage is a span** (`uc2.case` > anomaly / orchestrator / behavior_agent / identity / data /
  policy / investigation_agent / risk_agent / deterministic_risk / hitl), with a pseudonymous
  subject. See [`foundry-observability-setup.md`](foundry-observability-setup.md).

## 3. Three architectures, one harness

| | Agents | What changes |
|---|---|---|
| `single` | 1 | One agent with all 11 read-only tools |
| `lean` | 2 | Orchestrator (behaviour, identity, data and policy tools) plus an investigator |
| `full` | 4 | Orchestrator, behavior, investigator, risk (the Foundry agents) |

The services, model (`uc4-llm-medium`), cases, rubric and scoring are identical. Budgets differ
because the work divides differently ("don't force identical budgets"). Results are in
[`evaluation.md`](evaluation.md#3-one-two-or-four-agents).

## 4. Reuse

UC2 is deliberately thin over the platform.

| Reused | How |
|---|---|
| **UC4** classifier | `DataService` calls `Uc4Classifier` (caller `insider-risk-agent`) on case files; document text never leaves UC4 |
| **UC6** policy copilot | `PolicyService` asks templated questions (`access_beyond_role`, `incident_procedure`, `monitoring`); only verified, cited claims return |
| **UC1** policy intelligence | `external_transfer` uses UC1's `PolicyIntelligence` mapping (UC4 level → UC6 effect), with a UC2 level-aware conflict rule |
| UC1/UC6 agent loop patterns | `BoundedAgent` follows UC1's allow-list, typed arguments, budgets and repair; replay via the shared `ReplayAgentClient` |
| Observability | The same tracer, allow-list redactor, GenAI mapping and App Insights resource |
| MCP server | The existing server gains opt-in `--insider-tools` (4 read-only tools) |
| Demo app | One new page plus panels on the shared Evaluations, Guardrails and Observability pages |

UC1, UC4 and UC6 behaviour is unchanged. New Foundry resources were additive, created only at the
product owner's explicit request.

## 5. Data

`ml/insider/synth.py` writes `data/insider/`:
* `users.json`: 60 name-free users u-2001..u-2060 in 7 role families. No region, demographic or HR
  fields.
* `permissions.json`, `activity.csv`: 90 days; days 1-60 train on normal behaviour only.
* `eval_labels.csv`: labels used **only** for evaluation.

Scenarios S1-S8 inject anomalies (flagship, slow drip, source-code grab, credential probing and
others). H1-H5 inject hard-legitimate patterns (month-end close, release week, approved migration,
travel, onboarding). Log events, approvals and the case files UC4 classifies are deterministic per
case. See [`ml-design.md`](ml-design.md).

## 6. Deliberately out of scope

* Real identity, HRIS or SIEM connectors.
* Any enforcement (disable account, revoke access, notify a manager).
* Per-employee risk scores over time.
* Automated retraining.
* Harmonising UC1's notice-period input with UC2.

All are recorded in [`lessons-learned.md`](lessons-learned.md).
