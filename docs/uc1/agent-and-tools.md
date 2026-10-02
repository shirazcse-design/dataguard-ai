# UC1 agent, tool boundaries and MCP

## 1. Why an agent here, and why so small

After the deterministic stages, most of a case is already known. The agent's job is the **gaps**:
* is there an approved exception?
* does the user's recent activity matter?
* is a more specific policy clause relevant?
* should a person look at this?

It writes the analyst-facing findings, each tied to a piece of evidence. It does **not** classify,
score, decide or act.

The tool set was approved deliberately small:

| Tool | Arguments | What it returns |
|---|---|---|
| `search_policy` | `query` | up to 5 policy sections from UC6 retrieval (`E1..En`); injected chunks withheld |
| `get_policy_section` | `policy_id`, `section` | one exact current section |
| `get_user_activity` | `days` (1-30) | 7-day features of **this event's** user (`ACTIVITY`) |
| `check_dlp_exception` | none | an approved exception for **this** user and **this** destination covering the file's level, or why none applies (`EXCEPTION`) |
| `request_human_review` | `reason` (enum) | records the request; the harness turns it into a review trigger |

**Deliberately left out** (approved plan):
* `classify_document`, `get_user_profile`, `evaluate_destination` and `calculate_risk_score`:
  mandatory deterministic stages, not agent choices;
* `simulate_block_action`: a simulated action is a harness output that needs human approval.

## 2. Boundaries, enforced in code

| Boundary | Mechanism |
|---|---|
| Allow-list | Any other tool name returns `unknown_tool` (a failed step); there are no write tools |
| Typed arguments | Each tool validates its own arguments (`invalid_arguments`); schemas have `additionalProperties: false` |
| Bound to the event | `check_dlp_exception` takes **no** arguments: user, host, level and date come from the event, so the model cannot query someone else's exception |
| Typed tool output | A malformed activity record is rejected (`malformed_tool_result`), never passed on |
| Budgets | at most 6 tool calls and 8 planner turns; 2 consecutive tool failures stop the loop |
| No document text | The evidence pack carries level, categories and codes, never the file's content or filename |
| Untrusted justification | Scanned by UC6's injection scanner; flagged text is replaced by `[withheld: instruction-like text detected]` |
| Exceptions | `verified_exception` is set **only** from a matching record returned by the tool, never from the model's words |
| Findings | Each must cite an evidence id the agent was shown; findings with unknown ids are marked unverified |
| Decision | The agent's `proposed_outcome` is an input to the harness: higher sends the case to HUMAN_REVIEW, lower is ignored and logged |

## 3. The evidence pack (prompt `uc1-agent.v2`)

The agent receives one JSON message. Every section has its own evidence id:

| Id | Contents |
|---|---|
| `EVENT` | action, timestamp |
| `DESTINATION` | class, host, account type, known to user |
| `PRECHECKS` | deterministic findings (codes) |
| `CLASSIFICATION` | level, policy term, categories, confidence, review flag, injection flag |
| `IDENTITY` | role, department, employment type/status, privilege, region |
| `BEHAVIOR` | band and signals |
| `POLICY` | UC6 status, effect, conflict |
| `P1..Pn` | each verified UC6 policy claim with its citation |
| `E1..En` | policy text returned by `search_policy` / `get_policy_section` |
| `ACTIVITY`, `EXCEPTION` | tool results |

**v1 → v2.** In v1 only policy claims had ids. Foundry's AI judges found the agent citing policy
ids for facts about the file, user or destination. Our id-existence check could not see this,
because the ids existed. v2 added an id to every section and made "no exception" a citable result.
Groundedness went from 23/30 to 30/30, and task adherence from 19/30 to 28/30
([`results/foundry-evals.md`](results/foundry-evals.md)).

The final answer is JSON only:

```json
{"proposed_outcome": "ALLOW | WARN | ESCALATE | HUMAN_REVIEW",
 "findings": [{"text": "...", "evidence_id": "DESTINATION"}],
 "missing_evidence": []}
```

## 4. MCP

Decision (approved): **extend the existing MCP server**, not a second server.

* `classify_document` is unchanged: same name, schema, frozen output schema and behaviour. Without
  `--dlp-tools`, the server lists exactly the UC4 tool (tested).
* With `--dlp-tools`, it also lists the read-only `search_policy`, `get_user_profile` and
  `get_user_activity` (`app/dlp/mcp_tools.py`), all annotated `readOnlyHint: true`.
* **Per-tool caller allow-list** in a separate file, `config/dlp/mcp_tools.v1.yaml`, so UC4's
  config schema is unchanged. A caller not listed for a tool gets an authorization error
  (`-32001`) before the tool runs.
* Typed inputs (pydantic, `extra="forbid"`): `user_id` must match `^u-\d{4}$`, `days` must be
  1-30, `query` 3-300 characters. Errors name the field, never echo the value.
* **Not exposed:** `simulate_block_action`, `calculate_risk_score` and `check_dlp_exception` (which
  is event-bound inside the agent).

```
DATAGUARD_MCP_CALLER_ID=dlp-investigation-agent dataguard-uc4-mcp --llm-mode replay --dlp-tools
```

Over stdio the caller identity is asserted by whoever launches the server, not authenticated (the
same limitation UC4 documents).

## 5. Planners

| Backend | Where it runs | Cache namespace |
|---|---|---|
| `chat-completions` | `uc4-llm-medium` via UC4's `FoundryAgentClient` | `uc4-llm-medium/uc1-agent.v2` |
| `foundry-service` | Foundry Agent Service agent `dataguard-dlp-investigator` v3, with guardrail `uc1-dlp-investigator-guardrail` | `dataguard-dlp-investigator/uc1-agent-service.v2` |
| `offline` | `offline-planner` (deterministic stand-in, labelled; not a model) | none |

In every case the tools execute in DataGuard's loop. Foundry only proposes the next call.
