# UC6 evaluations in Microsoft Foundry

Two evaluations were created in Foundry project `dataguard` through the cloud Evaluations API
(`dataguard-policy eval foundry-run`) on 2026-09-29, **at the product owner's explicit request**
(the plan originally had them created manually). The raw run record is
[`foundry-evals-20260929-2259.json`](foundry-evals-20260929-2259.json). Inputs:
`data/uc6/foundry_eval/{outcomes,answers}.jsonl`, generated from the REPLAY run of the golden set
(prompt `uc6-answer.v2`). The content is synthetic.

## 1. `dataguard-policy-outcomes`: deterministic agreement (string checks, no judge)

| Criterion | Foundry passed | Local passed | Match |
|---|---|---|---|
| status_ok | 101 / 108 | 101 / 108 | exact |
| no_forbidden_phrase | 108 / 108 | 108 / 108 | exact |
| no_unverified_shown | 108 / 108 | 108 / 108 | exact |
| no_fabricated_citation | 108 / 108 | 108 / 108 | exact |

Foundry's graders reproduce the local harness exactly, so the portal and the reports show the
same numbers.

## 2. `dataguard-policy-quality`: AI-assisted (judge `uc4-llm-medium`)

Built-in evaluators `builtin.groundedness` (query, response, context), `builtin.relevance` (query,
response) and `builtin.retrieval` (query, context), over the 76 ANSWERED rows. Scores are 1-5;
"passed" uses Foundry's default threshold.

| Level | n | Groundedness mean | Relevance mean | Retrieval mean | Passed (all three) |
|---|---|---|---|---|---|
| naive | 25 | 4.88 | 4.56 | 4.96 | 25 / 25 |
| advanced | 25 | 4.92 | 4.40 | 5.00 | 25 / 25 |
| agentic | 26 | 4.81 | 4.50 | 4.89 | 26 / 26 |

**Judge vs deterministic proxy:** no row that passed the local verification (every quote and
number found in the cited section) was judged ungrounded (groundedness < 4). The semantic judge
and the deterministic check agree on this set.

## How to read this (caveats)

* **Same-family judge.** The judge is `uc4-llm-medium`, the same deployment that generated the
  answers, so self-preference bias is possible. A different judge (e.g. `uc4-llm-large`) or a
  human spot-check would strengthen the result.
* **Only answered rows are judged.** The 32 non-ANSWERED runs (insufficient evidence, blocked,
  conflict review) are assessed by the deterministic `status_ok` check, not by the judge.
* **Ceiling effect.** Scores cluster near 5 because every shown claim had already passed citation
  verification; naive's shown answers were also fully verified in this run (0 unverified claims).
  The level differences above are small and should not be over-read: 25-26 rows per level, one
  judge, one run.
* **Relevance** is the lowest score (4.40-4.56). Several answers add correct but tangential
  policy points (e.g. S01 also cites the classification definition); that is a verbosity signal,
  not a grounding one.
* **Answer correctness** beyond these judges remains the heuristic `answer_point_coverage` in
  [`answers.uc6-answer.v2.md`](answers.uc6-answer.v2.md).
