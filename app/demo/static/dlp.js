"use strict";
// Agentic DLP (UC1) page: the DLP Investigation timeline and decision trace, plus UC1 panels on the
// shared Evaluations, Responsible AI / Guardrails and Observability pages. Loaded after app.js and
// policy.js and reuses their helpers (h, card, dataClass, source, statusBadge, getJSON, applyMode,
// fmt, PAGES, extendPage, JUMP_TARGETS). DOM is built with textContent only, never innerHTML from
// data. Every value comes from the demo server: the REAL UC1 pipeline (REPLAY by default) or the
// committed docs/uc1/results files. Nothing on this page is hard-coded per case.

const dstate = { info: null, caseId: "D11", backend: "chat-completions", last: null, busy: false,
  error: null, mode: null, note: "" };

const OUTCOME_TEXT = {
  ALLOW: "Allowed. No policy or risk signal requires action.",
  WARN: "Allowed with a warning. The user is coached, and the event is logged for the analyst.",
  ESCALATE: "Escalated to an analyst. A blocking action is PROPOSED and is SIMULATED: nothing happens until a person approves it.",
  HUMAN_REVIEW: "Sent to human review. The system could not reach a safe decision on its own (uncertain, conflicting or missing evidence).",
};
const OUTCOME_KIND = { ALLOW: "l-PUBLIC", WARN: "l-none", ESCALATE: "failure", HUMAN_REVIEW: "l-none" };
const DLP_STAGE_TEXT = {
  prechecks: "Deterministic DLP prechecks", classification: "UC4 classification", identity: "Identity context",
  behavior: "Behaviour context", policy: "UC6 policy intelligence", agent: "Investigation agent", decision: "Risk & response harness",
};
const REVIEW_REASON_TEXT = {
  "review:classification_uncertain": "The classifier could not determine sensitivity.",
  "review:policy_conflict": "Current policies disagree for this situation.",
  "review:policy_insufficient_high_impact": "No policy evidence for a high-impact decision.",
  "review:stage_failure": "A required context source failed.",
  "review:agent_disagreement_up": "The agent proposed a stricter outcome than the rubric (the agent can raise, never lower).",
  "review:agent_requested_review": "The agent itself asked for a human analyst.",
  "review:injection_with_sensitive_data": "Instruction-like text was found alongside sensitive data.",
  "review:agent_failure_high_impact": "The agent stopped abnormally on a non-trivial case.",
};
const ACTION_TEXT = {
  approve_simulated_action: "Approve simulated action",
  dismiss_false_positive: "Dismiss (false positive)",
  request_more_information: "Request more information",
};

async function postDlp(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const out = await res.json();
  if (!res.ok) throw new Error(out.error || `HTTP ${res.status}`);
  return out;
}
async function dlpInfo() {
  if (!dstate.info) {
    const env = await getJSON("/api/dlp");
    applyMode(env);
    dstate.info = env.data;
    dstate.mode = env.mode_label;
  }
  return dstate.info;
}
function rerenderDlp() { return location.hash === "#dlp" ? route() : Promise.resolve(); }

// ---- the page --------------------------------------------------------------------------------
async function renderDlp(page) {
  const info = await dlpInfo();
  page.append(
    h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, "DLP Investigation"),
        h("p", { class: "lead" },
          "A DLP event goes through deterministic checks, UC4 classification, identity and behaviour context, and UC6 policy intelligence. "
          + "Then a bounded agent investigates the gaps, and a versioned deterministic rubric decides. The model proposes; the harness enforces. "
          + "Every action is simulated and needs a person's approval.")),
      dataClass(dstate.mode === "LIVE" ? "Live pipeline" : "Replay: recorded pipeline")),
    h("div", { class: "grid cols-2" }, caseCard(info), flowCard()),
    h("div", { id: "dlp-result" }),
    h("div", { id: "dlp-queue" }),
  );
  drawDlpResult();
  loadDlpQueue();
}

