"use strict";
// DataGuard AI interview demo. Every value on screen comes from the local demo server, which reads
// committed artifacts or calls the existing service. DOM is built with textContent only (no
// innerHTML from data), and every panel shows its data class and source.

const PAGES = {
  overview: { title: "Overview", phase: 4 },
  classify: { title: "Classify", render: renderClassify },
  trace: { title: "Decision Trace", render: renderTrace },
  agent: { title: "Agent Triage", render: renderAgent },
  review: { title: "Human Review", render: renderReview },
  evaluations: { title: "Evaluations", render: renderEvaluations },
  rai: { title: "Responsible AI / Guardrails", phase: 4 },
  observability: { title: "Observability", phase: 4 },
};
const DEFAULT_PAGE = "classify";
const state = {
  status: null, metrics: null, examples: null, last: null, busy: false,
  input: { example: null, text: "", filename: "pasted.txt", llmOff: false, upload: null },
  agent: { info: null, results: {}, running: null, selected: null, error: null },
  review: { items: null, selected: null, error: null, storedAt: null },
};

// ---- tiny DOM helpers ------------------------------------------------------------------------
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const SVGNS = "http://www.w3.org/2000/svg";
function s(tag, attrs, ...children) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const fmt = (v, d = 3) => (v === null || v === undefined ? "not computed" : Number(v).toFixed(d));
const pct = (v) => (v === null || v === undefined ? "not computed" : `${(v * 100).toFixed(1)}%`);
const dataClass = (label) => h("span", { class: "dc", title: "What kind of data this panel shows" }, label);
const source = (...paths) => h("div", { class: "source" }, "Source: ", ...paths.flatMap((p, i) => [i ? ", " : "", h("code", {}, p)]));
const levelChip = (lvl) => h("span", { class: `lvl lvl-${lvl}` }, lvl);
function card(title, badge, ...body) {
  return h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, title), badge || null), ...body);
}
function statusBadge(text, kind) {
  return h("span", { class: `badge badge-${kind}` }, text);
}

// ---- data ------------------------------------------------------------------------------------
async function getJSON(path) {
  const res = await fetch(path, { cache: "no-store" });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

function applyMode(envelope) {
  // The badge text comes from the server's mode_label only, never from the page.
  const badge = document.getElementById("mode-badge");
  const label = envelope.mode_label === "LIVE" ? "LIVE" : "REPLAY";
  badge.textContent = label;
  badge.className = `badge ${label === "LIVE" ? "badge-live" : "badge-replay"}`;
  badge.title = label === "LIVE"
    ? "Live mode: classification and agent runs call real Foundry services"
    : "Replay mode: recorded LLM responses and the offline agent planner; no live model calls";
}

// ---- routing ---------------------------------------------------------------------------------
function currentPage() {
  const key = location.hash.replace(/^#/, "");
  return PAGES[key] ? key : DEFAULT_PAGE;
}
async function route() {
  const key = currentPage();
  document.querySelectorAll(".sidenav a").forEach((a) => a.classList.toggle("active", a.dataset.page === key));
  const page = document.getElementById("page");
  page.replaceChildren();
  const def = PAGES[key];
  document.title = `${def.title} · DataGuard AI`;
  if (!def.render) {
    page.append(
      h("div", { class: "page-head" }, h("div", {}, h("h1", {}, def.title),
        h("p", { class: "lead placeholder" }, `This page arrives in build phase ${def.phase}.`))),
    );
    return;
  }
  try {
    await def.render(page);
  } catch (err) {
    page.append(h("div", { class: "card error" }, `Could not load this page: ${err.message}`));
  }
}

// ---- Evaluations -----------------------------------------------------------------------------
async function renderEvaluations(page) {
  if (!state.metrics) {
    const env = await getJSON("/api/metrics");
    applyMode(env);
    state.metrics = env.data;
  }
  const m = state.metrics;
  page.append(
    h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, "Evaluations"),
        h("p", { class: "lead" },
          "The frozen hybrid classifier (variant ", h("code", {}, "default"), "), headline tiers T1–T4, labels as reviewed. "
          + "Intervals are family-level bootstraps. These are recorded evaluation results, not production traffic.")),
      dataClass("Recorded evaluation")),
    statusPanel(m),
    levelChart(m),
    splitTable(m),
    h("div", { class: "grid cols-2" }, confusionMatrix(m), categoryBars(m)),
    h("div", { class: "grid cols-2" }, perLevelTable(m), highRiskPanel(m)),
    h("div", { class: "grid cols-2" }, agentPanel(m), foundryPanel(m)),
    openItems(m),
  );
}

function statusPanel(m) {
  const st = m.status;
  const locked = m.headline.find((r) => r.split === "locked test");
  const row = (label, value, kind) => h("div", { class: "status-row" }, h("span", {}, label), statusBadge(value, kind));
  return h("div", { class: "grid cols-2" },
    card("UC4 v0.1 Status", dataClass("Static documentation"),
      h("div", { class: "status-list" },
        row("Implemented", st.implemented ? "Yes" : "No", st.implemented ? "good" : "bad"),
        row("Evaluated", st.evaluated ? "Yes" : "No", st.evaluated ? "good" : "bad"),
        row("Human reviewed", st.human_review ? `Yes, ${st.human_review}` : "No", "warn"),
        row("Second independent review", titleCase(st.second_independent_review), st.second_independent_review === "done" ? "good" : "warn"),
        row("Independent validation", titleCase(st.independent_validation), st.independent_validation === "met" ? "good" : "warn")),
      source("docs/uc4/completion-report.md")),
    card("The level gate, stated honestly", dataClass("Recorded evaluation"),
      h("div", { class: "status-list" },
        h("div", { class: "status-row" }, h("span", {}, `Strict: locked-test lower bound ${fmt(st.locked_test_strict_lower_bound)} vs gate ${fmt(st.level_gate, 2)}`),
          statusBadge(st.strict_gate_met ? "Met" : "Not met", st.strict_gate_met ? "good" : "bad")),
        h("div", { class: "status-row" }, h("span", {}, `Lenient (adopted, decision A33): lower bound ${fmt(locked.lenient_level_f1.lo)}`),
          statusBadge(st.lenient_gate_met ? "Met" : "Not met", st.lenient_gate_met ? "good" : "bad"))),
      h("div", { class: "callout" },
        h("b", {}, "Why both are shown: "),
        `the strict point estimate on the locked test is ${fmt(locked.strict_level_f1.value)}, but its lower confidence bound `
        + `(${fmt(locked.strict_level_f1.lo)}) is below the ${fmt(st.level_gate, 2)} gate. The lenient view counts a level inside the `
        + "gold label's own documented alternatives as correct. Adopting it was a product decision, made and recorded explicitly; "
        + "the strict number stays visible."),
      source("docs/uc4/completion-report.md", "config/eval/gates.v1.yaml")));
}

