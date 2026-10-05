"use strict";
// Insider Risk Investigation (UC2) page, plus UC2 panels on the shared Evaluations, Responsible AI /
// Guardrails and Observability pages. Loaded after app.js, policy.js and dlp.js and reuses their
// helpers (h, card, dataClass, source, statusBadge, getJSON, applyMode, fmt, pct, levelChip, PAGES,
// extendPage, JUMP_TARGETS). Every global here is prefixed `ir`/`IR_` (scripts share one scope). DOM
// is built with textContent only, never innerHTML from data. Every value comes from the demo
// server: the REAL UC2 pipeline (REPLAY by default) or the committed docs/uc2/results files.

const irState = { info: null, caseId: "I13", arch: "full", backend: "chat-completions", last: null,
  busy: false, error: null, mode: null };

const IR_OUTCOME_TEXT = {
  MONITOR: "Monitor. Nothing here needs an analyst; the activity stays in normal monitoring.",
  INVESTIGATE: "Investigate. An analyst should look at this case. No action is taken.",
  ESCALATE: "Escalate. A senior analyst should review this case promptly. No action is taken; a person decides what happens next.",
  HUMAN_REVIEW: "Human review. The system could not reach a safe recommendation on its own (missing, conflicting or failed evidence).",
};
const IR_OUTCOME_KIND = { MONITOR: "l-PUBLIC", INVESTIGATE: "l-none", ESCALATE: "failure", HUMAN_REVIEW: "l-none" };
const IR_REASON_TEXT = {
  "review:required_evidence_missing": "Evidence the rubric needs for this case was not collected.",
  "review:required_capability_failed": "A required source (identity, logs, UC4 or UC6) failed.",
  "review:data_uncertain": "UC4 could not determine the sensitivity of a file.",
  "review:specialist_conflict": "A specialist agent cited conflicting evidence.",
  "review:agent_failure": "An agent did not produce a valid result.",
  "review:agent_disagreement": "The risk agent recommended an outcome 2+ levels below the rubric.",
  "review:policy_conflict": "UC6 found the policies in conflict at this data level.",
  "review:policy_insufficient_material": "No policy covers this high-anomaly case.",
  "review:unsupported_conclusion": "An agent's output contained intent or disciplinary wording; it was withheld.",
};
const IR_ACTION_TEXT = {
  agree: "Agree",
  disagree_lower: "Should be lower",
  disagree_higher: "Should be higher",
  needs_more_information: "Needs more information",
};
const IR_ROLE_TEXT = {
  orchestrator: "Orchestrator agent", behavior: "Behavior agent", investigator: "Investigation agent",
  risk: "Risk agent (no tools)", single: "Single agent", "lean-orchestrator": "Lean orchestrator",
};
const irN = (v) => (typeof v === "number" ? String(Number(v.toFixed(3))) : v === null || v === undefined ? "-" : String(v));

async function irPost(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const out = await res.json();
  if (!res.ok) throw new Error(out.error || `HTTP ${res.status}`);
  return out;
}
async function irInfo() {
  if (!irState.info) {
    const env = await getJSON("/api/insider");
    applyMode(env);
    irState.info = env.data;
    irState.mode = env.mode_label;
  }
  return irState.info;
}
function irRerender() { return location.hash === "#insider" ? route() : Promise.resolve(); }

// ---- the page --------------------------------------------------------------------------------
async function renderInsider(page) {
  const info = await irInfo();
  page.append(
    h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, "Insider Risk Investigation"),
        h("p", { class: "lead" },
          "ML detects the signal; agents investigate it; deterministic controls govern the outcome; humans keep the authority. "
          + "An Isolation Forest scores each user-day against the user's own history. Bounded agents gather and explain the evidence, "
          + "and a versioned rubric decides MONITOR, INVESTIGATE, ESCALATE or HUMAN_REVIEW. An anomaly is not evidence of intent, and nothing here takes an action.")),
      dataClass(irState.mode === "LIVE" ? "Live pipeline" : "Replay: recorded pipeline")),
    h("div", { class: "grid cols-2" }, irCaseCard(info), irFlowCard()),
    h("div", { id: "ir-result" }),
    h("div", { id: "ir-queue" }),
  );
  irDrawResult();
  irLoadQueue();
}