function caseCard(info) {
  const list = h("div", { class: "examples" }, info.examples.map((e) => {
    const b = h("button", { class: `example${dstate.caseId === e.id ? " active" : ""}`, type: "button" },
      h("span", { class: "t" }, h("span", {}, e.title), h("span", { class: "chip" }, e.id)),
      h("span", { class: "d" }, e.what + (e.foundry ? " · also recorded via the Foundry agent" : "")));
    b.addEventListener("click", () => { dstate.caseId = e.id; dstate.last = null; dstate.error = null; rerenderDlp(); });
    return b;
  }));
  const sel = info.examples.find((e) => e.id === dstate.caseId);
  const backend = h("select", { "aria-label": "Agent backend" },
    h("option", { value: "chat-completions" }, "App agent (chat-completions planner)"),
    h("option", { value: "foundry-service" }, "Foundry agent: dataguard-dlp-investigator"));
  backend.value = dstate.backend;
  backend.addEventListener("change", () => { dstate.backend = backend.value; });
  const go = h("button", { class: "btn", type: "button" }, dstate.busy ? "Investigating…" : "Investigate");
  go.disabled = dstate.busy;
  go.addEventListener("click", runInvestigate);
  return card("DLP event", dataClass("Synthetic event"),
    list,
    sel ? eventView(sel.event) : null,
    h("div", { class: "btn-row" }, backend, go),
    dstate.mode !== "LIVE"
      ? h("p", { class: "note" }, "REPLAY runs the real pipeline on recorded model responses (no network). "
        + "The Foundry agent is recorded for D01, D07, D11, D21 and D26; for other cases it reports a replay miss and the harness routes the case safely.")
      : null);
}

function eventView(ev) {
  const d = ev.destination;
  return h("dl", { class: "kv" },
    h("dt", {}, "When"), h("dd", {}, ev.timestamp),
    h("dt", {}, "User"), h("dd", {}, ev.user_id),
    h("dt", {}, "Action"), h("dd", {}, `${ev.action} → ${d.host} (${d.account_type} account)`),
    h("dt", {}, "File"), h("dd", {}, ev.document_ref, ev.existing_label ? ` · label: ${ev.existing_label}` : " · no label"),
    ev.user_justification ? h("dt", {}, "Justification") : null,
    ev.user_justification ? h("dd", {}, h("span", { class: "chip" }, "untrusted"), " ", ev.user_justification) : null,
    ev.simulate_fault.length ? h("dt", {}, "Fault") : null,
    ev.simulate_fault.length ? h("dd", {}, ev.simulate_fault.map((f) => statusBadge(`SIMULATED: ${f}`, "warn"))) : null);
}

function flowCard() {
  const steps = ["Prechecks", "UC4 classification", "Identity", "Behaviour", "UC6 policy", "Agent (5 read-only tools)", "Rubric + floors", "Outcome / approval"];
  return card("How a case is decided", dataClass("Architecture"),
    h("div", { class: "loop" }, steps.flatMap((s, i) => [i ? h("span", { class: "arrow" }, "→") : null, h("span", { class: `node${i === steps.length - 1 ? " hot" : ""}` }, s)])),
    h("div", { class: "callout" },
      h("b", {}, "Structural controls, not prompt requests: "),
      "classification, identity and destination are mandatory deterministic stages, not agent choices; the agent never sees document text; "
      + "an exception counts only if the check_dlp_exception tool returned it; the agent can raise a case to human review but never lower the outcome; "
      + "no tool can act, and every proposed action is simulated."));
}

