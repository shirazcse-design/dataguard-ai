# UC1 evaluations in Microsoft Foundry

The evaluations were created in Foundry project `dataguard` through the cloud Evaluations API
(`dataguard-dlp foundry-eval --run`) on 2026-10-02, **at the product owner's explicit request**.
Inputs are `data/dlp/foundry_eval/{outcomes,agent}.jsonl`, generated from the REPLAY run of the
30-case golden set (golden sha `6288359db0cc`, rubric 1.1.0, agent prompt `uc1-agent.v1`, planner
`uc4-llm-medium` chat-completions). The rows are synthetic: a fictional company, users and
policies. The evidence pack never contains document text, and neither do these rows. Rows travel
inline; no dataset is uploaded to project storage.

Every run is kept, including the failed ones:

| Run | File | Result |
|---|---|---|
| outcomes, 06:04 | `foundry-evals-20261002-0604.json` | completed |
| agent quality, 06:04 | same file | **failed**: `EvalValidationFailed` / `MissingRequiredDataMapping`. Foundry requires every field an evaluator supports to be mapped when the row has it. |
| agent quality, 06:08 | `foundry-evals-20261002-0608.json` | completed, but **30/30 rows errored**: `tool_definitions must be a list, but found str` |
| agent quality, 06:40 | `foundry-evals-20261002-0640.json` | scored, but `response` held only the final JSON, so the judges could not see the agent's tool calls. Superseded (see below). |
| agent quality, 06:44 | `foundry-evals-20261002-0644.json` | **v1 baseline** (sections 1-2): `response` is the agent's full run (tool calls, tool results, final answer) |
| outcomes + agent quality, 09:20 | `foundry-evals-20261002-0920.json` | **v2** (section 3): after the evidence-id fix, re-recorded |

## 1. `dataguard-dlp-outcomes`: deterministic agreement (string checks, no judge)

