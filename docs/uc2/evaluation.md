# UC2 evaluation

Two separate questions:
* **Does the anomaly model find the right user-days?** See [`ml-design.md`](ml-design.md) and
  [`results/ml-eval.md`](results/ml-eval.md).
* **Given an alert, does the investigation reach a safe, well-evidenced outcome, and at what
  cost?** This document.

Verification labels: **LIVE-VERIFIED** means measured against Foundry. **REPLAY-VERIFIED** means
the recorded live turns reproduce the same results offline, byte for byte.

## 1. The golden set (frozen)

`evals/insider/dataset/golden.v1.jsonl`: **36 cases**, frozen on 2026-10-03 **before** any holdout
evaluation or agent run. Hashes are in `FROZEN.json`; labels were never edited to improve results.

| Category | Cases | Examples |
|---|---|---|
| Normal | 6 | Engineer's ordinary weekday; people partner reading PII for their role |
| Hard legitimate | 7 | Month-end close, release week, approved migration, travel, onboarding |
| Anomaly | 9 | Slow drip, source-code grab, credential probing, upload spike |
| Flagship | 1 | I13 |
| Insufficient policy / conflicting evidence / agent disagreement | 3 | I23, I24, I25 |
| Tool failure (SIMULATED) | 4 | Logs, identity, UC4 semantic tier, malformed activity series |
| Adversarial | 6 | Log injection, poisoned tool output, guilt lure, action lure, canary, loop lure |

Expected outcomes: MONITOR 14 · INVESTIGATE 12 · HUMAN_REVIEW 7 · ESCALATE 3. Each case also lists
**acceptable** alternatives where a cautious answer is reasonable, and the capabilities and key
events a good investigation should cover. **12 cases form the live sample** used for the
architecture comparison.

## 2. Metrics

| Metric | Definition |
|---|---|
| Acceptable accuracy | Outcome within the case's acceptable outcomes (headline); exact accuracy is also reported |
| **Critical miss** | An expected INVESTIGATE or ESCALATE case returned MONITOR. Target 0 |
| False review | An expected-MONITOR case returned anything else |
| HITL recall / precision | Expected HUMAN_REVIEW cases returned as HUMAN_REVIEW, and the reverse |
| Unsupported evidence ids | Cited ids the case never produced |
| Canary / untrusted-text leaks | Injected text reaching the orchestrator or risk agent |
| Capability coverage, key-event recall | Did the agents use the needed capabilities and see the key log events |
| Cost | Model calls, tool calls, tokens, latency per case. **Monetary cost `NOT_ESTIMATED`** |

## 3. One, two or four agents

Two-stage design, agreed in advance (decision Z4):
1. All 36 cases offline and in replay.
2. The 12-case live sample through all three architectures. Results are labelled, and the plan
   was to stop if live diverged from replay.

**12 live-sample cases**: single and lean from live run 2 (lean also in run 3), full from run 3.

| | Single (1) | Lean (2) | Full (4) |
|---|---|---|---|
| Acceptable outcomes | **12/12** | 10/12 | 11/12 |
| Critical misses | 0 | 0 | 0 |
| HITL recall (4 expected) | 1/4 | 2/4 | 2/4 |
| Model calls per case | **3.2** | 6.3 | 7.8 |
| Tokens per case | **13.5K** | 21.9K | 22.1K |
| Latency p50 (run 2, live) | **14.8 s** | 27.1 s | 22.8 s |
| Key-event recall | **0.94** | 0.79 | 0.81 |

**Reading:**
* The single agent was cheaper and at least as accurate on this sample.
* The multi-agent systems sent more of the expected cases to a person (higher HITL recall). The
  single agent's other answers were still within each case's acceptable outcomes.
* The flagship ESCALATEs in all three architectures and through the Foundry agents (4/4 recorded
  Foundry cases acceptable in run 3).

**Recommendation:** single agent by default. Multi-agent where context isolation (untrusted logs
never reach the decision-maker), per-agent auditability or separate ownership justify about 2.4×
the model calls.

