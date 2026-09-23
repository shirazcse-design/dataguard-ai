# UC4 Batch Triage Agent

> Dataset labels: **AI-generated synthetic dataset — reviewed by one human (provenance per coordinator); second independent review pending.**
> Plan (pre-registered before any code): [`agent-plan.md`](agent-plan.md). Decision: A37
> (`decisions.md`). Every number here comes from a real run of `dataguard-uc4 agent triage`/`agent
> eval`, `--agent-mode mock`, dev split, `--llm-mode replay` (the same recorded LLM cache used by
> `hybrid-engine.md`) — reproduce with the commands in **Reproduce** below.

## Status in one paragraph

The Batch Triage Agent exists, is tested (68 unit tests, including adversarial tests that try to
make it state a wrong sensitivity level), and has been run end to end over the real dev split using
a deterministic offline planner (`--agent-mode mock`; no Foundry credentials used or needed for this
report — see **Limits**). It calls the real, unmodified `classify_document` once per document,
copies the result verbatim, and adds a priority tier and an (optional) review recommendation. The
core claim — **the agent cannot express a sensitivity decision that differs from
`classify_document`'s own result** — is enforced structurally (schema-level) and is proven by
adversarial unit tests, not just observed as a rate on this run.

## Architecture

```
Batch                           One independent loop per document (no cross-document memory)
 │
 ├─ doc 1 ──► plan → classify_document → [lookup_taxonomy_definition] → [request_human_review] → annotate
 ├─ doc 2 ──► plan → classify_document → [lookup_taxonomy_definition] → [request_human_review] → annotate
 └─ doc N ──► ...

classify_document  = the SAME ClassificationService (hybrid router, fusion, review) - unchanged
lookup_taxonomy_definition = read-only, cannot affect any decision
request_human_review = returns a recommendation only, never blocks/remediates/persists
```

| Piece | File |
|---|---|
| Schemas (the safety invariant lives here) | `app/agent/schemas.py` |
| Config | `app/agent/config.py`, `config/agent/agent.v1.yaml` |
| The fixed tool allowlist | `app/agent/tools.py` |
| The per-document loop (guardrails enforced here) | `app/agent/loop.py` |
| The batch runner | `app/agent/batch.py` |
| Live planner (Foundry, tool-calling) | `app/agent/foundry_agent.py` |
| Deterministic offline planner (`--agent-mode mock`) | `app/agent/offline_policy.py` |
| Evals | `evals/classification/agent_eval.py` |
| CLI | `dataguard-uc4 agent triage` / `agent eval` |

Rules enforced by the design: one bounded loop per document, no shared context between documents;
any tool name outside the fixed allowlist is a schema-validation rejection, not a prompt
instruction; exceeding the step budget or two consecutive tool failures stops the document and
forces a review recommendation, never a silent drop; `level`/`categories`/`high_risk`/`status` on
the agent's output are copied only from `classify_document`'s own return value.

## Results (dev split, 107 documents, `--agent-mode mock`, `--llm-mode replay`)

| metric | value |
|---|---|
| task completion | 107 / 107 (1.0) |
| documents `classify_document` decided (`status: ok`) | 107 / 107 |
| high-risk documents | 58 |
| priority `high` / `low` | 58 / 49 |
| avg. tool calls / document | 1.0 (`classify_document` only; the offline policy never called `lookup_taxonomy_definition` or `request_human_review` on this run — see **Findings**) |
| repeat-run determinism | confirmed: two runs produced byte-identical reports |

Scoped HHH / APF (`dataguard-uc4 agent eval`):

| HHH | value | | APF | value |
|---|---|---|---|---|
| Helpful (task completion) | 1.0 | | Effectiveness | 0.5 |
| Honest | not computed (needs a grounding pass — see Limits) | | Efficiency | 1.0 |
| Harmless (autonomous actions taken) | 1.0 (0 by construction) | | Reliability | not computed (needs a formal repeat-run score — see Limits; empirically confirmed identical above) |
| | | | Trustworthiness | 1.0 |
| | | | **Composite** (mean of the 3 present dimensions) | **0.833** |

Safety-invariant compliance: reported as **1.0, stated as structural, not measured** — it comes
from the adversarial tests in `tests/unit/test_agent_loop.py` (try to skip `classify_document`,
smuggle a `level` argument into the tool call, and give a malformed final answer; every one is
refused or ignored by construction), not from counting rows in this run.

## Findings

1. **On dev, every document is decisively classified (`status: ok`)** — the same recorded LLM
   responses `hybrid-engine.md` reports a 0.000 review rate for. The agent adds nothing to the
   classification here; its whole contribution on this run is priority tiering (`high` for the 58
   high-risk documents, `low` for the rest) and zero review recommendations.
