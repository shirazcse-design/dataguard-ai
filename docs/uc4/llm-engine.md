# UC4 LLM classifier (Approach C)

> Dataset labels: **AI-generated synthetic dataset — pending human gold-label review.**
> Plan (pre-registered before any code): [`llm-plan.md`](llm-plan.md) (deviations are listed below).
> Generated results: [`results/llm-baseline.md`](results/llm-baseline.md).

## Status

The whole LLM path exists, is tested (mock/replay/local fake server), and has now been **run against
the project's three deployments on the dev split**. The generated report is
[`results/llm-baseline.md`](results/llm-baseline.md); every number below comes from it.

| tier | deployment | served model (provider-reported) | level macro-F1 | category macro-F1 | HR precision | HR recall | HR FPR | quotes verified | P50 latency (s) | tokens / doc |
|---|---|---|---|---|---|---|---|---|---|---|
| small | uc4-llm-small | gpt-5-mini-2025-08-07 | 0.918 [0.787, 1.000] | 0.932 [0.881, 1.000] | 0.883 | 1.000 [1.000, 1.000] | 0.159 | 1.000 (194/194) | 6.4 | 5985 |
| mid | uc4-llm-medium | gpt-5.4-2026-03-05 | 0.870 [0.631, 1.000] | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] | 0.000 | 1.000 (169/169) | 2.4 | 5329 |
| large | uc4-llm-large | uc4-llm-large (provider echoes only the deployment name) | 0.870 [0.631, 1.000] | 0.990 [0.958, 1.000] | 1.000 | 1.000 [1.000, 1.000] | 0.000 | 1.000 (162/162) | 78.3 | 5896 |

All three tiers returned schema-valid output for 107/107 dev documents (no provider failures, no
review cases), and every evidence quote was found in the input. Read this table with the caveats
below: **near-perfect scores on this dataset should not be taken as evidence of real-world accuracy.**

## Architecture

```
document ─► injection scan (event only) ─► prompt (system: config-generated taxonomy + rules + 13 train
             few-shot examples;  user: filename + document between per-document boundary markers)
          ─► LLMClient.complete_structured ── retry (timeout / 429 / 5xx only, backoff + jitter)
          ─► strict JSON parse  ── invalid? ONE repair retry ── still invalid? review_required, NO level
          ─► evidence verification (quote must be in the text sent) ─► masked excerpts
          ─► confidence caps, high-risk derived from config, review triggers ─► ClassificationResult
```

| Piece | File |
|---|---|
| Provider interface, request/response, error kinds | `app/llm/types.py` |
| Adapters: mock (tests), replay/record cache, Foundry (HTTP) | `app/llm/{mock,replay,foundry}.py` |
| Retry policy | `app/llm/retry.py` |
| Config (tiers, generation, retry, evidence, prices) | `config/llm/llm.v1.yaml`, `app/llm/config.py` |
| Prompt assembly | `prompts/uc4/classifier.v1.md`, `app/llm/prompting.py` |
| Few-shot selection and loading | `app/llm/fewshot.py`, `prompts/uc4/fewshot.v1.json` |
| Output schema, verification, masking | `guardrails/output.py` |
| Injection scan | `guardrails/injection.py`, `config/guardrails/injection.v1.yaml` |
| Classifier | `app/llm/classifier.py` |
| Report | `evals/classification/llm_report.py` |

## Rules the design enforces

* **No model names anywhere.** A tier (`small|mid|large`) names an environment variable that holds the
  deployment name. Prices are `null` until a dated price is entered; without one, cost is "not
  estimated".
* **The document is untrusted data.** It sits between markers containing a token derived from
  `sha256(seed : content_hash)`: replayable, but a document cannot contain its own token. It never
  enters the system prompt, and placeholders inside it are never substituted (single-pass fill).
* **Embedded labels and metadata are not shown to the model** (spoofable; same rule as the ML model).
  The filename is, collapsed to one line.
* **Few-shot examples are train documents only**, chosen by the pre-registered rule, stored as ids and
  hashes; the loader refuses anything that is not a train document with the recorded hash, and a test
  checks the committed file equals what the rule produces. No example family appears in calibration
  or dev.
* **Output is validated strictly** (no type coercion: `"no"` is not `false`), with labels from the
  taxonomy. A test found that pydantic's default mode accepted `"no"` for a boolean; the models are now strict.
