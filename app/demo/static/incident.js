"use strict";
// Incident Investigation (UC5) page, plus UC5 panels on the shared Evaluations, Guardrails and Observability
// pages. Loaded after app.js, policy.js, dlp.js, insider.js and access.js; reuses their helpers (h, card,
// dataClass, source, statusBadge, getJSON, applyMode, levelChip, PAGES, extendPage, JUMP_TARGETS, route).
// Every global is prefixed `ic`/`IC_` (scripts share one scope). DOM is built with textContent only, never
// innerHTML from data. Every value comes from the demo server: the REAL UC5 pipeline (REPLAY by default) or
// the committed docs/uc5/results files.

const icState = { info: null, cid: "INC-001", backend: "chat-completions", last: null, busy: false, error: null, mode: null };

const IC_SEV_KIND = { LOW: "good", MEDIUM: "warn", HIGH: "bad", CRITICAL: "bad" };
const IC_CLAIM = { OBSERVED_FACT: "observed fact", DETERMINISTIC_FINDING: "deterministic finding", POLICY_REQUIREMENT: "policy requirement",
  AGENT_INFERENCE: "agent inference", UNKNOWN_OR_GAP: "unknown / gap" };
const IC_STATUS = { supported: ["supported", "good"], relabelled: ["relabelled as inference", "warn"], unsupported: ["excluded: unsupported", "bad"],
  withheld: ["withheld: intent or action claim", "bad"] };
const IC_EFFECT = {
  agreed: "The agent agreed with the deterministic floor.",
  raised_adopted: "The agent was more concerned: its higher severity was adopted, and a person reviews it.",
  lower_ignored: "The agent was one level lower: ignored (it cannot lower the floor).",
  disagreement_logged: "The agent was much lower: ignored, and the disagreement is shown to the analyst.",
  agent_failed: "The agent produced no valid report: the deterministic result stands, reviewed by a person.",
  not_run: "The agent did not run.",
};
const IC_GROUPS = [["data", "Data sensitivity (UC4)"], ["behaviour", "Behavioural context (UC2)"], ["access", "Access context (UC3 engine)"],
  ["dlp", "DLP alerts + destination (UC1 catalogue)"], ["policy", "Policy evidence (UC6)"], ["approvals", "Approvals"], ["identity", "Identity"],
  ["logs", "Security logs"]];

async function icPost(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const out = await res.json();
  if (!res.ok) throw new Error(out.error || `HTTP ${res.status}`);
  return out;
}
async function icInfo() {
  if (!icState.info) {
    const env = await getJSON("/api/incident");
    applyMode(env);
    icState.info = env.data;
    icState.mode = env.mode_label;
  }
  return icState.info;
}
function icRerender() { return location.hash === "#incident" ? route() : Promise.resolve(); }
const icClaim = (t) => h("span", { class: "chip", title: "What kind of statement this is" }, IC_CLAIM[t] || t);
const icSev = (s) => statusBadge(s, IC_SEV_KIND[s] || "warn");

// ---- the page --------------------------------------------------------------------------------
async function renderIncident(page) {
  const info = await icInfo();
  page.append(
    h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, "Incident Investigation"),
        h("p", { class: "lead" },
          "AI investigates and correlates evidence. Deterministic controls validate security facts. Humans own consequential actions. "
          + "One bounded agent pulls evidence from UC1 to UC6 through ten read-only tools; a deterministic timeline and correlation engine "
          + "link it; a versioned harness sets the severity floor from the facts; an analyst decides. Nothing here disables, revokes, deletes or notifies.")),
      dataClass(icState.mode === "LIVE" ? "Live pipeline" : "Replay: recorded pipeline")),
    h("div", { class: "grid cols-2" }, icQueueCard(info), icFlowCard()),
    h("div", { id: "ic-result" }),
    h("div", { id: "ic-queue" }),
  );
  icDrawResult();
  icLoadQueue();
}

