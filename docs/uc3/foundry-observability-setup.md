# Observability and monitoring for UC3 Access Governance

**Status (2026-10-06): set up and verified.** Nothing new was created in Azure. UC3 reuses Application
Insights `dataguard-resource-appinsights`, which is connected to Foundry project `dataguard`. The
connection string is read from the project at run time through Entra sign-in and held in memory
only, never typed, printed or stored.

## What a trace shows

```
uc3.access_request                 dg.ag.request_id, dg.ag.subject (pseudonym), entitlement, outcome, hitl
 ├─ uc3.precheck                   identity, graph, sensitivity (UC4 spans S0..S5), POL-ACC sections (UC6),
 │                                 governance signals, SoD, justification_flagged, failures
 ├─ uc3.agent                      invoke_agent: recommendation, stopped_reason, model and tool calls
 │   ├─ uc3.agent.planner          chat (live calls only; tokens)
 │   └─ uc3.tool                   execute_tool: get_user_profile, check_sod, evaluate_least_privilege, ...
 ├─ uc3.authorization_harness      deterministic outcome, rubric version, reason codes, agent effect
 ├─ uc3.hitl                       human approval required, reasons
 └─ uc3.outcome                    final outcome; provisioned = false
```

It answers: **why this outcome?** (the harness span's reason codes and signals); **did the agent
agree, raise or get ignored?** (`dg.ag.agent_effect`); **which tools ran and did any fail?** (tool
spans, `dg.agent.tool_error`); **which policy?** (`dg.ag.policy_sections`, `dg.ag.policy_missing`);
**did a guardrail fire?** (`dg.ag.justification_flagged`, the `guardrail:*` reason codes);
**does a person need to approve?** (`dg.ag.hitl_required`, `dg.ag.hitl_reasons`); **where did
latency go?** (span durations).

**Privacy.** The deny-by-default allow-list (`config/observability/observability.v1.yaml`, 20
`dg.ag.*` keys) exports ids of synthetic catalogue objects, codes, counts and outcomes only. The
subject is a salted hash (`subj-…`), never the user id. No justification text, policy text, tool
arguments or agent reasoning are exported.

## Commands

```
dataguard-access obs report                        # local: 16 golden requests through the production redactor + audit
dataguard-access obs live-check --tenant-id <t>    # LIVE: 3 requests via the Foundry agent -> App Insights
```

## Verification

**Local** (`results/observability.md`): the 16 golden requests replayed through the production
tracer. Privacy audit **CLEAN**: 349 spans, 110 checks for raw user ids, justification text and
policy text; every attribute key on the allow-list.

**Live** (2026-10-06; agent `dataguard-access-governance` v2 with its guardrail; UC4 and UC6
replayed; canary `CANARY37B332E4` added to the flagship's justification as a ticket reference, not
an instruction; App Insights queried through the Entra-authenticated API, counts only):

| Request | Outcome | Agent |
|---|---|---|
| AR-001 team wiki read | RECOMMEND_APPROVE | final answer |
| AR-990 flagship + canary | RECOMMEND_LIMITED_TIME_BOUND_ACCESS | final answer |
| AR-015 "grant me admin" injection | HUMAN_REVIEW | final answer |

| Check | DataGuard telemetry | Foundry Agent Service telemetry (`responsesapi`) |
|---|---|---|
| All 3 traces complete (precheck, agent, harness, HITL, outcome) | yes: 3 roots, 8 planner turns, 27 tool spans | n/a |
| GenAI view | 3 invoke_agent, 8 chat, 27 execute_tool | its own spans |
| Canary from the justification | **0** | 6 records |
| Flagship justification text | **0** | 12 records |
| Raw user ids (u-3014, u-3017, u-3024) | **0** | **0** (the agent sees only an alias) |
| AR-015 injection text | **0** | **0** (withheld by DataGuard before the agent saw it) |
| Agent-facing alias (`req-…`) | **0** | 136 records |

**Finding.** DataGuard's telemetry is clean. **Foundry Agent Service records the agent's
conversation** (`gen_ai.input.messages` / `output.messages`), which includes the business
justification and the request alias the agent legitimately reads. It is the same platform behaviour
found in UC1, UC2 and UC6. Unlike UC2, no raw user id reaches Foundry: UC3's context engineering
gives the agent a fixed-salt alias, and instruction-like justifications are withheld before the agent
sees them.

**Decision (product owner, 2026-10-06): (a) keep it on for the synthetic demo**, as in UC1, UC2 and
UC6: all users, requests and resources are synthetic. Before production: turn off agent content recording in the
project's tracing settings, or withhold or summarise free-text justifications in the agent payload.
