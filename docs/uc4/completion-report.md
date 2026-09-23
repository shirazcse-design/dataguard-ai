# UC4 completion report (v0.1)

> **AI-generated synthetic dataset — reviewed by one human (provenance per coordinator); second independent review pending.** Status as of 2026-09-21. This report says what is finished, what the evidence is, and what is still open, so "done" is not overstated.

## Verdict

**UC4 v0.1 is complete: implemented, evaluated, reviewed by one human, and the MCP adapter is release-ready for the v0.1 scope (decision A33). It is not independently validated, and the strict level-F1 gate still fails on its lower bound.**

| Completion criterion | State |
|---|---|
| The approved v0.1 scope is implemented and tested (Rules, ML, LLM, hybrid, observability, service, schema, MCP adapter, dashboard) | **Met** |
| Evaluated honestly on every split, nothing tuned on the locked test split | **Met** (dev; calibration, live and out-of-sample; locked test once; a post-hoc replay under the reviewed labels) |
| CI green on Python 3.11 and 3.12 | **Met** at the last merge (22 of 22 checks) |
| Gold labels reviewed by a human | **Met to the extent one reviewer allows** (Round 2: one reviewer, provenance per coordinator; accepted by the product owner as decision A29). A second independent review is still pending. |
| PRD level macro-F1 gate (>= 0.85), lower confidence bound | **Met under the adopted lenient gate** (decision A33; locked test and calibration 1.000 [1.000, 1.000]). **Strict: not met** (locked test 0.758). The adoption is a product decision, not a measurement. |
| PRD category macro-F1 gate and high-risk recall reference | **Met** on dev, calibration and test |
| MCP adapter release-ready | **Yes, for the v0.1 scope** (all freeze criteria met: audited confirmation, human review accepted A29, eval gates under A33). Read "What release-ready means" in `mcp-contract.md`: not authenticated over stdio, placeholder allowlist, no rate limiting, LLM tier capped at `mid`. |

## What was delivered

* **Classification service** (Python API and CLI, frozen result schema v1.0): Rules, supervised ML, three LLM tiers, and a deterministic hybrid router with fusion and review escalation; explicit confidence contract; input and injection guardrails.
* **Evaluation harness** validated with oracle, majority and random baselines (311 checks), family-level bootstrap intervals, a lenient level view reported beside the strict headline, hard protection of the locked test split (one audited evaluation, one audited post-hoc replay, both logged).
* **Observability**: redacted spans, privacy gate, failure-injection suite covering every failure row of the architecture, an offline dashboard.
* **MCP adapter** `classify_document` (`mcp_adapter/`, `dataguard-uc4-mcp`): deny-by-default allowlist, per-caller caps, spoofable inputs rejected, size limit, evidence off by default.
* **Review tooling**: three blind-review packages (content-only, metadata-shown, Round 2), comparison reports, AI/human labelling and provenance notes.
* **Responsible AI** (`responsible-ai.md`): scoped HHH/APF over Azure Foundry-produced results, an Azure AI Content Safety second-opinion guardrail (Prompt Shields, Groundedness Detection), and a Fairness & Inclusion counterfactual probe (66/66 dev-split documents invariant under a name swap, rules mode).

## Results (frozen hybrid `default`, headline T1-T4; labels as reviewed, decisions A29-A32)

| | strict level F1 | lenient level F1 | category F1 | high-risk recall |
|---|---|---|---|---|
| locked test (37 families; first run, then a post-hoc replay) | 0.884 [0.758, 0.980] | 1.000 [1.000, 1.000] | 0.997 | 1.000 (111/111) |
| calibration (18 families; out-of-sample) | 0.859 [0.685, 1.000] | 1.000 [1.000, 1.000] | 1.000 | 1.000 |
| dev (18 families; chose the variant) | 0.870 [0.631, 1.000] | 0.870 [0.631, 1.000] | 1.000 | 1.000 |

Sources: `results/validation-rescore.md` (side by side with the old labels), `results/hybrid-locked-test.md`, `results/hybrid-calibration-check.md`. On test and calibration every remaining strict level error is a disagreement inside the gold's own acceptable alternatives (the four test-family alternatives date from dataset creation, before any model ran). On dev, 5 genuine model errors remain (`hn_public_api_docs_placeholder_keys`, gold PUBLIC predicted INTERNAL; independent readers labelled it PUBLIC).

## What is still open, why, and who can close it