function titleCase(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : "Unknown"; }

function levelChart(m) {
  const W = 760, left = 150, right = 24, rowH = 22, groupGap = 14, top = 30;
  const lo = 0.5, hi = 1.0;
  const X = (v) => left + ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * (W - left - right);
  const splits = m.headline;
  const H = top + splits.length * (2 * rowH + groupGap) + 26;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": "Level macro-F1 by split, strict and lenient, with 95% family-bootstrap intervals and the 0.85 gate" });
  // ticks
  for (let t = lo; t <= hi + 1e-9; t += 0.1) {
    svg.append(s("line", { x1: X(t), x2: X(t), y1: top - 6, y2: H - 22, class: "axis" }),
      s("text", { x: X(t), y: H - 6, "text-anchor": "middle" }, t.toFixed(1)));
  }
  const gate = m.gates.level_macro_f1;
  svg.append(s("line", { x1: X(gate), x2: X(gate), y1: top - 14, y2: H - 22, class: "gate" }),
    s("text", { x: X(gate) + 4, y: top - 16, class: "gate-label" }, `gate ${gate.toFixed(2)}`));
  let y = top;
  for (const r of splits) {
    svg.append(s("text", { x: 0, y: y + rowH + 2, "font-weight": 600 }, r.split));
    for (const [kind, v] of [["strict", r.strict_level_f1], ["lenient", r.lenient_level_f1]]) {
      const cy = y + rowH / 2 + (kind === "lenient" ? rowH : 0);
      svg.append(
        s("line", { x1: X(v.lo), x2: X(v.hi), y1: cy, y2: cy, class: `whisker ${kind}` }),
        s("line", { x1: X(v.lo), x2: X(v.lo), y1: cy - 5, y2: cy + 5, class: `whisker ${kind}` }),
        s("line", { x1: X(v.hi), x2: X(v.hi), y1: cy - 5, y2: cy + 5, class: `whisker ${kind}` }),
        s("circle", { cx: X(v.value), cy, r: 5, class: kind }),
      );
    }
    y += 2 * rowH + groupGap;
  }
  return card("Sensitivity-level macro-F1 by split", dataClass("Recorded evaluation"),
    h("div", { class: "chart" }, svg),
    h("div", { class: "legend" },
      h("span", {}, h("i", { class: "i-strict" }), "strict"),
      h("span", {}, h("i", { class: "i-lenient" }), "lenient (gold's acceptable alternatives count)"),
      h("span", {}, h("i", { class: "i-gate" }), "PRD gate (config)")),
    source("docs/uc4/completion-report.md"));
}

function splitTable(m) {
  const rows = m.headline.map((r) => h("tr", {},
    h("td", {}, h("b", {}, r.split), h("div", { class: "source" }, r.note)),
    h("td", { class: "n" }, `${fmt(r.strict_level_f1.value)} [${fmt(r.strict_level_f1.lo)}, ${fmt(r.strict_level_f1.hi)}]`),
    h("td", { class: "n" }, `${fmt(r.lenient_level_f1.value)} [${fmt(r.lenient_level_f1.lo)}, ${fmt(r.lenient_level_f1.hi)}]`),
    h("td", { class: "n" }, fmt(r.category_f1.value)),
    h("td", { class: "n" }, fmt(r.high_risk_recall.value)
      + (r.high_risk_recall.numerator ? ` (${r.high_risk_recall.numerator}/${r.high_risk_recall.denominator})` : ""))));
  return h("div", { class: "grid" }, card("Results by split", dataClass("Recorded evaluation"),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Split"), h("th", { class: "n" }, "Strict level F1"), h("th", { class: "n" }, "Lenient level F1"),
        h("th", { class: "n" }, "Category F1"), h("th", { class: "n" }, "High-risk recall"))),
      h("tbody", {}, rows))),
    h("div", { class: "callout" }, h("b", {}, "Reading the splits: "),
      "dev chose the variant, so its numbers are optimistic; calibration is out-of-sample; the locked test was evaluated once "
      + "(plus one audited replay) and is now consumed."),
    source("docs/uc4/completion-report.md")));
}

function confusionMatrix(m) {
  const cm = m.locked_test.confusion_matrix;
  const cols = cm.columns;
  const SHORT = { PUBLIC: "Public", INTERNAL: "Internal", CONFIDENTIAL: "Conf.", HIGHLY_CONFIDENTIAL: "Highly conf.", NO_PREDICTION: "No label" };
  const head = h("tr", {}, h("th", {}, "gold \\ predicted"), cols.map((c) => h("th", { class: "n", title: c }, SHORT[c] || c)));
  const body = Object.entries(cm.rows).map(([gold, row]) => h("tr", {},
    h("td", {}, levelChip(gold)),
    cols.map((c) => {
      const v = row[c];
      const cls = ["cell", v === 0 ? "zero" : "", c === gold ? "diag" : ""].join(" ");
      return h("td", { class: cls }, v);
    })));
  return card("Locked-test confusion matrix (levels)", dataClass("Recorded evaluation"),
    h("div", { class: "table-wrap" }, h("table", { class: "cm" }, h("thead", {}, head), h("tbody", {}, body))),
    h("div", { class: "callout" }, h("b", {}, "The one error pattern: "),
      `${cm.rows.CONFIDENTIAL.INTERNAL} CONFIDENTIAL documents predicted INTERNAL, one level low. The completion report records that every remaining `
      + "strict level error on test sits inside the gold label's own acceptable alternatives. No severe under-classification."),
    source("docs/uc4/results/hybrid-locked-test.md", `run ${m.locked_test.run_id}`));
}

function categoryBars(m) {
  const rows = m.locked_test.per_category.map((c) => {
    const fill = h("div", { class: "fill" });
    fill.style.width = `${Math.max(0, Math.min(1, c.f1)) * 100}%`;
    return h("div", { class: "row" }, h("span", { class: "mono" }, c.category), h("div", { class: "track" }, fill), h("span", { class: "num" }, fmt(c.f1)));
  });
  return card("Locked-test F1 per data category", dataClass("Recorded evaluation"),
    h("div", { class: "bars" }, rows),
    h("div", { class: "source" }, "Each category has 18–24 positive documents on test (flagged small-sample by the harness)."),
    source("docs/uc4/results/hybrid-locked-test.md"));
}

