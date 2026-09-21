# UC4 Hybrid routing (Approach D)

> Dataset labels: **AI-generated synthetic dataset — reviewed by one human (provenance per coordinator); second independent review pending.**
> Plan (pre-registered before any code): [`hybrid-plan.md`](hybrid-plan.md). Generated results:
> [`results/hybrid-baseline.md`](results/hybrid-baseline.md). Every number here comes from that file.

## Status in one paragraph

The router, fusion and review logic exist, are tested, and were evaluated on the dev split against
every pre-registered variant, replaying the recorded LLM responses (no provider call). **On this
dataset the recommended hybrid is indistinguishable from the `mid` LLM tier alone:** the LLM is
near-saturated, so routing has no accuracy to add. Its measurable value is (a) safety behaviour when
stages fail or disagree (shown by fault injection, not by the recorded outputs) and (b) a cost/latency
lever (Rules short-circuit) that costs category recall here. Dev chose the recommended variant and
also evaluates it, so its numbers are optimistic; the locked test split has **not** been used.

## Architecture

```
S0 injection scan (event only)
S1 Rules ── sufficient AND short_circuit ───────────────────────────────► S4 fusion
S2 ML (optional) ── calibrated reliability >= tau on all axes, no conflict ─► S4
S3 LLM tiers in order (each at most once, within max_llm_calls):
      accepted = usable AND bucket >= min AND verified AND not "insufficient information"
                 AND no conflict with an accepted Rules/ML result   ─► S4
      otherwise escalate; none accepted ─► review (provisional fail-safe label)
S4 fusion: union of categories (per-category provenance), level from the most authoritative
   semantic stage, Rules level as a floor, category floors, injection restriction, derived high-risk
```

| Piece | File |
|---|---|
| Configuration and the fixed variants | `config/routing/routing.v1.yaml`, `app/classification/routing_config.py` |
| Stage acceptance and conflict tests (pure functions) | `app/classification/router.py` |
| Fusion (pure functions) | `app/classification/fusion.py` |
| Review decision | `app/classification/review.py` |
| The classifier | `app/classification/hybrid.py` |
| Gates | `config/eval/gates.v1.yaml` |
| Report | `evals/classification/hybrid_report.py` (`dataguard-uc4 hybrid report`) |

Rules enforced by the design: the harness, not a model, owns thresholds, escalation, conflicts, review
and floors; a stage that raises or breaks the result contract is skipped and recorded, never
propagated; nothing ever falls back to a low sensitivity (an undecidable document is `review_required`,
with the highest level any stage produced as a provisional label, or no label at all); high-risk is
derived from configuration; the hybrid never abstains silently; only development splits are read.

## Results (held-out dev, T1-T4, 18 independent families; selected rows)

| approach / variant | level macro-F1 | category macro-F1 | HR precision | HR recall | HR FPR | review rate | LLM calls / doc | tokens / doc | P50 (s) | P95 (s) | docs > 10 s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| LLM mid (C) | 0.870 [0.631, 1.000] | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] | 0.000 | 0.000 | 1.00 | 5329 | 2.4 | 3.0 | 0 |
| hybrid `default` **(recommended)** | 0.870 [0.631, 1.000] | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] | 0.000 | 0.000 | 1.00 | 5329 | 2.4 | 3.0 | 0 |
| hybrid `rules_short_circuit` | 0.870 [0.631, 1.000] | 0.958 [0.833, 1.000] | 1.000 | 1.000 [1.000, 1.000] | 0.000 | 0.000 | 0.64 | 3428 | 1.9 | 3.0 | 0 |
| hybrid `small_first` | 0.918 [0.787, 1.000] | 0.932 [0.881, 1.000] | 0.883 | 1.000 [1.000, 1.000] | 0.159 | 0.000 | 1.00 | 5985 | 6.4 | 9.4 | 2 |
| hybrid `ml_stage_50` | 0.773 [0.481, 1.000] | 0.897 [0.820, 0.985] | 0.930 | 1.000 [1.000, 1.000] | 0.091 | 0.000 | 0.66 | 3540 | 2.1 | 3.0 | 0 |
| hybrid `ml_stage_70` | 0.870 [0.631, 1.000] | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] | 0.000 | 0.000 | 1.00 | 5329 | 2.4 | 3.0 | 0 |

Gates on the recommended variant (PRD MVP targets, judged on the point estimate / the lower
family-bootstrap bound; **informational, because dev chose the variant**):

| approach / variant | level F1 (point / lower bound) | category F1 (point / lower bound) | HR recall (point / lower bound) |
|---|---|---|---|
| hybrid `default` | PASS / FAIL | PASS / PASS | PASS / PASS |

## Behaviour when LLM tiers fail (fault injection, dev)

| scenario | variant | review rate | documents with no label | provisional level correct | level macro-F1 | HR precision | HR recall | severely under-classified | LLM calls / doc | P95 (s) |
|---|---|---|---|---|---|---|---|---|---|---|
| mid unavailable for every document | `default` | 0.000 | 0 | 0/0 | 0.870 | 1.000 | 1.000 | 0 | 2.00 | 181.7 |
| mid unavailable for 20% of documents | `default` | 0.000 | 0 | 0/0 | 0.870 | 1.000 | 1.000 | 0 | 1.15 | 87.8 |
| mid and large unavailable for every document | `default` | 1.000 | 69 | 38/38 | 0.280 | 1.000 | 0.623 | 0 | 2.00 | 0.0 |
| every LLM tier unavailable, Rules short-circuit on | `rules_short_circuit` | 0.608 | 69 | 0/0 | 0.280 | 1.000 | 0.623 | 0 | 1.29 | 0.0 |

