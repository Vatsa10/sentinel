/* NETRA operator console. */
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const api = async (p, o) => (await fetch(p, o)).json();
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let CAMERAS = [], MAP = null, ROUTE_MAP = null, MARKERS = {}, ROUTE_LAYER = null;
const PC = {};                       // active WebRTC peer connections

/* ---------------------------------------------------------------- nav --- */
$$("nav a").forEach(a => a.onclick = () => {
  $$("nav a").forEach(x => x.classList.remove("active"));
  $$(".view").forEach(v => v.classList.remove("active"));
  a.classList.add("active");
  $("#v-" + a.dataset.view).classList.add("active");
  if (a.dataset.view === "map") setTimeout(initMap, 60);
  if (a.dataset.view === "route") setTimeout(initRouteMap, 60);
  if (a.dataset.view === "registry") loadRegistry();
  if (a.dataset.view === "watchlist") loadWatchlist();
  if (a.dataset.view === "alerts") loadAlerts();
  if (a.dataset.view === "detections") loadDetections();
});

function toast(html) {
  const d = document.createElement("div");
  d.className = "t";
  d.innerHTML = html;
  $("#toasts").appendChild(d);
  setTimeout(() => d.remove(), 9000);
}

/* ------------------------------------------------------------ pipeline --- */
$("#btnStart").onclick = async () => {
  $("#btnStart").disabled = true;
  $("#pipeTxt").textContent = "starting…";
  try {
    await api("/api/pipeline/start", { method: "POST" });
    toast("<b>Pipeline started.</b> Connecting to cameras…");
  } catch (e) { toast("Failed to start: " + e); }
  $("#btnStart").disabled = false;
  refresh();
};
$("#btnStop").onclick = async () => {
  await api("/api/pipeline/stop", { method: "POST" });
  refresh();
};

/* -------------------------------------------------------------- status --- */
async function refresh() {
  let st, stats;
  try {
    st = await api("/api/pipeline/status");
    stats = await api("/api/detections/stats");
  } catch { return; }

  const cams = st.cameras || [];
  const up = cams.filter(c => c.connected).length;
  const esc_ = cams.filter(c => c.escalated).length;
  const inf = st.inference || {};

  $("#pipeDot").className = "dot " + (st.running ? "on" : "off");
  $("#pipeTxt").textContent = st.running ? `pipeline live · ${up}/${cams.length} cams` : "pipeline idle";

  const drop = inf.submitted ? (100 * inf.dropped / inf.submitted) : 0;
  $("#statCards").innerHTML = [
    card(up + "/" + cams.length, "Cameras connected", up === cams.length && up > 0 ? "ok" : (up ? "warn" : "bad"), "live RTSP over TCP"),
    card(stats.total_detections ?? 0, "Detections", "", "vehicles observed"),
    card(stats.with_plate ?? 0, "Plates read", "ok", (stats.plate_rate_pct ?? 0) + "% of detections"),
    card(stats.total_alerts ?? 0, "Watchlist alerts", stats.total_alerts ? "bad" : "", "matches raised"),
    card(esc_, "Escalated to tier-2", "warn", "cameras with active traffic"),
    card((inf.infer_ms ?? 0) + "ms", "Inference latency", "", "last batch"),
    card(st.queue_depth ?? 0, "Queue depth", drop > 20 ? "warn" : "", drop.toFixed(1) + "% frames dropped"),
    card(cams.reduce((a, c) => a + (c.loop_cuts || 0), 0), "Loop cuts handled", "", "state resets"),
  ].join("");
}
const card = (n, l, cls = "", s = "") =>
  `<div class="stat ${cls}"><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div><div class="s">${esc(s)}</div></div>`;

async function loadRecent() {
  const d = await api("/api/detections?limit=25");
  const tb = $("#tblRecent tbody");
  if (!d.items.length) { tb.innerHTML = `<tr><td colspan="6" class="empty">No detections yet.</td></tr>`; return; }
  tb.innerHTML = d.items.map(x => `<tr>
    <td class="mono">${esc(x.at.slice(11, 19))}</td>
    <td>${esc(x.camera_name || x.camera_id)}</td>
    <td>${esc(x.vehicle_class)}</td>
    <td class="dim">${esc(x.colour || "—")}</td>
    <td class="mono">${x.plate_text ? esc(x.plate_text) : '<span class="faint">—</span>'}</td>
    <td>${x.evidence ? `<img src="${esc(x.evidence)}" style="height:30px;border-radius:4px">` : ""}</td></tr>`).join("");
}