function icQueueCard(info) {
  const list = h("div", { class: "examples" }, info.examples.map((e) => {
    const b = h("button", { class: `example${icState.cid === e.id ? " active" : ""}`, type: "button" },
      h("span", { class: "t" }, h("span", {}, e.title), h("span", { class: "chip" }, e.id)),
      h("span", { class: "d" }, e.what + (e.foundry ? " · also recorded through the Foundry agent" : "")));
    b.addEventListener("click", () => { icState.cid = e.id; icState.last = null; icState.error = null; if (!e.foundry) icState.backend = "chat-completions"; icRerender(); });
    return b;
  }));
  const sel = info.examples.find((e) => e.id === icState.cid);
  const backend = h("select", { "aria-label": "Agent backend" },
    h("option", { value: "chat-completions" }, "App agent (chat-completions planner)"),
    h("option", { value: "foundry-service" }, "Foundry agent: dataguard-incident-investigator"));
  backend.value = icState.backend;
  backend.disabled = !(sel && sel.foundry);
  backend.addEventListener("change", () => { icState.backend = backend.value; });
  const go = h("button", { class: "btn", type: "button" }, icState.busy ? "Investigating…" : "Investigate");
  go.disabled = icState.busy;
  go.addEventListener("click", icRun);
  return card("Incident queue", dataClass("Synthetic alerts"), list, sel ? icIncidentView(sel.incident) : null, h("div", { class: "btn-row" }, backend, go));
}

function icIncidentView(i) {
  return h("dl", { class: "kv" },
    h("dt", {}, "Alert"), h("dd", {}, `${i.trigger.summary} (${i.trigger.type.replace(/_/g, " ")}, ${i.trigger.time.replace("T", " ")})`),
    h("dt", {}, "Subject"), h("dd", {}, h("code", {}, i.subject), " · alias; the agent never sees the user id"),
    h("dt", {}, "Case files"), h("dd", {}, i.files.map((f) => `${f.handle} (${f.resource_id.replace(/_/g, " ")})`).join(", ")),
    i.faults.length ? h("dt", {}, "Fault") : null, i.faults.length ? h("dd", {}, statusBadge(`SIMULATED: ${i.faults.join(", ")}`, "warn")) : null,
    h("dt", {}, "Agent starts with"), h("dd", {}, "only this packet: it must retrieve every piece of evidence with a tool"));
}

function icFlowCard() {
  const steps = ["Alert", "Minimal case packet", "Agent (10 read-only tools)", "Timeline + correlation", "Severity harness", "Analyst"];
  return card("How an incident is investigated", dataClass("Architecture"),
    h("div", { class: "loop" }, steps.flatMap((x, i) => [i ? h("span", { class: "arrow" }, "→") : null, h("span", { class: `node${i === 4 ? " hot" : ""}` }, x)])),
    h("div", { class: "callout" }, h("b", {}, "Decision boundary: "),
      "after the agent investigates, the harness gathers the authoritative facts itself (UC4 sensitivity, UC1 destination class, UC2 behaviour, "
      + "UC3 access, UC6 policy) and sets a LOW, MEDIUM or HIGH floor. The agent can raise concern; it cannot lower a floor. CRITICAL "
      + "(Sev 1, confirmed loss) is set only by an analyst. Every claim must resolve to evidence or it is excluded."));
}

