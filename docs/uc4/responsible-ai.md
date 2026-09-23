# UC4 Responsible AI, HHH/APF, and Azure AI Foundry guardrails/evaluations

> Dataset labels: **AI-generated synthetic dataset — reviewed by one human (provenance per coordinator); second independent review pending.**
> Requested by the product owner (2026-09-22): use Azure AI Foundry for Evaluations, Guardrails and
> Observability/Monitoring, apply the PRD's HHH and APF frameworks (sections 14-15) where
> applicable, and map the Responsible AI pillars (PRD section 17). This document records what was
> built, what was deliberately scoped down, and why — before the results, per this project's own
> convention of writing the plan before the code.

## Scope decision, made explicit before anything else

**UC4 is a read/compute classification service. It has no tools, no multi-step loop, and takes no
autonomous action** (decisions A6, A19). The PRD's HHH rubric and its "behavioral guardrails"
(section 12.3) were written for the platform's autonomous incident-investigation agent — they ask
about tool allowlists, "no autonomous deletion, account disablement or policy modification," and
human approval before block/revoke/quarantine actions. **None of that applies to UC4**, because
none of it exists here to govern. Rather than force-fit those questions (which would trivially
score "pass" by having nothing to fail), every framework below is **scoped down**: the sub-measures
that assume tools or autonomy are dropped and named as dropped, and every remaining sub-measure is
computed from a real, already-defined metric — nothing here is a new, unvalidated number invented
to fill a template. See decision A34.

**Update (2026-09-23, decision A37):** the statement above is still true of the classifier itself —
`ClassificationService` remains a read/compute service with no tools, no loop, no autonomy, and
nothing in this document's HHH/APF scoping changes. Separately, UC4 now also includes a genuine
agentic component, the **Batch Triage Agent**, which does have real tools, a real bounded loop, and
its own real (not scoped-away) HHH/APF, computed from live runs — see
[`agent-plan.md`](agent-plan.md) and [`agent-engine.md`](agent-engine.md). The two are governed
separately: the agent uses `classify_document` as a tool but cannot change what it decides.

## 1. Evaluations — HHH and APF, scoped (PRD sections 14-15)

`evals/classification/apf.py`, `dataguard-uc4 eval hhh-apf`. Every score carries the raw inputs it
was computed from (`inputs` in the JSON output), so a reader can check the arithmetic rather than
trust a label.

### HHH

| Pillar | PRD's question (agent-scoped) | UC4-scoped formula | Real result (dev, replayed) |
|---|---|---|---|
| Helpful | Did the agent solve the task, use the right tools, avoid missing evidence? | documents with a usable prediction / headline documents (no tool-selection question — there are no tools) | **1.000** (97/97) |
| Honest | Unsupported-claim rate via a grounding judge | 1 − evidence-verification failure rate (the existing exact-substring check; requires a trace file) | **1.000** (with a trace file; `None` without one) |
| Harmless | No autonomous destructive/high-impact action; adversarial safety suite | **high-risk recall** — the harm this service can actually cause is under-classifying a sensitive document as safe, not an autonomous action, because it takes none | **1.000** (111/111 on the locked test; 1.000 on this dev run) |

Dropped, and why: "did it avoid autonomous destructive/high-impact actions" and "did it respect
tool scope/HITL for block/revoke" (section 14.2) — UC4 has no tools and issues no actions; a result
is always a recommendation (`docs/uc4/result-schema.md`).

### APF

`APF = 0.35·Effectiveness + 0.15·Efficiency + 0.25·Reliability + 0.25·Trustworthiness` — the PRD's
own default weights (section 15), a product-risk choice, versioned in `config/eval/apf.v1.yaml`,
not re-derived here.

