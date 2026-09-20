# MCP contract for `classify_document` (documentation only)

> **Not implemented, and blocked.** The architecture plan says to stop for approval before MCP and to
> add no MCP scaffolding in v0.1. This document only fixes what the tool WOULD be, so it can be
> reviewed now. The freeze criteria below are computed in
> [`results/service-baseline.md`](results/service-baseline.md); one of them is **not met**.
>
> Dataset labels: **AI-generated synthetic dataset — pending human gold-label review.**

## Tool

| Property | Value |
|---|---|
| Name | `classify_document` (PRD section 10: read/compute, medium risk) |
| Description | Classify one pre-extracted document by sensitivity level and data categories. Returns a **recommendation**; it never blocks or remediates |
| Input | The `ClassificationRequest` subset below |
| Output | The `ClassificationResult`, **unchanged**. No forked or wrapped schema |
| Schemas | `docs/uc4/schema/classification-request.v1.json`, `classification-result.v1.json` |

### Input subset

```
document.content      required for v0.1 (pre-extracted text, size-limited: see below)
document.document_id  reserved: preferred over raw content once a document store exists
document.filename, document.extension
options.mode, options.max_llm_tier, options.budget, options.include_evidence
caller                populated by the MCP adapter from the authenticated agent identity, NEVER
                      from tool arguments
```

`existing_labels` and `metadata` are **not** accepted through the tool: they are spoofable and the
service never trusts them.

## Rules the adapter must follow (PRD 10.1, 12, 19)

* **Schema validation on input and output** against the frozen schemas; a request that fails
  validation returns a `rejected` result, not a protocol error.
* **Allowlist and per-agent permissions**: only agents granted `classify_document` may call it; the
  tool is read/compute and needs no write permission anywhere.
* **No arbitrary execution**: the adapter is a thin call into `ClassificationService.classify`.
* **Prefer `document_id` references over raw content.** Passing document text through an agent's
  context is itself exposure. Raw `content` is allowed only for ad-hoc calls under a size limit
  (recommended: far below the 5 MB service limit, for example 100 KB). `document_id` resolution does
  not exist yet and is a separate decision.
* **Trace every call** with the caller (pseudonymised), redacted arguments (`content_hash`, size and
  extension only; never text), status, latency and outcome: the Phase 7 spans already do this.
* **Budgets**: the adapter sets `options.budget` and `max_llm_tier` from the calling agent's policy;
  the service enforces them and routes to review when they are exhausted.

## Error mapping

| Situation | MCP behaviour |
|---|---|
| Any `ClassificationResult` (`ok`, `degraded`, `review_required`, `rejected`, `error`) | A normal tool result carrying the result unchanged. **Statuses are data, not protocol errors.** A review is a flag, not a failure |
| Adapter cannot reach the service, or the service fails to start | An MCP protocol error; no result is fabricated |
| Caller not on the allowlist | An MCP authorization error before any classification |

## Security notes for the agent that consumes it

* `evidence[].excerpt` and the `llm_rationale` evidence are **model-generated text derived from an
  untrusted document**. They can carry injected instructions. The adapter should default
  `include_evidence` to what the calling agent needs and must mark this text as untrusted data; the
  agent must never obey it.
* Do not let an agent lower or override a `high_risk` result; a downgrade is a human decision.
* `versions` must be stored with any stored result; results are recommendations, not decisions.

## Freeze criteria (architecture section 15) and their status

Computed from a real run in `results/service-baseline.md` (see that file for the current numbers):

| Criterion | Status |
|---|---|
| The result schema is versioned | Met (v1.0 frozen, drift check in CI) |
| Failure semantics are documented | Met (`result-schema.md`; the Phase 7 failure matrix) |
| **The eval gates have passed** | **NOT MET.** Point estimates pass on dev, but the lower family-bootstrap bound of level macro-F1 fails (0.631 against 0.85), dev chose the configuration, and the locked test split has never been evaluated |
| Approval to build MCP | **Not given** |

**Therefore: MCP implementation is blocked.** To unblock it: (1) an audited, report-only confirmation
of the frozen configuration on data that did not choose it (the locked test split, or a
newly recorded split), (2) human review of the gold labels, and (3) explicit approval.

## Out of scope for this contract

Authentication and identity, rate limiting, multi-tenant isolation, a document store, and any write or
remediation tool.