async function icRun() {
  if (icState.busy) return;
  icState.busy = true; icState.error = null; icRerender();
  try {
    const env = await icPost("/api/incident/investigate", { case_id: icState.cid, backend: icState.backend });
    applyMode(env);
    icState.last = env.data;
  } catch (err) {
    icState.error = err.message;
  } finally {
    icState.busy = false;
    await icRerender();
    const el = document.getElementById("ic-result");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

// ---- result ----------------------------------------------------------------------------------
function icDrawResult() {
  const el = document.getElementById("ic-result");
  if (!el) return;
  if (icState.error) { el.replaceChildren(card("Investigation", statusBadge("Error", "bad"), h("p", {}, icState.error))); return; }
  const r = icState.last;
  if (!r || r.case_id !== icState.cid) {
    el.replaceChildren(card("Investigation", dataClass("Waiting"), h("p", { class: "placeholder" }, "Pick an incident (start with the flagship) and press Investigate.")));
    return;
  }
  el.replaceChildren(
    h("div", { class: "grid cols-2" }, icDecisionCard(r), icReportCard(r)),
    icTimelineCard(r), icCorrelationCard(r), icEvidenceCard(r), icClaimsCard(r), icTraceCard(r));
}

function icDecisionCard(r) {
  const d = r.decision;
  return card("Severity and review", dataClass("Deterministic harness · authoritative"),
    h("div", { class: "kv" },
      h("dt", {}, "Severity"), h("dd", {}, icSev(d.severity), ` floor ${d.deterministic_severity} · rule `, h("code", {}, d.rule)),
      h("dt", {}, "Incident status"), h("dd", {}, d.incident_status.replace(/_/g, " ").toLowerCase()),
      h("dt", {}, "Analyst review"), h("dd", {}, statusBadge(d.review_status, d.review_status === "REQUIRED" ? "warn" : "good"),
        d.review_reasons.length ? ` ${d.review_reasons.map((x) => x.replace(/_/g, " ")).join(", ")}` : ""),
      h("dt", {}, "Agent"), h("dd", {}, d.agent_recommendation ? icSev(d.agent_recommendation) : "-", " ", IC_EFFECT[d.agent_effect] || d.agent_effect),
      h("dt", {}, "Remediation"), h("dd", {}, "none executed (recommendations only)"),
      h("dt", {}, "Rubric"), h("dd", {}, `v${d.rubric_version}`)),
    d.potential_sev1 ? h("div", { class: "callout" }, h("b", {}, "Potential Severity 1: "),
      "the evidence pattern could meet POL-IR §3 Severity 1 (confirmed loss of Restricted information). Only an analyst can confirm it and set CRITICAL.") : null);
}

function icReportCard(r) {
  const v = r.validation;
  return card("Agent's report", dataClass("AI investigation · not authoritative"),
    h("p", {}, r.executive_summary || "The agent produced no valid report; the deterministic result stands."),
    r.next_steps.length ? h("div", {}, h("b", {}, "Recommended next steps"), h("ul", { class: "items" }, r.next_steps.map((x) => h("li", {}, h("span", {}, "→"), h("span", {}, x))))) : null,
    h("p", { class: "note" }, `${v.total} claims: ${v.supported} supported, ${v.relabelled} relabelled, ${v.unsupported} excluded as unsupported, ${v.withheld} withheld. `
      + "Claims are checked against the evidence the agent actually retrieved."));
}

function icTimelineCard(r) {
  const rows = r.timeline.map((e) => h("tr", {},
    h("td", { class: "n" }, `${e.time.replace("T", " ")}${e.precision === "hour" ? " (hour)" : ""}`),
    h("td", {}, e.event_type.replace(/_/g, " ")), h("td", {}, e.summary), h("td", {}, e.source.replace(/_/g, " ")),
    h("td", {}, h("code", {}, e.evidence_id)), h("td", {}, e.retrieved_by_agent ? "✓" : "-"),
    h("td", {}, e.order_uncertain_with.length ? statusBadge("order uncertain", "warn") : "")));
  return card("Timeline", dataClass("Deterministic · observed facts"),
    h("p", { class: "note" }, "Ordered by authoritative timestamps; times are never changed. Events in the same hour where one has no minute are marked order-uncertain rather than given an invented order."),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ...["Time", "Event", "Summary", "Source", "Evidence", "Agent retrieved", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, rows))));
}

function icCorrelationCard(r) {
  const col = (kind, title) => {
    const xs = r.correlations.filter((c) => c.kind === kind);
    return h("div", {}, h("h3", {}, `${title} (${xs.length})`), xs.length
      ? h("ul", { class: "items" }, xs.map((c) => h("li", {}, h("code", {}, c.rule), h("span", {}, `${c.summary} [${c.evidence_ids.join(", ")}]`))))
      : h("p", { class: "placeholder" }, "none"));
  };
  return card("Evidence correlation", dataClass("Deterministic rules v1"),
    col("conflict", "Conflicts"), col("gap", "Unknown / gaps"), col("finding", "Linked evidence"));
}

function icEvidenceCard(r) {
  return card("Evidence by source", dataClass("UC1-UC6 capabilities · authoritative"),
    h("div", { class: "grid cols-2" }, IC_GROUPS.filter(([k]) => r.evidence[k]).map(([k, title]) => h("div", {},
      h("h3", {}, title),
      h("ul", { class: "items" }, r.evidence[k].map((e) => h("li", {}, icClaim(e.claim_type),
        h("span", {}, `${e.summary} `, h("code", {}, e.evidence_id), e.retrieved_by_agent ? "" : " · not retrieved by the agent"))))))));
}

function icClaimsCard(r) {
  if (!r.claims.length) return card("Report claims", dataClass("Validated"), h("p", { class: "placeholder" }, "No claims (the agent did not return a valid report)."));
  return card("Report claims", dataClass("Validated against retrieved evidence"),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ...["Topic", "Type", "Claim", "Evidence", "Status"].map((x) => h("th", {}, x)))),
      h("tbody", {}, r.claims.map((c) => {
        const [label, kind] = IC_STATUS[c.status] || [c.status, "warn"];
        return h("tr", {}, h("td", {}, c.topic), h("td", {}, icClaim(c.claim_type)), h("td", {}, c.text),
          h("td", {}, c.evidence_ids.join(", ") || "-"), h("td", {}, statusBadge(label, kind)));
      })))));
}

