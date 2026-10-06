"use strict";
// Access Governance (UC3) page, plus UC3 panels on the shared Evaluations, Guardrails and Observability
// pages. Loaded after app.js, policy.js, dlp.js and insider.js; reuses their helpers (h, s, card,
// dataClass, source, statusBadge, getJSON, applyMode, fmt, levelChip, PAGES, extendPage, JUMP_TARGETS).
// Every global is prefixed `ag`/`AG_` (scripts share one scope). DOM is built with textContent only,
// never innerHTML from data. Every value comes from the demo server: the REAL UC3 pipeline (REPLAY by
// default) or the committed docs/uc3/results files.

const agState = { info: null, rid: "AR-002", backend: "chat-completions", last: null, busy: false, error: null, mode: null };

const AG_TEXT = {
  RECOMMEND_APPROVE: "Approve as requested. Low risk; no narrower access is needed.",
  RECOMMEND_LIMITED_TIME_BOUND_ACCESS: "Grant a narrower, time-bound version instead of what was asked.",
  HUMAN_REVIEW: "A person must decide: evidence is missing or conflicting, or a control requires an approver.",
  RECOMMEND_REJECT: "Do not grant: a control forbids it, or existing access already meets the need.",
};
const AG_KIND = { RECOMMEND_APPROVE: "l-PUBLIC", RECOMMEND_LIMITED_TIME_BOUND_ACCESS: "l-none", HUMAN_REVIEW: "l-none", RECOMMEND_REJECT: "failure" };
const AG_SHORT = { RECOMMEND_APPROVE: "APPROVE", RECOMMEND_LIMITED_TIME_BOUND_ACCESS: "LIMITED, TIME-BOUND", HUMAN_REVIEW: "HUMAN REVIEW", RECOMMEND_REJECT: "REJECT" };
const AG_CLAIM = { OBSERVED_FACT: "observed fact", DETERMINISTIC_CONTROL_RESULT: "deterministic control", POLICY_REQUIREMENT: "policy requirement",
  AGENT_INFERENCE: "agent inference", AGENT_RECOMMENDATION: "agent recommendation" };
const AG_EFFECT = {
  agreed: "The agent agreed with the deterministic result.",
  raised_adopted: "The agent was stricter: its concern was adopted and routed to a person.",
  lower_ignored: "The agent was one level more lenient: ignored (it cannot lower the result).",
  disagreement_logged: "The agent was much more lenient: ignored, and the disagreement is shown to the approver.",
  agent_failed: "The agent produced no valid recommendation: the deterministic result stands, reviewed by a person.",
  not_run: "The agent did not run.",
};

async function agPost(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const out = await res.json();
  if (!res.ok) throw new Error(out.error || `HTTP ${res.status}`);
  return out;
}
async function agInfo() {
  if (!agState.info) {
    const env = await getJSON("/api/access");
    applyMode(env);
    agState.info = env.data;
    agState.mode = env.mode_label;
  }
  return agState.info;
}
function agRerender() { return location.hash === "#access" ? route() : Promise.resolve(); }
const agClaim = (t) => h("span", { class: "chip", title: "Where this statement comes from" }, AG_CLAIM[t] || t);
const agDays = (d) => (d < 1 ? `${Math.round(d * 24)} hours` : `${Number(d).toFixed(d % 1 ? 1 : 0)} days`);

// ---- the page --------------------------------------------------------------------------------
async function renderAccess(page) {
  const info = await agInfo();
  page.append(
    h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, "Access Governance"),
        h("p", { class: "lead" },
          "AI reasons about access. Deterministic controls enforce authorization. Humans approve high-impact access. "
          + "One bounded agent gathers identity, access-graph, UC4 sensitivity and UC6 policy evidence and recommends; "
          + "a versioned harness recomputes the facts itself and decides. Nothing here grants, changes or revokes access.")),
      dataClass(agState.mode === "LIVE" ? "Live pipeline" : "Replay: recorded pipeline")),
    h("div", { class: "grid cols-2" }, agRequestCard(info), agFlowCard()),
    h("div", { id: "ag-result" }),
    h("div", { id: "ag-queue" }),
  );
  agDrawResult();
  agLoadQueue();
}