A failed stage never produces a low default. With both LLM tiers down, the 38 documents Rules can
decide are sent to review with a correct provisional label; the other 69 come back with no label.
Missing labels count as misses, which is why level macro-F1 drops to Rules' level.

## Findings

1. **Sanity check passed:** the pass-through variant `llm_mid_only` reproduces the standalone mid tier
   exactly (identical metrics fingerprint).
2. **Hybrid `default` equals `mid` alone on dev** (same level/category F1, precision, recall, tokens
   and latency). The Rules floor, category floors, conflict handling, escalation and injection
   restriction never fired on the recorded outputs: 0 floor raises, 0 conflicts, 0 escalations, 0
   reviews. They are validated by unit tests and fault injection, not by real disagreement.
3. **Rules short-circuit** decides 38 of 107 documents without an LLM (0.64 LLM calls per document,
   about 36% fewer tokens), but Rules recall on those documents is 0.884 against 1.000 for `mid`, so
   category macro-F1 falls from 1.000 to 0.958. The selection rule eliminates it as dominated.
4. **The ML stage does not help.** No tau of 0.7 or above ever accepts an ML decision; at 0.5 it
   decides 77 of 107 documents with 45% level accuracy and drags level macro-F1 to 0.651. ML's
   calibrated probabilities are not reliable enough to route on.
5. **Small-first saves nothing:** `small` says "high" confidence for every document, so it is never
   escalated and the variant equals `small` alone (precision 0.883, FPR 0.159). Verbalized confidence
   cannot route small-to-larger here; only `large` uses a lower bucket.
6. **Tokens per document are about the same for every tier (5.3-6.0k)**, so the tier choice is a
   latency/price question; prices were not supplied.
7. **Latency:** `default` P95 is 3.0 s (recorded), inside the 10 s tool-call limit; anything routing
   through `large` misses it.

## Recommendation (pre-registered rule; a recommendation, not a decision)

`default` (Rules floor on, no short-circuit, tiers `[mid, large]`, all safety mechanisms on). It is the
cheapest non-dominated variant, and dev cannot separate it from six others with identical metrics
(`no_rules_floor`, `no_category_floors`, `strict_confidence`, `no_injection_restriction`,
`ml_stage_70`, `ml_stage_90`), so ties resolve by configuration order, which puts the fully protected
`default` first. The thresholds in `routing.v1.yaml` (`min_confidence: medium`, ML `tau: 0.7` with ML
off) are defaults, not validated operating points: no minimum precision has been approved, and dev
gave no discriminating signal for them.

## Clarifications to the pre-registered plan

* `llm_mid_only` is configured as a pure pass-through (no floors, lowest confidence floor,
  abstentions accepted) so that "must reproduce standalone mid" is testable. It is a sanity check and
  excluded from the recommendation.
* The call budget may be smaller than the number of tiers (it caps escalation depth); the config
  validator originally forbade that, which made `BUDGET_EXHAUSTED` unreachable.
* Latency in the report is the sum of the LLM stages' RECORDED latencies. Local Rules/ML time is
  milliseconds and is excluded so the report is reproducible (a first version let live microseconds
  decide a tie-break).
* The optional "second opinion" for injection-flagged documents was not implemented.
* A bug found by a test and fixed: fusion keyed merged evidence by its original id, so `rules.e1` and
  `llm:mid.e1` collided and one was dropped.

## What was NOT done, and why

| Item | State |
|---|---|
| Locked-test evaluation of the frozen variant | **Done once, 2026-09-21** ([`results/hybrid-locked-test.md`](results/hybrid-locked-test.md)): level macro-F1 0.884 [0.758, 0.980] (point PASS, lower bound FAIL), category 0.997, high-risk recall 1.000 (111 of 111); 19 level errors, all one rank low in four ambiguous families. Report-only; not repeatable |
| Out-of-sample check on calibration | **Done 2026-09-21** ([`results/hybrid-calibration-check.md`](results/hybrid-calibration-check.md)): level macro-F1 0.867 [0.717, 1.000], category 1.000, **high-risk recall 0.891** (49 of 55; all 6 misses in one ambiguous family, `amb_aggregate_health_stats`). 103 `mid` responses recorded; large never called |
| Hybrid on train/calibration | No recorded LLM responses there; those documents route to review (`replay_miss`) |
| Injection second opinion | Not implemented |
| Observability export, service surface, MCP | Phases 7-8 |

## Reproduce

```
dataguard-uc4 eval run --classifier hybrid --hybrid-variant default --split dev
dataguard-uc4 hybrid report --out docs/uc4/results/hybrid-baseline.md
```

## Limits

Synthetic, AI-authored, template-generated labels not yet human reviewed; 18 dev families; dev used
for selection; single recorded samples with no temperature on two tiers; nothing transfers to real
data without validation; the locked test split was evaluated once for the frozen hybrid (2026-09-21, [`results/hybrid-locked-test.md`](results/hybrid-locked-test.md)) and is now consumed.
