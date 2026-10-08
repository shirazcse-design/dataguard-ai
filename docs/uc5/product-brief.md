# Product Brief: Data Security Incident Investigation Agent (UC5)

*DataGuard AI · AI Product Manager portfolio · built with Claude Code, Microsoft Foundry, Python and
GitHub · all data synthetic*

## The problem

A possible data exfiltration lands as a single alert, but the answer is spread across six systems:
security logs, DLP, behaviour analytics, entitlements, data classification and policy. Analysts
correlate by hand, the timeline is rebuilt in a spreadsheet, and the report mixes what was observed
with what was assumed. An LLM that "investigates" makes it worse if it invents events, states intent,
or is steered by text inside the evidence.

**Core question:** what happened, what evidence supports it, what was affected, which policies apply,
what remains unknown, and what should the analyst do next?

## Who it's for

* **SOC, data-security and insider-risk analysts:** a correlated timeline and an evidence-backed
  report in seconds, with the gaps named.
* **Security leads:** consistent severity from versioned rules, and a known cost per investigation.
* **Employees:** never judged by a model; an anomaly or a transfer is not treated as intent.
* **Auditors:** every statement traceable to evidence, every decision to a rule.

## The solution

1. **One bounded investigation agent** starts from the alert alone and decides what evidence it needs,
   through 10 read-only tools bound to the case: security logs and behaviour (UC2), access (UC3),
   classification (UC4), destinations (UC1) and policy (UC6). It cannot act.
2. **A deterministic timeline and correlation engine** (the new capability): authoritative timestamps,
   honest about uncertain ordering; rules that link events, flag conflicts and name gaps.
3. **Typed, validated claims:** observed fact, deterministic finding, policy requirement, agent
   inference, or unknown. A claim without valid evidence is excluded.
4. **A severity harness** recomputes the facts itself and sets a LOW / MEDIUM / HIGH floor from
   security evidence only. The agent can raise concern, never lower it.
5. **Analyst authority:** confirm, reject, modify, or ask for more investigation. Only an analyst can
   set CRITICAL (Severity 1 is "confirmed loss"). Nothing is executed.

## Flagship moment

A DLP alert: three files to a personal Dropbox at 23:48. The agent pulls the evidence and the engine
shows a 262-file download that morning, a 2.4 GB upload at 23:00 matched to the alert, all three files
Highly Confidential customer financial data, a high anomaly score, and policy that prohibits personal
cloud. It also names what is **not** known: who controls the Dropbox account. Result: **HIGH, analyst
review, potential Severity 1**, "consistent with a potential data-exposure incident requiring analyst
investigation", and no accusation.

## Results (indicative: synthetic data, one author, 16 incidents)

| | Result |
|---|---|
| Live, agent v2, 16 incidents | severity **15/16**, review **15/16**, **0** floor violations |
| Evidence | completeness **1.0**, tool selection **62/62**, timeline order **27/27** |
| Grounding | **2 of 311** claims unsupported (excluded), policy grounding 48/50 |
| Through the Foundry agent (4) | severity 3/4, review 4/4; p95 28.2 s (PRD target ≤45 s) |
| Foundry Evaluations | 13 string checks **= local**; groundedness 4.06/5 |
| Guardrails | 0 weakened outcomes through the app (11 offline + 10 live probes); Foundry's shields blocked none |
| Privacy | DataGuard telemetry clean live; raw user ids never reach Foundry |
| Cost | ~22K tokens, 4.6 model calls per incident; dollars `NOT_ESTIMATED` |

## Decisions I made as the PM

* **One agent, not a team.** UC2 already shows multi-agent specialisation; UC5's capabilities are
  authoritative services, and agentising them would add latency, cost and coordination risk.
* **A minimal case packet.** The agent must investigate, not summarise a pre-built dossier; the harness
  still gathers its own facts so the decision never depends on the agent's choices.
* **Evidence correlation is code, explanation is AI.** The model may explain a link; it cannot create
  one or move a timestamp.
* **No HR signals in severity.** UC1 uses notice period; UC5 uses security evidence only, and the
  difference is documented.
* **CRITICAL belongs to a person.** Policy defines Severity 1 as confirmed loss; confirmation is a human
  act.
* **One declared fix, then freeze.** Run 1 showed over-escalation and unverified approvals; prompt v2
  fixed both and was the last iteration. Remaining misses are kept and explained.
* **Report what the judges actually say.** Task adherence 0/16 traced to my own prompt-vs-tool
  mismatch, not hidden or tuned away.

## What it is not

Not connected to a real SIEM, DLP or identity system. It never disables, revokes, deletes or notifies.
Not a verdict on anyone's intent. Not validated on real data.
