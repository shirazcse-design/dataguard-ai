# Manual setup: Foundry Evaluations for the Policy Copilot (UC6)

**Owner: Shiraz (manual, in the Foundry portal).** Claude Code does not create evaluations,
upload data or run evaluators in Foundry. It produced the dataset files below from a REPLAY run
and will compare your Foundry results with the local ones. **No Foundry result is claimed anywhere
until you run it.**

## 1. What Foundry adds, and what stays local

The local harness ([`results/answers.uc6-answer.v2.md`](results/answers.uc6-answer.v2.md),
[`results/retrieval.md`](results/retrieval.md)) measures everything that can be measured
**deterministically**: statuses, citations, fabricated citations, insufficient-evidence accuracy,
Recall@K and the agent metrics. It cannot judge **semantics**: does each sentence actually follow
from its quote, and does the answer address the question? Those need a judge model. That is what
Foundry Evaluations is for here.

| Metric | Where | Why |
|---|---|---|
| **Groundedness** (claims supported by the context) | **Foundry**, AI-assisted evaluator | The semantic judgment the local quote/number check only approximates |
| **Relevance** (answer addresses the query) | **Foundry** | Semantic |
| **Retrieval** (context useful for the query) | **Foundry** | A judged complement to the local Recall@K |
| **Response completeness** (vs ground truth) | **Foundry**, optional | `ground_truth` is a rough list of expected points, so treat it as indicative only |
| Status / forbidden phrase / unverified shown / fabricated citation | **Foundry string-check graders** on `outcomes.jsonl` | Must reproduce the local numbers **exactly**: a check that the portal and the harness agree |
| Recall@K, Precision@K, MRR | **Local only** | Needs the ranked list and relevance labels, not a judge |
| Citation precision/recall, superseded citations | **Local only** | Deterministic against metadata |
| Agent metrics (tool selection, budget, safe termination) | **Local only** | The agent's tools run in the application. A portal agent evaluation cannot execute them, so it would stall at the first function call. |
| Coherence, Fluency, BLEU/ROUGE/F1, Similarity | **Not used** | Not risks for this product, or need written reference answers the golden set does not have |
| Risk & safety evaluators (hate, violence, ...) | Optional | Answers are policy text; the guardrails verification covers harmful input |

## 2. The dataset files (already generated, local)

Regenerate at any time with `dataguard-policy eval foundry-export` (REPLAY, no Azure). All content
is synthetic (the Harbourline corpus and the golden questions), so it is safe to upload.

| File | Rows | Use |
|---|---|---|
| `data/uc6/foundry_eval/answers.jsonl` | 76 (every ANSWERED run: naive 25, advanced 25, agentic 26) | AI-assisted evaluators |
| `data/uc6/foundry_eval/outcomes.jsonl` | 108 (36 questions x 3 levels) | string-check graders |

`answers.jsonl` columns: `id` (`S01-advanced`), `item_id`, `level`, `category`, **`query`**,
**`response`** (the verified claims with their citations), **`context`** (the text of the evidence
the answer could cite, withheld injected chunks excluded), **`ground_truth`** (expected answer
points), and local results for joining: `local_citation_precision`, `local_point_coverage`,
`local_all_claims_verified`.

`outcomes.jsonl` columns: `id`, `item_id`, `level`, `category`, `mode`, `expected_status`,
`actual_status`, and precomputed `status_ok`, `no_forbidden_phrase`, `no_unverified_shown`,
`no_fabricated_citation` (each `pass`/`fail`).

## 3. Evaluation A: AI-assisted quality on `answers.jsonl`

1. <https://ai.azure.com> (New Foundry) → project **dataguard** → **Observe → Evaluation**
   (classic: **Evaluation**) → **+ New evaluation / Create**.
2. Choose **Evaluate a dataset** (not "evaluate a model" or "evaluate an agent": the responses
   already exist).
3. **Upload** `data/uc6/foundry_eval/answers.jsonl` and name the dataset `uc6-answers-v2`.
4. **Judge model:** `uc4-llm-medium`. If the evaluator rejects a reasoning deployment, use
   `uc4-llm-small`, and note which one you used; judge choice changes scores.
5. **Evaluators and column mapping:**

| Evaluator | query | response | context | ground_truth | Suggested pass threshold |
|---|---|---|---|---|---|
| Groundedness | `query` | `response` | `context` | - | >= 4 of 5 |
| Relevance | `query` | `response` | - | - | >= 4 of 5 |
| Retrieval | `query` | - | `context` | - | >= 3 of 5 |
| Response completeness (optional) | - | `response` | - | `ground_truth` | >= 3 of 5 (indicative) |

   In the new portal the mapping is written as `{{item.query}}`, `{{item.response}}`,
   `{{item.context}}` and `{{item.ground_truth}}`.
6. Name the run `uc6-answers-v2-quality` and submit. UC4's runs took about 10 minutes.

## 4. Evaluation B: deterministic agreement on `outcomes.jsonl`

1. **+ New evaluation → Evaluate a dataset**, upload `outcomes.jsonl`, and name it `uc6-outcomes-v2`.
2. Add four **String check** graders (sometimes listed under custom evaluators or graders), each
   comparing a column to the literal `pass` with operation **equals**:
   `{{item.status_ok}}`, `{{item.no_forbidden_phrase}}`, `{{item.no_unverified_shown}}` and
   `{{item.no_fabricated_citation}}`.
3. Name the run `uc6-outcomes-v2-agreement` and submit.
4. **Expected pass counts. Foundry must match these exactly.** They were printed when the files
   were generated:

| Grader | naive (36) | advanced (36) | agentic (36) | total (108) |
|---|---|---|---|---|
| status_ok | 32 | 35 | 34 | 101 |
| no_forbidden_phrase | 36 | 36 | 36 | 108 |
| no_unverified_shown | 36 | 36 | 36 | 108 |
| no_fabricated_citation | 36 | 36 | 36 | 108 |

A mismatch means the upload or the mapping differs from the local files. Filtering the portal
results by `level` gives the per-level counts.

## 5. Comparing Foundry with local results

After both runs complete, download each run's results (**Download results / Export** as
CSV/JSONL), or send screenshots of the metric summary and the per-row table. Claude Code will then:

1. confirm Evaluation B reproduces 101 / 108 / 108 / 108 exactly;
2. report Groundedness, Relevance and Retrieval by level (naive / advanced / agentic) and by
   category;
3. **cross-check the judge against the deterministic proxy**: rows where every claim passed local
   verification (`local_all_claims_verified = true`) but Groundedness is below 4 are the
   interesting ones. They are sentences that quote correctly but over-state the quote. Each is
   listed for human review, and the gold data is not changed automatically;
4. write `docs/uc6/results/foundry-evals.md`, labelled **"Foundry, run by Shiraz on <date>, judge
   <deployment>"**.

## 6. Checklist

- [ ] `answers.jsonl` uploaded; Evaluation A with Groundedness, Relevance, Retrieval (+ optional completeness)
- [ ] Judge deployment noted: ________
- [ ] `outcomes.jsonl` uploaded; Evaluation B with 4 string checks
- [ ] Evaluation B totals = 101 / 108 / 108 / 108
- [ ] Results exported (or screenshots) sent to Claude Code
