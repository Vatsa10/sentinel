# Review package — Task 8

ddf5225 Surface zones, traffic and intelligence in the operator console

 netra/api/app.py     |  59 +++++++++-
 netra/web/app.js     | 305 ++++++++++++++++++++++++++++++++++++++++++++++++++-
 netra/web/index.html |  91 ++++++++++++++-
 3 files changed, 449 insertions(+), 6 deletions(-)

diff --git a/netra/api/app.py b/netra/api/app.py
index 11af75e..d037dc9 100644
--- a/netra/api/app.py
+++ b/netra/api/app.py
@@ -2,21 +2,22 @@
 from __future__ import annotations
 
 import asyncio
 import csv
 import io
 import json
 import logging
 from datetime import datetime, timedelta, timezone
 
 from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
-from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
+from fastapi.responses import (HTMLResponse, JSONResponse, Response,
+                               StreamingResponse)
 from fastapi.staticfiles import StaticFiles
 from sqlalchemy import func, select
 from sqlalchemy.orm import joinedload
 
 from fastapi import Depends, Header
 
 from netra import config
 from netra.analytics.loop_index import has_embedding
 from netra.analytics.route import build_route
 from netra.core import auth
@@ -151,20 +152,73 @@ def gap_analysis():
             f"in their current state.",
             f"{len(anpr_capable)} cameras have plate geometry adequate for ANPR; "
             f"the remainder are wide-area overview cameras where plate recognition "
             f"is unreliable and vehicle-level analytics apply instead.",
             f"Cross-camera route reconstruction is valid only within a shared "
             f"recording session; {len(TIME_GROUPS)} such groups exist in this grid.",
         ],
     }
 
 
+#: A snapshot opens an RTSP connection, so it is cached: an operator placing
+#: points in the zone editor clicks many times on one camera and must not cost
+#: one connection per click. Short enough that the still stays current.
+SNAPSHOT_TTL_S = 30.0
+SNAPSHOT_TIMEOUT_S = 25.0
+_snapshots: dict[str, tuple[float, bytes]] = {}
+
+
+@app.get("/api/cameras/{camera_id}/snapshot")
+def camera_snapshot(camera_id: str, refresh: bool = False):
+    """One still frame from a camera, for placing zone rules on.
+
+    Points are stored normalised, so the still only has to show the operator
+    the scene; it does not have to match the resolution the pipeline decodes.
+    """
+    import os
+    import subprocess
+    import tempfile
+    import time as _time
+
+    with SessionLocal() as db:
+        if not db.get(Camera, camera_id):
+            raise HTTPException(404, "camera not found")
+
+    cached = _snapshots.get(camera_id)
+    if cached and not refresh and (_time.time() - cached[0]) < SNAPSHOT_TTL_S:
+        data = cached[1]
+    else:
+        fd, path = tempfile.mkstemp(suffix=".jpg")
+        os.close(fd)
+        try:
+            subprocess.run(
+                ["ffmpeg", "-v", "error", "-rtsp_transport", "tcp",
+                 "-i", config.rtsp_url(camera_id), "-frames:v", "1",
+                 "-q:v", "4", path, "-y"],
+                capture_output=True, timeout=SNAPSHOT_TIMEOUT_S)
+            data = open(path, "rb").read() if os.path.exists(path) else b""
+        except Exception as exc:                      # timeout, no ffmpeg, ...
+            log.warning("snapshot failed for %s: %s", camera_id, exc)
+            data = b""
+        finally:
+            if os.path.exists(path):
+                os.unlink(path)
+        if len(data) < 1000:
+            raise HTTPException(
+                503, f"could not grab a frame from {camera_id} within "
+                     f"{SNAPSHOT_TIMEOUT_S:.0f}s")
+        _snapshots[camera_id] = (_time.time(), data)
+
+    return Response(content=data, media_type="image/jpeg",
+                    headers={"Cache-Control": "no-store"})
+
+
 # -------------------------------------------------------------- detections --
 @app.get("/api/detections")
 def list_detections(camera_id: str | None = None, plate: str | None = None,
                     vehicle_class: str | None = None, colour: str | None = None,
                     since_minutes: int | None = None,
                     limit: int = Query(100, le=1000), offset: int = 0):
     with SessionLocal() as db:
         q = db.query(Detection).options(joinedload(Detection.camera))
         if camera_id:
             q = q.filter(Detection.camera_id == camera_id)