| Check | Foundry | Local | Match |
|---|---|---|---|
| outcome_acceptable | 25 / 30 | 25 / 30 | exact |
| no_critical_false_negative | 30 / 30 | 30 / 30 | exact |
| no_unlisted_tool | 30 / 30 | 30 / 30 | exact |
| required_tools_used | 23 / 30 | 23 / 30 | exact |
| safe_termination (never ALLOW after an abnormal stop) | 30 / 30 | 30 / 30 | exact |
| findings_cite_known_evidence (every finding's id exists) | 14 / 30 | 14 / 30 | exact |

The portal reproduces the local harness exactly.

## 2. `dataguard-dlp-agent-quality`: AI-assisted (judge `uc4-llm-medium`)

Built-in evaluators run over the agent's recorded investigations: the instructions and evidence
pack (query), the agent's full run (response), the tool results and pack (context), and the tool
calls and definitions.

| Evaluator | Scale | Mean | Passed | Failed |
|---|---|---|---|---|
| `builtin.task_adherence` | pass/fail | 0.633 | **19 / 30** | 11 |
| `builtin.intent_resolution` | 1-5 | 3.60 | 27 / 30 | 3 |
| `builtin.groundedness` | 1-5 | 3.63 | 23 / 30 | 7 |
| `builtin.tool_call_accuracy` | 1-5 | 4.48 | 18 / 21 | 3 (9 cases made no tool call; not applicable) |

**What the judges found (from their reasons):**

1. **Mis-attributed evidence ids are the dominant failure** (most task-adherence and groundedness
   failures, including the flagship D11). The agent cites facts about the file's classification,
   the destination or behaviour to policy ids (P1-P3). Two design gaps make this unavoidable:
   * the evidence pack's classification, destination, behaviour and identity sections have **no
     evidence ids**, so there is nothing correct to cite;
   * the tool results do not carry the `EXCEPTION` / `ACTIVITY` ids that the prompt tells the agent
     to use.

   The local metric (`findings_cite_known_evidence`, unsupported-finding rate 0.204) checks only
   that an id exists, so it understated this. The semantic judge caught it.
2. **The judges agree on safety and scope.** Their reasons repeatedly note that the agent stayed
   in scope, followed the exception-check workflow, took no action, and did not follow injected
   text (D26).
3. **Tool-call accuracy is the strongest signal (4.48).** Its failures (D09, D12, D19) are
   judgement calls about whether an extra lookup was needed, not wrong tools or arguments.

**Fix applied as v2 (section 3).** The decision path was unaffected either way: findings are
explanations for the analyst, and the deterministic harness makes the decision.

## 3. v2: the evidence-id fix (2026-10-02, product owner's request)

Changes:
* every evidence-pack section has its own id: `EVENT`, `DESTINATION`, `PRECHECKS`,
  `CLASSIFICATION`, `IDENTITY`, `BEHAVIOR`, `POLICY`, plus `P1..Pn` for policy claims;
* `check_dlp_exception` always returns `EXCEPTION`, including when there is no exception. Only a
  matching record sets `verified_exception`, the only thing that can lower the score (unit-tested);
* prompt `uc1-agent.v2`: rule 4 says to cite the section that states the fact, and that a policy
  id never supports a fact about this file, user or destination;
* re-recorded all 30 cases (chat-completions); Foundry agent version 3 was registered with the v2
  instructions and the same guardrail. The golden set, rubric, UC4 and UC6 are unchanged.

| Evaluator | v1 (06:44) | **v2 (09:20)** |
|---|---|---|
| task_adherence (pass/fail) | 19 / 30 | **28 / 30** (D06, D12) |
| intent_resolution (1-5) | 3.60, 27 / 30 | **4.13, 28 / 30** (D06, D08) |
| groundedness (1-5) | 3.63, 23 / 30 | **4.90, 30 / 30** |
| tool_call_accuracy (1-5) | 4.48, 18 / 21 | 4.46, 18 / 22 (D08, D15, D20, D26) |

| Deterministic (Foundry = local, exact) | v1 | **v2** |
|---|---|---|
| outcome_acceptable | 25 / 30 | **26 / 30** |
| no_critical_false_negative | 30 / 30 | 30 / 30 |
| no_unlisted_tool | 30 / 30 | 30 / 30 |
| required_tools_used | 23 / 30 | **28 / 30** |
| safe_termination | 30 / 30 | 30 / 30 |
| findings_cite_known_evidence | 14 / 30 | 30 / 30 (partly by construction: more ids are now valid) |

Local replay (`eval.md`; v1 kept as `eval.uc1-agent.v1.md`): acceptable accuracy 0.833 → 0.867,
false-positive rate 0.429 → 0.286 (D05 is now ALLOW, because the agent no longer proposes WARN for
an approved corporate upload), HITL precision 0.667 → 0.727, required-tool use 0.767 → 0.933.
Critical false negatives stay 0 and high-risk recall stays 1.0.

**Caveats for v2:**
* `findings_cite_known_evidence` reaching 30/30 is partly mechanical: more ids now exist. The
  semantic judges are the real evidence that citations improved (groundedness 30/30, mean 4.90).
* One recording per prompt version: some of the change is model run-to-run variance, not the
  prompt alone (e.g. D05).
* The same-family judge and one-run caveats below still apply. Tool-call accuracy did not
  change; its remaining failures are whether an extra lookup was needed, which v2 did not target.

## How to read this (caveats)

* **Same-family judge.** `uc4-llm-medium` produced the agent's turns and also judged them, so
  self-preference bias is possible. A different judge or a human spot-check would strengthen the
  results.
* **Judge variability.** Between the 06:40 and 06:44 runs, groundedness inputs changed only in the
  response format, but the set of low-scoring rows moved (for example D03 low only in 06:44). With
  one run per configuration, treat ±1-2 rows as noise.
* **Input fidelity matters.** The 06:40 task-adherence score (7/30) was mostly an artifact of
  sending only the final JSON. It is kept on record as a lesson: agent evaluators need the agent's
  whole run.
* 30 cases, one judge, one run per configuration: indicative, not statistically strong.
