# UC4 observability and failure hardening (Phase 7)

> Dataset labels: **AI-generated synthetic dataset — pending human gold-label review.**
> Plan (pre-registered before any code): [`observability-plan.md`](observability-plan.md). Generated
> results: [`results/observability-baseline.md`](results/observability-baseline.md). Every number here
> comes from that file or from a command shown below.

## Status in one paragraph

Every stage of the classification path now emits OpenTelemetry-compatible spans under one trace per
request, through a deny-by-default redactor, and a privacy audit proves that no document text or
sensitive value reaches them (a CI gate). The failure-injection suite covers every row of the
architecture's failure table end to end (166 tests, 0 failures) and, more importantly, exposed and
closed real hardening gaps (below). **Export to Azure Monitor / Foundry tracing is not verified**: no
Application Insights connection string exists here. Dashboards and the service surface are not built.

## Architecture

```
request ─► TracedClassifier ── trace "classify" (request id, pseudonymous caller, content hash, size, extension)
             └─ S0.guardrails  input guard + injection scan (events)
             └─ S1.rules │ S2.ml │ S3.llm.<tier> ─ llm.call (retries as events, tokens, latency, cache hit)
             └─ S4.fusion ─ S5.result (level, confidence kind, review flag and reasons, status, stop reason)
spans ─► Redactor (allow-list, shape checks, masking) ─► sinks: JSONL │ memory │ OpenTelemetry SDK bridge
spans ─► summarize (derived metrics)  │  obs audit (privacy gate)
```

| Piece | File |
|---|---|
| Span model, tracer (no-op unless a trace is active) | `observability/types.py`, `observability/trace.py` |
| Redaction, pseudonymisation | `observability/redaction.py`, `config/observability/observability.v1.yaml` |
| Sinks (JSONL, memory, OTel bridge, unverified Azure glue) | `observability/sinks.py` |
| Wrapper and outcome attributes | `observability/instrument.py` |
| Derived metrics, privacy audit | `observability/metrics.py`, `observability/audit.py` |
| Input guard and the never-raises boundary | `guardrails/input.py`, `app/classification/boundary.py` |
| Failure matrix and suite | `evals/classification/failure_matrix.py`, `tests/failure_injection/` |
| Report | `evals/classification/obs_report.py` (`dataguard-uc4 obs report`) |

## Privacy (PRD 19): deny by default, then proven

* Only allow-listed `dg.*` keys are exported; any other attribute is dropped and counted. Keys whose
  final segment names text (`content`, `quote`, `excerpt`, `rationale`, `prompt`, `filename`, `metadata`,
  ...) are refused when the config loads.
* Identifier keys are validated by shape rather than masked (a request id that is not id-shaped is
  dropped, never echoed); the caller id is a salted HMAC pseudonym; other values are masked and
  truncated; exception messages are never recorded, only class names.
* The audit reads every key and string of a full dev run and looks for 6-word windows of every
  document, every gold evidence span, filenames and sensitive-value patterns. The generated report
  shows the counts, zero leaks, and a **negative control** proving the audit can fail. The same
  property is tested on error paths (a stage that raises an exception containing the document text;
  a provider error body containing text).

## Hardening gaps found by reading the code, and closed

| Gap | Fix |
|---|---|
| G1: one raising Rules detector killed the request | per-detector isolation; result `degraded`; a degraded Rules result is never sufficient for the hybrid |
| G2: empty, oversize or undecodable input was not rejected at the boundary | input guard and `classify_safely`: `rejected`/`error` results, never an exception, never text in a message |
| G3: `options.mode`, `max_llm_tier` and `budget` were ignored | honoured by the hybrid; cost caps need a price (a cap of 0 means no spend) |
| G4: a failed stage left the result `ok` | `degraded` whenever a configured stage failed but another decided |
| G5: failure behaviour tested piecemeal | one suite, one test-per-row meta-check |

## Failure matrix (architecture section 19), from an actual run of the suite

