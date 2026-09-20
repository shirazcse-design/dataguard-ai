"""The failure matrix: architecture plan section 19, one id per row, plus cross-cutting checks.

`tests/failure_injection/test_matrix.py` fails if a row has no test named `test_row_<id>_*`; `dataguard-uc4 obs report`
runs this suite and lists every row with its tests and results.
"""

ROWS = {
    "F01": "LLM timeout / 429: bounded retry with backoff, then next tier, then review (LLM_UNAVAILABLE)",
    "F02": "Malformed LLM JSON: one repair retry, then discard the LLM result and route on the remaining evidence",
    "F03": "Evidence fails verification: verified=false, confidence capped, review if the call is high-risk",
    "F04": "ML model missing or taxonomy-version mismatch: fail fast at startup; at runtime skip ML (degraded)",
    "F05": "Config invalid: refuse to start, never run with a partial taxonomy",
    "F06": "Empty, oversize or undecodable input: rejected with a reason, or truncated with a flag",
    "F07": "Injection detected: continue as data, log a guardrail event, restrict LLM downgrading",
    "F08": "Budget or cost cap hit: skip the LLM and route to review",
    "F09": "Rules engine error: isolate per detector; one broken detector marks the result degraded",
    "F10": "Eval-run document fails: counted as a failure in the report, never dropped silently",
    "X01": "Cross-request isolation: a result never depends on other requests (sequential or threaded)",
    "X02": "Telemetry privacy: no document text or sensitive value in any exported span, including error paths",
    "X03": "Tracing is inert: results are identical with tracing on and off; a broken sink never breaks a request",
}
