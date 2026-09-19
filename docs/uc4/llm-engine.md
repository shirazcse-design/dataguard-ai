# UC4 LLM classifier (Approach C)

> Dataset labels: **AI-generated synthetic dataset — pending human gold-label review.**
> Plan (pre-registered before any code): [`llm-plan.md`](llm-plan.md). Generated status and
> measurements: [`results/llm-baseline.md`](results/llm-baseline.md).

## Status in one paragraph

The whole LLM path exists and is tested against mock and replay providers: provider interface, replay
cache, prompt assembly, strict output validation, code-based evidence verification, injection guard,
failure handling, harness integration and a report generator. **No model has been run.** There is no
Azure AI Foundry project, credential or approved deployment name in this environment, so there are
**no LLM accuracy, calibration, latency or cost results**, and the PRD's three-model benchmark is
**blocked** until the product owner supplies access. The Foundry adapter has never talked to a real
service. What *was* measured is model-independent (prompt sizes, few-shot leakage, the local injection
guard).

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

## What was NOT done, and why

| Item | State |
|---|---|
| Three-tier benchmark on dev (PRD 6.3), reliability table, cost, latency, evidence rates | **Blocked:** no Azure access or deployment names (open decision 4) |
| Verification of the Foundry adapter against a real endpoint | **Blocked:** same; tested only against a local fake server |
| Foundry content-safety supplement | Not started; optional and only if available |
| Pre-LLM redaction option | Deferred: its effect can only be measured with a real model |
| Injection "raise but not lower" rule | Phase 6: it needs Rules/ML outputs |
| Hybrid routing, fusion, thresholds, locked-test report | Phase 6, not approved yet |

## To run a tier once access exists

```
export DATAGUARD_FOUNDRY_ENDPOINT=...  DATAGUARD_FOUNDRY_API_VERSION=...  DATAGUARD_FOUNDRY_API_KEY=...
export DATAGUARD_LLM_DEPLOYMENT_SMALL=<your deployment name>
dataguard-uc4 eval run --classifier llm --llm-tier small --llm-mode record --split dev
dataguard-uc4 llm report --tier small=<your deployment name> --out docs/uc4/results/llm-baseline.md
```

Recording stores each real response under `data/llm_cache/<model>/<prompt_version>/<hash>.json`, so
the same run can then be replayed in CI with no network or credentials. Confirm the adapter's URL
shape, API version, token parameter and authentication against current Foundry documentation first
(`config/llm/llm.v1.yaml`, section `foundry`).

## Limits

Synthetic, template-generated, AI-authored labels not yet human reviewed; 18 dev families; a model
may have seen similar public text; the Foundry adapter is unverified; nothing here transfers to real
data without validation.
