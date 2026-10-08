# Data Security Incident Investigation Agent (UC5)

A capstone that composes DataGuard's existing capabilities into one auditable incident investigation:
* **one bounded agent** plans the investigation and pulls evidence through **10 read-only, case-bound
  tools** over UC1-UC6;
* a **deterministic timeline and correlation engine** (UC5's main new capability) orders the evidence,
  links it, and names conflicts and gaps;
* a **versioned severity harness** gathers the authoritative facts itself and sets a LOW / MEDIUM /
  HIGH floor; the agent can raise concern, never lower it;
* **an analyst** decides. Only an analyst can set CRITICAL. Nothing disables, revokes, deletes or
  notifies.

> **AI investigates and correlates evidence. Deterministic controls validate security facts. Humans
> own consequential actions.**

> *Flagship (INC-001):* a DLP alert at 23:48 on 2026-08-25: a product manager's account uploads three
> files to a personal Dropbox. The agent starts with only that alert. It retrieves the security log
> (a 262-file bulk download at 11:00, a 2.4 GB upload at 23:00), UC4's classification (all three files
> HIGHLY_CONFIDENTIAL, FINANCIAL_PCI), UC1's destination class (personal cloud), UC2's anomaly (HIGH,
> 0.7996), UC3's access path (an active project grant) and UC6's policy (personal cloud prohibited).
> The engine links the upload to the alert, the download to the transfer, the transfer to after-hours
> activity, and names the gap: **the evidence does not establish who controls the Dropbox account.**
> The harness sets **HIGH, analyst review required, potential Severity 1** (POL-IR §3: only an analyst
> can confirm "confirmed loss"). The report says the evidence "is consistent with a potential
> data-exposure incident requiring analyst investigation", never that anyone is malicious.

All data is synthetic: UC2's name-free users and activity, UC4 dev-split documents, fictional hosts
and policies. No case ID or label appears in runtime code.

## Status (2026-10-07)

Labels: **IMPLEMENTED** (code exists) · **TESTED** (unit tests) · **REPLAY-VERIFIED** (recorded runs
reproduce exactly offline) · **LIVE-VERIFIED** (measured against Foundry) · **FUTURE** (not built).