function perLevelTable(m) {
  const rows = m.locked_test.per_level.map((r) => h("tr", {}, h("td", {}, levelChip(r.level)),
    h("td", { class: "n" }, fmt(r.precision)), h("td", { class: "n" }, fmt(r.recall)), h("td", { class: "n" }, fmt(r.f1)), h("td", { class: "n" }, r.support)));
  return card("Locked-test precision / recall per level", dataClass("Recorded evaluation"),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Level"), h("th", { class: "n" }, "Precision"), h("th", { class: "n" }, "Recall"), h("th", { class: "n" }, "F1"), h("th", { class: "n" }, "Support"))),
      h("tbody", {}, rows))),
    source("docs/uc4/results/hybrid-locked-test.md"));
}

function highRiskPanel(m) {
  const hr = m.locked_test.high_risk;
  const kpi = (l, v, sub) => h("div", { class: "kpi" }, h("div", { class: "l" }, l), h("div", { class: "v" }, v), sub ? h("div", { class: "s" }, sub) : null);
  return card("High-risk detection (locked test)", dataClass("Recorded evaluation"),
    h("div", { class: "grid cols-2" },
      kpi("Recall", fmt(hr.recall), `${hr.TP} of ${hr.TP + hr.FN} high-risk documents flagged`),
      kpi("Missed high-risk", String(hr.FN), "false negatives"),
      kpi("Precision", fmt(hr.precision), `${hr.FP} false positives`),
      kpi("Deferred to review", String(m.locked_test.deferred_to_review ?? "—"), "scored on their provisional label")),
    h("div", { class: "source" }, "High-risk is derived from the configured definition (config/taxonomy/high_risk.v1.yaml), never predicted by a model."),
    source("docs/uc4/results/hybrid-locked-test.md"));
}

function agentPanel(m) {
  const a = m.agent;
  const kpi = (l, v, sub) => h("div", { class: "kpi" }, h("div", { class: "l" }, l), h("div", { class: "v" }, v), sub ? h("div", { class: "s" }, sub) : null);
  return card("Batch Triage Agent evals (dev)", dataClass("Recorded evaluation"),
    h("div", { class: "grid cols-2" },
      kpi("Task completion", pct(a.task_completion), `${a.n_documents} documents`),
      kpi("Safety-invariant compliance", pct(a.safety_invariant_compliance), "structural, proven by adversarial tests"),
      kpi("APF composite", fmt(a.apf.composite), `effectiveness ${fmt(a.apf.effectiveness)}, efficiency ${fmt(a.apf.efficiency)}`),
      kpi("Honest / Reliability", "not computed", "by design (D9.27), not zero")),
    h("div", { class: "source" }, `Planner: ${a.planner}. This evaluates the agent, separately from the classifier above.`),
    source("docs/uc4/results/agent-eval-dev.json"));
}

function foundryPanel(m) {
  const f = m.foundry_evals;
  const row = (r) => h("tr", {}, h("td", { class: "mono" }, r.criterion), h("td", { class: "n" }, `${r.passed} / ${r.total}`));
  return card("Logged to Microsoft Foundry Evaluations", dataClass("Recorded evaluation"),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Criterion"), h("th", { class: "n" }, "Passed"))),
      h("tbody", {},
        h("tr", {}, h("td", { colspan: 2 }, h("b", {}, "dataguard-classifier"))), f.classifier.map(row),
        h("tr", {}, h("td", { colspan: 2 }, h("b", {}, "dataguard-agent"))), f.agent.map(row)))),
    h("div", { class: "source" }, `${f.split}, ${f.date}. Graded by ${f.grader}. `
      + (f.matched_local ? "Foundry's grading matched the local numbers exactly." : "")),
    source("docs/uc4/responsible-ai.md (decision D9.36)"));
}

function openItems(m) {
  const items = m.status.open_items.map((it) => h("li", {},
    statusBadge(it.closed ? "Closed" : "Open", it.closed ? "good" : "warn"), h("span", {}, it.item),
    it.note ? h("span", { class: "source" }, it.note.replace(/^Closed /, "")) : null));
  return h("div", { class: "grid" }, card("What is still open", dataClass("Static documentation"),
    h("ul", { class: "items" }, items), source("docs/uc4/completion-report.md")));
}

