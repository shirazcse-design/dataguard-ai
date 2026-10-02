# Observability and monitoring for the Agentic DLP investigator (UC1)

**Status (2026-10-01): set up and verified.** The product owner explicitly asked Claude Code to
set it up. Nothing new was created in Azure. UC1 reuses the Application Insights resource
`dataguard-resource-appinsights`, which is already connected to Foundry project `dataguard` (UC4,
D9.31). The connection string was read from the project at run time through Entra sign-in
(`--from-project`). It was held in memory only: it was never typed, printed, or written to the
repository, config or chat.

## What is in place

| Piece | State |
|---|---|
| Application Insights | `dataguard-resource-appinsights` (existing; shared with UC4 and UC6) |
| Export path | `observability/sinks.py` Azure Monitor bridge (UC4 code, reused unchanged) |
| UC1 spans | `uc1.investigation` > `uc1.prechecks`, `uc1.classification` (UC4's `S*` / `llm.call` spans nest inside), `uc1.identity`, `uc1.behavior`, `uc1.policy_retrieval` (UC6's `uc6.*` spans nest inside), `uc1.agent` > `uc1.agent.planner`, `uc1.tool`, then `uc1.risk_decision`, `uc1.hitl_decision` |
| GenAI view (Foundry Tracing) | `uc1.agent` exports as **invoke_agent**, `uc1.tool` as **execute_tool**, and `uc1.agent.planner` as **chat** (live calls only; replayed turns are not counted as model calls) |
| Privacy | Deny-by-default allow-list (`config/observability/observability.v1.yaml`). UC1 adds only `dg.dlp.*` fixed codes and numbers: a schema-validated case id, outcome, band, score, risk level, reason codes, simulated faults, destination **class**, behaviour band and counts. Never a user id, host, filename, justification, document text, policy text or tool arguments. |

Useful App Insights `customDimensions`: `dg.dlp.case_id`, `dg.dlp.outcome`, `dg.dlp.band`,
`dg.dlp.score`, `dg.dlp.reason_codes`, `dg.dlp.simulated_faults`, `dg.dlp.destination_class`,
`dg.dlp.policy_effect`, `dg.agent.tool`, `dg.agent.stopped_reason`, `dg.llm.cached`,
`dg.tokens_in` / `dg.tokens_out`, `dg.latency_ms`.

## Commands

```
dataguard-dlp obs report                     # local: all 30 cases, privacy audit, telemetry summary
dataguard-dlp obs live-check --from-project --tenant-id <tenant>   # LIVE: 4 investigations to App Insights
```

The live check sends four investigations, each through the live Foundry agent
`dataguard-dlp-investigator` (UC4/UC6 replay from the recorded caches):

1. D11, the flagship;
2. D11 with a unique **CANARY** token inside the user's justification;
3. D10 with the CANARY token inside the destination **host**;
4. D26, the prompt-injection justification.

## Verification

**Local (`dataguard-dlp obs report`, replay, all 30 cases):** privacy audit **CLEAN**.
649 spans were audited against 104 sources (case documents and every policy section): 2,892 word
windows, 5 sensitive-value patterns, and 62 user-id / destination-host / justification checks.
See [`results/observability.md`](results/observability.md).

**Live (2026-10-01, two runs, queried with the App Insights query API under Entra):**

1. **Arrival, and a sampling bug found and fixed.**
   * First run (canary `CANARYB8EC5B5B`): 1 of 4 traces arrived incomplete. `OBS-CANARY-J`
     was missing its UC6 retrieval and generation, agent, risk-decision and HITL spans, although
     the investigation itself completed (ESCALATE).
   * Cause: `azure-monitor-opentelemetry` 1.8 defaults to a **rate-limited sampler (5 traces/s)**,
     and DataGuard re-emits a whole request's spans in one burst.
   * Fix: `observability/sinks.py` now defaults `OTEL_TRACES_SAMPLER=always_on` unless an operator
     set a sampler explicitly. This is shared code, so UC4 and UC6 exports are complete too.
     Sampling affects completeness only, never content. A unit test covers it.
   * Second run (canary `CANARY30F4F152`): **all 4 traces complete** (20-23 spans each, each with
     `uc1.agent`, `uc1.risk_decision` and `uc1.hitl_decision`). The spans map as designed:
     `uc1.agent` → invoke_agent, `uc1.agent.planner` → chat, `uc1.tool` → execute_tool.
   * The outcomes, review-reason and tool-use queries below were run against this data and return
     it.
2. **DataGuard's own telemetry is clean.** Non-Foundry records containing the canary: **0**, in
   both runs. Document text and filename anywhere in App Insights: **0**.
3. **Foundry Agent Service records conversation content, in the same App Insights.** The canary,
   the canary host, user id `u-1003`, `dropbox.com` and the justification phrase **were found**
   in the first run (7, 5, 7, 20 and 2 records; in the second run, 6 `dependency` + 6
   `genAIContent` records for the canary, all `responsesapi`). Every match comes from Foundry's own telemetry
   (`cloud_RoleName: responsesapi`, SDK `aifoundry-v1.0`): its `invoke_agent
   dataguard-dlp-investigator:2` and `chat gpt-5.4` spans carry `gen_ai.input.messages` /
   `gen_ai.output.messages`, plus matching `genAIContent` items. This is the same platform
   behaviour found for UC6, where the owner decided to keep it on for the demo.
   * What it exposes: the **evidence pack** sent to the agent. That includes the user id,
     destination host and, when not withheld, the user's justification.
   * What it does **not** expose: document text (`Kestrel`: **0**) and the filename
     (`Acquisition_Targets_2027`: **0**). The agent never receives them, so the platform cannot
     record them.
   * **Decision (product owner, 2026-10-02): (a) keep it on for the demo**, as in UC6, since all
     data is synthetic. Revisit before production: turn off agent content recording in the
     project's tracing settings, or pseudonymise the user id and host in the evidence pack.
4. Our spans show `cloud_RoleName = unknown_service`; setting `OTEL_SERVICE_NAME=dataguard` would
   label them. Cosmetic; not changed.

## Useful queries (App Insights → Logs)

```kusto
// UC1 outcomes over time
dependencies
| where name == "uc1.investigation"
| summarize count() by tostring(customDimensions["dg.dlp.outcome"]), bin(timestamp, 1h)

// Why cases went to human review
dependencies
| where name == "uc1.investigation"
| mv-expand code = extract_all(@"(review:[a-z_]+)", tostring(customDimensions["dg.dlp.reason_codes"]))
| where isnotempty(code)
| summarize count() by tostring(code)

// Agent tool use and failures
dependencies
| where name == "uc1.tool"
| summarize calls = count(), failed = countif(tostring(customDimensions["dg.agent.tool_ok"]) == "False")
    by tostring(customDimensions["dg.agent.tool"])

// Privacy spot check: replace with any value that must never appear
union * | where * has "CANARY..." | count
```

## Alerts (optional, not created)

Two Log-search alert rules would make sense in production. Neither was created, because the demo
data is synthetic and an alert would only produce noise:

* critical false negatives can't be seen at run time, so alert on a **rise in HUMAN_REVIEW** with
  `review:stage_failure` (a dependency outage) instead;
* **agent failures**: `uc1.agent` spans with a `dg.agent.stopped_reason` other than `final_answer`.
