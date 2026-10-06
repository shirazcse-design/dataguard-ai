# Observability and monitoring for UC2 Insider Risk

**Status (2026-10-04): set up and verified.** The product owner had planned to configure this by
hand, then asked Claude Code to set it up. Nothing new was created in Azure. UC2 reuses Application
Insights `dataguard-resource-appinsights`, which is connected to Foundry project `dataguard`. The
connection string was read from the project at run time through Entra sign-in. It was held in
memory only, never typed, printed or stored.

## What a trace shows

```
uc2.case                          dg.ir.case_id, dg.ir.subject (pseudonym), architecture, outcome, band, delegations, early_stop
 ├─ uc2.anomaly                   model version, anomaly score and band, n_signals (Isolation Forest: authoritative)
 ├─ uc2.orchestrator              invoke_agent
 │   ├─ uc2.agent.planner         chat (live calls only; tokens)
 │   ├─ uc2.tool                  execute_tool: delegate_behavior, get_identity_context, check_data_sensitivity, ...
 │   │   ├─ uc2.behavior_agent    invoke_agent > planner turns > behaviour tools
 │   │   ├─ uc2.identity          role family, privilege (access context, not HR)
 │   │   ├─ uc2.data              n_files, max level, uncertain   > UC4 spans (S0..S5, llm.call)
 │   │   ├─ uc2.policy            topic, status, effect, conflict > UC6 spans (uc6.*)
 │   │   └─ uc2.investigation_agent  invoke_agent > planner turns > log / approval / UC4 / UC6 tools
 │   └─ uc2.risk_agent            invoke_agent (no tools)
 ├─ uc2.deterministic_risk        score, floor, outcome, reason codes
 └─ uc2.hitl                      analyst review required
```

It answers:
* **Why anomalous?** The anomaly span holds the score and band, and the case's reason codes list the factors.
* **Which agents ran?** The agent spans.
* **Which tools?** The tool spans.
* **Which policy?** The policy span.
* **Where did latency go?** Span durations.
* **Did a tool fail?** `dg.agent.tool_error`.
* **Did a guardrail fire?** `dg.guardrail.type`.
* **Why did it stop?** `dg.agent.stopped_reason`, `dg.ir.early_stop`.
* **What was decided?** `dg.ir.outcome`.

**Privacy.** The deny-by-default allow-list (`config/observability/observability.v1.yaml`) exports
codes, scores and counts only. The subject is a salted hash (`subj-…`), never the user id. No raw
logs, document or policy text, justifications, repository or host names, tool arguments or agent
reasoning are exported.

## Commands

```
dataguard-insider obs report                       # local: 36 cases through the production redactor + audit
dataguard-insider obs live-check --tenant-id <t>   # LIVE: 3 investigations via the Foundry agents -> App Insights
```

## Verification

**Local** (`results/observability.md`): all 36 cases replayed from the recorded live run through
the production tracer. Privacy audit **CLEAN**: 1,403 spans checked against every case document and
policy section, plus 51 checks for user ids, repository names, destinations, injected text and
the canary.

**Live** (2026-10-04, canary `CANARYE1315133` planted as a non-instruction note in the flagship's
security logs; App Insights queried through the Entra-authenticated API):

| Check | DataGuard telemetry | Foundry Agent Service telemetry (`responsesapi`) |
|---|---|---|
| All 3 traces complete (4 agents, risk decision, HITL each) | yes (75-77 spans for the flagship) | n/a |
| GenAI view | 12 invoke_agent, 45 execute_tool, 25 chat (72,001 input tokens) | its own invoke_agent / chat spans |
| Canary from the raw logs | **0** | 14 records |
| Raw user id `u-2043` | **0** (pseudonym only) | 104 records |
| Repository name / destination host | **0** / **0** | 38 / 52 records |
| Document text (flagship spreadsheet) | **0** | **0** (agents never receive document text) |
| Injected log text | **0** | **0** (withheld by DataGuard before any agent saw it) |

**Finding.** DataGuard's telemetry is clean. **Foundry Agent Service records the agents'
conversations** (`gen_ai.input.messages` / `output.messages` and `genAIContent`). That includes the
evidence the agents legitimately read: the user id, and repository and destination names from the
logs. It is the same platform behaviour found in UC6 and UC1, where the product owner kept it on
for the synthetic demo.

**Decision (product owner, 2026-10-04): (a) keep it on for the synthetic demo**, as in UC1 and
UC6: all users, logs and resources are synthetic. Before production: turn off agent content
recording in the project's tracing settings, or pseudonymise user ids and resource names in the
agent payloads.

## Useful queries (App Insights → Logs)

```kusto
// Outcomes and why
dependencies | where name == "uc2.case"
| summarize count() by outcome=tostring(customDimensions["dg.ir.outcome"]), band=tostring(customDimensions["dg.ir.band"])

// Which agents ran, and how they stopped
dependencies | where name in ("uc2.orchestrator", "uc2.behavior_agent", "uc2.investigation_agent", "uc2.risk_agent")
| summarize count() by agent=tostring(customDimensions["dg.agent.name"]), stop=tostring(customDimensions["dg.agent.stopped_reason"])

// Tool use and failures
dependencies | where name == "uc2.tool"
| summarize calls=count(), failed=countif(tostring(customDimensions["dg.agent.tool_ok"]) == "False")
    by tool=tostring(customDimensions["dg.agent.tool"])

// Token usage per agent (live model calls only)
dependencies | where name == "uc2.agent.planner" and isnotempty(customDimensions["gen_ai.usage.input_tokens"])
| summarize tokens_in=sum(toint(customDimensions["gen_ai.usage.input_tokens"])) by agent=tostring(customDimensions["dg.agent.name"])

// Privacy spot check (replace the value)
union * | where * has "CANARY..." | summarize count() by cloud_RoleName
```