function agRequestCard(info) {
  const list = h("div", { class: "examples" }, info.examples.map((e) => {
    const b = h("button", { class: `example${agState.rid === e.id ? " active" : ""}`, type: "button" },
      h("span", { class: "t" }, h("span", {}, e.title), h("span", { class: "chip" }, e.id)),
      h("span", { class: "d" }, e.what + (e.foundry ? " · also recorded through the Foundry agent" : "")));
    b.addEventListener("click", () => { agState.rid = e.id; agState.last = null; agState.error = null; if (!e.foundry) agState.backend = "chat-completions"; agRerender(); });
    return b;
  }));
  const sel = info.examples.find((e) => e.id === agState.rid);
  const backend = h("select", { "aria-label": "Agent backend" },
    h("option", { value: "chat-completions" }, "App agent (chat-completions planner)"),
    h("option", { value: "foundry-service" }, "Foundry agent: dataguard-access-governance"));
  backend.value = agState.backend;
  backend.disabled = !(sel && sel.foundry);
  backend.addEventListener("change", () => { agState.backend = backend.value; });
  const go = h("button", { class: "btn", type: "button" }, agState.busy ? "Reviewing…" : "Review request");
  go.disabled = agState.busy;
  go.addEventListener("click", agRun);
  return card("Access request", dataClass("Synthetic request"), list, sel ? agRequestView(sel.request) : null,
    h("div", { class: "btn-row" }, backend, go));
}

function agRequestView(r) {
  return h("dl", { class: "kv" },
    h("dt", {}, "Requester"), h("dd", {}, `${r.user_id} · ${r.role_id.replace(/_/g, " ")}`),
    h("dt", {}, "Resource"), h("dd", {}, `${r.resource_name} (${r.environment})`),
    h("dt", {}, "Entitlement"), h("dd", {}, h("code", {}, r.entitlement_id), ` · ${r.privilege}${r.scope !== "n/a" ? `, ${r.scope} scope` : ""}`),
    h("dt", {}, "Purpose"), h("dd", {}, r.purpose_category ? r.purpose_category.replace(/_/g, " ") : statusBadge("missing", "warn")),
    h("dt", {}, "Duration"), h("dd", {}, `${r.duration_days} days`, r.project_id ? ` · project ${r.project_id.replace(/_/g, " ")}` : ""),
    h("dt", {}, "Justification"), h("dd", {}, h("span", { class: "chip" }, "untrusted"), " ", r.justification || "(none)"),
    r.simulate_fault ? h("dt", {}, "Fault") : null, r.simulate_fault ? h("dd", {}, statusBadge(`SIMULATED: ${r.simulate_fault}`, "warn")) : null);
}

function agFlowCard() {
  const steps = ["Request", "Prechecks + facts", "Agent (12 read-only tools)", "Recommendation", "Authorization harness", "Approver"];
  return card("How a request is decided", dataClass("Architecture"),
    h("div", { class: "loop" }, steps.flatMap((x, i) => [i ? h("span", { class: "arrow" }, "→") : null, h("span", { class: `node${i === 4 ? " hot" : ""}` }, x)])),
    h("div", { class: "callout" }, h("b", {}, "Authorization boundary: "),
      "identity, access paths, UC4 sensitivity, UC6 policy, least-privilege and SoD results come from deterministic services, computed before the agent runs. "
      + "The harness decides from those facts, never from the agent's tool calls. The agent can raise a concern; it cannot lower a floor. "
      + "No tool can grant, revoke or change a group or role."));
}

