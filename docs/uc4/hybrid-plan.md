# UC4 Hybrid routing (Approach D): pre-registered plan

**Status: committed BEFORE any router, fusion or review code is written.** It fixes the design, the
variants to be compared, the selection rule and the reporting rules, so results cannot shape them.
Deviations go in the results document.

> Dataset labels: **AI-generated synthetic dataset — pending human gold-label review.**

## Scope

Approved: Phase 6 (architecture plan sections 11, 13, 20): router, fusion, review logic, threshold
derivation on dev, ablations, and gate evaluation. **Not approved and not done here:** a
locked-test-split evaluation (the plan lists "one locked-test report"; you have not authorised it, so
the capability is built and the run is not made), observability/OTel export (Phase 7), the service
surface, MCP, RAG, agents, UI. No new provider calls: every LLM request the hybrid makes is one the
standalone LLM classifier already made, so it is replayed from `data/llm_cache` on the dev split.
Hybrid cannot be evaluated on train or calibration (no recorded LLM responses there); those
requests return `replay_miss` and route to review, and CI runs dev only.

## Design (recommendations from the approved architecture; the hybrid is deterministic harness logic)

```
S0 guardrails: injection scan (event only)
S1 Rules ── sufficient AND short_circuit enabled ────────────────────────────────► S4 fusion
S2 ML (optional) ─ calibrated reliability >= tau on ALL axes, no conflict ───────► S4
S3 LLM tiers in the configured order, each at most once, within the call budget:
     accepted = usable output AND confidence bucket >= min AND evidence not unverified
                AND not "insufficient information" AND no conflict with an accepted upstream stage
     otherwise escalate to the next tier;  none accepted / budget exhausted / failures ─► review
S4 fusion (floors, union, high-risk derived) ─► result
```

* **Rules sufficiency:** the level is decisive (not abstained) with rule strength >= `min_level_strength`.
  A Rules abstention is never "Public"; it means "escalate".
* **ML acceptance:** calibrated top level probability >= tau, and for every category
  `max(p, 1-p) >= tau`. Uncalibrated scores are never accepted.
* **LLM acceptance** uses the model's verbalized bucket, which is not a probability; how far to trust
  it is a measured question (reliability table), not an assumption.
* **Conflict** (architecture section 11): two accepted stages disagree on `high_risk`, or on the level
  by MORE than one rank. A conflict escalates to the next LLM tier; a conflict that survives every tier
  goes to review.
* **Fusion:** categories are the union of accepted categories, each with per-category provenance. The
  level is that of the most authoritative accepted semantic stage (LLM, then ML, then Rules). Then
  (each toggleable and benchmarked): the Rules level is a floor that is never lowered; the level is
  raised to the highest floor of the fused categories. High-risk is derived from configuration, never
  predicted.
* **Injection:** a flagged document may have its level RAISED but not LOWERED by the LLM relative to
  the accepted Rules/ML level; an attempted lowering keeps the higher level and requests review
  (`INJECTION_DOWNGRADE_ATTEMPT`). Optionally a flagged document gets a second opinion from an unused
  tier, and the higher level wins; disagreement requests review.
* **Review** (section 13 codes): `review_required` with reason codes, a **provisional** label (the
  fail-safe fusion of every stage that produced a level: highest level, union of categories) and a
  priority (provisional high-risk first). The service only flags; it never blocks.
* **Failures are values.** A stage that fails is skipped and recorded; if no stage decides, the result
  is `review_required` with no level (`LLM_UNAVAILABLE`), never a default sensitivity.
* **Budget:** `max_llm_calls` per document (no prices exist, so cost is counted in calls and tokens);
  exhaustion routes to review (`BUDGET_EXHAUSTED`).
* **Hybrid never abstains silently:** `routing.abstained` is false; an undecided document is a review.

## Variants compared (fixed now)

