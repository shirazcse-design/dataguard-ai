# Manual setup: Foundry observability and monitoring for the Policy Copilot (UC6)

**Owner: Shiraz (manual, in the Foundry and Azure portals).** Claude Code does not create or
configure tracing, Application Insights, dashboards or alerts. The application side is already
instrumented, and a local privacy audit of it is clean
([`results/observability.md`](results/observability.md)).

## What already exists (no action needed)

| Piece | State |
|---|---|
| Application Insights `dataguard-resource-appinsights` | Connected to Foundry project `dataguard` (UC4, D9.31) |
| Export path | `observability/sinks.py` Azure Monitor bridge (UC4 code, reused unchanged) |
| UC6 spans | `uc6.request` > `uc6.input_guard`, `uc6.retrieve`, `uc6.generate`, `uc6.citation_verify`; agentic: `uc6.agent` > `uc6.agent.planner`, `uc6.tool` |
| GenAI view | `uc6.generate` / `uc6.agent.planner` export as **chat** spans (live calls only), `uc6.agent` as **invoke_agent**, `uc6.tool` as **execute_tool** |
| Privacy | deny-by-default allow-list: ids, counts, statuses, fixed codes and `POL-XXX:4.2` section ids only. Never the question, search queries, policy text, quotes or answers. |

Useful attributes (App Insights `customDimensions`): `dg.request_id`, `dg.policy.level`,
`dg.policy.status`, `dg.policy.mode` (live/replay), `dg.policy.cited`,
`dg.policy.claims_unverified`, `dg.policy.fabricated_citations`, `dg.outcome.review_reasons`,
`dg.guardrail.type`, `dg.policy.dense_status`, `dg.llm.model_id`, `dg.tokens_in`/`dg.tokens_out`,
`dg.llm.cached`, `dg.agent.tool`, `dg.agent.stopped_reason`, `dg.latency_ms`.

## Step 1 - Enable and check tracing in Foundry

1. Open <https://ai.azure.com> with **New Foundry** on, and select project **dataguard**.
2. Go to **Observe → Tracing** (in the classic portal: **Tracing** in the left menu). The
   connected resource should be `dataguard-resource-appinsights`. If it asks to connect one, pick
   that resource. Do not create a new one; UC4's traces already live there.
3. If there is a setting for recording **message content** / **prompt and completion content**
   for agents or model calls, note its value. Step 5 tests whether it leaks content, and the
   recommendation is **off** (see Step 5).

## Step 2 - Send live UC6 traces (one command, in your Terminal)

The application exports only when you run it with the connection string. Get it from the Azure
portal: **Application Insights → dataguard-resource-appinsights → Overview → Connection String**.
It is a secret (it contains the ingestion key), so never paste it into chat or a file.

Claude Code gives you a small script, `uc6_obs_live_check.zsh` in the session scratchpad. It
asks, with hidden input, for the connection string and the resource API key, and it signs you in
through the browser for the agent. It then runs:

```
dataguard-policy obs live-check --agent-backend foundry-service --tenant-id <tenant>
```

This sends four live requests:

1. S01, the primary demo question (Advanced)
2. a question containing a random **CANARY** token (Advanced)
3. a question containing the same CANARY token (Agentic, through the Foundry agent
   `dataguard-policy-copilot`)
4. a prompt-injection question (blocked by the input guard)

It prints the four `request_id`s and the canary, e.g. `CANARYA1B2C3D4`. Paste that output back
to Claude Code; it contains no secret. Ingestion takes 2-5 minutes.

## Step 3 - Verify traces, model calls, agent/tool calls, tokens and latency

In **Observe → Tracing**, filter to the last hour. Then check:

| # | Check | Where / how | Expected |
|---|---|---|---|
| 1 | Traces arrived | Tracing list | Traces named `uc6.request` from Step 2 |
| 2 | Span tree | open the S01 trace | `uc6.request` > `uc6.input_guard`, `uc6.retrieve`, `uc6.generate` (**Chat**), `uc6.citation_verify` |
| 3 | Model call | the `uc6.generate` span | model `uc4-llm-medium`, served `gpt-5.4-...`, input/output tokens present |
| 4 | Agent and tool calls | the agentic trace | `uc6.agent` (**invoke_agent**, `dataguard-policy-copilot`) > `uc6.agent.planner` (Chat) and `uc6.tool` (**execute_tool**, `search_policy`), with **no arguments or results attached** |
| 5 | Latency | span durations | `uc6.generate` is most of `uc6.request`; the other stages take milliseconds |
| 6 | Blocked request | the injection trace | only `uc6.input_guard`; root `dg.policy.status = BLOCKED`, `dg.guardrail.type = prompt_injection_suspected`, no Chat span |
| 7 | Errors/retries | any span | `dg.llm.attempts` = 1 normally; a transport retry shows > 1; a failure shows `error.type` |

The Foundry agent's own view: **Build → Agents → dataguard-policy-copilot → Traces / Monitor**
(if your portal shows it). It lists the Agent Service runs from Step 2. These are Foundry's
server-side records, separate from the application's `uc6.*` spans.

## Step 4 - Logs (KQL) for the same checks

**Application Insights → Logs**. OpenTelemetry spans land in `dependencies` (and `requests` for
roots, depending on span kind), so the queries union both.

