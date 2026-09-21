# UC4 completion report (v0.1)

> **AI-generated synthetic dataset — pending human gold-label review.** Status as of 2026-09-21. This report says what is finished, what the evidence is, and what is still open, so "done" is not overstated.

## Verdict

**The v0.1 implementation is complete. UC4 is not validated.** Everything in the approved plan (Phases 0-8), plus the MCP adapter and an offline dashboard, is built, tested and merged. What remains is not code: it needs independent human judgement and, for one item, an Azure connection string.

| Completion criterion | State |
|---|---|
| The approved v0.1 scope is implemented and tested (Rules, ML, LLM, hybrid, observability, service, schema) | **Met** |
| Evaluated honestly on every split, with nothing tuned on the locked test split | **Met** (dev, calibration live and out-of-sample, locked test once, report-only) |
| CI green on Python 3.11 and 3.12 | **Met** (PR #11: 22 of 22 checks) |
| Gold labels independently reviewed by humans | **Not met** (see below) |
| PRD level macro-F1 gate (>= 0.85) on the lower confidence bound | **Not met** (locked test lower bound 0.758) |
| MCP adapter release-ready | **Not met** (freeze criteria: 1 of 3) |

## What was delivered

* **Classification service** (Python API and CLI, frozen result schema v1.0): Rules, supervised ML, three LLM tiers, and a deterministic hybrid router with fusion and review escalation; explicit confidence contract; input and injection guardrails.
* **Evaluation harness** validated with oracle, majority and random baselines (311 checks), family-level bootstrap intervals, hard protection of the locked test split (one audited run, logged).
* **Observability**: redacted spans, privacy gate, failure-injection suite covering every failure row of the architecture, an offline dashboard.
* **MCP adapter** `classify_document` (`mcp_adapter/`, `dataguard-uc4-mcp`): deny-by-default allowlist, per-caller caps, spoofable inputs rejected, size limit, evidence off by default.
* **Review tooling**: blind-review packages (content-only, metadata-shown, and Round 2), comparison reports, AI/human labelling and provenance notes.
* **1299 tests** pass locally; CI runs the same suite plus baseline reports and a docs-integrity check.

## Results (frozen hybrid `default`, headline T1-T4)

| | locked test (once) | calibration | dev (chose the variant) |
|---|---|---|---|
| level macro-F1 | 0.884 [0.758, 0.980] | 0.867 [0.717, 1.000] | 0.870 [0.631, 1.000] |
| category macro-F1 | 0.997 | 1.000 | 1.000 |
| high-risk recall | 1.000 (111/111) | 0.891 (49/55) | 1.000 |

Sources: `results/hybrid-locked-test.md`, `results/hybrid-calibration-check.md`, `results/hybrid-baseline.md`. The level shortfall is one pattern: gold `CONFIDENTIAL` predicted `INTERNAL` in ambiguous families whose gold is itself a tie-break. Whether those golds are right is a human policy question.

## What is still open, why, and who can close it

| # | Item | Why it is not done | Who / what closes it |
|---|---|---|---|
| 1 | **Independent human review of the gold labels** | The Round 1 sheet designated as human was identical to an AI sheet on all 33 rows, so no independent human has reviewed any gold label. An AI cannot supply this. | Two independent reviewers using the Round 2 package: `human-review-round2.md` |
| 2 | **`amb_aggregate_health_stats` label** (gold HIGHLY_CONFIDENTIAL; the model says INTERNAL; all 6 calibration high-risk misses) | A policy question (small-cell de-identified health statistics), never in a blind package before Round 2 | Reviewer input, then a decision by a policy owner |
| 3 | **Level-F1 lower bound** | Concentrated in four ambiguous families (three of them locked-test, so they need a separate post-hoc review); changing golds or the model after seeing the test split would be tuning on it | Outcome of items 1-2; re-score under old and new labels, side by side |
| 4 | **MCP release-readiness** | Audited confirmation done; eval gates not met; human review not satisfied | Items 1-3, then re-check the freeze criteria in `mcp-contract.md` |
| 5 | **Azure Monitor export** | No Application Insights connection string | An Azure resource owner |
| 6 | A **shared live dashboard** (DG-018) and the optional **prompt-injection second opinion** | Shared platform work / an unmeasured LLM feature (decision A8) | Product decision; not advised for v0.1 |
| 7 | The **locked test split is consumed** | One audited run was made | A fresh, newly generated held-out split, if an unbiased re-confirmation is needed |

## Limits that apply to everything above

Synthetic, template-generated data; labels are AI-authored and unreviewed; the headline rests on 18 (dev, calibration) and 37 (test) independent families; nothing transfers to real data without validation; large-tier LLM calls take about 78 s (up to about 245 s), so a call that escalates does not meet the PRD's 10 s limit.

## Definition of done from here

UC4 may be called validated when: (a) two independent humans have reviewed the labels and every disagreement is adjudicated and recorded (then, and only then, does "pending human gold-label review" change); (b) the frozen hybrid is re-scored under the reviewed labels with both label sets reported; (c) the MCP freeze criteria are re-checked and either met or the gap is accepted in writing by the product owner.

## Reproduce

```
pip install -e ".[dev,mcp]" && pytest && dataguard-uc4 config validate && dataguard-uc4 dataset validate
dataguard-uc4 eval run --classifier hybrid --hybrid-variant default --split dev --llm-mode replay
```

Recorded LLM responses under `data/llm_cache/` make the dev, calibration and test runs replayable without credentials.
