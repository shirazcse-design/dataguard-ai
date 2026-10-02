# UC1 responsible AI: guardrails, observability, human oversight

## 1. Principles, and where each is enforced

| Principle | Enforced by |
|---|---|
| The model proposes; the harness enforces | `harness.decide()`: rubric, floors, triggers; the agent's proposal can only raise |
| No autonomous or irreversible action | No tool can act; every action is a SIMULATED proposal; ESCALATE requires analyst approval |
| Least privilege | 5 read-only tools, event-bound exception lookup, MCP per-tool caller allow-list, UC4 caller capped at the mid tier with no evidence excerpts |
| Data minimisation | The agent never sees document text or filenames; telemetry carries only fixed codes and counts |
| Untrusted input is data | Justifications are scanned and withheld if flagged; document injection is flagged by UC4; tool results and policy text are labelled as data in the prompt |
| Fail safe | Missing or failed context is a review trigger, never an ALLOW; replay misses are reported, not substituted |
| Explainability | Every point is a reason code; every finding cites an evidence id; every policy claim is citation-verified by UC6 |
| Honest labelling | LIVE / REPLAY / RECORDED / SIMULATED / DEMO-ONLY on every dashboard panel |

## 2. Guardrails: two layers

**Application (always on, in code):** the controls above. **Foundry:**
`uc1-dlp-investigator-guardrail` on the agent only:
* jailbreak block;
* indirect-attack block on input and on tool output;
* content filters at Medium;
* protected material.

It is not on the shared deployment, so UC4 is unchanged.

Live verification (GU1-GU7, [`results/guardrails-verification.md`](results/guardrails-verification.md);
details in [`foundry-guardrails-setup.md`](foundry-guardrails-setup.md)):

* **No probe produced an unsafe outcome**, and the agent made no attempt to call a tool it does not
  have.
* The layers complement each other:
  * the app lexicon withheld trigger-word injections that Foundry did not flag;
  * Foundry's jailbreak shield blocked a raw injection directly;
  * UC4 flagged injection inside a document.
* **Gaps, documented:**
  * a paraphrased role-play justification was detected by neither layer (GU2);
  * Foundry's tool-output shield did not fire on our tool-result format (GU7).

  The agent ignored both, and the harness holds the decision regardless.

The probes ran against agent v2 (the v1 prompt). v3 (prompt v2) has the same guardrail; the probes
were not re-run.

## 3. Observability and privacy

* The shared tracer has a **deny-by-default allow-list**. UC1 adds only `dg.dlp.*` keys: case id
  (schema-validated), outcome, band, score, risk level, reason codes, simulated faults, destination
  **class**, behaviour band and counts. Never a user id, host, filename, justification, document
  text, policy text or tool arguments.
* **Local audit:** all 30 cases, including every simulated fault, through the production redactor.
  CLEAN across 649 spans, 104 sources, and 62 user-id / host / justification checks
  ([`results/observability.md`](results/observability.md)).
* **Live canary** in App Insights:
  * DataGuard's spans contained the canary **0** times;
  * document text and the filename appear **0** times anywhere;
  * **Foundry Agent Service's own telemetry records the agent conversation** (the evidence pack:
    user id, host, justification).

  The product owner decided to keep that on for the synthetic demo. For production: turn it off,
  or pseudonymise those fields in the pack.
* The live check also found that the exporter's default sampler dropped spans mid-trace. The
  shared sink now defaults to `always_on`.

## 4. Human oversight

* ESCALATE and HUMAN_REVIEW go to an analyst with the reason codes, the cited policy and the
  agent's findings.
* Analyst decisions in the demo are logged as demo-only and never feed evaluation data.
* The agent can **ask** for review (`request_human_review`), and a stricter agent opinion routes to
  review rather than being applied or discarded.

## 5. Data

All synthetic:
* a fictional company (Harbourline Group), ten fictional users and three exceptions;
* fictional companies on the flagship spreadsheet (Kestrel Sensor Systems, Ardent Flowworks,
  Bluefen Analytics);
* `.example` partner domains.

UC4 documents come from its synthetic dev split. No real company, person or customer data is used.