| # | Item | Why it is not done | Who / what closes it |
|---|---|---|---|
| 1 | **A second independent human review** of the gold labels | One reviewer so far; independence is the coordinator's statement | A second reviewer using the Round 2 package (`human-review-round2.md`); the label wording then changes |
| 2 | The **5 dev errors** in `hn_public_api_docs_placeholder_keys` | Genuine model errors (the models are not shown the `source_system` metadata by design); **not** covered by decision A33 | A product/model decision; not tuned on dev here |
| 3 | The four ambiguous locked-test families have **not been human-reviewed** | The blind generator refuses locked-split documents | A separate, explicitly authorised, labelled post-hoc package |
| 4 | **Azure Monitor export — portal confirmation** | The SDK reports success (no error, `force_flush` completed) against a real Application Insights resource; nobody has yet confirmed the traces landed in Transaction search | Anyone with portal access; 2 minutes |
| 5 | A **shared live dashboard** (DG-018) | Shared platform work | Not advised for v0.1 |
| 6 | The **locked test split is consumed** | Evaluated once and replayed once | A freshly generated held-out split, if an unbiased re-confirmation is needed |
| 7 | **Azure AI Content Safety second opinion — live verification** | Code-complete and fake-server tested; needs a real Content Safety resource (same pattern as Application Insights) | Anyone who can create the resource; `dataguard-uc4 guardrails azure-check` afterward |
| 8 | The **Fairness & Inclusion probe** has only been run in `rules` mode | Covering the ML/LLM stages needs live calls (`--llm-mode record`/`foundry`); `replay` is refused because a name-swapped document always misses the replay cache and would falsely look like a mass fairness finding (confirmed while building this) | A live Foundry key, used deliberately on a small subset |

## Limits that apply to everything above

Synthetic, template-generated data; labels are AI-authored and reviewed by one person; the headline rests on 18 (dev, calibration) and 37 (test) independent families; alternatives were authored by the same AI as the gold; `1.000 [1.000, 1.000]` means no errors on a small dataset, not proven quality; nothing transfers to real data without validation; large-tier LLM calls take about 78 s (up to about 245 s), so a call that escalates does not meet the PRD's 10 s limit.

## Definition of done from here

UC4 is complete for v0.1 and the MCP adapter is release-ready under decision A33. It may additionally be called **independently validated** when: (a) a second independent human has reviewed the labels and every disagreement is adjudicated and recorded (then, and only then, does the dataset label drop "second independent review pending"); (b) the 5 dev errors in `hn_public_api_docs_placeholder_keys` are addressed or accepted in writing; (c) the adopted lenient gate is re-confirmed on a freshly generated held-out split, since the locked test split is consumed.

## Update (2026-09-23): Azure AI Foundry Responsible AI, and a real agentic component

Two additions since the verdict above, both scoped and decided by the product owner, neither
changing the verdict or the frozen hybrid `default` results:

* **Azure AI Foundry Evaluations/Guardrails/Observability and the Responsible AI pillar mapping**
  (`responsible-ai.md`, decisions A34-A36): scoped HHH/APF (dropping the tool/autonomy sub-measures
  that do not apply to a non-agentic classifier), an Azure AI Content Safety audit-time second
  opinion, and a Fairness & Inclusion counterfactual probe (66/66 dev-split documents invariant,
  rules mode).
* **The Batch Triage Agent** (`agent-plan.md`, `agent-engine.md`, decision A37): a new, separate,
  genuinely agentic component — real tools, a real bounded per-document loop, its own real
  (not scoped-away) HHH/APF. `ClassificationService` itself is unchanged and is still not an
  autonomous agent. Run on the real dev split (`--agent-mode mock`, no Foundry credentials): task
  completion 1.0, safety-invariant compliance 1.0 (structural, proven by adversarial tests), APF
  composite 0.833. A live Foundry planner run (`--agent-mode foundry`) was smoke-tested on 3
  documents (2026-09-23, decision D9.29) and held the safety invariant on real output; not scaled
  to a full-split run or recorded as a report — see `agent-engine.md`'s Limits.

## Reproduce

```
pip install -e ".[dev,mcp]" && pytest && dataguard-uc4 config validate && dataguard-uc4 dataset validate
dataguard-uc4 eval run --classifier hybrid --hybrid-variant default --split dev --llm-mode replay
```

Recorded LLM responses under `data/llm_cache/` make the dev, calibration and test runs replayable without credentials.