/* ----------------------------------------------------------------- map --- */
function initMap() {
  if (!MAP) {
    MAP = L.map("map", { zoomControl: true }).setView([22.6, 71.6], 7);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
      { attribution: "&copy; OpenStreetMap &copy; CARTO", maxZoom: 19 }).addTo(MAP);
  }
  MAP.invalidateSize();
  drawCameras();
}
const CAP_COLOUR = { anpr: "#22c55e", vehicle: "#3b82f6", person: "#a855f7", degraded: "#ef4444", unknown: "#64748b" };

function drawCameras() {
  const filter = $("#mapFilter").value;
  Object.values(MARKERS).forEach(m => MAP.removeLayer(m));
  MARKERS = {};
  CAMERAS.filter(c => c.lat && (!filter || c.capability === filter)).forEach(c => {
    const col = CAP_COLOUR[c.capability] || "#64748b";
    const live = c.live && c.live.connected;
    const m = L.circleMarker([c.lat, c.lon], {
      radius: 7, color: col, weight: 2,
      fillColor: col, fillOpacity: live ? .85 : .25,
    }).addTo(MAP);
    m.bindPopup(`<b>${esc(c.name)}</b><br>
      <span style="color:#8b9bb4">${esc(c.id)} · ${esc(c.city || "")}</span><br>
      <span style="color:#7fd1ff">${esc(c.codec || "?")} ${c.width}×${c.height} @ ${esc(c.declared_fps || "?")}</span><br>
      capability: <b style="color:${col}">${esc(c.capability)}</b><br>
      ${c.capability_note ? `<span style="color:#ff9b9b">${esc(c.capability_note)}</span><br>` : ""}
      ${live ? `<span style="color:#22c55e">● live ${c.live.measured_fps} fps</span>` : `<span style="color:#5c6b86">○ not connected</span>`}`);
    MARKERS[c.id] = m;
  });
}
$("#mapFilter").onchange = drawCameras;

/* ---------------------------------------------------------- video wall --- */
async function whep(cam, video) {
  const pc = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] });
  PC[cam.id] = pc;
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.addTransceiver("audio", { direction: "recvonly" });
  pc.ontrack = (e) => { video.srcObject = e.streams[0]; };
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  const r = await fetch(cam.whep_url, {
    method: "POST", headers: { "Content-Type": "application/sdp" }, body: offer.sdp,
  });
  if (!r.ok) throw new Error("WHEP " + r.status);
  const answer = await r.text();
  await pc.setRemoteDescription({ type: "answer", sdp: answer });
}

$("#btnWall").onclick = async () => {
  const n = parseInt($("#wallCount").value, 10);
  const pick = CAMERAS.filter(c => c.capability !== "degraded").slice(0, n);
  $("#wall").innerHTML = pick.map(c => `<div class="cell" id="cell-${c.id}">
      <span class="badge tag t-${c.capability}">${esc(c.capability)}</span>
      <video id="vid-${c.id}" autoplay muted playsinline></video>
      <div class="ph" id="ph-${c.id}">connecting…</div>
      <div class="cap"><b>${esc(c.id)}</b> <span class="dim">${esc(c.name)}</span></div>
    </div>`).join("");
  for (const c of pick) {
    try {
      await whep(c, $("#vid-" + c.id));
      const ph = $("#ph-" + c.id); if (ph) ph.remove();
    } catch (e) {
      const ph = $("#ph-" + c.id);
      if (ph) ph.innerHTML = `<span style="color:#ff9b9b">stream unavailable</span>
        <span style="font-size:10.5px">${esc(e.message)}</span>`;
    }
  }
};
$("#btnWallStop").onclick = () => {
  Object.values(PC).forEach(p => p.close());
  for (const k in PC) delete PC[k];
  $("#wall").innerHTML = "";
};

