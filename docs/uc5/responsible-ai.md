# Responsible AI in UC5

## Privacy

* **Synthetic only:** UC2's name-free users and activity, UC4 dev-split documents, fictional hosts.
* **Minimum necessary context:** the agent starts with a case packet holding no evidence; each tool
  returns only the case's own subject, window, files, resources and references.
* **Pseudonyms:** the agent sees a fixed-salt alias, never the user id, including inside tool results
  (verified live: raw user ids reached Foundry 0 times). Telemetry uses a separately salted pseudonym.
* **No protected or HR attributes:** identity is role, privilege, usual hours and expected data
  classes. UC1's employment-status / notice-period signal and its overall risk score are not used
  (decision (c)).
* **Telemetry:** deny-by-default allow-list of 33 `dg.ic.*` keys; local audit clean (497 spans) and live
  canary clean. Foundry records the agent's conversation; kept on for the synthetic demo by the product
  owner, with turning it off listed as a production step.

## Transparency

Every statement in the report carries one of five claim types and the evidence ids behind it; policy
claims carry UC6 citations. Gaps and conflicts are first-class. The page shows which evidence the agent
retrieved and which it did not, which claims were excluded, the agent's recommendation next to the
harness's decision, and why review is required.

## Safety

* **An anomaly is not intent; a transfer is not theft.** An output filter withholds intent, guilt and
  disciplinary wording ("malicious", "stole", "intentional", "should be fired") and any claim that an
  action was taken; the case then goes to review.
* **No remediation:** no tool disables, revokes, deletes, quarantines or notifies. The decision's
  remediation field is `NONE_EXECUTED` by schema.
* **Untrusted text** (log comments, DLP justifications, documents) is data, never instruction: scanned
  and withheld when instruction-like or naming another user or case; document text never reaches the
  agent.
* **The harness owns severity:** the agent can raise concern, never lower a floor; CRITICAL is an
  analyst's act (POL-IR §3).
* Verified: 11 offline probes and 10 live probes, 0 weakened outcomes through the app
  ([`foundry-guardrails-setup.md`](foundry-guardrails-setup.md)).

## Accountability

The analyst owns every action (confirm, reject, set severity incl. CRITICAL, request more
investigation); decisions are demo-only, recorded as simulated, never used as labels. Prompts (v1
frozen, v2), rubric 1.0.0, correlation rules 1.0.0, the agent config and the golden set (hash-frozen)
are versioned; every run is recorded and replayable.

## Controlled improvement

UC5 reuses UC2's governed loop (`dataguard-insider learn`) rather than duplicating it: runs and analyst
feedback → failure, low-confidence and override mining → human-curated evaluation candidates →
candidate change (prompt, tool, rule, harness, retrieval) → offline regression and safety evaluation
against the frozen golden set → human approval → controlled deployment. The runtime agent cannot
rewrite its prompt, change policy or labels, create tools, alter permissions or deploy versions. The
UC5 candidates already identified are listed as future work in [`README.md`](README.md#known-limitations).

## Out of scope (by decision)

New classifiers, anomaly models, RAG systems, access engines or graph databases; real SIEM, SOAR,
identity or DLP integration; employee monitoring; autonomous remediation; additional agents; a UC5 MCP
server; fine-tuning.
