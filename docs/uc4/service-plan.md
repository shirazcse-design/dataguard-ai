# UC4 service surface (Phase 8): pre-registered plan

**Status: committed BEFORE any Phase 8 code.** It fixes scope, the freeze criteria and their honest
status, so the "frozen" schema cannot be declared ready by redefining the criteria.

> Dataset labels: **AI-generated synthetic dataset — pending human gold-label review.**

## Scope

Approved: Phase 8 (architecture plan sections 3, 15, 20): a Python API and CLI, a frozen and
versioned result schema, and a **documented** MCP contract. **Not approved and not done:** any MCP
server, tool, adapter or scaffolding (the plan says "Stop for approval before MCP" and "Do not add MCP
scaffolding in v0.1"), a JSON/HTTP API (optional in the plan; the approved decision was Python
library + CLI first), RAG, agents, UI, `document_id` resolution against a document store (v0.1
classifies pre-extracted text only), and any locked-test evaluation.

## Deliverables

1. **`ClassificationService`** (`app/classification/service.py`): one entry point over the hybrid.
   It validates configuration at startup (refuses to start on any invalid config), builds the
   configured variant, wraps it in the never-raises boundary, optionally traces (deny-by-default
   redaction, as in Phase 7), and always returns a valid `ClassificationResult`.
2. **CLI**: `dataguard-uc4 classify` (text file, JSON request, stdin, or JSON lines), `schema
   export|check|examples`, `service info`. Documented exit codes; no command ever prints a traceback
   or document text for a bad input.
3. **Frozen result schema v1.0**: JSON Schemas for request and result committed under
   `docs/uc4/schema/`, golden examples for every status, a changelog, and a compatibility policy. A
   test compares the frozen schema with the models by *structural signature* (property names, types,
   required sets, enums), so an accidental change fails CI and a deliberate change needs a version
   bump and a changelog entry.
4. **Semantic contract**: `docs/uc4/result-schema.md` (fields, invariants, status semantics, the
   failure semantics as implemented, guidance for consumers) and `docs/uc4/service-api.md`.
5. **MCP contract (documentation only)**: `docs/uc4/mcp-contract.md` for `classify_document`: input
   and output (the result UNCHANGED, no forked schema), permissions, allowlist, redaction, error
   mapping, size limits, and the freeze-criteria checklist below.

## Design decisions (fixed now)

* `schema_version` is `MAJOR.MINOR`. A request is accepted only if its major equals the service's and
  its minor is not newer than the service's; otherwise it is `rejected` (`unsupported_schema_version`)
  rather than parsed leniently, so a field is never silently dropped. Adding an optional field is a
  minor bump; removing/renaming a field, changing a type, tightening an enum or adding a required
  field is a major bump.
* The result is returned **unchanged** everywhere (Python, CLI, future MCP): no per-surface schema.
* CLI exit codes: 0 for a valid result with status `ok`, `degraded` or `review_required` (a
  review is a flag, never a block); 3 `rejected`; 4 `error`; 2 usage or invalid configuration.
* `llm_mode` in the service configuration is `foundry` (live), `replay`, `record` or `off`. `off`
  builds a rules-only variant at runtime (not one of the 13 pre-registered variants) and refuses LLM
  calls. Missing credentials in `foundry` mode fail at startup, not at request time.
* Recommendations, not decisions: results are labeled as recommendations in the docs; the service
  never blocks or remediates.

## MCP freeze criteria (architecture section 15) and their status, stated in advance

| Criterion | Status before any Phase 8 code |
|---|---|
| The result schema is versioned | Met by this phase |
| Failure semantics are documented | Met by this phase (from the Phase 7 failure matrix) |
| **The eval gates have passed** | **NOT MET.** On dev the point estimates of level macro-F1, category macro-F1 and high-risk recall pass, but the lower family-bootstrap bound of level macro-F1 fails, dev chose the variant, and the locked test split has never been evaluated |
| Approval to build MCP | Not given; the plan says to stop for it |

The MCP contract is therefore documented and **not implemented**, and the freeze checklist in it
says "blocked" until the gates are confirmed on data that did not choose the configuration.

## Reporting

The generated results file lists the schema signature digest, the examples validated, the exit-code
behaviour exercised, and the service's version block. No number is typed by hand.

## Expectations stated in advance

* Building the schema tests will expose invariants the models do not yet enforce (for example
  contradictory status/review combinations); those are fixed or documented.
* Hostile CLI input (huge files, invalid UTF-8, malformed JSON, path traversal in `--file`) will
  expose gaps; they are fixed or reported.

## Limits stated in advance

Synthetic, AI-authored labels not yet human reviewed; the surface is a library and CLI for a
portfolio MVP, not a production service; no authentication, rate limiting or multi-tenant isolation
beyond per-request isolation; nothing transfers to real data without validation.