@@ -183,21 +237,24 @@ def list_detections(camera_id: str | None = None, plate: str | None = None,
         items = [{
             "id": d.id, "camera_id": d.camera_id,
             "camera_name": d.camera.name if d.camera else None,
             "lat": d.camera.lat if d.camera else None,
             "lon": d.camera.lon if d.camera else None,
             "at": d.wall_time.isoformat(),
             "pts_ms": d.pts_ms,
             "vehicle_class": d.vehicle_class, "confidence": round(d.confidence, 3),
             "colour": d.colour, "plate_text": d.plate_text,
             "plate_conf": round(d.plate_conf, 3) if d.plate_conf else None,
+            "plate_chars": d.plate_chars,
             "evidence": d.evidence_path, "bbox": d.bbox,
+            "track_id": d.track_id,
+            "scene_time": d.scene_time.isoformat() if d.scene_time else None,
         } for d in rows]
     return {"total": total, "count": len(items), "items": items}
 
 
 @app.get("/api/detections/stats")
 def detection_stats():
     with SessionLocal() as db:
         total = db.query(func.count(Detection.id)).scalar() or 0
         with_plate = db.query(func.count(Detection.id)).filter(
             Detection.plate_text.isnot(None)).scalar() or 0
diff --git a/netra/web/app.js b/netra/web/app.js
index c60974b..d5fee7a 100644
--- a/netra/web/app.js
+++ b/netra/web/app.js
@@ -13,20 +13,23 @@ $$("nav a").forEach(a => a.onclick = () => {
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
+  if (a.dataset.view === "zones") loadZones();
+  if (a.dataset.view === "traffic") loadTraffic();
+  if (a.dataset.view === "intel") loadIntel();
 });
 
 function toast(html) {
   const d = document.createElement("div");
   d.className = "t";
   d.innerHTML = html;
   $("#toasts").appendChild(d);
   setTimeout(() => d.remove(), 9000);
 }
 
@@ -169,28 +172,33 @@ $("#btnWallStop").onclick = () => {
 
 /* ---------------------------------------------------------- detections --- */
 async function loadDetections() {
   const q = new URLSearchParams({ limit: "200" });
   if ($("#dPlate").value) q.set("plate", $("#dPlate").value);
   if ($("#dCam").value) q.set("camera_id", $("#dCam").value);
   if ($("#dClass").value) q.set("vehicle_class", $("#dClass").value);
   const d = await api("/api/detections?" + q);
   $("#dCount").textContent = `${d.count} of ${d.total}`;
   const tb = $("#tblDet tbody");
-  if (!d.items.length) { tb.innerHTML = `<tr><td colspan="8" class="empty">No matching detections.</td></tr>`; return; }
+  if (!d.items.length) { tb.innerHTML = `<tr><td colspan="10" class="empty">No matching detections.</td></tr>`; return; }
   tb.innerHTML = d.items.map(x => `<tr>
     <td class="mono">${esc(x.at.replace("T", " ").slice(0, 19))}</td>
     <td class="mono faint">${Math.round(x.pts_ms)}</td>
     <td>${esc(x.camera_name || x.camera_id)}</td>
     <td>${esc(x.vehicle_class)}</td><td class="dim">${esc(x.colour || "—")}</td>
-    <td class="mono">${x.plate_text ? esc(x.plate_text) : '<span class="faint">—</span>'}</td>
+    <td class="mono">${x.plate_text ? esc(x.plate_text) : '<span class="faint">—</span>'}
+      ${x.plate_chars ? `<span class="faint" style="font-size:10.5px">· ${esc(x.plate_chars)} chars</span>` : ""}</td>
     <td class="dim">${x.plate_conf ?? "—"}</td>
+    <td class="mono faint">${x.track_id != null ? esc(x.track_id) : "—"}</td>
+    <td class="mono faint">${x.scene_time
+      ? esc(x.scene_time.replace("T", " ").slice(0, 19))
+      : '<span title="no clock recovered from the overlay">—</span>'}</td>
     <td>${x.evidence ? `<img src="${esc(x.evidence)}" style="height:34px;border-radius:4px">` : ""}</td></tr>`).join("");
 }
 $("#dSearch").onclick = loadDetections;
 $("#dCsv").onclick = () => {
   const p = $("#dPlate").value;
   location.href = "/api/export/detections.csv" + (p ? "?plate=" + encodeURIComponent(p) : "");
 };
 
 /* --------------------------------------------------------------- route --- */
 function initRouteMap() {
@@ -364,20 +372,34 @@ function connectWs() {
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
+    if (a.kind === "zone") {
+      // A rule breach is not a watchlist hit, and rendering one as the other
+      // would put an identity claim on an event that carries none.
+      feed.insertAdjacentHTML("afterbegin", zoneEventHtml(a));
+      const ze = $("#zEvents");
+      if (ze) {
+        if (ze.querySelector(".empty")) ze.innerHTML = "";
+        ze.insertAdjacentHTML("afterbegin", zoneEventHtml(a));
+      }
+      toast(`<b style="color:#ffb066">ZONE ${esc(a.rule)}</b><br>
+        <span style="font-size:12px;color:#8b9bb4">${esc(a.zone || "")} ·
+        ${esc(a.camera_id)} · ${esc(a.detail || "")}</span>`);
+      return;
+    }
     feed.insertAdjacentHTML("afterbegin", alertHtml(a));
     toast(`<b style="color:#ff8080">WATCHLIST HIT</b><br>
       <span class="mono" style="font-size:15px">${esc(a.plate_watchlist)}</span><br>
       <span style="font-size:12px;color:#8b9bb4">${esc(a.camera_id)} · ${esc(a.match_type)} · score ${a.score}</span>`);
   };
 }
 
 (async function init() {
   await loadCameras();
   await refresh();
@@ -479,19 +501,296 @@ $("#rAppearance").onclick = async () => {
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
-          ${h.leg_km != null ? `· ${h.leg_km} km from previous` : ""}</div>
+          ${h.leg_km != null ? `· ${h.leg_km} km from previous` : ""}
+          ${h.ambiguous ? `<div class="tag t-degraded" style="margin-top:4px">ambiguous</div>
+            <div class="faint" style="font-size:11px;margin-top:3px">${esc(h.ambiguity_note || "")}</div>` : ""}</div>
         ${h.evidence || h.evidence_path ? `<img src="${esc(h.evidence || h.evidence_path)}">` : ""}
       </div></div>`).join("");
 
   $("#routeRejected").innerHTML = r.rejected.length
     ? r.rejected.map(x => `<div class="finding" style="border-color:var(--bad);background:rgba(239,68,68,.06)">
         <b class="mono">${esc(x.camera_id)}</b> — ${esc(x.plausibility || "excluded")}</div>`).join("")
     : `<div class="faint" style="font-size:12px">None excluded.</div>`;
 };
+
+/* ---------------------------------------------------------------- zones --- */
+let ZPOINTS = [];                      // normalised [x, y] pairs, in click order
+
+function zoneEventHtml(e) {
+  return `<div class="zev">
+    <div class="row" style="margin:0 0 5px 0;gap:8px">
+      <span class="tag sev-${esc(e.severity || "medium")}">${esc(e.severity || "")}</span>
+      <span class="tag t-vehicle">${esc(e.rule)}</span>
+      <b>${esc(e.zone || "zone")}</b>
+      <span class="faint mono" style="font-size:11px">${esc(e.camera_name || e.camera_id)}</span>
+      <span style="margin-left:auto" class="faint mono">${esc((e.at || "").slice(11, 19))}</span>
+    </div>
+    <div class="dim">${esc(e.detail || "")}
+      ${e.object_class ? `· ${esc(e.object_class)}` : ""}
+      ${e.direction ? `· heading ${esc(e.direction)}` : ""}</div>
+    ${e.evidence ? `<img src="${esc(e.evidence)}">` : ""}
+  </div>`;
+}
+
+function drawZone() {
+  const cv = $("#zCanvas"), img = $("#zImg");
+  if (!cv || !img || !img.naturalWidth) return;
+  cv.width = img.clientWidth; cv.height = img.clientHeight;
+  const g = cv.getContext("2d");
+  g.clearRect(0, 0, cv.width, cv.height);
+  const pts = ZPOINTS.map(([x, y]) => [x * cv.width, y * cv.height]);
+  if (!pts.length) return;
+  g.strokeStyle = "#ff6b00"; g.lineWidth = 2;
+  g.fillStyle = "rgba(255,107,0,.18)";
+  g.beginPath();
+  pts.forEach(([x, y], i) => i ? g.lineTo(x, y) : g.moveTo(x, y));
+  if ($("#zRule").value !== "crossing" && pts.length > 2) { g.closePath(); g.fill(); }
+  g.stroke();
+  pts.forEach(([x, y], i) => {
+    g.beginPath(); g.arc(x, y, 6, 0, 6.283); g.fillStyle = "#ff6b00"; g.fill();
+    g.fillStyle = "#111"; g.font = "bold 10px monospace";
+    g.fillText(String(i + 1), x - 3, y + 3);
+  });
+}
+
+$("#zLoad").onclick = async () => {
+  const cam = $("#zCam").value;
+  if (!cam) return;
+  const btn = $("#zLoad");
+  btn.disabled = true; btn.textContent = "Grabbing frame…";
+  const img = $("#zImg");
+  img.onload = () => { $("#zWrap").style.display = "inline-block"; drawZone(); };
+  img.onerror = () => toast("Could not grab a still from " + esc(cam) +
+    " — the camera may be down or the feed unreachable.");
+  img.src = `/api/cameras/${encodeURIComponent(cam)}/snapshot?t=${Date.now()}`;
+  try { await img.decode(); } catch (e) { /* onerror has already reported it */ }
+  btn.disabled = false; btn.textContent = "Load still frame";
+};
+
+$("#zCanvas").onclick = (e) => {
+  const r = e.currentTarget.getBoundingClientRect();
+  ZPOINTS.push([(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]);
+  drawZone();
+  $("#zHint").textContent = `${ZPOINTS.length} point(s) placed.`;
+};
+$("#zClear").onclick = () => {
+  ZPOINTS = []; drawZone(); $("#zHint").textContent = "Points cleared.";
+};
+$("#zRule").onchange = drawZone;
+window.addEventListener("resize", drawZone);
+
+$("#zSave").onclick = async () => {
+  const rule = $("#zRule").value;
+  const needed = rule === "crossing" ? 2 : 3;
+  if (ZPOINTS.length < needed) {
+    toast(`A ${esc(rule)} rule needs at least ${needed} points.`); return;
+  }
+  const body = {
+    camera_id: $("#zCam").value, name: $("#zName").value || "Zone",
+    rule, points: ZPOINTS.map(([x, y]) => [+x.toFixed(4), +y.toFixed(4)]),
+    classes: $("#zClasses").value ? [$("#zClasses").value] : [],
+    severity: $("#zSev").value, dwell_s: parseFloat($("#zDwell").value) || 30,
+  };
+  const r = await fetch("/api/zones", {
+    method: "POST", headers: { "Content-Type": "application/json" },
+    body: JSON.stringify(body),
+  });
+  const out = await r.json().catch(() => ({}));
+  if (!r.ok) { toast("Rule rejected: " + esc(out.detail || r.status)); return; }
+  ZPOINTS = []; drawZone();
+  toast("Rule saved on " + esc(body.camera_id) + ".");
+  loadZones();
+};
+
+window.delZone = async (id) => {
+  await fetch("/api/zones/" + id, { method: "DELETE" });
+  loadZones();
+};
+
+async function loadZones() {
+  if (!$("#zCam").options.length) {
+    $("#zCam").innerHTML = CAMERAS.map(c =>
+      `<option value="${esc(c.id)}">${esc(c.id)} — ${esc(c.name)}</option>`).join("");
+  }
+  const zones = await api("/api/zones");
+  $("#zList").innerHTML = zones.length ? zones.map(z => `<div class="zone-item">
+    <div style="flex:1">
+      <div><b>${esc(z.name)}</b>
+        <span class="tag t-vehicle" style="margin-left:6px">${esc(z.rule)}</span>
+        <span class="tag sev-${esc(z.severity)}" style="margin-left:4px">${esc(z.severity)}</span>
+        ${z.active ? "" : `<span class="tag t-degraded" style="margin-left:4px">inactive</span>`}</div>
+      <div class="faint mono" style="font-size:11.5px;margin-top:3px">${esc(z.camera_id)} ·
+        ${esc((z.points || []).length)} points ·
+        ${(z.classes || []).length ? esc((z.classes || []).join(", ")) : "any class"}
+        ${z.rule === "loitering" ? `· dwell ${esc(z.dwell_s)}s` : ""}</div>
+    </div>
+    <button onclick="delZone(${z.id})">Delete</button></div>`).join("")
+    : `<div class="empty">No rules configured.</div>`;
+
+  const events = await api("/api/zones/events?limit=50");
+  $("#zEvents").innerHTML = events.length
+    ? events.map(zoneEventHtml).join("") : `<div class="empty">No zone events yet.</div>`;
+}
+
+/* -------------------------------------------------------------- traffic --- */
+function sparks(values) {
+  const max = Math.max(1, ...values);
+  return `<div class="sparks">${values.map(v =>
+    `<i style="height:${Math.round(100 * v / max)}%" title="${esc(v)}"></i>`).join("")}</div>`;
+}
+const kv = (obj) => Object.entries(obj || {})
+  .map(([k, v]) => `${esc(k)} ${esc(v)}`).join(" · ") || "—";
+
+async function loadTraffic() {
+  const live = await api("/api/traffic/live");
+  const history = await api("/api/traffic/history?limit=1000");
+  const byCam = {};
+  history.forEach(h => (byCam[h.camera_id] = byCam[h.camera_id] || []).push(h.total));
+
+  const cams = live.cameras || [];
+  const totals = cams.reduce((a, c) => a + (c.total_counted || 0), 0);
+  const loop = cams.reduce((a, c) => a + (c.counted_this_loop || 0), 0);
+  const active = cams.reduce((a, c) => a + (c.active_tracks || 0), 0);
+  const loops = cams.length ? Math.max(...cams.map(c => c.loops_seen || 0)) : 0;
+  $("#tStats").innerHTML = [
+    card(cams.length, "Cameras counting", cams.length ? "ok" : ""),
+    card(active, "Active tracks", "", "being followed right now"),
+    card(loop, "Counted this loop", "ok", "current pass of the recording"),
+    card(totals, "Total counted", "", "cumulative across every replay"),
+    card(loops, "Loops seen", loops > 1 ? "warn" : "", "recording replays observed"),
+    card(live.zone_events ?? 0, "Zone events", live.zone_events ? "warn" : "", "rule breaches raised"),
+  ].join("");
+
+  const tb = $("#tblTraffic tbody");
+  tb.innerHTML = cams.length ? cams.map(c => {
+    const cam = CAMERAS.find(x => x.id === c.camera_id);
+    const hist = (byCam[c.camera_id] || []).slice(0, 30).reverse();
+    return `<tr>
+      <td><b>${esc(cam ? cam.name : c.camera_id)}</b>
+        <div class="faint mono" style="font-size:11px">${esc(c.camera_id)}</div></td>
+      <td class="mono">${esc(c.active_tracks)}</td>
+      <td class="mono" style="color:#4ade80;font-weight:700">${esc(c.counted_this_loop)}</td>
+      <td class="mono">${esc(c.total_counted)}</td>
+      <td class="mono faint">${esc(c.loops_seen)}</td>
+      <td class="mono faint">${esc(c.dropped_tracks)}</td>
+      <td class="dim">${kv(c.counts_by_class)}</td>
+      <td class="dim">${kv(c.directions)}</td>
+      <td class="mono">${esc(c.mean_dwell_s)}s</td>
+      <td style="min-width:110px">${hist.length ? sparks(hist)
+        : `<span class="faint">no snapshots</span>`}</td></tr>`;
+  }).join("") : `<tr><td colspan="10" class="empty">No cameras counting — start the pipeline.</td></tr>`;
+}
+$("#tRefresh").onclick = loadTraffic;
+$("#tSnap").onclick = async () => {
+  const r = await api("/api/traffic/snapshot", { method: "POST" });
+  toast(`Traffic snapshot written for ${esc(r.buckets_written)} camera(s).`);
+  loadTraffic();
+};
+
+/* --------------------------------------------------------- intelligence --- */
+async function loadIntel() {
+  if (!$("#iGroup").options.length) {
+    const groups = [...new Set(CAMERAS.map(c => c.time_group).filter(Boolean))].sort();
+    $("#iGroup").innerHTML = groups.map(g =>
+      `<option value="${esc(g)}">${esc(g)}</option>`).join("");
+  }
+  loadClones(); loadAnomalies(); loadJourneys();
+}
+$("#iRefresh").onclick = loadIntel;
+$("#iGroup").onchange = loadJourneys;
+
+async function loadClones() {
+  const r = await api("/api/analytics/cloned-plates");
+  const f = r.findings || [];
+  $("#iClones").innerHTML =
+    `<div class="faint" style="font-size:12px;margin-bottom:9px">${esc(r.note)}</div>` +
+    (f.length ? f.map(x => `<div class="finding" style="border-color:var(--bad);background:rgba(239,68,68,.06)">
+      <b class="mono" style="font-size:14px;color:#fff">${esc(x.plate)}</b>
+      <span class="faint mono" style="font-size:11px">· confidence ${esc(x.confidence)}</span>
+      <div style="margin-top:5px">${esc(x.sighting_a.camera_name)}
+        <span class="faint mono">${esc(x.sighting_a.at)}</span> &rarr;
+        ${esc(x.sighting_b.camera_name)}
+        <span class="faint mono">${esc(x.sighting_b.at)}</span></div>
+      <div class="dim" style="margin-top:4px">${esc(x.distance_km)} km ·
+        ${esc(x.elapsed_s)} s ·
+        ${x.implied_kmh == null ? "speed not computable" : esc(x.implied_kmh) + " km/h implied"}</div>
+      <div style="margin-top:5px">${esc(x.reason)}</div></div>`).join("")
+      : `<div class="empty">No cloned-plate findings in the stored detections.</div>`);
+}
+
+async function loadAnomalies() {
+  const r = await api("/api/analytics/anomalies");
+  const a = r.assessments || [];
+  const head = `<div class="faint" style="font-size:12px;margin-bottom:9px">
+    ${esc(r.cameras_assessed)} camera(s) assessed against ${esc(r.buckets_read)} stored buckets ·
+    ${esc(r.anomalies)} flagged. Cameras with too little history to judge are shown muted rather
+    than hidden: hiding them would imply coverage that does not exist.</div>`;
+  $("#iAnoms").innerHTML = head + (a.length ? a.map(x => {
+    const thin = x.status === "insufficient_data";
+    const colour = thin ? "var(--faint)" : (x.anomalous ? "var(--warn)" : "var(--ok)");
+    return `<div class="finding ${thin ? "muted" : ""}"
+      style="border-color:${colour};background:rgba(59,130,246,.05)">
+      <b class="mono">${esc(x.camera_id)}</b>
+      <span class="tag ${thin ? "t-unknown" : (x.anomalous ? "sev-high" : "t-anpr")}"
+        style="margin-left:6px">${esc(x.status)}</span>
+      <span class="faint mono" style="font-size:11px;margin-left:6px">hour ${esc(x.hour)} UTC ·
+        observed ${esc(x.observed)}${x.z_score == null ? "" : " · z " + esc(x.z_score)}</span>
+      <div style="margin-top:4px">${esc(x.explanation)}</div>
+      ${x.baseline ? `<div class="faint" style="font-size:11px;margin-top:3px">
+        baseline mean ${esc(x.baseline.mean)} · stdev ${esc(x.baseline.stdev)} ·
+        ${esc(x.baseline.samples)} samples</div>` : ""}</div>`;
+  }).join("") : `<div class="empty">No traffic snapshots stored yet — write one from the Traffic tab.</div>`);
+}
+
+async function loadJourneys() {
+  const g = $("#iGroup").value;
+  if (!g) { $("#iJourneys").innerHTML = `<div class="empty">No time group available.</div>`; return; }
+  const r = await api("/api/analytics/journeys?group=" + encodeURIComponent(g));
+  if (r.detail) { $("#iJourneys").innerHTML = `<div class="empty">${esc(r.detail)}</div>`; return; }
+  const idx = r.index || {};
+  let head = `<div class="faint" style="font-size:12px;margin-bottom:9px">
+    ${esc(r.note)}<br>Index: ${esc(idx.detections_in_group ?? 0)} detections ·
+    ${esc(idx.comparable ?? 0)} comparable ·
+    ${esc(idx.excluded_no_scene_time ?? 0)} without a scene clock ·
+    ${esc(idx.excluded_no_embedding ?? 0)} without an appearance vector.</div>`;
+  if (r.mining_skipped) {
+    head += `<div class="finding">Mining was skipped: this group has been mined already and held no
+      journeys, so it is not re-derived on every poll. Nothing re-mines on a timer —
+      next mine ${esc(r.next_mine)}.</div>`;
+  }
+  const js = r.journeys || [];
+  if (!js.length) {
+    $("#iJourneys").innerHTML = head +
+      `<div class="empty">No cross-camera journey found in the indexed recordings.</div>`;
+    return;
+  }
+  $("#iJourneys").innerHTML = head + js.map((j, n) => `<div class="panel" style="margin-bottom:12px">
+    <h3>Journey ${n + 1} · ${esc(j.hop_count)} hops · ${esc(j.total_km)} km ·
+      confidence ${esc(j.confidence)}
+      ${j.truncated ? `<span class="tag t-degraded">truncated</span>` : ""}</h3>
+    <div class="body">
+      ${j.note ? `<div class="faint" style="font-size:11.5px;margin-bottom:8px">${esc(j.note)}</div>` : ""}
+      ${(j.hops || []).map((h, i) => `<div class="hop">
+        <div class="num">${i + 1}</div>
+        <div style="flex:1">
+          <div><b>${esc(h.camera_name || h.camera_id)}</b>
+            <span class="faint mono">${esc(h.camera_id)}</span></div>
+          <div class="mono dim" style="font-size:11.5px">${esc(h.at)}</div>
+          <div style="font-size:11.5px;margin-top:3px">
+            ${esc(h.colour || "")} ${esc(h.vehicle_class || "")}
+            ${h.plate_text ? `· plate <span class="mono">${esc(h.plate_text)}</span>` : ""}
+            ${h.similarity != null ? `· <b style="color:#c99bff">similarity ${esc(h.similarity)}</b>`
+              : "· first sighting"}
+            ${h.leg_km != null ? `· ${esc(h.leg_km)} km · ${esc(h.implied_kmh ?? "?")} km/h` : ""}</div>
+          ${h.reason ? `<div class="faint" style="font-size:11px;margin-top:3px">${esc(h.reason)}</div>` : ""}
+          ${h.evidence_path ? `<img src="${esc(h.evidence_path)}">` : ""}
+        </div></div>`).join("")}
+    </div></div>`).join("");
+}
diff --git a/netra/web/index.html b/netra/web/index.html
index 86d055f..7aec271 100644
--- a/netra/web/index.html
+++ b/netra/web/index.html
@@ -98,20 +98,30 @@ input.mono{font-family:var(--mono);letter-spacing:1px;text-transform:uppercase}
 .empty{text-align:center;padding:50px 20px;color:var(--faint);font-size:13px}
 .two{display:grid;grid-template-columns:1fr 380px;gap:14px;align-items:start}
 @media(max-width:1100px){.two{grid-template-columns:1fr}}
 .finding{background:rgba(59,130,246,.07);border-left:3px solid var(--blue);padding:10px 13px;border-radius:0 7px 7px 0;font-size:12.5px;margin-bottom:8px;line-height:1.6;color:#b9c8dd}
 .bar{height:7px;background:#0a0e14;border-radius:4px;overflow:hidden;margin-top:6px}
 .bar span{display:block;height:100%;background:var(--blue)}
 .hop{display:flex;gap:12px;padding:11px 0;border-bottom:1px solid var(--line);align-items:flex-start}
 .hop:last-child{border-bottom:none}
 .hop .num{width:26px;height:26px;border-radius:50%;background:var(--accent);color:#111;display:grid;place-items:center;font-weight:800;font-size:12px;flex-shrink:0}
 .hop img{height:52px;border-radius:5px;border:1px solid var(--line)}
+.muted{opacity:.55}
+.zwrap{position:relative;display:inline-block;background:#000;border:1px solid var(--line);border-radius:9px;overflow:hidden;max-width:100%}
+.zwrap img{display:block;max-width:100%}
+.zwrap canvas{position:absolute;inset:0;width:100%;height:100%;cursor:crosshair}
+.zone-item{display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px solid var(--line)}
+.zone-item:last-child{border-bottom:none}
+.sparks{display:flex;align-items:flex-end;gap:2px;height:38px;margin-top:8px}
+.sparks i{flex:1;background:var(--blue);border-radius:2px 2px 0 0;min-height:2px;display:block}
+.zev{border-left:3px solid var(--warn);background:var(--panel2);border-radius:0 9px 9px 0;padding:10px 13px;margin-bottom:8px;font-size:12.5px}
+.zev img{max-height:56px;border-radius:5px;border:1px solid var(--line);margin-top:6px}
 .toast{position:fixed;right:18px;bottom:18px;z-index:9999;display:flex;flex-direction:column;gap:9px;max-width:390px}
 .toast .t{background:var(--panel2);border:1px solid var(--bad);border-left:4px solid var(--bad);border-radius:9px;padding:12px 15px;box-shadow:0 12px 32px rgba(0,0,0,.6);animation:slideIn .25s}
 </style>
 </head>
 <body>
 
 <header>
   <div class="logo">NET<b>RA</b></div>
   <div class="tagline">Networked Evidence, Tracking &amp; Recognition for Analytics<br>
     <span style="color:var(--faint)">Gujarat Police Innovation Challenge 2026 · Model 1 + Model 2</span></div>
@@ -123,20 +133,23 @@ input.mono{font-family:var(--mono);letter-spacing:1px;text-transform:uppercase}
 </header>
 
 <nav>
   <a data-view="overview" class="active">Overview</a>
   <a data-view="map">GIS Map</a>
   <a data-view="wall">Video Wall</a>
   <a data-view="detections">Detections</a>
   <a data-view="route">Vehicle Trace</a>
   <a data-view="watchlist">Watchlist</a>
   <a data-view="alerts">Alerts</a>
+  <a data-view="zones">Zones</a>
+  <a data-view="traffic">Traffic</a>
+  <a data-view="intel">Intelligence</a>
   <a data-view="registry">Registry &amp; Gaps</a>
   <a data-view="assistant">Assistant</a>
 </nav>
 
 <main>
   <!-- OVERVIEW -->
   <section class="view active" id="v-overview">
     <div class="grid stats" id="statCards"></div>
     <div class="two">
       <div class="panel">
@@ -187,22 +200,22 @@ input.mono{font-family:var(--mono);letter-spacing:1px;text-transform:uppercase}
     <div class="row">
       <input id="dPlate" class="mono" placeholder="Plate contains…" style="width:170px">
       <select id="dCam"><option value="">All cameras</option></select>
       <select id="dClass"><option value="">All classes</option><option>car</option><option>truck</option>
         <option>bus</option><option>motorcycle</option></select>
       <button id="dSearch" class="primary">Search</button>
       <button id="dCsv">Export CSV</button>
       <span class="faint" id="dCount" style="font-size:12px"></span>
     </div>
     <div class="panel"><div style="max-height:calc(100vh - 250px);overflow:auto"><table id="tblDet">
-      <thead><tr><th>Time (UTC)</th><th>PTS</th><th>Camera</th><th>Class</th><th>Colour</th><th>Plate</th><th>Conf</th><th>Evidence</th></tr></thead>
-      <tbody><tr><td colspan="8" class="empty">No detections.</td></tr></tbody>
+      <thead><tr><th>Time (UTC)</th><th>PTS</th><th>Camera</th><th>Class</th><th>Colour</th><th>Plate</th><th>Conf</th><th>Track</th><th>Scene time</th><th>Evidence</th></tr></thead>
+      <tbody><tr><td colspan="10" class="empty">No detections.</td></tr></tbody>
     </table></div></div>
   </section>
 
   <!-- ROUTE -->
   <section class="view" id="v-route">
     <div class="row">
       <input id="rPlate" class="mono" placeholder="GJ01AB1234" style="width:200px">
       <button id="rGo" class="primary">Trace by plate</button>
       <span style="width:14px"></span>
       <input id="rDet" placeholder="detection id" style="width:120px">
@@ -264,20 +277,94 @@ input.mono{font-family:var(--mono);letter-spacing:1px;text-transform:uppercase}
         </div>
         <div class="row" style="margin-bottom:8px">
           <input id="asstQ" placeholder="e.g. which cameras are down?" style="flex:1;min-width:260px">
           <button id="asstGo" class="primary">Ask</button>
         </div>
         <div class="row" id="asstChips" style="margin:0"></div>
       </div>
     </div>
   </section>
 
+  <!-- ZONES -->
+  <section class="view" id="v-zones">
+    <div class="two">
+      <div>
+        <div class="panel" style="margin-bottom:14px">
+          <h3>Define a rule on a live still</h3>
+          <div class="body">
+            <div class="row">
+              <select id="zCam"></select>
+              <button id="zLoad" class="primary">Load still frame</button>
+              <select id="zRule">
+                <option value="intrusion">Intrusion (area)</option>
+                <option value="crossing">Line crossing (2 points)</option>
+                <option value="loitering">Loitering (area)</option>
+              </select>
+              <input id="zName" placeholder="Rule name" style="width:150px">
+              <select id="zClasses"><option value="">Any class</option><option>car</option>
+                <option>truck</option><option>bus</option><option>motorcycle</option><option>person</option></select>
+              <select id="zSev"><option value="critical">Critical</option><option value="high">High</option>
+                <option value="medium" selected>Medium</option><option value="low">Low</option></select>
+              <input id="zDwell" type="number" value="30" style="width:90px" title="Dwell seconds (loitering)">
+              <button id="zClear">Clear points</button>
+              <button id="zSave" class="primary">Save rule</button>
+            </div>
+            <div class="zwrap" id="zWrap" style="display:none">
+              <img id="zImg" alt="camera still">
+              <canvas id="zCanvas"></canvas>
+            </div>
+            <div class="faint" id="zHint" style="font-size:12px;margin-top:8px">
+              Load a still, then click to place points. Points are stored normalised 0&ndash;1,
+              so a rule survives a resolution change on the source camera.</div>
+          </div>
+        </div>
+        <div class="panel"><h3>Live zone events</h3>
+          <div class="body" id="zEvents" style="max-height:340px;overflow:auto">
+            <div class="empty">No zone events yet.</div></div></div>
+      </div>
+      <div class="panel"><h3>Configured rules</h3>
+        <div class="body" id="zList"><div class="empty">No rules configured.</div></div></div>
+    </div>
+  </section>
+
+  <!-- TRAFFIC -->
+  <section class="view" id="v-traffic">
+    <div class="row">
+      <button id="tSnap" class="primary">Write traffic snapshot</button>
+      <button id="tRefresh">Refresh</button>
+      <span class="faint" style="font-size:12px">Counts are cumulative across every replay of a looping
+        recording. <b>Counted this loop</b> beside <b>loops seen</b> is the honest reading.</span>
+    </div>
+    <div class="grid stats" id="tStats"></div>
+    <div class="panel"><h3>Per-camera counters</h3>
+      <div style="max-height:calc(100vh - 380px);overflow:auto"><table id="tblTraffic">
+        <thead><tr><th>Camera</th><th>Active tracks</th><th>Counted this loop</th><th>Total counted</th>
+          <th>Loops seen</th><th>Dropped</th><th>Class mix</th><th>Directions</th><th>Mean dwell</th>
+          <th>History</th></tr></thead>
+        <tbody><tr><td colspan="10" class="empty">Start the pipeline to begin counting.</td></tr></tbody>
+      </table></div></div>
+  </section>
+
+  <!-- INTELLIGENCE -->
+  <section class="view" id="v-intel">
+    <div class="row"><button id="iRefresh" class="primary">Refresh analysis</button>
+      <select id="iGroup"></select>
+      <span class="faint" style="font-size:12px">Every finding here is inference from wide-area footage,
+        carrying the arithmetic behind it. None of it is an identification.</span></div>
+    <div class="panel" style="margin-bottom:14px"><h3>Cloned-plate findings</h3>
+      <div class="body" id="iClones"><div class="empty">Not analysed yet.</div></div></div>
+    <div class="panel" style="margin-bottom:14px"><h3>Behavioural anomalies</h3>
+      <div class="body" id="iAnoms"><div class="empty">Not analysed yet.</div></div></div>
+    <div class="panel"><h3>Mined cross-camera journeys</h3>
+      <div class="body" id="iJourneys"><div class="empty">Not analysed yet.</div></div></div>
+  </section>
+
   <!-- REGISTRY -->
   <section class="view" id="v-registry">
     <div class="row">
       <button id="regOnboard" class="primary">Re-run onboarding &amp; profiling</button>
       <span class="faint" style="font-size:12px">Probes every camera, measures signal quality, and classifies what each can deliver.</span>
     </div>
     <div id="gapFindings" style="margin-bottom:14px"></div>
     <div class="grid stats" id="gapStats"></div>
     <div class="panel"><h3>Camera registry</h3>
       <div style="max-height:calc(100vh - 430px);overflow:auto"><table id="tblReg">