/* ---------------------------------------------------------- detections --- */
async function loadDetections() {
  const q = new URLSearchParams({ limit: "200" });
  if ($("#dPlate").value) q.set("plate", $("#dPlate").value);
  if ($("#dCam").value) q.set("camera_id", $("#dCam").value);
  if ($("#dClass").value) q.set("vehicle_class", $("#dClass").value);
  const d = await api("/api/detections?" + q);
  $("#dCount").textContent = `${d.count} of ${d.total}`;
  const tb = $("#tblDet tbody");
  if (!d.items.length) { tb.innerHTML = `<tr><td colspan="8" class="empty">No matching detections.</td></tr>`; return; }
  tb.innerHTML = d.items.map(x => `<tr>
    <td class="mono">${esc(x.at.replace("T", " ").slice(0, 19))}</td>
    <td class="mono faint">${Math.round(x.pts_ms)}</td>
    <td>${esc(x.camera_name || x.camera_id)}</td>
    <td>${esc(x.vehicle_class)}</td><td class="dim">${esc(x.colour || "—")}</td>
    <td class="mono">${x.plate_text ? esc(x.plate_text) : '<span class="faint">—</span>'}</td>
    <td class="dim">${x.plate_conf ?? "—"}</td>
    <td>${x.evidence ? `<img src="${esc(x.evidence)}" style="height:34px;border-radius:4px">` : ""}</td></tr>`).join("");
}
$("#dSearch").onclick = loadDetections;
$("#dCsv").onclick = () => {
  const p = $("#dPlate").value;
  location.href = "/api/export/detections.csv" + (p ? "?plate=" + encodeURIComponent(p) : "");
};

/* --------------------------------------------------------------- route --- */
function initRouteMap() {
  if (!ROUTE_MAP) {
    ROUTE_MAP = L.map("routeMap").setView([23.03, 72.57], 12);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
      { attribution: "&copy; CARTO", maxZoom: 19 }).addTo(ROUTE_MAP);
  }
  ROUTE_MAP.invalidateSize();
}
$("#rGo").onclick = async () => {
  const plate = $("#rPlate").value.trim().toUpperCase();
  if (!plate) return;
  initRouteMap();
  const r = await api("/api/route?plate=" + encodeURIComponent(plate));

  if (ROUTE_LAYER) { ROUTE_MAP.removeLayer(ROUTE_LAYER); ROUTE_LAYER = null; }
  const pts = r.hops.filter(h => h.lat).map(h => [h.lat, h.lon]);
  if (pts.length) {
    ROUTE_LAYER = L.layerGroup().addTo(ROUTE_MAP);
    L.polyline(pts, { color: "#ff6b00", weight: 3, opacity: .85 }).addTo(ROUTE_LAYER);
    r.hops.forEach((h, i) => {
      if (!h.lat) return;
      L.marker([h.lat, h.lon]).addTo(ROUTE_LAYER)
        .bindPopup(`<b>${i + 1}. ${esc(h.camera_name)}</b><br>${esc(h.at)}<br>
          plate: <span style="color:#7fd1ff">${esc(h.plate_text)}</span>`);
    });
    ROUTE_MAP.fitBounds(L.latLngBounds(pts).pad(.25));
  }

  $("#routeList").innerHTML = r.hops.length ? `
    <div style="margin-bottom:10px;font-size:12.5px" class="dim">
      <b style="color:#e6edf5">${r.hop_count} sightings</b> · ${r.total_km} km ·
      ${(r.duration_s / 60).toFixed(1)} min</div>` +
    r.hops.map((h, i) => `<div class="hop">
      <div class="num">${i + 1}</div>
      <div style="flex:1">
        <div><b>${esc(h.camera_name)}</b> <span class="faint mono">${esc(h.camera_id)}</span></div>
        <div class="mono dim" style="font-size:11.5px">${esc(h.at)}</div>
        <div style="font-size:11.5px;margin-top:3px">
          plate <span class="mono">${esc(h.plate_text)}</span>
          ${h.colour ? `· ${esc(h.colour)} ${esc(h.vehicle_class)}` : ""}</div>
        ${h.leg_km != null ? `<div class="faint" style="font-size:11px;margin-top:3px">
          ${h.leg_km} km from previous · ${h.leg_seconds}s · ${h.implied_kmh} km/h</div>` : ""}
        ${h.evidence_path ? `<img src="${esc(h.evidence_path)}">` : ""}
      </div></div>`).join("")
    : `<div class="empty">No sightings of ${esc(plate)}.</div>`;

  $("#routeRejected").innerHTML = r.rejected.length
    ? r.rejected.map(x => `<div class="finding" style="border-color:var(--bad);background:rgba(239,68,68,.06)">
        <b class="mono">${esc(x.camera_id)}</b> — ${esc(x.reason)}</div>`).join("")
    : `<div class="faint" style="font-size:12px">None excluded.</div>`;
};