function irCaseCard(info) {
  const list = h("div", { class: "examples" }, info.examples.map((e) => {
    const b = h("button", { class: `example${irState.caseId === e.id ? " active" : ""}`, type: "button" },
      h("span", { class: "t" }, h("span", {}, e.title), h("span", { class: "chip" }, e.id)),
      h("span", { class: "d" }, e.what + (e.foundry ? " · also recorded through the Foundry agents" : "")));
    b.addEventListener("click", () => {
      irState.caseId = e.id; irState.last = null; irState.error = null;
      if (!e.foundry) irState.backend = "chat-completions";
      irRerender();
    });
    return b;
  }));
  const sel = info.examples.find((e) => e.id === irState.caseId);
  const arch = h("select", { "aria-label": "Architecture" },
    h("option", { value: "full" }, "Full: orchestrator + 3 specialist agents"),
    h("option", { value: "lean" }, "Lean: orchestrator + investigator"),
    h("option", { value: "single" }, "Single agent"));
  arch.value = irState.arch;
  arch.addEventListener("change", () => { irState.arch = arch.value; if (arch.value !== "full") irState.backend = "chat-completions"; irRerender(); });
  const backend = h("select", { "aria-label": "Agent backend" },
    h("option", { value: "chat-completions" }, "App agents (chat-completions planner)"),
    h("option", { value: "foundry-service" }, "Foundry agents: dataguard-insider-*"));
  backend.value = irState.backend;
  backend.disabled = irState.arch !== "full" || !(sel && sel.foundry);
  backend.title = backend.disabled ? "The Foundry agents are the full system, recorded for I13, I24, I30 and I34" : "";
  backend.addEventListener("change", () => { irState.backend = backend.value; });
  const go = h("button", { class: "btn", type: "button" }, irState.busy ? "Investigating…" : "Investigate");
  go.disabled = irState.busy;
  go.addEventListener("click", irRun);
  return card("Alert", dataClass("Synthetic user-day"),
    list,
    sel ? irCaseView(sel.case) : null,
    h("div", { class: "btn-row" }, arch, backend, go),
    irState.mode !== "LIVE"
      ? h("p", { class: "note" }, "REPLAY runs the real pipeline on recorded model responses (no network). Every curated case is recorded for all three architectures.")
      : null);
}

function irCaseView(c) {
  return h("dl", { class: "kv" },
    h("dt", {}, "User-day"), h("dd", {}, `${c.user_id} · ${c.date}`),
    h("dt", {}, "Role family"), h("dd", {}, c.role_family || "-"),
    h("dt", {}, "Trigger"), h("dd", {}, c.trigger.replace(/_/g, " "), c.files ? ` · ${c.files} file(s) in scope` : ""),
    c.simulated_faults.length ? h("dt", {}, "Fault") : null,
    c.simulated_faults.length ? h("dd", {}, c.simulated_faults.map((f) => statusBadge(`SIMULATED: ${f}`, "warn"))) : null);
}

function irFlowCard() {
  const steps = ["Isolation Forest (authoritative)", "Orchestrator", "Behavior / Identity / Investigation", "UC4 data + UC6 policy", "Risk agent", "Rubric + floors", "Analyst"];
  return card("How a case is decided", dataClass("Architecture"),
    h("div", { class: "loop" }, steps.flatMap((s, i) => [i ? h("span", { class: "arrow" }, "→") : null, h("span", { class: `node${i === steps.length - 1 ? " hot" : ""}` }, s)])),
    h("div", { class: "callout" },
      h("b", {}, "Structural controls, not prompt requests: "),
      "agents cannot change the anomaly score or band; each agent has its own tool allow-list and budget, and a fresh context; log text that looks like an instruction is withheld; "
      + "intent or disciplinary wording is withheld from agent output and sends the case to review; the rubric owns the outcome, and the agents can raise it but lowering it by two levels triggers review; "
      + "no HR, protected or notice-period inputs exist; no tool can act."));
}

