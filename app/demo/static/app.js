"use strict";
// DataGuard AI interview demo. Every value on screen comes from the local demo server, which reads
// committed artifacts or calls the existing service. DOM is built with textContent only (no
// innerHTML from data), and every panel shows its data class and source.

const PAGES = {
  overview: { title: "Overview", render: renderOverview },
  classify: { title: "Classify", render: renderClassify },
  trace: { title: "Decision Trace", render: renderTrace },
  agent: { title: "Agent Triage", render: renderAgent },
  review: { title: "Human Review", render: renderReview },
  evaluations: { title: "Evaluations", render: renderEvaluations },
  rai: { title: "Responsible AI / Guardrails", render: renderRai },
  observability: { title: "Observability", render: renderObservability },
};
const DEFAULT_PAGE = "overview";
const state = {
  status: null, metrics: null, examples: null, last: null, busy: false,
  input: { example: null, text: "", filename: "pasted.txt", llmOff: false, upload: null },
  agent: { info: null, results: {}, running: null, selected: null, error: null },
  review: { items: null, selected: null, error: null, storedAt: null },
  rai: null, obs: { selected: null },
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
  if (state.pendingExample) {
    const ex = state.examples.find((e) => e.key === state.pendingExample);
    if (ex) state.input = { example: ex.key, text: ex.content, filename: ex.filename, llmOff: ex.llm_tiers === "off", upload: null };
    state.pendingExample = null;
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
        h("label", { class: "field dev-only" }, "File name (a weak signal)", fn),
        h("label", { class: "field dev-only" }, "Or upload a text file (UTF-8, 5 MB max, checked by the service's input guard)", file),
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
    h("details", { class: "dev-only" }, h("summary", {}, "Raw result (frozen schema v1.0)"), h("pre", { class: "json" }, JSON.stringify(res, null, 2))),
    h("details", { class: "dev-only" }, h("summary", {}, `Warnings (${sm.warnings.length})`), h("ul", { class: "why-list" }, sm.warnings.map((w) => h("li", {}, h("code", {}, w))))));
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
    h("div", { class: "grid dev-only" }, card("Redacted spans for this request", dataClass(L.data_class),
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

// ---- Overview --------------------------------------------------------------------------------
async function ensureMetrics() {
  if (!state.metrics) {
    const env = await getJSON("/api/metrics");
    applyMode(env);
    state.metrics = env.data;
  }
  return state.metrics;
}

async function renderOverview(page) {
  const m = await ensureMetrics();
  const locked = m.headline.find((r) => r.split === "locked test");
  const st = m.status, a = m.agent;
  const kpi = (label, value, sub, src) => h("div", { class: "card kpi" },
    h("div", { class: "l" }, label), h("div", { class: "v" }, value), h("div", { class: "s" }, sub), src ? h("div", { class: "source" }, src) : null);
  page.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Overview"),
        h("p", { class: "lead" }, "Automatically discover and classify sensitive enterprise data using a hybrid Rules + ML + LLM architecture "
          + "with human review, guardrails, evaluations, and production observability.")),
      dataClass("Recorded evaluation")),
    h("div", { class: "grid cols-3" },
      kpi("Strict level F1", `${fmt(locked.strict_level_f1.value)}`, `95% CI [${fmt(locked.strict_level_f1.lo)}, ${fmt(locked.strict_level_f1.hi)}]; lower bound below the ${fmt(st.level_gate, 2)} gate`, "locked test, frozen hybrid"),
      kpi("Category F1", fmt(locked.category_f1.value), "macro-F1 over 8 data categories", "locked test"),
      kpi("High-risk recall", fmt(locked.high_risk_recall.value), `${locked.high_risk_recall.numerator} of ${locked.high_risk_recall.denominator} high-risk documents flagged`, "locked test"),
      kpi("v0.1 status", st.implemented && st.evaluated ? "Implemented, evaluated" : "In progress",
        `one human reviewer; independent validation ${st.independent_validation}`, "completion report"),
      kpi("Agent task completion", pct(a.task_completion), `${a.n_documents} documents, offline planner`, "dev split, agent eval"),
      kpi("Agent safety invariant", pct(a.safety_invariant_compliance), "structural: never-downgrade, proven by adversarial tests", "dev split, agent eval")),
    h("div", { class: "grid" }, card("How it works", dataClass("Static documentation"), architecture(),
      h("div", { class: "callout" }, h("b", {}, "Two different things, on purpose. "),
        "The classification service is deterministic and is not an agent: a harness, not a model, owns routing, thresholds and review. "
        + "The Batch Triage Agent is the genuinely agentic component: it plans and calls tools, but only classify_document can set a level."))),
    h("p", { class: "note" }, "All data is synthetic. No production traffic metrics exist or are shown."));
}