async function agRun() {
  if (agState.busy) return;
  agState.busy = true; agState.error = null; agRerender();
  try {
    const env = await agPost("/api/access/investigate", { request_id: agState.rid, backend: agState.backend });
    applyMode(env);
    agState.last = env.data;
  } catch (err) {
    agState.error = err.message;
  } finally {
    agState.busy = false;
    await agRerender();
    const el = document.getElementById("ag-result");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

// ---- result ----------------------------------------------------------------------------------
function agDrawResult() {
  const el = document.getElementById("ag-result");
  if (!el) return;
  el.replaceChildren();
  if (agState.error) { el.append(h("div", { class: "card error" }, `Could not review: ${agState.error}`)); return; }
  const r = agState.last;
  if (!r) { el.append(h("div", { class: "card empty-state" }, "Pick a request (start with the flagship) and press Review request.")); return; }
  el.append(agVerdict(r));
  el.append(h("div", { class: "grid cols-2" }, agAiCard(r), agControlCard(r)));
  el.append(agGraphCard(r));
  el.append(h("div", { class: "grid cols-2" }, agAccessCard(r), agContextCard(r)));
  el.append(agChecksCard(r));
  el.append(agTraceCard(r));
}

function agVerdict(r) {
  const d = r.decision, t = r.totals;
  return h("div", { class: `verdict ${AG_KIND[d.outcome] || "l-none"}` },
    h("div", { class: "lvl-big" }, AG_SHORT[d.outcome]),
    h("p", { class: "lead" }, AG_TEXT[d.outcome],
      d.alternative ? ` Alternative: ${d.alternative.entitlement_id} for ${agDays(d.alternative.duration_days)}.` : ""),
    h("div", { class: "badges" },
      statusBadge(agState.mode === "LIVE" ? "LIVE" : "REPLAY", agState.mode === "LIVE" ? "bad" : "muted"),
      statusBadge(r.backend === "foundry-service" ? "Foundry agent" : "App agent", "muted"),
      statusBadge(`agent: ${AG_SHORT[d.agent_recommendation] || "no recommendation"}`, "muted"),
      statusBadge(`harness: ${AG_SHORT[d.outcome]}`, d.outcome === "RECOMMEND_APPROVE" ? "good" : "warn"),
      d.hitl_required ? statusBadge("approver required", "warn") : statusBadge("no approver needed", "good"),
      statusBadge("provisioned: no", "muted"),
      statusBadge(`${t.model_calls} model ${t.model_calls === 1 ? "call" : "calls"} · ${t.tool_calls} tool ${t.tool_calls === 1 ? "call" : "calls"}`, "muted"),
      statusBadge(`rubric ${d.rubric_version}`, "muted"),
      statusBadge(`${Math.round(r.wall_ms)} ms`, "muted")));
}

function agAiCard(r) {
  const rec = r.recommendation, a = r.agent || {};
  if (!rec) {
    return card("AI reasoning", dataClass("Agent"),
      h("p", { class: "placeholder" }, `No valid recommendation (${a.stopped_reason || "not run"}). The deterministic result stands and goes to a person.`));
  }
  return card("AI reasoning", dataClass("Agent recommendation · not authoritative"),
    h("div", { class: "badges" }, h("b", {}, AG_SHORT[rec.recommended_outcome]), " ", agClaim("AGENT_RECOMMENDATION"), statusBadge(`confidence ${rec.confidence}`, "muted"),
      a.budget_finish ? statusBadge("answered at the tool budget", "warn") : null),
    rec.alternative ? h("p", {}, `Proposed alternative: ${rec.alternative.entitlement_id} for ${agDays(rec.alternative.duration_days)}`) : null,
    h("p", {}, rec.rationale),
    rec.findings.length
      ? h("ul", { class: "items" }, rec.findings.map((f) => h("li", {}, h("span", {}, "•"), h("span", {}, f.text, " ", agClaim(f.claim_type), " ", h("span", { class: "chip" }, f.evidence_id)))))
      : null,
    rec.missing_evidence.length ? h("p", { class: "note" }, `Missing evidence (agent): ${rec.missing_evidence.join(", ")}`) : null,
    h("p", { class: "note" }, "Each finding cites an evidence id that a tool returned. The agent never sees another user's data or document text."));
}

function agControlCard(r) {
  const d = r.decision;
  const rule = (d.reason_codes.find((c) => c.startsWith("rule:")) || "").slice(5);
  const signals = d.reason_codes.filter((c) => c.startsWith("signal:")).map((c) => c.slice(7));
  return card("Deterministic control", dataClass("Authorization harness · authoritative"),
    h("div", { class: "badges" }, h("b", {}, AG_SHORT[d.outcome]), " ", agClaim("DETERMINISTIC_CONTROL_RESULT")),
    h("dl", { class: "kv" },
      h("dt", {}, "Rule"), h("dd", {}, h("code", {}, rule || "-")),
      h("dt", {}, "From facts alone"), h("dd", {}, AG_SHORT[d.deterministic_outcome]),
      h("dt", {}, "Agent effect"), h("dd", {}, AG_EFFECT[d.agent_effect] || d.agent_effect),
      h("dt", {}, "Alternative"), h("dd", {}, d.alternative ? `${d.alternative.entitlement_id} for ${agDays(d.alternative.duration_days)}` : "none"),
      h("dt", {}, "Signals"), h("dd", {}, signals.length ? signals.map((x) => h("span", { class: "chip" }, x)) : "none"),
      h("dt", {}, "Approver"), h("dd", {}, d.hitl_required ? `required: ${d.hitl_reasons.join(", ")}` : "not required (low-risk read)")),
    h("p", { class: "note" }, "The harness recomputes identity, graph, UC4, UC6, least-privilege and SoD facts itself. An agent that skipped a tool cannot change this result."));
}

// The access graph: existing paths to the requested resource and its family (solid), the request
// (dashed) and, when the harness proposes one, the least-privilege alternative (accent, dashed).
function agGraphCard(r) {
  const f = r.facts, req = r.request, d = r.decision;
  const lines = f.family_paths.map((p) => ({ path: p.path, kind: p.active ? (p.inherited ? `has today (${p.via})` : "has today (direct)") : "expired", cls: p.active ? "has" : "expired" }));
  lines.push({ path: [req.user_id, "REQUESTS", req.entitlement_id, "PERMITS", req.resource_id], kind: `requested: ${req.duration_days} days`, cls: "requested" });
  if (d.alternative) {
    lines.push({ path: [req.user_id, "ALTERNATIVE", d.alternative.entitlement_id, "PERMITS", f.alternative_resource],
      kind: `proposed: ${agDays(d.alternative.duration_days)}`, cls: "alternative" });
  }
  const w = 760, rowH = 72, top = 52;
  const svg = s("svg", { viewBox: `0 0 ${w} ${top + lines.length * rowH + 4}`, role: "img", "aria-label": "Access paths from the requester to the resource", class: "ag-graph" },
    s("defs", {}, s("marker", { id: "ag-arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "6", markerHeight: "6", orient: "auto-start-reverse" },
      s("path", { d: "M0 0L10 5L0 10z", fill: "var(--ink-3)" }))),
    s("text", { x: "12", y: "22", class: "ag-graph-title" }, "What the requester has today, what was requested, and the narrower alternative"));
  lines.forEach((ln, i) => {
    const nodes = ln.path.filter((_, j) => j % 2 === 0), rels = ln.path.filter((_, j) => j % 2 === 1);
    const n = nodes.length, nw = n >= 4 ? 150 : 200, gap = (w - 24 - n * nw) / Math.max(n - 1, 1), y = top + i * rowH;
    const fit = Math.floor((nw - 12) / 6.6);
    nodes.forEach((node, j) => {
      const x = 12 + j * (nw + gap);
      svg.append(s("rect", { x, y, width: nw, height: 34, rx: 8, class: `ag-node ag-${ln.cls}` }),
        s("text", { x: x + nw / 2, y: y + 21, "text-anchor": "middle", class: "ag-node-text" }, node.length > fit ? `${node.slice(0, fit - 1)}…` : node));
      if (j < n - 1) {
        const x1 = x + nw, x2 = x + nw + gap;
        svg.append(s("line", { x1, y1: y + 17, x2: x2 - 2, y2: y + 17, class: `ag-edge ag-${ln.cls}`, "marker-end": "url(#ag-arrow)" }),
          s("text", { x: (x1 + x2) / 2, y: y - 6, "text-anchor": "middle", class: "ag-edge-text" }, rels[j]));
      }
    });
    svg.append(s("text", { x: w - 12, y: y + 50, "text-anchor": "end", class: "ag-graph-note" }, ln.kind));
  });
  return card("Access graph", dataClass("Deterministic graph · observed fact"), svg,
    h("p", { class: "note" }, "Existing paths come from the typed access graph; the model never adds an edge. Dashed lines are the request and the proposed alternative, not access anyone holds."));
}

function agAccessCard(r) {
  const f = r.facts;
  const active = f.grants.filter((g) => g.active), expired = f.grants.filter((g) => !g.active);
  return card("Current access", dataClass("Observed fact"),
    f.identity ? h("dl", { class: "kv" },
      h("dt", {}, "Role"), h("dd", {}, `${f.identity.role_id.replace(/_/g, " ")} · ${f.identity.department}`, f.identity.moved_role ? " · moved role" : ""),
      h("dt", {}, "Groups / projects"), h("dd", {}, [...f.identity.groups, ...f.identity.projects].map((x) => h("span", { class: "chip" }, x))),
      h("dt", {}, "Usage"), h("dd", {}, f.usage ? `last used ${f.usage.last_used}, ${f.usage.uses_90d} uses in 90 days` : "never used (the requested entitlement)"),
      h("dt", {}, "Peers"), h("dd", {}, f.peer ? `${f.peer.holders} of ${f.peer.peers} ${f.peer.role_id.replace(/_/g, " ")} peers hold it` : "-", " ", h("span", { class: "chip" }, "context only")))
      : h("p", { class: "placeholder" }, "Identity unavailable: access context could not be established."),
    active.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Entitlement"), h("th", {}, "Via"), h("th", {}, "Expires"))),
      h("tbody", {}, active.map((g) => h("tr", {}, h("td", {}, h("code", {}, g.entitlement_id)), h("td", {}, `${g.via} ${g.via_id}`), h("td", {}, g.expires || "-")))))) : null,
    expired.length ? h("p", { class: "note" }, `Expired, not counted: ${expired.map((g) => g.entitlement_id).join(", ")}`) : null);
}

function agContextCard(r) {
  const f = r.facts, c = f.sensitivity;
  return card("Data and policy context", dataClass("UC4 + UC6"),
    h("dl", { class: "kv" },
      h("dt", {}, "Sensitivity (UC4)"), h("dd", {}, c ? [levelChip(c.level), " ", ...c.categories.map((x) => h("span", { class: "chip" }, x)), " ", agClaim("DETERMINISTIC_CONTROL_RESULT")] : statusBadge("unavailable", "warn"))),
    f.policy.length
      ? h("ul", { class: "items" }, f.policy.map((p) => h("li", {}, h("span", {}, "✓"), h("span", {}, h("b", {}, p.citation), " ", p.text, " ", agClaim("POLICY_REQUIREMENT")))))
      : h("p", { class: "placeholder" }, "No policy section was returned."),
    f.policy_missing.length ? h("div", { class: "callout" }, h("b", {}, "Policy evidence missing: "), f.policy_missing.join(", "), ". Nothing is invented; a person decides.") : null,
    h("p", { class: "note" }, "Sections come from UC6's frozen corpus, citation-checked. UC4 classified a recorded sample of the resource's data; document text never reaches the agent."));
}

function agChecksCard(r) {
  const f = r.facts;
  return card("Governance checks", dataClass("Least privilege + SoD · deterministic"),
    f.signals.length
      ? h("ul", { class: "items" }, f.signals.map((x) => h("li", {}, h("span", {}, x.contextual ? "○" : "⚑"),
          h("span", {}, h("code", {}, x.code), " ", x.detail, x.contextual ? [" ", h("span", { class: "chip" }, "context only")] : null))))
      : h("p", { class: "placeholder" }, f.failures.length ? "Not computed: a required service failed." : "No least-privilege or SoD finding."),
    f.failures.length ? h("p", { class: "note" }, `Failed: ${f.failures.join(", ")}`) : null,
    f.justification_flagged ? h("div", { class: "callout" }, h("b", {}, "Guardrail: "), "the justification contained instruction-like text and was withheld from the agent.") : null);
}

function agTraceCard(r) {
  const a = r.agent;
  if (!a) return card("Agent trace", dataClass("Trace"), h("p", { class: "placeholder" }, "The agent did not run."));
  return card("Agent trace", dataClass(r.backend === "foundry-service" ? "Foundry agent" : "App agent"),
    h("p", { class: "note" }, `${a.turns} turns · ${a.model_calls} model calls · ${a.tool_calls} tool calls${a.tokens_in ? ` · ${a.tokens_in} tokens in` : ""} · stopped: ${a.stopped_reason}`
      + (a.refusals.length ? ` · refused: ${a.refusals.join(", ")}` : "") + (a.action_claims_withheld ? ` · ${a.action_claims_withheld} action claim(s) withheld` : "")),
    h("ol", { class: "timeline" }, a.steps.map((st) => h("li", { class: st.ok ? "st-executed" : "st-failed" },
      h("span", { class: "dot" }, String(st.step)),
      h("div", { class: "body" }, h("div", { class: "head" }, h("b", {}, st.tool), statusBadge(st.ok ? "ok" : st.error, st.ok ? "good" : "bad")),
        st.evidence_ids && st.evidence_ids.length ? h("div", { class: "meta" }, `returned ${st.evidence_ids.slice(0, 6).join(", ")}${st.evidence_ids.length > 6 ? "…" : ""}`) : null)))));
}

// ---- approver decisions (demo-only state; nothing is provisioned) -------------------------------
async function agLoadQueue() {
  const el = document.getElementById("ag-queue");
  if (!el) return;
  let q;
  try { q = (await getJSON("/api/access/review")).data; } catch (err) { el.replaceChildren(); return; }
  const rows = q.pending.map((p) => {
    const note = h("input", { type: "text", maxlength: "500", placeholder: "Note (optional)", "aria-label": `Note for ${p.request_id}` });
    const days = h("input", { type: "number", min: "1", max: "365", step: "1", value: p.alternative ? Math.max(1, Math.round(p.alternative.duration_days)) : "7",
      "aria-label": `Modified duration for ${p.request_id}`, class: "ag-days" });
    const act = (action) => {
      const b = h("button", { class: "btn btn-small", type: "button" }, action === "MODIFY" ? "Modify (days)" : action[0] + action.slice(1).toLowerCase());
      b.addEventListener("click", async () => {
        b.disabled = true;
        const body = { key: p.key, action, note: note.value };
        if (action === "MODIFY") body.modified = { entitlement_id: p.alternative ? p.alternative.entitlement_id : null, duration_days: Number(days.value) };
        try { await agPost("/api/access/review/decide", body); } catch (err) { b.title = err.message; b.disabled = false; return; }
        agLoadQueue();
      });
      return b;
    };
    return h("tr", {}, h("td", {}, p.at), h("td", {}, h("b", {}, p.request_id)), h("td", {}, p.backend === "foundry-service" ? "Foundry" : "app"),
      h("td", {}, AG_SHORT[p.outcome]), h("td", {}, p.alternative ? `${p.alternative.entitlement_id}, ${agDays(p.alternative.duration_days)}` : "-"),
      h("td", {}, note, h("div", { class: "btn-row" }, act("APPROVE"), act("REJECT"), days, act("MODIFY"))));
  });
  el.replaceChildren(card("Approver decisions (this session)", dataClass("Demo-only state · nothing is provisioned"),
    q.pending.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Time"), h("th", {}, "Request"), h("th", {}, "Agent"), h("th", {}, "System outcome"), h("th", {}, "Alternative"), h("th", {}, "Decision"))),
      h("tbody", {}, rows))) : h("p", { class: "placeholder" }, "Nothing is waiting for an approver in this session."),
    q.decisions.length ? h("ul", { class: "items" }, q.decisions.map((d) => h("li", {}, h("span", {}, "✓"),
      h("span", {}, `${d.ts.slice(11)} ${d.request_id}: ${d.action}${d.modified ? ` (${d.modified.entitlement_id}, ${d.modified.duration_days} days)` : ""}${d.note ? ` "${d.note}"` : ""} · provisioned: no`)))) : null,
    h("p", { class: "note" }, `Decisions are written to ${q.stored_at}, marked simulated, and never used as evaluation labels.`)));
}