/* ----------------------------------------------------------- watchlist --- */
async function loadWatchlist() {
  const rows = await api("/api/watchlist");
  const tb = $("#tblWl tbody");
  if (!rows.length) { tb.innerHTML = `<tr><td colspan="7" class="empty">Watchlist is empty.</td></tr>`; return; }
  tb.innerHTML = rows.map(e => `<tr>
    <td class="mono" style="font-weight:700">${esc(e.plate)}</td>
    <td>${esc(e.category)}</td>
    <td><span class="tag sev-${esc(e.severity)}">${esc(e.severity)}</span></td>
    <td class="dim">${esc([e.vehicle_colour, e.vehicle_class].filter(Boolean).join(" ") || "—")}</td>
    <td class="mono faint">${esc(e.case_ref || "—")}</td>
    <td class="dim">${esc(e.source_db)}</td>
    <td><button onclick="delWl(${e.id})">Remove</button></td></tr>`).join("");
}
window.delWl = async (id) => {
  await fetch("/api/watchlist/" + id, { method: "DELETE" });
  loadWatchlist();
};
$("#wAdd").onclick = async () => {
  const plate = $("#wPlate").value.trim().toUpperCase();
  if (!plate) return;
  await fetch("/api/watchlist", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      plate, category: $("#wCat").value, severity: $("#wSev").value,
      vehicle_colour: $("#wColour").value || null,
      vehicle_class: $("#wClass").value || null,
      case_ref: $("#wCase").value || null, source_db: "MANUAL",
    }),
  });
  $("#wPlate").value = ""; $("#wCase").value = "";
  loadWatchlist();
};
$("#wSeed").onclick = async () => {
  await fetch("/api/watchlist/seed", { method: "POST" });
  loadWatchlist();
  toast("Sample watchlist loaded.");
};

/* -------------------------------------------------------------- alerts --- */
function alertHtml(a) {
  const reasons = Object.entries(a.reasons || {})
    .map(([k, v]) => `<b>${esc(k)}</b> ${v.score} — ${esc(v.detail)}`).join("<br>");
  return `<div class="alert-item">
    <div class="top">
      <span class="plate">${esc(a.plate_watchlist || a.plate_observed)}</span>
      <span class="tag sev-${esc(a.severity || "medium")}">${esc(a.severity || "")}</span>
      <span class="tag t-vehicle">${esc(a.match_type)}</span>
      <span class="faint mono" style="font-size:11px">score ${a.score}</span>
      <span class="spacer" style="margin-left:auto"></span>
      <span class="faint mono" style="font-size:11px">${esc((a.at || "").slice(11, 19))}</span>
    </div>
    <div style="font-size:12.5px">
      <span class="dim">read as</span> <span class="mono">${esc(a.plate_observed || "—")}</span>
      <span class="dim">on</span> <b>${esc(a.camera_name || a.camera_id)}</b>
      ${a.category ? `<span class="dim"> · ${esc(a.category)}</span>` : ""}
      ${a.case_ref ? `<span class="faint mono"> · ${esc(a.case_ref)}</span>` : ""}
    </div>
    <div class="why">${reasons}</div>
    ${a.evidence ? `<img src="${esc(a.evidence)}">` : ""}
  </div>`;
}
async function loadAlerts() {
  const rows = await api("/api/alerts?limit=100");
  $("#alertList").innerHTML = rows.length
    ? rows.map(alertHtml).join("") : `<div class="empty">No alerts raised.</div>`;
}
$("#aRefresh").onclick = loadAlerts;