async function postJSON(path, body) {
  const res = await fetch(path, {
    method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  let payload = null;
  try { payload = await res.json(); } catch (_) { /* non-JSON error body */ }
  if (!res.ok) throw new Error((payload && payload.error) || `HTTP ${res.status}`);
  return payload;
}

const STATE_LABEL = {
  executed: ["Executed", "good"], failed: ["Failed / escalated", "warn"], escalated: ["Escalated", "warn"],
  not_in_variant: ["Not in this variant", "muted"], disabled_by_request: ["LLM not used", "muted"],
  not_needed: ["Not needed", "muted"], skipped: ["Skipped", "muted"], not_recorded: ["Not recorded", "muted"],
};
const OUTCOME_BADGE = {
  success: (sm) => (sm.degraded ? ["Degraded", "warn"] : ["Classified", "good"]),
  review: () => ["Review required", "warn"],
  failure: (sm) => [sm.status === "rejected" ? "Rejected" : "Error", "bad"],
};

// ---- Classify --------------------------------------------------------------------------------
async function renderClassify(page) {
  if (!state.examples) {
    const env = await getJSON("/api/examples");
    state.examples = env.data;
  }
  const replay = state.status && state.status.mode !== "live";
  const exampleButtons = state.examples.map((ex) => {
    const b = h("button", { class: `example${state.input.example === ex.key ? " active" : ""}`, type: "button" },
      h("span", { class: "t" }, ex.title, ex.expect.level ? levelChip(ex.expect.level) : statusBadge("Review", "warn")),
      h("span", { class: "d" }, ex.story));
    b.addEventListener("click", () => {
      state.input = { example: ex.key, text: ex.content, filename: ex.filename, llmOff: ex.llm_tiers === "off", upload: null };
      route();
    });
    return b;
  });
  const ta = h("textarea", { "aria-label": "Document text", spellcheck: "false" });
  ta.value = state.input.text;
  ta.addEventListener("input", () => { state.input.text = ta.value; state.input.example = null; state.input.upload = null; });
  const fn = h("input", { type: "text", "aria-label": "File name" });
  fn.value = state.input.filename;
  fn.addEventListener("input", () => { state.input.filename = fn.value; state.input.example = null; });
  const off = h("input", { type: "checkbox" });
  off.checked = state.input.llmOff;
  off.addEventListener("change", () => { state.input.llmOff = off.checked; });
  const file = h("input", { type: "file", accept: ".txt,.md,.csv,.json,.log,.yaml,.yml,.xml,.html,.py,.js,.ts,.java,.sql,.env,.ini,.cfg,text/*" });
  file.addEventListener("change", async () => {
    const f = file.files && file.files[0];
    if (!f) return;
    const buf = await f.arrayBuffer();
    let text = "";
    try { text = new TextDecoder("utf-8", { fatal: true }).decode(buf); } catch (_) { text = ""; }
    state.input = { example: null, text, filename: f.name, llmOff: state.input.llmOff, upload: { name: f.name, bytes: new Uint8Array(buf) } };
    route();
  });
  const run = h("button", { class: "btn", type: "button" }, state.busy ? "Analyzing…" : "Analyze Document");
  run.disabled = state.busy;
  run.addEventListener("click", analyze);

  page.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Classify"),
        h("p", { class: "lead" }, "Runs the real classification service on a document. Every field on the right is the frozen v1.0 result, as returned.")),
      state.last ? dataClass(state.last.data_class) : null),
    h("div", { class: "grid cols-2" },
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Demo documents"), dataClass("Synthetic dev-split documents")),
        h("div", { class: "examples" }, exampleButtons),
        h("label", { class: "field" }, "Document text (pre-extracted; binary parsing is out of scope)", ta),
        h("label", { class: "field" }, "File name (a weak signal)", fn),
        h("label", { class: "field" }, "Or upload a text file (UTF-8, 5 MB max, checked by the service's input guard)", file),
        h("label", { class: "check" }, off,
          h("span", {}, "Turn the LLM tiers off for this request (", h("code", {}, "max_llm_tier: none"),
            "): shows what happens when no LLM verdict is available.")),
        h("div", { class: "btn-row" }, run),
        replay ? h("div", { class: "note" }, "REPLAY mode replays recorded LLM answers, which exist for the demo documents above. New text has no recording, "
          + "so its LLM stages fail and the document escalates to review. That's correct fail-safe behavior; use LIVE mode for new text.") : null),
      resultCard()),
  );
  if (state.last) page.append(evidenceCard());
}

async function analyze() {
  if (state.busy) return;
  state.busy = true;
  route();
  const inp = state.input;
  try {
    let env;
    if (inp.upload) {
      env = await postJSON("/api/classify-upload", {
        name: inp.upload.name, data_base64: b64(inp.upload.bytes), llm_tiers: inp.llmOff ? "off" : "default",
      });
    } else if (inp.example) {
      env = await postJSON("/api/classify", { example: inp.example, llm_tiers: inp.llmOff ? "off" : "default" });
    } else {
      env = await postJSON("/api/classify", { text: inp.text, filename: inp.filename || "pasted.txt", llm_tiers: inp.llmOff ? "off" : "default" });
    }
    applyMode(env);
    state.last = { ...env.data, data_class: env.data_class, text: inp.text, title: inp.example ? (state.examples.find((e) => e.key === inp.example) || {}).title : inp.filename };
    state.lastError = null;
  } catch (err) {
    state.lastError = err.message;
  } finally {
    state.busy = false;
    route();
  }
}

function b64(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

function resultCard() {
  if (state.lastError) return h("div", { class: "card error" }, h("h2", {}, "The request failed"), h("p", {}, state.lastError));
  if (!state.last) {
    return h("div", { class: "card" }, h("div", { class: "empty-state" },
      h("p", {}, "Pick a demo document, then Analyze Document."),
      h("p", { class: "note" }, "The result appears here exactly as the service returns it.")));
  }
  const L = state.last, sm = L.summary, res = L.result;
  const [otext, okind] = OUTCOME_BADGE[sm.outcome](sm);
  const verdictClass = sm.outcome === "failure" ? "failure" : `l-${sm.level || "none"}`;
  const conf = sm.level_confidence;
  const lat = res.telemetry && res.telemetry.latency_ms ? res.telemetry.latency_ms.total : null;
  const llmRan = (L.trace.stages || []).some((st) => st.id === "S3" && st.state === "executed");
  return h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "Result"), dataClass(L.data_class)),
    h("div", { class: `verdict ${verdictClass}` },
      h("div", { class: "lvl-big" }, sm.outcome === "failure" ? "Not classified" : (sm.level ? sm.level.replace("_", " ") : "No level: needs a human")),
      h("div", { class: "badges" },
        statusBadge(otext, okind),
        sm.high_risk === true ? statusBadge("High risk", "bad") : null,
        sm.provisional ? statusBadge("Provisional label", "warn") : null,
        sm.guardrail_events.length ? statusBadge(`${sm.guardrail_events.length} guardrail event(s)`, "warn") : statusBadge("Guardrail passed", "good"),
        L.replay_miss ? statusBadge("Replay: no recording", "warn") : null)),
    sm.outcome === "failure"
      ? h("div", { class: "callout" }, h("b", {}, "Never treated as low sensitivity. "), `The service could not classify this input (${sm.failure_reason || sm.status}). Fix the input or retry.`)
      : null,
    L.replay_miss
      ? h("div", { class: "callout" }, h("b", {}, "No recording for this text. "), "In REPLAY mode the LLM stage has no recorded answer for new text, so it escalated to review instead of guessing. Use a demo document, or LIVE mode.")
      : null,
    h("dl", { class: "kv" },
      h("dt", {}, "Status"), h("dd", {}, h("code", {}, sm.status)),
      h("dt", {}, "Level decided by"), h("dd", {}, sm.level_decided_by || "—"),
      h("dt", {}, "Confidence"), h("dd", {}, conf ? confidenceText(conf) : "—"),
      h("dt", {}, "Categories"), h("dd", {}, sm.categories.length
        ? h("div", { class: "chips" }, sm.categories.map((c) => h("span", { class: "chip", title: c.confidence ? confidenceText(c.confidence) : "" }, `${c.id} · ${c.decided_by}`)))
        : "none"),
      h("dt", {}, "High risk"), h("dd", {}, sm.high_risk === null || sm.high_risk === undefined ? "—"
        : `${sm.high_risk ? "yes" : "no"}${sm.high_risk_reasons.length ? ` (${sm.high_risk_reasons.map((r) => `${r.axis} ${r.value}`).join(", ")})` : ""}, derived from policy config`),
      h("dt", {}, "Review"), h("dd", {}, sm.review_required ? `required: ${sm.review_reasons.join(", ")}` : "not required"),
      h("dt", {}, "Route"), h("dd", {}, h("code", {}, (res.routing.stages_run || []).join(" → ") || "—"), res.routing.stop_reason ? ` (${res.routing.stop_reason})` : ""),
      h("dt", {}, "Latency"), h("dd", {}, lat === null || lat === undefined ? "—" : `${lat.toFixed(1)} ms`
        + (L.llm_mode === "replay" && llmRan ? " (LLM part = recorded latency of the original call)" : "")),
      h("dt", {}, "Versions"), h("dd", {}, h("code", {}, `${res.versions.classifier || ""} · rules ${res.versions.ruleset || "—"} · ${res.versions.llm_deployment || "no LLM"}`))),
    h("div", { class: "btn-row" }, (() => { const a = h("a", { href: "#trace", class: "btn btn-quiet" }, "See the decision trace →"); return a; })()),
    h("details", {}, h("summary", {}, "Raw result (frozen schema v1.0)"), h("pre", { class: "json" }, JSON.stringify(res, null, 2))),
    h("details", {}, h("summary", {}, `Warnings (${sm.warnings.length})`), h("ul", { class: "why-list" }, sm.warnings.map((w) => h("li", {}, h("code", {}, w))))));
}

