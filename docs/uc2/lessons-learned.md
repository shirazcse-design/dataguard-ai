# UC2 challenges, lessons, trade-offs and limitations

## 1. Challenges, and what happened

| Challenge | What happened | Resolution |
|---|---|---|
| Legitimate spikes look like exfiltration | Release weeks and month-end close dominate naive alerts | Per-user, day-type-aware robust baselines; IF alerts on 7 release-week days vs the baseline's 15. Rubric 1.2.0 gates data points by role context |
| Zero-variance histories | A user who never uploads gets an infinite z on 1 MB | Per-feature scale floors in the robust z |
| Rubric gaps before any model call | Offline dry runs escalated an approved migration (I10) and month-end finance work (I07, I08) | Rubric 1.1.0 (approval ceiling) and 1.2.0 (role-context gating), approved and logged; golden set unchanged |
| Orchestrator budget | Live run 1: 12 of 36 cases ran out of budget | Orchestrator v2: budget 10 and a graceful stop that still runs the risk assessment (0 exhaustions in run 3) |
| Over-triggered review | UC6 conflicts that didn't matter at the data level, and "conflicts" that were supporting evidence, sent good cases to review | A level-aware conflict rule, cited-only conflicts, and investigator v2's definition of a conflict |
| Replay that silently missed | Recorded tool-call arguments were in model order, the cache in sorted order | `sort_keys` everywhere and a record-then-replay test that fails without the fix |
| A wasted live run | A formatter reflow made a script edit silently fail, so a run ran without its flags | A test that parses the exact script invocations |
| Connectivity | A run was interrupted mid-way | Kept as `run3a-interrupted`; reran with `caffeinate` and a network pre-check |
| Foundry judges misled by our input | Judges failed "extra field / truncated" answers that the model never produced | Send the raw final reply (run 1906); the earlier run is kept as the lesson |
| A telemetry report bug | Review reasons were read from the root span instead of the risk span, so the report showed none | Fixed, regenerated, and a regression test added; found while building the dashboard |
| Platform content recording | Foundry Agent Service logs agent conversations (user id, repositories, hosts) | Measured with a canary; owner decision (a) for the synthetic demo; production options documented |

## 2. Lessons

1. **Let each component be authoritative for one thing.** The model is authoritative for "how
   unusual", services for facts, the rubric for the outcome, and people for action. The agents
   assemble and explain. That made every number traceable, and made "who decided?" a one-line
   answer.
2. **Run the architecture experiment and accept the answer.** The single agent was cheaper and at
   least as accurate on 12 cases. Multi-agent earns its cost through isolation and auditability,
   not accuracy. Saying that plainly is more credible than a diagram with four boxes.
3. **Declare the last iteration before you see the results.** Investigator v2 was named final
   before run 3, so its 0.861 is a measurement, not a tuned score. The misses are part of the
   result.
4. **Most failures were harness and budget problems, not model problems.** Budget exhaustion,
   conflict over-triggering and a replay bug produced most of run 1's misses. Fixing the system
   around the model moved the result more than prompting did.
5. **Two kinds of evaluation, two kinds of truth.** Foundry's string checks reproduced our numbers
   exactly (trust); its judges flagged the investigator's tool selection (insight). A judge is only
   as good as the evidence you show it.
6. **"Self-learning" is a governance design.** The interesting part isn't mining patterns; it's
   refusing to auto-fix the safety-sensitive ones, and admitting that a candidate mined from the
   test set hasn't really been tested.
7. **Say what you didn't do.** Cost `NOT_ESTIMATED`, UC1's notice period inconsistency, and
   fairness only by role family are all written down rather than smoothed over.

## 3. Trade-offs

| Choice | Gained | Gave up |
|---|---|---|
| Multi-agent (full) as the Foundry showcase | Context isolation, per-agent audit and budgets | About 2.4× the model calls of a single agent; lower outcome accuracy on the sample |
| Agents can raise, not silently lower | Safety; a stricter agent view is never lost | More reviews (I05, I11), which the learning-loop candidate addresses |
| Rubric over model judgement | Stable, versioned, explainable outcomes | Rubric gaps must be found and fixed by people (two so far) |
| Guilt-word filter | No accusatory language reaches an analyst | Blunt: legitimate uses of "fired" (as in a process) would also be withheld |
| Synthetic data | Safe to share, fully controlled scenarios | Optimistic separation; scenarios known to their author |

## 4. Limitations

* **Not validated:** synthetic data, 36 cases written by one author, one live run per
  configuration, one model family for agents and judges.
* **HITL recall 0.571** on the full system: three expected-review cases returned an acceptable but
  non-review outcome.
* **No production connectors** (identity provider, SIEM, endpoint DLP), no enforcement point, and no
  per-employee history across days.
* **Foundry's shields** didn't detect paraphrased instructions in logs or tool output; the app's
  layers carried it.
* **Agent conversations are recorded by Foundry** in this demo configuration.
* **Monetary cost not estimated.**

## 5. Next steps (FUTURE)

1. **Label the learning loop's six eval candidates,** re-gate rubric 1.3.0-candidate, and let the
   product owner decide.
2. **Harmonise notice period across UC1 and UC2,** removing or justifying it platform-wide.
3. **Pseudonymise agent payloads** or disable Foundry content recording; then a privacy impact
   assessment.
4. **Improve the investigator's tool selection** (approvals and permissions before the timeline) as
   a gated change.
5. **Evaluate on a fresh, independently written case set,** and on real-population fairness data
   where lawful.
6. **Per-family alert budgets** for the analyst queue.