function architecture() {
  const W = 1000, H = 640;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Architecture: the deterministic classification service and the separate Batch Triage Agent" });
  svg.append(s("defs", {}, s("marker", { id: "arr", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse" },
    s("path", { d: "M0 0L10 5L0 10z", fill: "currentColor" }))));
  const lane = (x, w, title, sub) => [s("rect", { x, y: 14, width: w, height: 548, rx: 12, class: "lane" }),
    s("text", { x: x + 16, y: 40, class: "lane-title" }, title), s("text", { x: x + 16, y: 58, class: "quiet" }, sub)];
  const box = (x, y, w, hgt, label, sub, cls) => [s("rect", { x, y, width: w, height: hgt, rx: 8, class: `box ${cls || ""}` }),
    s("text", { x: x + w / 2, y: y + (sub ? 19 : hgt / 2 + 4), "text-anchor": "middle", "font-weight": 600 }, label),
    sub ? s("text", { x: x + w / 2, y: y + 35, "text-anchor": "middle", class: "quiet" }, sub) : null];
  const down = (x, y1, y2) => s("path", { d: `M${x} ${y1}V${y2}`, class: "edge", "marker-end": "url(#arr)", color: "var(--ink-3)" });
  const L = 30, LW = 540, cx = L + LW / 2, bw = 380, bx = cx - bw / 2;
  svg.append(...lane(L, LW, "Classification service", "deterministic; not an agent; the only source of a level"));
  const rows = [
    [78, "Document", "pre-extracted text; binary parsing out of scope", ""],
    [140, "Input guard + injection scan", "size/encoding; injection flagged, never obeyed", "svc"],
    [202, "Hybrid router (harness)", "owns thresholds, escalation, conflicts, review", "svc"],
  ];
  rows.forEach(([y, a, b, c]) => svg.append(...box(bx, y, bw, 46, a, b, c)));
  svg.append(down(cx, 124, 140), down(cx, 186, 202));
  // Rules | ML | LLM
  const sy = 270, sw = 116, gap = 16, sx = cx - (3 * sw + 2 * gap) / 2;
  svg.append(...box(sx, sy, sw, 46, "Rules", "always run", "svc"),
    ...box(sx + sw + gap, sy, sw, 46, "ML", "off in default variant", "svc off"),
    ...box(sx + 2 * (sw + gap), sy, sw, 46, "LLM tiers", "mid, then large", "svc"));
  svg.append(down(cx, 248, 270));
  svg.append(...box(bx, 340, bw, 46, "Fusion + confidence", "floors; high-risk derived from policy", "svc"), down(cx, 316, 340));
  svg.append(...box(bx, 402, bw, 46, "Classification (frozen schema v1.0)", "level, categories, evidence, review", "svc"), down(cx, 386, 402));
  svg.append(...box(bx, 464, bw, 46, "Human review, if required", "a flag, never a block; no default to PUBLIC", "review"), down(cx, 448, 464));
  svg.append(...box(bx, 526 - 6, bw, 30, "Policy / triage (the consumer)", null, ""), down(cx, 510, 520));
  // Agent lane
  const AL = 600, AW = 370, ax = AL + 20, aw = AW - 40, acx = AL + AW / 2;
  svg.append(...lane(AL, AW, "Batch Triage Agent", "genuinely agentic; bounded loop"));
  const arows = [
    [78, "Goal (instructions)", "registered in Foundry Agent Service"],
    [140, "Planner turn", "offline policy, or the Foundry agent"],
    [202, "Tool call (allow-list)", "3 tools; step budget 6"],
    [270, "classify_document", "bound to the original document"],
    [340, "Review request", "can only add a review, never remove one"],
    [402, "Stop", "final answer, budget, or repeated failure"],
  ];
  arows.forEach(([y, a, b]) => svg.append(...box(ax, y, aw, 46, a, b, "agent")));
  [[124, 140], [186, 202], [248, 270], [316, 340], [386, 402]].forEach(([a, b]) => svg.append(down(acx, a, b)));
  // The agent calls the WHOLE service (it enters at the document input), never an LLM directly.
  const gx = 585;
  svg.append(s("path", { d: `M${ax} 293H${gx}V101H${bx + bw + 4}`, class: "edge call", "marker-end": "url(#arr)", color: "#16a3a3" }),
    s("text", { x: (bx + bw + gx) / 2 + 2, y: 150, "text-anchor": "middle", class: "quiet" }, "calls the"),
    s("text", { x: (bx + bw + gx) / 2 + 2, y: 164, "text-anchor": "middle", class: "quiet" }, "whole service"));
  // Foundry strip
  svg.append(s("rect", { x: 30, y: 580, width: 940, height: 46, rx: 10, class: "foundry" }),
    s("text", { x: 500, y: 600, "text-anchor": "middle", "font-weight": 600 }, "Microsoft Foundry"),
    s("text", { x: 500, y: 616, "text-anchor": "middle", class: "quiet" }, "model deployments + content filter · Agent Service · Tracing (Application Insights) · Evaluations"));
  return h("div", { class: "arch" }, svg);
}

// ---- Responsible AI / Guardrails -------------------------------------------------------------
const GUARD_STATUS = {
  implemented: ["Implemented", "good"], ci_gate: ["Implemented · CI gate", "good"],
  portal_configured: ["Configured in portal", "good"], live_verified: ["Live verified", "good"],
  fake_server_tested: ["Fake-server tested · not live verified", "warn"], not_applicable: ["Not applicable (by design)", "muted"],
  unknown: ["Unknown", "bad"],
};

async function renderRai(page) {
  if (!state.rai) state.rai = (await getJSON("/api/rai")).data;
  const r = state.rai, a = r.agent;
  const table = (cols, rows) => h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, cols.map((c) => h("th", {}, c)))), h("tbody", {}, rows)));
  page.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Responsible AI / Guardrails"),
        h("p", { class: "lead" }, "Only what exists and what it was verified against. Classifier and agent are evaluated separately, and every guardrail states its real status.")),
      dataClass("Static documentation")),
    h("div", { class: "grid cols-2" },
      card("Classifier evaluation: HHH, scoped", dataClass("Recorded evaluation"),
        table(["Pillar", "Measured as", "Result (dev)"], r.classifier_hhh.map((x) => h("tr", {}, h("td", {}, h("b", {}, x.pillar)), h("td", {}, x.measures), h("td", {}, x.result)))),
        h("div", { class: "note" }, "Tool-use and autonomy questions are dropped for the classifier: it has no tools and takes no actions."),
        source("docs/uc4/responsible-ai.md §1")),
      card("Agent evaluation: HHH + APF", dataClass("Recorded evaluation"),
        table(["Measure", "Result (dev, offline planner)"], [
          ["Helpful (task completion)", pct(a.hhh.helpful)], ["Honest", a.hhh.honest === null ? "not computed (by design, D9.27)" : fmt(a.hhh.honest)],
          ["Harmless", pct(a.hhh.harmless)], ["APF effectiveness", fmt(a.apf.effectiveness)], ["APF efficiency", fmt(a.apf.efficiency)],
          ["APF reliability", a.apf.reliability === null ? "not computed" : fmt(a.apf.reliability)], ["APF trustworthiness", fmt(a.apf.trustworthiness)],
          ["APF composite", fmt(a.apf.composite)], ["Safety-invariant compliance", `${pct(a.safety_invariant_compliance)} (structural)`],
        ].map(([k, v]) => h("tr", {}, h("td", {}, k), h("td", {}, v)))),
        source("docs/uc4/results/agent-eval-dev.json"))),
    h("div", { class: "grid" }, card("Classifier evaluation: APF, scoped", dataClass("Recorded evaluation"),
      table(["Dimension", "Measured as", "Result (dev)"], r.classifier_apf.map((x) => h("tr", {}, h("td", {}, h("b", {}, x.dimension)), h("td", {}, x.measures), h("td", {}, x.result)))),
      source("docs/uc4/responsible-ai.md §1"))),
    h("div", { class: "grid" }, card("Guardrails and their real status", dataClass("Static documentation"),
      table(["Layer", "Guardrail", "Status", "Evidence"], r.guardrails.map((g) => {
        const [label, kind] = GUARD_STATUS[g.status] || GUARD_STATUS.unknown;
        return h("tr", {}, h("td", {}, g.layer), h("td", {}, h("b", {}, g.name), h("div", { class: "note" }, h("code", {}, g.where))),
          h("td", {}, statusBadge(label, kind)), h("td", { class: "note" }, g.evidence));
      })),
      h("div", { class: "note" }, "Statuses are derived from the completion report and decisions log, not typed into this page."),
      source("docs/uc4/responsible-ai.md §2", "docs/uc4/completion-report.md", "docs/uc4/decisions.md"))),
    h("div", { class: "grid cols-2" },
      card("Responsible AI pillars", dataClass("Static documentation"),
        h("div", { class: "status-list" }, r.pillars.map((p) => h("div", { class: "check-row" },
          h("div", {}, h("b", {}, p.pillar), h("div", { class: "note" }, p.implementation))))),
        source("docs/uc4/responsible-ai.md §4")),
      card("Fairness & Inclusion probe: its actual scope", dataClass("Recorded evaluation"),
        h("dl", { class: "kv" },
          h("dt", {}, "Test"), h("dd", {}, "same content, only a person's name swapped: does the classification change?"),
          h("dt", {}, "Result"), h("dd", {}, `${r.fairness.documents} documents × ${r.fairness.substitute_names} substitute names: ${r.fairness.result}`),
          h("dt", {}, "Mode"), h("dd", {}, statusBadge(`${r.fairness.mode} mode only`, "warn")),
          h("dt", {}, "Skipped"), h("dd", {}, `${r.fairness.skipped} documents with no detectable name`)),
        h("div", { class: "callout" }, h("b", {}, "Not claimed: "), r.fairness.not_claimed.join("; "), "."),
        source("docs/uc4/responsible-ai.md (Fairness & Inclusion)"))),
  );
}