function confidenceText(c) {
  const kinds = {
    verbalized_bucket: "the LLM's own low/medium/high, never calibrated",
    rule_strength: "rule strength, not a probability",
    calibrated_probability: "calibrated probability",
    uncalibrated_score: "uncalibrated score",
    none: "no confidence",
  };
  return `${c.raw ?? "—"} (${kinds[c.kind] || c.kind})`;
}

function evidenceCard() {
  const L = state.last, ev = L.result.evidence || [];
  const located = ev.filter((e) => e.locator && Number.isInteger(e.locator.char_start) && Number.isInteger(e.locator.char_end));
  let docView = null;
  if (L.text && located.length) {
    // Highlight the service's own evidence locators in the text that was sent. Overlapping spans
    // are merged, and a merged span records every source that pointed at it (rules, LLM or both).
    const spans = located.map((e) => ({ a: e.locator.char_start, b: e.locator.char_end, src: e.source.startsWith("llm") ? "llm" : "rules", id: e.evidence_id }))
      .filter((x) => x.a >= 0 && x.b <= L.text.length && x.a < x.b).sort((x, y) => x.a - y.a || y.b - x.b);
    const merged = [];
    for (const sp of spans) {
      const last = merged[merged.length - 1];
      if (last && sp.a < last.b) {
        last.b = Math.max(last.b, sp.b); last.srcs.add(sp.src); last.ids.push(sp.id);
      } else {
        merged.push({ a: sp.a, b: sp.b, srcs: new Set([sp.src]), ids: [sp.id] });
      }
    }
    const parts = [];
    let pos = 0;
    for (const m of merged) {
      parts.push(L.text.slice(pos, m.a));
      const cls = m.srcs.size > 1 ? "src-both" : `src-${[...m.srcs][0]}`;
      parts.push(h("mark", { class: `ev ${cls}`, title: m.ids.join(", ") }, L.text.slice(m.a, m.b)));
      pos = m.b;
    }
    parts.push(L.text.slice(pos));
    docView = h("div", {}, h("div", { class: "doc" }, parts),
      h("div", { class: "legend" }, h("span", {}, h("mark", { class: "ev src-rules" }, "rule match")),
        h("span", {}, h("mark", { class: "ev src-llm" }, "LLM quote")), h("span", {}, h("mark", { class: "ev src-both" }, "rule match + LLM quote"))));
  }
  const items = ev.map((e) => h("div", { class: "ev-item" },
    h("div", { class: "top" }, h("code", {}, e.evidence_id), h("span", { class: "chip" }, `${e.supports.axis}: ${e.supports.value}`),
      statusBadge(e.provenance, e.provenance === "observed" ? "good" : "muted"),
      e.verified ? statusBadge("verified quote", "good") : null, e.strength ? h("span", { class: "chip" }, e.strength) : null),
    h("div", {}, h("code", {}, e.excerpt || "(no excerpt)")),
    h("div", { class: "note" }, `${e.type}${e.detector ? ` · detector ${e.detector.id}@${e.detector.version}` : ""}`)));
  return h("div", { class: "grid" }, card("Why: the evidence", dataClass(L.data_class),
    h("p", { class: "note" }, "Evidence as returned by the service. Excerpts are masked by the service itself; highlighted spans use its own character locators."),
    docView,
    ev.length ? h("div", { class: "ev-list" }, items) : h("p", { class: "note" }, "No evidence returned for this result.")));
}