/* ------------------------------------------------------------ registry --- */
async function loadRegistry() {
  const gap = await api("/api/cameras/gap-analysis");
  $("#gapFindings").innerHTML = gap.findings.map(f => `<div class="finding">${esc(f)}</div>`).join("");
  const bc = gap.by_capability || {};
  $("#gapStats").innerHTML = [
    card(gap.total_cameras, "Cameras in registry"),
    card(bc.anpr || 0, "ANPR-capable", "ok", gap.anpr_coverage_pct + "% of grid"),
    card(bc.vehicle || 0, "Vehicle analytics", ""),
    card(bc.person || 0, "Person analytics", ""),
    card(bc.degraded || 0, "Degraded / faulty", "bad", "cannot deliver analytics"),
    card(gap.usable_pct + "%", "Usable coverage", gap.usable_pct > 75 ? "ok" : "warn"),
  ].join("");

  const tb = $("#tblReg tbody");
  tb.innerHTML = CAMERAS.length ? CAMERAS.map(c => `<tr>
    <td class="mono">${esc(c.id)}</td><td>${esc(c.name)}</td><td class="dim">${esc(c.city || "—")}</td>
    <td class="mono">${esc(c.codec || "—")}</td>
    <td class="mono faint">${c.width ? c.width + "×" + c.height : "—"}</td>
    <td class="mono faint">${esc(c.declared_fps || "—")}</td>
    <td><span class="tag t-${esc(c.capability)}">${esc(c.capability)}</span></td>
    <td class="${c.health === "ok" ? "" : "dim"}">${esc(c.health)}</td>
    <td class="faint" style="font-size:11.5px">${esc(c.capability_note || "")}</td></tr>`).join("")
    : `<tr><td colspan="9" class="empty">Registry empty — run onboarding.</td></tr>`;
}
$("#regOnboard").onclick = async () => {
  $("#regOnboard").disabled = true;
  $("#regOnboard").textContent = "Probing 30 cameras…";
  await api("/api/cameras/onboard", { method: "POST" });
  await loadCameras();
  await loadRegistry();
  $("#regOnboard").disabled = false;
  $("#regOnboard").textContent = "Re-run onboarding & profiling";
  toast("Registry refreshed.");
};

