import { SponsoredContent } from "./sponsored-content.js";

const $ = (id) => document.getElementById(id);
const STEPS = {
  pdf: [["uploading", "Upload"], ["scanning", "Scan"], ["analyzing", "Analyze"], ["policy_check", "Policy"],
        ["generating", "Generate"], ["completed", "Done"]],
  prompt: [["scanning", "Check message"], ["policy_check", "Policy"], ["thinking", "Think"], ["writing", "Write"],
           ["output_check", "Check answer"], ["completed", "Done"]],
};
let inputKind = "prompt";
const TERMINAL = new Set(["completed", "blocked", "error", "cancelled"]);
const SESSION_ID = (() => {
  try {
    let id = sessionStorage.getItem("drip-session");
    if (!id) { id = crypto.randomUUID().slice(0, 12); sessionStorage.setItem("drip-session", id); }
    return id;
  } catch { return "anon-" + Math.random().toString(36).slice(2, 10); }
})();

let current = null;     // { id, source, startedAt, timer }
const onClick = (ad) => current && post(`/api/jobs/${current.id}/click`, { ad_id: ad.id });
// Two surfaces: inline in the "Thinking..." line, or a card under it. Only one is ever visible.
const lineAd = new SponsoredContent($("status-ad"), { onClick });
const cardAd = new SponsoredContent($("sponsored"), { onClick });
const sponsored = {
  show(ad, format) {
    const [on, off] = format === "status_line" ? [lineAd, cardAd] : [cardAd, lineAd];
    off.hide();
    on.show(ad, format);
  },
  hide() { lineAd.hide(); cardAd.hide(); },
};

function buildStepper(kind) {
  $("stepper").replaceChildren(...STEPS[kind].map(([state, text]) => {
    const li = document.createElement("li");
    li.dataset.state = state;
    li.textContent = text;
    return li;
  }));
}

function setInputKind(kind) {
  inputKind = kind;
  $("tab-prompt").setAttribute("aria-selected", String(kind === "prompt"));
  $("tab-pdf").setAttribute("aria-selected", String(kind === "pdf"));
  $("pane-prompt").hidden = kind !== "prompt";
  $("pane-pdf").hidden = kind !== "pdf";
  $("start").textContent = kind === "prompt" ? "Send" : "Upload & summarize";
  $("result-h").textContent = kind === "prompt" ? "AI answer" : "AI summary";
  if (!current) buildStepper(kind);
}

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}

async function loadSamples() {
  const samples = await (await fetch("/api/samples")).json();
  const sel = $("sample");
  const groups = { benign: "Benign", attack: "Attacks", robustness: "Robustness" };
  for (const [label, name] of Object.entries(groups)) {
    const og = document.createElement("optgroup");
    og.label = name;
    for (const s of samples.filter((x) => x.label === label)) {
      const o = document.createElement("option");
      o.value = s.name;
      o.textContent = s.name.replaceAll("_", " ");
      og.append(o);
    }
    sel.append(og);
  }
  sel.value = "benign_travel";
}

function fileToB64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1]);
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}

function resetUI() {
  sponsored.hide();
  for (const id of ["result", "security", "review"]) $(id).hidden = true;
  $("timeline").replaceChildren();
  $("adlog").tBodies[0].replaceChildren();
  $("metrics").replaceChildren();
  for (const li of $("stepper").children) li.className = "";
}

function setState(state, label) {
  $("status-label").textContent = label;
  $("spinner").hidden = TERMINAL.has(state) || state === "review";
  $("status").dataset.state = state;
  const idx = [...$("stepper").children].findIndex((li) => li.dataset.state === state);
  [...$("stepper").children].forEach((li, i) => {
    li.className = idx < 0 ? li.className : i < idx ? "done" : i === idx ? "active" : "";
    if (state === "completed" && i === idx) li.className = "done";
  });
  if (["blocked", "error", "cancelled"].includes(state)) {
    const active = $("stepper").querySelector(".active");
    if (active) active.className = "failed";
  }
}

