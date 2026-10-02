# UC1 challenges, lessons, trade-offs and limitations

## 1. Challenges, and what happened

| Challenge | What happened | Resolution |
|---|---|---|
| Two vocabularies (UC4 levels vs policy terms) | `HIGHLY_CONFIDENTIAL` vs *Restricted*; UC4 categories vs policy phrases | Explicit, versioned integration contract (`mapping.v1.yaml`) and a test that every mapped section exists; no renaming, no LLM inference |
| Sensitivity scored inside the company | Offline dry run (no model): rubric 1.0.0 escalated a Confidential file going to the company's own file transfer (D03, score 60) | Rubric 1.1.0 exposure gating, recorded in the changelog; golden set unchanged |
| A realistic low-confidence path | Faking "uncertain" would be dishonest | UC4's real behaviour with its semantic tier unavailable (`max_llm_tier=none`) returns `review_required`, used as a SIMULATED fault |
| Citations that exist but are wrong | Foundry judges: findings cited policy ids for facts about the file or user; our check only verified that ids exist | Agent v2: an evidence id on every pack section; groundedness 23 → 30/30 |
| Foundry eval input formats | Validation failure, then all rows errored, then a judge blind to tool calls | Mapped every supported field, sent tool fields as lists, and sent the agent's whole run; all four runs kept |
| Telemetry dropped spans mid-trace | One live trace was missing its agent and decision spans | The exporter's default 5-traces/s sampler; the shared sink now defaults to `always_on` (helps UC4 and UC6 too) |
| Platform content recording | Foundry Agent Service logs the agent conversation (user id, host) in App Insights | Measured with a canary; owner decision to keep it for the synthetic demo; production options documented |
| Credentials | Live runs need a key or an Entra token | Keys only at hidden prompts in throwaway scripts; Entra browser sign-in; App Insights string fetched in memory from the project |

## 2. Lessons

1. **Composition beats a bigger agent.** The agent got five read-only tools. Classification,
   identity and destination stayed deterministic. That made the system testable (68 UC1 tests) and
   the agent's job small enough to evaluate.
2. **Put the decision where you can version it.** Every outcome is reproducible from rubric 1.1.0
   and mapping 1.0.0. Asking "why ESCALATE?" gets a table, not a paragraph.
3. **Let the model raise, never lower.** Discarding a stricter model view wastes signal; applying
   it bypasses the rubric. Routing it to a person keeps both, and the measured cost is two extra
   reviews (D08, D10).
4. **Semantic judges find what deterministic checks can't, and vice versa.** Foundry's string
   checks reproduced our numbers exactly (trust). Its AI judges found a citation flaw our metric was
   structurally blind to (insight). Neither alone is enough.
5. **Evaluator input fidelity is part of the evaluation.** A judge shown only the final JSON scored
   task adherence 7/30. Shown the whole run, it scored 19/30 for the same agent.
6. **Measure privacy end to end.** Our spans were clean. The platform's own telemetry was not. Only
   a live canary search showed the difference.
7. **Report the misses.** Four cautious misses, kept as they are, tell an interviewer more than a
   tuned 100%.

## 3. Trade-offs

| Decision | Gained | Cost |
|---|---|---|
| Deterministic rubric owns the outcome | Auditability, stability, no prompt-injection path to the decision | Less nuance; known gaps (D02) need a rubric version, not a prompt tweak |
| Agent can only raise | Model caution is never lost or silently applied | Review workload (HUMAN_REVIEW on D08, D10) |
| No document text to the agent | Data minimisation; injection in documents can't steer the agent | The agent relies on UC4's summary; it can't spot a nuance UC4 missed |
| Exception only via the tool | The model can't invent approval | One more tool call per non-corporate case |
| Replay-first | Deterministic CI and demos without Azure | Recordings go stale when prompts change (v2 needed a re-record) |
| Deterministic behaviour bands | Simple, explainable, replaceable (UC2 seam) | Not real anomaly detection |
| Guardrail on the agent only | UC4's deployment is untouched | The chat-completions planner relies on the shared filter |
| Simulated actions | No irreversible harm possible in the MVP | Not a working enforcement point yet |

## 4. Limitations

* **Small, single-author evaluation**: 30 cases, one recording per prompt version, a same-family
  judge.
* **Rubric gap D02** (Public data to personal cloud → WARN) is known and unfixed.
* **UC4 over-classification** (D24) flows through to a review.
* **Paraphrased social engineering** in justifications is not detected by either guardrail layer
  (GU2). Foundry's tool-output shield did not fire on our format (GU7).
* **Foundry content recording** stores evidence packs (user id, host, justification) in App
  Insights for the demo.
* **Behaviour** is deterministic and synthetic; there is no real identity provider, endpoint agent
  or CASB feed.
* **MCP caller identity** over stdio is asserted, not authenticated.
* **Cost** is not estimated (no prices configured). Latency figures are local replay timings.
* The guardrail probes ran on agent v2 (prompt v1), not re-run on v3.

## 5. Next steps (not started)

1. Rubric 1.2.0: `allowed` for Public data cancels destination exposure (fixes D02). Evaluate on a
   **new** held-out set, not this one.
2. Pseudonymise the user id and host in the evidence pack; turn off Foundry content recording
   outside demos.
3. Plug in UC2's behaviour model through `BehaviorProvider`.
4. A real enforcement point behind the approval queue (still human-approved).
5. A second judge model and a human spot-check for the agent-quality evaluation.
