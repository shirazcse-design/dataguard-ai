"use strict";
// Data Security Policy Copilot (UC6) page, plus UC6 panels appended to the shared Evaluations,
// Responsible AI / Guardrails and Observability pages. Loaded after app.js and reuses its helpers
// (h, card, dataClass, source, statusBadge, getJSON, applyMode, fmt, pct, PAGES). Like app.js, DOM is
// built with textContent only, never innerHTML from data. Every value comes from the demo server:
// the REAL pipeline (REPLAY by default) or the committed docs/uc6/results files.

const pstate = { info: null, question: "", level: "advanced", backend: "chat-completions",
  last: null, busy: false, error: null, review: null, activeKey: null };

const STATUS_TEXT = {
  ANSWERED: "Every claim shown is backed by a quote that was verified against retrieved policy text.",
  INSUFFICIENT_EVIDENCE: "The retrieved policies do not support an answer. This is a valid outcome, not a failure: the Copilot does not guess.",
  CONFLICT_REVIEW: "Current policies disagree for this situation and no metadata says which one applies, so a person must decide.",
  BLOCKED: "The input guard refused the question before retrieval or any model call.",
  UNAVAILABLE: "No verified answer could be produced. An unverified answer is never shown instead.",
};
const STATUS_KIND = { ANSWERED: "good", INSUFFICIENT_EVIDENCE: "warn", CONFLICT_REVIEW: "warn", BLOCKED: "bad", UNAVAILABLE: "bad" };
const REVIEW_TEXT = {
  insufficient_evidence: "No policy evidence answers this. A policy owner may need to cover it.",
  no_verified_claims: "The model answered, but none of its claims survived citation verification.",
  policy_conflict: "Two current policies disagree for this situation.",
  conflict_unverified: "The model reported a conflict the evidence does not support.",
  unverified_claims_removed: "Some claims were removed because their citation or quote could not be verified.",
  guardrail_injection: "The question contains instruction-override text (prompt injection).",
  input_rejected: "The question was empty or too long.",
  evidence_injection: "A retrieved policy passage contained instruction-like text and was withheld from the model.",
  generation_unavailable: "The model was unavailable, or (in REPLAY) this question was never recorded.",
  agent_requested: "The agent itself asked for a human review.",
  step_budget_exceeded: "The agent reached its tool-call budget and stopped safely.",
  tool_failure: "The agent's tools failed repeatedly; it stopped safely.",
};
const DROP_TEXT = {
  fabricated_evidence_id: "cited evidence that was never retrieved (fabricated citation)",
  quote_not_in_evidence: "its quote is not in the cited section",
  number_not_in_evidence: "it states a number that is not in the cited section",
  superseded_version: "it cites a superseded policy version",
  over_claim_limit: "over the claim limit",
};
const STAGE_TEXT = {
  input_guard: "Input guard", retrieval: "Retrieval", evidence_scan: "Evidence injection scan",
  evidence_gate: "Evidence gate", generation: "LLM generation", citation_verification: "Citation verification",
  conflict_check: "Conflict check", result: "Result", agent: "Agent loop",
};
const LEVEL_TEXT = {
  naive: "Naive RAG: embedding search, top-5, generate. No query processing, no filter, no rerank; citations measured, not enforced.",
  advanced: "Advanced RAG: query expansion, hybrid keyword + embedding search, draft filter, rerank, evidence gate, then citation verification.",
  agentic: "Agentic RAG: a bounded agent (4 tools, at most 6 calls) decides what to search, then the same citation verification and conflict checks.",
};

async function postPolicy(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const out = await res.json();
  if (!res.ok) throw new Error(out.error || `HTTP ${res.status}`);
  return out;
}

