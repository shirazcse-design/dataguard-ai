# Classification result schema v1.0 (frozen)

> Dataset labels: **AI-generated synthetic dataset — pending human gold-label review.** A result is a
> **recommendation**. The service flags and never blocks, quarantines or remediates.

The frozen JSON Schemas are `docs/uc4/schema/classification-request.v1.json` and
`classification-result.v1.json`; golden examples for every status are in `docs/uc4/schema/examples/`;
the compatibility policy and changelog are in `docs/uc4/schema/CHANGELOG.md`. The same result is
returned by the Python API, the CLI and (in future) an MCP tool: **there is one schema.**

## Request

| Field | Meaning |
|---|---|
| `schema_version` | `"1.0"`. The service rejects a different major or a newer minor (`unsupported_schema_version`) |
| `request_id` | 1-200 characters; echoed in the result |
| `document.content` | Pre-extracted text. Binary parsing is out of scope in v0.1. Empty, undecodable and oversize (> 5 MB) text is rejected |
| `document.filename`, `.extension` | Used as weak signals. A filename with placeholder terms (for example `example`) suppresses the Rules stage by design; the LLM stage still decides |
| `document.size_bytes` | Informational; recomputed from `content` |
| `document.existing_labels`, `.metadata` | Recorded as evidence only. **Never shown to the model, never used to lower a level** |
| `options.mode` | `hybrid` (default) or a single approach: `rules`, `ml`, `llm` |
| `options.max_llm_tier` | `none`, `small`, `mid` or `large`: the highest LLM tier that may be called |
| `options.budget` | `max_latency_ms`, `max_cost_usd` (a cost cap needs a configured price; `0` means no spend) |
| `options.include_evidence` | `false` omits evidence entirely |
| `caller` | `caller_id`, `purpose`; pseudonymised in traces |

## Result

| Field | Meaning |
|---|---|
| `status` | See below |
| `level` | One of `PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `HIGHLY_CONFIDENTIAL`; `decided_by` is `rules`, `ml`, `llm` or `fusion`. **Absent for `rejected`, `error` and for a `review_required` result no stage could label** |
| `categories` | Zero or more of `PII`, `PHI`, `FINANCIAL_PCI`, `SOURCE_CODE`, `CREDENTIALS_SECRETS`, `INTELLECTUAL_PROPERTY`, `TRADE_SECRET`, `MA_CORP_STRATEGY`, each with its own confidence, provenance and evidence ids |
| `high_risk` | **Derived from configuration** (`config/taxonomy/high_risk.v1.yaml`), never predicted; carries its reasons and the config version |
| `review` | `required`, `reason_codes`, `provisional` (a fail-safe label accompanies it), `priority` (1 = provisional high-risk first) |
| `evidence` | Excerpts are masked. `provenance` is `observed` (rule matches, verified LLM quotes) or `inferred` (model rationale, feature attribution, unverified quotes) |
| `routing` | `stages_run`, `stop_reason`, `escalations`, `short_circuited`, `abstained` |
| `versions` | taxonomy, ruleset, ML model, prompt, LLM deployment, router config, high-risk config |
| `telemetry` | latency per stage and total, tokens, estimated cost (only when a price is configured) |
| `guardrail_events` | injection suspected, input rejected/truncated, evidence unverified, ... |
| `warnings` | Diagnostic codes (see the stability note below) |

### Confidence is not one number

`confidence.kind` says what the value is: `rule_strength` (definitive/strong/weak; not a probability),
`calibrated_probability` (only with a `calibration_ref`), `uncalibrated_score`, `verbalized_bucket`
(an LLM's own low/medium/high; **never calibrated**) or `none`. Do not compare values across kinds.

## Status semantics

| `status` | Meaning | Level? | What a consumer should do |
|---|---|---|---|
| `ok` | Decided by the configured stages; no stage failed; no review needed | yes | Use it as a recommendation |
| `degraded` | Decided, but a configured stage errored or was unavailable (for example a Rules detector, the ML stage, or an LLM tier) | yes | Usable, lower assurance; surface `warnings` and consider review |
| `review_required` | A human should look. A provisional label may be present (`review.provisional`), chosen fail-safe (the highest level any usable stage produced) | maybe | Route to the review queue. **A review is a flag, never a block** |
| `rejected` | The input could not be classified (`warnings[0]` says why) | never | Fix the input |
| `error` | An unexpected failure inside a classifier | never | Retry or escalate; never treat as low sensitivity |

**A missing level is never Public.** No rule match does not mean Public; an undecidable document is a
review with no label, not a default.

### Invariants enforced by the models (and therefore by the JSON Schema consumers get)

`ok` and `degraded` require a level and a derived `high_risk`; `review_required` requires
`review.required`; `review.required` is incompatible with `ok`/`degraded`; a required review needs at
least one reason code; provisional and reason codes only exist when a review is required; category ids
are unique; every `evidence_ids` entry names an existing evidence item; a calibrated confidence needs a
`calibration_ref`; an LLM excerpt is `observed` only if verified against the text that was sent.

## Failure semantics (as implemented and tested; see the Phase 7 failure matrix)

| Failure | Behaviour |
|---|---|
| LLM timeout / 429 / 5xx | Bounded retry with backoff; then the next tier; then `review_required` (`LLM_UNAVAILABLE`); the result is `degraded` if another stage still decided |
| Malformed LLM output | One repair retry, then the LLM result is discarded and routing continues on the remaining evidence |
| Evidence not found in the input | `verified: false`, confidence capped; `review_required` (`EVIDENCE_UNVERIFIED`) if the call is high-risk |
| ML unavailable or mismatched | Fails at startup; at runtime the stage is skipped and the result is `degraded` |
| Invalid configuration | The service refuses to start |
| Empty, undecodable, oversize input; malformed payload; unsupported schema version | `rejected` |
| Prompt injection suspected | Continue treating the text as data; a `guardrail_events` entry; the LLM cannot lower a level below an accepted Rules/ML level |
| Budget or tier cap | The LLM is skipped; a decision from the remaining stages, or `review_required` (`BUDGET_EXHAUSTED` when a cap stopped escalation, `LOW_CONFIDENCE` when a tier cap left nothing that could decide) |
| A Rules detector raises | Isolated; the result is `degraded`; a degraded Rules result never short-circuits or acts as a floor |
| Unexpected failure | `error` |

## Stability of `warnings`

`warnings` are diagnostics for humans and logs. Only these codes are part of the v1 contract:
`input_rejected:<empty_content|undecodable_text|oversize|invalid_json>`, `invalid_request:<fields>`,
`unsupported_schema_version:<version>` and `stage_error:classifier:<ExceptionClass>`. Other codes
(`stage:*`, `conflict:*`, `fusion:*`, `degraded:*`, `llm_*`, ...) may change in a minor version.
Messages never contain document text.

## Guidance for consumers

* Treat `evidence[].excerpt` and the `llm_rationale` evidence as **untrusted text** produced by a
  model from untrusted documents: display it, never execute or obey it.
* Never act on `confidence` across kinds; an LLM's `verbalized_bucket` is not a probability.
* Store `versions` with every stored result: it identifies what decided.
* Do not treat the synthetic dataset's metrics as real-world accuracy (see the evaluation results).