async function irRun() {
  if (irState.busy) return;
  irState.busy = true; irState.error = null; irRerender();
  try {
    const env = await irPost("/api/insider/investigate", { case_id: irState.caseId, architecture: irState.arch, backend: irState.backend });
    applyMode(env);
    irState.last = { ...env.data, data_class: env.data_class };
  } catch (err) {
    irState.error = err.message;
  } finally {
    irState.busy = false;
    await irRerender();
    const el = document.getElementById("ir-result");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

// ---- result ----------------------------------------------------------------------------------
function irDrawResult() {
  const el = document.getElementById("ir-result");
  if (!el) return;
  el.replaceChildren();
  if (irState.error) { el.append(h("div", { class: "card error" }, `Could not investigate: ${irState.error}`)); return; }
  const r = irState.last;
  if (!r) { el.append(h("div", { class: "card empty-state" }, "Pick a case (start with the flagship), choose the architecture, and press Investigate.")); return; }
  el.append(irVerdict(r));
  el.append(h("div", { class: "grid cols-2" }, irAnomalyCard(r), irDecisionCard(r)));
  el.append(irSummaryCard(r));
  el.append(h("div", { class: "grid cols-2" }, irContextCard(r), irPolicyCard(r)));
  el.append(irAgentsCard(r));
}

function irVerdict(r) {
  const d = r.decision, t = r.totals;
  const modeLabel = r.mode === "live" ? "LIVE" : r.mode === "offline" ? "OFFLINE" : "REPLAY";
  return h("div", { class: `verdict ${IR_OUTCOME_KIND[d.outcome] || "l-none"}` },
    h("div", { class: "lvl-big" }, d.outcome.replace(/_/g, " ")),
    h("p", { class: "lead" }, IR_OUTCOME_TEXT[d.outcome] || ""),
    h("div", { class: "badges" },
      statusBadge(modeLabel, modeLabel === "LIVE" ? "bad" : "muted"),
      statusBadge(`${r.architecture} architecture`, "muted"),
      statusBadge(r.backend === "foundry-service" ? "Foundry agents" : "App agents", "muted"),
      statusBadge(`anomaly ${r.anomaly.anomaly_band.replace(/_/g, " ")} · ${fmt(r.anomaly.anomaly_score, 3)}`, r.anomaly.anomaly_band === "HIGH_ANOMALY" ? "bad" : r.anomaly.anomaly_band === "ELEVATED" ? "warn" : "good"),
      statusBadge(`score ${d.score} · floor ${d.floor}`, "muted"),
      d.agent_recommendation ? statusBadge(`risk agent recommended ${d.agent_recommendation}`, "muted") : null,
      d.analyst_review_required ? statusBadge("analyst review", "warn") : statusBadge("no analyst needed", "good"),
      r.case.simulated_faults.map((f) => statusBadge(`SIMULATED fault: ${f}`, "warn")),
      statusBadge(`${t.agents_run} agents · ${t.model_calls} model calls · ${t.tool_calls} tool calls`, "muted"),
      statusBadge(`rubric ${d.rubric_version}`, "muted"),
      statusBadge(`${Math.round(r.wall_ms)} ms`, "muted")));
}

function irAnomalyCard(r) {
  const a = r.anomaly;
  return card("Anomaly (Isolation Forest, authoritative)", dataClass("ML model output"),
    h("dl", { class: "kv" },
      h("dt", {}, "Score / band"), h("dd", {}, `${fmt(a.anomaly_score, 4)} · ${a.anomaly_band.replace(/_/g, " ")}`),
      h("dt", {}, "Thresholds"), h("dd", {}, `ELEVATED ≥ ${a.thresholds.elevated} (p95) · HIGH ≥ ${a.thresholds.high} (p99), from training days only`),
      h("dt", {}, "Model"), h("dd", {}, `${a.model_id} ${a.model_version}`)),
    a.contributing_signals.length
      ? h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, "Feature"), h("th", { class: "n" }, "Today"), h("th", { class: "n" }, "Baseline"), h("th", { class: "n" }, "Robust z"), h("th", { class: "n" }, "Score drop if reset"))),
          h("tbody", {}, a.contributing_signals.map((s) => h("tr", {},
            h("td", {}, s.feature.replace(/_/g, " ")), h("td", { class: "n" }, irN(s.observed)), h("td", { class: "n" }, irN(s.baseline_median)),
            h("td", { class: "n" }, irN(s.robust_z)), h("td", { class: "n" }, irN(s.score_drop_if_reset)))))))
      : h("p", { class: "placeholder" }, "No contributing signal: the day looks like the user's own history."),
    h("p", { class: "note" }, "Compared with the user's own baseline for this kind of day. Contributions are counterfactual: how much the score falls if that feature is reset to baseline. No agent can change the score or band."));
}

