# UC4 observability and failure hardening (Phase 7): pre-registered plan

**Status: committed BEFORE any Phase 7 code.** It fixes scope, the privacy rules, the failure matrix
and what "done" means, so the work cannot be reshaped to fit what happens to pass. Deviations go in
the results document.

> Dataset labels: **AI-generated synthetic dataset — pending human gold-label review.**

## Scope

Approved: Phase 7 (architecture plan sections 14, 17, 19, 20): OpenTelemetry-style spans per stage,
redaction, an exporter, a failure-injection suite, and the hardening the suite exposes. **Not
approved and not done:** dashboards (shared platform work, DG-018), the service surface (Phase 8),
MCP, RAG, agents, UI, and any locked-test evaluation. No provider call is needed: the LLM path is
exercised by replay, a local fake server and fault injection.

## Gaps known before writing any code (found by reading the code, stated in advance)

| # | Gap | Failure-matrix row |
|---|---|---|
| G1 | The Rules engine has no per-detector isolation: one raising detector kills the request | Rules engine error |
| G2 | No input validation: empty, oversize or undecodable text is not `rejected` at the classification boundary | Empty, oversize or undecodable input |
| G3 | `options.mode`, `options.max_llm_tier` and `options.budget` (cost, latency) are ignored by every stage | Budget or cost cap hit |
| G4 | A stage that fails while another still decides leaves the result `ok`; there is no `degraded` signal, and a degraded Rules result could still short-circuit | ML unavailable at runtime; Rules engine error |
| G5 | Failure behaviour is tested piecemeal; there is no suite that maps every row to a test | all rows |

## Design

* **Spans:** one trace per request (`trace_id` 32 hex, `span_id` 16 hex, parent links, start/end in
  ns, attributes, events, status), stage spans S0 guardrails, S1 rules, S2 ml, S3 llm per tier, S4
  fusion, S5 result. Tracing is a **no-op unless enabled**, so behaviour and results are unchanged
  when it is off (a test proves prediction-for-prediction equality with tracing on and off).
* **Sinks:** JSONL (local default), in-memory (tests), and an optional bridge to the OpenTelemetry SDK
  (extra `otel`; tested with the SDK's in-memory exporter). **Azure Monitor / Foundry export is NOT
  verified:** no Application Insights connection string exists here. The glue is written from general
  knowledge, is lazy and optional, and is labelled unverified.
* **Fields** (architecture section 14): request (request/trace ids, pseudonymous caller), model
  (model/prompt version, tokens, latency, cost estimate, status, retries, cache hit), stage (name,
  stop reason), guardrail (type, trigger, action), outcome (level, confidence kind, review flag,
  reason codes, status).
* **Privacy (PRD 19), deny by default:** spans carry ONLY allow-listed attribute keys. Never raw
  content, quotes, rationales or unmasked evidence; only `content_hash`, counts, masked/truncated
  excerpts and locators. Exception messages are never recorded, only the class name. The caller id is
  pseudonymised with a salted hash. A redactor drops unknown keys, truncates strings and masks
  identifier-like values as defence in depth.
* **Privacy is tested by an audit, not by inspection:** every dev document is classified with tracing
  on, and the whole span output is scanned for any substring of the document text (8+ words, plus
  every gold evidence span) and for sensitive-value patterns. `dataguard-uc4 obs audit` is a CI gate.
* **Derived metrics** from spans (section 14): per-stage latency P50/P95, route distribution, review
  rate, tokens per document, schema-validation failures, evidence-verification failure rate, fallback
  counts, guardrail triggers.

## Hardening to implement (closing G1-G4)

* **G1:** per-detector isolation; a broken detector is recorded, the result is `degraded`, other
  detectors still run; a degraded Rules result is **never sufficient** for the hybrid (its evidence is
  incomplete), so it cannot short-circuit or act as a floor.
* **G2:** an input guard at the classification boundary (`config/guardrails/input.v1.yaml`): empty or
  whitespace-only, invalid encoding, and above a hard byte limit become `rejected` with a reason and a
  guardrail event; above the soft limit the text is truncated with a flag (already true per stage).
  Payloads that fail schema validation become `rejected` results instead of exceptions.
* **G3:** the hybrid honours `max_llm_tier`, `budget.max_latency_ms`, `budget.max_cost_usd` (only when a
  price exists) and `mode`; exhaustion routes to review (`BUDGET_EXHAUSTED`).
* **G4:** the hybrid marks a decided result `degraded` when a configured stage errored or was
  unavailable but another stage still decided; a runtime ML failure skips ML with a warning.

## Failure matrix and the suite (`tests/failure_injection/`)

Every row of architecture section 19 gets at least one end-to-end test through the real classifiers
(replay, a local fake provider server, injected faults); a meta-test fails if a row has no test:

LLM timeout/429 · malformed LLM JSON · evidence fails verification · ML missing or taxonomy mismatch ·
config invalid · empty/oversize/undecodable input · injection detected · budget or cost cap hit ·
Rules engine error · eval-run document fails. Two further checks: cross-request isolation (results do
not depend on other requests, sequential or threaded) and telemetry privacy.

## Reporting

`docs/uc4/results/observability-baseline.md` (generated): derived metrics for a full dev run, the
privacy-audit result (documents scanned, checks, leaks), and the failure matrix with the test that
covers each row and its result. No number is typed by hand.

## Expectations stated in advance

* The audit will find leaks in the first version of any instrumentation that records more than
  counts and hashes; the allow-list and the redactor exist because of that.
* The suite will expose more gaps than G1-G4; anything found is fixed or reported, not hidden.
* Tracing overhead is small next to LLM latency and negligible for Rules; this is measured, not
  assumed.

## Limits stated in advance

Synthetic, AI-authored labels not yet human reviewed; spans are not verified against Azure Monitor or
Foundry tracing; the fake provider server verifies the adapter's failure handling, not the real
service's; nothing transfers to real data without validation; the locked test split is untouched.