* **Evidence:** a quote is *verified* only if it occurs in the text actually sent (exact or up to
  whitespace). Verified quotes are `observed` with a locator; unverified quotes are `inferred` and cap
  confidence (`medium` if some fail, `low` if all fail). An unverified high-risk call requests review
  (`EVIDENCE_UNVERIFIED`). Excerpts are masked (digit runs, emails, long tokens) so results never
  carry raw identifiers.
* **Confidence is a verbalized bucket only, never calibrated.** How far to trust a bucket is answered
  only by the measured reliability table in the report.
* **High-risk is derived from configuration**, never taken from the model (an extra field in the
  model's output is rejected).
* **Failures are values, never silent defaults.** Provider failure after bounded retries, or output
  that is still malformed after one repair retry, returns `review_required` (`LLM_UNAVAILABLE`) with
  no level. Empty input is `rejected`. Nothing falls back to Internal or Public.
* **Replay never guesses.** A missing entry is `replay_miss` (an error the harness records), and the
  cache key is `(prompt_version, model_id, input_hash)`; the hash covers system, user, schema,
  temperature and token cap.
* **The locked test split is not reachable:** the classifier loads only development splits.

## What was measured (model-independent; see the generated report)

* Prompt sizes: the fixed system prompt is about 20.7k characters; dev user messages have a median of
  539 and a maximum of 727 characters. No dev document is truncated. Tokens are not estimated.
* Few-shot leakage: 13 examples, all from train, none sharing a family with calibration or dev.
* **Injection guard (lexicon):** developed on train only.

| Guardrail version | train T5 (developed on) | calibration T5 (held out) | dev T5 (held out) | false positives on T1-T4 (train / cal / dev) |
|---|---|---|---|---|
| 1.0.0 (first measurement) | 17/25 | 1/5 | 3/10 | 0/391, 0/98, 0/97 |
| 1.0.1 (patterns added for train misses) | 25/25 | 1/5 | 3/10 | 0/391, 0/98, 0/97 |

Version 1.0.1 added general patterns for phrasings seen in train misses (bypass rules, "guardrails
are disabled", claimed authority, "[assistant instruction]", label dictation without a connector).
Nothing was derived from calibration or dev, but the dev attack-type names appeared in the first
report, so treat later dev numbers as no longer blind. **The lexicon generalises poorly:** it caught
none of the dev "upgrade" attacks and 3 of 5 "exfiltration" attacks, attack types absent from train.
Five T5 families in train and 3-4 in dev make these rates anecdotal. The guard is a supplement; the
delimiting and the prompt rules are the primary defence, and whether they work can only be measured
with a real model.

## Recording log, verification and deviations from the pre-registered plan

Recorded 2026-09-19 against a Foundry resource, prompt `classifier.v1`, dev split only (107
documents per deployment), 4,000 output-token cap, records committed under `data/llm_cache/`.
* `small` and `mid`: sequential runs (`eval run --classifier llm --llm-mode record`).
* `large`: a concurrent driver (8 workers) in record mode, because a single call took about 87 s
  and a sequential run would have taken hours; its recorded latencies may include queueing. The
  official numbers come from a sequential **replay** of the recorded responses and reproduce the
  live runs' metrics fingerprints for `small` and `mid` exactly.
* The adapter was verified live: the v1 route with the deployment name in `model`; a project
  endpoint reduced to its resource host; `api-key` header. Probes on a trivial, document-free
  prompt found deployment quirks now recorded in `config/llm/llm.v1.yaml`: `small` rejects a
  temperature of 0 and `large` rejects the parameter altogether (both reasoning models), so neither
  sends one; `large` supports only the Responses API. Their runs are therefore not guaranteed
  reproducible by re-calling the model; the replay cache is the reproducible record.
* Deviations from `llm-plan.md`: output cap 700 to 4000 tokens (reasoning tokens count against it);
  timeout 10 s to 300 s (the reasoning deployments exceed 10 s, so 10 s made calls fail); temperature
  omitted on two tiers. The prompt and few-shot set were not changed after seeing any dev result.
* The provider reports `gpt-5-mini-2025-08-07` and `gpt-5.4-2026-03-05` as the served model for
  `small` and `mid`; for `large` it echoes only the deployment name.

## Findings (dev; see the generated report for every number)

1. **Structured output and evidence are reliable here:** 107/107 schema-valid per tier and 100% of
   quotes verified. The verification and masking code was therefore not stressed by real
   hallucination on this dataset.
2. **Categories are near-saturated for `mid` (F1 1.000) and `large` (0.990); `small` is at 0.932**
   with its errors concentrated in one T2 family where it added Trade Secret (and Highly
   Confidential) to Intellectual Property documents, exactly the IP-versus-Trade-Secret overlap the
   guidelines call out. Its high-risk precision is 0.883 (FPR 0.159).
3. **The single remaining level error for `mid` and `large` is one T4 family**
   (`hn_public_api_docs_placeholder_keys`, gold PUBLIC, predicted INTERNAL for all 5 documents). With
   10 PUBLIC documents in dev this alone caps level macro-F1 at 0.870. It is a candidate for the
   human gold-label review.
4. **Verbalized confidence is informative for one tier only.** `small` and `mid` say `high` for all
   107 documents (so their reliability tables are degenerate: `small` is right on 92.5% of levels,
   `mid` on 95.3%). `large` uses `medium` on 15 documents, which are correct 66.7% (10/15) of the
   time versus 100% (92/92) for its `high` calls. That is a real signal, but from one tier and 15
   documents, and the buckets are never calibrated probabilities. Do not use them alone as a routing
   signal.
5. **Prompt injection:** 0 of 10 dev injection documents were under-classified by any tier, while the
   local lexicon flagged only 3 of them. Two families; anecdotal.
6. **Latency misses the PRD's 10 s tool-call limit for `large` (every call; P50 78 s) and for `small`
   at the tail (P95 9.4 s, 2 of 107 over 10 s); `mid` is well inside (P50 2.4 s, max 3.7 s).**
7. **Token cost per document is about 5.3-6.0k tokens, dominated by the 20.7k-character system
   prompt (13 few-shot examples).** Prices were not supplied, so no cost is estimated.

## Caveats that matter more than the scores

* **Optimism / circularity.** The dataset was authored by an AI following the same labeling
  guidelines that the prompt states (level procedure, overlap rules, "choose the higher level" tie
  break). Documents are short, templated and semantically explicit. A strong LLM can recover that
  logic. These results say little about real, messy documents, and the gold labels are still
  pending human review.
* 18 dev families; per-tier intervals are wide and category supports are small (SMALL_SAMPLE).
* One sample per document; two tiers have no temperature.
* Rules were developed against dev; the LLM prompt was not tuned on dev, but the prompt does encode
  the labeling guidelines.
* The locked test split has never been evaluated.

## What was NOT done, and why

| Item | State |
|---|---|
| Calibration of LLM confidence | Not possible from verbalized buckets (all `high`); no logprob calibration was attempted |
| Foundry content-safety supplement | Not started; optional |
| Pre-LLM redaction option | Deferred; its effect can now be measured, but was not in scope |
| Injection "raise but not lower" rule | Phase 6: it needs Rules/ML outputs |
| Hybrid routing, fusion, thresholds, locked-test report | Phase 6, not approved |
| Cost estimate | Prices not supplied |

## To run or re-record a tier

```
export DATAGUARD_FOUNDRY_ENDPOINT=...   # resource or project endpoint
export DATAGUARD_FOUNDRY_API_KEY=...    # never commit; use a secret store in CI
export DATAGUARD_LLM_DEPLOYMENT_SMALL=<deployment>   # likewise _MID and _LARGE
dataguard-uc4 eval run --classifier llm --llm-tier small --llm-mode record --split dev
dataguard-uc4 llm report --tier small=<deployment> --tier mid=<deployment> --tier large=<deployment> \
    --out docs/uc4/results/llm-baseline.md
```

Replaying needs no credentials: `--llm-mode replay --llm-model-id <deployment>`. Any change to the
prompt, few-shot set, schema, temperature or token cap changes the cache key, so a stale cache
shows up as `replay_miss` rather than silently reusing old answers.

## Limits

Synthetic, template-generated, AI-authored labels not yet human reviewed; 18 dev families; a model
may have seen similar public text; two tiers omit temperature; nothing here transfers to real
data without validation.