async function runInvestigate() {
  if (dstate.busy) return;
  dstate.busy = true; dstate.error = null; rerenderDlp();
  try {
    const env = await postDlp("/api/dlp/investigate", { case_id: dstate.caseId, backend: dstate.backend });
    applyMode(env);
    dstate.last = { ...env.data, data_class: env.data_class };
  } catch (err) {
    dstate.error = err.message;
  } finally {
    dstate.busy = false;
    await rerenderDlp();
    const el = document.getElementById("dlp-result");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

// ---- result ----------------------------------------------------------------------------------
function drawDlpResult() {
  const el = document.getElementById("dlp-result");
  if (!el) return;
  el.replaceChildren();
  if (dstate.error) { el.append(h("div", { class: "card error" }, `Could not investigate: ${dstate.error}`)); return; }
  const r = dstate.last;
  if (!r) { el.append(h("div", { class: "card empty-state" }, "Pick a case (start with the flagship), choose the agent backend, and press Investigate.")); return; }
  el.append(dlpVerdict(r));
  el.append(h("div", { class: "grid cols-2" }, decisionCard(r), actionCard(r)));
  el.append(timelineCard(r));
  el.append(h("div", { class: "grid cols-2" }, contextCard(r), policyCard(r)));
  el.append(dlpAgentCard(r));
}

function dlpVerdict(r) {
  const d = r.decision;
  const modeLabel = r.mode === "live" ? "LIVE" : r.mode === "offline" ? "OFFLINE" : "REPLAY";
  return h("div", { class: `verdict ${OUTCOME_KIND[d.outcome] || "l-none"}` },
    h("div", { class: "lvl-big" }, d.outcome.replace(/_/g, " ")),
    h("p", { class: "lead" }, OUTCOME_TEXT[d.outcome] || ""),
    h("div", { class: "badges" },
      statusBadge(modeLabel, modeLabel === "LIVE" ? "bad" : "muted"),
      statusBadge(r.backend === "foundry-service" ? "Foundry agent" : "App agent", "muted"),
      statusBadge(`risk ${d.risk_level}`, d.risk_level === "HIGH" ? "bad" : d.risk_level === "MEDIUM" ? "warn" : d.risk_level === "LOW" ? "good" : "muted"),
      statusBadge(`score ${d.score} · band ${d.band}`, "muted"),
      d.agent_proposal ? statusBadge(`agent proposed ${d.agent_proposal}`, "muted") : null,
      d.human_review_required ? statusBadge("analyst approval required", "warn") : statusBadge("no analyst needed", "good"),
      r.simulated_faults.map((f) => statusBadge(`SIMULATED fault: ${f}`, "warn")),
      statusBadge(`rubric ${d.rubric_version} · mapping ${d.mapping_version}`, "muted"),
      statusBadge(`${Math.round(r.wall_ms)} ms`, "muted")),
    !r.recorded_for_backend
      ? h("p", { class: "note" }, "The Foundry agent was not recorded for this case, so REPLAY reports a replay miss for the agent; the harness still decides safely.")
      : null);
}

function decisionCard(r) {
  const d = r.decision;
  const rows = d.contributing_factors.map((f) => h("tr", {},
    h("td", {}, f.factor.replace(/_/g, " ")), h("td", {}, String(f.value)), h("td", { class: "n" }, f.points > 0 ? `+${f.points}` : String(f.points))));
  const review = d.reason_codes.filter((c) => c.startsWith("review:"));
  const other = d.reason_codes.filter((c) => !c.startsWith("review:") && !/:[+-]?\d+$/.test(c));
  return card("Decision trace (deterministic harness)", dataClass("Rubric output"),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Factor"), h("th", {}, "Value"), h("th", { class: "n" }, "Points"))),
      h("tbody", {}, rows, h("tr", {}, h("td", {}, h("b", {}, "Score")), h("td", {}, `band ${d.band}`), h("td", { class: "n" }, h("b", {}, String(d.score))))))),
    other.length ? h("p", { class: "note" }, "Rules applied: ", other.map((c) => h("span", { class: "chip" }, c))) : null,
    review.length
      ? h("ul", { class: "items" }, review.map((c) => h("li", {}, h("span", {}, "⚑"), h("span", {}, REVIEW_REASON_TEXT[c] || c, " ", h("span", { class: "chip" }, c)))))
      : null,
    h("p", { class: "note" }, "Bands: score ≥60 ESCALATE, ≥30 WARN. Floors (e.g. prohibited + Confidential → ESCALATE) and review triggers apply after the score. The agent's proposal can only raise the outcome."));
}