function timeline(ev) {
  const li = document.createElement("li");
  const t = document.createElement("code");
  t.textContent = `${Number(ev.t ?? 0).toFixed(2)}s`;
  const what = document.createElement("span");
  const detail = { state: ev.state, reason: ev.reason, ad: ev.ad?.id, format: ev.format, visible_s: ev.visible_s, action: ev.action };
  what.textContent = `${ev.type} ${JSON.stringify(Object.fromEntries(Object.entries(detail).filter(([, v]) => v !== undefined)))}`;
  li.className = `ev ev--${ev.type}`;
  li.append(t, what);
  $("timeline").append(li);
}

function dl(target, rows) {
  target.replaceChildren();
  for (const [k, v, cls] of rows) {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v; if (cls) dd.className = cls;
    target.append(dt, dd);
  }
}

function showSecurity(sec) {
  $("security").hidden = false;
  dl($("security-dl"), [
    ["Decision", sec.action.toUpperCase(), `badge badge--${sec.action}`],
    ["Reason", sec.reason],
    ["Category", sec.category],
    ["Sensitivity", sec.sensitivity.toUpperCase()],
    ["PII masked", sec.pii_types.length ? sec.pii_types.join(", ") : "none"],
    ["Engine", sec.source === "sieve" ? "Sieve adapter" : "Mock security gateway"],
  ]);
}

function showResult(ev) {
  const m = ev.metrics;
  const fmt = (v, unit = "s") => (v === null || v === undefined ? "—" : `${Number(v).toFixed(unit === "ms" ? 0 : 1)}${unit}`);
  const adMs = m.ad_decision_ms?.length ? Math.max(...m.ad_decision_ms) : null;
  dl($("metrics"), [
    ["Security latency", fmt(m.security_s)],
    ["LLM latency", fmt(m.llm_s)],
    ["Total", fmt(m.total_s)],
    ["Predicted wait", fmt(m.estimated_wait_s)],
    ["Predicted answer", ev.prediction ? `${ev.prediction.output_tokens} tokens (${ev.prediction.size})` : "—"],
    ["Actual answer", ev.actual_output_tokens ? `${ev.actual_output_tokens} tokens` : "—"],
    ["Why", ev.prediction ? ev.prediction.reasons.join(", ") : "—"],
    ["Sponsors shown", String(m.creatives_shown)],
    ["Ad decision latency", fmt(adMs, "ms")],
    ["Security compute (real)", fmt(m.security_compute_ms, "ms")],
    ["Ad shown", m.ad_shown ? "YES" : "NO", m.ad_shown ? "yes" : "no"],
    ["Ad visible", fmt(m.ad_visible_s)],
    ["Billable impressions", String(m.impressions)],
    ["Ad reason", m.decision_reason],
    ["Slot closed by", m.closed_reason ?? "—"],
    ["Sensitivity", (ev.security?.sensitivity ?? "—").toUpperCase()],
    ["Strategy", ev.strategy],
    ["Ad surface", ev.surface === "card" ? "card" : "status line"],
    ["Input", ev.kind],
    ["Security mode", ev.mode],
  ]);
  const tbody = $("adlog").tBodies[0];
  tbody.replaceChildren();
  for (const row of ev.ad_log) {
    const tr = document.createElement("tr");
    const s = row.signal;
    const signal = (row.sent_to_ad_engine ? "" : "(not sent: withdrawn locally) ") + `state=${s.state} phase=${s.phase} mode=${s.ad_mode} category=${s.category ?? "∅"} sensitivity=${s.sensitivity ?? "∅"} wait≈${s.estimated_wait_s}s tier=${s.tier}`;
    const d = row.decision;
    const decision = d.show_ad ? `SHOW ${d.ad_id} (${d.format}, ${d.reason})` : `NO AD (${d.reason})`;
    for (const v of [`${row.t.toFixed(2)}s`, row.phase, signal, decision]) {
      const td = document.createElement("td"); td.textContent = v; tr.append(td);
    }
    tbody.append(tr);
  }
}

