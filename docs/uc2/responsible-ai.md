# UC2 Responsible AI

Insider-risk tooling is about people, so a mistake can harm someone. UC2's controls are
**structural** (what the system can see, call and decide), not requests in a prompt.

## 1. What the system cannot see

| Excluded input | Why | How it's enforced |
|---|---|---|
| Protected characteristics (age, gender, ethnicity, religion, disability, nationality and others) | Discrimination risk; not relevant to behaviour | The synthetic schema has no such field; identity returns access context only |
| HR and performance data, disciplinary history, employment status, **notice period** | Would turn behaviour triage into a judgement about a person | Not in the data, the tools or the rubric |
| Names | Not needed to triage behaviour | Users are `u-2001`…`u-2060`; telemetry uses a salted pseudonym |
| Document text | Not needed; a leakage and injection risk | UC4 returns a level and categories only |

**Known inconsistency, recorded and not hidden:** UC1's DLP rubric still uses notice period.
Harmonising the platform (remove it from UC1, or justify it) is **FUTURE** work for the product
owner (decision Z1).

## 2. Anomaly ≠ malicious intent

* **The analyst summary states the principle.** Its anomaly line always ends "*An anomaly is not
  evidence of intent.*"
* **Every summary line carries a claim type:** observed fact, inferred anomaly, policy requirement
  or agent interpretation.
* **Guilt, intent and employment wording is withheld from agent output.** Examples: malicious,
  theft, termination, disciplinary. Any hit sends the case to HUMAN_REVIEW
  (`unsupported_conclusion`). In live run 3 it fired 0 times on the golden set and once on the guilt
  lure probe (GA4).
* **The outcome vocabulary is about review urgency,** never about the person.

## 3. Human authority

* **No action tool exists.** Nothing can disable an account, revoke access, contact a manager or
  open an HR case. MCP exposes four read-only tools.
* **Analysts decide.** Their decisions are recorded as simulated demo state, never as evaluation
  labels.
* **The rubric is versioned and owned by people.** Every change has an approver and a changelog.
* **Learning loop changes need human approval.** Candidates pass an offline safety gate first.
  Changes that would *lower* a high-risk outcome never become automatic candidates. See
  [`learning-loop.md`](learning-loop.md).

## 4. Fairness

Measured **by role family only**, the only grouping in the data. On normal holdout days, the
Isolation Forest alert rate ranges from **1.3% (product) to 8.9% (IT operations)**: high-variance
roles alert more.
* **Reported, not tuned away.** An operator can set per-family review budgets.
* **Role-context gating** (rubric 1.2.0) stops data that is normal for a role (finance data at
  month-end) from adding risk points.
* **Not done:** fairness across protected groups (no such data, by design), and real-population
  validation. See [`ml-design.md`](ml-design.md#4-evaluation-holdout-labels-used-only-here).

## 5. Security of the AI system

* **Untrusted log text:**
  * scanned and withheld when instruction-like;
  * the orchestrator and risk agent never see raw logs (0 canary leaks in 36 live cases);
  * agents run with fresh, isolated contexts.
* **Bounded agents:** per-agent allow-lists, typed arguments bound to the case, and hard budgets.
* **The anomaly score is immutable to agents:** a modified score is restored and logged.
* **Foundry guardrail** `uc2-insider-risk-guardrail` is on all four agents:
  * content filters at Medium;
  * jailbreak shield;
  * indirect-attack shield on input and tool output;
  * protected material.

  Live probes (GA1-GA15): **0 unsafe outcomes**. Foundry's shields blocked none of them; the app
  scanner, the output filter and the rubric carried every case. See
  [`foundry-guardrails-setup.md`](foundry-guardrails-setup.md).

## 6. Privacy and observability

* **DataGuard telemetry is allow-listed:** codes, scores, counts and a salted subject pseudonym.
  The privacy audit is clean (1,403 spans).
* **The live canary in App Insights** appeared 0 times in DataGuard spans.
* **Finding:** Foundry Agent Service records the agents' conversations (user id, repository and
  host names; never document text or withheld text). The product owner kept it on for the
  synthetic demo (decision (a)). **Before production:** turn it off, or pseudonymise payloads. See
  [`foundry-observability-setup.md`](foundry-observability-setup.md).

## 7. Transparency

* **Every case** carries reason codes for every point, floor, ceiling and trigger.
* **The dashboard** labels each panel LIVE, REPLAY, SIMULATED or DEMO-ONLY.
* **Misses, failed runs and superseded results are kept** in `results/`.

## 8. Limits of these claims

Synthetic data, one author, small samples, one model family. These controls make the system
auditable and conservative. They don't make it validated. Production use would need:
* a privacy impact assessment;
* works-council or legal review where required;
* real-population fairness testing;
* independent red-teaming.
