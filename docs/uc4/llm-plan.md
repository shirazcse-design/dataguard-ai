# UC4 LLM classifier (Approach C): pre-registered plan

**Status: committed BEFORE any LLM code is written.** It fixes design, data-use and reporting rules so
results cannot be shaped after the fact. Deviations go in the results document.

> Dataset labels: **AI-generated synthetic dataset — pending human gold-label review.**

## Scope of this phase

Approved: Phase 5 only (architecture plan section 20): provider interface and adapters, prompts,
evidence verification, injection guard, and a three-tier benchmark **when a model endpoint exists**.
Not approved and not started: Hybrid routing/fusion (Phase 6), observability export (Phase 7), service
surface, MCP, RAG, agents, UI. The locked test split is never loaded.

## Blocker stated in advance (open decision 4)

This environment has no Azure AI Foundry project, credentials, or SDK, and no model names have been
approved (approved decision: do not assume model names). Therefore:

* Everything up to the network call is built and tested against **mock** and **replay** adapters.
* The **Foundry adapter is written from general knowledge of chat-completion HTTP APIs and is
  UNVERIFIED against a real endpoint.** Endpoint, API version, auth and deployment names are
  configuration/environment, never code. It is exercised only against a local fake HTTP server.
* **No LLM accuracy, calibration, latency or cost number will be reported until a real run has been
  executed and recorded.** The three-tier benchmark is BLOCKED until the user supplies access and
  deployment names. Reports state "NOT RUN" rather than showing placeholders.
* Measurable without a model, and reported as such: the local injection scan (detection on T5, false
  positives on T1-T4), prompt sizes and truncation rate, few-shot leakage checks, schema and
  evidence-verification correctness on adversarial fixtures.

## Design

* **Provider interface** `LLMClient.complete_structured(request) -> LLMResponse`; adapters: `mock`
  (tests only), `replay` (cache keyed by `(prompt_version, model_id, input_hash)`; a miss is an error,
  never a silent fallback), `foundry` (HTTP, stdlib only, optional token provider). Replay can record
  from an inner client so a real run becomes a committed, CI-replayable cache.
* **Tiers** `small|mid|large` are config aliases; each names an environment variable that holds the
  deployment name. Model names appear nowhere in the repository. Temperature 0, bounded output tokens,
  timeout and bounded retries with backoff+jitter (transport errors only) are configuration.
* **Prompt** is assembled from `prompts/uc4/classifier.v1.md`; the taxonomy section (levels, floors,
  definitions, positive/counter examples) is **generated from `config/taxonomy/taxonomy.v1.yaml`**, so
  there is one source of truth. Prompts are versioned files; the prompt version and hash are recorded.
* **Few-shot examples come only from `train`**, are a fixed versioned list of document ids
  (`prompts/uc4/fewshot.v1.json`), chosen by a fixed rule written here before selection:
  from `train`, for each of the 8 categories the alphabetically-first family whose T1 documents are
  single-category and <= 600 characters (first `doc_id` in that family); plus the alphabetically-first
  PUBLIC and INTERNAL T1 families; plus two T4 hard negatives from different families with
  different `decoy_for`; plus one multi-category T1 document. The loader re-checks that every example is
  a `train` document with the recorded hash, and that no example family appears in the evaluated
  documents. Train documents that are few-shot examples are excluded from any in-sample train report.
* **Untrusted-data delimiting.** The document is placed between boundary markers containing a token
  derived from `sha256(seed || content_hash)`, so the token is deterministic (replayable) yet cannot be
  known to the document's author; generation re-derives the token if the content contains it. The
  prompt states that the block is data, not instructions.
* **Structured output** (strict JSON schema, validated): `level`, `categories[]`,
  `evidence[{quote, supports_axis, supports_value}]`, `rationale`, `level_confidence` and
  `category_confidence` in {low, medium, high}, `insufficient_information`. Unknown fields, labels
  outside the taxonomy, or bad types make the output invalid.
* **Confidence** is `verbalized_bucket` only and is never marked calibrated (DEC-10). Any statement
  about how reliable a bucket is comes from a measured reliability table, not from the model.
* **Evidence verification (code-based):** a quote is *verified* if it is a substring of the text
  actually sent (exact, or equal after collapsing whitespace runs). Verified quotes become
  `llm_excerpt` evidence with provenance `observed` and a locator; unverified quotes are kept with
  `verified=false` / provenance `inferred`. The rationale is `llm_rationale` (inferred). Excerpts are
  masked (digit runs, emails and long tokens) and truncated before storage so the result never carries
  raw sensitive values. Confidence is capped at `medium` when any quote is unverified and at `low` when
  none of several quotes is verified; an unverified high-risk call requests review (`EVIDENCE_UNVERIFIED`).
* **Injection guard (local):** a configurable lexicon of instruction-override patterns over the input,
  developed on `train` only, evaluated on dev. A hit adds a `GuardrailEvent`, never blocks, and never
  lowers anything. The "LLM may raise but not lower relative to Rules/ML" rule needs those outputs and
  is therefore a Phase 6 (Hybrid) responsibility; Phase 5 records the flag and the events.
* **Failure semantics (architecture section 19):** the classification path never raises. Transport
  failure after bounded retries, or malformed output after ONE repair retry, returns
  `review_required` with `LLM_UNAVAILABLE` and **no level** (never a silent low default). Oversize
  input is truncated with a `truncated` warning; empty input is `rejected`.
* **Cost:** tokens come from the provider response. Price is configuration and defaults to `null`;
  with no price configured, cost is reported as not estimated. No price is ever hard-coded or invented.
* **Abstention:** `insufficient_information=true` sets `routing.abstained` and keeps the model's
  level as provisional; the benchmark reports the abstention rate alongside coverage.
* **No pre-LLM redaction option** in this phase: its effect can only be measured with a real model,
  and the plan lists it as an optional recommendation. Deferred, not dropped.

## Evaluation protocol (when a model endpoint exists)

* Same harness, headline (T1-T4), family-level intervals, and slices as Rules/ML; T5 as a slice.
* Evaluate on `dev` only; `calibration` and `train` (minus few-shot ids) only as flagged secondary
  numbers. The locked test split is not touched. Prompt or config changes after seeing dev are logged.
* Report per tier: schema-valid rate, evidence-verification rate, evidence coverage of predicted
  categories, level and category F1, high-risk P/R/F1/FPR/review rate, abstention, a **reliability
  table per verbalized bucket**, latency P50/P95, tokens and cost (only if priced), and comparison with
  frozen Rules 1.0.3 and ML on the same documents.
* Three configurations must be benchmarked (PRD 6.3); a tier with no cache/run is reported "NOT RUN".

## Expectations stated in advance

* An LLM should do better than Rules and ML on the semantic categories (Trade Secret, IP, M&A,
  narrative PII/PHI) and on hard negatives; it may over-elevate (under-classification less likely than
  over-classification) and its verbalized confidence is expected to be poorly calibrated.
* Dataset documents are short (median ~330 characters), so truncation should be rare and cost small.
* Injection documents (T5) are a genuine risk; the lexicon guard will miss paraphrased attacks.
* These are hypotheses, not results; they may be wrong.

## Limits stated in advance

Synthetic, AI-authored, template-generated, unreviewed labels; 18 dev families; models may have seen
similar text; the Foundry adapter is unverified; nothing transfers to real data without validation.
