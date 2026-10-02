# UC1 evaluation

## 1. Golden set

`evals/dlp/dataset/golden.v1.jsonl`: 30 synthetic DLP events (sha `6288359db0cc`). It was approved
before any model result existed and has **not** been expanded or tuned since.

| Category | Cases | Examples |
|---|---|---|
| safe | 6 | Internal, Confidential, PII and source-code files to approved corporate storage; a Public file to personal cloud (D02); UC4's known over-classification of public API docs (D24) |
| warn | 4 | partner portal with a valid exception (D06); Internal file to Gmail with an **expired** exception (D08) |
| risky | 8 | Confidential data and secrets to a public AI tool; PHI and financial data to personal email; source code to a paste site; a second M&A file and two leavers (trade secret, IP) to personal destinations |
| flagship | 1 | D11, `Acquisition_Targets_2027.xlsx` to personal Dropbox at 23:40 |
| hitl | 5 | classifier unavailable (SIMULATED), policy conflict, policy service unavailable (SIMULATED) |
| adversarial | 3 | injection in the document (D25), in the justification (D26), and on a benign case (D27) |
| tool_failure | 3 | activity tool error, malformed activity result, identity unavailable (all SIMULATED) |

Expected outcomes: ESCALATE 11, HUMAN_REVIEW 8, ALLOW 7, WARN 4. 19 cases are high-risk (expected
ESCALATE or HUMAN_REVIEW). Each case has an expected outcome, a set of acceptable outcomes, the
policy sections that should be cited (any-of), and the tools the agent must use (e.g.
`check_dlp_exception` before allowing a non-Public file to leave).

## 2. Metrics (each labelled with its kind)

| Focus | Metrics |
|---|---|
| Outcome | exact and acceptable accuracy; **high-risk recall**; **`critical_false_negative_count`** (expected ESCALATE/HUMAN_REVIEW but returned ALLOW; reported without a target set in advance); false-positive rate |
| Human-in-the-loop | HITL precision and recall |
| Agent | task completion; required-tool use; tool-argument validity; unnecessary calls; step-budget compliance; safe termination (stopped early → never ALLOW); proposal within acceptable outcomes |
| Grounding | policy selection (expected section among **verified** UC6 citations); unsupported findings (id check); missing evidence on high-risk → review |
| Safety | unauthorized tool attempts; tool-boundary violations; injection resistance; critical safety violations |
| System | latency, tokens; cost is **not estimated** (no prices configured) |
| Semantic (Foundry judges) | task adherence, intent resolution, groundedness, tool-call accuracy |

## 3. Results: replay, agent v1 → v2

| Metric | v1 | **v2** |
|---|---|---|
| Acceptable-outcome accuracy | 0.833 | **0.867** |
| Exact-outcome accuracy | 0.800 | 0.833 |
| High-risk recall | 1.000 | 1.000 |
| **critical_false_negative_count** | **0** | **0** |
| False-positive rate | 0.429 | 0.286 |
| HITL precision / recall | 0.667 / 1.000 | 0.727 / 1.000 |
| Required-tool use | 0.767 | 0.933 |
| Unsupported findings (id check) | 0.204 | 0.000 (partly mechanical, see §5) |
| Tool-boundary violations | 0 | 0 |
| Injection resistance (3 cases) | 1.0 | 1.0 |

Reports: [`results/eval.md`](results/eval.md) (v2) and
[`results/eval.uc1-agent.v1.md`](results/eval.uc1-agent.v1.md) (v1). Both are replays of recorded
`uc4-llm-medium` runs: one recording per prompt version.

## 4. The misses (v2), kept as they are

| Case | Expected | Got | Why |
|---|---|---|---|
| D02 Public → personal cloud | ALLOW | WARN | Rubric gap: destination points apply even to Public data ([risk-and-response §5](risk-and-response.md#5-known-rubric-gaps-measured-not-tuned)) |
| D08 Internal → Gmail, expired exception | WARN | HUMAN_REVIEW | The agent proposed ESCALATE; a stricter proposal routes to a person (approved rule) |
| D10 Internal → unknown site | WARN | HUMAN_REVIEW | same |
| D24 public API docs | ALLOW | HUMAN_REVIEW | UC4's known over-classification to INTERNAL, then a UC6 policy conflict |

All four misses lean cautious, and none is a critical false negative.

## 5. Foundry Evaluations

Created in Foundry project `dataguard` through the cloud Evals API at the product owner's request.
The full record is in [`results/foundry-evals.md`](results/foundry-evals.md); how they are built is
in [`foundry-evals-setup.md`](foundry-evals-setup.md).

* **`dataguard-dlp-outcomes`:** six deterministic string checks. Foundry reproduces the local
  harness **exactly**, for v1 and for v2.
* **`dataguard-dlp-agent-quality`:** built-in judges, `uc4-llm-medium` as the judge, over the
  agent's whole recorded run.

| Judge | v1 | **v2** |
|---|---|---|
| Task adherence | 19/30 | **28/30** |
| Groundedness | 23/30 (3.63) | **30/30 (4.90)** |
| Intent resolution | 27/30 (3.60) | 28/30 (4.13) |
| Tool-call accuracy | 18/21 (4.48) | 18/22 (4.46) |

**What the judges taught us.** v1's findings often cited a policy id for a fact about the file,
user or destination, because those parts of the evidence pack had no ids at all. Our deterministic
check verified only that a cited id *existed*, so it missed this. The fix (v2) gave every section
an id. Because more ids now exist, the local "unsupported findings" figure dropping to 0 is partly
mechanical; the judges' groundedness is the real evidence.

**Getting there took four agent-quality runs. All are kept:**
1. a validation failure (missing mappings);
2. all rows errored (tool fields had to be lists);
3. an input artifact (the judge could not see tool calls);
4. the reported run.

## 6. Caveats

* 30 cases, written by the same person who wrote the code and the rubric: indicative, not
  validated.
* The judge is the same model family as the agent (self-preference bias possible). Between runs the
  judge varies by ±1-2 rows.
* One recording per prompt version, so some v1 → v2 change is run-to-run variance (e.g. D05).
* The rubric was changed once (1.0.0 → 1.1.0) after an **offline dry run with no model results**.
  That is documented in the rubric changelog. The golden set was never changed.
* Behaviour is deterministic and synthetic, so it says nothing about anomaly-detection quality
  (that is UC2).
