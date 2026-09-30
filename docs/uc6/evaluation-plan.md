# UC6 evaluation plan

Evaluation came **before** optimisation. The golden set was written before any retrieval code
existed, the retrieval settings were fixed before the first run, and the owner chose to report
them as measured instead of tuning them on the same questions (decision "Option A").

## 1. Golden set: `evals/policy/dataset/golden.v1.jsonl`

36 questions: straightforward 8, multi-policy 6, paraphrase 6, ambiguous 3, insufficient 6,
conflict 3, adversarial 4. 28 have expected evidence.

| Field | Meaning |
|---|---|
| `expected_sections` | Section keys (`POL-DLP@2.1#4.2`) that answer the question: the retrieval relevance labels |
| `expected_policy_ids` | Derived from the sections (checked by the loader) |
| `expected_answer_points` | Groups of acceptable phrasings, for a heuristic coverage check |
| `forbidden_answer_points` | Phrases that must not appear (e.g. following an injected instruction) |
| `answerable` | Has expected evidence |
| `expected_status` / `acceptable_statuses` | e.g. X01: BLOCKED preferred, ANSWERED also acceptable |
| `conflict_sections` | Sections that must be surfaced for a conflict (e.g. the superseded retention v1.0) |
| `risk_type` | e.g. `near_miss_retention`, `prompt_injection_corpus` |

Design choices: near-miss insufficient questions (CCTV retention, encryption at rest, personal
VPN) that retrieve plausible but non-answering text; paraphrases with low word overlap (Dropbox,
ChatGPT, personal GitHub); one conflict resolvable from metadata (retention v1.0 vs v2.0) and one
that is not (Acceptable Use §5.3 vs DLP §4.2 on **Internal** data); an unapproved draft containing
an injected instruction.

**Limitations:** 36 items and one author (who also wrote the corpus). There is no held-out split;
the evidence-gate threshold and prompt v2 were chosen after seeing golden-set results, and both
are labelled post-hoc. Gold labels are never changed from results or reviewer actions.

## 2. What is measured, and how

Every metric is labelled **deterministic** (exact comparison with gold or with the evidence
sent), **heuristic** (substring checks, transparent but fallible) or **judge** (an LLM or human).

### Retrieval: separate from generation (`dataguard-policy eval retrieval`)

| Metric | Kind | Why |
|---|---|---|
| Recall@3/5/10 | deterministic | Did the right sections reach the model? The ceiling on answer quality |
| Precision@K | deterministic | Noise sent to the model (bounded by the number of relevant sections) |
| Hit@5 | deterministic | The PRD's "retrieval hit rate" |
| MRR | deterministic | Is the best evidence ranked first? |
| Draft in top-k | deterministic | Exposure to unapproved and poisoned content |
| Conflict surfaced | deterministic | Are both sides of a conflict retrieved, so a conflict can be shown? |

Variants form an ablation ladder: naive (dense) → sparse (BM25) → hybrid (RRF) → hybrid + query
processing → advanced (+ draft filter + rerank). Relevance is per section, so a split section
counts once.

### Answers (`dataguard-policy eval answers`)

| Metric | Kind |
|---|---|
| Status accuracy (status in `acceptable_statuses`) | deterministic |
| Insufficient-evidence accuracy (the 6 out-of-corpus questions) / false "insufficient" on answerable ones | deterministic |
| Unresolved conflict sent to review (C01) | deterministic |
| Prompt-injection resistance (acceptable status and no forbidden phrase) | deterministic + heuristic |
| Fabricated-citation rate (claims citing an id that was never sent) | deterministic |
| Unverified-claim rate (quote or number not in the cited section): a **proxy** for groundedness | deterministic |
| Unsupported-answer rate (answers showing an unverified claim) | deterministic |
| Superseded-citation rate | deterministic |
| Citation precision / recall vs expected sections | deterministic |
| Answer-point coverage / forbidden-phrase rate | heuristic |
| Groundedness, relevance, retrieval quality | **judge**: Foundry evaluators (section 3) |

### Agent (from the agent trace)

Task completion, retrieval tool used, average tool calls, unnecessary calls (repeats + failures),
budget compliance, safe termination. All deterministic. Budget and termination are enforced, so
they are reported as evidence rather than as open questions.

### Safety

Covered above (injection resistance, fabricated citations, unsupported answers), plus the live
guardrail verification G1-G8 across four paths (`dataguard-policy guardrails verify`) and the
telemetry privacy audit (`dataguard-policy obs report`).

## 3. Foundry Evaluations (the judge layer)

* **Agreement:** four string-check graders over 108 outcome rows must reproduce the local counts
  exactly (this was met: 101 / 108 / 108 / 108).
* **Quality:** built-in Groundedness (query, response, context), Relevance and Retrieval over the
  76 answered rows, judge `uc4-llm-medium`. Rows that pass the deterministic check but score
  groundedness < 4 are flagged for human review: they catch sentences that quote correctly but
  over-state the quote.

Not used: Coherence/Fluency (not risks here), BLEU/ROUGE/Similarity (no written reference
answers), portal agent evaluation (the portal cannot execute the application-side tools).

## 4. Reproducibility

Everything replays offline from committed recordings: embeddings
(`data/embedding_cache/uc6-embed-small`), answers (`data/llm_cache/uc4-llm-medium/uc6-answer.v1`,
`uc6-answer.v2`), agent turns (`uc6-agent.v1`, and `dataguard-policy-copilot/uc6-agent-service.v1`).
Every report records the golden-set hash, the corpus fingerprint, the config hash and the prompt
version. Replay keys hash the full model input, so changing the corpus, the retrieval or the
prompt is a replay miss (reported as such), never a silently different answer.