| row | failure | tests | passed | failed | result |
|---|---|---|---|---|---|
| F01 | LLM timeout / 429: bounded retry with backoff, then next tier, then review (LLM_UNAVAILABLE) | 10 | 10 | 0 | PASS |
| F02 | Malformed LLM JSON: one repair retry, then discard the LLM result and route on the remaining evidence | 9 | 9 | 0 | PASS |
| F03 | Evidence fails verification: verified=false, confidence capped, review if the call is high-risk | 4 | 4 | 0 | PASS |
| F04 | ML model missing or taxonomy-version mismatch: fail fast at startup; at runtime skip ML (degraded) | 8 | 8 | 0 | PASS |
| F05 | Config invalid: refuse to start, never run with a partial taxonomy | 60 | 60 | 0 | PASS |
| F06 | Empty, oversize or undecodable input: rejected with a reason, or truncated with a flag | 34 | 34 | 0 | PASS |
| F07 | Injection detected: continue as data, log a guardrail event, restrict LLM downgrading | 8 | 8 | 0 | PASS |
| F08 | Budget or cost cap hit: skip the LLM and route to review | 10 | 10 | 0 | PASS |
| F09 | Rules engine error: isolate per detector; one broken detector marks the result degraded | 5 | 5 | 0 | PASS |
| F10 | Eval-run document fails: counted as a failure in the report, never dropped silently | 3 | 3 | 0 | PASS |
| X01 | Cross-request isolation: a result never depends on other requests (sequential or threaded) | 3 | 3 | 0 | PASS |
| X02 | Telemetry privacy: no document text or sensitive value in any exported span, including error paths | 4 | 4 | 0 | PASS |
| X03 | Tracing is inert: results are identical with tracing on and off; a broken sink never breaks a request | 8 | 8 | 0 | PASS |

The suite drives the real Foundry adapter and LLM classifier against a **local fake provider server**
(429, 5xx, timeouts, malformed JSON, fabricated quotes), the real Rules engine with injected
detector faults, the real config loaders with corrupted files, and the real harness with a flaky
classifier. It verifies failure handling, not the real service's failure behaviour.

## Measured cost of tracing

`dataguard-uc4 obs overhead` (best of 5 over the 107 dev documents, on the development machine):
about 40 microseconds per request for the Rules classifier and about 160 microseconds for the hybrid
with replayed LLM responses. A real LLM call takes seconds, so tracing is negligible next to it; the
numbers vary by machine and are not part of the generated (reproducible) report.

## Findings and bugs found by tests while building this phase

* The redactor first **mangled the identifiers it exists to preserve** (`request_id` became
  `req-uc#-##ac######`, the content hash `[token]`) and masked snake_case codes; identifier keys are now
  shape-validated and safe codes pass unless they look like secrets.
* The first allow-list check rejected safe keys such as `dg.content_hash`; the rule is now "the final
  key segment names text".
* Guardrail events were emitted twice (hybrid and wrapper), doubling the trigger counts; only the
  wrapper emits them now.
* Fusion evidence-id collision and other earlier findings are in `decisions.md`.
* With the recorded model outputs nothing fails, escalates or retries on dev, so the derived failure
  counters are zero; they are exercised by the failure-injection suite, not by dev data.

## What was NOT done, and why

| Item | State |
|---|---|
| Azure Monitor / Foundry tracing export | **Unverified**: no Application Insights connection string; the glue (`azure_monitor_sink`) is optional, lazy and never called in tests |
| Dashboards (DG-018) | Shared platform work; only the derived metrics they would show are computed |
| Service surface, frozen result schema, MCP contract | Phase 8 |
| Real-service failure behaviour (rates, latency under load) | Unmeasured; the suite uses a local fake |
| Locked-test evaluation | Not authorised; never used |

## Use

```
dataguard-uc4 eval run --classifier hybrid --split dev --trace-out spans.jsonl
dataguard-uc4 obs audit --spans spans.jsonl --split dev      # exits 1 on any leaked text
dataguard-uc4 obs summarize --spans spans.jsonl
dataguard-uc4 obs report --out docs/uc4/results/observability-baseline.md
python -m pytest tests/failure_injection
```

Set `DATAGUARD_OBS_SALT` (from a secret store) for stable caller pseudonyms; without it a per-process
random salt is used.

## Limits

Synthetic, AI-authored labels not yet human reviewed; spans not verified against Azure; the privacy
audit proves the absence of the tested strings and patterns, and the allow-list and redactor are the
primary control; nothing transfers to real data without validation.