function icTraceCard(r) {
  const a = r.agent;
  if (!a) return card("Agent trace", dataClass("Trace"), h("p", { class: "placeholder" }, "The agent did not run."));
  return card("Agent trace", dataClass(r.backend === "foundry-service" ? "Foundry agent" : "App agent"),
    h("dl", { class: "kv" },
      h("dt", {}, "Stopped"), h("dd", {}, a.stopped_reason.replace(/_/g, " ")),
      h("dt", {}, "Calls"), h("dd", {}, `${a.model_calls} model · ${a.tool_calls} tool · ${a.turns} turns`),
      h("dt", {}, "Tokens"), h("dd", {}, a.tokens_in ? `${a.tokens_in} in · ${a.tokens_out} out` : "n/a (offline)"),
      h("dt", {}, "Refused (out of scope)"), h("dd", {}, a.refusals.length ? a.refusals.join(", ") : "none"),
      h("dt", {}, "Review requested"), h("dd", {}, a.review_requests.length ? a.review_requests.join(", ").replace(/_/g, " ") : "no")),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ...["#", "Tool", "Result", "Evidence ids"].map((x) => h("th", {}, x)))),
      h("tbody", {}, a.steps.map((s) => h("tr", {}, h("td", { class: "n" }, String(s.step)), h("td", {}, h("code", {}, s.tool)),
        h("td", {}, s.ok ? "ok" : statusBadge(s.error || "error", "warn")),
        h("td", {}, s.evidence_ids.length ? `${s.evidence_ids.slice(0, 5).join(", ")}${s.evidence_ids.length > 5 ? ` +${s.evidence_ids.length - 5}` : ""}` : "-")))))),
    h("p", { class: "note" }, "No tool can disable an account, revoke access, delete or quarantine anything, or notify anyone."));
}

// ---- analyst review (demo-only state; nothing is executed) -------------------------------------
async function icLoadQueue() {
  const el = document.getElementById("ic-queue");
  if (!el) return;
  let q;
  try { q = (await getJSON("/api/incident/review")).data; } catch (err) { el.replaceChildren(); return; }
  const rows = q.pending.map((p) => {
    const note = h("input", { type: "text", maxlength: "500", placeholder: "Note (optional)", "aria-label": `Note for ${p.case_id}` });
    const sev = h("select", { "aria-label": `Severity for ${p.case_id}` }, ["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((x) => h("option", { value: x }, x)));
    sev.value = p.severity;
    const act = (action, label) => {
      const b = h("button", { class: "btn btn-small", type: "button" }, label);
      b.addEventListener("click", async () => {
        b.disabled = true;
        const body = { key: p.key, action, note: note.value };
        if (action === "MODIFY") body.severity = sev.value;
        try { await icPost("/api/incident/review/decide", body); } catch (err) { b.title = err.message; b.disabled = false; return; }
        icLoadQueue();
      });
      return b;
    };
    return h("tr", {}, h("td", {}, p.at), h("td", {}, h("b", {}, p.case_id)), h("td", {}, p.backend === "foundry-service" ? "Foundry" : "app"),
      h("td", {}, icSev(p.severity), p.potential_sev1 ? " potential Sev 1" : ""), h("td", {}, p.reasons.map((x) => x.replace(/_/g, " ")).join(", ")),
      h("td", {}, note, h("div", { class: "btn-row" }, act("CONFIRM", "Confirm"), act("REJECT", "Reject"), sev, act("MODIFY", "Set severity"),
        act("MORE_INVESTIGATION", "More investigation"))));
  });
  el.replaceChildren(card("Analyst review (this session)", dataClass("Demo-only state · nothing is executed"),
    q.pending.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ...["Time", "Case", "Agent", "System severity", "Why review", "Decision"].map((x) => h("th", {}, x)))),
      h("tbody", {}, rows))) : h("p", { class: "placeholder" }, "Nothing is waiting for an analyst in this session."),
    q.decisions.length ? h("ul", { class: "items" }, q.decisions.map((d) => h("li", {}, h("span", {}, "✓"),
      h("span", {}, `${d.ts.slice(11)} ${d.case_id}: ${d.action.replace(/_/g, " ")}${d.severity ? ` → ${d.severity}` : ""}${d.note ? ` "${d.note}"` : ""} · remediation executed: no`)))) : null,
    h("p", { class: "note" }, `Decisions are written to ${q.stored_at}, marked simulated, and never used as evaluation labels. CRITICAL can only be set here, by the analyst.`)));
}

// ---- UC5 panels on the shared pages --------------------------------------------------------------
function icHeader(title, text) {
  return h("div", { class: "page-head uc5-head" }, h("div", {}, h("h1", {}, title), h("p", { class: "lead" }, text)), dataClass("Recorded UC5 results"));
}

