"""UC6 telemetry through the PRODUCTION redactor: the privacy audit is clean on a real traced run,
it catches a planted leak, UC6 keys survive redaction intact, and uc6 spans map to GenAI views."""

from __future__ import annotations

from evals.policy.golden import load_golden
from evals.policy.observability_report import privacy_audit, run_traced, summarise
from observability.sinks import _genai_attrs
from observability.types import Span

ITEMS = {i.id: i for i in load_golden()[0]}
PICK = [ITEMS[k] for k in ("S01", "C02", "I02", "X01", "X02")]


def test_traced_run_is_clean_and_useful():
    spans, copilot = run_traced("offline", ["naive", "advanced", "agentic"], PICK)
    audit = privacy_audit(spans, PICK, copilot.corpus)
    assert audit["clean"], audit["leaks"]
    s = summarise(spans)
    assert {"uc6.request", "uc6.input_guard", "uc6.retrieve", "uc6.generate", "uc6.citation_verify",
            "uc6.agent", "uc6.tool"} <= set(s["span_names"])  # fmt: skip
    roots = [x for x in spans if x.name == "uc6.request"]
    cited = {c for r in roots for c in r.attributes.get("dg.policy.cited", [])}
    assert "POL-DLP:4.2" in cited  # identifiers survive the redactor unmasked
    assert any(r.attributes.get("dg.policy.stages") for r in spans if r.name == "uc6.retrieve")
    blocked = [r for r in roots if r.attributes.get("dg.policy.status") == "BLOCKED"]
    assert blocked and all(
        "prompt_injection_suspected" in r.attributes["dg.guardrail.type"] for r in blocked
    )


def test_audit_catches_a_planted_leak():
    spans, copilot = run_traced("offline", ["advanced"], PICK[:1])
    leak = spans[0].model_copy(update={"attributes": {**spans[0].attributes,
                                                      "dg.policy.status": PICK[0].question}})  # fmt: skip
    assert not privacy_audit([leak, *spans[1:]], PICK[:1], copilot.corpus)["clean"]


def _span(name, **attrs):
    return Span(trace_id="t" * 32, span_id="s" * 16, parent_span_id=None, name=name, start_ns=0, end_ns=1,
                status="ok", attributes=attrs, events=[])  # fmt: skip


def test_uc6_spans_map_to_genai_conventions():
    live = _genai_attrs(
        "uc6.generate",
        {"dg.llm.model_id": "uc4-llm-medium", "dg.tokens_in": 10, "dg.llm.cached": False},
    )
    assert live["gen_ai.operation.name"] == "chat" and live["gen_ai.usage.input_tokens"] == 10
    assert (
        _genai_attrs("uc6.generate", {"dg.llm.model_id": "m", "dg.llm.cached": True}) == {}
    )  # replay is not a model call
    assert (
        _genai_attrs("uc6.agent", {"dg.agent.name": "dataguard-policy-copilot"})[
            "gen_ai.operation.name"
        ]
        == "invoke_agent"
    )
    assert (
        _genai_attrs("uc6.tool", {"dg.agent.tool": "search_policy"})["gen_ai.tool.name"]
        == "search_policy"
    )
    assert _genai_attrs("uc6.agent.planner", {}) == {}  # the offline planner is not a model call


def test_replayed_agent_turns_are_not_exported_as_model_calls(tmp_path):
    import json as _json

    from app.agent.mock import MockAgentClient
    from app.agent.types import AgentTurn, ParsedToolCall
    from app.policy.agent import ReplayAgentClient, tool_schemas
    from app.policy.embeddings import HashingEmbedder
    from app.policy.service import build_copilot
    from observability.config import build_tracer, load_observability_config
    from observability.sinks import MemorySink

    final = AgentTurn(final_text=_json.dumps({"status": "INSUFFICIENT_EVIDENCE", "claims": [],
                                               "conflict_evidence_ids": [], "conflict_note": ""}),
                      model_id="uc4-llm-medium", tokens_in=100, tokens_out=10)  # fmt: skip
    search = AgentTurn(tool_calls=[ParsedToolCall("c1", "search_policy", {"query": "api key rotation"})],
                       model_id="uc4-llm-medium", tokens_in=50, tokens_out=5)  # fmt: skip

    def run(inner):
        cfg, _ = load_observability_config()
        sink = MemorySink()
        tracer, _ = build_tracer(cfg, [sink], deterministic_ids=True)
        planner = ReplayAgentClient(tmp_path, "uc4-llm-medium", "p", tool_schemas(), inner=inner)
        cp = build_copilot(
            "replay", embedder=HashingEmbedder(), tracer=tracer, agent_planner=planner
        )
        cp.answer("How often must API keys be rotated?", "agentic")
        return [s for s in sink.spans if s.name == "uc6.agent.planner"]

    live = run(MockAgentClient([search, final]))
    assert all(s.attributes["dg.llm.cached"] is False for s in live)
    assert all(
        _genai_attrs(s.name, s.attributes).get("gen_ai.operation.name") == "chat" for s in live
    )
    replayed = run(None)
    assert replayed and all(s.attributes["dg.llm.cached"] is True for s in replayed)
    assert all(_genai_attrs(s.name, s.attributes) == {} for s in replayed)