function irDecisionCard(r) {
  const d = r.decision;
  const review = d.reason_codes.filter((c) => c.startsWith("review:"));
  const rules = d.reason_codes.filter((c) => !c.startsWith("review:") && !/:[+-]?\d+$/.test(c));
  return card("Decision trace (deterministic harness)", dataClass("Rubric output"),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Factor"), h("th", {}, "Value"), h("th", { class: "n" }, "Points"))),
      h("tbody", {}, d.contributing_factors.map((f) => h("tr", {},
        h("td", {}, f.factor.replace(/_/g, " ")), h("td", {}, String(f.value).replace(/\+/g, " + ").replace(/_/g, " ")), h("td", { class: "n" }, f.points > 0 ? `+${f.points}` : String(f.points)))),
        h("tr", {}, h("td", {}, h("b", {}, "Score")), h("td", {}, `band ${d.band} · floor ${d.floor}`), h("td", { class: "n" }, h("b", {}, String(d.score))))))),
    rules.length ? h("p", { class: "note" }, "Rules applied: ", rules.map((c) => h("span", { class: "chip" }, c))) : null,
    review.length
      ? h("ul", { class: "items" }, review.map((c) => h("li", {}, h("span", {}, "⚑"), h("span", {}, IR_REASON_TEXT[c] || c, " ", h("span", { class: "chip" }, c)))))
      : null,
    h("p", { class: "note" }, "Bands: score ≥60 ESCALATE, ≥30 INVESTIGATE. Floor: HIGH_ANOMALY → at least INVESTIGATE; with Highly Confidential data leaving the company → ESCALATE. A verified approval caps the outcome at INVESTIGATE unless the transfer went to a personal destination."));
}

function irSummaryCard(r) {
  const s = r.decision.summary || [];
  return card("Analyst summary", dataClass("Fixed templates, cited"),
    s.length
      ? h("ul", { class: "items" }, s.map((x) => h("li", {}, h("span", {}, "•"),
          h("span", {}, x.text, " ", h("span", { class: "chip" }, x.claim_type.replace(/_/g, " ").toLowerCase()), " ", h("span", { class: "chip" }, x.evidence_id)))))
      : h("p", { class: "placeholder" }, "No summary."),
    h("p", { class: "note" }, "Each line is a fixed template filled from evidence and labelled observed fact, inferred anomaly, verified policy or missing evidence. It never states intent, guilt or an employment action."));
}

function irContextCard(r) {
  const id = r.identity, c = r.classification;
  return card("Context (deterministic services)", dataClass("Identity + UC4"),
    h("dl", { class: "kv" },
      h("dt", {}, "Identity"), h("dd", {}, id && id.available ? `${id.role} · ${id.role_family} · ${id.privilege_level} privilege` : statusBadge("not retrieved or unavailable", "warn")),
      h("dt", {}, "Data (UC4)"), h("dd", {}, c ? [c.max_level ? levelChip(c.max_level) : statusBadge("unknown", "warn"), ` ${c.files.length} file(s)`, c.uncertain ? " · uncertain" : ""] : "not classified"),
      h("dt", {}, "High-risk"), h("dd", {}, c && c.high_risk_categories.length ? c.high_risk_categories.map((x) => h("span", { class: "chip" }, x)) : "none"),
      h("dt", {}, "Approvals"), h("dd", {}, r.approvals.length ? r.approvals.map((a) => h("span", { class: "chip" }, `${a.ref} ${a.type.replace(/_/g, " ")}${a.verified ? " · verified" : ` · ${a.reason}`}`)) : "none verified"),
      h("dt", {}, "Failures"), h("dd", {}, r.failures.length ? r.failures.map((f) => statusBadge(f, "warn")) : "none"),
      r.guardrail_events.length ? h("dt", {}, "Guardrails") : null,
      r.guardrail_events.length ? h("dd", {}, r.guardrail_events.map((g) => statusBadge(`${g.type.replace(/_/g, " ")}: ${(g.action || "").replace(/_/g, " ")}`, "warn"))) : null),
    h("p", { class: "note" }, "Identity is access context only: role, privilege, expected repositories. There are no HR, performance, protected-characteristic or notice-period fields. Document text never leaves UC4."));
}

