# Observability and monitoring for UC5 Incident Investigation

**Status (2026-10-07): set up and verified live.** Nothing new was created in Azure. UC5 reuses
Application Insights `dataguard-resource-appinsights`, connected to Foundry project `dataguard`. The
connection string is read from the project at run time through Entra sign-in and held in memory only.

## What a trace shows

```
uc5.incident_case                dg.ic.case_id, dg.ic.subject (pseudonym), trigger type, severity, review, calls
 ├─ uc5.case_initialization      the minimal packet: file count, prompt version
 ├─ uc5.agent                    invoke_agent: model turns (uc5.agent.planner = chat) and tools (uc5.tool =
 │                               execute_tool), with UC4 (S0..S5, llm.call) and UC6 (uc6.*) spans nested
 ├─ uc5.authoritative_facts      the harness's own evidence: failures, UC4 level, UC2 band, transfers, counts
 ├─ uc5.evidence_correlation     correlations, conflict and gap rule ids
 ├─ uc5.timeline_build           events, order-uncertain count
 ├─ uc5.report_validation        claims, unsupported, withheld, claim types
 ├─ uc5.deterministic_severity   floor, severity, rule, agent effect, rubric version, potential Sev 1
 ├─ uc5.human_review             review status and reasons
 └─ uc5.final_report             remediation = NONE_EXECUTED
```

It answers: what was decided and why (rule, reason codes), which evidence sources the agent used and
which tools failed, whether the agent agreed or raised, which correlations, conflicts and gaps were
found, where latency and tokens went, and whether a person must review.

**Privacy.** The deny-by-default allow-list (`config/observability/observability.v1.yaml`, 33
`dg.ic.*` keys) exports codes, counts, versions and rule ids only. The subject is a salted pseudonym.
No log or document text, justifications, destination hosts, policy text, tool arguments or agent
reasoning are exported.

## Commands

```
dataguard-incident obs report                       # local: 16 golden incidents through the production redactor + audit
dataguard-incident obs live-check --tenant-id <t>   # LIVE: 3 incidents via the Foundry agent -> App Insights, with a canary
```

## Verification

**Local** (`results/observability.md`): 16 incidents through the production tracer. Privacy audit
**CLEAN**: 497 spans, 143 checks (raw user ids, agent aliases, untrusted free text, document ids and
text, destination hosts, policy text); every key on the allow-list.

**Live** (2026-10-07; agent `dataguard-incident-investigator` v2 with its guardrail; UC4/UC6 replayed;
a fresh canary planted as a benign ticket reference in the flagship's DLP user justification and in a
log comment; one sign-in for the run and the read-only queries):

| Check | DataGuard telemetry | Foundry Agent Service (`responsesapi`) |
|---|---|---|
| Traces complete (all 10 uc5 stages) | 3 of 3; 12 model turns, 27 tool calls | its own spans |
| GenAI view | 3 invoke_agent, 12 chat, 27 execute_tool | |
| Canary (justification + log comment) | **0** | 10 records |
| Raw user ids (u-2043, u-2028, u-2016) | **0** | **0** |
| Agent alias of the subject | **0** | 28 records |
| INC-012 injection text | **0** | **0** (withheld before the agent saw it) |
| Document id | **0** | **0** (the agent sees file handles only) |
| Destination host `dropbox.com` | **0** (see note) | 20 records |

**Note on the host.** A first query counted 6 non-Foundry-agent records containing `dropbox.com`. A
read-only follow-up showed none is a DataGuard span: they are Foundry **Evaluations** records
(`genAIContent`, and `gen_ai.evaluation.result` events whose `gen_ai.evaluation.explanation` field holds
the judges' written reasons) from the UC5 evaluation run earlier the same evening. The first query's
filter ("not the agent service") was too broad.

**Findings.**
1. DataGuard's telemetry is clean, live.
2. **Raw user ids never reach Foundry** in UC5: tool results carry only the alias. This closes the
   limitation recorded for UC3, where grant paths carried raw (synthetic) ids.
3. Foundry Agent Service records the agent's conversation (the canary, alias and destination hosts the
   agent legitimately reads), as in UC1-UC3 and UC6. Foundry Evaluations also write the judges'
   explanations, which quote evidence, into App Insights.

**Decision (product owner, 2026-10-07): (a) keep it on for the synthetic demo**, as in UC1-UC3 and
UC6: all users, events, files and destinations are synthetic. Before production: turn off agent content recording in the
project's tracing settings, or keep free text out of agent-visible tool results.