// ---- the page --------------------------------------------------------------------------------
async function renderPolicy(page) {
  if (!pstate.info) {
    const env = await getJSON("/api/policy");
    applyMode(env);
    pstate.info = env.data;
    pstate.mode = env.mode_label;
  }
  const info = pstate.info;
  page.append(
    h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, "Data Security Policy Copilot"),
        h("p", { class: "lead" },
          "Grounded answers about Harbourline's (synthetic) data-security policies. Every claim cites retrieved policy text, "
          + "every citation is verified by the harness, and \"insufficient evidence\" is a valid answer. "
          + "Shared Policy Intelligence for the DLP, Insider Risk, Access Governance and Incident agents.")),
      dataClass(pstate.mode === "LIVE" ? "Live pipeline" : "Replay: recorded pipeline")),
  );
  const ask = h("div", { class: "grid cols-2" }, askCard(info), pipelineCard());
  const result = h("div", { id: "pc-result" });
  page.append(ask, result, reviewQueueCard());
  drawResult();
  loadReviewQueue();
}

function askCard(info) {
  const list = h("div", { class: "examples" }, info.examples.map((e) => {
    const b = h("button", { class: `example${pstate.activeKey === e.key ? " active" : ""}`, type: "button" },
      h("span", { class: "t" }, h("span", {}, e.title), h("span", { class: "chip" }, e.id)),
      h("span", { class: "d" }, e.what + (e.foundry ? " · also recorded via the Foundry agent" : "")));
    b.addEventListener("click", () => {
      pstate.question = e.question; pstate.activeKey = e.key;
      if (e.key === "injection") pstate.level = pstate.level === "naive" ? "advanced" : pstate.level;
      rerender();
    });
    return b;
  }));
  const ta = h("textarea", { rows: "3", placeholder: "Ask a policy question…", "aria-label": "Question" });
  ta.style.minHeight = "84px";
  ta.value = pstate.question;
  ta.addEventListener("input", () => { pstate.question = ta.value; pstate.activeKey = null; });
  const level = h("select", { "aria-label": "RAG level" },
    ["naive", "advanced", "agentic"].map((lv) => { const o = h("option", { value: lv }, lv[0].toUpperCase() + lv.slice(1) + " RAG"); if (lv === pstate.level) o.selected = true; return o; }));
  level.addEventListener("change", () => { pstate.level = level.value; rerender(); });
  const backend = h("select", { "aria-label": "Agent backend" },
    h("option", { value: "chat-completions" }, "App agent (chat-completions planner)"),
    h("option", { value: "foundry-service" }, "Foundry agent: dataguard-policy-copilot v5"));
  backend.value = pstate.backend;
  backend.addEventListener("change", () => { pstate.backend = backend.value; });
  const go = h("button", { class: "btn", type: "button" }, pstate.busy ? "Answering…" : "Ask");
  go.disabled = pstate.busy;
  go.addEventListener("click", runAsk);
  return card("Ask", dataClass("Synthetic corpus"),
    list,
    h("label", { class: "field" }, "Question", ta),
    h("div", { class: "btn-row" }, level, pstate.level === "agentic" ? backend : null, go),
    h("p", { class: "note" }, LEVEL_TEXT[pstate.level]),
    pstate.mode !== "LIVE"
      ? h("p", { class: "note" }, "REPLAY answers the curated and golden questions from recordings. A new question is still run through the real pipeline, "
        + "but with no recording the model step reports UNAVAILABLE rather than inventing an answer. The Foundry agent is recorded for S01, M01, I02, C02 and X01.")
      : null);
}

function pipelineCard() {
  const steps = pstate.level === "agentic"
    ? ["Input guard", "Agent: search_policy / get_policy_section / lookup_policy_metadata / request_human_review", "Citation verification", "Conflict check", "Answer / Review"]
    : pstate.level === "naive"
      ? ["Embedding search (top-5)", "LLM generation", "Citations measured", "Answer"]
      : ["Input guard", "Query expansion", "Hybrid BM25 + embeddings", "Draft filter + rerank", "Evidence gate", "LLM generation", "Citation verification", "Conflict check", "Answer / Review"];
  return card("How this level works", dataClass("Architecture"),
    h("div", { class: "loop" }, steps.flatMap((s, i) => [i ? h("span", { class: "arrow" }, "→") : null, h("span", { class: `node${i === steps.length - 1 ? " hot" : ""}` }, s)])),
    h("div", { class: "callout" },
      h("b", {}, "Structural controls, not prompt requests: "),
      "the model cites evidence by label (E1…En) and never types a section number; the harness checks every label, quote and number; "
      + "superseded versions lose to current ones by metadata; conflicts between current policies go to a person; retrieved text is data, never instructions."));
}

