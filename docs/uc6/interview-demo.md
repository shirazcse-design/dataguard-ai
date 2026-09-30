# UC6 interview demo: Data Security Policy Copilot

## Start

```
dataguard-uc4 demo serve --mode replay          # no Azure needed
open http://127.0.0.1:8765/#policy
```

The badge says **REPLAY**: recorded embeddings, recorded `uc4-llm-medium` answers and recorded
agent turns, all run through the real pipeline. Every curated question is recorded at all three
levels; the Foundry agent is recorded for S01, M01, I02, C02 and X01. An unrecorded question in
REPLAY is answered honestly: no model call is possible, so it is UNAVAILABLE, never invented.

LIVE (optional): `dataguard-uc4 demo serve --mode live` with `DATAGUARD_FOUNDRY_ENDPOINT`
(`https://dataguard-resource.cognitiveservices.azure.com`), `DATAGUARD_FOUNDRY_API_KEY`,
`DATAGUARD_LLM_DEPLOYMENT_MID=uc4-llm-medium` and `DATAGUARD_EMBEDDING_DEPLOYMENT=uc6-embed-small`.
For the Foundry agent, also set `DATAGUARD_FOUNDRY_PROJECT_ENDPOINT` and `DATAGUARD_TENANT_ID`
(the browser sign-in appears on first use).

## 7-minute script

| Min | Do | Say |
|---|---|---|
| 0:00 | Policy Copilot page | "UC6 answers data-security policy questions only from retrieved policy text, with verified citations. It's also the shared policy layer the DLP, insider-risk, access and incident agents will call." |
| 0:45 | **S01**, Advanced → Ask | "Answered, three verified citations. Scroll to the evidence: the highlighted text is the exact quote each claim was checked against. The model never typed a section number; it cited E-labels and the harness mapped them." |
| 1:45 | Decision trace + retrieval trace | "Only stages that ran are listed. Query expansion added 'Dropbox, Google Drive'; hybrid keyword + embedding search; three unapproved drafts filtered; reranked with scores you can recompute." |
| 2:30 | **I02** CCTV retention | "Insufficient evidence, and that's a good outcome. The retention policy exists but doesn't cover CCTV, so the Copilot doesn't guess; a policy owner can be told about the gap." |
| 3:10 | **C02** financial retention | "Two versions of the retention policy. v2.0 supersedes v1.0 in the metadata, so the harness keeps the 7-year answer and shows the old 10-year text as superseded. The model didn't decide that; metadata did." |
| 3:40 | **C01** Internal data in personal cloud | "Two *current* policies disagree. No metadata says which wins, so it goes to human review with both sources shown. Click Escalate to policy owner: logged, never written back to the evaluation gold data." |
| 4:20 | **X01** injection | "Blocked by the input guard before retrieval or any model call." Mention the live guardrail test: "the app's pattern list and Foundry's Prompt Shields caught *different* attacks, which is why we run both." |
| 5:00 | Agentic + **Foundry agent**, **I02** | "The same question through `dataguard-policy-copilot` in Foundry Agent Service: three refined searches, then it asked for human review. Tools run in the app, so the allow-list, 6-call budget and citation checks still apply." |
| 5:50 | Evaluations page → UC6 panels | "Retrieval and answers are evaluated separately. Surprise: plain embedding search had the best recall. We reported it as measured instead of tuning on the test set. Advanced is the most reliable end to end: 97% correct status, all out-of-corpus questions declined, all injections stopped, zero fabricated citations. Foundry's judge scored groundedness 4.8-4.9 out of 5, and Foundry's own graders reproduced our local numbers exactly." |
| 6:40 | Observability page → UC6 panels | "Telemetry carries ids and counts, never questions or policy text. We proved it with a canary word in Application Insights, and found that Foundry's *agent* service stores conversations itself: a platform setting to turn off in production." |

## Likely questions (short answers)

1. **Why RAG, not fine-tuning?** Policies change and must be cited. RAG updates by re-indexing,
   cites the exact clause, and can say "not covered". Fine-tuning bakes in text that goes stale,
   cannot cite, and hallucinates confidently.
2. **Why evaluate retrieval and answers separately?** Different failure, different fix. A wrong
   answer from missing evidence is a retrieval problem (Recall@K); a wrong answer from good
   evidence is a generation problem (groundedness, citations). Here, dense retrieval had the best
   recall, while Advanced had the best answers.
3. **Naive vs Advanced vs Agentic?** The same components with different switches. Naive is fast
   and exposed (it read the poisoned draft and let an injection through). Advanced adds query
   expansion, hybrid search, filter, rerank, gate and enforced verification. Agentic adds a
   bounded agent that re-searches, giving higher citation recall at about 3× the tokens.
4. **Why do citations matter?** Auditability: a reviewer can check the clause in seconds. They
   are also the hook for verification: an unverifiable claim is dropped.
5. **How is hallucination controlled?** Structurally: labels instead of section numbers, verbatim
   quote and number checks, no free-text summary, "no verified claim means insufficient evidence",
   and a judge layer on top.
6. **What happens with insufficient evidence?** An explicit status, a review reason, and a
   policy-gap signal to the owner. 6 of 6 out-of-corpus questions were declined at Advanced.
7. **Conflicting policies?** Superseded versions are resolved by metadata; conflicts between
   current policies go to a human, never to the model.
8. **What may the agent decide?** What to search and when to ask for review. It cannot change
   evidence, cite outside its tool results, resolve conflicts, or exceed 6 calls.
9. **Why both application and Foundry guardrails?** Measured: the lexicon caught phrasings Prompt
   Shields ignored, Prompt Shields caught paraphrases the lexicon missed, and a violent request got
   past both, which is a documented gap.
10. **How are RAG and the agent evaluated?** A 36-question golden set with deterministic metrics
    first (labelled), heuristics second, then an LLM judge in Foundry, cross-checked against the
    deterministic results.
11. **How is it observed safely?** Allow-listed span attributes, an automated leak audit and a
    live canary. The canary revealed Foundry's own content recording, which is a production
    setting to turn off.
12. **How does it serve UC1/2/3/5?** Through a `PolicyAnswer` contract and a `search_policy` tool,
    next exposed over MCP. DLP gets the cited clause for its decision, Access Governance the
    access-standard rule, and Incident Investigation the notification deadlines.

## If something goes wrong

* A page panel says "unavailable": the committed results file is missing. Run `dataguard-policy
  eval answers` / `obs report` (both offline).
* An answer is UNAVAILABLE in REPLAY: the question is not recorded. Use a curated question.
* Port busy: `--port 8770`.