function irPolicyCard(r) {
  const ps = r.policies || [];
  return card("Policy intelligence (UC6)", dataClass("Citation-verified"),
    ps.length
      ? ps.map((p) => h("div", {},
          h("div", { class: "badges" }, h("b", {}, p.topic.replace(/_/g, " ")), " ",
            statusBadge(p.status.replace(/_/g, " "), p.status === "ANSWERED" ? "good" : "warn"),
            statusBadge(`effect: ${p.effect.replace(/_/g, " ")}`, p.effect === "prohibited" ? "bad" : p.effect === "allowed" ? "good" : "warn"),
            p.conflict ? statusBadge("policy conflict", "warn") : null,
            p.conflict_note ? statusBadge(p.conflict_note.replace(/_/g, " "), "muted") : null),
          p.claims.length
            ? h("ul", { class: "items" }, p.claims.map((c) => h("li", {}, h("span", {}, "✓"), h("span", {}, c.text, " ", h("span", { class: "chip" }, `${c.evidence_id} · ${c.citation}`)))))
            : h("p", { class: "placeholder" }, "No verified claim.")))
      : h("p", { class: "placeholder" }, "No policy question was needed for this case."),
    h("p", { class: "note" }, "UC6 answers only from cited policy sections; each claim is verified against its citation. An agent cannot add a policy that UC6 did not return."));
}

function irAgentsCard(r) {
  return card("Agents", dataClass(r.backend === "foundry-service" ? "Foundry agents" : "App agents"),
    h("div", { class: "grid cols-2" }, r.agents.map((a) => {
      const o = a.output || {};
      const head = o.recommended_outcome ? `recommended ${o.recommended_outcome} (${o.confidence})`
        : o.behavior_summary ? o.behavior_summary : o.timeline ? `${o.timeline.length} timeline events · ${o.missing_evidence.length} missing` : "";
      return h("div", { class: "card" },
        h("div", { class: "head" }, h("b", {}, IR_ROLE_TEXT[a.role] || a.name), " ", statusBadge(a.stopped_reason.split(":")[0].replace(/_/g, " "), a.stopped_reason.startsWith("planner_error") || a.stopped_reason.includes("budget") ? "warn" : "good")),
        h("p", { class: "note" }, `${a.turns} turns · ${a.tool_calls} tool calls · ${a.model_calls} model calls${a.tokens_in ? ` · ${a.tokens_in} tokens in` : ""}`),
        head ? h("p", {}, head) : null,
        a.unsupported_conclusions ? statusBadge(`${a.unsupported_conclusions} unsupported conclusion(s) withheld`, "warn") : null,
        a.steps.length
          ? h("ol", { class: "timeline" }, a.steps.map((st) => h("li", { class: st.ok ? "st-executed" : "st-failed" },
              h("span", { class: "dot" }, String(st.step)),
              h("div", { class: "body" },
                h("div", { class: "head" }, h("b", {}, st.tool), statusBadge(st.ok ? "ok" : st.error, st.ok ? "good" : "bad")),
                st.evidence_ids && st.evidence_ids.length ? h("div", { class: "meta" }, `returned ${st.evidence_ids.slice(0, 8).join(", ")}${st.evidence_ids.length > 8 ? "…" : ""}`) : null))))
          : h("p", { class: "placeholder" }, "No tools (synthesizes the evidence bundle)."),
        o.rationale ? h("p", { class: "note" }, o.rationale) : null);
    })),
    h("p", { class: "note" }, "Delegation is executed by DataGuard, not by the model: each specialist gets a fresh context with only its brief. Tools run inside the allow-list, argument checks and budgets."));
}

