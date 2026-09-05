/* NETRA operator console. */
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
/* API key. Empty by default and empty in the demo, where no data/api_keys.json
   exists and the server runs open; the header is simply sent blank and every
   endpoint behaves as it always has. Where an operator has configured keys,
   this is what lets the console reach the protected endpoints — including the
   zone editor's still, which an <img src> alone cannot fetch because it has no
   way to carry a header. */
const API_KEY_STORE = "NETRA_API_KEY";
const apiKey = () => { try { return localStorage.getItem(API_KEY_STORE) || ""; }
                       catch (e) { return ""; } };
const authHeaders = (extra) => Object.assign({ "X-API-Key": apiKey() }, extra || {});
const api = async (p, o) => {
  const opts = Object.assign({}, o);
  opts.headers = authHeaders(opts.headers);
  return (await fetch(p, opts)).json();
};
document.addEventListener("DOMContentLoaded", () => {
  const box = document.getElementById("apiKey");
  if (!box) return;
  box.value = apiKey();
  box.onchange = () => {
    try { localStorage.setItem(API_KEY_STORE, box.value.trim()); }
    catch (e) { toast("This browser will not persist the key for this session."); }
  };
});
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
  if (a.dataset.view === "zones") loadZones();
  if (a.dataset.view === "traffic") loadTraffic();
  if (a.dataset.view === "intel") loadIntel();
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
    // Counts detection-frames whose plate was replaced by their track's voted
    // consensus - not the same quantity as the per-detection read count shown
    // beside each plate in the Detections table, which is why it is named for
    // what it is.
    card(inf.plate_consensus_applied ?? 0, "Plate consensus applied", "", "frames given a voted plate"),
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

/* ------------------------------------------------------- descriptions --- */
/* A vision-language description is the only account of a vehicle an officer
   can read, search and testify to on this grid, where no plate is recoverable.
   It is always rendered as a description of the crop, never as an identity. */
function attrHtml(at) {
  if (!at) return "";
  const bits = [];
  if (at.body_type && at.body_type !== "unknown") bits.push(at.body_type.replace(/_/g, " "));
  if (at.colour) bits.unshift(at.colour);
  const tags = [];
  if (at.tinted_windows === true) tags.push("tinted");
  if (at.wheels && at.wheels !== "unknown") tags.push(at.wheels + " wheels");
  if (at.roof_rack === true) tags.push("roof rack");
  (at.markings || []).forEach(m => tags.push("marking: " + m));
  (at.damage || []).forEach(d => tags.push(d));
  return `<div class="vdesc" title="${esc(at.raw_caption || "")}">
    <b>${esc(at.description || bits.join(" ") || "—")}</b>
    ${tags.length ? `<span class="faint"> · ${esc(tags.join(" · "))}</span>` : ""}
    <span class="faint mono" style="font-size:10px"> · ${esc(at.model || "model")}
      conf ${at.confidence ?? 0}</span>
    <div class="faint" style="font-size:10px">Describes the crop; not an identification.</div>
  </div>`;
}

function describeBtn(detectionId, has) {
  if (detectionId == null) return "";
  return `<button class="mini" data-describe="${esc(detectionId)}">${
    has ? "Re-describe" : "Describe"}</button>`;
}

async function describeDetection(btn) {
  const id = btn.dataset.describe;
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "describing…";
  try {
    const r = await api(`/api/detections/${encodeURIComponent(id)}/describe`, { method: "POST" });
    if (r.attributes) {
      const holder = btn.closest("[data-desc-holder]") || btn.parentElement;
      const existing = holder.querySelector(".vdesc");
      if (existing) existing.remove();
      btn.insertAdjacentHTML("beforebegin", attrHtml(r.attributes));
      btn.textContent = "Re-describe";
    } else {
      toast("No description could be produced: " + esc(r.detail || "unavailable"));
      btn.textContent = label;
    }
  } catch (e) {
    toast("Describe failed: " + esc(e));
    btn.textContent = label;
  }
  btn.disabled = false;
}