function rerender() { return location.hash === "#policy" ? route() : Promise.resolve(); }

async function runAsk() {
  if (!pstate.question.trim() || pstate.busy) return;
  pstate.busy = true; pstate.error = null; rerender();
  try {
    const env = await postPolicy("/api/policy/ask", { question: pstate.question, level: pstate.level, backend: pstate.backend });
    applyMode(env);
    pstate.last = { ...env.data, data_class: env.data_class };
  } catch (err) {
    pstate.error = err.message;
  } finally {
    pstate.busy = false;
    await rerender();
    const el = document.getElementById("pc-result");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

// ---- result ----------------------------------------------------------------------------------
function drawResult() {
  const el = document.getElementById("pc-result");
  if (!el) return;
  el.replaceChildren();
  if (pstate.error) { el.append(h("div", { class: "card error" }, `Could not answer: ${pstate.error}`)); return; }
  const a = pstate.last;
  if (!a) {
    el.append(h("div", { class: "card empty-state" }, "Pick a curated question (start with the primary demo) or type your own, choose a RAG level, and press Ask."));
    return;
  }
  el.append(verdictBanner(a));
  el.append(h("div", { class: "grid cols-2" }, answerCard(a), reviewCard(a)));
  if (a.conflicts && a.conflicts.length) el.append(conflictCard(a));
  el.append(evidenceCard(a));
  el.append(h("div", { class: "grid cols-2" }, traceCard(a), a.agent ? agentCard(a) : retrievalCard(a)));
}

function verdictBanner(a) {
  const kind = STATUS_KIND[a.status] || "bad";
  const modeLabel = a.mode === "live" ? "LIVE" : a.mode === "offline" ? "OFFLINE" : "REPLAY";
  const hint = a.status === "UNAVAILABLE" && modeLabel === "REPLAY" && !a.recorded_question
    ? h("p", { class: "note" }, "This question is not in the recorded set, so REPLAY has no model response for it. Pick a curated question, or run the server in LIVE mode.")
    : null;
  return h("div", { class: `verdict ${kind === "good" ? "l-PUBLIC" : kind === "warn" ? "l-none" : "failure"}` },
    h("div", { class: "lvl-big" }, a.status.replace(/_/g, " ")),
    h("p", { class: "lead" }, STATUS_TEXT[a.status] || ""),
    h("div", { class: "badges" },
      statusBadge(modeLabel, modeLabel === "LIVE" ? "bad" : "muted"),
      statusBadge(`${a.level} RAG`, "muted"),
      a.backend ? statusBadge(a.backend === "foundry-service" ? "Foundry agent" : "App agent", "muted") : null,
      statusBadge(`evidence: ${a.evidence_status}`, a.evidence_status === "grounded" ? "good" : a.evidence_status === "none" ? "muted" : "warn"),
      a.top_evidence_score !== null && a.top_evidence_score !== undefined ? statusBadge(`top evidence ${fmt(a.top_evidence_score, 2)}`, "muted") : null,
      statusBadge(`${a.citations.length} verified citation${a.citations.length === 1 ? "" : "s"}`, a.citations.length ? "good" : "muted"),
      a.review.required ? statusBadge("human review", "warn") : statusBadge("no review needed", "good"),
      statusBadge(`${Math.round(a.wall_ms)} ms`, "muted")),
    hint);
}

function answerCard(a) {
  const claims = a.claims.length
    ? h("ul", { class: "items" }, a.claims.map((c) => h("li", {},
        h("span", {}, c.verified ? "✓" : "⚠"),
        h("span", {}, c.text, " ", h("span", { class: "chip" }, c.citation || c.evidence_id)))))
    : h("p", { class: "placeholder" }, a.status === "BLOCKED" ? "No answer: the question was blocked." : "No policy claim is shown.");
  const dropped = a.dropped_claims && a.dropped_claims.length
    ? h("details", {}, h("summary", {}, `${a.dropped_claims.length} claim(s) removed by the harness`),
        h("ul", { class: "items" }, a.dropped_claims.map((c) => h("li", {}, h("span", {}, "✕"),
          h("span", {}, `"${c.text}" `, h("b", {}, "removed: "), DROP_TEXT[c.drop_reason] || c.drop_reason)))))
    : null;
  const note = a.level === "naive" ? h("p", { class: "note" }, "Naive RAG shows the model's claims even if unverified (marked ⚠): the verification is measured, not enforced, to show what the harness adds.") : null;
  return card("Answer", dataClass(a.data_class || "Pipeline output"),
    claims, dropped, note,
    a.citations.length ? h("div", {}, h("h2", { class: "sub" }, "Sources (verified)"), h("div", { class: "chips" }, a.citations.map((c) => h("span", { class: "chip" }, c)))) : null);
}

function reviewCard(a) {
  if (!a.review.required) {
    return card("Human review", statusBadge("Not required", "good"),
      h("p", { class: "lead" }, "Evidence is verified and no conflict or guardrail event needs a person."));
  }
  const note = h("input", { type: "text", maxlength: "500", placeholder: "Optional note for the policy owner", "aria-label": "Review note" });
  const out = h("p", { class: "note" });
  const act = (action, label, cls) => {
    const b = h("button", { class: `btn ${cls}`, type: "button" }, label);
    b.addEventListener("click", async () => {
      try {
        const env = await postPolicy("/api/policy/review/decide", { request_id: a.request_id, action, note: note.value });
        out.textContent = `Recorded "${action}" in ${env.data.stored_at} (demo-only; never written back to the golden set).`;
        loadReviewQueue();
      } catch (err) { out.textContent = `Not recorded: ${err.message}`; }
    });
    return b;
  };
  return card("Human review", statusBadge("Required", "warn"),
    h("ul", { class: "why-list" }, a.review.reasons.map((r) => h("li", {}, h("b", {}, r.replace(/_/g, " ")), ": ", REVIEW_TEXT[r] || ""))),
    h("label", { class: "field" }, "Reviewer note", note),
    h("div", { class: "btn-row" },
      act("confirm", "Confirm outcome", "btn-good"),
      act("escalate_to_policy_owner", "Escalate to policy owner", "btn-warn"),
      act("record_policy_gap", "Record policy gap", "btn-quiet")),
    out);
}

function conflictCard(a) {
  return card("Policy conflicts", dataClass("Deterministic check"),
    h("div", { class: "ev-list" }, a.conflicts.map((c) => h("div", { class: "ev-item" },
      h("div", { class: "top" },
        statusBadge(c.kind === "version" ? "version conflict" : "cross-policy conflict", "warn"),
        statusBadge(c.resolution === "resolved_by_metadata" ? "resolved by metadata" : "human review", c.resolution === "resolved_by_metadata" ? "good" : "warn"),
        c.citations.map((x) => h("span", { class: "chip" }, x))),
      h("div", {}, c.note),
      c.authoritative ? h("div", { class: "note" }, "Authoritative source: ", h("b", {}, c.authoritative)) : null))),
    a.conflict_note ? h("div", { class: "callout" }, h("b", {}, "Model's description (shown because the conflict was verified): "), a.conflict_note) : null);
}

function highlight(body, quotes) {
  // Mark the verified quotes inside the section text (text nodes only).
  const spans = [];
  for (const q of quotes) {
    const at = body.indexOf(q);
    if (q && at >= 0) spans.push([at, at + q.length]);
  }
  spans.sort((x, y) => x[0] - y[0]);
  const out = [];
  let pos = 0;
  for (const [s0, e0] of spans) {
    if (s0 < pos) continue;
    out.push(body.slice(pos, s0), h("mark", { class: "ev src-llm" }, body.slice(s0, e0)));
    pos = e0;
  }
  out.push(body.slice(pos));
  return out;
}

function evidenceCard(a) {
  if (!a.evidence.length) return card("Policy evidence", dataClass("Retrieved"), h("p", { class: "placeholder" }, "No evidence was retrieved for this run."));
  const quotesFor = (id) => a.claims.filter((c) => c.chunk_id === id && c.verified).map((c) => c.quote);
  return card("Policy evidence", dataClass("Retrieved policy text"),
    h("div", { class: "ev-list" }, a.evidence.map((e) => h("div", { class: "ev-item" },
      h("div", { class: "top" },
        h("span", { class: "chip" }, e.evidence_id),
        h("b", {}, e.citation), h("span", {}, `${e.title} · ${e.section} ${e.heading}`),
        statusBadge(e.status, e.status === "current" ? "good" : e.status === "superseded" ? "warn" : "bad"),
        e.cited ? statusBadge("cited", "good") : null,
        e.flagged_injection ? statusBadge("withheld: injected instructions", "bad") : null),
      h("div", { class: "note" }, `v${e.version} · effective ${e.effective_date} · owner ${e.policy_owner} · score ${fmt(e.score, 3)}`),
      e.flagged_injection
        ? h("div", { class: "note" }, "This passage contains instruction-override text. It was not shown to the model.")
        : h("div", { class: "doc" }, highlight(e.body, quotesFor(e.chunk_id)))))),
    h("p", { class: "note" }, "Highlighted text is the exact quote each verified claim was checked against."));
}

function traceCard(a) {
  const cls = { ok: "st-executed", short_circuit: "st-failed", blocked: "st-failed", failed: "st-failed", skipped: "st-skipped" };
  const detail = (d) => Object.entries(d || {}).filter(([, v]) => v !== null && v !== undefined && !(Array.isArray(v) && !v.length))
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : typeof v === "number" ? Math.round(v * 1000) / 1000 : v}`).join(" · ");
  return card("Decision trace", dataClass("Stages that ran"),
    h("ol", { class: "timeline" }, a.stages.map((s, i) => h("li", { class: cls[s.status] || "st-executed" },
      h("span", { class: "dot" }, String(i + 1)),
      h("div", { class: "body" },
        h("div", { class: "head" }, h("b", {}, STAGE_TEXT[s.name] || s.name), statusBadge(s.status.replace(/_/g, " "), s.status === "ok" ? "good" : "warn")),
        h("div", { class: "meta" }, detail(s.detail), s.ms !== null && s.ms !== undefined ? ` · ${fmt(s.ms, 1)} ms` : ""))))),
    h("p", { class: "note" }, "Only stages that actually executed are listed; a short-circuit ends the list."));
}

function retrievalCard(a) {
  const t = a.retrieval;
  if (!t) return card("Retrieval trace", dataClass("Retrieval"), h("p", { class: "placeholder" }, "Retrieval did not run."));
  const rows = a.evidence.map((e) => h("tr", {},
    h("td", { class: "n" }, e.rank), h("td", {}, e.citation), h("td", { class: "n" }, fmt(e.score, 3)), h("td", {}, e.cited ? "cited" : "")));
  return card("Retrieval trace", dataClass("Retrieval"),
    h("dl", { class: "kv" },
      h("dt", {}, "Stages"), h("dd", {}, t.stages.join(" → ") || "-"),
      h("dt", {}, "Query expansion"), h("dd", {}, t.added_terms.length ? t.added_terms.join(", ") : "none"),
      h("dt", {}, "Draft filter"), h("dd", {}, t.level.metadata_filter ? `${t.excluded_by_filter} unapproved chunks excluded` : "off (naive)"),
      h("dt", {}, "Dense (embeddings)"), h("dd", {}, t.dense_status === "ok" ? `ok · ${t.dense_candidates.length} candidates` : t.dense_status),
      h("dt", {}, "Sparse (BM25)"), h("dd", {}, t.sparse_candidates.length ? `${t.sparse_candidates.length} candidates` : "not used"),
      h("dt", {}, "Fused / reranked"), h("dd", {}, `${t.fused_candidates} candidates → top ${a.evidence.length}${t.level.rerank ? " after rerank" : ""}`)),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", { class: "n" }, "Rank"), h("th", {}, "Section"), h("th", { class: "n" }, t.level.rerank ? "Rerank score" : "Cosine"), h("th", {}, ""))),
      h("tbody", {}, rows))));
}

function agentCard(a) {
  const g = a.agent;
  return card("Agent trace", dataClass(g.backend === "foundry-service" ? "Foundry agent" : "App agent"),
    h("dl", { class: "kv" },
      h("dt", {}, "Agent"), h("dd", {}, g.name),
      h("dt", {}, "Planner"), h("dd", {}, `${g.planner} (${g.backend})`),
      h("dt", {}, "Tool calls"), h("dd", {}, `${g.tool_calls} of ${g.max_tool_calls} allowed · ${g.turns} planner turns`),
      h("dt", {}, "Stopped"), h("dd", {}, g.stopped_reason),
      g.tokens_in ? h("dt", {}, "Tokens") : null, g.tokens_in ? h("dd", {}, `${g.tokens_in} in / ${g.tokens_out} out`) : null),
    h("ol", { class: "timeline" }, g.steps.map((st) => h("li", { class: st.ok ? "st-executed" : "st-failed" },
      h("span", { class: "dot" }, String(st.step)),
      h("div", { class: "body" },
        h("div", { class: "head" }, h("b", {}, st.tool), statusBadge(st.ok ? "ok" : st.error, st.ok ? "good" : "bad")),
        Object.keys(st.arguments || {}).length ? h("div", { class: "meta" }, Object.entries(st.arguments).map(([k, v]) => `${k}: "${v}"`).join(" · ")) : null,
        st.evidence_ids.length ? h("div", { class: "meta" }, `returned ${st.evidence_ids.join(", ")}${st.withheld ? ` (${st.withheld} withheld)` : ""}`) : null)))),
    h("p", { class: "note" }, "Tools run in the application, inside the allow-list, argument validation and step budget. The agent can only cite what its tools returned."));
}

// ---- review queue (this session) -------------------------------------------------------------
function reviewQueueCard() {
  return h("div", { id: "pc-queue" });
}
async function loadReviewQueue() {
  const el = document.getElementById("pc-queue");
  if (!el) return;
  try {
    const env = await getJSON("/api/policy/review");
    const q = env.data;
    el.replaceChildren(card("Policy review queue (this session)", dataClass("Demo-only state"),
      q.pending.length
        ? h("div", { class: "table-wrap" }, h("table", {},
            h("thead", {}, h("tr", {}, h("th", {}, "Time"), h("th", {}, "Level"), h("th", {}, "Status"), h("th", {}, "Why"))),
            h("tbody", {}, q.pending.map((p) => h("tr", {}, h("td", {}, p.at), h("td", {}, p.level), h("td", {}, p.status), h("td", {}, p.reasons.join(", ")))))))
        : h("p", { class: "placeholder" }, "No answers are waiting for review in this session."),
      q.decisions.length ? h("p", { class: "note" }, `${q.decisions.length} decision(s) recorded in ${q.stored_at} (never written back to evaluation gold data).`) : null));
  } catch (err) {
    el.replaceChildren();
  }
}

// ---- UC6 panels on the shared pages ------------------------------------------------------------
async function policySummary() {
  if (!pstate.info) {
    const env = await getJSON("/api/policy");
    pstate.info = env.data;
    pstate.mode = env.mode_label;
  }
  return pstate.info.summary;
}

// Result files are written with sorted keys; show levels and the ablation ladder in their own order.
const LEVEL_ORDER = ["naive", "advanced", "agentic"];
const VARIANT_ORDER = ["naive (dense)", "sparse (BM25)", "hybrid (RRF)", "hybrid + query processing", "advanced"];
function ordered(keys, order) {
  return [...order.filter((k) => keys.includes(k)), ...keys.filter((k) => !order.includes(k))];
}

function uc6Header(title, text) {
  return h("div", { class: "page-head uc6-head" },
    h("div", {}, h("h1", {}, title), h("p", { class: "lead" }, text)), dataClass("Recorded UC6 results"));
}

function metricTable(rows, cols, get) {
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Metric"), cols.map((c) => h("th", { class: "n" }, c)))),
    h("tbody", {}, rows.map(([label, key]) => h("tr", {}, h("td", {}, label), cols.map((c) => h("td", { class: "n" }, get(c, key))))))));
}

async function uc6EvaluationPanels(page) {
  const s = await policySummary();
  const lv = s.answers.levels;
  const levels = ordered(Object.keys(lv), LEVEL_ORDER);
  const v = s.retrieval.variants;
  const variants = ordered(Object.keys(v), VARIANT_ORDER);
  const fe = s.foundry_evals;
  page.append(
    uc6Header("Data Security Policy Copilot (UC6)", "Retrieval and answers are evaluated separately, on a 36-question golden set, replayed from recorded runs. Deterministic unless marked."),
    h("div", { class: "grid cols-2" },
      card("Retrieval ablation (deterministic)", dataClass("Recorded"),
        metricTable([["Recall@5", "recall@5"], ["MRR", "mrr"], ["Hit@5", "hit@5"], ["Draft in top-5", "draft_in_top_k_rate"]], variants, (c, k) => fmt(v[c][k], 3)),
        h("p", { class: "note" }, "Plain embedding search had the best recall; Advanced adds safety (no unapproved draft in the top 5) and query expansion. Reported as measured, no tuning on the test set."),
        source(s.sources.retrieval)),
      card("Answers: Naive vs Advanced vs Agentic", dataClass("Recorded"),
        metricTable([["Status accuracy", "status_accuracy"], ["Insufficient-evidence accuracy", "insufficient_evidence_accuracy"],
          ["Injection resistance", "injection_resistance"], ["Fabricated-citation rate", "fabricated_citation_rate"],
          ["Unsupported-answer rate", "unsupported_answer_rate"], ["Citation recall", "citation_recall"],
          ["Answer points (heuristic)", "answer_point_coverage"]], levels, (c, k) => fmt(lv[c].metrics[k], 3)),
        source(s.sources.answers))),
    h("div", { class: "grid cols-2" },
      card("Agent behaviour (Agentic RAG)", dataClass("Recorded"),
        lv.agentic && lv.agentic.agent
          ? h("dl", { class: "kv" }, Object.entries(lv.agentic.agent).filter(([k]) => typeof lv.agentic.agent[k] !== "object")
              .flatMap(([k, val]) => [h("dt", {}, k.replace(/_/g, " ")), h("dd", {}, typeof val === "number" ? fmt(val, 3) : String(val))]))
          : h("p", { class: "placeholder" }, "not computed")),
      card("Foundry Evaluations", dataClass(fe ? "Foundry run" : "Not run"),
        fe && fe.outcomes
          ? h("div", {},
              h("p", { class: "lead" }, "Deterministic string checks in Foundry vs the local harness:"),
              metricTable(Object.keys(fe.outcomes.per_criterion).map((k) => [k.replace(/_/g, " "), k]), ["Foundry", "Local"],
                (c, k) => c === "Foundry" ? `${fe.outcomes.per_criterion[k].passed}` : `${fe.outcomes.local[k].passed}`),
              fe.quality && fe.quality.summary && Object.keys(fe.quality.summary.by_level || {}).length
                ? metricTable([["Groundedness (1-5)", "groundedness"], ["Relevance (1-5)", "relevance"], ["Retrieval (1-5)", "retrieval"]],
                    ordered(Object.keys(fe.quality.summary.by_level), LEVEL_ORDER), (c, k) => fmt(fe.quality.summary.by_level[c][k].mean, 2))
                : h("p", { class: "note" }, "AI-assisted quality evaluation: not available in this results file."),
              source(s.sources.foundry_evals))
          : h("p", { class: "placeholder" }, "No Foundry evaluation results recorded yet."))),
  );
}

async function uc6GuardrailPanels(page) {
  const s = await policySummary();
  page.append(
    uc6Header("Policy Copilot guardrails (UC6)", "Application guardrails and Foundry guardrails were tested live with the same attacks on four paths (G1-G8)."),
    card(`Live verification (${s.guardrails_date || "not run"})`, dataClass("Live run, recorded"),
      s.guardrails.length
        ? h("div", { class: "table-wrap" }, h("table", {},
            h("thead", {}, h("tr", {}, h("th", {}, "Case"), h("th", {}, "Who stopped it"))),
            h("tbody", {}, s.guardrails.map((g) => h("tr", {}, h("td", {}, h("b", {}, g.id), ` ${g.name}`), h("td", {}, g.verdict || "-"))))))
        : h("p", { class: "placeholder" }, "No verification run recorded."),
      h("div", { class: "callout" }, h("b", {}, "Takeaway: "),
        "the app's lexicon caught phrasings Foundry ignored (G1, G7); Foundry's Prompt Shields caught paraphrased and role-play jailbreaks the lexicon missed (G2, G3). "
        + "G5 (a violent request) was stopped by no layer at Medium and was accepted for the synthetic demo by the product owner."),
      source(s.sources.guardrails, "docs/uc6/foundry-guardrails-setup.md")));
}

async function uc6ObservabilityPanels(page) {
  const s = await policySummary();
  const o = s.observability;
  const levels = ordered(Object.keys(o.by_level || {}), LEVEL_ORDER);
  page.append(
    uc6Header("Policy Copilot telemetry (UC6)", "uc6.* spans carry ids, counts, statuses and POL-XXX:4.2 section ids. Never the question, search queries or policy text."),
    h("div", { class: "grid cols-2" },
      card("Privacy audit", statusBadge(o.privacy_audit && o.privacy_audit.clean ? "Clean" : "Check", o.privacy_audit && o.privacy_audit.clean ? "good" : "bad"),
        o.privacy_audit ? h("p", { class: "lead" }, `${o.privacy_audit.spans_audited} spans audited against ${o.privacy_audit.sources_checked} sources (every golden question and policy section): ${o.privacy_audit.leaks.length} leaks.`) : null,
        h("div", { class: "callout" }, h("b", {}, "Foundry finding: "),
          "a live canary proved DataGuard's own spans carry no question text, but Foundry Agent Service's content recording stores agent conversations (genAIContent). Kept on for the synthetic demo; off in production."),
        source(s.sources.observability, "docs/uc6/results/observability-foundry-verification.md")),
      card("Telemetry by level (replayed run)", dataClass("Recorded"),
        metricTable([["Requests", "requests"], ["Insufficient-evidence rate", "insufficient_evidence_rate"], ["Review-required rate", "review_required_rate"],
          ["Model calls", "model_calls"], ["Tokens in", "tokens_in"], ["Fabricated citations", "fabricated_citations"]], levels,
          (c, k) => { const val = o.by_level[c][k]; return typeof val === "number" && !Number.isInteger(val) ? fmt(val, 3) : String(val); }))));
}

// A "Jump to" bar at the top of the shared pages, one button per use case on the page. Buttons,
// not "#" links: the dashboard routes pages by the URL hash, so an anchor link would switch pages
// instead of scrolling. Each use case's panels start with a header carrying `data-jump`.
const JUMP_TARGETS = [
  ["Sensitive Data Discovery & Classification", "[data-jump-top]"],
  ["Data Security Policy Copilot", ".uc6-head"],
];
function jumpBar(page) {
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const items = JUMP_TARGETS.flatMap(([label, sel], i) => {
    const target = page.querySelector(sel);
    const b = h("button", { class: "jump-link", type: "button" }, label);
    if (!target) { b.disabled = true; b.title = "This section is not available"; }
    b.addEventListener("click", () => target && target.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" }));
    return [i ? h("span", { class: "jump-sep", "aria-hidden": "true" }, "·") : null, b];
  });
  return h("nav", { class: "jump-bar", "aria-label": "Jump to a use case on this page" },
    h("span", { class: "jump-label" }, "Jump to:"), items);
}

// Appends a use case's panels to a shared page. Safe to stack (UC6 here, UC1 in dlp.js): the first
// wrapper marks the shared page's own first element as the UC4 target, and the bar is rebuilt once
// all panels are on the page.
function extendPage(key, extra, label = "UC6") {
  const def = PAGES[key];
  if (!def || !def.render) return;
  const orig = def.render;
  def.render = async (page) => {
    await orig(page);
    if (!page.querySelector("[data-jump-top]")) {
      const top = [...page.children].find((c) => !c.classList.contains("jump-bar"));
      if (top) top.dataset.jumpTop = "1";
    }
    try { await extra(page); } catch (err) { page.append(h("div", { class: "card error" }, `${label} panel unavailable: ${err.message}`)); }
    page.querySelectorAll(":scope > .jump-bar").forEach((b) => b.remove());
    page.prepend(jumpBar(page));
  };
}

PAGES.policy = { title: "Policy Copilot", render: renderPolicy };
extendPage("evaluations", uc6EvaluationPanels);
extendPage("rai", uc6GuardrailPanels);
extendPage("observability", uc6ObservabilityPanels);
