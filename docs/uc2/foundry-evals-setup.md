# Foundry Evaluations for the UC2 Insider Risk agents

**Status (2026-10-04): created and run** in Foundry project `dataguard` through the cloud Evals API
(`dataguard-insider foundry-eval --run`), at the product owner's explicit request (P11). Both runs
are recorded: `results/foundry-evals-20261004-1844.json` (input defect, below) and
**`results/foundry-evals-20261004-1906.json` (reported)**.

## 1. What runs where

| Metric | Where | Why |
|---|---|---|
| Outcome acceptable, no critical miss, no unlisted tool, evidence ids supported, no canary leak, no untrusted-text leak, within budget, anomaly score unmodified | **Foundry string checks** (`dataguard-insider-outcomes`, 36 cases) and locally | Foundry must reproduce the local harness exactly |
| Task adherence, intent resolution, groundedness, tool-call accuracy | **Foundry built-in judges** on the orchestrator, behavior and investigator runs (`dataguard-insider-agent-quality`) | Semantic judgements the harness cannot make |
| Task adherence, intent resolution, groundedness (no tools) | **Foundry built-in judges** on the risk agent (`dataguard-insider-risk-quality`) | The risk agent has no tools, so tool-call accuracy does not apply |
| Architecture comparison, HITL recall, key-event recall, latency, role-family fairness, ML metrics | **Local only** | Need golden labels, the trace, or the model |

Source: the replay of live run 3 (full 4-agent system, chat-completions). Judged rows: the 12
live-sample cases (33 tool-agent rows, since the investigator ran on 9 of them; 12 risk rows).
Judge: `uc4-llm-medium`.

## 2. The dataset files (generated, synthetic)

`dataguard-insider foundry-eval` writes `data/insider/foundry_eval/`:
* `outcomes.jsonl`: id, category, expected, outcome, and eight `pass`/`fail` fields;
* `agents.jsonl` / `risk.jsonl`: one row per agent run: `query` (instructions + case brief),
  `response` (the whole run as messages, ending with the agent's **raw** final reply), `context`
  (brief + tool results), and for tool agents `tool_calls` and `tool_definitions` as JSON lists.

Checked before sending: no injected log text, no canary, and no 8-word overlap with any of the 43
case documents. Rows travel inline; nothing is uploaded to project storage.

## 3. Results

**Deterministic checks: Foundry = local on all eight** (both runs): outcome acceptable 31/36,
every other check 36/36.

**Judges (run 1906, reported):**

| Agent | Task adherence (pass/fail) | Intent resolution (1-5) | Groundedness (1-5) | Tool-call accuracy (1-5) |
|---|---|---|---|---|
| Orchestrator | 10/12 | 4.08 (10/12) | 4.92 (12/12) | 4.17 (9/12) |
| Behavior | 10/12 | 4.42 (12/12) | 4.75 (12/12) | 5.00 (12/12) |
| Investigator | 7/9 | 3.67 (8/9) | 4.44 (9/9) | **3.33 (6/9)** |
| Risk (no tools) | 11/12 | 4.50 (12/12) | 4.42 (12/12) | n/a |

**Findings:**
1. **Groundedness is the strongest signal:** 45/45 rows pass. The agents' statements are supported
   by the evidence they were given, consistent with 0 unsupported evidence ids locally.
2. **The investigator's tool selection is the weakest area** (3.33). In run 1844 the judges' reasons
   for the same cases (I25, I27, I30) say it skipped lookups that would have helped the timeline
   (for example approvals or permissions). This is a real finding. It is **not tuned**: investigator
   v2 was the agreed final iteration. The harness still decides, and missing evidence lowers
   confidence and raises review, not the reverse.
3. **I27 fails across agents** (orchestrator and investigator: intent, adherence, tool calls).
   I27 is the SIMULATED identity-service outage: the agents could not complete the task as asked.
   Locally it is acceptable: the harness routed it to HUMAN_REVIEW, its expected outcome. The judges
   score the agents' incomplete work, which is what a failed dependency should produce.
4. **The judges and the harness measure different things.** The five locally unacceptable cases
   (I05, I07, I11 over-reviewed; I15, I21 over-escalated) are harness outcome errors. Of these,
   only I11 is in the judged sample, and only its orchestrator intent resolution fails.

## 4. Input fidelity: run 1844 (kept on record)

The first run sent the harness's **parsed** output as each agent's final answer. That adds
`schema_version` and truncates long fields to 400 characters. The judges, reading the prompt's
"reply with ONLY this JSON object", failed task adherence for "an extra field not in the schema"
and "truncated rationale": behavior 2/12, risk 5/12. Run 1906 sends the model's raw reply
(`FinalRecorder`; a unit test guards it). Agents, prompts and the golden set are unchanged.

| Task adherence | Run 1844 | **Run 1906** |
|---|---|---|
| Orchestrator | 9/12 | **10/12** |
| Behavior | 2/12 | **10/12** |
| Investigator | 7/9 | 7/9 |
| Risk | 5/12 | **11/12** |

Lesson (as in UC1): an agent evaluator must see what the agent actually produced.

## 5. Caveats

* **Same-family judge.** `uc4-llm-medium` produced the agents' turns and judged them;
  self-preference bias is possible.
* **One run per configuration, 12 cases.** Treat ±1-2 rows as noise: indicative, not statistically
  strong. The judges' reasons were read for run 1844 only.
* **Synthetic data.** All users, logs and resources are fictional.

## 6. Re-running

```
DATAGUARD_FOUNDRY_PROJECT_ENDPOINT=https://dataguard-resource.services.ai.azure.com/api/projects/dataguard \
  .venv/bin/dataguard-insider foundry-eval --run --which all --tenant-id <tenant>
```

`--which outcomes|agent|risk|all`; `--ids` to choose the judged cases. Each run creates new
evaluations (nothing is overwritten) and writes `docs/uc2/results/foundry-evals-<timestamp>.json`.