// ---- Observability ---------------------------------------------------------------------------
async function renderObservability(page) {
  const env = await getJSON("/api/observability");
  applyMode(env);
  const o = env.data, O = state.obs, sess = o.session;
  if (!O.selected && sess.traces.length) O.selected = sess.traces[0].request_id;
  const sel = sess.traces.find((t) => t.request_id === O.selected);
  const table = (rows, cols) => h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, cols.map((c) => h("th", {}, c)))), h("tbody", {}, rows.map((r) => h("tr", {}, cols.map((c) => h("td", {}, r[c])))))));
  const picks = sess.traces.map((t) => {
    const b = h("button", { type: "button", class: t.request_id === O.selected ? "active" : "" },
      h("b", {}, t.root === "agent.document" ? "Agent run" : "Classification"), " · ", h("span", { class: "mono" }, t.request_id), ` · ${t.spans.length} spans`);
    b.addEventListener("click", () => { O.selected = t.request_id; route(); });
    return b;
  });
  page.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Observability"),
        h("p", { class: "lead" }, "Every request is one trace of redacted spans: no document text ever leaves the process. Exported to Azure Monitor and rendered in Microsoft Foundry's Tracing view.")),
      null),
    h("div", { class: "grid cols-3" }, o.foundry_status.map((f) => card(f.concern, dataClass("Static documentation"),
      h("div", { class: "badges" }, statusBadge(f.verdict, /verified|confirmed/i.test(f.verdict) && !/only|not/i.test(f.verdict) ? "good" : "warn")),
      h("details", {}, h("summary", {}, "Details"), h("p", { class: "note" }, f.status)), source("docs/uc4/observability-engine.md")))),
    h("div", { class: "grid cols-2" },
      card("This session's traces", dataClass(sess.data_class === "LIVE" ? "LIVE (this demo session)" : "REPLAY (this demo session)"),
        sess.traces.length ? h("div", { class: "trace-pick" }, picks)
          : h("div", { class: "empty-state" }, h("p", {}, "No traces yet. Classify a document or run the agent; their spans appear here.")),
        h("div", { class: "note" }, "Local telemetry from this demo session only. Not production traffic.")),
      card("Privacy by design", dataClass("Static configuration"),
        h("dl", { class: "kv" },
          h("dt", {}, "Allow-list"), h("dd", {}, `${o.allow_listed_keys} dg.* attribute keys; everything else is dropped (deny by default)`),
          h("dt", {}, "Document text"), h("dd", {}, "never exported: a content hash and byte count instead"),
          h("dt", {}, "Agent"), h("dd", {}, "fixed-vocabulary values only: no tool arguments, results or rationale"),
          h("dt", {}, "CI gate"), h("dd", {}, "the privacy audit fails the build on any leaked text")),
        source("config/observability/observability.v1.yaml"))),
    sel ? h("div", { class: "grid" }, card(`Trace waterfall: ${sel.request_id}`, dataClass(sess.data_class), waterfall(sel),
      h("div", { class: "note" }, sess.data_class === "REPLAY" ? "Durations are measured now; a replayed LLM call completes instantly (its recorded latency is on the Decision Trace page)." : ""),
      h("details", { class: "dev-only" }, h("summary", {}, "Span attributes (redacted)"),
        h("pre", { class: "json" }, JSON.stringify(sel.spans.map((sp) => ({ name: sp.name, attributes: sp.attributes })), null, 2))))) : null,
    h("div", { class: "grid cols-2" },
      card("Recorded baseline: stage latency (dev, 107 traces)", dataClass("Recorded evaluation / demo telemetry"),
        table(o.baseline.stage_latency, ["stage span", "spans", "errors", "P50", "P95"]),
        h("div", { class: "note" }, "LLM stage latency is the recorded latency of the benchmark run."),
        source("docs/uc4/results/observability-baseline.md")),
      card("Recorded baseline: privacy audit", dataClass("Recorded evaluation / demo telemetry"),
        table(o.baseline.privacy_audit, ["check", "result"]), source("docs/uc4/results/observability-baseline.md"))),
    h("div", { class: "grid" }, card("Recorded baseline: failure-injection matrix", dataClass("Recorded evaluation / demo telemetry"),
      table(o.baseline.failure_matrix, Object.keys(o.baseline.failure_matrix[0] || {})),
      o.baseline.header_predates_foundry_verification
        ? h("div", { class: "callout" }, h("b", {}, "Note: "), "this baseline report was generated before the Azure Monitor / Foundry tracing verification, so its header still says export is not verified. The current status is in the cards at the top of this page.")
        : null,
      source("docs/uc4/results/observability-baseline.md"))),
  );
}