/* Delegated, because rows and alert cards are both re-rendered wholesale. */
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-describe]");
  if (btn) describeDetection(btn);
});

/* ---------------------------------------------------------- detections --- */
async function loadDetections() {
  const q = new URLSearchParams({ limit: "200" });
  if ($("#dPlate").value) q.set("plate", $("#dPlate").value);
  if ($("#dCam").value) q.set("camera_id", $("#dCam").value);
  if ($("#dClass").value) q.set("vehicle_class", $("#dClass").value);
  const d = await api("/api/detections?" + q);
  $("#dCount").textContent = `${d.count} of ${d.total}`;
  const tb = $("#tblDet tbody");
  if (!d.items.length) { tb.innerHTML = `<tr><td colspan="10" class="empty">No matching detections.</td></tr>`; return; }
  tb.innerHTML = d.items.map(x => `<tr>
    <td class="mono">${esc(x.at.replace("T", " ").slice(0, 19))}</td>
    <td class="mono faint">${Math.round(x.pts_ms)}</td>
    <td>${esc(x.camera_name || x.camera_id)}</td>
    <td>${esc(x.vehicle_class)}</td><td class="dim">${esc(x.colour || "—")}</td>
    <td class="mono">${x.plate_text ? esc(x.plate_text) : '<span class="faint">—</span>'}
      ${x.plate_chars ? `<span class="faint" style="font-size:10.5px">· ${esc(x.plate_chars)} chars</span>` : ""}
      ${x.plate_votes ? `<span class="faint" style="font-size:10.5px"
        title="OCR reads of this tracked vehicle that agreed on this plate. One read is a single guess, not a consensus."
        >· ${esc(x.plate_votes)} read${x.plate_votes === 1 ? "" : "s"}</span>` : ""}</td>
    <td class="dim">${x.plate_conf ?? "—"}</td>
    <td class="mono faint">${x.track_id != null ? esc(x.track_id) : "—"}</td>
    <td class="mono faint">${x.scene_time && x.scene_time_corroborated
      ? esc(x.scene_time.replace("T", " ").slice(0, 19))
      : (x.scene_time
        ? `<span title="read once from the overlay and never confirmed by a second reading, so it is not used for any timing claim">${esc(x.scene_time.replace("T", " ").slice(0, 19))} <b style="color:var(--warn)">?</b></span>`
        : '<span title="no clock recovered from the overlay">—</span>')}</td>
    <td data-desc-holder>${x.evidence ? `<img src="${esc(x.evidence)}" style="height:34px;border-radius:4px">` : ""}
      ${attrHtml(x.attributes)}${describeBtn(x.id, !!x.attributes)}</td></tr>`).join("");
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
  await fetch("/api/watchlist/" + id, { method: "DELETE", headers: authHeaders() });
  loadWatchlist();
};
$("#wAdd").onclick = async () => {
  const plate = $("#wPlate").value.trim().toUpperCase();
  if (!plate) return;
  await fetch("/api/watchlist", {
    method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
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
  await fetch("/api/watchlist/seed", { method: "POST", headers: authHeaders() });
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
    <div data-desc-holder>
      ${a.evidence ? `<img src="${esc(a.evidence)}">` : ""}
      ${attrHtml(a.attributes)}${describeBtn(a.detection_id, !!a.attributes)}
    </div>
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
    if (a.kind === "attributes") {
      // The description is extracted after the alert has already been sent, so
      // it arrives separately and is folded into the card that is already up.
      if (a.detection_id != null) {
        const btn = document.querySelector(`[data-describe="${a.detection_id}"]`);
        if (btn) btn.insertAdjacentHTML("beforebegin", attrHtml(a));
      } else if (a.zone_event_id != null) {
        // A zone event describes a whole frame, so it has no detection row to
        // key a description to; the card it belongs to is addressed directly.
        $$(`[data-zone-event="${a.zone_event_id}"]`)
          .forEach(card => card.insertAdjacentHTML("beforeend", attrHtml(a)));
      }
      return;
    }
    const feed = $("#alertFeed");
    if (feed.querySelector(".empty")) feed.innerHTML = "";
    if (a.kind === "zone") {
      // A rule breach is not a watchlist hit, and rendering one as the other
      // would put an identity claim on an event that carries none.
      feed.insertAdjacentHTML("afterbegin", zoneEventHtml(a));
      const ze = $("#zEvents");
      if (ze) {
        if (ze.querySelector(".empty")) ze.innerHTML = "";
        ze.insertAdjacentHTML("afterbegin", zoneEventHtml(a));
      }
      toast(`<b style="color:#ffb066">ZONE ${esc(a.rule)}</b><br>
        <span style="font-size:12px;color:#8b9bb4">${esc(a.zone || "")} ·
        ${esc(a.camera_id)} · ${esc(a.detail || "")}</span>`);
      return;
    }
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
  // The Traffic tab is the one an operator leaves open while watching a
  // junction, and until now it only ever showed what was there when the tab
  // was opened. Gated on visibility, like the overview poll above: an unseen
  // tab must not spend a database round trip every five seconds, and the
  // history query it makes is the heaviest of the console's reads.
  setInterval(() => {
    if ($("#v-traffic").classList.contains("active")) loadTraffic();
  }, 5000);
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
          ${h.leg_km != null ? `· ${h.leg_km} km from previous` : ""}
          ${h.ambiguous ? `<div class="tag t-degraded" style="margin-top:4px">ambiguous</div>
            <div class="faint" style="font-size:11px;margin-top:3px">${esc(h.ambiguity_note || "")}</div>` : ""}</div>
        ${h.evidence || h.evidence_path ? `<img src="${esc(h.evidence || h.evidence_path)}">` : ""}
      </div></div>`).join("");

  $("#routeRejected").innerHTML = r.rejected.length
    ? r.rejected.map(x => `<div class="finding" style="border-color:var(--bad);background:rgba(239,68,68,.06)">
        <b class="mono">${esc(x.camera_id)}</b> — ${esc(x.plausibility || "excluded")}</div>`).join("")
    : `<div class="faint" style="font-size:12px">None excluded.</div>`;
};

/* ---------------------------------------------------------------- zones --- */
let ZPOINTS = [];                      // normalised [x, y] pairs, in click order

function zoneEventHtml(e) {
  return `<div class="zev" data-zone-event="${esc(e.id ?? "")}">
    <div class="row" style="margin:0 0 5px 0;gap:8px">
      <span class="tag sev-${esc(e.severity || "medium")}">${esc(e.severity || "")}</span>
      <span class="tag t-vehicle">${esc(e.rule)}</span>
      <b>${esc(e.zone || "zone")}</b>
      <span class="faint mono" style="font-size:11px">${esc(e.camera_name || e.camera_id)}</span>
      <span style="margin-left:auto" class="faint mono">${esc((e.at || "").slice(11, 19))}</span>
    </div>
    <div class="dim">${esc(e.detail || "")}
      ${e.object_class ? `· ${esc(e.object_class)}` : ""}
      ${e.direction ? `· heading ${esc(e.direction)}` : ""}</div>
    ${e.evidence ? `<img src="${esc(e.evidence)}">` : ""}
  </div>`;
}

function drawZone() {
  const cv = $("#zCanvas"), img = $("#zImg");
  if (!cv || !img || !img.naturalWidth) return;
  cv.width = img.clientWidth; cv.height = img.clientHeight;
  const g = cv.getContext("2d");
  g.clearRect(0, 0, cv.width, cv.height);
  const pts = ZPOINTS.map(([x, y]) => [x * cv.width, y * cv.height]);
  if (!pts.length) return;
  g.strokeStyle = "#ff6b00"; g.lineWidth = 2;
  g.fillStyle = "rgba(255,107,0,.18)";
  g.beginPath();
  pts.forEach(([x, y], i) => i ? g.lineTo(x, y) : g.moveTo(x, y));
  if ($("#zRule").value !== "crossing" && pts.length > 2) { g.closePath(); g.fill(); }
  g.stroke();
  pts.forEach(([x, y], i) => {
    g.beginPath(); g.arc(x, y, 6, 0, 6.283); g.fillStyle = "#ff6b00"; g.fill();
    g.fillStyle = "#111"; g.font = "bold 10px monospace";
    g.fillText(String(i + 1), x - 3, y + 3);
  });
}

$("#zLoad").onclick = async () => {
  const cam = $("#zCam").value;
  if (!cam) return;
  const btn = $("#zLoad");
  btn.disabled = true; btn.textContent = "Grabbing frame…";
  const img = $("#zImg");
  // Fetched rather than set as an <img src>: the snapshot endpoint is behind
  // `require`, and an <img> has no way to carry X-API-Key, so with keys
  // configured the still 401'd while the rest of the console worked. The blob
  // is handed to the <img> as an object URL instead. The previous one is
  // revoked because the zone editor is reloaded repeatedly while an operator
  // draws, and each blob would otherwise be held for the life of the page.
  try {
    const r = await fetch(`/api/cameras/${encodeURIComponent(cam)}/snapshot?t=${Date.now()}`,
                          { headers: authHeaders() });
    if (!r.ok) throw new Error(r.status === 401 || r.status === 403
      ? "not authorised — set an API key in the header"
      : "HTTP " + r.status);
    const url = URL.createObjectURL(await r.blob());
    if (img.dataset.blobUrl) URL.revokeObjectURL(img.dataset.blobUrl);
    img.dataset.blobUrl = url;
    img.src = url;
    await img.decode();
    $("#zWrap").style.display = "inline-block";
    drawZone();
  } catch (e) {
    toast("Could not grab a still from " + esc(cam) + " — " + esc(e.message) +
      ". The camera may be down or the feed unreachable.");
  }
  btn.disabled = false; btn.textContent = "Load still frame";
};

$("#zCanvas").onclick = (e) => {
  const r = e.currentTarget.getBoundingClientRect();
  ZPOINTS.push([(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]);
  drawZone();
  $("#zHint").textContent = `${ZPOINTS.length} point(s) placed.`;
};
$("#zClear").onclick = () => {
  ZPOINTS = []; drawZone(); $("#zHint").textContent = "Points cleared.";
};
$("#zRule").onchange = drawZone;
window.addEventListener("resize", drawZone);

$("#zSave").onclick = async () => {
  const rule = $("#zRule").value;
  const needed = rule === "crossing" ? 2 : 3;
  if (ZPOINTS.length < needed) {
    toast(`A ${esc(rule)} rule needs at least ${needed} points.`); return;
  }
  const body = {
    camera_id: $("#zCam").value, name: $("#zName").value || "Zone",
    rule, points: ZPOINTS.map(([x, y]) => [+x.toFixed(4), +y.toFixed(4)]),
    classes: $("#zClasses").value ? [$("#zClasses").value] : [],
    severity: $("#zSev").value, dwell_s: parseFloat($("#zDwell").value) || 30,
  };
  const r = await fetch("/api/zones", {
    method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  const out = await r.json().catch(() => ({}));
  if (!r.ok) { toast("Rule rejected: " + esc(out.detail || r.status)); return; }
  ZPOINTS = []; drawZone();
  toast("Rule saved on " + esc(body.camera_id) + ".");
  loadZones();
};

window.delZone = async (id) => {
  await fetch("/api/zones/" + id, { method: "DELETE", headers: authHeaders() });
  loadZones();
};

async function loadZones() {
  if (!$("#zCam").options.length) {
    $("#zCam").innerHTML = CAMERAS.map(c =>
      `<option value="${esc(c.id)}">${esc(c.id)} — ${esc(c.name)}</option>`).join("");
  }
  const zones = await api("/api/zones");
  $("#zList").innerHTML = zones.length ? zones.map(z => `<div class="zone-item">
    <div style="flex:1">
      <div><b>${esc(z.name)}</b>
        <span class="tag t-vehicle" style="margin-left:6px">${esc(z.rule)}</span>
        <span class="tag sev-${esc(z.severity)}" style="margin-left:4px">${esc(z.severity)}</span>
        ${z.active ? "" : `<span class="tag t-degraded" style="margin-left:4px">inactive</span>`}</div>
      <div class="faint mono" style="font-size:11.5px;margin-top:3px">${esc(z.camera_id)} ·
        ${esc((z.points || []).length)} points ·
        ${(z.classes || []).length ? esc((z.classes || []).join(", ")) : "any class"}
        ${z.rule === "loitering" ? `· dwell ${esc(z.dwell_s)}s` : ""}</div>
    </div>
    <button onclick="delZone(${esc(z.id)})">Delete</button></div>`).join("")
    : `<div class="empty">No rules configured.</div>`;

  const events = await api("/api/zones/events?limit=50");
  $("#zEvents").innerHTML = events.length
    ? events.map(zoneEventHtml).join("") : `<div class="empty">No zone events yet.</div>`;
}

/* -------------------------------------------------------------- traffic --- */
function sparks(values) {
  const max = Math.max(1, ...values);
  return `<div class="sparks">${values.map(v =>
    `<i style="height:${Math.round(100 * v / max)}%" title="${esc(v)}"></i>`).join("")}</div>`;
}
const kv = (obj) => Object.entries(obj || {})
  .map(([k, v]) => `${esc(k)} ${esc(v)}`).join(" · ") || "—";

async function loadTraffic() {
  const live = await api("/api/traffic/live");
  const history = await api("/api/traffic/history?limit=1000");
  const byCam = {};
  history.forEach(h => (byCam[h.camera_id] = byCam[h.camera_id] || []).push(h.total));

  const cams = live.cameras || [];
  const totals = cams.reduce((a, c) => a + (c.total_counted || 0), 0);
  const loop = cams.reduce((a, c) => a + (c.counted_this_loop || 0), 0);
  const active = cams.reduce((a, c) => a + (c.active_tracks || 0), 0);
  const loops = cams.length ? Math.max(...cams.map(c => c.loops_seen || 0)) : 0;
  $("#tStats").innerHTML = [
    card(cams.length, "Cameras counting", cams.length ? "ok" : ""),
    card(active, "Active tracks", "", "being followed right now"),
    card(loop, "Counted this loop", "ok", "current pass of the recording"),
    card(totals, "Total counted", "", "cumulative across every replay"),
    card(loops, "Loops seen", loops > 1 ? "warn" : "", "recording replays observed"),
    card(live.zone_events ?? 0, "Zone events", live.zone_events ? "warn" : "", "rule breaches raised"),
  ].join("");

  const tb = $("#tblTraffic tbody");
  tb.innerHTML = cams.length ? cams.map(c => {
    const cam = CAMERAS.find(x => x.id === c.camera_id);
    const hist = (byCam[c.camera_id] || []).slice(0, 30).reverse();
    return `<tr>
      <td><b>${esc(cam ? cam.name : c.camera_id)}</b>
        <div class="faint mono" style="font-size:11px">${esc(c.camera_id)}</div></td>
      <td class="mono">${esc(c.active_tracks)}</td>
      <td class="mono" style="color:#4ade80;font-weight:700">${esc(c.counted_this_loop)}</td>
      <td class="mono">${esc(c.total_counted)}</td>
      <td class="mono faint">${esc(c.loops_seen)}</td>
      <td class="mono faint">${esc(c.dropped_tracks)}</td>
      <td class="dim">${kv(c.counts_by_class)}</td>
      <td class="dim">${kv(c.directions)}</td>
      <td class="mono">${esc(c.mean_dwell_s)}s</td>
      <td style="min-width:110px">${hist.length ? sparks(hist)
        : `<span class="faint">no snapshots</span>`}</td></tr>`;
  }).join("") : `<tr><td colspan="10" class="empty">No cameras counting — start the pipeline.</td></tr>`;
}
$("#tRefresh").onclick = loadTraffic;
$("#tSnap").onclick = async () => {
  const r = await api("/api/traffic/snapshot", { method: "POST" });
  toast(`Traffic snapshot written for ${esc(r.buckets_written)} camera(s).`);
  loadTraffic();
};

/* --------------------------------------------------------- intelligence --- */
async function loadIntel() {
  if (!$("#iGroup").options.length) {
    const groups = [...new Set(CAMERAS.map(c => c.time_group).filter(Boolean))].sort();
    $("#iGroup").innerHTML = groups.map(g =>
      `<option value="${esc(g)}">${esc(g)}</option>`).join("");
  }
  loadClones(); loadAnomalies(); loadJourneys();
}
$("#iRefresh").onclick = loadIntel;
$("#iGroup").onchange = loadJourneys;

async function loadClones() {
  const r = await api("/api/analytics/cloned-plates");
  const f = r.findings || [];
  $("#iClones").innerHTML =
    `<div class="faint" style="font-size:12px;margin-bottom:9px">${esc(r.note)}</div>` +
    (f.length ? f.map(x => `<div class="finding" style="border-color:var(--bad);background:rgba(239,68,68,.06)">
      <b class="mono" style="font-size:14px;color:#fff">${esc(x.plate)}</b>
      <span class="faint mono" style="font-size:11px">· confidence ${esc(x.confidence)}</span>
      <div style="margin-top:5px">${esc(x.sighting_a.camera_name)}
        <span class="faint mono">${esc(x.sighting_a.at)}</span> &rarr;
        ${esc(x.sighting_b.camera_name)}
        <span class="faint mono">${esc(x.sighting_b.at)}</span></div>
      <div class="dim" style="margin-top:4px">${esc(x.distance_km)} km ·
        ${esc(x.elapsed_s)} s ·
        ${x.implied_kmh == null ? "speed not computable" : esc(x.implied_kmh) + " km/h implied"}</div>
      <div style="margin-top:5px">${esc(x.reason)}</div></div>`).join("")
      : `<div class="empty">No cloned-plate findings in the stored detections.</div>`);
}

async function loadAnomalies() {
  const r = await api("/api/analytics/anomalies");
  const a = r.assessments || [];
  const head = `<div class="faint" style="font-size:12px;margin-bottom:9px">
    ${esc(r.cameras_assessed)} camera(s) assessed against ${esc(r.buckets_read)} stored buckets ·
    ${esc(r.anomalies)} flagged${r.stale ? `, ${esc(r.stale)} not reporting` : ""}. Cameras with too
    little history to judge, or whose last bucket is too old to be a current reading, are shown muted
    rather than hidden: hiding them would imply coverage that does not exist.</div>`;
  $("#iAnoms").innerHTML = head + (a.length ? a.map(x => {
    // A stale camera is muted alongside an unjudged one: neither is a
    // statement about the road, and colouring stale green would present a
    // dropped feed as a road confirmed clear.
    const thin = x.status === "insufficient_data" || x.status === "stale";
    const colour = thin ? "var(--faint)" : (x.anomalous ? "var(--warn)" : "var(--ok)");
    return `<div class="finding ${thin ? "muted" : ""}"
      style="border-color:${colour};background:rgba(59,130,246,.05)">
      <b class="mono">${esc(x.camera_id)}</b>
      <span class="tag ${thin ? "t-unknown" : (x.anomalous ? "sev-high" : "t-anpr")}"
        style="margin-left:6px">${esc(x.status)}</span>
      <span class="faint mono" style="font-size:11px;margin-left:6px">hour ${esc(x.hour)} UTC ·
        observed ${esc(x.observed)}${x.z_score == null ? "" : " · z " + esc(x.z_score)}</span>
      <div style="margin-top:4px">${esc(x.explanation)}</div>
      ${x.baseline ? `<div class="faint" style="font-size:11px;margin-top:3px">
        baseline mean ${esc(x.baseline.mean)} · stdev ${esc(x.baseline.stdev)} ·
        ${esc(x.baseline.samples)} samples</div>` : ""}</div>`;
  }).join("") : `<div class="empty">No traffic snapshots stored yet — write one from the Traffic tab.</div>`);
}

async function loadJourneys() {
  const g = $("#iGroup").value;
  if (!g) { $("#iJourneys").innerHTML = `<div class="empty">No time group available.</div>`; return; }
  const r = await api("/api/analytics/journeys?group=" + encodeURIComponent(g));
  if (r.detail) { $("#iJourneys").innerHTML = `<div class="empty">${esc(r.detail)}</div>`; return; }
  const idx = r.index || {};
  let head = `<div class="faint" style="font-size:12px;margin-bottom:9px">
    ${esc(r.note)}<br>Index: ${esc(idx.detections_in_group ?? 0)} detections ·
    ${esc(idx.comparable ?? 0)} comparable ·
    ${esc(idx.excluded_no_scene_time ?? 0)} without a scene clock ·
    ${esc(idx.excluded_no_embedding ?? 0)} without an appearance vector.</div>`;
  if (r.mining_skipped) {
    head += `<div class="finding">Mining was skipped: this group has been mined already and held no
      journeys, so it is not re-derived on every poll. Nothing re-mines on a timer —
      next mine ${esc(r.next_mine)}.</div>`;
  }
  const js = r.journeys || [];
  if (!js.length) {
    $("#iJourneys").innerHTML = head +
      `<div class="empty">No cross-camera journey found in the indexed recordings.</div>`;
    return;
  }
  $("#iJourneys").innerHTML = head + js.map((j, n) => `<div class="panel" style="margin-bottom:12px">
    <h3>Journey ${n + 1} · ${esc(j.hop_count)} hops · ${esc(j.total_km)} km ·
      confidence ${esc(j.confidence)}
      ${j.truncated ? `<span class="tag t-degraded">truncated</span>` : ""}</h3>
    <div class="body">
      ${j.note ? `<div class="faint" style="font-size:11.5px;margin-bottom:8px">${esc(j.note)}</div>` : ""}
      ${(j.hops || []).map((h, i) => `<div class="hop">
        <div class="num">${i + 1}</div>
        <div style="flex:1">
          <div><b>${esc(h.camera_name || h.camera_id)}</b>
            <span class="faint mono">${esc(h.camera_id)}</span></div>
          <div class="mono dim" style="font-size:11.5px">${esc(h.at)}</div>
          <div style="font-size:11.5px;margin-top:3px">
            ${esc(h.colour || "")} ${esc(h.vehicle_class || "")}
            ${h.plate_text ? `· plate <span class="mono">${esc(h.plate_text)}</span>` : ""}
            ${h.similarity != null ? `· <b style="color:#c99bff">similarity ${esc(h.similarity)}</b>`
              : "· first sighting"}
            ${h.leg_km != null ? `· ${esc(h.leg_km)} km · ${esc(h.implied_kmh ?? "?")} km/h` : ""}</div>
          ${h.reason ? `<div class="faint" style="font-size:11px;margin-top:3px">${esc(h.reason)}</div>` : ""}
          ${h.evidence_path ? `<img src="${esc(h.evidence_path)}">` : ""}
        </div></div>`).join("")}
    </div></div>`).join("");
}
