"""A static, self-contained HTML dashboard over `summarize(spans)` (DG-018, offline).

It reads only the derived metrics, which are computed from spans, and spans never carry document
text (see `redaction`). Every value is HTML-escaped, including route and guardrail labels. The page
has no script and loads nothing: it can be opened from disk or attached to a ticket.

Bars encode one measure per chart in a single hue with the number printed beside each bar, so
nothing depends on colour; every chart is a table.
"""

from __future__ import annotations

from html import escape
from typing import Any

TITLE = "UC4 classification service: observability"

_CSS = """
:root{color-scheme:light;--surface:#fcfcfb;--card:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--grid:#e3e2dc;--bar:#2a78d6;--warn:#8a5a00}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--surface:#1a1a19;--card:#232322;--ink:#ffffff;--ink2:#c3c2b7;--grid:#3a3a37;--bar:#3987e5;--warn:#e0a53a}}
:root[data-theme="dark"]{color-scheme:dark;--surface:#1a1a19;--card:#232322;--ink:#ffffff;--ink2:#c3c2b7;--grid:#3a3a37;--bar:#3987e5;--warn:#e0a53a}
*{box-sizing:border-box}body{margin:0;background:var(--surface);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1040px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:0 0 12px}
.note{color:var(--ink2);font-size:13px;margin:0 0 20px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:20px}
.card{overflow-x:auto}.tile,.card{background:var(--card);border:1px solid var(--grid);border-radius:10px;padding:14px 16px}
.tile .l{color:var(--ink2);font-size:13px}.tile .v{font-size:28px;font-weight:600;font-variant-numeric:tabular-nums}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:12px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th{font-weight:500;color:var(--ink2);text-align:left;font-size:13px}th.n,td.n{text-align:right}
th,td{padding:5px 8px;border-bottom:1px solid var(--grid);vertical-align:middle}
td.k{white-space:nowrap}
.bar{height:8px;border-radius:0 4px 4px 0;background:var(--bar);min-width:2px}
.track{width:45%;min-width:60px}
.empty{color:var(--ink2);font-style:italic}.warn{color:var(--warn)}
"""


def _num(v: Any, digits: int = 0) -> str:
    if v is None:
        return "n/a"
    return f"{v:,.{digits}f}"


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _tile(label: str, value: str) -> str:
    return (
        f'<div class="tile"><div class="l">{escape(label)}</div><div class="v">{value}</div></div>'
    )


def _bars(title: str, counts: dict[str, int | float], *, unit: str = "") -> str:
    """One measure, one hue, the number beside each bar; sorted by value (largest first)."""
    rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    if not rows:
        return f'<section class="card"><h2>{escape(title)}</h2><p class="empty">none recorded</p></section>'
    top = max(v for _, v in rows) or 1
    body = "".join(
        f'<tr><td class="k">{escape(k or "(none)")}</td>'
        f'<td class="track"><div class="bar" style="width:{max(v / top * 100, 0):.1f}%"></div></td>'
        f'<td class="n">{_num(v)}{escape(unit)}</td></tr>'
        for k, v in rows
    )
    return (
        f'<section class="card"><h2>{escape(title)}</h2><table><thead><tr><th>label</th>'
        f'<th>share of largest</th><th class="n">count</th></tr></thead><tbody>{body}</tbody></table></section>'
    )


def _stages(stage: dict[str, dict[str, Any]]) -> str:
    if not stage:
        return '<section class="card"><h2>Stage latency</h2><p class="empty">no stage spans</p></section>'
    top = max((s.get("p95_ms") or 0) for s in stage.values()) or 1
    body = "".join(
        f'<tr><td class="k">{escape(name)}</td><td class="n">{_num(s["n"])}</td>'
        f'<td class="n">{_num(s["p50_ms"], 1)}</td><td class="n">{_num(s["p95_ms"], 1)}</td>'
        f'<td class="track"><div class="bar" style="width:{(s.get("p95_ms") or 0) / top * 100:.1f}%"></div></td>'
        f'<td class="n{" warn" if s["errors"] else ""}">{_num(s["errors"])}</td></tr>'
        for name, s in stage.items()
    )
    return (
        '<section class="card"><h2>Stage latency (ms; bar = P95)</h2><table><thead><tr><th>stage</th>'
        '<th class="n">spans</th><th class="n">P50</th><th class="n">P95</th><th>P95</th>'
        f'<th class="n">errors</th></tr></thead><tbody>{body}</tbody></table></section>'
    )


def render_dashboard(summary: dict[str, Any], *, title: str = TITLE) -> str:
    fb = summary.get("fallbacks", {})
    tiles = "".join(
        [
            _tile("Traces", _num(summary.get("n_traces"))),
            _tile("Review rate", _pct(summary.get("review_rate"))),
            _tile("LLM calls per trace", _num(summary.get("llm_calls_per_trace"), 2)),
            _tile("Estimated cost per trace (USD)", _num(summary.get("est_cost_usd_per_trace"), 4)),
            _tile("LLM schema-failure rate", _pct(summary.get("schema_failure_rate"))),
            _tile(
                "Evidence-verification failure rate",
                _pct(summary.get("evidence_verification_failure_rate")),
            ),
        ]
    )
    dropped = summary.get("dropped_attributes", 0)
    parts = [
        _bars("Result status", summary.get("status_distribution", {})),
        _bars("Route (why processing stopped)", summary.get("route_distribution", {})),
        _stages(summary.get("stage_latency", {})),
        _bars(
            "Fallbacks",
            {
                "escalations": fb.get("escalations", 0),
                "degraded results": fb.get("degraded_results", 0),
                "failed stage spans": fb.get("failed_stage_spans", 0),
                "retries": fb.get("retries", 0),
            },
        ),
        _bars("Guardrail triggers", summary.get("guardrail_triggers", {})),
    ]
    note = (
        f"Derived from {_num(summary.get('n_spans'))} spans. Spans carry hashes, sizes and codes, "
        "never document text. Results are recommendations."
    )
    if dropped:
        note += (
            f' <span class="warn">{_num(dropped)} span attributes were dropped by redaction.</span>'
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body><main>"
        f'<h1>{escape(title)}</h1><p class="note">{note}</p>'
        f'<div class="tiles">{tiles}</div><div class="grid">{"".join(parts)}</div></main></body></html>\n'
    )