function waterfall(t) {
  const W = 900, left = 190, right = 70, rowH = 22, top = 10;
  const end = Math.max(...t.spans.map((sp) => sp.offset_ms + sp.duration_ms), 0.001);
  const X = (ms) => left + (ms / end) * (W - left - right);
  const H = top + t.spans.length * rowH + 20;
  const depth = {};
  for (const sp of t.spans) depth[sp.id] = sp.parent && depth[sp.parent] !== undefined ? depth[sp.parent] + 1 : 0;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Span waterfall for this request" });
  svg.append(s("line", { x1: left, x2: left, y1: 0, y2: H - 16, class: "grid" }));
  t.spans.forEach((sp, i) => {
    const y = top + i * rowH, d = depth[sp.id] || 0;
    const kind = sp.status === "error" ? "err" : sp.name.startsWith("agent.") ? "agent" : (sp.name === "llm.call" || sp.name.startsWith("S3.")) ? "llm" : "";
    svg.append(
      s("text", { x: 4 + d * 12, y: y + 14 }, sp.name),
      s("rect", { x: X(sp.offset_ms), y: y + 4, width: Math.max(2, X(sp.offset_ms + sp.duration_ms) - X(sp.offset_ms)), height: 12, rx: 3, class: `bar ${kind}` }),
      s("text", { x: Math.min(X(sp.offset_ms + sp.duration_ms) + 6, W - right + 4), y: y + 14 }, `${sp.duration_ms.toFixed(2)} ms`));
  });
  svg.append(s("text", { x: left, y: H - 4 }, "0 ms"), s("text", { x: W - right, y: H - 4, "text-anchor": "end" }, `${end.toFixed(2)} ms`));
  return h("div", { class: "wf" }, svg);
}