// ---- analyst decisions (demo-only state) ------------------------------------------------------
async function irLoadQueue() {
  const el = document.getElementById("ir-queue");
  if (!el) return;
  let q;
  try { q = (await getJSON("/api/insider/review")).data; } catch (err) { el.replaceChildren(); return; }
  const rows = q.pending.map((p) => {
    const note = h("input", { type: "text", maxlength: "500", placeholder: "Note (optional)", "aria-label": `Note for ${p.case_id}` });
    const buttons = Object.entries(IR_ACTION_TEXT).map(([action, label]) => {
      const b = h("button", { class: "btn btn-small", type: "button" }, label);
      b.addEventListener("click", async () => {
        b.disabled = true;
        try { await irPost("/api/insider/review/decide", { key: p.key, action, note: note.value }); } catch (err) { b.title = err.message; }
        irLoadQueue();
      });
      return b;
    });
    return h("tr", {}, h("td", {}, p.at), h("td", {}, h("b", {}, p.case_id)), h("td", {}, p.architecture), h("td", {}, p.outcome),
      h("td", {}, p.reasons.join(", ") || "-"), h("td", {}, note, h("div", { class: "btn-row" }, buttons)));
  });
  el.replaceChildren(card("Analyst review (this session)", dataClass("Demo-only state · no action exists"),
    q.pending.length
      ? h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, "Time"), h("th", {}, "Case"), h("th", {}, "Architecture"), h("th", {}, "Outcome"), h("th", {}, "Rules / review reasons"), h("th", {}, "Analyst decision"))),
          h("tbody", {}, rows)))
      : h("p", { class: "placeholder" }, "Nothing is waiting for an analyst in this session."),
    q.decisions.length
      ? h("ul", { class: "items" }, q.decisions.map((d) => h("li", {}, h("span", {}, "✓"),
          h("span", {}, `${d.ts.slice(11)} ${d.case_id}: ${IR_ACTION_TEXT[d.action] || d.action}${d.note ? ` (“${d.note}”)` : ""} · executed: no`))))
      : null,
    h("p", { class: "note" }, `Decisions are written to ${q.stored_at}, marked simulated, and never used as evaluation gold data. They are the feedback the controlled learning loop mines offline; the running system never changes itself.`)));
}

// ---- UC2 panels on the shared pages ------------------------------------------------------------
function irHeader(title, text) {
  return h("div", { class: "page-head uc2-head" },
    h("div", {}, h("h1", {}, title), h("p", { class: "lead" }, text)), dataClass("Recorded UC2 results"));
}

