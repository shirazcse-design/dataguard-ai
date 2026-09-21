# MCP contract for `classify_document` (documentation only)

> **Implemented ahead of the freeze criteria.** The product owner approved building the adapter on
> 2026-09-20. It lives in `mcp_adapter/` (core: `adapter.py`, SDK-independent; `server.py`: stdio
> server, optional `mcp` extra) and is started with `dataguard-uc4-mcp`. Two of the three conditions
> the plan set for unblocking it are **not satisfied** (see the freeze criteria below), so treat it as
> a development and evaluation surface, not a release. Criteria are computed in
> [`results/service-baseline.md`](results/service-baseline.md).
>
> Dataset labels: **AI-generated synthetic dataset — reviewed by one human (provenance per coordinator); second independent review pending.**

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
| **The eval gates have passed** | **NOT MET.** Point estimates pass on dev, but the lower family-bootstrap bound of level macro-F1 fails (0.631 against 0.85), dev chose the configuration. The single locked-test evaluation (2026-09-21) confirms the pattern: level macro-F1 0.884, lower bound 0.758, so the gate still fails on the lower bound |
| Approval to build MCP | Given (2026-09-20) |

**Therefore: the adapter exists but is not release-ready.** Of the two conditions that remained after
the build approval: (1) the audited, report-only confirmation on data that did not choose the
configuration is **done** (2026-09-21, [`results/hybrid-locked-test.md`](results/hybrid-locked-test.md));
(2) human review of the gold labels was **accepted by the product owner with one reviewer** (decision A29;
provenance per the coordinator). What is still unmet is the **eval-gates criterion on the strict metric**:
the level macro-F1 lower bound is 0.758 on the locked test (0.85 required). A lenient view reaches 1.000
([`results/validation-rescore.md`](results/validation-rescore.md)), but whether the gate may be judged on it is
a product decision that has not been made.

## As implemented

* **Deny by default.** `config/mcp/mcp.v1.yaml` lists the allowed callers; the launcher refuses to
  start for an identity that is not listed, and every call re-checks. Over stdio the identity is set
  by whoever launches the server (`DATAGUARD_MCP_CALLER_ID`): **asserted, not authenticated.**
* **Per-caller policy** caps `max_llm_tier`, cost and latency and gates evidence. A request may lower
  the caps, never raise them. The shipped policy caps at `mid` because `large` (~78 s) exceeds the
  PRD's 10 s tool-call limit.
* **Input subset only**: `request_id`, `document.{content, filename, extension}`, `options.{mode,
  max_llm_tier, max_cost_usd, max_latency_ms, include_evidence}`. `metadata`, `existing_labels`,
  `caller`, `schema_version` and `document_id` are **rejected, not ignored** (`document_id`
  resolution does not exist). The caller is the connection identity, and the purpose is fixed at
  `mcp:classify_document`.
* **Evidence is off by default** (`default_include_evidence: false`), because excerpts are model
  text from an untrusted document.
* **Size limit** 100 KB of UTF-8 (`max_content_bytes`), counted in bytes; over it is a `rejected`
  result.
* **Output** is the `ClassificationResult` unchanged; the tool advertises the frozen result schema
  as its output schema. Invalid input is a `rejected` result with field paths only (sanitised,
  truncated), never the offending value.
* **Protocol errors** are only: an unknown tool, and an identity that is not allowlisted (JSON-RPC
  code -32001).
* **Tracing** reuses the Phase 7 spans: caller pseudonymised, content hash and size only.

## Out of scope for this contract

Authentication and identity, rate limiting, multi-tenant isolation, a document store, and any write or
remediation tool.
