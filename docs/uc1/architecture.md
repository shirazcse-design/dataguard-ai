# UC1 architecture

## 1. The problem

Classic DLP fires on patterns: an SSN, a card number, a "Confidential" label. Two things go wrong:

* **Missed exposure.** The most damaging files often carry no pattern and no label. A spreadsheet
  of acquisition targets is sensitive because of what it *means*.
* **Alert fatigue.** Analysts get pattern hits without context: who the user is, whether this is
  normal for them, what policy actually says, and whether an approved exception exists. Each alert
  becomes a manual investigation across five tools.

An LLM can help with both, but an LLM that *decides* (or worse, *acts*) on data-loss events is
hard to trust, hard to audit, and open to prompt injection from the very content it inspects.

**UC1's answer:**
* use models where they add judgement: semantic classification (UC4), policy interpretation (UC6)
  and gap-filling investigation (the agent);
* keep the decision and every action in deterministic, versioned, auditable code.

## 2. Personas

| Persona | Needs | What UC1 gives them |
|---|---|---|
| **DLP / SOC analyst** | Fewer, better alerts; a case they can act on in minutes | One investigation per event: outcome, score breakdown, cited policy, agent findings with evidence ids, a proposed (simulated) action to approve |
| **Data-protection lead** | Consistent decisions they can defend to auditors | A versioned rubric; every point is a reason code; floors no model can lower |
| **Policy owner** | To know where policy is unclear | Policy conflicts and missing evidence go to human review with the reason |
| **Employee** | Not to be blocked for legitimate work | Approved corporate destinations carry no exposure points; a verified exception lowers risk; WARN coaches instead of blocking |
| **Platform / AI engineer** | Reuse, not another silo | UC4 and UC6 consumed through their existing interfaces; one MCP server; one tracer |

## 3. Architecture

```
DLP event (user, action, destination, file ref, optional justification)
   │
   ├─ 1. Prechecks (deterministic) ── destination class, known-to-user, label, UC4 rules-engine patterns
   ├─ 2. UC4 classification ───────── ClassifyDocumentAdapter (caller dlp-investigation-agent, mid cap)
   │                                   level, categories, confidence, injection flag. Document text stays here.
   ├─ 3. Identity ─────────────────── role, employment type/status, privilege (synthetic directory)
   ├─ 4. Behaviour ────────────────── deterministic bands from 7-day features (UC2 replaces this seam)
   ├─ 5. UC6 policy intelligence ──── templated question → PolicyCopilot (Advanced) → VERIFIED citations
   │                                   → mapping.v1.yaml turns cited sections into an effect
   ├─ 6. Justification guard ──────── UC6's scanner; instruction-like text is WITHHELD from the agent
   ├─ 7. Investigation agent ──────── evidence pack (ids, facts, no document text) + 5 read-only tools
   │                                   → proposed outcome + findings that cite evidence ids
   └─ 8. Risk & response harness ──── rubric 1.1.0: points → band → floors → review triggers
                                       → ALLOW / WARN / ESCALATE / HUMAN_REVIEW
                                       + reason codes + SIMULATED action needing analyst approval
```

Each stage is a span (`uc1.prechecks` … `uc1.hitl_decision`). UC4's and UC6's own spans nest
inside, so one trace shows the whole investigation.

**Failure behaviour.** A stage that fails is recorded, not hidden, and the harness turns it into a
`review:stage_failure` (or `classification_uncertain`) trigger. Missing data is never treated as
safe:
* if sensitivity is unknown, the policy question is not asked, and the case goes to HUMAN_REVIEW;
* the agent's absence or failure can never produce an ALLOW on a case the rubric scores at WARN or
  above.

## 4. The UC4→UC6 integration contract

UC4 and UC6 were built independently and use different vocabularies. UC4 says
`HIGHLY_CONFIDENTIAL`; the policy corpus says *Restricted*. UC4 has categories; policies talk about
"merger and acquisition plans". The approved decision was to join them through an **explicit,
versioned table**, `config/dlp/mapping.v1.yaml` (1.0.0), and not to rename either taxonomy or let
the LLM infer it:

* **levels**: rank and policy term (`HIGHLY_CONFIDENTIAL → Restricted`);
* **categories**: the phrase used in the policy question, and the minimum level the classification
  standard assigns. The effective level is the maximum of UC4's level, the existing label and the
  category minimums;
* **destination phrases**: how each destination class reads in the policy question;
* **policy effects**: for each policy section, the destinations and level range it governs and
  its effect (`prohibited`, `requires_approval` or `allowed`). An effect applies **only** when UC6
  returned that section as a **verified** citation for this event's own question. When verified
  effects disagree (e.g. AUP §5.3 allows Internal data in personal cloud while DLP §4.2 prohibits
  it), the result is a conflict, which means human review.

A unit test checks that every mapped section exists in the UC6 corpus. This is documented as a
**platform consideration**: a shared taxonomy service would remove the table. Until then, it is
the single place the two vocabularies meet.

## 5. Reuse of UC4 and UC6 (nothing rebuilt)

| Need | Reused as-is | How UC1 calls it |
|---|---|---|
| Classification | UC4 `ClassificationService` via `ClassifyDocumentAdapter` | New caller `dlp-investigation-agent` in `mcp.v1.yaml` (additive): mid tier max, no evidence excerpts |
| Pattern prechecks | UC4 `RulesEngine.analyze` | same rules, same masking |
| Policy answers | UC6 `build_copilot` → `PolicyCopilot.answer(question, "advanced")` | the templated question from `dlp.v1.yaml` |
| Agent policy tools | UC6 `PolicyTools` (search, section lookup, evidence labelling, withheld injected chunks) | wrapped as `search_policy` / `get_policy_section` |
| Injection scanning | UC6 `load_policy_scanner` | on the user's justification |
| Planners | UC6 `ReplayAgentClient`; UC4 `FoundryAgentClient`, `FoundryAgentServiceClient` | `uc1-agent.v2` prompt, own cache namespace |
| Tracing and privacy | shared `observability` tracer, allow-list, GenAI mapping, `audit_spans` | `dg.dlp.*` keys added; `uc1.*` spans mapped |
| MCP | UC4's `mcp_adapter/server.py` | extended with an opt-in tool registry |
| Dashboard | UC4/UC6 demo server and helpers | one new page plus panels on the shared pages |

No UC4 or UC6 result, taxonomy, prompt or golden set was changed. Two shared files received
additive changes:
* `mcp.v1.yaml`: one new caller;
* `observability/sinks.py`: the UC1 span mapping and a sampler fix that also benefits UC4 and UC6
  (see [`foundry-observability-setup.md`](foundry-observability-setup.md)).

## 6. Run modes

| Mode | UC4 | UC6 | Agent |
|---|---|---|---|
| `replay` (default; demo; CI) | recorded | recorded answers and embeddings | recorded turns (`uc1-agent.v2` or `uc1-agent-service.v2`) |
| `offline` | recorded | offline stand-in | `offline-planner` (deterministic, not a model; labelled) |
| `live` | Foundry | Foundry | `uc4-llm-medium` chat-completions, or the Foundry agent |
| `record` | live, and writes the caches | same | same |

A replay miss is reported, never substituted. UC4 returns `review_required`, UC6 returns
`UNAVAILABLE`, and the agent returns `planner_error:replay_miss`. The harness routes each of these
safely.