// ---- UC3 panels on the shared pages --------------------------------------------------------------
function agHeader(title, text) {
  return h("div", { class: "page-head uc3-head" }, h("div", {}, h("h1", {}, title), h("p", { class: "lead" }, text)), dataClass("Recorded UC3 results"));
}

async function agEvaluationPanels(page) {
  const s = (await agInfo()).summary, e = s.eval;
  if (!e) return;
  const rows = [["Acceptable outcomes", e.outcome_accuracy_acceptable], ["Unsafe approvals", e.unsafe_approvals], ["Safety floors lowered", e.floors_lowered],
    ["Least-privilege alternative correct", e.alternative_correct], ["SoD handled", e.sod_handled], ["Approver requirement correct", e.hitl_correct],
    ["Agent recommendation acceptable", e.agent_recommendation_acceptable], ["Evidence completeness", e.evidence_completeness],
    ["Unsupported evidence ids", e.unsupported_evidence_ids], ["Invalid policy citations", e.invalid_policy_citations],
    ["Write-tool attempts / provisioned", `${e.write_tool_attempts} / ${e.provisioned}`], ["Schema compliance", e.schema_compliance],
    ["Model calls · tool calls per request", `${e.model_calls_per_case} · ${e.tool_calls_per_case}`], ["Tokens per request", e.tokens_per_case], ["Cost", e.cost_usd]];
  page.append(agHeader("Access Governance (UC3)", "16 frozen golden requests through one bounded agent and the deterministic harness. Every metric is code-checked."),
    card(`Golden set (${s.eval_verification})`, dataClass("Recorded"),
      h("div", { class: "table-wrap" }, h("table", {}, h("tbody", {}, rows.map(([k, v]) => h("tr", {}, h("td", {}, k), h("td", { class: "n" }, String(v))))))),
      s.foundry4 ? h("p", { class: "note" }, `Through the Foundry agent (4 demo requests): acceptable ${s.foundry4.outcome_accuracy_acceptable}, unsafe approvals ${s.foundry4.unsafe_approvals}, agent acceptable ${s.foundry4.agent_recommendation_acceptable}.`) : null,
      source(s.sources.eval || "-", s.sources.foundry4 || "")));
}