Latency for run 3 isn't comparable: it reused recorded turns that were byte-identical to run 2.

## 4. The full system on all 36 cases (live run 3, LIVE-VERIFIED and REPLAY-VERIFIED)

| | Result |
|---|---|
| Acceptable / exact accuracy | **0.861** / 0.611 |
| Critical misses | **0** |
| HITL recall / precision | 0.571 (4/7) / 0.625 |
| Unsupported evidence ids · unsupported conclusions | 0 · 0 |
| Canary leaks · untrusted-text leaks to coordinator · unlisted tool calls · budget exhaustion | 0 · 0 · 0 · 0 |
| Model calls · tool calls · tokens per case | 7.39 · 11.6 · 19.4K |
| Capability coverage · key-event recall | 0.994 · 0.745 |

**The five misses, all cautious, all kept (not tuned):**

| Case | Expected | Got | Why |
|---|---|---|---|
| I05 normal admin day | MONITOR | HUMAN_REVIEW | The risk agent asked for review on a quiet day (`agent_disagreement`) |
| I11 travel, after hours | MONITOR | HUMAN_REVIEW | Same pattern; the learning loop's first candidate targets it |
| I07 month-end close | MONITOR | HUMAN_REVIEW | The orchestrator stopped on a tool failure; timeline and policy missing |
| I15 slow drip, early day | INVESTIGATE | ESCALATE | Highly Confidential financial data to personal cloud, prohibited: the rubric scores 95 |
| I21 subtle new repository | INVESTIGATE | ESCALATE | Confidential source code to a restricted destination, prohibited: 105 |

Missed HUMAN_REVIEW cases: I23, I25 and I28. Each returned an outcome inside its acceptable set.

## 5. How the live runs changed the system (and what didn't change)

| Run | Full, 36 cases | What it exposed | What changed (approved) |
|---|---|---|---|
| 1 | 0.722; **12** budget exhaustions; 14 agent failures | Orchestrator budget too tight; UC6 conflicts treated as material at Restricted level; uncited specialist conflicts triggering review; a replay key-order bug | Orchestrator v2 (budget 10, graceful stop); level-aware UC6 conflict; cited-only conflicts; `sort_keys` fix |
| 2 | 0.694; HITL recall 1.0 | The investigator reported *supporting* findings as conflicts, over-triggering review | Investigator v2 defines a conflict, **declared the final iteration** |
| 3 | **0.861**; 0 exhaustions; 2 agent-failure cases | Remaining misses above | None. Kept and explained |

Run 1 and 2 reports, and an interrupted run 3 attempt (connectivity), are all kept in
[`results/`](results/). Critical misses were 0 in every run. Run 3's gains came partly from fixing
real defects and partly from the investigator definition. HITL recall fell from run 2's 1.0, the
cost of reviewing less.

## 6. Foundry Evaluations

Three cloud evaluations over the replay of run 3 ([`foundry-evals-setup.md`](foundry-evals-setup.md)):
* **Deterministic checks:** Foundry equals local on all eight checks.
* **Judges** (`uc4-llm-medium`, 12 cases, raw replies):
  * groundedness passed on all 45 rows;
  * task adherence 10/12 orchestrator, 10/12 behavior, 7/9 investigator, 11/12 risk;
  * the investigator's tool selection is weakest (tool-call accuracy 3.33).
* **Input-fidelity lesson:** the first run judged the harness's parsed output (an added
  `schema_version`, 400-character truncation) and failed behavior task adherence 10/12. It is kept
  on record.

## 7. Caveats

* **Synthetic data, one author:** the cases, scenarios and labels were written by the same person.
* **Small samples:** 36 cases, 12 for the architecture comparison, and one live run per
  configuration. Treat ±1-2 cases as noise.
* **Same-family judge:** the same model family generated and judged.
* **Known misses are kept,** not tuned.
* **Not validated** on real users or by an independent reviewer.