// ---- Demo Mode -------------------------------------------------------------------------------
// Steps navigate and preload; they never press Analyze or Run, so no action (and no Azure/LLM cost
// in LIVE mode) happens without the presenter's click.
const DEMO_STEPS = [
  { page: "overview", say: "<b>The problem and the architecture.</b> A deterministic classification service, plus a separate, genuinely agentic triage agent." },
  { page: "classify", example: "healthcare", say: "<b>Classify a healthcare document.</b> Click <b>Analyze Document</b>: PHI, HIGHLY_CONFIDENTIAL, high-risk, with evidence." },
  { page: "trace", say: "<b>Explain the decision.</b> Rules found PHI, the LLM confirmed it, ML is off in this variant; nothing is inferred." },
  { page: "classify", example: "review", say: "<b>An ambiguous case with the LLM unavailable.</b> Click <b>Analyze Document</b>: it escalates to review with no label, never a default of PUBLIC." },
  { page: "review", say: "<b>Human in the loop.</b> The escalated case is in the queue; override it with a level and a note. Demo decisions never touch gold labels." },
  { page: "agent", say: "<b>The agent.</b> Click <b>Run Agent Triage</b>. Open the injection document: the instruction was treated as data, and the invariant held." },
  { page: "evaluations", say: "<b>Evaluations, honestly.</b> Strict lower bound 0.758 misses the 0.85 gate; the adopted lenient gate passes. Both are shown." },
  { page: "rai", say: "<b>Guardrails and Responsible AI.</b> Each guardrail with its real status, including what is only fake-server tested." },
  { page: "observability", say: "<b>Observability.</b> Redacted spans for the requests you just ran, verified in Microsoft Foundry's Tracing view." },
];

