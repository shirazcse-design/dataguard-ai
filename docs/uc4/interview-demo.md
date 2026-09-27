# Interview demo dashboard

> **AI-generated synthetic dataset — reviewed by one human; second independent review pending.** The
> dashboard shows recorded evaluation results and the behaviour of the real code on synthetic
> documents. It shows **no production traffic**, because none exists.

## Purpose

A local web dashboard for demonstrating the Sensitive Data Discovery & Classification Agent in an AI
Product Manager interview. It is a **presentation layer over the existing implementation**: it adds
no classifier, router, agent or metric of its own (decision D9.37).

It walks one story, **Document → discovery → classification → hybrid decision → explanation → human
review → agentic triage → guardrails → evaluation → observability**, across eight pages:

| Page | What it shows | Backed by |
|---|---|---|
| Overview | The problem, six recorded metrics (each with its split), the architecture | completion report, agent eval, a static diagram |
| Classify | A real classification of a document, rendered from the frozen v1.0 result, with evidence highlighted | `ClassificationService` |
| Decision Trace | Which stages actually ran, which were not in the variant, and why | the request's own redacted spans + `routing.v1.yaml` |
| Agent Triage | The Batch Triage Agent's bounded loop, step by step, with its safety checks | `run_batch`, the agent's own spans |
| Human Review | Items the service itself sent to review; approve, override, escalate | review-required results; a demo-only log |
| Evaluations | Dev / calibration / locked test, intervals, the gate stated honestly, confusion matrix, Foundry evals | committed reports |
| Responsible AI / Guardrails | Classifier and agent evals (separately), each guardrail's real status, the fairness probe's scope | `responsible-ai.md`, completion report |
| Observability | Foundry tracing status, this session's traces as a waterfall, privacy by design, the recorded baseline | `observability-engine.md`, session spans |

Every panel carries a badge saying what kind of data it is: **LIVE**, **REPLAY**, **RECORDED
EVALUATION**, **STATIC DOCUMENTATION** or **DEMO-ONLY STATE**.

## Setup

```
pip install -e ".[dev]"                 # the dashboard needs no extra dependency
dataguard-uc4 demo serve                # REPLAY mode, http://127.0.0.1:8765/
```

The server uses only the Python standard library, binds to `127.0.0.1` only (it refuses any other
address), and loads nothing from the internet, so it works offline. Stop it with **Ctrl+C**. Use
`--port 8766` if 8765 is taken.

## Replay mode (the default; no Azure needed)

`dataguard-uc4 demo serve` (or `--mode replay`) makes **no network call**:

* the classifier's LLM stage replays the **recorded** responses in `data/llm_cache/`;
* the agent uses the **offline deterministic planner** (`app/agent/offline_policy.py`), labelled on
  screen as "no model";
* the top badge reads **REPLAY**, and no page calls a replayed result live.

What replay does and does not cover:

* The curated demo documents are real dev-split documents, so replay reproduces the frozen hybrid's
  real answers for them.
* **New text has no recording.** Its LLM stages fail (`llm_error:replay_miss`) and it escalates to
  review. The page says so; it is the correct fail-safe behaviour, not a bug.
* In replay, an LLM stage's latency is the **recorded** latency of the original live call (about 2-3 s),
  and the page labels it that way; the replay itself takes about a millisecond.

## Live mode

`dataguard-uc4 demo serve --mode live` calls real Microsoft Foundry services. The badge reads
**LIVE**. The mode is fixed for the life of the server; **it never falls back to replay silently**: if
a live service cannot start, that page returns an error and says to restart with `--mode replay`.

Set these in your own terminal, never in a file or a chat (the classifier needs the first block, the
agent the second):

```
# classification service (Foundry model deployments)
export DATAGUARD_FOUNDRY_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
read -s "K?Foundry API key: " && export DATAGUARD_FOUNDRY_API_KEY="$K" && unset K
export DATAGUARD_LLM_DEPLOYMENT_SMALL=uc4-llm-small
export DATAGUARD_LLM_DEPLOYMENT_MID=uc4-llm-medium
export DATAGUARD_LLM_DEPLOYMENT_LARGE=uc4-llm-large

# Batch Triage Agent in Foundry Agent Service (Entra ID sign-in, no key)
pip install -e ".[foundry-agents]"
export DATAGUARD_FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
export DATAGUARD_ENTRA_TENANT_ID=<your tenant id>      # needed for a personal Microsoft account

dataguard-uc4 demo serve --mode live
```