function actionCard(r) {
  const d = r.decision;
  const queued = d.simulated_action || d.human_review_required;
  return card("Proposed response", dataClass("SIMULATED"),
    d.simulated_action
      ? h("div", { class: "callout" }, h("b", {}, "Simulated action: "), d.simulated_action)
      : h("p", { class: "placeholder" }, "No action proposed."),
    queued
      ? h("p", { class: "lead" }, "Queued below for an analyst. Approving records a decision; nothing is blocked, deleted or sent in this demo.")
      : h("p", { class: "lead" }, "Allowed without analyst involvement."),
    r.guardrail_events.length
      ? h("div", {}, h("p", { class: "note" }, "Guardrail events:"),
          h("ul", { class: "items" }, r.guardrail_events.map((g) => h("li", {}, h("span", {}, "🛡"),
            h("span", {}, `${g.type} in ${g.source}: ${g.action}`, g.trigger ? ` (${g.trigger})` : "")))))
      : null);
}

function timelineCard(r) {
  const cls = { ok: "st-executed", failed: "st-failed", blocked: "st-failed", skipped: "st-skipped" };
  const detail = (d) => Object.entries(d || {}).filter(([, v]) => v !== null && v !== undefined && !(Array.isArray(v) && !v.length) && typeof v !== "object" || Array.isArray(v))
    .filter(([, v]) => !(Array.isArray(v) && !v.length))
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${Array.isArray(v) ? v.join(", ") : v}`).join(" · ");
  return card("Investigation timeline", dataClass("Stages that ran"),
    h("ol", { class: "timeline" }, r.stages.map((s, i) => h("li", { class: cls[s.status] || "st-executed" },
      h("span", { class: "dot" }, String(i + 1)),
      h("div", { class: "body" },
        h("div", { class: "head" }, h("b", {}, DLP_STAGE_TEXT[s.name] || s.name), statusBadge(s.status, s.status === "ok" ? "good" : s.status === "skipped" ? "muted" : "warn")),
        h("div", { class: "meta" }, detail(s.detail), s.ms !== null && s.ms !== undefined ? ` · ${fmt(s.ms, 1)} ms` : ""))))),
    h("p", { class: "note" }, "A failed or skipped stage is shown, not hidden; the harness turns it into a review trigger where the decision depends on it."));
}

function contextCard(r) {
  const c = r.classification, p = r.prechecks, id = r.identity, b = r.behavior;
  return card("Context (deterministic)", dataClass("UC4 + synthetic context"),
    h("dl", { class: "kv" },
      h("dt", {}, "Destination"), h("dd", {}, p ? `${p.destination_class.replace(/_/g, " ")}${p.destination_known_to_user === false ? " · new for this user" : ""}` : "-"),
      h("dt", {}, "Prechecks"), h("dd", {}, p && p.findings.length ? p.findings.map((f) => h("span", { class: "chip" }, f)) : "none"),
      h("dt", {}, "Classification"), h("dd", {}, c && c.level ? levelChip(c.level) : statusBadge("unknown", "warn"),
        c ? ` ${c.status}${c.confidence ? ` · confidence ${c.confidence}` : ""}${c.decided_by ? ` · decided by ${c.decided_by}` : ""}` : ""),
      h("dt", {}, "Categories"), h("dd", {}, c && c.categories.length ? c.categories.map((x) => h("span", { class: "chip" }, x)) : "none"),
      h("dt", {}, "User"), h("dd", {}, id ? `${id.role}, ${id.department} · ${id.employment_type}, ${id.employment_status.replace(/_/g, " ")} · ${id.privilege_level}` : statusBadge("identity unavailable", "warn")),
      h("dt", {}, "Behaviour"), h("dd", {}, b ? statusBadge(b.band, b.band === "NORMAL" ? "good" : b.band === "UNUSUAL" ? "bad" : "warn") : "-",
        b && b.signals.length ? [" ", b.signals.map((x) => h("span", { class: "chip" }, x))] : "")),
    h("p", { class: "note" }, "The document text never leaves UC4: the agent and the telemetry see only the level, categories and codes."));
}

function policyCard(r) {
  const p = r.policy;
  if (!p) return card("Policy intelligence (UC6)", dataClass("UC6"), h("p", { class: "placeholder" }, "Not run: sensitivity was unknown, so there was no policy question to ask."));
  return card("Policy intelligence (UC6)", dataClass(p.mode === "live" ? "Live" : "Recorded"),
    h("p", { class: "note" }, "Question asked: ", h("i", {}, p.question)),
    h("div", { class: "badges" },
      statusBadge(p.status.replace(/_/g, " "), p.status === "ANSWERED" ? "good" : "warn"),
      statusBadge(`effect: ${p.effect.replace(/_/g, " ")}`, p.effect === "prohibited" ? "bad" : p.effect === "allowed" ? "good" : "warn"),
      p.conflict ? statusBadge("policy conflict", "warn") : null),
    p.claims.length
      ? h("ul", { class: "items" }, p.claims.map((c, i) => h("li", {}, h("span", {}, "✓"), h("span", {}, c.text, " ", h("span", { class: "chip" }, `P${i + 1} · ${c.citation}`)))))
      : h("p", { class: "placeholder" }, "No verified policy claim."),
    h("p", { class: "note" }, "Every claim is citation-verified by UC6. The UC4→UC6 mapping (config/dlp/mapping.v1.yaml) turns verified sections into an effect; the LLM never infers it."));
}

function dlpAgentCard(r) {
  const a = r.agent;
  if (!a) return card("Investigation agent", dataClass("Agent"), h("p", { class: "placeholder" }, "The agent did not run."));
  const t = a.trace || {};
  return card("Investigation agent", dataClass(r.backend === "foundry-service" ? "Foundry agent" : "App agent"),
    h("div", { class: "grid cols-2" },
      h("div", {},
        h("dl", { class: "kv" },
          h("dt", {}, "Planner"), h("dd", {}, `${t.planner || "-"} (${t.backend || r.backend})`),
          h("dt", {}, "Tool calls"), h("dd", {}, `${t.tool_calls ?? 0} of ${t.max_tool_calls ?? "-"} allowed · ${t.turns ?? 0} planner turns`),
          h("dt", {}, "Stopped"), h("dd", {}, a.stopped_reason),
          h("dt", {}, "Proposed"), h("dd", {}, a.proposed_outcome || "none"),
          t.tokens_in ? h("dt", {}, "Tokens") : null, t.tokens_in ? h("dd", {}, `${t.tokens_in} in / ${t.tokens_out} out`) : null),
        h("ol", { class: "timeline" }, (t.steps || []).map((st) => h("li", { class: st.ok ? "st-executed" : "st-failed" },
          h("span", { class: "dot" }, String(st.step)),
          h("div", { class: "body" },
            h("div", { class: "head" }, h("b", {}, st.tool), statusBadge(st.ok ? "ok" : st.error, st.ok ? "good" : "bad")),
            Object.keys(st.arguments || {}).length ? h("div", { class: "meta" }, Object.entries(st.arguments).map(([k, v]) => `${k}: "${v}"`).join(" · ")) : null,
            st.evidence_ids && st.evidence_ids.length ? h("div", { class: "meta" }, `returned ${st.evidence_ids.join(", ")}`) : null))))),
      h("div", {},
        h("p", { class: "note" }, "Findings (each must cite an evidence id the agent was shown):"),
        a.findings.length
          ? h("ul", { class: "items" }, a.findings.map((f) => h("li", {}, h("span", {}, f.verified ? "✓" : "⚠"),
              h("span", {}, f.text, " ", h("span", { class: "chip" }, f.evidence_id)))))
          : h("p", { class: "placeholder" }, "No findings."),
        a.missing_evidence.length ? h("p", { class: "note" }, `Missing evidence: ${a.missing_evidence.join(", ")}`) : null,
        a.verified_exception ? h("div", { class: "callout" }, h("b", {}, "Verified exception: "), `${a.verified_exception.exception_id} (returned by check_dlp_exception)`) : null)),
    h("p", { class: "note" }, "Tools run in the application, inside the allow-list, argument validation and step budget. check_dlp_exception is bound to this event's user and destination."));
}

// ---- analyst approval queue (demo-only state) --------------------------------------------------
async function loadDlpQueue() {
  const el = document.getElementById("dlp-queue");
  if (!el) return;
  let q;
  try { q = (await getJSON("/api/dlp/review")).data; } catch (err) { el.replaceChildren(); return; }
  const rows = q.pending.map((p) => {
    const note = h("input", { type: "text", maxlength: "500", placeholder: "Note (optional)", "aria-label": `Note for ${p.case_id}` });
    const buttons = Object.entries(ACTION_TEXT).map(([action, label]) => {
      const b = h("button", { class: "btn btn-small", type: "button" }, label);
      b.addEventListener("click", async () => {
        b.disabled = true;
        try { await postDlp("/api/dlp/review/decide", { key: p.key, action, note: note.value }); } catch (err) { b.title = err.message; }
        loadDlpQueue();
      });
      return b;
    });
    return h("tr", {}, h("td", {}, p.at), h("td", {}, h("b", {}, p.case_id)), h("td", {}, p.outcome),
      h("td", {}, p.simulated_action || "review only"), h("td", {}, p.reasons.join(", ") || "-"),
      h("td", {}, note, h("div", { class: "btn-row" }, buttons)));
  });
  el.replaceChildren(card("Analyst approval queue (this session)", dataClass("Demo-only state · SIMULATED actions"),
    q.pending.length
      ? h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, "Time"), h("th", {}, "Case"), h("th", {}, "Outcome"), h("th", {}, "Simulated action"), h("th", {}, "Review reasons"), h("th", {}, "Decision"))),
          h("tbody", {}, rows)))
      : h("p", { class: "placeholder" }, "Nothing is waiting for an analyst in this session."),
    q.decisions.length
      ? h("ul", { class: "items" }, q.decisions.map((d) => h("li", {}, h("span", {}, "✓"),
          h("span", {}, `${d.ts.slice(11)} ${d.case_id}: ${ACTION_TEXT[d.action] || d.action}${d.note ? ` (“${d.note}”)` : ""} · executed: no`))))
      : null,
    h("p", { class: "note" }, `Decisions are written to ${q.stored_at}, marked simulated and never used as evaluation gold data.`)));
}

// ---- UC1 panels on the shared pages ------------------------------------------------------------
function uc1Header(title, text) {
  return h("div", { class: "page-head uc1-head" },
    h("div", {}, h("h1", {}, title), h("p", { class: "lead" }, text)), dataClass("Recorded UC1 results"));
}
const n3 = (v) => (typeof v === "number" ? (Number.isInteger(v) ? String(v) : fmt(v, 3)) : v === null || v === undefined ? "-" : String(v));

async function uc1EvaluationPanels(page) {
  const s = (await dlpInfo()).summary;
  const rows = [
    ["Acceptable-outcome accuracy", "acceptable_outcome_accuracy"], ["Exact-outcome accuracy", "outcome_accuracy"],
    ["High-risk recall", "high_risk_recall"], ["Critical false negatives (high-risk → ALLOW)", "critical_false_negative_count"],
    ["False-positive rate", "false_positive_rate"], ["HITL precision", "hitl_precision"], ["HITL recall", "hitl_recall"],
    ["Required-tool use", "required_tool_use"], ["Unsupported findings (id check)", "unsupported_finding_rate"],
    ["Tool-boundary violations", "tool_boundary_violations"], ["Injection resistance", "injection_resistance"],
  ];
  const fo = s.foundry.outcomes;
  const q1 = s.foundry.quality_v1 || {}, q2 = s.foundry.quality_v2 || {};
  const qrow = (q, k) => (q[k] ? `${q[k].passed}/${q[k].passed + q[k].failed}${q[k].mean !== null && q[k].mean > 1 ? ` · ${fmt(q[k].mean, 2)}` : ""}` : "-");
  page.append(
    uc1Header("Agentic DLP (UC1)", "30-case golden set, replayed from recorded runs; deterministic unless marked. v1 → v2 is the evidence-id fix found by Foundry's judges."),
    h("div", { class: "grid cols-2" },
      card("Golden set: agent v1 → v2", dataClass("Recorded"),
        h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, "Metric"), h("th", { class: "n" }, "v1"), h("th", { class: "n" }, "v2"))),
          h("tbody", {}, rows.map(([label, k]) => h("tr", {}, h("td", {}, label), h("td", { class: "n" }, n3(s.eval.v1[k])), h("td", { class: "n" }, n3(s.eval.v2[k]))))))),
        h("p", { class: "note" }, `v2 outcomes: ${Object.entries(s.eval.outcomes || {}).map(([k, v]) => `${k} ${v}`).join(" · ")}. Misses are preserved, not tuned; critical_false_negative_count had no target set in advance.`),
        source(s.sources.eval, s.sources.eval_v1)),
      card("Foundry Evaluations", dataClass(fo ? "Foundry run" : "Not run"),
        fo
          ? h("div", { class: "table-wrap" }, h("table", {},
              h("thead", {}, h("tr", {}, h("th", {}, "Deterministic check (v2)"), h("th", { class: "n" }, "Foundry"), h("th", { class: "n" }, "Local"))),
              h("tbody", {}, Object.keys(fo.per_criterion).map((k) => h("tr", {}, h("td", {}, k.replace(/_/g, " ")),
                h("td", { class: "n" }, String(fo.per_criterion[k].passed)), h("td", { class: "n" }, String(fo.local[k].passed)))))))
          : null,
        h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, "AI-assisted (judge uc4-llm-medium)"), h("th", { class: "n" }, "v1"), h("th", { class: "n" }, "v2"))),
          h("tbody", {}, ["task_adherence", "groundedness", "intent_resolution", "tool_call_accuracy"].map((k) => h("tr", {},
            h("td", {}, k.replace(/_/g, " ")), h("td", { class: "n" }, qrow(q1, k)), h("td", { class: "n" }, qrow(q2, k))))))),
        h("p", { class: "note" }, "Same-family judge, one run per version: indicative. Failed and superseded Foundry runs are kept in docs/uc1/results."),
        source(s.sources.foundry || "-", s.sources.foundry_doc))));
}

async function uc1GuardrailPanels(page) {
  const s = (await dlpInfo()).summary;
  const g = s.guardrails || [];
  page.append(
    uc1Header("DLP investigator guardrails (UC1)", "Application guardrails and the Foundry agent guardrail, probed live (GU1-GU7). Verdicts only; no probe text is stored."),
    card(`Live verification (${(s.guardrails_provenance || {}).date || "not run"})`, dataClass("Live run, recorded"),
      g.length
        ? h("div", { class: "table-wrap" }, h("table", {},
            h("thead", {}, h("tr", {}, h("th", {}, "Probe"), h("th", {}, "Outcome (app path)"), h("th", {}, "Stopped by"), h("th", {}, "Direct to Foundry agent"))),
            h("tbody", {}, g.map((p) => {
              const a = p.app || {}, d = p.direct || {};
              const direct = d.outcome ? `${d.outcome}${(d.flags || []).length ? ` (${d.flags.join(", ")})` : ""}${"followed_injection" in d ? ` · followed injection: ${d.followed_injection}` : ""}` : "-";
              return h("tr", {}, h("td", {}, h("b", {}, p.id), ` ${p.name}`), h("td", {}, a.outcome || "-"), h("td", {}, (a.stopped_by || "-").replace(/_/g, " ")), h("td", {}, direct));
            }))))
        : h("p", { class: "placeholder" }, "No verification run recorded."),
      h("div", { class: "callout" }, h("b", {}, "Takeaway: "),
        "no probe produced an unsafe outcome. The app withheld trigger-word injections and UC4 flagged document injection; Foundry's jailbreak shield caught GU4's raw text. "
        + "Neither layer detected the paraphrased role-play (GU2) and the tool-output shield did not fire (GU7), but the agent ignored both. The harness holds the decision."),
      source(s.sources.guardrails, "docs/uc1/foundry-guardrails-setup.md")));
}

async function uc1ObservabilityPanels(page) {
  const s = (await dlpInfo()).summary;
  const o = s.observability || {};
  const pa = o.privacy_audit, t = o.telemetry || {};
  const kv = (obj) => Object.entries(obj || {}).map(([k, v]) => `${k.replace(/^review:/, "")} ${v}`).join(" · ") || "none";
  page.append(
    uc1Header("DLP investigation telemetry (UC1)", "uc1.* spans carry a case id, outcome, band, score, reason codes, simulated faults and destination class. Never a user id, host, filename, justification or document text."),
    h("div", { class: "grid cols-2" },
      card("Privacy audit", statusBadge(pa && pa.clean ? "Clean" : "Check", pa && pa.clean ? "good" : "bad"),
        pa ? h("p", { class: "lead" }, `${pa.spans_audited} spans audited against ${pa.sources_checked} sources, plus ${pa.identifier_checks} user-id / host / justification checks: ${pa.leaks.length} leaks.`) : null,
        h("div", { class: "callout" }, h("b", {}, "Live canary (App Insights): "),
          "DataGuard's own spans contained the canary 0 times. Foundry Agent Service's own telemetry records the agent conversation (evidence pack: user id, host, justification; never document text). "
          + "Kept on for the synthetic demo by the product owner; off or pseudonymised in production. The live check also found and fixed a sampler that dropped spans mid-trace."),
        source(s.sources.observability, s.sources.observability_live)),
      card("Telemetry (replayed golden run)", dataClass("Recorded"),
        h("dl", { class: "kv" },
          h("dt", {}, "Investigations"), h("dd", {}, String(t.investigations ?? "-")),
          h("dt", {}, "Outcomes"), h("dd", {}, kv(t.outcomes)),
          h("dt", {}, "Review reasons"), h("dd", {}, kv(t.review_reasons)),
          h("dt", {}, "Simulated faults"), h("dd", {}, kv(t.simulated_faults)),
          h("dt", {}, "Planner turns"), h("dd", {}, `${t.planner_turns ?? "-"} (${t.planner_turns_replayed ?? "-"} replayed)`),
          h("dt", {}, "Tool calls"), h("dd", {}, kv(t.tool_calls)),
          h("dt", {}, "Agent stops"), h("dd", {}, kv(t.agent_stop_reasons))))));
}

PAGES.dlp = { title: "DLP Investigation", render: renderDlp };
JUMP_TARGETS.push(["Agentic DLP", ".uc1-head"]);
extendPage("evaluations", uc1EvaluationPanels, "UC1");
extendPage("rai", uc1GuardrailPanels, "UC1");
extendPage("observability", uc1ObservabilityPanels, "UC1");