| Area | State | Evidence |
|---|---|---|
| Evidence ledger, five claim types, content-derived ids | IMPLEMENTED, TESTED | [`architecture.md`](architecture.md) |
| Deterministic timeline (precision-preserving, order-uncertain marks) and 10 correlation rules | IMPLEMENTED, TESTED, LIVE-VERIFIED | [`architecture.md`](architecture.md#3-timeline-and-correlation) |
| Reuse of UC1 (destination catalogue), UC2 (logs, behaviour, identity, approvals), UC3 (access engine), UC4, UC6 | IMPLEMENTED, TESTED | [`architecture-decisions.md`](architecture-decisions.md) |
| One bounded agent `dataguard-incident-investigator` (v2, final), 10 read-only tools | IMPLEMENTED, TESTED, LIVE-VERIFIED | [`architecture.md`](architecture.md#4-the-agent) |
| Severity harness, rubric 1.0.0; review status; CRITICAL analyst-only | IMPLEMENTED, TESTED | [`architecture.md`](architecture.md#5-severity-and-review) |
| 16-incident golden set, frozen before evaluation (sha256 `5e1f46af`) | LIVE-VERIFIED (run 2), REPLAY-VERIFIED | [`evaluation.md`](evaluation.md) |
| Foundry agent and guardrail (created at the product owner's request) | LIVE-VERIFIED | [`foundry-guardrails-setup.md`](foundry-guardrails-setup.md) |
| Foundry Evaluations (13 string checks, 4 judges) | LIVE-VERIFIED | [`foundry-evals-setup.md`](foundry-evals-setup.md) |
| Adversarial suite: 11 offline probes, 10 live probes | TESTED, LIVE-VERIFIED | [`foundry-guardrails-setup.md`](foundry-guardrails-setup.md) |
| Telemetry privacy audit (local) and live canary (App Insights) | REPLAY-VERIFIED, LIVE-VERIFIED | [`foundry-observability-setup.md`](foundry-observability-setup.md) |
| Dashboard page and UC5 panels on the shared pages | IMPLEMENTED, TESTED | [`demo-guide.md`](demo-guide.md) |
| Controlled learning loop | Reused from UC2 (documented, not duplicated) | [`responsible-ai.md`](responsible-ai.md#controlled-improvement) |
| Real SIEM/SOAR, identity or DLP integration; remediation | FUTURE | [`responsible-ai.md`](responsible-ai.md) |

Headline (all 16 golden incidents, live run 2 with agent v2 on Chat Completions; replay reproduces it
exactly):
* severity acceptable **15/16**, review correct **15/16**, **0 severity-floor violations**;
* evidence completeness **1.0**, tool selection **62/62**, timeline order **27/27**, correlations,
  gaps and conflicts found **30/31**;
* **2 unsupported claims out of 311** (0.6%), policy grounding 48/50, **0** case-boundary violations,
  **0** intent or action wording;
* about 4.6 model calls, 10.3 tool calls and 22K tokens per incident; cost `NOT_ESTIMATED`.

Through the Foundry agent v2 (4 demo incidents): severity 3/4, review 4/4, evidence 1.0, p50 21.6 s,
p95 28.2 s. 16 incidents written by one author: indicative, not validated.

## Known limitations

* **INC-014 rubric gap.** The rubric weighs the case files' sensitivity; an upload of *unknown*
  content to a restricted destination (a gist) is not weighted. The agent flagged it and raised to
  MEDIUM; the frozen label says LOW. Future: a rule for content-unknown transfers.
* **Prompt / tool-schema mismatch.** The prompt lists tool signatures without the optional `subject`
  argument and names review reasons differently from the tool's enum; Foundry's task-adherence judge
  failed all 16 runs largely on this. The harness is unaffected. Future: align them.
* **Tool habits.** The agent calls most tools on most cases (~10 per incident) and once passed a
  placeholder approval reference. Future: reject placeholder references before the call.
* **The Foundry guardrail covers the Foundry agent path only**, and blocked none of the live probes;
  DataGuard's own layers carried them.
* **Foundry records agent content** (evidence text the agent reads, and evaluation explanations); kept
  on for the synthetic demo by the product owner. Raw user ids never reach Foundry.
* **Offline vs live policy answers differ.** UC6 answered INC-009 "requires approval" live where the
  offline mode found it insufficient; one expected gap is missing in live runs.
* **Small, synthetic, single author:** 16 incidents, one judge run.

## Documents

| Document | What it covers |
|---|---|
| [`product-brief.md`](product-brief.md) | The interview one-pager |
| [`architecture.md`](architecture.md) | Flow, evidence model, timeline and correlation, agent and tools, severity and review, reuse |
| [`architecture-decisions.md`](architecture-decisions.md) | Decisions (a)-(e) and the refinements |
| [`evaluation.md`](evaluation.md) | Golden set, metrics, runs 1-2, Foundry Evaluations, misses |
| [`responsible-ai.md`](responsible-ai.md) | Privacy, transparency, safety, accountability, controlled improvement |
| [`demo-guide.md`](demo-guide.md) | How to run the demo and a 5-minute script |
| [`foundry-guardrails-setup.md`](foundry-guardrails-setup.md) | Guardrail settings and the offline and live probes |
| [`foundry-observability-setup.md`](foundry-observability-setup.md) | Span tree, privacy audit, live canary, decision (a) |
| [`foundry-evals-setup.md`](foundry-evals-setup.md) | The two Foundry evaluations and the judges' reasons |
| [`foundry/`](foundry/) | The agent's instructions, tool schemas and I/O schemas |
| [`results/`](results/) | Generated reports |

## Commands (`dataguard-incident`)

| Command | Network |
|---|---|
| `data build` | none |
| `investigate INC-001 [--mode offline\|replay\|live] [--backend chat-completions\|foundry-service] [--json]` | replay/offline: none |
| `eval [--mode offline\|replay] [--backend ...] [--ids ...]` | none |
| `obs report` · `guardrails verify` | none |
| `agent export` · `foundry-eval` (rows) | none |
| `record [--backend ...] [--ids ...] --label ...` | LIVE (key or Entra) |
| `agent register` · `obs live-check` · `guardrails verify --live` · `foundry-eval --run\|--fetch` | LIVE (Entra) |

Dashboard: `dataguard-uc4 demo serve --mode replay`, then **Data Security Incident Investigation Agent
→ Incident Investigation**. Credentials come only from the environment or an Entra browser sign-in.

## Code map

| Path | What |
|---|---|
| `app/incident/synth.py` | 16 incidents on UC2 user-days; the UC5 access dataset (UC3 schema) |
| `app/incident/schemas.py` | Incident, case packet, evidence item, timeline entry, correlation, report, decision |
| `app/incident/services.py` | Evidence ledger and adapters over UC1-UC6 |
| `app/incident/correlate.py` | Timeline and correlation rules |
| `app/incident/tools.py` | The 10 read-only tools |
| `app/incident/validate.py` | Claim validation; intent and action filter |
| `app/incident/harness.py` | Authoritative facts and the severity rubric |
| `app/incident/pipeline.py`, `service.py`, `cli.py` | Pipeline and spans, wiring, CLI |
| `config/incident/` | `agent.v1.yaml` (frozen), `agent.v2.yaml`, `rubric.v1.yaml`, `correlation.v1.yaml` |
| `prompts/uc5/` | `agent.v1.md` (frozen), `agent.v2.md` |
| `evals/incident/` | Golden set, evaluation, adversarial suite, observability report, Foundry evaluations |
| `app/demo/incident.py`, `app/demo/static/incident.js` | The dashboard page |
