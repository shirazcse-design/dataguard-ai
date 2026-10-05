# UC2 controlled learning loop

"Self-learning" in UC2 means a **governed, feedback-driven product-improvement loop**, not
autonomous self-modification. The runtime agents never:
* rewrite their prompts;
* retrain;
* change their permissions;
* modify policy;
* alter evaluation labels;
* deploy changes.

Code: `evals/insider/learning_loop.py`. Commands: `dataguard-insider learn run` and
`learn approve`. Report: [`results/learning-loop.md`](results/learning-loop.md).

## 1. The loop

| Step | What | Where |
|---|---|---|
| 1. Runs | Recorded investigations (live run 3, 36 cases) | `results/agents-live-run3-full36.json` |
| 2. Observability + analyst feedback | Telemetry summary; analyst decisions from the dashboard plus a **seeded synthetic set** (6 records, labelled `source: seed`) | `results/observability.json`, `data/insider/feedback/`, `var/demo/insider-reviews.jsonl` |
| 3. Failure / low-confidence mining | Groups false reviews, over-escalations, under-calls, agent-reliability issues, low-confidence recommendations and analyst disagreement into patterns | `mine()` |
| 4. Curated eval candidates | One new-case proposal per distinct pattern: a fresh variant to label. **NEEDS_HUMAN_LABEL**; never added to the golden set by the loop | `eval_candidates()` |
| 5. Candidate improvement | A versioned file written by a person: prompt, rule, retrieval, model or harness | `config/insider/candidates/` |
| 6. Offline regression + safety gate | Replays all 36 golden cases with the current rubric and the candidate; every check must pass | `gate()` |
| 7. Human approval | `learn approve`: an approver name, a decision and a reason; it checks the candidate's hash against the gated one | `docs/uc2/learning/approvals.jsonl` |
| 8. Controlled deployment | A reviewed change to `config/insider/risk.v1.yaml` (new version and changelog), merged like any code | a PR |

## 2. The safety rails (each one tested)

* **The runtime never imports the loop or reads `candidates/`.** It loads only
  `config/insider/risk.v1.yaml`.
* **Protected files are hash-checked before and after every run:** the rubric, agent config,
  prompts, golden set and `FROZEN.json`. The run fails if any changed.
* **Candidates must live in `config/insider/candidates/`** and target a **mined** pattern.
* **Safety-sensitive patterns never become automatic candidates.** These are patterns whose fix
  would lower a high-risk outcome: over-escalations, under-calls, or an analyst asking to lower an
  escalation. They go to a human review list instead.
* **The gate blocks:**
  * any critical miss;
  * lower acceptable accuracy or lower HITL recall;
  * a flagship that no longer escalates;
  * any lowered escalation;
  * any lowered adversarial case;
  * unsupported evidence;
  * new agent failures.
* **Approval deploys nothing.** It requires a name and a reason, and refuses a candidate that failed
  the gate or differs from the gated file.

## 3. First iteration (2026-10-05)

**Mined:** 9 patterns from the live run and 6 seeded feedback records. Three are safety-sensitive:
* I15 and I21 over-escalated;
* the analyst wanted I21 lowered.

**Candidate `1.3.0-candidate` (harness rule):** on a NORMAL-band day that the rubric scores MONITOR,
a risk-agent HUMAN_REVIEW recommendation is logged for the analyst (`agent_review_request_logged`)
instead of forcing review. It targets `false_review:review:agent_disagreement` (I05, I11). The
harness supports it behind a rubric key that 1.2.0 doesn't set, so runtime behaviour is unchanged.

| Gate (36 cases, replay of live run 3) | 1.2.0 | 1.3.0-candidate |
|---|---|---|
| Acceptable outcomes | 31 | **33** |
| False reviews | 4 | **2** |
| HITL precision | 0.5 | **0.667** |
| Critical misses | 0 | 0 |
| HITL recall | 0.571 | 0.571 |
| Changed cases | | I05, I11: HUMAN_REVIEW → MONITOR |

Verdict **PASS**; status **AWAITING_HUMAN_APPROVAL**.

**Why it isn't promoted yet:** the candidate was mined from the same cases it was gated on, so
passing is *necessary, not sufficient*. The next step is to label the proposed eval candidate (a
fresh variant of I05/I11) and re-gate. The product owner decides.

**The trade-off a reviewer should weigh:** the agent loses the ability to force review on a quiet
day; it can only flag one. That's acceptable only because the rubric's floors and the other
triggers still apply.

## 4. What this demonstrates

The PRD's "self-learning" becomes a product process with an audit trail:
* evidence (runs and feedback);
* hypothesis (a pattern);
* change (a versioned candidate);
* test (an offline gate on frozen cases);
* decision (a named human);
* release (a reviewed PR).

The system improves because people improve it, using the system's own evidence.
