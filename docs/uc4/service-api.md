# UC4 classification service: Python API and CLI

> A library and CLI for a portfolio MVP, not a production service: no authentication, rate limiting
> or multi-tenant isolation beyond per-request isolation. Results are recommendations.

## Python API

```python
from app.classification.service import ClassificationService

svc = ClassificationService(llm_mode="replay")  # foundry | replay | record | off
result = svc.classify_text("Employee record\nNational ID: 905-37-6209", filename="hr.txt")
result.status, result.level.value, result.high_risk.value

svc.classify({...})  # a ClassificationRequest as a dict, a model, or a JSON string
svc.classify_many(list_of_requests)  # ordered; at most config.max_batch_size
svc.info()  # every version that decides a result
svc.self_check()  # a rules-only end-to-end call, no model
```

* `classify` **never raises**: bad input becomes a `rejected` result, an unexpected failure an
  `error` result (neither carries a level or any text).
* Construction **validates all configuration and refuses to start** on any invalid config, an unknown
  variant, or (in `foundry` mode) missing LLM credentials.
* Thread-safe: results do not depend on other requests, sequential or concurrent.
* `llm_mode="off"` builds a rules-only variant and never calls a model.
* Tracing: `trace_path=...` writes redacted spans (no document text; see the observability docs).

Configuration: `config/service/service.v1.yaml` (`default_variant`, `llm_mode`, `max_batch_size`,
`trace.enabled`); routing variants and thresholds are in `config/routing/routing.v1.yaml`.
Live mode needs `DATAGUARD_FOUNDRY_ENDPOINT`, `DATAGUARD_FOUNDRY_API_KEY` and
`DATAGUARD_LLM_DEPLOYMENT_{SMALL,MID,LARGE}` in the environment (never in files or the repository).

## CLI

```
dataguard-uc4 classify --text "..."                       # one document
dataguard-uc4 classify --file report.txt --pretty         # a UTF-8 file, or --file - for stdin
dataguard-uc4 classify --json request.json                # a full ClassificationRequest
dataguard-uc4 classify --jsonl requests.jsonl             # one result per input line
dataguard-uc4 classify --text "..." --mode rules --max-llm-tier none --no-evidence
dataguard-uc4 classify --text "..." --llm-mode replay --trace-out spans.jsonl
dataguard-uc4 service info --check --llm-mode off         # versions + a rules-only self-check
dataguard-uc4 schema check                                # fail if the models drifted from the frozen schemas
```

Options: `--mode`, `--max-llm-tier`, `--max-latency-ms`, `--max-cost-usd`, `--no-evidence`,
`--request-id`, `--caller-id`, `--purpose`, `--filename`, `--variant`, `--llm-mode`, `--llm-cache-dir`,
`--trace-out`, `--pretty`.

**stdout is only result JSON** (one object per line, or pretty for a single document); diagnostics go
to stderr; nothing prints a traceback or document text for a bad input.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | A valid result: `ok`, `degraded` or `review_required` (**a review is a flag, not a block**) |
| 3 | `rejected` (for JSON lines: at least one rejected) |
| 4 | `error` |
| 2 | Usage error, invalid configuration, unknown variant, missing credentials in live mode, unreadable input file, or a JSON-lines batch over `max_batch_size` |

For JSON lines the exit code is the worst status in the batch.

### Hostile input

Files are read with a cap (at most the hard limit plus one byte), so an enormous file cannot exhaust
memory; oversize and undecodable files become `rejected` results without their text; only the file's
**basename** is used as the filename signal, so directory names never reach a model or a trace.

## What this is not

No JSON/HTTP API (optional in the plan; the approved decision was library + CLI first), no MCP server
(see `mcp-contract.md`), no `document_id` resolution against a document store, no authentication.