| Variant | Differs from `default` |
|---|---|
| `llm_mid_only` | Rules and ML off, tiers `[mid]`. **Sanity check:** must reproduce the standalone `mid` predictions exactly |
| `default` | Rules floor on, no short-circuit, ML off, tiers `[mid, large]`, conflict escalation, injection restriction on |
| `rules_short_circuit` | Rules sufficiency short-circuits the LLM |
| `no_rules_floor` | Rules level is not a floor |
| `no_category_floors` | No level raise from category floors |
| `small_first` | Tiers `[small, mid, large]` |
| `small_mid` | Tiers `[small, mid]` |
| `large_only` | Tiers `[large]` |
| `strict_confidence` | LLM buckets below `high` are not accepted |
| `no_injection_restriction` | Ablates the raise-not-lower rule |
| `ml_stage_50`, `ml_stage_70`, `ml_stage_90` | ML on with tau 0.5, 0.7, 0.9 (may short-circuit) |

## Threshold derivation and selection rule (fixed now)

No minimum precision has been approved, so no threshold is derived by targeting one. Instead:
* **Thresholds are read off the measured trade-off**: the report tabulates, for the ML tau grid and the
  LLM bucket floor, coverage (share of documents the stage decides), the accuracy of its accepted
  decisions, and its conflicts, next to what the next stage would have done on the same documents.
* **Recommended variant (a recommendation, not a decision):** (1) keep variants with high-risk recall
  >= 0.90 (the informational reference) AND zero severe under-classification (Highly Confidential
  predicted as Public/Internal) AND no provider failures; (2) drop variants dominated on
  (level macro-F1, category macro-F1, high-risk precision, high-risk recall), treating differences of
  <= 0.02 as ties; (3) among what remains choose the lowest mean tokens per document, then the lowest
  P95 latency, then the fewest stages. The product owner chooses the operating point.
* **Dev is used for BOTH selection and evaluation**, so the recommended variant's dev numbers are
  optimistic by construction. The report says so beside every such number. The honest confirmation is a
  single audited report-only run on the locked test split with the frozen variant, which needs your
  explicit approval (`--allow-locked-test`); an alternative is recording the calibration split's LLM
  responses (needs credentials again) as an out-of-sample check.

## Gates (evaluated honestly, pass or fail)

`config/eval/gates.v1.yaml`: the PRD MVP targets as approved earlier: **level macro-F1 >= 0.85 and
category macro-F1 >= 0.85 (two separate gates, no combined score)** and **high-risk recall >= 0.90**,
which is informational and never sufficient alone. Each gate is reported PASS/FAIL on the point
estimate and on the lower family-bootstrap bound. Passing on dev is NOT a release gate: dev was used to
choose the variant.

## Reporting

Same harness, headline (T1-T4), family-level intervals and slices as every approach. Per variant: the
headline metrics with precision/FPR beside recall; deferral views (coverage, auto-decided metrics,
deferrals as errors, deferrals resolved by a perfect reviewer, labelled hypothetical); which stage
decided each document; LLM calls and tokens per document; recorded latency (sum of stages, replayed
from recording) against the 10 s tool-call limit; review reasons and the quality of provisional
labels; T4 hard negatives; T5 injection slice; conflicts; per-family errors for the recommended
variant. Unpriced, so cost is calls and tokens.

## Expectations stated in advance

* Hybrid will look much like `mid` alone: the LLM is near-saturated on this dataset, so there is
  little for routing to add in accuracy. Its value is in cost/latency (short-circuit) and safety
  (floors, conflict review, injection restriction).
* The ML stage will almost never pass a meaningful tau (its level probabilities sit at 0.4-0.7) and
  will not help.
* Rules short-circuit will lose category recall on mixed documents (a rule fires on one category and
  hides a semantic one) and may not save much because Rules abstain on about half the documents.
* The Rules floor may raise the "STRICTLY CONFIDENTIAL lunch menu" hard negative (a marking decoy).
* Tiny dev sample and saturated scores mean most variant differences are within noise.

## Limits stated in advance

Synthetic, AI-authored, template-generated labels not yet human reviewed; 18 dev families; dev used
for selection; replayed single-sample LLM outputs with no temperature on two tiers; nothing transfers
to real data without validation; the locked test split has never been evaluated.
