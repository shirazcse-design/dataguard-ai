# UC6 observability: Foundry / Application Insights verification

**Verified by the product owner in the Azure portal on 2026-09-29, from screenshots reviewed in the
session.** These are portal observations, not automated checks. The automated evidence is
[`observability.md`](observability.md): the replay run through the production redactor, whose
privacy audit is clean.

## The live run

`dataguard-policy obs live-check --agent-backend foundry-service` (the product owner's terminal;
connection string and key entered at hidden prompts; export flushed OK). Canary: `CANARYB444E0F7`.

| request_id | What | Path | Result |
|---|---|---|---|
| `40f2d814d869` | S01 | Advanced, live model call | ANSWERED |
| `480d9c4f29ed` | question containing the canary | Advanced, live model call | ANSWERED |
| `f10db4ee601f` | question containing the canary | Agentic via Foundry agent `dataguard-policy-copilot` v5 | ANSWERED |
| `887cdbd050fe` | prompt injection | input guard | BLOCKED (no model call) |

## Privacy searches (Application Insights `dataguard-resource-appinsights`, Logs, KQL, last 24 h)

| Search | Result | Source of every row |
|---|---|---|
| `search "CANARYB444E0F7"` | **8 rows**: 4 in `genAIContent`, 4 in `dependencies` (`invoke_agent dataguard-policy-copilot:5`, `chat gpt-5.4-2026-03-05`) | **`responsesapi`** (Foundry Agent Service), all 8. **0 rows from DataGuard's `uc6.*` spans.** |
| `search "personal cloud-storage account" or "multi-factor authentication" or "Harbourline"` | **52 rows**, from the 2026-09-29 agent live check and this run | Every row inspected was `genAIContent` or an Agent Service `dependency` carrying `gen_ai.*` fields. A per-source count was **not** run, so "none from DataGuard" is confirmed for the canary but not separately for this search. |

## Findings

1. **DataGuard's own telemetry passed the canary test.** The canary question went through both
   application paths: a direct model call, and the agent loop with its tools. It appears in none of
   DataGuard's spans. This matches the local audit.
2. **Foundry Agent Service records conversation content.** Its server-side tracing stored the
   question and the policy text in tool outputs (the `genAIContent` table and `gen_ai.*` span
   fields) in the same Application Insights resource. This is Foundry's own content recording, not
   data the application exports, and the application cannot suppress it.
3. The direct model-call path (`480d9c4f29ed`, chat completions) left **no** content anywhere.
   Server-side content recording applies to the Agent Service path only.

## Decision (product owner, 2026-09-29)

**Keep Foundry's agent content recording ON for the demo.** The corpus and questions are synthetic.

Production guidance recorded with it: with content recording on, "no question or policy text in
telemetry" holds for DataGuard's telemetry but **not** for the Foundry agent path. In production:
turn content recording off for the project/agent, or send sensitive questions through the
application's own agent path (`--agent-backend chat-completions`), which leaves no server-side
content, and restrict access to the Application Insights resource (the `genAIContent` table in
particular).

## Checklist status (from `foundry-observability-setup.md`)

| Item | Status |
|---|---|
| Tracing connected to `dataguard-resource-appinsights` | yes (traces arrived there) |
| Live traces visible | yes (Agent Service spans seen; DataGuard spans implied by the searches, span tree not reviewed) |
| Span tree / model tokens / agent `execute_tool` view (Step 3) | not reviewed in the portal yet |
| KQL 1-5 (Step 4) | not run yet |
| CANARY search | done: 0 DataGuard rows; 8 Foundry Agent Service rows (finding 2) |
| Policy-phrase search | done: 52 rows seen from Foundry; per-source count not run |
| Content-recording decision | made: keep on for the demo (above) |
| Monitoring dashboard + alerts (Step 6) | not done yet |
