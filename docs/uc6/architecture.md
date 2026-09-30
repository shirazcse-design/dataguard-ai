# UC6 architecture

## The pipeline

```
question
  → 1 input guard           size limit; UC6 injection lexicon (blocks)             app/policy/guard.py
  → 2 retrieval             query expansion → BM25 + embeddings → draft filter
                            → Reciprocal Rank Fusion → deterministic rerank          app/policy/retrieval.py
  → 3 evidence scan         chunks with instruction-like text withheld              guard.py / pipeline.py
  → 4 evidence gate         no model call if the best reranked score < 0.35         pipeline.py
  → 5 generation            uc4-llm-medium, strict JSON, evidence labelled E1..En    app/policy/generate.py
  → 6 citation verification label sent? quote verbatim? numbers present?           app/policy/verify.py
  → 7 conflict check        superseded version → metadata; cross-policy → review    verify.py
  → 8 outcome               ANSWERED | INSUFFICIENT_EVIDENCE | CONFLICT_REVIEW
                            | BLOCKED | UNAVAILABLE, plus review reasons             app/policy/schemas.py
```

`PolicyAnswer.stages` lists exactly the stages that ran. A short-circuit ends the list, so the
decision trace never shows a stage that did not execute.

## Three RAG levels, one set of components

The levels are switches in `config/policy/policy.v1.yaml`, not three systems.

| Switch | Naive | Advanced | Agentic |
|---|---|---|---|
| query processing (configured expansions) | off | on | on (inside `search_policy`) |
| retrieval | embeddings only | hybrid BM25 + embeddings | hybrid, via tools |
| draft (metadata) filter | off | on | on |
| deterministic rerank | off | on | on |
| input guard (injection) | off | on | on |
| evidence scan | off | on | on (per tool result) |
| evidence gate | off | on | - (the agent decides) |
| citation verification | measured, **not enforced** | enforced | enforced |

Naive is the experimental baseline. It shows what the harness adds, so its unverified claims are
displayed (marked) instead of dropped.

## Components