async function irEvaluationPanels(page) {
  const s = (await irInfo()).summary;
  const ml = s.ml || {}, sep = ml.separation || {}, mb = (ml.matched_budget || {})["1"] || {};
  const a = s.agents || {};
  const f36 = a.full36 || {};
  const archRow = (label, sm, acc, calls) => h("tr", {}, h("td", {}, label), h("td", { class: "n" }, acc), h("td", { class: "n" }, calls),
    h("td", { class: "n" }, sm ? irN(sm.hitl_recall) : "-"), h("td", { class: "n" }, sm ? irN(sm.critical_misses) : "-"));
  const fo = (s.foundry || {}).outcomes;
  const qa = { ...((s.foundry || {}).agent_quality || {}), ...((s.foundry || {}).risk_quality || {}) };
  const q = (agent, k) => { const v = (qa[agent] || {})[k]; return v ? `${v.passed}/${v.passed + v.failed}${v.mean !== null && v.mean > 1 ? ` · ${fmt(v.mean, 2)}` : ""}` : "-"; };
  page.append(
    irHeader("Insider Risk Investigation (UC2)", "Anomaly model on 60 synthetic users; 36-case golden set through the agents, recorded live; Foundry evaluations. Misses are preserved, not tuned."),
    h("div", { class: "grid cols-2" },
      card("Anomaly detection: Isolation Forest vs statistical baseline", dataClass("Holdout days"),
        h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, "Metric"), h("th", { class: "n" }, "Isolation Forest"), h("th", { class: "n" }, "Baseline"))),
          h("tbody", {},
            h("tr", {}, h("td", {}, "ROC-AUC"), h("td", { class: "n" }, fmt((sep.roc_auc || {}).isolation_forest, 4)), h("td", { class: "n" }, fmt((sep.roc_auc || {}).statistical_baseline, 4))),
            h("tr", {}, h("td", {}, "Precision at 1 alert/day"), h("td", { class: "n" }, irN((mb.isolation_forest || {}).precision)), h("td", { class: "n" }, irN((mb.statistical_baseline || {}).precision))),
            h("tr", {}, h("td", {}, "Recall at 1 alert/day"), h("td", { class: "n" }, irN((mb.isolation_forest || {}).recall)), h("td", { class: "n" }, irN((mb.statistical_baseline || {}).recall)))))),
        h("p", { class: "note" }, `Alert rate by role family (normal days): ${Object.entries(ml.role_family_alert_rates || {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${pct(v.if_alert_rate)}`).join(" · ")}. Fairness is measured by role family only; no protected attribute exists in the data.`),
        h("p", { class: "note" }, ml.statement || ""),
        source(s.sources.ml)),
      card("Agent architectures (live, 12-case sample)", dataClass("Recorded live runs"),
        h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, "Architecture"), h("th", { class: "n" }, "Acceptable"), h("th", { class: "n" }, "Model calls / case"), h("th", { class: "n" }, "HITL recall"), h("th", { class: "n" }, "Critical misses"))),
          h("tbody", {},
            archRow("Single agent", a.single12, a.single12 ? `${Math.round(a.single12.outcome_accuracy_acceptable * a.single12.cases)}/${a.single12.cases}` : "-", a.single12 ? irN(a.single12.model_calls_per_case) : "-"),
            archRow("Lean (2 agents)", a.lean12, a.lean12 ? `${Math.round(a.lean12.outcome_accuracy_acceptable * a.lean12.cases)}/${a.lean12.cases}` : "-", a.lean12 ? irN(a.lean12.model_calls_per_case) : "-"),
            archRow("Full (4 agents)", a.full12, a.full12 ? `${a.full12.acceptable}/${a.full12.cases}` : "-", a.full12 ? irN(a.full12.model_calls_per_case) : "-")))),
        h("p", { class: "note" }, `Full system on all 36 cases: acceptable ${irN(f36.outcome_accuracy_acceptable)}, critical misses ${irN(f36.critical_misses)}, unsupported evidence ids ${irN(f36.unsupported_id_rate)}, canary leaks ${irN(f36.canary_leaks)}, ${irN(f36.model_calls_per_case)} model calls per case. Recommendation: single agent by default; multi-agent where isolation and auditability justify about 2.4× the calls.`),
        source(s.sources.agents, s.sources.comparison))),
    s.foundry ? card("Foundry Evaluations", dataClass("Foundry run"),
      h("div", { class: "grid cols-2" },
        fo ? h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, "Deterministic check (36 cases)"), h("th", { class: "n" }, "Foundry"), h("th", { class: "n" }, "Local"))),
          h("tbody", {}, Object.keys(fo.per_criterion).map((k) => h("tr", {}, h("td", {}, k.replace(/_/g, " ")),
            h("td", { class: "n" }, String(fo.per_criterion[k].passed)), h("td", { class: "n" }, String(fo.local[k].passed)))))))
          : null,
        h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, `Judge ${s.foundry.judge}`), h("th", { class: "n" }, "Orch."), h("th", { class: "n" }, "Behavior"), h("th", { class: "n" }, "Investig."), h("th", { class: "n" }, "Risk"))),
          h("tbody", {}, ["task_adherence", "intent_resolution", "groundedness", "tool_call_accuracy"].map((k) => h("tr", {},
            h("td", {}, k.replace(/_/g, " ")), ...["orchestrator", "behavior", "investigator", "risk"].map((ag) => h("td", { class: "n" }, q(ag, k))))))))),
      h("p", { class: "note" }, "Same-family judge, one run, 12 cases: indicative. The investigator's tool selection is the weakest area (documented, not tuned). An earlier run judged the harness's parsed output instead of the model's reply; it is kept on record."),
      source(s.sources.foundry || "-", s.sources.foundry_doc)) : null);
}

async function irGuardrailPanels(page) {
  const s = (await irInfo()).summary;
  const g = s.guardrails || [];
  const complied = (d) => Boolean(d.guilt_wording || d.instructions_echoed || d.followed_injection);
  const outcome = (p) => {
    if (p.app) return p.app.outcome;
    const d = p.direct || {};
    if (d.outcome === "blocked") return "blocked";
    if (d.outcome !== "passed") return d.outcome || "-";
    return complied(d) ? "UNSAFE: agent complied" : "agent did not comply";
  };
  const stop = (p) => {
    if (p.app) return (p.app.stopped_by || []).join(", ").replace(/_/g, " ");
    const d = p.direct || {};
    if (d.outcome === "blocked") return `Foundry blocked (${(d.flags || []).join(", ")})`;
    return d.outcome === "passed" ? `agent (Foundry did not flag the ${d.stage === "tool_output" ? "tool output" : "input"})` : "-";
  };
  page.append(
    irHeader("Insider risk guardrails (UC2)", "Application guardrails and uc2-insider-risk-guardrail on all four Foundry agents, probed live (GA1-GA15). Verdicts only; no probe text is stored."),
    card(`Live verification (${(s.guardrails_provenance || {}).date || "not run"})`, dataClass("Live run, recorded"),
      g.length
        ? h("div", { class: "table-wrap" }, h("table", {},
            h("thead", {}, h("tr", {}, h("th", {}, "Probe"), h("th", {}, "Path"), h("th", {}, "Outcome"), h("th", {}, "Stopped by"))),
            h("tbody", {}, g.map((p) => h("tr", {}, h("td", {}, h("b", {}, p.id), ` ${p.name}`), h("td", {}, p.path.replace(/_/g, " ")),
              h("td", {}, outcome(p)), h("td", {}, stop(p)))))))
        : h("p", { class: "placeholder" }, "No verification run recorded."),
      h("div", { class: "callout" }, h("b", {}, "Takeaway: "),
        "no probe produced an unsafe outcome, and Foundry's shields blocked none of them. The app scanner withheld trigger-word injections, the output filter withheld intent wording (and sent the case to review), "
        + "and the agents ignored the paraphrased lures; the rubric floor would have held regardless. The safety rests on the application boundary."),
      source(s.sources.guardrails, s.sources.guardrails_doc)));
}

async function irObservabilityPanels(page) {
  const s = (await irInfo()).summary;
  const o = s.observability || {};
  const pa = o.privacy_audit, t = o.telemetry || {};
  const kv = (obj) => Object.entries(obj || {}).map(([k, v]) => `${k.replace(/^review:/, "")} ${v}`).join(" · ") || "none";
  page.append(
    irHeader("Insider risk telemetry (UC2)", "uc2.* spans carry a case id, a salted pseudonym for the user, the anomaly band, agents, tools, reason codes and the outcome. Never a user id, repository, host, log text or agent reasoning."),
    h("div", { class: "grid cols-2" },
      card("Privacy audit", statusBadge(pa && pa.clean ? "Clean" : "Check", pa && pa.clean ? "good" : "bad"),
        pa ? h("p", { class: "lead" }, `${pa.spans_audited} spans audited against ${pa.sources_checked} sources, plus ${pa.identifier_checks} user-id / repository / destination / untrusted-text / canary checks: ${pa.leaks.length} leaks.`) : null,
        h("div", { class: "callout" }, h("b", {}, "Live canary (App Insights): "),
          "DataGuard's spans held the canary, user id, repositories and hosts 0 times. Foundry Agent Service's own telemetry records the agents' conversations (user id, repository and host names from the logs; never document text or withheld text). "
          + "Kept on for the synthetic demo; off or pseudonymised in production."),
        source(s.sources.observability, s.sources.observability_live)),
      card("Telemetry (replayed golden run)", dataClass("Recorded"),
        h("dl", { class: "kv" },
          h("dt", {}, "Cases"), h("dd", {}, String(t.cases ?? "-")),
          h("dt", {}, "Outcomes"), h("dd", {}, kv(t.outcomes)),
          h("dt", {}, "Anomaly bands"), h("dd", {}, kv(t.anomaly_bands)),
          h("dt", {}, "Review reasons"), h("dd", {}, kv(t.review_reasons)),
          h("dt", {}, "Agent stops"), h("dd", {}, kv(t.agent_stop_reasons)),
          h("dt", {}, "Planner turns"), h("dd", {}, `${t.planner_turns ?? "-"} (${t.planner_turns_replayed ?? "-"} replayed)`),
          h("dt", {}, "Guardrail events"), h("dd", {}, kv(t.guardrail_events))))));
}

PAGES.insider = { title: "Insider Risk Investigation", render: renderInsider };
JUMP_TARGETS.push(["Insider Risk", ".uc2-head"]);
extendPage("evaluations", irEvaluationPanels, "UC2");
extendPage("rai", irGuardrailPanels, "UC2");
extendPage("observability", irObservabilityPanels, "UC2");
