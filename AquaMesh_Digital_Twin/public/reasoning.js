/* AquaMesh reasoning view.  Everything shown here is produced by the Python engine (engine/); this file only draws it.
   Truth values:  T true · F false · U unknown now (never treated as safe) · N sensor not installed (rule ignored, but reported) */
(() => {
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const VN = { T: "TRUE", F: "FALSE", U: "UNKNOWN", N: "NOT INSTALLED" };
const STYLE = { // node fill / stroke / text by truth value
  T: ["#dcebe4", "#3c8270", "#1f4d3d"], F: ["#f3f1e8", "#cbc8ba", "#8a8f82"],
  U: ["#fbefd6", "#d88a2c", "#7a5410"], N: ["#fbfaf6", "#b9b5a6", "#a5a99b"] };
const STATE_COL = { UNASSESSED: "#c9c6b8", NORMAL: "#6fa56f", WATCH: "#e3c24a", WARNING: "#e08e30", CRITICAL: "#c9473a" };
const HEALTH_COL = { OK: "#6fa56f", PARTIAL_COVERAGE: "#b8cfa8", DEGRADED: "#e08e30", SENSOR_FAULT: "#c9473a" };
const DOMAIN_ORDER = ["link", "rain", "soil", "gas", "hydro", "flood", "health"];
const PRESETS = [
  ["No drain blockage", { drain_level_high: "F", drain_flow_low: "F", overflow_contact: "F" }],
  ["Rain stops", { rain_wet: "F" }],
  ["Soil is dry", { soil_wet_high: "F" }],
  ["River is low", { river_level_high: "F" }],
  ["Rain node offline", { rain_node_fresh: "F" }],
];
const R = { sel: null, replayId: null, replay: null, proof: null, tl: null, tlAt: 0, cf: null, cfName: "", evidence: null, lastProofAt: 0, graphSig: "" };
const ACKNOWLEDGED = new Set();
let alertFilter = "all";
let LASTSNAP = null;

const active = () => $("reasonView") && $("reasonView").classList.contains("active");
const j = async (u, o) => { try { const r = await fetch(u, o); return await r.json(); } catch (e) { return null; } };
const pv = (t, extra = "") => `<span class="pv ${esc(t)}">${esc(String(t).replace(/_/g, " "))}${extra}</span>`;
const clock = t => new Date(t * 1000).toLocaleTimeString("en-IN", { hour12: false });
const A = () => (LASTSNAP && LASTSNAP.assessment) || null;

/* ───────────── top strip: states, hysteresis, and data quality ───────────── */
function renderTop(a) {
  const h = a.hysteresis, hys = d => d.pending ? `candidate ${d.pending} · ${d.pending_for_s}s of ${d.needs_s}s needed` : "stable";
  const chip = (k, st, sub, col) => `<div class="rs-chip cut s-${st}"><small>${k}</small><b style="color:${col || "inherit"}">${st.replace(/_/g, " ")}</b><br><em>${esc(sub)}</em></div>`;
  const q = Object.entries(a.quality).map(([k, v]) => `<span class="pv ${v}" title="${k}">${k.toUpperCase()}: ${String(v).replace(/_/g, " ")}</span>`).join("");
  const na = a.not_assessable.length ? `<div class="rs-chip cut s-UNASSESSED"><small>CANNOT BE ASSESSED</small><em>${a.not_assessable.map(esc).join(" · ")}</em><br><em>missing sensors are never read as zero</em></div>` : "";
  $("rsTop").innerHTML =
    chip("FLOOD STATE", a.flood_state, hys(h.flood), "") + chip("GAS STATE (separate)", a.gas_state, hys(h.gas), "") + chip("SYSTEM HEALTH (separate)", a.health, "coverage " + a.evidence_coverage_pct + "% of applicable evidence", "") +
    `<div class="rs-chip cut"><small>SOURCE</small><b>LIVE HARDWARE</b><br><em>rules ${esc(a.kb_version)} · ${esc(a.calibration)}</em></div>` + na +
    `<div class="rs-chip cut" style="flex:1"><small>DATA QUALITY</small><div class="rs-q">${q}</div></div>`;
}

/* ───────────── inference network (layered DAG) ───────────── */
function layout(g) {
  const cols = {}; let mx = 0;
  g.nodes.forEach(n => { (cols[n.layer] = cols[n.layer] || []).push(n); mx = Math.max(mx, n.layer); });
  const W = 760, H = 430, NW = 132, NH = 19, pos = {};
  Object.entries(cols).forEach(([L, arr]) => {
    arr.sort((a, b) => DOMAIN_ORDER.indexOf(a.domain) - DOMAIN_ORDER.indexOf(b.domain) || a.id.localeCompare(b.id));
    const pitch = Math.min(24, (H - 20) / arr.length), x = 6 + (W - NW - 12) * (mx ? L / mx : 0);
    const y0 = 10 + (H - 20 - pitch * arr.length) / 2;
    arr.forEach((n, i) => { pos[n.id] = { x, y: y0 + i * pitch, w: NW, h: NH }; });
  });
  return pos;
}
function ancestors(g, id) {
  const up = {}; g.edges.forEach(e => (up[e.to] = up[e.to] || []).push(e));
  const seen = new Set([id]), es = new Set(), st = [id];
  while (st.length) { const x = st.pop(); (up[x] || []).forEach(e => { es.add(e.from + ">" + e.to + ">" + e.rule); if (!seen.has(e.from)) { seen.add(e.from); st.push(e.from); } }); }
  return { nodes: seen, edges: es };
}
function renderGraph(g) {
  const pos = layout(g), anc = R.sel ? ancestors(g, R.sel) : null, svg = $("rsSvg");
  let out = "";
  g.edges.forEach(e => {
    const a = pos[e.from], b = pos[e.to]; if (!a || !b) return;
    const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2, mx = (x1 + x2) / 2;
    const v = e.rule_value, key = e.from + ">" + e.to + ">" + e.rule;
    const col = v === "T" ? "#3c8270" : v === "U" ? "#d88a2c" : v === "N" ? "#d8d5c8" : "#d3d0c2";
    const op = anc ? (anc.edges.has(key) ? 1 : .08) : (v === "T" ? .9 : v === "F" ? .35 : .55);
    out += `<path class="ed ${v === "T" ? "fire" : ""}" d="M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}" stroke="${col}" stroke-width="${v === "T" ? 2.2 : 1}" ${v === "U" || v === "N" ? 'stroke-dasharray="3 3"' : ""} opacity="${op}"><title>${e.rule} (${VN[v]})${e.want ? "" : " · premise must be FALSE"}</title></path>`;
    if (!e.want) out += `<text x="${x2 - 9}" y="${y2 - 3}" font-size="10" fill="${col}" opacity="${op}">¬</text>`;
  });
  g.nodes.forEach(n => {
    const p = pos[n.id], st = STYLE[n.value] || STYLE.U, dim = anc && !anc.nodes.has(n.id) ? .25 : 1;
    const badge = n.kind === "evidence" ? (n.prov === "SIMULATED" ? "S" : n.prov === "MEASURED" ? "M" : n.prov === "ESTIMATED" ? "E" : "–") : "";
    out += `<g class="nd ${R.sel === n.id ? "sel" : ""}" data-id="${n.id}" opacity="${dim}"><title>${esc(n.label)} = ${VN[n.value]}${n.detail ? " · " + esc(n.detail) : ""}${n.prov && n.prov !== "DERIVED" ? " · " + n.prov : ""}</title>
      <rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="5" fill="${st[0]}" stroke="${st[1]}" stroke-width="${n.kind === "derived" ? 1.8 : 1.1}" ${n.value === "U" || n.value === "N" ? 'stroke-dasharray="4 3"' : ""}/>
      <text x="${p.x + 7}" y="${p.y + 13.5}" fill="${st[2]}" ${n.kind === "derived" ? 'font-weight="800"' : ""}>${esc(n.label.length > 24 ? n.label.slice(0, 23) + "…" : n.label)}</text>
      ${badge ? `<text x="${p.x + p.w - 10}" y="${p.y + 13.5}" fill="${st[1]}" font-size="8" font-family="DM Mono">${badge}</text>` : ""}</g>`;
  });
  svg.innerHTML = out;
  svg.querySelectorAll(".nd").forEach(el => el.onclick = () => selectGoal(el.dataset.id));
}
function renderLegend() {
  $("rsLegend").innerHTML = ["T", "F", "U", "N"].map(k => `<span><i style="background:${STYLE[k][0]};border-color:${STYLE[k][1]};${k === "U" || k === "N" ? "border-style:dashed" : ""}"></i>${VN[k]}</span>`).join("") +
    `<span>M measured · E estimated · S simulated · – unavailable</span><span>animated = rule fired · ¬ = must be false</span>`;
}

/* ───────────── backward chaining proof ───────────── */
function treeHtml(n, want) {
  const val = `<span class="dot ${n.value}"></span><b>${esc(n.label)}</b> <span class="rl">${VN[n.value]}${want === false ? " (needed FALSE)" : ""}${n.kind === "evidence" ? " · " + esc(n.provenance) : ""}</span>${n.detail ? `<div class="rl" style="margin-left:14px">${esc(n.detail)}</div>` : ""}`;
  if (n.kind === "evidence") return `<div style="margin-left:10px">${val}</div>`;
  const open = n.value === "T" || n.value === "U";
  let inner = "";
  n.via.forEach(v => {
    const o2 = v.value === "T" || v.value === "U";
    inner += `<details ${o2 ? "open" : ""}><summary><span class="dot ${v.value}"></span><span class="rl">${v.rule} · ${esc(v.text)} → ${VN[v.value]}</span></summary>${v.premises.map(p => treeHtml(p, p.wanted)).join("")}</details>`;
  });
  return `<details ${open ? "open" : ""}><summary>${val}</summary>${inner}</details>`;
}
function renderProof() {
  const p = R.proof;
  if (R.evidence) {
    const e = R.evidence;
    $("rsProofTitle").textContent = "Alert evidence (stored)"; $("rsProofTag").textContent = "EVENT #" + e.id;
    const sup = Object.entries(e.evidence.supporting || {}).map(([k, v]) => `<div><span class="dot ${v.value}"></span><b>${esc(k)}</b> <span class="rl">${esc(v.provenance)} · ${esc(v.detail)}</span></div>`).join("");
    $("rsProof").innerHTML = `<div class="why"><b>${esc(e.title)}</b><br>${clock(e.ts)} · ${esc(e.evidence.mode)} · rules ${esc(e.evidence.kb_version)} · ${esc(e.evidence.calibration)}<br>reasons: ${(e.evidence.reason_codes || []).map(x => pv(x)).join(" ")}</div>${sup || "<i>no supporting evidence recorded</i>"}<div style="margin-top:6px" class="rl">data quality at the time: ${Object.entries(e.evidence.data_quality || {}).map(([k, v]) => k + "=" + v).join(" · ")}</div>
      <div class="rl">not assessable then: ${(e.evidence.not_assessable || []).join(", ") || "–"}</div><div class="alert-why" id="rsBackProof">→ prove this goal on current data</div>`;
    const b = $("rsBackProof"); if (b) b.onclick = () => { R.evidence = null; selectGoal(e.goal); };
    return;
  }
  $("rsProofTitle").textContent = "Why? (backward chaining)"; $("rsProofTag").textContent = "GOAL-DRIVEN";
  if (!p || p.error) { $("rsProof").innerHTML = "Click a node in the network or an alert to see its proof."; return; }
  const head = p.value === "T" ? `<b>${esc(p.tree.label)}</b> is TRUE because a rule's premises all hold:` : p.value === "F" ? `<b>${esc(p.tree.label)}</b> is FALSE — every rule is blocked:` :
    p.value === "U" ? `<b>${esc(p.tree.label)}</b> is UNKNOWN — it cannot be decided without:` : `<b>${esc(p.tree.label)}</b> does not apply: it needs a sensor that is not installed.`;
  const miss = p.missing_evidence.length ? `<div style="margin-top:3px">${p.missing_evidence.map(m => `<span class="miss">${esc(m)}</span>`).join("")}</div>` : "";
  $("rsProof").innerHTML = `<div class="why">${head}${miss}</div>${treeHtml(p.tree)}`;
}
async function selectGoal(id) {
  R.sel = id; R.evidence = null; R.proof = await j("/api/twin/why?goal=" + encodeURIComponent(id));
  renderProof(); const g = (R.replay ? R.replay.graph : (A() && A().graph)); if (g) renderGraph(g);
}

/* ───────────── hypotheses, trace ───────────── */
function renderHyp(h) {
  $("rsHyp").innerHTML = h.slice(0, 3).map((x, i) => `<div class="hyp ${i === 0 ? "top" : ""}"><span>${esc(x.label)}</span><b>${x.support.toFixed(0)}%</b><i style="--p:${x.support}%"></i>${x.for.length ? `<small>for: ${x.for.map(f => f.fact + " ×" + f.lr).join(", ")}</small>` : `<small>${x.evidence_used ? "no supporting evidence" : "no evidence available (prior only)"}</small>`}</div>`).join("") +
    `<div class="rl" style="font:9px 'DM Mono';color:#8a8f82">Likelihood-ratio diagnosis. UNKNOWN facts add no evidence. Not a calibrated probability.</div>`;
}
function renderTrace(tr, rounds) {
  $("rsTraceTag").textContent = `${tr.length} firings · fixpoint in ${rounds} rounds`;
  $("rsTrace").innerHTML = tr.length ? tr.map(t => `<div><b>R${t.round}</b><span><span class="dot ${t.to}"></span>${esc(t.fact)} ${t.from}→${t.to}</span><span style="color:#8a8f82">${esc(t.by.slice(0, 3).join(","))}</span></div>`).join("") : "<i>no derived facts yet</i>";
}

/* ───────────── decision history, replay ───────────── */
async function renderTimeline() {
  if (Date.now() - R.tlAt < 4000 && R.tl) return drawTimeline();
  R.tlAt = Date.now(); R.tl = await j("/api/twin/timeline?minutes=20"); drawTimeline();
}
function drawTimeline() {
  const d = R.tl; if (!d) return;
  const P = d.points; let svg = "";
  if (P.length > 1) {
    const t0 = P[0].ts, t1 = P[P.length - 1].ts, W = 600, x = t => 4 + (W - 8) * (t - t0) / Math.max(1, t1 - t0);
    const strip = (key, col, y, h) => P.forEach((p, i) => { const nx = i < P.length - 1 ? x(P[i + 1].ts) : W - 4; svg += `<rect x="${x(p.ts)}" y="${y}" width="${Math.max(1.5, nx - x(p.ts))}" height="${h}" fill="${col(p[key])}"><title>${clock(p.ts)} ${p[key]}</title></rect>`; });
    strip("flood_state", s => STATE_COL[s] || "#ccc", 4, 20); strip("gas_state", s => STATE_COL[s] || "#ccc", 27, 8); strip("health", s => HEALTH_COL[s] || "#ccc", 37, 6);
    if (R.replayId) { const rp = P.find(p => p.id === R.replayId); if (rp) svg += `<line x1="${x(rp.ts)}" x2="${x(rp.ts)}" y1="0" y2="46" stroke="#4a3c85" stroke-width="2"/>`; }
    $("rsTimeline").innerHTML = `<svg class="tl-svg" viewBox="0 0 600 46" preserveAspectRatio="none" id="tlSvg">${svg}</svg><div class="rl" style="font:9px 'DM Mono';color:#8a8f82">top: flood · middle: gas · bottom: health · click to replay that moment · ${clock(t0)} → ${clock(t1)}</div>` + trList(d.transitions);
    $("tlSvg").onclick = e => { const r = e.currentTarget.getBoundingClientRect(), t = t0 + (t1 - t0) * (e.clientX - r.left) / r.width; const p = P.reduce((a, b) => Math.abs(b.ts - t) < Math.abs(a.ts - t) ? b : a); startReplay(p.id); };
  } else $("rsTimeline").innerHTML = "<i>collecting history…</i>" + trList(d.transitions);
  $("rsTimeline").querySelectorAll(".tl-tr").forEach(el => el.onclick = async () => {
    if (el.dataset.aid && el.dataset.aid !== "null") startReplay(+el.dataset.aid);
    const ev = (await j("/api/twin/events?limit=80") || []).find(e => Math.abs(e.ts - +el.dataset.ts) < 1.5 && e.goal);
    if (ev) { const full = await j("/api/twin/event?id=" + ev.id); if (full) { R.evidence = full; renderProof(); } }
  });
}
const trList = tr => (tr || []).slice(0, 8).map(t => `<div class="tl-tr" data-aid="${t.assessment_id}" data-ts="${t.ts}"><b>${clock(t.ts)}</b><span>${t.domain.toUpperCase()} ${t.from_state} → <b>${t.to_state}</b><br><small>${esc(JSON.parse(t.reasons_json || "[]").join(" · "))}</small></span></div>`).join("");
async function startReplay(id) {
  const r = await j("/api/twin/replay?id=" + id); if (!r || r.error) return;
  R.replayId = id; R.replay = r; paint();
}
function stopReplay() { R.replayId = null; R.replay = null; paint(); }

/* ───────────── counterfactual ───────────── */
function stateFrom(af) { return af.flood_critical === "T" ? "CRITICAL" : af.flood_warning === "T" ? "WARNING" : af.flood_watch === "T" ? "WATCH" : af.flood_normal === "T" ? "NORMAL" : "UNASSESSED"; }
function renderCf() {
  const aid = R.replayId || (A() && A().assessment_id) || "";
  let html = PRESETS.map(([n], i) => `<span class="cf-b ${R.cfName === n ? "on" : ""}" data-i="${i}">${n}</span>`).join("");
  if (R.cf) {
    const c = R.cf, b = stateFrom(c.before), a = stateFrom(c.after);
    html += `<div class="cf-res"><div class="arrow">${b} → <b>${a}</b> ${a === b ? "(unchanged)" : ""}</div><div style="margin-top:4px">${c.changed.length ? c.changed.map(x => `<div><span class="dot ${x.before}"></span>${esc(x.label)}: ${VN[x.before]} → <b>${VN[x.after]}</b></div>`).join("") : "No conclusion changes."}</div>
      <div class="rl" style="margin-top:5px;font:9px 'DM Mono';color:#8a8f82">Same recorded inputs, different assumption. A model experiment — not proof that this factor caused a real event.</div></div>`;
  } else html += `<div class="rl" style="font:10px 'DM Mono';color:#8a8f82">Pick an assumption: the engine re-runs forward chaining on the ${R.replayId ? "replayed" : "current"} recorded facts.</div>`;
  $("rsCf").innerHTML = html;
  $("rsCf").querySelectorAll(".cf-b").forEach(el => el.onclick = async () => {
    const [n, ov] = PRESETS[+el.dataset.i]; R.cfName = n;
    R.cf = await j("/api/twin/counterfactual", { method: "POST", body: JSON.stringify({ overrides: ov, assessment_id: R.replayId || null }) }); renderCf();
  });
}

/* ───────────── paint the whole view ───────────── */
function paint() {
  const live = A(); if (!live) { $("rsTop").innerHTML = '<div class="rs-chip cut"><small>ENGINE</small><b>NO DATA</b><br><em>gateway API not responding</em></div>'; return; }
  const rp = R.replay, a = rp ? Object.assign({}, live, { flood_state: rp.assessment.flood_state, gas_state: rp.assessment.gas_state, health: rp.assessment.health }) : live;
  renderTop(live);
  const ban = rp ? `<div class="rs-banner" id="rsBan">⏪ REPLAY of ${clock(rp.assessment.ts)} · flood ${rp.assessment.flood_state} · ${esc(rp.assessment.calib)} · click to return to live</div>` : "";
  $("rsGraphTag").textContent = rp ? "REPLAYED FROM SQLITE · same rules, recorded facts" : "EVIDENCE → RULES → CONCLUSIONS · click any node for its proof";
  const g = rp ? rp.graph : live.graph;
  renderGraph(g); renderLegend(); renderHyp(rp ? rp.hypotheses : live.hypotheses); renderTrace(rp ? rp.trace : live.trace, rp ? rp.rounds : live.rounds); renderCf();
  const top = $("rsTop"); if (ban) { top.insertAdjacentHTML("afterbegin", ban.replace('class="rs-banner"', 'class="rs-banner" style="width:100%"')); $("rsBan").onclick = stopReplay; }
  renderTimeline();
}

/* ───────────── hooks called by app.js ───────────── */
const AQR = window.AQR = {
  render(S) { // called every live tick: tiles + map banner + (if visible) the reasoning view
    LASTSNAP = S; const a = S && S.assessment; if (!a) return;
    const rs = a.flood_state, col = STATE_COL[rs] || "#ccc", gas = a.gas_state;
    $("riskLabel").textContent = "Flood state · rule engine"; $("riskValue").textContent = a.risk_index == null ? "–" : a.risk_index;
    $("riskBadge").textContent = rs; $("riskBadge").style.background = col + "44"; $("riskBadge").style.color = "#33402f";
    $("riskNote").textContent = rs === "UNASSESSED" ? "Cannot assess: evidence missing" : "Evidence coverage " + a.evidence_coverage_pct + "%";
    $("riskTrend").textContent = "heuristic score";
    $("riskMeter").style.width = (a.risk_index || 0) + "%";
    const m = a.metrics, N = S.nodes, R1 = N["RAIN-01"], cal = R1 && R1.rain_mm_h != null;
    $("rainLabel").textContent = cal ? "Rainfall intensity (est.)" : "Rain wetness (raw ADC)";
    $("rainValue").textContent = cal ? R1.rain_mm_h.toFixed(1) : (R1 && R1.rain_raw != null ? R1.rain_raw : "–"); $("rainUnit").textContent = cal ? "mm/h" : "raw";
    const A1 = N["AREA-01"], sk = A1 && A1.soil_moisture_pct != null;
    $("riverLabel").textContent = sk ? "Soil moisture" : "Soil probe (raw ADC)";
    $("riverValue").textContent = sk ? A1.soil_moisture_pct.toFixed(0) : (A1 && A1.soil_raw != null ? A1.soil_raw : "–"); $("riverUnit").textContent = sk ? "%" : "raw";
    $("subRisk").textContent = "LIVE HEURISTIC SCORE · not a probability"; $("subRain").textContent = "quality: " + a.quality.rain.replace(/_/g, " ");
    $("subRiver").textContent = "quality: " + a.quality.soil.replace(/_/g, " ");
    const txt = rs === "UNASSESSED" ? "Flood risk cannot be assessed · check rain node" : rs === "CRITICAL" ? "CRITICAL · " + a.alerts[0].reasons.slice(0, 2).join(", ") : rs === "WARNING" ? "Flood WARNING · rain sustained with supporting evidence" : rs === "WATCH" ? "Flood WATCH · early-warning rule met" : gas === "WARNING" ? "Gas hazard (separate from flood)" : "Flood: normal for installed sensors";
    $("mapAlertText").textContent = txt;
    $("mapAlertDot").className = rs === "CRITICAL" || gas === "WARNING" ? "crit" : (rs === "WARNING" || rs === "WATCH" || rs === "UNASSESSED") ? "warn" : "";
    $("footerTime").textContent = "FLOOD " + rs + " · GAS " + gas + " · " + a.health.replace(/_/g, " ");
    if (active() && !R.replay) paint();
    else if (active() && R.replay) { renderTop(a); }
    if (active() && R.sel && Date.now() - R.lastProofAt > 2500 && !R.evidence && !R.replay) { R.lastProofAt = Date.now(); j("/api/twin/why?goal=" + R.sel).then(p => { R.proof = p; renderProof(); }); }
  },
  alerts(S) {
    const a = S.assessment, items = [], sym = { critical: "!", warn: "△", info: "✓" };
    const sev = l => ({ CRITICAL: "critical", WARNING: "warn", WATCH: "warn", FAULT: "critical", DEGRADED: "warn", UNASSESSED: "warn", UNRESOLVED: "warn" }[l] || "info");
    a.alerts.forEach(x => items.push({ s: sev(x.level), t: x.title + " · " + x.level, reasons: x.reasons || [], goal: x.goal, key: [x.domain, x.goal, x.level].join("|") }));
    if (!a.alerts.length) items.push({ s: "info", t: "System normal for installed sensors", reasons: ["NO_WARNING_CONDITION"], goal: "flood_normal", key: "system-normal" });
    if ((a.not_assessable || []).length) items.push({ s: "info", t: "Not assessable: " + a.not_assessable.join(", "), reasons: ["SENSOR_NOT_INSTALLED"], goal: "river_backwater", key: "not-assessable" });
    $("alertCount").textContent = a.alerts.filter(x => !["UNRESOLVED"].includes(x.level)).length + " ACTIVE";
    const shown = items.filter(x => alertFilter === "all" || x.s === alertFilter);
    document.querySelectorAll("[data-alert-filter]").forEach(b => { const on = b.dataset.alertFilter === alertFilter; b.setAttribute("aria-pressed", String(on)); b.classList.toggle("active", on); });
    $("alertList").innerHTML = shown.slice(0, 6).map(x => { const ack = ACKNOWLEDGED.has(x.key); return `<div class="alert-item"><span class="alert-symbol ${x.s}">${sym[x.s]}</span><div class="alert-copy"><b>${esc(x.t)}</b><div class="rc">${x.reasons.map(r => pv(r.split(":")[0].length > 30 ? "EVIDENCE" : r.replace(/ /g, "_"))).join("")}</div><span class="alert-why" data-g="${esc(x.goal)}">WHY? → open proof</span><button type="button" class="alert-ack ${ack ? "done" : ""}" data-ack="${esc(x.key)}" ${ack ? "disabled" : ""}>${ack ? "Acknowledged · local demo" : "Acknowledge · local demo"}</button></div><span class="alert-time">${new Date().toLocaleTimeString("en-IN", { hour12: false })}</span></div>`; }).join("") || `<p class="empty-note">No alerts match this severity filter.</p>`;
    $("alertList").querySelectorAll(".alert-why").forEach(el => el.onclick = () => { window.AQ.go(8); selectGoal(el.dataset.g); });
    $("alertList").querySelectorAll(".alert-ack:not(:disabled)").forEach(el => el.onclick = () => { ACKNOWLEDGED.add(el.dataset.ack); if (LASTSNAP) AQR.alerts(LASTSNAP); });
  },
  infer(S, I) {
    const a = S.assessment, ind = a.indicators, f = v => v == null ? "–" : (Math.abs(v) >= 100 ? Math.round(v) : +(+v).toFixed(1));
    const row = x => x.value == null ? `<div class="m"><span>${esc(x.label)}</span><b style="font-size:12px;color:#8a8f82">${pv(x.type)}</b><i style="--p:0%"></i><span style="grid-column:1/3;font:9px 'DM Mono';color:#8a8f82">${esc(x.why || "")}</span></div>` :
      `<div class="m"><span>${esc(x.label)} ${pv(x.type)}</span><b>${f(x.value)}<small>${esc(x.unit)}</small></b><i style="--p:${Math.min(100, x.unit === "raw" ? x.value / 20 : x.value)}%"></i></div>`;
    const sec = (t, tag, rows) => `<article class="card cut"><div class="c-head"><h2>${t}</h2><span class="tiny">${tag}</span></div><div class="ms">${rows.join("")}</div></article>`;
    const p = a.pattern, pm = p.status === "MATCH" ? `${p.best.label} (${p.best.similarity}% similar)` : p.status === "NO_KNOWN_PATTERN" ? "no known pattern (rejected: too dissimilar)" : "not enough data yet";
    $("infGrid").innerHTML =
      sec("Observed", "WHAT THE HARDWARE MEASURED", ind.filter(x => x.type === "MEASURED").map(row)) +
      sec("Inferred", "WHAT THE SOFTWARE ESTIMATES", ind.filter(x => x.type === "ESTIMATED" || (x.type === "UNAVAILABLE" && !/probability/.test(x.label) && !/Drain/.test(x.label))).map(row).concat(ind.filter(x => /Drain/.test(x.label)).map(row))) +
      sec("Predicted", "WHAT MAY HAPPEN NEXT", [ind.filter(x => /probability/.test(x.label)).map(row)[0], `<div class="m"><span>Template pattern match ${pv("ESTIMATED")}</span><b style="font-size:12px">${esc(pm)}</b><i style="--p:${p.best ? p.best.similarity : 0}%"></i></div>`, `<div class="m"><span>Top explanation ${pv("ESTIMATED")}</span><b style="font-size:12px">${esc(a.hypotheses[0].label)}</b><i style="--p:${a.hypotheses[0].support}%"></i></div>`, `<div class="m"><span>Evidence coverage</span><b>${a.evidence_coverage_pct}<small>%</small></b><i style="--p:${a.evidence_coverage_pct}%"></i></div>`]);
    const lo = t => t <= 50 ? "bad" : t <= 75 ? "warn" : "ok", tr = I.trust || {};
    $("tBars").innerHTML = Object.entries(tr).map(([id, t]) => t == null ? `<div class="tb"><span>${id.split("-")[0]}</span><i style="--p:0%"></i><b>n/a</b></div>` : `<div class="tb ${lo(t)}"><span>${id.split("-")[0]}</span><i style="--p:${t}%"></i><b>${Math.round(t)}</b></div>`).join("");
    $("analogTxt").textContent = "Pattern similarity is not probability. Templates are synthetic until real labelled storms are recorded. " + (p.status === "MATCH" ? "Closest: " + pm + "." : "");
    $("subNet").textContent = "evidence coverage " + a.evidence_coverage_pct + "%"; $("subRain").textContent = "quality: " + a.quality.rain.replace(/_/g, " ");
  },
};
document.querySelectorAll("[data-alert-filter]").forEach(b => b.addEventListener("click", () => { alertFilter = b.dataset.alertFilter; if (LASTSNAP) AQR.alerts(LASTSNAP); }));
setInterval(() => { if (LASTSNAP && !LASTSNAP.assessment) return; }, 5000);
document.addEventListener("click", e => { if (e.target.closest && e.target.closest("[data-k='9']")) setTimeout(() => LASTSNAP && AQR.render(LASTSNAP), 50); });
})();