function interviewOn() {
  try { return localStorage.getItem("dg-interview") === "1"; } catch (_) { return false; }
}
function setInterview(on) {
  try { localStorage.setItem("dg-interview", on ? "1" : "0"); } catch (_) { /* per-viewer convenience only */ }
  applyInterview();
}
function demoStep() {
  try { return Math.max(0, Math.min(DEMO_STEPS.length - 1, Number(sessionStorage.getItem("dg-step") || 0))); } catch (_) { return 0; }
}
function goStep(i) {
  const step = DEMO_STEPS[i];
  try { sessionStorage.setItem("dg-step", String(i)); } catch (_) { /* ignore */ }
  if (step.example && state.examples) {
    const ex = state.examples.find((e) => e.key === step.example);
    if (ex) state.input = { example: ex.key, text: ex.content, filename: ex.filename, llmOff: ex.llm_tiers === "off", upload: null };
  } else if (step.example) {
    state.pendingExample = step.example;
  }
  if (location.hash === `#${step.page}`) route(); else location.hash = step.page;
  renderDemoBar();
}
function renderDemoBar() {
  const bar = document.getElementById("demo-bar");
  if (!interviewOn()) { bar.hidden = true; return; }
  bar.hidden = false;
  const i = demoStep(), step = DEMO_STEPS[i];
  const say = h("div", { class: "say" });
  // Trusted, static step copy (the only markup not built from text nodes); never data.
  say.innerHTML = step.say;
  const prev = h("button", { class: "btn btn-quiet", type: "button" }, "Back");
  prev.disabled = i === 0;
  prev.addEventListener("click", () => goStep(i - 1));
  const next = h("button", { class: "btn", type: "button" }, i === DEMO_STEPS.length - 1 ? "Finish" : "Next Demo Step");
  next.addEventListener("click", () => (i === DEMO_STEPS.length - 1 ? setInterview(false) : goStep(i + 1)));
  const start = h("button", { class: "btn btn-quiet", type: "button" }, "Start Demo");
  start.addEventListener("click", () => goStep(0));
  bar.replaceChildren(
    h("span", { class: "step-n" }, `Step ${i + 1} of ${DEMO_STEPS.length}`),
    h("span", { class: "demo-dots" }, DEMO_STEPS.map((_, k) => h("i", { class: k <= i ? "on" : "" }))),
    say, start, prev, next);
}
function applyInterview() {
  const on = interviewOn();
  document.body.classList.toggle("interview", on);
  const t = document.getElementById("interview-toggle");
  t.setAttribute("aria-pressed", on ? "true" : "false");
  renderDemoBar();
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
  document.getElementById("interview-toggle").addEventListener("click", () => {
    const on = !interviewOn();
    setInterview(on);
    if (on) goStep(demoStep());
  });
  applyInterview();
  route();
}
document.addEventListener("DOMContentLoaded", boot);