| Component | File | Notes |
|---|---|---|
| Corpus + chunking | `app/policy/corpus.py` | Front-matter metadata (policy_id, title, version, effective_date, status, supersedes, owner, source). One chunk per numbered section (the citation unit); long sections split at 180 words with 30 words of overlap; a contextual header is prepended for indexing. Ids like `POL-DLP@2.1#4.2`. |
| Sparse index | `app/policy/sparse.py` | Okapi BM25 (k1 1.2, b 0.75) with a light stemmer |
| Embeddings | `app/policy/embeddings.py` | `FoundryEmbeddingClient` (subclasses UC4's `FoundryClient`); `CachedEmbedder` record/replay (hashes + vectors only, never text); `HashingEmbedder` for tests, always labelled `offline-hash` |
| Vector store | `app/policy/store.py` | A `VectorStore` interface plus exact cosine search over 74 vectors. Swappable (e.g. Azure AI Search) without touching the retriever. |
| Query processing | `app/policy/query.py` | Appends configured synonyms and acronyms; never drops the user's words |
| Rerank | `app/policy/rerank.py` | 0.5·term coverage + 0.2·heading coverage + 0.3·normalised fused score − 0.15 if superseded. Every component is shown in the trace. |
| Generation | `app/policy/generate.py` | UC4 `LLMClient` stack (Foundry, retry, record/replay); prompt `prompts/uc6/answer.v2.md` |
| Verification + conflicts | `app/policy/verify.py` | Harness-side only; the model never decides the final status |
| Pipeline | `app/policy/pipeline.py` | `PolicyCopilot.answer()`; `decide()` is shared with the agent |
| Agent | `app/policy/agent.py` | Bounded loop, 4 tools, record/replay planner, offline planner |
| Service / modes | `app/policy/service.py` | `replay` · `offline` · `live` · `record`; agent backend `chat-completions` or `foundry-service` |
| Evaluation | `evals/policy/` | Golden set, retrieval / answer / agent metrics, reports, Foundry export and runs, guardrail verification |
| Dashboard | `app/demo/policy.py`, `app/demo/static/policy.js` | One page plus panels on the shared pages |

## The agent (Agentic RAG), kept deliberately small

```
question → input guard → [planner turn → allow-listed tool call(s)]* → final JSON → decide()
tools: search_policy(query) · get_policy_section(policy_id, section)
       lookup_policy_metadata(policy_id) · request_human_review(reason ∈ fixed list)
```

The agent decides **whether and what to retrieve**: it can refine a search, read an exact
section, check versions, or ask for review. It does **not** decide what counts as an answer.
Controls enforced in code:

* only the 4 tools run; any other name is `unknown_tool`, and there are no write tools;
* arguments are validated (types, lengths, id and section patterns, review-reason enum);
* at most 6 tool calls and 8 planner turns; 2 consecutive tool failures stop the run. A stopped
  run returns no answer and a review;
* evidence ids are assigned by the harness to text the tools returned, so a claim citing anything
  else is dropped as fabricated;
* tool results containing injection text are withheld, and the agent sees only that an item was
  excluded;
* the agent can add a review but never remove one.

Planners: recorded turns (replay), UC4's chat-completions `FoundryAgentClient` (live), or
**`dataguard-policy-copilot` v5 in Foundry Agent Service** (`--agent-backend foundry-service`,
UC4's `FoundryAgentServiceClient`, Entra sign-in). With Foundry, the agent's instructions and
tool **definitions** live in Foundry, while the tools still execute in the application.

## Structural controls (why the model cannot talk its way past them)

| Risk | Control | Where |
|---|---|---|
| Invented citation | The model sees `E1..En` labels only; the harness maps labels to sections; unknown label → dropped | generate.py, verify.py |
| Misquoted policy | The quote must appear verbatim (whitespace-collapsed) in the cited section | verify.py (UC4 `verify_quote`) |
| Wrong number ("10 years") | Every number in the claim must appear in the cited section | verify.py |
| Free-text hallucination | No free-text summary field: the shown answer is the verified claims only | generate.py schema |
| Answering from memory | No verified claim → INSUFFICIENT_EVIDENCE | pipeline.decide |
| Older policy version | Claims citing a superseded version are removed when the current version is present | verify.version_conflicts |
| Silent conflict resolution | Cross-policy conflict → CONFLICT_REVIEW, neither side preferred | verify.model_reported_conflict |
| Injected instructions in documents | Unapproved drafts filtered; flagged chunks withheld; evidence delimited as data | retrieval, guard |

## Shared platform code reused (UC4 unchanged)

`app/llm` (Foundry client, retry, replay cache), `app/agent` (turn types, chat-completions and
Agent Service clients), `guardrails/injection.py` (scanner class; UC6 has its own pattern file),
`guardrails/output.py` (`verify_quote`), `observability/` (tracer, deny-by-default redactor,
audit, Azure Monitor sink), `evals/classification/foundry_evals.py` (`run_in_foundry`), and the
demo server. Changes to shared files are additive only: allow-list keys, GenAI span mapping,
routes and a nav link.

## Observability

Spans: `uc6.request` > `uc6.input_guard`, `uc6.retrieve`, `uc6.generate`, `uc6.citation_verify`;
agentic `uc6.agent` > `uc6.agent.planner`, `uc6.tool`. Attributes are ids, counts, statuses, fixed
codes and `POL-XXX:4.2` section ids only. They export to Application Insights with GenAI semantics
(`chat` / `invoke_agent` / `execute_tool`). Replayed calls are never labelled as model calls.

## How UC6 becomes the shared Policy Intelligence layer

The contract is `PolicyCopilot.answer(question, level) → PolicyAnswer` (status, verified claims,
citations, evidence metadata, conflicts, review reasons), plus the `search_policy` tool. Planned
consumers (not built in UC6):

| Consumer | Use |
|---|---|
| UC1 Agentic DLP | "Which policy does this upload violate?" → cited clause for the Allow/Warn/Escalate rationale; INSUFFICIENT_EVIDENCE → escalate |
| UC2 Insider Risk | Policy grounding for each anomalous step in a case timeline |
| UC3 Access Governance | The access-control standard's rule (e.g. production DB ≤ 90 days) as the cited basis for "time-bound access" |
| UC5 Incident Investigation | Reporting deadlines and breach-notification clauses in the incident report |

Next step for reuse: expose `search_policy` and `answer_policy_question` through the MCP adapter
pattern UC4 already uses for `classify_document`.