| Dimension | PRD's measures (agent-scoped) | UC4-scoped formula | Real result (dev, replayed, with a trace file) |
|---|---|---|---|
| Effectiveness | Task success, tool-call correctness | mean(level macro-F1, category macro-F1, high-risk recall) | 0.957 |
| Efficiency | Latency, tokens, tool calls, cost | 1 − (worst-stage P95 latency / the PRD's own 10 s tool-call budget, `gates.v1.yaml tool_call_limit_s`) — **not an invented threshold** | 0.695 |
| Reliability | Run consistency, retry/failure rate, schema compliance | 1 − mean(review rate, schema-failure rate, failed-stage rate) | 1.000 |
| Trustworthiness | Grounding, safety, explainability, human override | reuses HHH's Honest score (evidence-verification rate) | 1.000 |

**Composite: 0.939** (dev, replayed, with a trace file). A missing dimension (no trace file
supplied) is excluded and the remaining weights renormalized — **never treated as zero** — and the
report says so explicitly (`composite_note`).

Reproduce:
```
dataguard-uc4 eval run --classifier hybrid --hybrid-variant default --split dev --llm-mode replay --trace-out spans.jsonl
dataguard-uc4 eval hhh-apf --split dev --llm-mode replay --spans spans.jsonl
```

## 2. Guardrails — input, output, behavioral (PRD section 12)

| Layer | Custom (already existed, tested in CI) | Azure AI Foundry addition |
|---|---|---|
| Input | S0 injection scan (`guardrails/injection.py`); input guard (size/encoding, `guardrails/input.py`) | **Azure AI Content Safety Prompt Shields** — a second opinion, closing the gap `hybrid-engine.md` recorded as "not implemented" |
| Output | Exact-substring evidence verification (`guardrails/output.py verify_quote`); schema validation; "insufficient information" abstention | **Azure AI Content Safety Groundedness Detection** — a second, semantic opinion alongside the exact-substring check |
| Behavioral | No tool allowlist needed for the classifier itself (no tools). The real equivalent: MCP's deny-by-default caller allowlist and per-caller LLM-tier/cost/latency caps (`config/mcp/mcp.v1.yaml`); the hybrid's `max_llm_calls` budget; fail-safe review escalation instead of any autonomous action. **The Batch Triage Agent (A37) does have real behavioral guardrails** — a fixed tool allowlist, a step budget, repeated-tool-failure escalation, and the structurally-enforced never-downgrade invariant; see `agent-plan.md`'s "Guardrails" section and `agent-engine.md` | Not applicable to the classifier. The agent's guardrails are custom/structural, not an Azure AI Foundry addition |

**Deliberate design choice (decision A35): this is an audit-time second opinion, not a per-request
runtime guardrail.** Adding a live external call to the production classify path would introduce a
new failure mode, latency and cost with no measured justification yet. `dataguard-uc4 guardrails
second-opinion` runs the frozen classifier over real documents and reports where the custom
guardrail and Azure agree or disagree; `dataguard-uc4 guardrails azure-check` is a connectivity
self-check. Both are lazy, optional and never contact Azure in a unit test (tested against a local
fake server, exactly like the Foundry adapter, in `test_azure_content_safety.py`).

REST shapes (verified against Microsoft Learn, 2026-09-22 — see the module docstring for sources):
`POST {endpoint}/contentsafety/text:shieldPrompt?api-version=2024-09-01` and
`POST {endpoint}/contentsafety/text:detectGroundedness?api-version=2024-02-15-preview`.

### Status

**Code-complete and tested against a local fake server. Not yet run against a real Azure AI
Content Safety resource** — that needs a resource created the same way `dataguard-uc4-appinsights`
was (see the coordinator note at the end of this document). Once credentials exist:
```
dataguard-uc4 guardrails azure-check
dataguard-uc4 guardrails second-opinion --split dev --llm-mode replay
```
Run today, without credentials, `second-opinion` still reports honestly: every row says
`azure_flagged: null` / `azure_grounded: null` (**not scored**, never treated as agreement) —
```
{"n_documents": 10, "n_scored_by_azure": 0, "agreement_rate": null, ...}  # injection, dev, T5 (10 adversarial docs)
{"n_claims": 15, "n_scored_by_azure": 0, "agreement_rate": null, ...}     # groundedness, first 15 evidence claims
```

## 3. Observability and monitoring (PRD section 16)

Already covered in `docs/uc4/observability-engine.md`. Azure Monitor export is **SDK-confirmed
against a real Application Insights resource, portal-confirmation pending** (a separate PR;
decision D9.20 records exactly what that phrase does and does not claim).

## 4. Responsible AI (PRD section 17), pillar by pillar

| Pillar | PRD requirement | UC4 implementation | Evidence |
|---|---|---|---|
| Privacy & Security | Minimize and protect sensitive context | Synthetic data only; deny-by-default span redaction (only `dg.*` allow-listed keys ever leave the process); content-hash, not text, in traces; input guard rejects oversize/undecodable input | `observability-engine.md` privacy audit; `guardrails/input.py` |
| Fairness & Inclusion | Avoid unfair conclusions from protected/personal attributes | **New**: a counterfactual name-swap probe (`evals/classification/fairness_probe.py`) — classify the same document with the named person's name swapped across a diverse pool of invented names, holding everything else identical | **66 documents probed, 100% invariant, 31 skipped (no name detected)**, rules mode, all development splits (`dataguard-uc4 eval fairness-probe --split all`) |
| Transparency & Control | Users understand why the AI made a recommendation | Evidence spans with locators, a confidence contract (calibrated / uncalibrated / verbalized / none, never conflated), `versions` on every result, human review as the control surface | `result-schema.md`; `app/classification/schemas/confidence.py` |
| Robustness & Safety | Fails safely under bad input/models | Guardrails (above); 13-row failure-injection suite (166 tests, 0 failures); an undecidable document escalates to review, never defaults to a low sensitivity | `observability-engine.md` failure matrix |
| Governance & Accountability | Clear ownership, change control | Every taxonomy/config/prompt/threshold is versioned; `decisions.md` is the audit trail; the locked test split has an access log; CI gates schema drift | `decisions.md`; `data/synthetic/uc4/locked_test_access.jsonl` |

### Fairness & Inclusion — read this before citing the 100% figure

* **What it tests**: whether the SAME content, with only a person's name changed, gets the SAME
  classification. It does not test whether the *gold labels themselves* are fair, and it does not
  have or assume ground truth about any name's demographic association — the substitute names are
  the project's own invented pool (`pools.yaml`: "everything here is invented or generic"), chosen
  to spread across it, not curated by demographic category.
* **What a 100% result means, and does not mean**: on 66 documents that name a person, across
  development splits, rules-mode classification did not change under any of 10 substitute names.
  It does not mean the ML or LLM stages are certified fair (the probe supports any `mode`, and only
  `rules` has been run so far), and it does not mean the gold labels are unbiased.
* **31 skipped documents** had no `Firstname Lastname` pattern detected by the probe's simple
  regex heuristic — not a name-entity recognizer. Some of those may contain names the heuristic
  missed; this is a real limitation, stated rather than hidden.
* **Next step, not yet done, and not as simple as it sounds**: covering the ML and LLM stages needs
  live calls (`--mode hybrid --llm-mode record` or `foundry`), not replay. A name-swapped document
  has a different input hash, so `--llm-mode replay` makes every variant **miss the replay cache**
  and escalate to review regardless of the name — that looks exactly like a mass fairness finding
  but is a cache artifact (confirmed: every "changed" row showed `status: review_required`, no
  decision at all). The CLI now refuses that specific combination rather than silently reporting
  it (`test_hybrid_mode_with_replay_is_refused_as_a_cache_artifact`). Extend `NAME_PAIRS` if a
  reviewer wants broader coverage.

## What was NOT done, and why

| Item | State |
|---|---|
| Azure AI Content Safety run against a real resource | Code-complete, fake-server tested; needs a resource (below) |
| Fairness probe on the ML/LLM stages | Only `rules` mode has been run; the tool supports `hybrid`/`llm` today |
| A live per-request Content Safety guardrail | Deliberately not built (decision A35) — audit-time only, to avoid a new latency/failure mode without measured justification |
| Azure AI Evaluation SDK's own evaluator classes (`azure-ai-evaluation` package) | Not used. The stdlib-HTTP Content Safety client covers the two evaluators that map onto this service's real risks (Prompt Shields, Groundedness); the SDK's agent-oriented evaluators (tool-call accuracy, task adherence) do not apply here, matching the same scoping decision as HHH |

## For the coordinator: creating the Content Safety resource

Same pattern as `dataguard-uc4-appinsights`: Azure portal → create an **Azure AI services** or
**Content Safety** resource in the same resource group (`rg-shirazcse-6271`), any region that
offers Content Safety. Then, in your terminal (not in chat):
```zsh
read -s "DATAGUARD_CONTENT_SAFETY_API_KEY?Content Safety key: "; export DATAGUARD_CONTENT_SAFETY_API_KEY
export DATAGUARD_CONTENT_SAFETY_ENDPOINT='https://<resource-name>.cognitiveservices.azure.com'
dataguard-uc4 guardrails azure-check
```
