# Foundry Evaluations for the DLP investigator (UC1)

**Status (2026-10-02): created and run** in Foundry project `dataguard` through the cloud Evals API
(`dataguard-dlp foundry-eval --run`), at the product owner's explicit request. Results and every
run, including the failed ones, are in [`results/foundry-evals.md`](results/foundry-evals.md).

## 1. What runs where

| Metric | Where | Why |
|---|---|---|
| Outcome acceptable, no critical false negative, no unlisted tool, required tools used, safe termination, findings cite known evidence | **Foundry string checks** on `outcomes.jsonl`, and locally | Foundry must reproduce the local harness exactly: a check that the portal and the reports agree |
| Task adherence, intent resolution, groundedness, tool-call accuracy | **Foundry built-in judges** on `agent.jsonl` | Semantic judgements the deterministic harness cannot make |
| Policy selection, HITL precision/recall, high-risk recall, latency | **Local only** | Need golden labels or the investigation trace, not a judge |

## 2. The dataset files (generated, synthetic)

`dataguard-dlp foundry-eval` replays the golden set and writes:

* `data/dlp/foundry_eval/outcomes.jsonl`: one row per case: id, category, expected, outcome, and
  six `pass`/`fail` fields.
* `data/dlp/foundry_eval/agent.jsonl`: one row per case where the agent answered:
  * `query`: the agent's instructions and evidence pack;
  * `response`: the agent's **whole run** as messages (tool calls, tool results, final answer);
  * `context`: the pack and tool results;
  * `tool_calls`, `tool_definitions`.

  The evidence pack never contains document text, and a test checks it.

## 3. Evaluators and mapping

| Evaluator | Mapped fields |
|---|---|
| `builtin.task_adherence` | query, response, tool_definitions |
| `builtin.intent_resolution` | query, response, tool_definitions |
| `builtin.groundedness` | query, response, context, tool_definitions |
| `builtin.tool_call_accuracy` | query, response, tool_calls, tool_definitions |

The judge is `uc4-llm-medium` (`--judge` to change it). Three rules were learned the hard way:
* Foundry rejects an evaluation unless **every** field an evaluator supports is mapped, when the
  row has it;
* `tool_calls`, `tool_definitions` and `response` must be **JSON lists**, not strings;
* a judge given only the final answer cannot see tool use, and scores task adherence far too low.

## 4. Re-running

```
DATAGUARD_FOUNDRY_PROJECT_ENDPOINT=https://dataguard-resource.services.ai.azure.com/api/projects/dataguard \
  .venv/bin/dataguard-dlp foundry-eval --run --which both --tenant-id <tenant>
```

`--which outcomes|agent|both`. Each run creates a new evaluation in the portal (nothing is
overwritten) and writes `docs/uc1/results/foundry-evals-<timestamp>.json`. Rows travel inline; no
dataset is uploaded to project storage.