// ---- Decision Trace --------------------------------------------------------------------------
async function renderTrace(page) {
  const head = h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, "Decision Trace"),
      h("p", { class: "lead" }, "How the hybrid router reached this decision: each stage marked with what actually happened, from the request's own redacted spans and routing record.")),
    state.last ? dataClass(state.last.data_class) : null);
  page.append(head);
  if (!state.last) {
    const b = h("button", { class: "btn", type: "button" }, "Classify the healthcare document");
    b.addEventListener("click", async () => {
      const ex = (state.examples || (await getJSON("/api/examples")).data).find((e) => e.key === "healthcare");
      state.examples = state.examples || [ex];
      state.input = { example: "healthcare", text: ex.content, filename: ex.filename, llmOff: false, upload: null };
      await analyze();
    });
    page.append(h("div", { class: "card" }, h("div", { class: "empty-state" }, h("p", {}, "No document has been classified yet."), b)));
    return;
  }
  const L = state.last, t = L.trace, sm = L.summary;
  const items = t.stages.map((st) => {
    const [label, kind] = STATE_LABEL[st.state] || [st.state, "muted"];
    const meta = [];
    if (st.latency_ms !== undefined && st.latency_ms !== null) meta.push(`${st.latency_ms.toFixed(1)} ms${st.latency_note ? ` (${st.latency_note})` : ""}`);
    if (st.model) meta.push(`deployment ${st.model}${st.served_model && st.served_model !== st.model ? `, served by ${st.served_model}` : ""}`);
    if (st.replayed) meta.push("replayed response");
    if (st.tokens && (st.tokens.in || st.tokens.out)) meta.push(`${st.tokens.in ?? "?"} in / ${st.tokens.out ?? "?"} out tokens`);
    if (st.replay_miss) meta.push("no recorded response for this text");
    return h("li", { class: `st-${st.state}` },
      h("div", { class: "dot" }, st.id),
      h("div", { class: "body" },
        h("div", { class: "head" }, h("b", {}, st.name), statusBadge(label, kind)),
        st.detail ? h("div", {}, st.detail) : null,
        meta.length ? h("div", { class: "meta" }, meta.join(" · ")) : null,
        st.note ? h("div", { class: "why" }, st.note) : null));
  });
  const v = t.variant;
  page.append(
    h("div", { class: "grid cols-4" },
      kpiBox("Final", sm.outcome === "failure" ? "Not classified" : (sm.level ? sm.level.replace("_", " ") : "Review")),
      kpiBox("Stop reason", t.stop_reason || "—", "code"),
      kpiBox("Escalations", String(t.escalations)),
      kpiBox("Total latency", t.total_latency_ms === undefined || t.total_latency_ms === null ? "—" : `${t.total_latency_ms.toFixed(0)} ms`)),
    h("div", { class: "grid cols-2" },
      card(`Stages: ${L.title || "document"}`, dataClass(L.data_class), h("ol", { class: "timeline" }, items)),
      h("div", {},
        card("Routing variant in force", dataClass("Static configuration"),
          h("dl", { class: "kv" },
            h("dt", {}, "Variant"), h("dd", {}, h("code", {}, L.variant)),
            h("dt", {}, "Rules short-circuit"), h("dd", {}, v.rules_short_circuit ? "on" : "off (Rules never skip the LLM)"),
            h("dt", {}, "ML stage"), h("dd", {}, v.ml_enabled ? "enabled" : "disabled in this variant"),
            h("dt", {}, "LLM tier order"), h("dd", {}, h("code", {}, v.llm_tier_order.join(" → ")), " (each tier at most once)"),
            h("dt", {}, "LLM acceptance"), h("dd", {}, `confidence bucket ≥ ${v.llm_min_confidence}, quotes verified, no conflict`)),
          source("config/routing/routing.v1.yaml")),
        whyCard())),
    h("div", { class: "grid" }, card("Redacted spans for this request", dataClass(L.data_class),
      h("p", { class: "note" }, "What the tracer exported: only allow-listed dg.* attributes. No document text, only a content hash."),
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Span"), h("th", {}, "Status"), h("th", { class: "n" }, "Duration"), h("th", {}, "Attributes"))),
        h("tbody", {}, t.spans.map((sp) => h("tr", {},
          h("td", { class: "mono" }, sp.name), h("td", {}, sp.status), h("td", { class: "n" }, `${sp.duration_ms.toFixed(2)} ms`),
          h("td", {}, h("details", {}, h("summary", {}, `${Object.keys(sp.attributes).length} attributes`), h("pre", { class: "json" }, JSON.stringify(sp.attributes, null, 2))))))))),
      h("div", { class: "note" }, L.llm_mode === "replay" ? "Span durations are measured now; a replayed LLM call completes instantly, which is why its stage latency above is the recorded one." : ""))),
  );
}

function kpiBox(label, value, kind) {
  return h("div", { class: "card kpi" }, h("div", { class: "l" }, label), h("div", { class: kind === "code" ? "v v-code" : "v" }, value));
}

function whyCard() {
  const L = state.last, sm = L.summary, ev = L.result.evidence || [];
  const facts = [];
  if (sm.outcome === "failure") facts.push(`The input was not classified: ${sm.failure_reason || sm.status}.`);
  if (sm.level) facts.push(`Level ${sm.level} was set by ${sm.level_decided_by}${sm.level_confidence ? `, confidence ${confidenceText(sm.level_confidence)}` : ""}.`);
  for (const c of sm.categories) {
    const refs = ev.filter((e) => c.evidence_ids.includes(e.evidence_id));
    const obs = refs.filter((e) => e.provenance === "observed").length;
    facts.push(`${c.id}: decided by ${c.decided_by}, backed by ${refs.length} evidence item(s) (${obs} observed, ${refs.length - obs} inferred).`);
  }
  if (sm.high_risk) facts.push(`High risk because of ${sm.high_risk_reasons.map((r) => `${r.axis} ${r.value}`).join(" and ")}, per the high-risk policy config.`);
  if (sm.review_required) facts.push(`Sent to human review: ${sm.review_reasons.join(", ")}.${sm.level ? "" : " No stage produced a usable level, so there is no label, and no default."}`);
  if (L.result.routing.stop_reason) facts.push(`The router stopped at ${L.result.routing.stop_reason}.`);
  return h("div", { class: "card" }, h("details", { open: true }, h("summary", {}, "Why this decision?"),
    h("ul", { class: "why-list" }, facts.map((f) => h("li", {}, f))),
    h("div", { class: "note" }, "Built only from the result's own fields: level, categories, evidence, high-risk reasons, review codes and routing.")));
}