```kusto
// 1. UC6 spans in the last hour, by name
union dependencies, requests
| where timestamp > ago(1h) and name startswith "uc6."
| summarize count(), p50_ms = percentile(duration, 50), p95_ms = percentile(duration, 95) by name
```

```kusto
// 2. Outcomes per level (root spans)
union dependencies, requests
| where timestamp > ago(1d) and name == "uc6.request"
| extend level = tostring(customDimensions["dg.policy.level"]),
         status = tostring(customDimensions["dg.policy.status"]),
         mode   = tostring(customDimensions["dg.policy.mode"])
| summarize runs = count() by level, status, mode
```

```kusto
// 3. Token usage by model (live model calls only; replayed calls carry dg.llm.cached = true)
union dependencies, requests
| where timestamp > ago(1d) and name in ("uc6.generate", "uc6.agent.planner")
| where tostring(customDimensions["dg.llm.cached"]) != "true"
| summarize calls = count(),
            tokens_in  = sum(toint(customDimensions["dg.tokens_in"])),
            tokens_out = sum(toint(customDimensions["dg.tokens_out"])) by tostring(customDimensions["dg.llm.model_id"])
```

```kusto
// 4. Agent tool calls and stop reasons
union dependencies, requests
| where timestamp > ago(1d) and name in ("uc6.tool", "uc6.agent")
| summarize count() by name, tool = tostring(customDimensions["dg.agent.tool"]),
            ok = tostring(customDimensions["dg.agent.tool_ok"]),
            stop = tostring(customDimensions["dg.agent.stopped_reason"])
```

```kusto
// 5. Quality/safety signals: citation failures, guardrail events, insufficient evidence
union dependencies, requests
| where timestamp > ago(1d) and name == "uc6.request"
| summarize requests = count(),
            insufficient = countif(tostring(customDimensions["dg.policy.status"]) == "INSUFFICIENT_EVIDENCE"),
            blocked      = countif(tostring(customDimensions["dg.policy.status"]) == "BLOCKED"),
            review       = countif(tostring(customDimensions["dg.outcome.review_required"]) == "true"),
            unavailable  = countif(tostring(customDimensions["dg.policy.status"]) == "UNAVAILABLE")
```

## Step 5 - Verify no question or policy text leaked (the privacy check)

Replace `CANARYxxxxxxxx` with the token Step 2 printed:

```kusto
// Must return 0 rows: the canary only ever existed inside a user question
search in (dependencies, requests, traces, customEvents, exceptions) "CANARYxxxxxxxx"
| where timestamp > ago(1d)
```

```kusto
// Must return 0 rows: distinctive phrases from the policy corpus and the answer to S01
search in (dependencies, requests, traces, customEvents) "personal cloud-storage account" or "multi-factor authentication" or "Harbourline"
| where timestamp > ago(1d)
```

**If either returns rows**, check the `cloud_RoleName` / source of those rows:

* rows from the application's `uc6.*` spans: a real leak in DataGuard. Tell Claude Code, because
  the local audit is supposed to make this impossible.
* rows from **Foundry Agent Service's own tracing** (the service logging conversation content):
  this is the Step 1 **content recording** setting. Turn it **off** for this project, or keep it
  on only if you accept that Foundry stores question text. That is a product/privacy decision to
  record, not something the application can control.

## Step 6 - Monitoring and alerts (recommended)

In **Observe → Monitoring** (Foundry), confirm the project dashboard shows request count, latency
and token usage for `uc4-llm-medium` and the agent. Then create alerts in **Application Insights →
Alerts → Create → Alert rule → Custom log search**, each evaluated every 15 minutes over 1 hour:

| Alert | Query (summarised) | Threshold | Why |
|---|---|---|---|
| UC6 unavailable rate | `uc6.request` with status `UNAVAILABLE` / all | > 10% | model or retrieval outage |
| Guardrail spike | `uc6.request` with `dg.guardrail.type` present | > 5 per hour | possible injection campaign |
| Citation failures | sum of `dg.policy.fabricated_citations` + `dg.policy.claims_unverified` | > 0 | the model started citing badly |
| Latency | p95 of `uc6.request` duration | > 20 s | PRD latency expectations |
| Replay in production | `dg.policy.mode == "replay"` on a production role | > 0 | recorded answers must never serve users |

An action group (email to yourself) is enough for the demo.

## Verification checklist (tick in the portal)

- [ ] Tracing is connected to `dataguard-resource-appinsights`
- [ ] The Step 2 traces are visible, with the correct span tree
- [ ] The model call shows the model, tokens and latency; replayed calls are not shown as Chat
- [ ] The agent trace shows `invoke_agent` > planner Chat + `execute_tool search_policy`, without arguments or results
- [ ] The blocked injection trace has no model call
- [ ] KQL 1-5 return sensible numbers
- [ ] **CANARY search: 0 rows**
- [ ] **Policy-phrase search: 0 rows** (or any rows explained, and the content-recording decision made)
- [ ] The monitoring dashboard is visible; at least the unavailable-rate and citation-failure alerts exist

Send the checklist results (and any non-zero search) back to Claude Code, which records them in
`docs/uc6/results.md` as portal-verified by you, not as automated checks.