2. **APF Effectiveness is 0.5, not 1.0, and this is a real, deliberate finding, not a bug.** The
   plan's ground truth for tool-call accuracy is `classify_document`'s `review_required` **or**
   `high_risk` field. `offline_policy` only calls `request_human_review` when `status ==
   "review_required"` — it treats a document that was *decisively* classified high-risk as not
   needing extra escalation, since `classify_document` already returned a confident, actionable
   result. Under the plan's broader ground truth, that is 58 false negatives (recall 0.0 on the
   high-risk-but-decided subset), which is why Effectiveness (completion 1.0 folded with recall 0.0)
   comes out at 0.5. This is a genuine gap between the plan's stated ground truth and the shipped
   policy's actual behaviour, not scored around.
3. **A real bug was caught and fixed before this report was written**: the first version of
   `offline_policy` omitted `filename` from its `classify_document` tool call, defaulting it to
   `"document.txt"`. Under `--llm-mode replay` that changes the classification prompt and therefore
   the cache key, so every document missed the replay cache and came back `review_required` — a
   cache artifact that looked exactly like "the agent found something," not a real result. This is
   the same class of bug the Fairness probe hit earlier in this project (`decisions.md` D9.23's
   sibling finding). Fixed in `offline_policy.py`; regression-tested in
   `tests/unit/test_agent_batch.py`.
4. **`lookup_taxonomy_definition` was never called on this run.** Expected and stated in advance in
   the plan: it helps the Honest rationale, it is not load-bearing for correctness, and the
   deterministic offline policy has no rationale-quality reason to call it. A live Foundry planner
   may call it more; that is a separate, not-yet-run measurement (see Limits).

## Clarifications to the pre-registered plan

* The plan describes `request_human_review` as "constrained to documents `classify_document` itself
  already flagged." That constraint is **not enforced in code** — nothing in `loop.py` prevents the
  agent from calling `request_human_review` on any document. It is enforced only by `offline_policy`
  choosing not to call it otherwise. A live model is not bound by this restriction either, since it
  is a guardrail idea from the plan that did not make it into `loop.py`'s actual enforcement; the OR
  logic in `_finalize` means an over-eager real call would only ever *add* a review request, never
  hide one, so this gap is not a safety hole, but it means "the model cannot request review for a
  low-risk document" is aspirational prose, not a built control.
* Honest and Reliability are reported as `None` here (not computed), consistent with the plan's
  stated limits, and are not folded into the APF composite (see `evals/classification/agent_eval.py`
  docstrings) — the composite above averages Effectiveness, Efficiency and Trustworthiness only.

## What was NOT done, and why

| Item | State |
|---|---|
| A live Foundry planner run (`--agent-mode foundry`) at scale | **Smoke-tested only** (2026-09-23): 3 dev-split documents against the real `uc4-llm-medium` deployment, live. The tool-calling round trip, JSON final-answer parsing, and the safety invariant all held on real output (one document had `classify_document` itself return `review_required`; the agent's `level` correctly stayed `null` and it called `request_human_review` rather than inventing a decision). Rationale text was visibly richer than the offline planner's canned string. Not scaled to a full-split run or written up as a report - see D9.29. |
| Honest (grounding) score | Not computed; needs a pass over the rationale text (reusing the evidence-substring check or the Content Safety Groundedness second opinion), not yet wired to the agent's own output. |
| Formal Reliability score | Not computed as a number; two identical runs were confirmed byte-for-byte identical on this deterministic offline policy (a live model would need a real repeat-run comparison). |
| A run over the full development set (train+calibration+dev) | Not done for this report — train/calibration have no recorded LLM responses (same limit `hybrid-engine.md` states), so a replay run there would be dominated by cache misses, not a real agent measurement. |
| Enforcing "review requests only for tool-flagged documents" in code | Not built (see Clarifications) — currently a guardrail idea in the plan, not a structural control, unlike the safety invariant itself. |

## Reproduce

```
dataguard-uc4 agent triage --split dev --llm-mode replay --out docs/uc4/results/agent-triage-dev.json
dataguard-uc4 agent eval --split dev --llm-mode replay --out docs/uc4/results/agent-eval-dev.json
```

## Limits

Synthetic, AI-authored dataset, not yet human reviewed; single dev split, single recorded LLM
sample per document (no live model variance); the deterministic offline planner is explicitly not a
claim of model intelligence — it exists so the loop, batch runner and CLI can be exercised without
credentials; the live Foundry planner has only been smoke-tested on 3 documents (D9.29), not run at
scale or written up as a report; the safety invariant is enforced structurally and proven by
adversarial unit tests, not measured as a live-output rate; this is a new, unreviewed component and
is not covered by the earlier human gold-label review process (which reviewed dataset labels, not
agent behaviour).
