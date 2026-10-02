# Product Brief: Agentic DLP & Sensitive Data Protection (UC1)

*DataGuard AI · AI Product Manager portfolio · built with Claude Code, Microsoft Foundry, Python
and GitHub · all data synthetic*

## The problem

Data Loss Prevention misses the files that matter most and overwhelms analysts with the ones that
don't:
* **Pattern rules miss meaning.** An unlabelled acquisition shortlist has no SSN to match.
* **Every alert becomes a manual investigation** across identity, activity, policy and exception
  tools.
* An LLM could help, but one that **decides or acts** on data-loss events is hard to audit and open
  to prompt injection from the content it inspects.

## Who it's for

* **DLP / SOC analysts:** fewer, better-explained cases.
* **Data-protection leads:** consistent, defensible decisions.
* **Policy owners:** to see where policy is unclear.
* **Employees:** not to be blocked for legitimate work.

## The solution

An agent-assisted investigation that **composes** existing platform capabilities:

1. **Deterministic DLP checks** (destination, labels, patterns).
2. **UC4 classification:** semantic sensitivity, e.g. "Highly Confidential, M&A strategy".
3. **Identity and behaviour context:** role, employment status, privilege, 7-day activity bands.
4. **UC6 policy intelligence:** what policy says, with verified citations.
5. **A bounded investigation agent:** 5 read-only tools; checks exceptions and fills gaps; cites
   evidence.
6. **A versioned deterministic rubric:** decides ALLOW / WARN / ESCALATE / HUMAN_REVIEW, with
   every point shown.

**Principle: the model proposes; the harness enforces.** The agent can send a case to a person,
never lower its outcome. No action is automatic: a proposed block is **simulated** and waits for
an analyst's approval.

## Flagship moment

A privileged director uploads `Acquisition_Targets_2027.xlsx` to a personal Dropbox at 23:40. The
file has no label and no pattern.

* UC4 recognises it as M&A strategy.
* UC6 cites the policies that prohibit Restricted data in personal cloud storage.
* The agent confirms there's no approved exception.

The rubric scores **130 → ESCALATE**, with a simulated block awaiting approval. The analyst sees
*why* in one screen.

## Results (30-case golden set, replayed; indicative)

| | Result |
|---|---|
| High-risk recall | **1.0** (19/19) |
| Critical false negatives (high-risk returned ALLOW) | **0**. No target was set in advance |
| Acceptable-outcome accuracy | **0.867** (four misses, all cautious, all kept) |
| False-positive rate | 0.286 (2 of 7 expected-ALLOW cases) |
| Tool-boundary violations / injection resistance | 0 / 3 of 3 |
| Foundry judges: task adherence · groundedness | **28/30 · 30/30** (after fix; were 19 · 23) |
| Foundry deterministic checks vs local harness | identical |

## Decisions I made (and why)

| Decision | Why |
|---|---|
| A rubric, not the model, owns the outcome | Auditability, stability across model versions, no injection path to the decision |
| The agent gets 5 read-only tools; classification and identity stay mandatory stages | A small, evaluable agent; facts aren't optional |
| An exception counts only if the tool returned it | The model can't invent an approval |
| An explicit UC4→UC6 mapping table | Two independently built taxonomies; join them visibly, don't let an LLM guess |
| Extend the existing MCP server; no action tool exposed | One integration surface; enforcement stays behind human approval |
| Keep misses; version every rubric change | Credibility over a tuned score |

## What the evaluation taught us

Foundry's AI judges found that the agent's findings sometimes cited the wrong evidence. Our
deterministic check couldn't see it, because the ids existed. Root cause: half the evidence had no
ids. **v2 gave every piece of evidence an id**, and groundedness went from 23/30 to 30/30.
Deterministic checks give trust; semantic judges give insight. You need both.

## Risks and limitations

* A small, single-author evaluation.
* A known rubric gap (Public data to personal cloud).
* Paraphrased social engineering in justifications is not detected by either guardrail layer.
* Foundry's own agent telemetry records the evidence pack. It is kept on for the synthetic demo;
  off or pseudonymised in production.
* Behaviour bands are simple. UC2's anomaly detection plugs into the same seam.

## Next

* Rubric 1.2.0 on a new held-out set.
* Pseudonymised evidence pack.
* UC2 behaviour.
* A real enforcement point behind the same human approval.