async function icEvaluationPanels(page) {
  const s = (await icInfo()).summary, e = s.eval;
  if (!e) return;
  const rows = [["Severity acceptable", e.severity_acceptable], ["Review correct", e.review_correct], ["Severity floor violations", e.severity_floor_violations],
    ["Evidence completeness", e.evidence_completeness], ["Timeline order correct", e.timeline_order_correct], ["Correlations / gaps / conflicts found", e.correlations_gaps_conflicts_found],
    ["Unsupported claim rate", e.unsupported_claim_rate], ["Policy grounding", e.policy_grounding], ["Tool selection", e.tool_selection],
    ["Case-boundary violations", e.case_boundary_violations], ["Schema compliance", e.schema_compliance],
    ["Model calls · tool calls per incident", `${e.model_calls_per_case} · ${e.tool_calls_per_case}`], ["Tokens per incident", e.tokens_per_case], ["Cost", e.cost_usd]];
  const j = s.foundry_judges || {};
  page.append(icHeader("Incident Investigation (UC5)", "16 frozen golden incidents through one bounded agent, the correlation engine and the severity harness. Code checks for facts; Foundry judges for semantics."),
    card(`Golden set (${s.eval_verification})`, dataClass("Recorded"),
      h("div", { class: "table-wrap" }, h("table", {}, h("tbody", {}, rows.map(([k, v]) => h("tr", {}, h("td", {}, k), h("td", { class: "n" }, String(v))))))),
      s.foundry4 ? h("p", { class: "note" }, `Through the Foundry agent (4 demo incidents): severity ${s.foundry4.severity_acceptable}, review ${s.foundry4.review_correct}.`) : null,
      Object.keys(j).length ? h("p", { class: "note" }, "Foundry judges: " + Object.entries(j).map(([k, v]) => `${k.replace(/_/g, " ")} ${v.passed}/${v.passed + v.failed}`).join(" · ")
        + ". Foundry's 13 string checks match the local counts.") : null,
      source(s.sources.eval || "-", s.sources.foundry_evals || "")));
}

async function icGuardrailPanels(page) {
  const s = (await icInfo()).summary;
  if (!s.guardrails.length) return;
  page.append(icHeader("Incident investigation guardrails (UC5)", "Live attacks through the app and straight to the Foundry agent. Verdicts only."),
    card("Live probes", dataClass("Live run, recorded"),
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Probe"), h("th", {}, "Severity (without attack)"), h("th", {}, "Stopped by"), h("th", {}, "Direct to Foundry"))),
        h("tbody", {}, s.guardrails.map((p) => h("tr", {}, h("td", {}, h("b", {}, p.id), ` ${p.name}`),
          h("td", {}, p.severity ? `${p.severity} (${p.base})` : "-"), h("td", {}, (p.stopped_by || []).join(", ").replace(/_/g, " ")), h("td", {}, p.direct || "-")))))),
      source(s.sources.guardrails)));
}

async function icObservabilityPanels(page) {
  const s = (await icInfo()).summary, o = s.observability || {}, pa = o.privacy_audit, t = o.telemetry || {};
  if (!pa) return;
  page.append(icHeader("Incident investigation telemetry (UC5)", "uc5.* spans carry a case id, a salted pseudonym, codes, counts and versions. Never log or document text, justifications, hosts or tool arguments."),
    h("div", { class: "grid cols-2" },
      card("Privacy audit", statusBadge(pa.clean ? "Clean" : "Check", pa.clean ? "good" : "bad"),
        h("p", { class: "lead" }, `${pa.spans_audited} spans audited, ${pa.checks} checks: ${pa.leaks.length} leaks, ${pa.unlisted_keys.length} unlisted keys.`),
        source(s.sources.observability)),
      card("Telemetry", dataClass("Recorded"), h("dl", { class: "kv" },
        h("dt", {}, "Cases"), h("dd", {}, String(t.cases ?? "-")),
        h("dt", {}, "Severity"), h("dd", {}, Object.entries(t.severity || {}).map(([k, v]) => `${k} ${v}`).join(" · ")),
        h("dt", {}, "Review required"), h("dd", {}, String(t.review_required ?? "-")),
        h("dt", {}, "Tool errors"), h("dd", {}, Object.entries(t.tool_errors || {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "none")))));
}

PAGES.incident = { title: "Incident Investigation", render: renderIncident };
JUMP_TARGETS.push(["Incident Investigation", ".uc5-head"]);
extendPage("evaluations", icEvaluationPanels, "UC5");
extendPage("rai", icGuardrailPanels, "UC5");
extendPage("observability", icObservabilityPanels, "UC5");