// ---- Agent Triage ----------------------------------------------------------------------------
async function renderAgent(page) {
  const A = state.agent;
  if (!A.info) {
    const env = await getJSON("/api/agent");
    applyMode(env);
    A.info = env.data;
  }
  const info = A.info, live = info.planner_is_model;
  const runBtn = h("button", { class: "btn", type: "button" }, A.running ? "Running…" : "Run Agent Triage");
  runBtn.disabled = Boolean(A.running);
  runBtn.addEventListener("click", runAgentBatch);
  const done = Object.keys(A.results).length;
  const rows = info.batch.map((d, i) => {
    const r = A.results[d.key];
    let right;
    if (A.running === d.key) right = h("span", {}, h("span", { class: "spinner" }), " running");
    else if (r && r.error) right = statusBadge("Error", "bad");
    else if (r) {
      const ann = r.annotation;
      right = h("span", { class: "badges" },
        ann.level ? levelChip(ann.level) : statusBadge("No level", "warn"),
        statusBadge(`priority ${ann.priority}`, ann.priority === "high" ? "bad" : ann.priority === "medium" ? "warn" : "muted"),
        ann.review_requested ? statusBadge("Review", "warn") : null,
        r.guardrail_events.length ? statusBadge("Injection flagged", "warn") : null,
        r.invariant_violated ? statusBadge("Invariant violated", "bad") : statusBadge("Invariant held", "good"));
    } else right = h("span", { class: "note" }, "queued");
    const row = h("div", { class: `batch-row${A.selected === d.key ? " sel" : ""}` },
      h("span", { class: "n" }, String(i + 1)),
      h("div", { class: "main" }, h("b", {}, d.title), h("div", { class: "why" }, d.why, " · ", h("span", { class: "mono" }, d.family_id))),
      h("div", { class: "status" }, right));
    row.addEventListener("click", () => { if (A.results[d.key]) { A.selected = d.key; route(); } });
    return row;
  });
  page.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Agent Triage"),
        h("p", { class: "lead" }, "The Batch Triage Agent, the genuinely agentic part of the system: a bounded loop that uses classify_document as a tool. "
          + "It never decides the sensitivity level itself.")),
      dataClass(live ? "LIVE" : "REPLAY")),
    h("div", { class: `planner-banner${live ? " live" : ""}` },
      h("div", {}, h("b", {}, "Planner: "), info.planner),
      statusBadge(live ? "LIVE FOUNDRY" : "REPLAY / LOCAL DEMO", live ? "bad" : "muted")),
    h("div", { class: "grid cols-2" },
      card("The bounded loop", dataClass("Static configuration"),
        h("div", { class: "loop" },
          ...["Goal", "Planner turn", "Tool call (allow-list)", "classify_document", "Review? (can only add)", "Next turn or stop"]
            .flatMap((n, i) => [i ? h("span", { class: "arrow" }, "→") : null, h("span", { class: `node${n === "classify_document" ? " hot" : ""}` }, n)])),
        h("dl", { class: "kv" },
          h("dt", {}, "Allowed tools"), h("dd", {}, h("div", { class: "chips" }, info.allowed_tools.map((t) => h("span", { class: "chip" }, t)))),
          h("dt", {}, "Step budget"), h("dd", {}, `${info.max_steps_per_document} planner steps per document`),
          h("dt", {}, "Stops on"), h("dd", {}, "a final answer, the step budget, or two consecutive tool failures (then forces review)"),
          h("dt", {}, "Invariant"), h("dd", {}, "the level is copied from classify_document, never from the planner's text")),
        source(`config/agent/agent.v1.yaml (v${info.agent_config_version})`, "app/agent/loop.py")),
      card("Goal given to the planner", dataClass("Static configuration"),
        h("pre", { class: "json wrap" }, info.goal),
        h("div", { class: "note" }, live ? "Registered as the agent's instructions in Foundry Agent Service."
          : "The offline planner follows a fixed, inspectable policy instead of a model (app/agent/offline_policy.py)."))),
    h("div", { class: "grid cols-2" },
      card(`Batch: ${info.batch.length} synthetic dev-split documents`, dataClass(live ? "LIVE" : "REPLAY"),
        h("div", {}, rows),
        h("div", { class: "btn-row" }, runBtn, h("span", { class: "note" }, `${done}/${info.batch.length} processed, one request per document`)),
        A.error ? h("div", { class: "callout" }, h("b", {}, "Stopped: "), A.error) : null),
      agentDetail()),
  );
}

async function runAgentBatch() {
  const A = state.agent;
  if (A.running) return;
  A.results = {};
  A.error = null;
  for (const d of A.info.batch) {
    A.running = d.key;
    route();
    try {
      const env = await postJSON("/api/agent/run", { doc: d.key });
      applyMode(env);
      A.results[d.key] = env.data;
      A.selected = d.key;
    } catch (err) {
      A.results[d.key] = { error: err.message };
      A.error = err.message;
      break; // never continue silently after a failure
    }
  }
  A.running = null;
  route();
}

function agentDetail() {
  const A = state.agent, r = A.selected ? A.results[A.selected] : null;
  if (!r) return card("Run detail", null, h("div", { class: "empty-state" }, h("p", {}, "Run the batch, then pick a document to see the agent's steps.")));
  if (r.error) return card("Run detail", null, h("div", { class: "callout" }, r.error));
  const ann = r.annotation;
  const steps = r.steps.map((st) => h("li", { class: `st-${st.kind === "planner" ? (st.status === "error" ? "failed" : "executed") : (st.ok ? "executed" : "failed")}` },
    h("div", { class: "dot" }, st.kind === "planner" ? `P${st.step}` : `T${st.step}`),
    h("div", { class: "body" },
      h("div", { class: "head" },
        h("b", {}, st.kind === "planner" ? `Planner turn ${st.step}` : `Tool: ${st.tool}`),
        st.kind === "planner" ? statusBadge(st.turn === "final" ? "final answer" : `${st.tool_calls} tool call(s)`, "muted")
          : statusBadge(st.ok ? "ok" : `failed: ${st.error}`, st.ok ? "good" : "bad")),
      h("div", { class: "meta" }, [
        `${st.duration_ms.toFixed(1)} ms`,
        st.model ? `model ${st.model}` : (st.kind === "planner" ? "offline planner, no model" : null),
        st.tokens && (st.tokens.in || st.tokens.out) ? `${st.tokens.in} in / ${st.tokens.out} out tokens` : null,
        st.error && st.kind === "planner" ? `error ${st.error}` : null,
        st.args_rebound ? "planner arguments ignored; original document classified" : null,
      ].filter(Boolean).join(" · ")))));
  return card(`Run detail: ${r.doc.title}`, dataClass(state.agent.info.planner_is_model ? "LIVE" : "REPLAY"),
    r.invariant_violated ? h("div", { class: "violation" }, "SAFETY INVARIANT VIOLATED: the agent's level does not match classify_document's. This run must not be trusted.") : null,
    h("dl", { class: "kv" },
      h("dt", {}, "Level"), h("dd", {}, ann.level ? levelChip(ann.level) : "none (review)"),
      h("dt", {}, "Priority"), h("dd", {}, ann.priority),
      h("dt", {}, "Review"), h("dd", {}, ann.review_requested ? `requested (${r.review_reason_fixed})` : "not requested"),
      h("dt", {}, "Termination"), h("dd", {}, h("code", {}, ann.stopped_reason)),
      h("dt", {}, "Rationale"), h("dd", {}, ann.rationale || "—")),
    h("h2", { class: "sub" }, "Steps"),
    h("ol", { class: "timeline" }, steps),
    h("h2", { class: "sub" }, "Safety checks (from this run's trace)"),
    h("div", {}, r.checks.map((c) => h("div", { class: "check-row" }, statusBadge(c.holds ? "Holds" : "Violated", c.holds ? "good" : "bad"),
      h("div", {}, h("b", {}, c.check), h("div", { class: "note" }, c.detail))))),
    r.guardrail_events.length
      ? h("div", { class: "callout" }, h("b", {}, "Guardrail: "), r.guardrail_events.map((g) => `${g.type} (${g.trigger}) → ${g.action}`).join("; "),
        ". The embedded instruction was treated as document data, not obeyed.")
      : null);
}