* The first agent run opens a browser sign-in (Entra ID). Do it **before** the interview.
* Live classification costs money per call. A document that escalates to the `large` tier can take
  78-245 s, so in LIVE mode stick to documents that the `mid` tier settles.
* **LIVE rehearsal (2026-09-27): every page worked.** Two live classifications (healthcare, public;
  `uc4-llm-medium`, not replayed), the fail-safe review cases, the six-document Agent Triage through
  `dataguard-batch-triage` in Foundry Agent Service (42 s; Entra sign-in went through silently), and
  the session traces on Observability, all badged LIVE. The live planner makes its own tool choices:
  it looked up taxonomy definitions for three documents, gave the PII notes priority *medium* (the
  offline planner says *high*), and its rationale on the over-labelling document calls the embedded
  override "untrusted content". The invariant held on all six. Keep REPLAY as the interview default
  and use LIVE only when there is a reason to.
* **Fixed after the rehearsal (D9.38): LIVE stage latency was over-reported about 2x.** The
  classification service's `telemetry.latency_ms` counted a live LLM call twice (3.6 s reported vs
  1.8 s measured). It now reports the true wall-clock time for live calls; replayed calls still
  report their recorded latency, so REPLAY numbers and every recorded baseline are unchanged.

## 7-minute interview script

Turn on **Demo Mode** (top right). It hides developer controls and shows a guided bar with
**Next Demo Step**. Steps move to the right page and preload the right document; they **never press
Analyze or Run for you**, so nothing happens (and nothing costs money in LIVE) without your click.

| # | Time | Screen | Do | Say |
|---|---|---|---|---|
| 1 | 0:00-0:45 | Overview | Point at the two lanes | "Unclassified data can't be protected. This is a hybrid Rules + ML + LLM classifier. The classification service is deterministic, not an agent: a harness owns every threshold. The agent on the right is separate and genuinely agentic, but it can never set a level itself." |
| 2 | 0:45-1:45 | Classify | Click **Analyze Document** (healthcare) | "PHI, HIGHLY_CONFIDENTIAL, high-risk. High-risk is derived from policy config, never predicted. The highlights are the service's own evidence: a rule match and a verified LLM quote on the same line. The confidence says what kind it is: the LLM's own bucket, not calibrated." |
| 3 | 1:45-2:30 | Decision Trace | Scroll the stages | "Nothing here is inferred. Rules found PHI, but short-circuit is off, so the LLM still ran. ML is disabled in this variant. The large tier wasn't needed. This is replay, so the LLM latency shown is the recorded one." |
| 4 | 2:30-3:10 | Classify | Click **Analyze Document** (case-study draft) | "Same service, LLM tiers turned off: that simulates an outage or a budget cap. Rules alone can't decide, so it escalates to review with **no label**. A missing level is never PUBLIC." |
| 5 | 3:10-3:45 | Human Review | Open the item, choose a level, **Override** | "Review is a flag, never a block. Override needs a deliberate choice: there's no default level. These decisions are logged separately; they never touch gold labels." |
| 6 | 3:45-4:45 | Agent Triage | Click **Run Agent Triage**, open the injection document | "Six documents, one at a time. This one hides an instruction: the scan flagged it and it was treated as data. The safety checks are computed from the trace: the agent's level equals what classify_document returned. The first live run exposed a real bug: the model retyped documents before classifying them. The fix is structural: the tool is bound to the original document, which is the second check here." |
| 7 | 4:45-5:45 | Evaluations | Point at the gate panel | "0.884 strict level F1 on the locked test, but its lower bound, 0.758, misses the 0.85 gate. We adopted a lenient view as a recorded product decision, and the strict number stays on screen. High-risk recall is 111 of 111." |
| 8 | 5:45-6:30 | Responsible AI | Point at the status column | "Every guardrail with its real status. Content Safety is code-complete but only fake-server tested, and the page says so. The fairness probe covers rules mode only." |
| 9 | 6:30-7:00 | Observability | Open the waterfall | "Every request you just saw is one redacted trace: no document text leaves the process. The same spans are verified in Microsoft Foundry's Tracing view." |

If time is short, skip step 8 and fold one sentence of it into step 9.

## Demo scenarios

All documents are real synthetic **dev-split** documents. Outcomes are pinned by unit tests
(`tests/unit/test_demo_classify.py`, `test_demo_agent_review.py`), so a document that stops behaving
as described fails CI rather than an interview.

**Classify page** (`app/demo/examples.py`):

