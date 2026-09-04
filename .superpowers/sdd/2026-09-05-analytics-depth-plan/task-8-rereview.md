# Re-review — Task 8 fix round 1

90600c3 Vatsa Joshi | De-duplicate concurrent snapshot grabs and gate the endpoint on read
cd29d31 Vatsa10 | fix: address serialization issues and enhance API security in Task 8
77c2519 Vatsa10 | feat: add vision-language model for vehicle attribute extraction

diff --git a/netra/api/app.py b/netra/api/app.py
index d037dc9..0542703 100644
--- a/netra/api/app.py
+++ b/netra/api/app.py
@@ -1,18 +1,19 @@
 """NETRA REST + WebSocket API and operator console."""
 from __future__ import annotations
 
 import asyncio
 import csv
 import io
 import json
 import logging
+import threading
 from datetime import datetime, timedelta, timezone
 
 from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
 from fastapi.responses import (HTMLResponse, JSONResponse, Response,
                                StreamingResponse)
 from fastapi.staticfiles import StaticFiles
 from sqlalchemy import func, select
 from sqlalchemy.orm import joinedload
 
 from fastapi import Depends, Header
@@ -159,61 +160,94 @@ def gap_analysis():
     }
 
 
 #: A snapshot opens an RTSP connection, so it is cached: an operator placing
 #: points in the zone editor clicks many times on one camera and must not cost
 #: one connection per click. Short enough that the still stays current.
 SNAPSHOT_TTL_S = 30.0
 SNAPSHOT_TIMEOUT_S = 25.0
 _snapshots: dict[str, tuple[float, bytes]] = {}
 
+#: One lock per camera, so concurrent callers for the same camera wait on the
+#: grab already running instead of each starting their own. Without it the
+#: cache only protects the warm path: five clicks on "Load still frame" before
+#: the first returns would be five ffmpeg processes holding five threadpool
+#: threads for seventeen seconds apiece, which is how a snapshot request ends
+#: up starving /api/pipeline/status. The registry of locks needs its own lock
+#: because it is filled lazily from several request threads.
+_snapshot_locks: dict[str, threading.Lock] = {}
+_snapshot_locks_guard = threading.Lock()
 
-@app.get("/api/cameras/{camera_id}/snapshot")
-def camera_snapshot(camera_id: str, refresh: bool = False):
-    """One still frame from a camera, for placing zone rules on.
 
-    Points are stored normalised, so the still only has to show the operator
-    the scene; it does not have to match the resolution the pipeline decodes.
-    """
+def _snapshot_lock(camera_id: str) -> threading.Lock:
+    with _snapshot_locks_guard:
+        return _snapshot_locks.setdefault(camera_id, threading.Lock())
+
+
+def _cached_snapshot(camera_id: str) -> bytes | None:
+    import time as _time
+    hit = _snapshots.get(camera_id)
+    if hit and (_time.time() - hit[0]) < SNAPSHOT_TTL_S:
+        return hit[1]
+    return None
+
+
+def _grab_snapshot(camera_id: str) -> bytes:
+    """One JPEG off the camera, bounded in time. Caller holds the camera lock."""
     import os
     import subprocess
     import tempfile
     import time as _time
 
+    fd, path = tempfile.mkstemp(suffix=".jpg")
+    os.close(fd)
+    try:
+        subprocess.run(
+            ["ffmpeg", "-v", "error", "-rtsp_transport", "tcp",
+             "-i", config.rtsp_url(camera_id), "-frames:v", "1",
+             "-q:v", "4", path, "-y"],
+            capture_output=True, timeout=SNAPSHOT_TIMEOUT_S)
+        data = open(path, "rb").read() if os.path.exists(path) else b""
+    except Exception as exc:                          # timeout, no ffmpeg, ...
+        log.warning("snapshot failed for %s: %s", camera_id, exc)
+        data = b""
+    finally:
+        if os.path.exists(path):
+            os.unlink(path)
+
+    if len(data) < 1000:
+        raise HTTPException(
+            503, f"could not grab a frame from {camera_id} within "
+                 f"{SNAPSHOT_TIMEOUT_S:.0f}s")
+    _snapshots[camera_id] = (_time.time(), data)
+    return data
+
+
+@app.get("/api/cameras/{camera_id}/snapshot")
+def camera_snapshot(camera_id: str, refresh: bool = False,
+                    _p=Depends(require("read"))):
+    """One still frame from a camera, for placing zone rules on.
+
+    Points are stored normalised, so the still only has to show the operator
+    the scene; it does not have to match the resolution the pipeline decodes.
+    """
     with SessionLocal() as db:
         if not db.get(Camera, camera_id):
             raise HTTPException(404, "camera not found")
 
-    cached = _snapshots.get(camera_id)
-    if cached and not refresh and (_time.time() - cached[0]) < SNAPSHOT_TTL_S:
-        data = cached[1]
-    else:
-        fd, path = tempfile.mkstemp(suffix=".jpg")
-        os.close(fd)
-        try:
-            subprocess.run(
-                ["ffmpeg", "-v", "error", "-rtsp_transport", "tcp",
-                 "-i", config.rtsp_url(camera_id), "-frames:v", "1",
-                 "-q:v", "4", path, "-y"],
-                capture_output=True, timeout=SNAPSHOT_TIMEOUT_S)
-            data = open(path, "rb").read() if os.path.exists(path) else b""
-        except Exception as exc:                      # timeout, no ffmpeg, ...
-            log.warning("snapshot failed for %s: %s", camera_id, exc)
-            data = b""
-        finally:
-            if os.path.exists(path):
-                os.unlink(path)
-        if len(data) < 1000:
-            raise HTTPException(
-                503, f"could not grab a frame from {camera_id} within "
-                     f"{SNAPSHOT_TIMEOUT_S:.0f}s")
-        _snapshots[camera_id] = (_time.time(), data)
+    data = None if refresh else _cached_snapshot(camera_id)
+    if data is None:
+        with _snapshot_lock(camera_id):
+            # Re-checked inside the lock: whoever we queued behind has just
+            # filled the cache, and using their frame is the whole point of
+            # having queued.
+            data = _cached_snapshot(camera_id) or _grab_snapshot(camera_id)
 
     return Response(content=data, media_type="image/jpeg",
                     headers={"Cache-Control": "no-store"})
 
 
 # -------------------------------------------------------------- detections --
 @app.get("/api/detections")
 def list_detections(camera_id: str | None = None, plate: str | None = None,
                     vehicle_class: str | None = None, colour: str | None = None,
                     since_minutes: int | None = None,
diff --git a/netra/web/app.js b/netra/web/app.js
index d5fee7a..5bc0d19 100644
--- a/netra/web/app.js
+++ b/netra/web/app.js
@@ -624,21 +624,21 @@ async function loadZones() {
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
-    <button onclick="delZone(${z.id})">Delete</button></div>`).join("")
+    <button onclick="delZone(${esc(z.id)})">Delete</button></div>`).join("")
     : `<div class="empty">No rules configured.</div>`;
 
   const events = await api("/api/zones/events?limit=50");
   $("#zEvents").innerHTML = events.length
     ? events.map(zoneEventHtml).join("") : `<div class="empty">No zone events yet.</div>`;
 }
 
 /* -------------------------------------------------------------- traffic --- */
 function sparks(values) {
   const max = Math.max(1, ...values);