/* ----------------------------------------------------------- bootstrap --- */
async function loadCameras() {
  CAMERAS = await api("/api/cameras");
  $("#dCam").innerHTML = `<option value="">All cameras</option>` +
    CAMERAS.map(c => `<option value="${esc(c.id)}">${esc(c.id)} — ${esc(c.name)}</option>`).join("");
  if (MAP) drawCameras();
}

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/alerts`);
  ws.onopen = () => { $("#wsDot").className = "dot on"; $("#wsTxt").textContent = "alerts live"; };
  ws.onclose = () => {
    $("#wsDot").className = "dot off"; $("#wsTxt").textContent = "reconnecting";
    setTimeout(connectWs, 3000);
  };
  ws.onmessage = (e) => {
    const a = JSON.parse(e.data);
    if (a.type === "ping") return;
    const feed = $("#alertFeed");
    if (feed.querySelector(".empty")) feed.innerHTML = "";
    feed.insertAdjacentHTML("afterbegin", alertHtml(a));
    toast(`<b style="color:#ff8080">WATCHLIST HIT</b><br>
      <span class="mono" style="font-size:15px">${esc(a.plate_watchlist)}</span><br>
      <span style="font-size:12px;color:#8b9bb4">${esc(a.camera_id)} · ${esc(a.match_type)} · score ${a.score}</span>`);
  };
}

(async function init() {
  await loadCameras();
  await refresh();
  await loadRecent();
  connectWs();
  setInterval(refresh, 3000);
  setInterval(() => {
    if ($("#v-overview").classList.contains("active")) loadRecent();
  }, 4000);
})();

/* ----------------------------------------------------------- assistant --- */
const ASST_SUGGESTIONS = [
  "Which cameras are down?",
  "How many detections so far?",
  "Show me the alerts",
  "What is on the watchlist?",
  "Where has GJ01AB1234 been seen?",
  "Coverage by location",
];

function asstBubble(role, text, actions) {
  const cls = role === "user" ? "border-color:var(--accent);background:rgba(255,107,0,.06)" : "";
  const btns = (actions || []).map(a =>
    `<button style="margin:6px 6px 0 0" onclick="asstAction(${esc(JSON.stringify(JSON.stringify(a)))})">${esc(a.label)}</button>`
  ).join("");
  return `<div class="finding" style="${cls}">
    <b style="color:${role === "user" ? "var(--accent)" : "#7fb0ff"}">${role === "user" ? "You" : "NETRA"}</b><br>
    ${esc(text)}${btns ? `<div>${btns}</div>` : ""}</div>`;
}

window.asstAction = (raw) => {
  const a = JSON.parse(raw);
  if (a.query && !a.view) { $("#asstQ").value = a.query; asstAsk(); return; }
  if (a.view) {
    const tab = $$("nav a").find(x => x.dataset.view === a.view);
    if (tab) tab.click();
    if (a.view === "route" && a.query) {
      $("#rPlate").value = a.query;
      setTimeout(() => $("#rGo").click(), 300);
    }
  }
};

async function asstAsk() {
  const q = $("#asstQ").value.trim();
  if (!q) return;
  const log = $("#asstLog");
  log.insertAdjacentHTML("beforeend", asstBubble("user", q));
  $("#asstQ").value = "";
  log.scrollTop = log.scrollHeight;

  try {
    const r = await api("/api/assistant", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });
    log.insertAdjacentHTML("beforeend", asstBubble("netra", r.answer, r.actions));
  } catch (e) {
    log.insertAdjacentHTML("beforeend", asstBubble("netra", "Request failed: " + e));
  }
  log.scrollTop = log.scrollHeight;
}

$("#asstGo").onclick = asstAsk;
$("#asstQ").addEventListener("keydown", e => { if (e.key === "Enter") asstAsk(); });
$("#asstChips").innerHTML = ASST_SUGGESTIONS.map(s =>
  `<button onclick="document.getElementById('asstQ').value=${esc(JSON.stringify(s))};asstAsk()">${esc(s)}</button>`
).join("");

/* ------------------------------------------- trace a vehicle by looks ---- */
$("#rAppearance").onclick = async () => {
  const id = parseInt($("#rDet").value, 10);
  if (!id) { toast("Enter a detection id (from the Detections screen)."); return; }
  initRouteMap();

  const r = await api(`/api/vehicles/${id}/track`);
  if (r.detail) { toast(esc(r.detail)); return; }

  if (ROUTE_LAYER) { ROUTE_MAP.removeLayer(ROUTE_LAYER); ROUTE_LAYER = null; }
  const pts = r.hops.filter(h => h.lat).map(h => [h.lat, h.lon]);
  if (pts.length) {
    ROUTE_LAYER = L.layerGroup().addTo(ROUTE_MAP);
    L.polyline(pts, { color: "#a855f7", weight: 3, dashArray: "6 5", opacity: .9 }).addTo(ROUTE_LAYER);
    r.hops.forEach((h, i) => {
      if (!h.lat) return;
      L.marker([h.lat, h.lon]).addTo(ROUTE_LAYER)
        .bindPopup(`<b>${i + 1}. ${esc(h.camera_name || h.camera_id)}</b><br>${esc(h.at)}
          ${h.similarity ? `<br>similarity ${h.similarity}` : ""}`);
    });
    ROUTE_MAP.fitBounds(L.latLngBounds(pts).pad(.25));
  }

  $("#routeList").innerHTML = `
    <div class="finding" style="border-color:#a855f7;background:rgba(168,85,247,.07)">
      <b>Appearance-based trace.</b> ${esc(r.note)}</div>
    <div style="margin-bottom:10px;font-size:12.5px" class="dim">
      <b style="color:#e6edf5">${r.hop_count} sightings</b> · ${r.total_km} km ·
      ${esc(r.method)}</div>` +
    r.hops.map((h, i) => `<div class="hop">
      <div class="num" style="background:#a855f7;color:#fff">${i + 1}</div>
      <div style="flex:1">
        <div><b>${esc(h.camera_name || h.camera_id)}</b>
          <span class="faint mono">${esc(h.camera_id)}</span></div>
        <div class="mono dim" style="font-size:11.5px">${esc(h.at)}</div>
        <div style="font-size:11.5px;margin-top:3px">
          ${esc(h.colour || "")} ${esc(h.vehicle_class || "")}
          ${h.similarity ? `· <b style="color:#c99bff">similarity ${h.similarity}</b>` : "· query vehicle"}
          ${h.leg_km != null ? `· ${h.leg_km} km from previous` : ""}</div>
        ${h.evidence || h.evidence_path ? `<img src="${esc(h.evidence || h.evidence_path)}">` : ""}
      </div></div>`).join("");

  $("#routeRejected").innerHTML = r.rejected.length
    ? r.rejected.map(x => `<div class="finding" style="border-color:var(--bad);background:rgba(239,68,68,.06)">
        <b class="mono">${esc(x.camera_id)}</b> — ${esc(x.plausibility || "excluded")}</div>`).join("")
    : `<div class="faint" style="font-size:12px">None excluded.</div>`;
};