function handle(ev) {
  if (ev.type !== "result") timeline(ev);
  switch (ev.type) {
    case "job":
      if (ev.prediction) $("status-label").textContent = `Expected wait ≈ ${ev.estimated_wait_s}s (${ev.prediction.size} answer)`;
      break;
    case "state":
      setState(ev.state, ev.reason && ev.state !== "completed" ? `${ev.label} (${ev.reason})` : ev.label);
      if (ev.state === "review") $("review").hidden = false;
      if (TERMINAL.has(ev.state)) { sponsored.hide(); $("review").hidden = true; }
      break;
    case "security":
      showSecurity(ev);
      if (ev.action === "review") $("review-reason").textContent = `Reason: ${ev.reason}. The document was only partly inspected or contains suspicious text.`;
      break;
    case "ad_show":
      sponsored.show(ev.ad, ev.format);
      break;
    case "ad_hide":
      sponsored.hide();
      break;
    case "output_check":
      if (ev.findings?.length) timeline({ t: ev.t, type: "output_guard", reason: ev.findings.map((f) => f.detail).join(", ") });
      break;
    case "summary":
      $("summary").textContent = ev.text;
      $("result").hidden = false;
      break;
    case "result":
      showResult(ev);
      break;
  }
}

async function start() {
  if (current) return;
  resetUI();
  const [kind, value] = ($("failure").value || ":").split(":");
  buildStepper(inputKind);
  const body = {
    instruction: $("instruction").value,
    strategy: $("strategy").value,
    surface: $("surface").value,
    mode: $("mode").value,
    security_s: $("security_s").value === "auto" ? "auto" : Number($("security_s").value),
    llm_s: $("llm_s").value === "auto" ? "auto" : Number($("llm_s").value),
    ad_failure: kind === "ad" ? value : null,
    llm_failure: kind === "llm" ? value : null,
    security_hang: kind === "security",
    session_id: SESSION_ID,
    tier: $("tier").value,
    consent: $("consent").checked,
  };
  if (inputKind === "prompt") {
    body.prompt = $("prompt").value;
  } else {
    const file = $("file").files[0];
    if (file) body.file_b64 = await fileToB64(file);
    else body.sample = $("sample").value;
  }

  const { job_id, error } = await post("/api/jobs", body);
  if (error) { setState("error", error); return; }
  const source = new EventSource(`/api/jobs/${job_id}/events`);
  const startedAt = performance.now();
  const timer = setInterval(() => { $("elapsed").textContent = `${((performance.now() - startedAt) / 1000).toFixed(1)}s`; }, 100);
  current = { id: job_id, source, timer };
  $("start").disabled = true;
  $("cancel").disabled = false;
  source.onmessage = (m) => handle(JSON.parse(m.data));
  const done = () => {
    source.close(); clearInterval(timer); current = null;
    $("start").disabled = false; $("cancel").disabled = true;
  };
  source.addEventListener("end", done);
  source.onerror = () => { if (current?.id === job_id) { setState("error", "Connection lost"); sponsored.hide(); done(); } };
}

$("start").addEventListener("click", start);
$("cancel").addEventListener("click", () => current && post(`/api/jobs/${current.id}/cancel`, {}));
$("review-yes").addEventListener("click", () => { $("review").hidden = true; current && post(`/api/jobs/${current.id}/review`, { proceed: true }); });
$("review-no").addEventListener("click", () => { $("review").hidden = true; current && post(`/api/jobs/${current.id}/review`, { proceed: false }); });
$("reset-session").addEventListener("click", () => post("/api/session/reset", { session_id: SESSION_ID }));
$("file").addEventListener("change", () => { $("sample").disabled = $("file").files.length > 0; });
$("tab-prompt").addEventListener("click", () => setInputKind("prompt"));
$("tab-pdf").addEventListener("click", () => setInputKind("pdf"));
for (const chip of document.querySelectorAll(".chip")) chip.addEventListener("click", () => { $("prompt").value = chip.dataset.prompt; });
setInputKind("prompt");
const syncDebug = () => { $("debug-panel").hidden = !$("debug").checked; };
$("debug").addEventListener("change", syncDebug);
syncDebug();
loadSamples();