// ---- Human Review ----------------------------------------------------------------------------
async function renderReview(page) {
  const R = state.review;
  const env = await getJSON("/api/review");
  R.items = env.data.items;
  R.storedAt = env.data.stored_at;
  const seed = h("button", { class: "btn btn-quiet", type: "button" }, "Add the fail-safe review cases");
  seed.addEventListener("click", async () => {
    try { const e = await postJSON("/api/review/seed", {}); applyMode(e); R.error = null; } catch (err) { R.error = err.message; }
    route();
  });
  const head = h("div", { class: "q-row q-head" }, h("span", {}, "Document"), h("span", {}, "AI classification"), h("span", {}, "Reason for review"), h("span", {}, "Status"));
  const rows = R.items.map((it) => {
    const row = h("div", { class: `q-row${R.selected === it.id ? " sel" : ""}` },
      h("span", {}, h("b", {}, it.title), h("div", { class: "note" }, `${it.source} · ${it.id}`)),
      h("span", {}, it.ai_level ? levelChip(it.ai_level) : statusBadge("No label", "warn"), it.provisional ? " provisional" : ""),
      h("span", { class: "mono" }, it.reasons.join(", ")),
      statusBadge(it.status, it.status === "open" ? "warn" : it.status === "escalated" ? "bad" : "good"));
    row.addEventListener("click", () => { R.selected = it.id; route(); });
    return row;
  });
  page.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Human Review"),
        h("p", { class: "lead" }, "Classifications the service itself sent to review. A review is a flag, never a block, and a document with no usable level is never defaulted to PUBLIC.")),
      dataClass("Demo-only state")),
    h("div", { class: "callout" }, h("b", {}, "Demo reviewer decisions, not gold-label adjudication. "),
      `Actions here are stored separately in ${R.storedAt}. They never change the dataset, gold labels or any evaluation artifact.`),
    h("div", { class: "grid cols-2" },
      card(`Review queue (${R.items.filter((i) => i.status === "open").length} open)`, dataClass("Demo-only state"),
        R.items.length ? h("div", {}, head, rows)
          : h("div", { class: "empty-state" }, h("p", {}, "The queue is empty. Classify a document that needs review, or add the fail-safe cases."), seed),
        R.items.length ? h("div", { class: "btn-row" }, seed) : null,
        R.error ? h("div", { class: "callout" }, R.error) : null),
      reviewDetail()),
  );
}

function reviewDetail() {
  const R = state.review, it = (R.items || []).find((i) => i.id === R.selected);
  if (!it) return card("Inspect", null, h("div", { class: "empty-state" }, h("p", {}, "Select a queued item.")));
  const note = h("input", { type: "text", "aria-label": "Reviewer note", placeholder: "Reviewer note (optional)" });
  // No pre-selected level: an override is a deliberate choice, never a default (least of all PUBLIC).
  const level = h("select", { "aria-label": "Override level" },
    h("option", { value: "" }, "Choose a level…"),
    ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_CONFIDENTIAL"].map((l) => h("option", { value: l }, l)));
  const act = (action) => async () => {
    try {
      await postJSON("/api/review/decide", { id: it.id, action, level: action === "override" ? level.value : undefined, note: note.value });
      R.error = null;
    } catch (err) { R.error = err.message; }
    route();
  };
  const approve = h("button", { class: "btn btn-good", type: "button" }, "Approve AI label");
  approve.disabled = !it.ai_level || it.status !== "open";
  approve.title = it.ai_level ? "" : "There is no AI label to approve";
  approve.addEventListener("click", act("approve"));
  const override = h("button", { class: "btn", type: "button" }, "Override");
  override.disabled = true;
  level.addEventListener("change", () => { override.disabled = it.status !== "open" || !level.value; });
  override.addEventListener("click", act("override"));
  const escalate = h("button", { class: "btn btn-warn", type: "button" }, "Escalate");
  escalate.disabled = it.status !== "open";
  escalate.addEventListener("click", act("escalate"));
  return card(`Inspect: ${it.title}`, dataClass("Demo-only state"),
    h("dl", { class: "kv" },
      h("dt", {}, "AI recommendation"), h("dd", {}, it.ai_level ? levelChip(it.ai_level) : "no label (no stage produced a usable level)"),
      h("dt", {}, "Confidence"), h("dd", {}, it.confidence ? confidenceText(it.confidence) : "—"),
      h("dt", {}, "Categories"), h("dd", {}, it.categories.length ? it.categories.join(", ") : "none"),
      h("dt", {}, "Reason for escalation"), h("dd", {}, h("code", {}, it.reasons.join(", ")), it.stop_reason ? ` (router: ${it.stop_reason})` : ""),
      h("dt", {}, "Evidence"), h("dd", {}, it.evidence.length ? h("ul", { class: "why-list" }, it.evidence.map((e) => h("li", {}, h("code", {}, e.excerpt || e.id), ` · ${e.supports.axis} ${e.supports.value} · ${e.provenance}`))) : "none returned")),
    it.decision
      ? h("div", { class: "callout" }, h("b", {}, `Decided: ${it.decision.action}`), it.decision.final_level ? ` → ${it.decision.final_level}` : "",
        it.decision.note ? `. Note: ${it.decision.note}` : "", ". Stored as a demo reviewer decision (not gold).")
      : h("div", {},
        h("label", { class: "field" }, "Override to", level),
        h("label", { class: "field" }, "Note", note),
        h("div", { class: "btn-row" }, approve, override, escalate)),
    R.error ? h("div", { class: "callout" }, R.error) : null);
}

// ---- boot ------------------------------------------------------------------------------------
async function boot() {
  try {
    const env = await getJSON("/api/status");
    state.status = env.data;
    applyMode(env);
  } catch (err) {
    document.getElementById("mode-badge").textContent = "SERVER UNREACHABLE";
  }
  window.addEventListener("hashchange", route);
  route();
}
document.addEventListener("DOMContentLoaded", boot);
