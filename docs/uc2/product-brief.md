# Product Brief: Insider Risk Investigation Agent (UC2)

*DataGuard AI · AI Product Manager portfolio · built with Claude Code, Microsoft Foundry, Python,
scikit-learn and GitHub · all data synthetic*

## The problem

Insider-risk programmes fail in two opposite ways:
* **Rules drown analysts.** "More than N downloads" fires on every release week and month-end close.
* **Real exfiltration hides in normal-looking volume.** A low-volume employee's spike matters more
  than a power user's ordinary day, and static thresholds can't tell the difference.
* **Every alert becomes a manual investigation** across behaviour, identity, logs, data sensitivity,
  approvals and policy.
* **An LLM that "decides" is the wrong answer.** Calling someone a malicious insider is a
  high-stakes, potentially discriminatory judgement. A model that reads untrusted logs can also be
  steered by them.

## Who it's for

* **Insider-risk and SOC analysts:** fewer, better-explained cases, with the evidence assembled.
* **Security leads:** consistent, auditable outcomes, and a known cost per case.
* **Employees:** not to be flagged for legitimate work, and never judged by a model.
* **Legal, privacy and HR partners:** no protected or HR data in scoring, and no automated
  employment decisions.

## The solution

1. **Behavioural ML (authoritative for the anomaly).** An Isolation Forest scores each user-day
   against that user's own robust baseline for that kind of day. Contributions are counterfactual:
   how much the score falls if a feature is reset to baseline.
2. **Four bounded agents (they investigate, they don't decide).**
   * The **orchestrator** plans and delegates.
   * The **behavior** agent interprets the anomaly.
   * The **investigator** reconstructs the timeline from logs, approvals, UC4 and UC6.
   * The **risk** agent, which has no tools, synthesizes a recommendation.

   Each has its own tool allow-list, hard budgets and a fresh context. DataGuard, not the model,
   executes delegation.
3. **Reused platform services.**
   * UC4 classifies the files.
   * UC6 answers policy questions with verified citations.
   * Identity is deterministic access context (role family, privilege), not HR data.
4. **A versioned deterministic rubric** decides MONITOR / INVESTIGATE / ESCALATE / HUMAN_REVIEW, with
   every point, floor and trigger shown. Agents can raise an outcome. Recommending two or more
   levels lower sends the case to review.
5. **Analyst authority.** Nothing is executed. The analyst agrees, disagrees or asks for more. That
   feedback drives a **governed learning loop**, where nothing changes without an offline gate and a
   person's approval.

## Flagship moment

u-2043 normally downloads about 15 files a day (baseline median 14.5) and uploads nothing externally (baseline median 0). On 2026-08-25
they download 262 files (78 sensitive, 34 after hours), touch three new repositories, and upload
2.4 GB to a personal Dropbox.
* **Model:** the Isolation Forest scores the day HIGH_ANOMALY (0.7996).
* **Data:** UC4 finds Highly Confidential credentials, financial data and source code.
* **Policy:** UC6 cites the prohibition on Restricted data in personal cloud.

The rubric returns **ESCALATE (115)**. That holds in all three architectures and through the
Foundry agents. The summary says what was observed and what policy says. It never says "malicious".

## Results (indicative: synthetic data, one author, 36 cases)

| | Result |
|---|---|
| Anomaly detection, ROC-AUC | Isolation Forest **0.9942** vs statistical baseline 0.9937 |
| At 1 analyst alert/day | IF precision **0.467**, recall **0.778** vs baseline 0.400 / 0.667 |
| Release-week false alerts (3 alerts/day) | IF **7** vs baseline 15 |
| Full system, 36 cases, live | acceptable **0.861**, **0 critical misses**, 0 unsupported evidence ids, 0 leaks |
| 1 vs 2 vs 4 agents (12 live cases) | **12/12** at 3.2 calls · 10/12 at 6.3 · 11/12 at 7.8 |
| Guardrails (15 live probes) | **0 unsafe outcomes**; Foundry's shields blocked none, the app's layers carried all |
| Foundry Evaluations | deterministic checks **= local, 8/8**; groundedness 45/45 rows |
| Learning loop, first candidate | acceptable 31 → **33**, false reviews 4 → 2, gate PASS, awaiting approval |

## Decisions I made as the PM

* **The ML is authoritative for "how unusual"; agents can't touch the score.** Explanations live
  next to the number; they don't replace it.
* **No protected, HR or notice-period inputs.** UC1 still uses notice period. Harmonising the two
  is recorded as future work, not done silently.
* **Fairness measured by role family only:** alert rates from 1.3% (product) to 8.9% (IT
  operations), reported, not tuned away.
* **The rubric, not the model, owns the outcome.** Changes to it are versioned with a changelog.
* **Run the architecture experiment honestly.** The single agent won on cost and accuracy. I
  recommend it as the default, and multi-agent only where isolation or auditability justifies about
  2.4× the calls.
* **The final iteration was declared in advance.** After investigator v2, no tuning to the test set.
  Remaining misses are kept and explained.
* **Cost is `NOT_ESTIMATED`.** Tokens, calls and latency are measured; I don't invent a dollar
  figure.

## What it is not

Not production-ready, and not validated on real data. No automated employment, disciplinary or
access action. Not a verdict on anyone's intent.
