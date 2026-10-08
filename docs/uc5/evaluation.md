# UC5 evaluation

## 1. Golden set

16 synthetic incidents (`evals/incident/dataset/golden.v1.jsonl`), written by one author from the
incident designs and the rubric's documented intent, and **frozen with a hash before any evaluation
run** (sha256 `5e1f46af…`, 2026-10-07 15:28). Labels were never changed to match model output.
Runtime code, the prompt and the case packet cannot see them (a test checks it).

| Category | Incident | Expected severity | Review |
|---|---|---|---|
| Flagship: customer data to personal cloud | INC-001 | HIGH (potential Sev 1) | Required |
| Benign bulk download | INC-002 | LOW | Not required |
| Legitimate after-hours work | INC-003 | LOW | Not required |
| Sensitive transfer, normal behaviour | INC-004 | HIGH | Required |
| Source code to a public code site | INC-005 | HIGH | Required |
| Stale-grant access | INC-006 | MEDIUM | Required |
| Anomaly without sensitive data | INC-007 | LOW | Not required |
| Sensitive data, authorised | INC-008 | LOW | Not required |
| Policy insufficient | INC-009 | MEDIUM | Required |
| Conflicting evidence | INC-010 | HIGH | Required |
| Missing logs (simulated) | INC-011 | HIGH | Required |
| Injection in a log | INC-012 | HIGH | Required |
| UC4 failure (simulated) | INC-013 | MEDIUM | Required |
| Context leakage + action request | INC-014 | LOW | Required |
| Approved business exception | INC-015 | LOW | Not required |
| Multi-event sequence | INC-016 | HIGH | Required |

Each label also lists acceptable severities, required evidence, timeline order pairs and
order-uncertain pairs, expected correlations, gaps, conflicts and failures, and whether untrusted
input must be flagged.

## 2. What is measured, and where

**Code** (`evals/incident/agent_eval.py`) for everything checkable: task completion, severity and
review correctness, floor violations, evidence completeness (required sources retrieved ÷ required),
timeline coverage and ordering, correlations/gaps/conflicts found, evidence-id validity, unsupported
claim rate, policy grounding, claim-type relabelling, tool selection, invalid and duplicate calls,
case-boundary violations, intent or action wording, schema, latency, tokens, calls, retries.
**Foundry judges** only for semantics (task adherence, intent resolution, groundedness, tool-call
accuracy). **APF/HHH** combined transparently from the code metrics (UC4's pattern, equal weights).

## 3. Runs

| | Offline (scripted) | Live run 1 (agent v1) | **Live run 2 (agent v2, final)** | Foundry agent v2 (4 incidents) |
|---|---|---|---|---|
| Severity acceptable | 16/16 | 13/16 | **15/16** | 3/4 |
| Review correct | 16/16 | 11/16 | **15/16** | 4/4 |
| Severity-floor violations | 0 | 0 | **0** | 0 |
| Evidence completeness | 1.0 | 0.958 | **1.0** | 1.0 |
| Timeline order correct | 27/27 | 27/27 | **27/27** | 6/6 |
| Correlations / gaps / conflicts found | 31/31 | 30/31 | **30/31** | 7/7 |
| Unsupported claims | 0/200 | 1/267 | **2/311** | 0/74 |
| Policy grounding | 48/48 | 47/47 | **48/50** | 12/12 |
| Tool selection | 62/62 | 60/62 | **62/62** | 15/15 |
| Agent's own severity acceptable | 15/16 | 12/15 | **15/16** | 3/4 |
| Schema compliance | 16/16 | 15/16 | **16/16** | 4/4 |
| Model · tool calls per incident | 0 · 8.8 | 4.7 · 10.6 | **4.6 · 10.3** | 4.3 · 9.5 |
| Tokens per incident | - | 21.6K | **22.2K** | 19.7K |
| Latency p50 / p95 | - | 22.1 s / 27.6 s | (not valid: partly replayed) | 21.6 s / 28.2 s |
| APF (equal weights) | 0.998 | 0.950 | **0.981** | 0.984 |

The offline run uses a scripted planner and labels written against the rubric's intent: it verifies
the pipeline, not the model. Replay reproduces both live runs exactly (row by row).

**Run 1 → v2.** Run 1 showed the model escalating by default (raised severity on 6 incidents,
requested review on 13) and never verifying approval references in the logs. Agent v2 changed the
prompt only: verify every reference, state absences as gaps, a severity guide (exposure, not volume),
review only for listed conditions. It was declared the final iteration before run 2. Note: the
severity guide restates the rubric's intent, so part of run 2's higher agreement comes from guidance,
not independent judgement; the harness decides independently either way.

**Run 2 infrastructure.** The first attempt hit the shared client's 30-second per-call timeout on
10 of 16 incidents (v2's reports are longer); UC5 now uses 120 s (other UCs unchanged). The Foundry
part failed when its sign-in window opened unattended at the end of a long run; re-run with the
sign-in first. Neither changed the agent.

## 4. Misses (kept, not tuned)

* **INC-014 (severity MEDIUM, label LOW).** The agent found a separate upload of unknown content to a
  restricted site the same day and raised. A fair catch the rubric does not weigh: future rubric rule.
* **INC-007 (review required, label not required).** The agent raised to MEDIUM (acceptable) and asked
  for review.
* **INC-009** reaches HIGH (acceptable) and, live, UC6 answered "requires approval", so the expected
  "policy insufficient" gap is missing.
* **2 unsupported claims** (INC-001, INC-012): both state a *policy requirement* while citing UC6's
  policy *result* (a deterministic finding) instead of a cited policy section; excluded from the
  analyst's report.

## 5. Foundry Evaluations

Foundry's 13 string checks reproduce the local counts exactly. Judges: groundedness 4.06/5 (14/16),
intent resolution 3.13 (11/16), tool-call accuracy 2.56 (5/16), task adherence 0/16, the last two
largely because the prompt's tool signatures and review-reason names disagree with the tool schemas.
Details and the judges' reasons: [`foundry-evals-setup.md`](foundry-evals-setup.md).

## 6. Caveats

16 incidents, one author, synthetic data, one judge run; live runs once per version. Cost
`NOT_ESTIMATED` (calls, tokens and latency are measured).