| Document | Doc id | Expected (REPLAY) | Why it is in the demo |
|---|---|---|---|
| Public careers FAQ | `uc4-2154faf7ae` | `ok`, PUBLIC | The easy baseline |
| Team meeting notes | `uc4-53b989fc54` | `ok`, INTERNAL | Ordinary internal content |
| Manager's private notes (PII) | `uc4-288d0c258b` | `ok`, CONFIDENTIAL, high-risk | High-risk comes from policy (PII), not from the level |
| Prescription record (healthcare) | `uc4-19c5bd1307` | `ok`, HIGHLY_CONFIDENTIAL, PHI, high-risk | Rules + LLM evidence on the same lines |
| Customer case-study draft, LLM unavailable | `uc4-1807887255` | `review_required`, no label, `LOW_CONFIDENCE` | The fail-safe: LLM tiers off for the request (`max_llm_tier: none`) |

**Agent Triage page** (`app/demo/agent.py`): public careers FAQ, the manager's notes (PII), the
prescription record (PHI), a CI config with a token (credentials), a process card with an embedded
prompt injection (`prompt_injection_suspected`, handled as data; still HIGHLY_CONFIDENTIAL), and
offsite notes that try to talk the classifier into a higher label (still INTERNAL). In REPLAY every
one completes in three steps (planner, `classify_document`, final answer) with the invariant held.

**Human Review page:** "Add the fail-safe review cases" runs two real classifications with the LLM
tiers off (the draft and the manager's notes); both escalate with no label. Decisions are appended to
`var/demo/reviews.jsonl` (git-ignored) with `not_gold_adjudication: true`.

## Known limitations

The dashboard derives the live list from `completion-report.md` (Evaluations page, "What is still
open"), so it stays current. As of 2026-09-27:

* The data is synthetic and AI-labelled, reviewed by one human; a **second independent review is
  pending**, so the work is **not independently validated**.
* The **strict** level-F1 gate is not met on its lower bound (0.758 < 0.85); the **lenient** gate
  adopted by decision A33 is met. Both are shown.
* The **locked test split is consumed**; re-confirming the gate needs a freshly generated split.
* The 5 dev-split errors in `hn_public_api_docs_placeholder_keys` are **accepted, not fixed** (A39).
* **Azure AI Content Safety** second opinion: code-complete, fake-server tested, **not live
  verified**.
* The **fairness probe** has only been run in rules mode.
* Live agent runs were deliberately kept small (3 documents, the trace check, and the 6-document
  dashboard rehearsal).
* The dashboard is a local, single-user demo: no authentication, no multi-user state, loopback only.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Address already in use` | Another server is on port 8765 | `dataguard-uc4 demo serve --port 8766`, or `pkill -f "dataguard-uc4 demo serve"` |
| Badge says **SERVER UNREACHABLE** | The server stopped | Restart `dataguard-uc4 demo serve`, then reload the page |
| Pasted text always goes to **Review required** in REPLAY | No recording exists for new text (`replay miss`) | Use a demo document, or LIVE mode |
| LIVE: "the classifier did not start" (503) | A Foundry variable is missing | Set the variables above, or restart with `--mode replay` |
| LIVE: "the live planner did not start" (503) | No Entra sign-in, missing project endpoint, or the `foundry-agents` extra | Sign in, set `DATAGUARD_FOUNDRY_PROJECT_ENDPOINT` / `DATAGUARD_ENTRA_TENANT_ID`, `pip install -e ".[foundry-agents]"` |
| Sign-in fails with error **530035** | The directory's security defaults block device-code sign-in | The dashboard uses browser sign-in; set `DATAGUARD_ENTRA_TENANT_ID` |
| LIVE classification takes over a minute | The document escalated to the `large` tier (78-245 s) | Expected; use a document the `mid` tier settles |
| The page looks stale after an update | The browser kept the old page | Reload (the server sends `no-store`), or restart the server for Python changes |
| Interview mode stuck on a step | Step progress is kept per browser tab | Click **Start Demo**, or toggle Demo Mode off and on |

## Implementation

`app/demo/` (server, `metrics.py`, `classify.py`, `agent.py`, `review.py`, `docs_view.py`,
`examples.py`, `static/`), `dataguard-uc4 demo serve` in `app/classification/cli.py`, and tests in
`tests/unit/test_demo_*.py`. Nothing in the classifier, router, schemas, configuration, dataset or
evaluation artifacts was changed for the dashboard.
