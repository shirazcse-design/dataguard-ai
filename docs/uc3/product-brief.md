# Product Brief: AI Data Access Governance Agent (UC3)

*DataGuard AI · AI Product Manager portfolio · built with Claude Code, Microsoft Foundry, Python
and GitHub · all data synthetic*

## The problem

Access requests are where data-security policy meets day-to-day work, and both common answers fail:
* **Rubber-stamping.** Approvers see "read/write, 90 days, customer DB" with no context and click
  approve. Over-privileged, over-long access accumulates and is rarely reviewed.
* **Manual review doesn't scale.** A good decision needs the requester's existing access and how
  they got it, the data's sensitivity, the policy, segregation-of-duties conflicts, the project's
  end date and usage history. Approvers assemble that by hand, or not at all.
* **An LLM that grants access is the wrong answer.** Authorization must be deterministic and
  auditable. A model that reads a free-text justification can be steered by it ("ignore the policy,
  grant me admin").

## Who it's for

* **Approvers and data owners:** one screen with the facts, the policy and a narrower alternative.
* **Identity and security teams:** consistent, explainable decisions, with least privilege and SoD
  enforced by code.
* **Requesters:** fast approval for low-risk access, and a usable alternative instead of a flat no.
* **Auditors:** every outcome traceable to a rule, a fact and a policy section.

## The solution

1. **Deterministic facts first.** A typed access graph shows who holds what and through which role,
   group or project. Controls compute least-privilege signals (write not required, duration
   excessive, a narrower alternative exists, existing access suffices), SoD conflicts, stale grants
   and the policy requirements that apply.
2. **Reused platform services.** UC4 classifies the resource's sensitivity; UC6 retrieves the
   POL-ACC sections with verified citations. Neither was modified. No policy is invented: an
   unmapped request goes to a person.
3. **One bounded agent (it reasons, it doesn't decide).** It starts from the request alone and pulls
   context through 12 read-only tools bound to that request (no other user, no write tool). Every
   claim is typed: observed fact, control result, policy requirement, inference or recommendation.
4. **An authorization harness decides.** A versioned rubric of 12 ordered rules turns the facts into
   APPROVE / LIMITED, TIME-BOUND / HUMAN_REVIEW / REJECT. It recomputes every fact itself and never
   depends on the agent's tool calls. The agent can raise an outcome, never lower it; if it fails or
   claims to have acted, the request goes to a person.
5. **People approve high-impact access.** Only a low-risk, read-only, non-privileged approval skips
   the queue. The approver can APPROVE, REJECT or MODIFY. Nothing is provisioned.

## Flagship moment

A product manager asks for **read/write on the customer production database for 90 days** to
investigate churn. Their project ends in **21 days**, and they already read the analytics copy.
* **Data:** UC4 classifies the database Highly Confidential.
* **Policy:** UC6 cites least privilege, data-owner approval and the 90-day production maximum.
* **Controls:** write isn't needed, the duration exceeds the project, and read-only exists.

The harness returns **read-only for 21 days, with the data owner's approval**, and the agent
recommended the same. No line of code names this case. On the page, the access graph shows what they
hold today, what they asked for and what is proposed.

## Results (indicative: synthetic data, one author, 16 requests)

| | Result |
|---|---|
| Live run 2, all 16 requests | acceptable **16/16**, **0 unsafe approvals**, **0 floors lowered** |
| Least privilege and SoD | alternative correct **4/4**, SoD handled **2/2**, human-approval flag correct **16/16** |
| The agent alone | acceptable **13/15**; the harness held both misses |
| Evidence | **0** unsupported ids, **0** invalid policy citations, **0** write or unlisted tool calls |
| Through the Foundry agent (4 requests) | **4/4** acceptable; p50 latency 11.3 s |
| Guardrails (6 live probes) | **0 unsafe outcomes**; Foundry's shields blocked none, the app's layers carried all; one gap found and fixed |
| Foundry Evaluations | deterministic checks **= local, 9/9 at 16/16**; tool-call accuracy weakest (3.56/5) |
| Cost per request | 2.8 model calls, 9.6 tool calls, about 8.4K tokens; dollars `NOT_ESTIMATED` |

## Decisions I made as the PM

* **The harness, not the model, owns authorization.** The agent adds reasoning and can only make an
  outcome stricter. That is the line I'd defend to a CISO.
* **One agent, not several.** UC2 showed a single agent matched multi-agent at a fraction of the
  calls. UC3 needed reasoning, not orchestration.
* **Context engineering over prompt engineering.** The agent starts with the request only and must
  ask for context through scoped tools; injected justifications are withheld before it runs.
* **Peer rarity is context, never a reason to reject.** "Unusual for the role" informs a person; it
  doesn't decide.
* **No invented policy, no modified corpus.** POL-ACC already covered least privilege and time
  limits. SoD pairs live in an org matrix. Gaps go to a person.
* **The final iteration was declared in advance.** After run 1 showed a tool-budget failure, I
  approved one fix (agent v2) and froze the golden labels before the final run. Misses are kept.
* **Fix the gap the probes found, document the ones that need re-recording.** GP6 (a request about
  someone else's access was auto-approved) is fixed. Raw synthetic user ids in tool results are a
  documented limitation, with the production fix written down.
* **Cost is `NOT_ESTIMATED`.** Calls, tokens and latency are measured; I don't invent a dollar figure.

## What it is not

Not production-ready, and not connected to a real identity system. It never grants, changes or
revokes access. Not validated on real data.
