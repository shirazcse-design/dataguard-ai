# UC5 architecture

## 1. Flow

```
 Alert (DLP alert, anomaly alert or analyst referral)
        │
        ▼
 uc5.case_initialization   MINIMAL packet: case id, subject ALIAS, trigger, window, file handles + resources
        │                  (no evidence, no labels)
        ▼
 uc5.agent                 ONE bounded agent: plan → select tool → gather → reason → find gaps → gather more
        │                  → build_timeline → report. 10 read-only, case-bound tools over UC1-UC6.
        ▼
 uc5.authoritative_facts   the harness gathers the facts ITSELF through the same services (never depends on
        │                  which tools the agent chose)
 uc5.evidence_correlation  10 versioned rules: links, conflicts, gaps
 uc5.timeline_build        ordered, precision-preserving, order-uncertain marks
 uc5.report_validation     every claim resolved to retrieved evidence; unsupported claims excluded
 uc5.deterministic_severity  rubric 1.0.0: LOW / MEDIUM / HIGH floor; the agent can raise, never lower
 uc5.human_review          review status + reasons; CRITICAL only by an analyst
 uc5.final_report          the auditable report; remediation NONE_EXECUTED
```

The agent does real investigation: the packet carries no evidence, so every fact it cites it had to
retrieve. The harness then recomputes the facts independently, so a lazy, wrong or manipulated agent
cannot change the floor (a test runs a do-nothing agent: the floor holds and the disagreement is
logged).

## 2. Evidence model

Every item is an `EvidenceItem`: `evidence_id`, `case_id`, `timestamp` + `time_precision` (minute,
hour, day, none), `source`, `source_type`, `subject` (alias), `event_type`, a fixed-vocabulary
`summary` (never raw log, document or policy text), `claim_type`, `confidence`, `provenance`
(capability, version, mode) and `related_evidence_ids`.

Ids are assigned in code **from content** (`LOG-<hash>`, `DLP-<alert>`, `UC1-DEST-<host>`,
`UC2-ANOM`, `UC2-SIG-<feature>`, `UC3-PATH-<resource>-n`, `UC3-STALE-<grant>`, `UC4-<handle>`,
`UC6-<citation>`, `APR-<ref>`, `IDN`, `COR-<rule>-n`, `GAP-<rule>-n`), so the agent's tools and the
harness give the same fact the same id.

| Claim type | Meaning | Validation |
|---|---|---|
| OBSERVED_FACT | a record: log event, DLP alert, identity, access path, approval | ≥1 cited id, all retrieved, ≥1 of this type; else relabelled as inference |
| DETERMINISTIC_FINDING | a capability or rule output: anomaly band, UC4 level, destination class, stale grant, correlation | same |
| POLICY_REQUIREMENT | a verified UC6 claim | must cite a UC6 policy item |
| AGENT_INFERENCE | the agent's reading of how evidence fits | ≥1 retrieved id |
| UNKNOWN_OR_GAP | what is not established, including absences | ids optional; never filled in |

## 3. Timeline and correlation

**Timeline** (`app/incident/correlate.py`): events sorted by authoritative time. UC2's routine log
events are hour-level; planted incident events (DLP alerts, uploads) carry minutes. Two events in the
same hour where one has no minute are marked `order_uncertain_with` each other instead of being given
an invented order. Times are never changed; day-level and timeless items are not events.

**Correlation** (`config/incident/correlation.v1.yaml`, versioned):

| Kind | Rules |
|---|---|
| finding | transfer matches alert (same host, within 60 min) · download before transfer (same day, ≤14 h) · transfer outside usual hours · sensitive file in transfer · archive before transfer · approval verified · stale grant on a case resource · data outside expected classes |
| conflict | approval cited but expired or not this subject's · case data with no active access path · policy conflict (material only for CONFIDENTIAL or higher) |
| gap | who controls the destination account · upload content not recorded · alert without a logged upload · sensitivity not established · policy evidence insufficient |

## 4. The agent

`dataguard-incident-investigator` (`config/incident/agent.v2.yaml`, `prompts/uc5/agent.v2.md`; v1
kept frozen). Model `uc4-llm-medium`; at most 14 tool calls with a graceful finish, 8 turns, 3
consecutive failures, 120 s per model call. Backends: Chat Completions, and the same agent in Foundry
Agent Service (v2, guardrail attached). Every turn is recorded for replay.

| Tool | Source | Bound by |
|---|---|---|
| `search_security_logs` | UC2 LogService (injection scanner) | subject, case window |
| `get_identity_context` | UC2 IdentityService (no HR fields) | subject |
| `get_behavior_findings` | UC2 Isolation Forest (authoritative) | subject, day |
| `get_access_context` | UC3 AccessGraph + stale grants, over the UC5 access dataset | case resources |
| `get_dlp_findings` | DLP alerts + UC1 destination catalogue | case alerts; hosts already seen |
| `classify_files` | UC4 via its MCP adapter (caller `incident-investigation-agent`) | case file handles |
| `search_policy` | UC6 via UC2's PolicyService (UC1 question template + effect mapping) | 3 topics; level and class already retrieved |
| `check_approval` | UC2 ApprovalService | references already seen |
| `build_timeline` | the timeline + correlation engine | retrieved evidence only |
| `request_human_review` | records a reason | fixed reasons |

Ten tools, not the planned twelve: timeline and correlation are one tool (both deterministic views of
the same evidence), and a separate policy-section tool is unnecessary because UC6 returns verified
claims with their text and citation. No tool can act.

**Why one agent** (decision (e) of the approved plan): the capabilities are already authoritative
services; agentising them would add latency, cost and coordination risk. UC2 showed a single agent
matched its multi-agent systems at about 40% of the model calls.

## 5. Severity and review

`config/incident/rubric.v1.yaml`, from security evidence only (no HR or employment input; UC1's
overall risk score is not used):

| Rule (first match) | Floor |
|---|---|
| HIGHLY_CONFIDENTIAL data sent to a non-approved destination (an approval never excuses it) | HIGH |
| CONFIDENTIAL data, or data of unknown sensitivity, sent to a non-approved destination | MEDIUM |
| a verified approval for this subject and date | LOW |
| sensitive data reached through a stale grant, no active path, or outside expected classes | MEDIUM |
| otherwise | LOW |

Agent effect: higher → adopted, with review; equal → agreed; one lower → ignored; two or more lower →
logged, with review; no valid report → the floor stands, with review. Review is REQUIRED for HIGH,
capability failure, material conflict, insufficient policy for sensitive data, flagged input, a
sensitive-access concern, agent raise, disagreement, failure or review request, unsupported or
withheld claims, and out-of-scope attempts. **Potential Severity 1** is flagged for HIGH +
HIGHLY_CONFIDENTIAL + personal destination + HIGH_ANOMALY + a DLP alert; only an analyst can confirm it
and set CRITICAL.

## 6. Reuse

| Capability | Reused as | Changed? |
|---|---|---|
| UC1 | destination catalogue, question template, effect mapping | no |
| UC2 | log, behaviour, identity, approval, data and policy services; the learning-loop pattern | no |
| UC3 | `AccessGraph` and `stale_grants` over `data/incident/access` | no |
| UC4 | `classify_document` through the MCP adapter | additive caller entry |
| UC6 | the copilot (verified claims, INSUFFICIENT_EVIDENCE) | corpus untouched |
| Shared | bounded agent loop, replay client, Foundry client, tracer and redactor, APF pattern | additive: per-call timeout setting, `dg.ic.*` keys, `uc5.*` span mapping |

No UC5 MCP server (decision (d)): MCP is already demonstrated by UC4, UC1 and UC2.
