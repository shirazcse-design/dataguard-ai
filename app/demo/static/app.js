"use strict";
// DataGuard AI interview demo. Every value on screen comes from the local demo server, which reads
// committed artifacts or calls the existing service. DOM is built with textContent only (no
// innerHTML from data), and every panel shows its data class and source.

const PAGES = {
  overview: { title: "Overview", phase: 4 },
  classify: { title: "Classify", phase: 2 },
  trace: { title: "Decision Trace", phase: 2 },
  agent: { title: "Agent Triage", phase: 3 },
  review: { title: "Human Review", phase: 3 },
  evaluations: { title: "Evaluations", render: renderEvaluations },
  rai: { title: "Responsible AI / Guardrails", phase: 4 },
  observability: { title: "Observability", phase: 4 },
};
const DEFAULT_PAGE = "evaluations";
const state = { status: null, metrics: null };

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
