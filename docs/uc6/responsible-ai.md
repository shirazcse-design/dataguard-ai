# UC6 responsible AI

## 1. Grounding and hallucination control

The Copilot must not answer policy questions from the model's general knowledge. That rule is
enforced by structure, not by asking the model nicely:

1. **Retrieval first.** The model sees only retrieved policy sections, delimited as data.
2. **Labels, not references.** Evidence arrives as `E1..En`. The model cannot type a real
   section number, and the harness fills in policy, section and version from metadata.
3. **Verification.** Each claim needs a label that was sent, a quote found verbatim in that
   section, and no number that is absent from it. Claims that fail are dropped, with the reason
   shown.
4. **No free text.** The shown answer is the verified claims only; there is no unverified summary.
5. **Insufficient evidence is a feature.** If nothing survives verification, or the model says the
   evidence does not answer the question, the result is INSUFFICIENT_EVIDENCE and a policy owner
   can be alerted to a coverage gap. Advanced RAG declined all 6 out-of-corpus questions and never
   wrongly declined an answerable one.
6. **A judge layer on top.** Foundry's groundedness evaluator scored 4.8-4.9/5, with no disagreement
   with the deterministic check. It is a same-family judge, so this is corroboration, not proof.

What verification does **not** guarantee: that a sentence faithfully paraphrases its quote beyond
the numbers in it. That is the job of the judge layer and human review.

## 2. Conflicting policies

* **Superseded versions** (retention v1.0 → v2.0): the current version wins **only** because
  metadata says so (`status`, `supersedes`, `effective_date`). The older text is still shown,
  labelled superseded. No review is needed.
* **Current policies that disagree** (Acceptable Use §5.3 vs DLP §4.2 on Internal data): CONFLICT_REVIEW.
  Both sources are shown, neither is preferred, and a person decides. The model may *report* a
  conflict, but the harness checks that it names real evidence from two policies, and the model
  never *resolves* one.
* Over-escalation seen in prompt v1 (flagging an Internal-data conflict for a Confidential-data
  question) was fixed in v2 with a post-hoc prompt change, which is labelled as such.

## 3. Guardrail layers

| Layer | Controls | Verified |
|---|---|---|
| Application input | size limit; UC6 injection lexicon (blocks) | G1, G7 blocked |
| Application evidence | draft filter; withholding flagged chunks; evidence as data | G4 withheld |
| Application output | citation, quote and number verification; conflict rules | G8 dropped |
| Foundry model filter (`CustomContentFilter412`, shared with UC4, unchanged) | Prompt Shields jailbreak (block), indirect (annotate), content categories | G2, G3 blocked |
| Foundry agent guardrail (`uc6-policy-copilot-guardrail`, agent only) | jailbreak block; indirect injection block on user input + tool output; content medium block; protected material | G2, G3 blocked on the agent path |

Findings kept visible, not hidden: the lexicon and Prompt Shields catch *different* attacks; a
jailbreak diluted inside the RAG prompt was not detected; the tool-output injection block did not
fire on the poisoned result; and a violent request (G5) was stopped by no layer. **G5 was accepted
for the synthetic demo by the product owner**, with a production follow-up recorded.

## 4. Privacy

* **Telemetry:** deny-by-default allow-list, with no question, query, policy text, quote or answer
  in spans. A local audit found 608 spans clean against all questions and policy sections. A live
  canary search in Application Insights found 0 occurrences in DataGuard's spans.
* **Foundry's own content recording:** Agent Service stored the agent conversation, including the
  canary question, in `genAIContent`. That is outside the application's control. It was **kept on
  for the synthetic demo** by owner decision. Production guidance: turn it off, or route sensitive
  questions through the application's own agent path, and restrict access to the Application
  Insights resource.
* **Evaluation data sent to Foundry** is the synthetic corpus and questions only.
* **Recordings** store hashes, vectors and model outputs of synthetic content, never credentials.
  Keys were entered at hidden prompts and never stored.

## 5. Human oversight

Review is triggered for insufficient evidence, verified conflicts, unverified claims removed,
guardrail events, withheld evidence, generation failures and agent-requested reviews. The
dashboard explains each reason in plain English. Reviewer decisions (confirm / escalate to policy
owner / record policy gap) go to a demo-only log marked `not_gold_adjudication` and are **never**
written back to the golden set.

## 6. What the agent may and may not decide

| May | May not |
|---|---|
| Whether and what to search; refine a search; read an exact section; check versions | Modify policies or evidence (no write tools exist) |
| Ask for human review | Remove a review the harness raised |
| Propose claims with quotes | Cite anything its tools did not return (the citation is dropped) |
| Report a possible conflict | Resolve a conflict between current policies |
| Stop with "insufficient evidence" | Exceed 6 tool calls or 8 turns (it is stopped with no answer) |

## 7. Honest scope

* Synthetic corpus of 8 policies. One author wrote the corpus, the questions and the code, so the
  results are **not independently validated**.
* The evidence-gate threshold (0.35) and prompt v2 were set after seeing golden-set results. Both
  are labelled post-hoc; neither was tuned on a held-out set, because none exists.
* The retrieval settings were pre-registered and **not** tuned (Option A), even though dense-only
  retrieval scored higher recall.
* Same-family LLM judge; 25-26 judged rows per level.