async function agGuardrailPanels(page) {
  const s = (await agInfo()).summary;
  if (!s.guardrails.length) return;
  page.append(agHeader("Access governance guardrails (UC3)", "Adversarial requests against the application controls and the Foundry agent guardrail. Verdicts only."),
    card("Live probes", dataClass("Live run, recorded"),
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Probe"), h("th", {}, "Outcome"), h("th", {}, "Stopped by"))),
        h("tbody", {}, s.guardrails.map((p) => h("tr", {}, h("td", {}, h("b", {}, p.id), ` ${p.name}`), h("td", {}, p.outcome || "-"),
          h("td", {}, (p.stopped_by || []).join(", "))))))),
      source(s.sources.guardrails)));
}

async function agObservabilityPanels(page) {
  const s = (await agInfo()).summary, o = s.observability || {}, pa = o.privacy_audit, t = o.telemetry || {};
  if (!pa) return;
  page.append(agHeader("Access governance telemetry (UC3)", "uc3.* spans carry a request id, a salted pseudonym, entitlement and policy ids, signal and reason codes and outcomes. Never justifications, tool payloads or other users' data."),
    h("div", { class: "grid cols-2" },
      card("Privacy audit", statusBadge(pa.clean ? "Clean" : "Check", pa.clean ? "good" : "bad"),
        h("p", { class: "lead" }, `${pa.spans_audited} spans audited, ${pa.checks} checks for raw user ids, justification and policy text: ${pa.leaks.length} leaks, ${pa.unlisted_keys.length} unlisted keys.`),
        source(s.sources.observability)),
      card("Telemetry", dataClass("Recorded"), h("dl", { class: "kv" },
        h("dt", {}, "Requests"), h("dd", {}, String(t.requests ?? "-")),
        h("dt", {}, "Outcomes"), h("dd", {}, Object.entries(t.outcomes || {}).map(([k, v]) => `${AG_SHORT[k] || k} ${v}`).join(" · ")),
        h("dt", {}, "Approver required"), h("dd", {}, String(t.hitl_required ?? "-")),
        h("dt", {}, "Tool errors"), h("dd", {}, Object.entries(t.tool_errors || {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "none")))));
}

PAGES.access = { title: "Access Governance", render: renderAccess };
JUMP_TARGETS.push(["Access Governance", ".uc3-head"]);
extendPage("evaluations", agEvaluationPanels, "UC3");
extendPage("rai", agGuardrailPanels, "UC3");
extendPage("observability", agObservabilityPanels, "UC3");
