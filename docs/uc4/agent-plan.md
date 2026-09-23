# UC4 Batch Triage Agent: pre-registered plan

**Status: committed BEFORE any agent code is written.** It fixes the task, the tools, the loop, the
guardrails and the eval plan, so results cannot shape them. Deviations go in the results document.

> Dataset labels: **AI-generated synthetic dataset — reviewed by one human (provenance per coordinator); second independent review pending.**

## Why this exists, and the decision it reverses

Decisions A6 and A19 scoped UC4 as a deterministic classification service, explicitly **not** an
autonomous agent, for this stage. The product owner has now asked for UC4 to also include a real
agentic component (2026-09-23), reversing that scope narrowly — recorded as decision A37. This plan
is written to do that honestly: a genuine agent with real tool use and real autonomy over a bounded
task, not a wrapper around the existing deterministic pipeline relabelled "agentic."

## Scope

**Approved and built here:** a new, separate orchestrator agent — the **Batch Triage Agent** — that
uses `classify_document` (the existing MCP tool, unchanged) as its primary tool, for a document
batch triage task described below. **The classifier itself (`ClassificationService`, the hybrid
router, fusion, review logic) is not modified in any way.** The deterministic "harness owns
routing, never the model" property that the rest of UC4 relies on stays exactly as it is.

**Not approved, not built:** giving the classifier's own LLM stage a tool-use loop (that was the
riskier of two options presented to the product owner and was not chosen); promoting the
deterministic hybrid router to an LLM-driven orchestrator (the third, explicitly-not-recommended
option); RAG or vector search (still out of scope per A5 unless separately approved); any
autonomous remediation (delete, quarantine, block, notify an external system); a record/replay
cache for agent tool-calling conversations (tests use a scripted fake client; a live run needs
`--llm-mode foundry`, real credentials, and is a separate, explicit run, exactly like the
classifier's own live evaluations).

## The task: Batch Triage

Given a batch of documents (the same pre-extracted-text input contract as UC4 itself), the agent
produces a `BatchTriageReport`: for every document, **the `classify_document` result, unchanged**,
plus the agent's own annotations — a review-priority tier, a rationale grounded in the tool's own
returned fields, and whether it requested human review. The agent orders and prioritizes; it never
re-decides sensitivity.

## Tools (a fixed allowlist — enforced structurally, not by prompt instruction)

| Tool | What it does | Why it's safe |
|---|---|---|
| `classify_document` | The existing MCP tool, called once per document | Unchanged; already deny-by-default, already tested |
| `lookup_taxonomy_definition(id)` | Read-only: the verbatim definition of a level or category from the loaded config | Cannot affect any decision; exists so the agent's rationale cites the real rule instead of inventing one (an Honest-pillar mechanism) |
| `request_human_review(document_id, reason)` | Returns a structured review recommendation | Never persists anywhere sensitive, never blocks, never remediates; constrained to documents `classify_document` itself already flagged (enforced, see Guardrails) |

Any other tool name is a schema-validation rejection at the function-calling boundary, not a prompt
instruction the model could ignore.

## Loop

One bounded loop **per document**, not one loop over the whole batch: plan → call
`classify_document` → optionally `lookup_taxonomy_definition` → optionally
`request_human_review` → emit the document's annotation. No cross-document memory. This keeps step
budgets attributable to one document, and it means one document's content can never leak into
another document's classification through shared agent context — the same per-request isolation
property `ClassificationService` already guarantees, kept true one level up.

Planner model: the Foundry `mid` tier (the same latency reasoning as the MCP adapter's cap: `large`
takes up to ~245s per call, far past a usable per-document budget in a batch).

## Guardrails, built for real this time

Unlike the classifier's HHH/APF scoping (which correctly drops behavioral-guardrail questions
because there is no tool use to bound), the agent genuinely has tools and steps, so these are real:

* **Tool allowlist**: exactly the three above.
* **Step budget**: `max_steps_per_document` (config); exceeding it stops the loop and forces
  `request_human_review` with reason `"step_budget_exceeded"` — fail-safe, matching PRD 12.4's
  "agent loop: terminate at max steps, summarize what's done, unresolved gaps."
* **The core safety invariant, enforced structurally**: *the agent's report can never assign a
  sensitivity level or category set that differs from `classify_document`'s own result for that
  document.* The report schema takes level/categories directly from the tool's return value, not
  from the agent's free text — the agent cannot express a different answer even if it wanted to,
  let alone have one accepted. This is the single most important guardrail here, and it is a
  schema-level control, not a hope about model behaviour.
* **No self-escalation**: the agent cannot grant itself a different tool, a higher `max_llm_tier`,
  or a larger budget than the operator's own config allows (reuses the MCP adapter's per-caller
  policy caps unchanged).
* **No autonomous remediation**: `request_human_review` only ever returns a recommendation.
* **Repeated tool failure → stop and escalate**: two consecutive tool errors on one document mark
  it for human review with reason `"tool_failure"`, never a silent skip.

## Evals plan

* **Tool-call accuracy**: precision/recall of `request_human_review` calls against
  `classify_document`'s own `review_required`/`high_risk` fields on the *same* document — the
  ground truth is the tool's own output, not an external judge, so this is directly computable.
* **Task completion**: every input document appears in the final report. Checked, not just
  measured — a missing document is a hard failure, not a metric that can drift.
* **Safety-invariant compliance**: the never-downgrade property, checked on every document. This
  is enforced structurally (see Guardrails), so the eval's job is to prove the enforcement can't be
  bypassed (adversarial tests: force the agent to try to state a different level/category and
  confirm the schema rejects it), not just to observe a rate.
* **HHH, real, not scoped away here** (the agent has real tool use, unlike the classifier):
  Helpful = task completion rate; Honest = the rationale's claims checked for grounding against the
  tool's own evidence (reuses the existing evidence-verification substring check, or the Azure
  Content Safety Groundedness second opinion); Harmless = zero autonomous actions taken beyond
  recommendations — expected to be 1.0 by construction, stated as such rather than oversold as a
  measured finding.
* **APF, real**: Effectiveness = task completion + tool-call accuracy; Efficiency = tool calls and
  latency per document against a budget; Reliability = repeat-run determinism on the same batch;
  Trustworthiness = safety-invariant compliance + Honest score.

## Expectations stated in advance

* The agent will very likely call `classify_document` exactly once per document (there is no
  reason to call it twice) and `request_human_review` only for the subset `classify_document`
  already flags — so tool-call accuracy should be high **by construction**. The interesting
  question is not "did the agent get it right" but "does the safety invariant hold when the model
  tries something creative," which is why an adversarial test that tries to *make* the agent state
  a wrong level matters more here than an accuracy percentage.
* `lookup_taxonomy_definition` will be called inconsistently — it helps the Honest rationale, it is
  not load-bearing for correctness, and a low call rate is not itself a finding.

## Limits stated in advance

Synthetic dataset; no live agent run has been made yet (needs Foundry credentials — a separate,
explicit step, like the classifier's own live evaluations); the safety invariant is enforced
structurally, but the LLM's *text* rationale is fact-checked only by a grounding pass, not a full
semantic audit; single-document loops only, no batch-level cross-referencing; this is a new,
unreviewed component and is not covered by the earlier human gold-label review process (which
reviewed dataset labels, not this agent's behaviour).
