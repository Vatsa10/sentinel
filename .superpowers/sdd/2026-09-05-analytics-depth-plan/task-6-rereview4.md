# Re-review — Task 6 fix round 4 (full range)

84888d7 Vatsa Joshi | Require a second reading before trusting the scene clock
8ee8117 Vatsa10 | feat: add Task 8 re-review documentation and fix snapshot handling
90600c3 Vatsa Joshi | De-duplicate concurrent snapshot grabs and gate the endpoint on read
cd29d31 Vatsa10 | fix: address serialization issues and enhance API security in Task 8
77c2519 Vatsa10 | feat: add vision-language model for vehicle attribute extraction
ddf5225 Vatsa Joshi | Surface zones, traffic and intelligence in the operator console
808fdef Vatsa10 | Refine scene clock anchoring logic; implement corroboration for overlay readings and tighten plausible year range

 netra/analytics/inference.py   | 118 +++++++++++++++-
 netra/analytics/scene_clock.py |  17 ++-
 netra/api/app.py               |  93 ++++++++++++-
 netra/web/app.js               | 305 ++++++++++++++++++++++++++++++++++++++++-
 netra/web/index.html           |  91 +++++++++++-
 5 files changed, 611 insertions(+), 13 deletions(-)

diff --git a/netra/analytics/inference.py b/netra/analytics/inference.py
index b4b0498..ba07cd1 100644
--- a/netra/analytics/inference.py
+++ b/netra/analytics/inference.py
@@ -42,20 +42,39 @@ CLOCK_REANCHOR_AFTER_S = 900.0
 #: Anchoring budget for an offline exhaustive pass over a finite recording.
 #: Far larger than the live limit because the two situations are opposites: a
 #: live camera competes with detection for the same second, an indexing pass
 #: competes with nothing and exists precisely to get this right. Attempts are
 #: spaced INDEX_CLOCK_RETRY_MS apart in *stream* time so the budget is spent
 #: across the recording rather than burnt on its first ten seconds - an overlay
 #: obscured by a passing lorry at the join may be perfectly legible a minute in.
 INDEX_CLOCK_ATTEMPT_LIMIT = 30
 INDEX_CLOCK_RETRY_MS = 20000.0
 
+#: Spacing between attempts while a first reading is waiting to be
+#: corroborated. Measured on cam13, only about one attempt in seven produces a
+#: legible overlay, so waiting a further twenty seconds of stream time for the
+#: confirming read means the pair almost never completes and the camera stays
+#: unanchored despite a readable clock. Close the gap while a candidate is
+#: pending: the overlay that was legible a moment ago probably still is.
+#: Not zero, though - a second reading of near-identical pixels tests very
+#: little. A second of stream time changes the seconds digit, so an agreeing
+#: pair has read a *different* number correctly twice. What it still cannot
+#: catch is a systematic misread that produces the same wrong digit every
+#: time; only a differently-derived clock could.
+INDEX_CLOCK_CORROBORATE_RETRY_MS = 1000.0
+
+#: How far a second overlay reading may fall from the first one projected
+#: forward by PTS and still corroborate it. Overlays read to the second and
+#: PTS is milliseconds, so a genuine pair agrees to within rounding; anything
+#: wider is a different reading of a different number.
+CLOCK_CORROBORATION_TOLERANCE_S = 2.5
+
 #: How the engine spends its scene-clock budget.
 #:   opportunistic  live: skip the read whenever frames are backing up, because
 #:                  detection is the primary duty. Measured: without this,
 #:                  83% of frames dropped.
 #:   exhaustive     offline indexing: never skip. An indexing pass feeds frames
 #:                  blocking, so the queue is always full and the opportunistic
 #:                  rule would skip every single attempt - which is exactly
 #:                  what it did, anchoring 0% of 27,000 indexed detections.
 CLOCK_OPPORTUNISTIC = "opportunistic"
 CLOCK_EXHAUSTIVE = "exhaustive"
@@ -193,20 +212,23 @@ class InferenceEngine:
         self._plate_model = None
         self._ocr = None
         self._reid = None
         #: camera_id -> ClockAnchor, tying each stream to real scene time
         self._clocks: dict = {}
         #: how many overlay reads have been attempted per camera
         self._clock_attempts: dict = {}
         #: stream time of the last overlay attempt per camera, used only by the
         #: exhaustive policy to space its attempts across the recording
         self._clock_last_try: dict = {}
+        #: a first overlay reading, held unanchored until a second one
+        #: corroborates it. See _anchor_clock.
+        self._clock_pending: dict = {}
         #: live behaviour is the default and is unchanged; only an offline
         #: indexing pass sets this to CLOCK_EXHAUSTIVE
         self.clock_policy: str = CLOCK_OPPORTUNISTIC
         #: per-camera trackers; tracking is what counting, direction, dwell
         #: and zone rules are all built on
         from netra.analytics.tracking import TrackerRegistry
         self.trackers = TrackerRegistry()
         #: camera_id -> PlateVoter; plate reads from one tracked vehicle vote
         #: together, because a single frame's read is a guess
         self._plate_voters: dict = {}
@@ -296,20 +318,21 @@ class InferenceEngine:
     # -- the actual work -----------------------------------------------------
     def reset_camera_state(self, camera_id: str) -> None:
         """Discard per-camera state after a loop cut.
 
         The recording restarted, so the previous scene-time anchor no longer
         describes this stream and must be read again.
         """
         self._clocks.pop(camera_id, None)
         self._clock_attempts.pop(camera_id, None)
         self._clock_last_try.pop(camera_id, None)
+        self._clock_pending.pop(camera_id, None)
         self.trackers.reset(camera_id)
         self._plate_voters.pop(camera_id, None)
         self._dark_streak.pop(camera_id, None)
         self._dark_cameras.pop(camera_id, None)
         self._dark_skipped.pop(camera_id, None)
         self.stats["dark_cameras"] = len(self._dark_cameras)
         if self.zone_engine is not None:
             self.zone_engine.reset_camera(camera_id)
 
     def _anchor_clock(self, frame) -> None:
@@ -337,47 +360,82 @@ class InferenceEngine:
         limit = INDEX_CLOCK_ATTEMPT_LIMIT if exhaustive else CLOCK_ATTEMPT_LIMIT
         attempts = self._clock_attempts.get(cam, 0)
         if attempts >= limit:
             return
 
         if exhaustive:
             # An offline pass over a finite recording has nothing to starve and
             # every reason to succeed, so it never skips - but it spaces its
             # attempts through the recording rather than spending the whole
             # budget on the first frames, where the overlay may be obscured.
+            spacing = (INDEX_CLOCK_CORROBORATE_RETRY_MS
+                       if cam in self._clock_pending else INDEX_CLOCK_RETRY_MS)
             last_try = self._clock_last_try.get(cam)
-            if last_try is not None and frame.pts_ms - last_try < INDEX_CLOCK_RETRY_MS:
+            if last_try is not None and frame.pts_ms - last_try < spacing:
                 return
             self._clock_last_try[cam] = frame.pts_ms
         # Anchoring costs roughly a second of OCR per attempt. Detection is the
         # primary duty and must not queue behind it, so on the live path scene
         # time is enriched opportunistically: attempted only while the pipeline
         # has slack, and skipped whenever frames are backing up. A camera
         # simply anchors a little later instead of the whole pipeline stalling.
         elif self.queue.qsize() > self.queue.maxsize // 4:
             return
 
         self._clock_attempts[cam] = attempts + 1
         from netra.analytics.scene_clock import read_scene_time
         try:
             anchor = read_scene_time(self._ocr, frame.image, frame.pts_ms, cam)
         except Exception:
             log.debug("scene clock read failed for %s", cam, exc_info=True)
             return
 
         if anchor:
-            self._clocks[cam] = anchor
-            # The budget is per anchoring window, not per connection, so a
-            # camera that anchors successfully may re-anchor again when this
-            # reading in turn goes stale.
+            # One reading is not evidence. A single misread digit anchors the
+            # whole stream and mis-times every sighting on it for the rest of
+            # the pass - measured on this grid as spans dated 2025-06-14,
+            # 2026-06-24 and 2028-06-13, each from one bad read that passed
+            # every syntactic check. So a reading is held until a second,
+            # independent reading agrees with it once projected forward by the
+            # PTS between them. A contradicting reading is discarded rather
+            # than averaged: the average of a right answer and a wrong one is
+            # simply a third wrong answer.
+            #
+            # The attempt budget is not spent by a *successful* read, because
+            # its purpose is to stop retrying cameras with no legible overlay,
+            # not to stop a legible one corroborating itself.
             self._clock_attempts[cam] = 0
+            pending = self._clock_pending.get(cam)
+            if pending is None:
+                self._clock_pending[cam] = anchor
+                log.debug("%s overlay read %s; awaiting corroboration",
+                          cam, anchor.scene_time.isoformat())
+                return
+            drift = abs((anchor.scene_time
+                         - pending.at(anchor.pts_ms)).total_seconds())
+            if drift > CLOCK_CORROBORATION_TOLERANCE_S:
+                log.info("%s overlay readings disagree by %.1fs (%s then %s); "
+                         "both discarded", cam, drift,
+                         pending.scene_time.isoformat(),
+                         anchor.scene_time.isoformat())
+                # Keep the newer reading as the one to be corroborated: the
+                # older is now known to be unreliable, the newer merely
+                # unconfirmed.
+                self._clock_pending[cam] = anchor
+                return
+            self._clock_pending.pop(cam, None)
+            self._clocks[cam] = anchor
             self.stats["clocks_anchored"] = len(self._clocks)
+            log.info("%s scene clock corroborated to %s (two readings %.1fs "
+                     "apart agreeing to %.1fs)", cam,
+                     anchor.scene_time.isoformat(),
+                     (anchor.pts_ms - pending.pts_ms) / 1000.0, drift)
         elif self._clock_attempts[cam] >= limit:
             # A failed re-anchor leaves the existing anchor alone: an anchor
             # carrying some drift still times sightings far better than none.
             # The attempt still counts, so a camera whose overlay has become
             # unreadable (night, rain, a moved caption) stops retrying instead
             # of burning OCR on every frame for the rest of the connection.
             log.info("%s has no legible timestamp overlay after %d attempts; "
                      "%s", cam, limit,
                      "keeping the existing anchor despite its age" if existing
                      else "sightings on this camera carry no scene time")
@@ -692,15 +750,65 @@ def _self_check() -> None:
     assert "CAM2" not in engine._dark_cameras
     assert engine._dark_gate("CAM2") is True
 
     # A streak broken before the limit starts again from zero.
     for _ in range(DARK_FRAME_LIMIT - 1):
         engine._note_luma("CAM3", black, found=False)
     engine._note_luma("CAM3", black, found=True)
     engine._note_luma("CAM3", black, found=False)
     assert engine._dark_streak["CAM3"] == 1 and "CAM3" not in engine._dark_cameras
 
+    # Scene-clock corroboration. One reading never anchors: a single misread
+    # digit would mis-time every sighting on the camera for the rest of the
+    # pass, which is how this grid produced streams dated 2028. No model and no
+    # GPU: the OCR object and the overlay reader are stubs.
+    from datetime import datetime, timedelta, timezone
+
+    from netra.analytics import scene_clock as _sc
+    from netra.analytics.scene_clock import ClockAnchor
+
+    class _Frame:
+        def __init__(self, cam, pts):
+            self.camera_id, self.image, self.pts_ms = cam, black, pts
+
+    base = datetime(2026, 6, 14, 2, 32, 18, tzinfo=timezone.utc)
+    clock = InferenceEngine(on_detection=lambda d: None)
+    clock._ocr = object()
+    readings: dict = {}
+    real_reader = _sc.read_scene_time
+    _sc.read_scene_time = lambda ocr, img, pts, cam: ClockAnchor(
+        cam, readings[cam].pop(0), pts, 0.8) if readings.get(cam) else None
+    try:
+        # Two readings that agree once projected forward by PTS: anchored.
+        readings["AGREE"] = [base, base + timedelta(seconds=30)]
+        clock._anchor_clock(_Frame("AGREE", 0.0))
+        assert "AGREE" not in clock._clocks, "one reading must not anchor"
+        assert "AGREE" in clock._clock_pending
+        clock._anchor_clock(_Frame("AGREE", 30000.0))
+        assert clock._clocks["AGREE"].scene_time == base + timedelta(seconds=30)
+
+        # Two readings that contradict: neither anchors, and the later one is
+        # held as the next thing to be corroborated rather than trusted.
+        readings["DISAGREE"] = [base, base + timedelta(minutes=5)]
+        clock._anchor_clock(_Frame("DISAGREE", 0.0))
+        clock._anchor_clock(_Frame("DISAGREE", 30000.0))
+        assert "DISAGREE" not in clock._clocks, clock._clocks
+        assert clock._clock_pending["DISAGREE"].scene_time == base + timedelta(minutes=5)
+
+        # A camera that yields exactly one reading stays unanchored: no scene
+        # time is better than a wrong one.
+        readings["ONCE"] = [base]
+        clock._anchor_clock(_Frame("ONCE", 0.0))
+        clock._anchor_clock(_Frame("ONCE", 30000.0))
+        assert "ONCE" not in clock._clocks, clock._clocks
+
+        # A loop cut voids the pending reading along with everything else.
+        clock.reset_camera_state("AGREE")
+        assert "AGREE" not in clock._clock_pending and "AGREE" not in clock._clocks
+    finally:
+        _sc.read_scene_time = real_reader
+
     print("inference self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/analytics/scene_clock.py b/netra/analytics/scene_clock.py
index 7fc1b13..52c057e 100644
--- a/netra/analytics/scene_clock.py
+++ b/netra/analytics/scene_clock.py
@@ -46,22 +46,30 @@ _DIGIT_LAYOUTS = {
     14: {"day": (0, 2), "month": (2, 4), "year": (4, 8),
          "hour": (8, 10), "minute": (10, 12), "second": (12, 14)},
     16: {"day": (0, 2), "month": (3, 5), "year": (6, 10),
          "hour": (10, 12), "minute": (12, 14), "second": (14, 16)},
 }
 
 
 # A parsed date must be a real recording date, not merely a valid datetime.
 # Without this, an OCR misread like "0921-05-16" is accepted and silently
 # corrupts every downstream correlation - observed on cam04 at confidence 0.02.
-MIN_PLAUSIBLE_YEAR = 2015
-MAX_PLAUSIBLE_YEAR = 2035
+#
+# The window is deliberately narrow. Every recording in this sandbox is dated
+# June 2026, and a wide window catches only the absurd misreads while passing
+# the dangerous ones: a single wrong digit turning 2026 into 2025 or 2028 sailed
+# through a 2015-2035 window and mis-dated whole streams. One year either side
+# tolerates footage re-recorded a season later while rejecting a year that
+# differs by a digit. It cannot catch a misread day or hour - only corroboration
+# between two readings can, and InferenceEngine._anchor_clock requires it.
+MIN_PLAUSIBLE_YEAR = 2025
+MAX_PLAUSIBLE_YEAR = 2027
 
 #: OCR readings below this confidence are discarded. No scene time is better
 #: than a wrong one: an incorrect anchor mis-times every sighting on a camera.
 MIN_OCR_CONFIDENCE = 0.25
 
 
 def is_plausible(when: datetime | None) -> bool:
     return bool(when) and MIN_PLAUSIBLE_YEAR <= when.year <= MAX_PLAUSIBLE_YEAR
 
 
@@ -231,20 +239,25 @@ def _self_check() -> None:
     assert parse_overlay("") is None
     assert parse_overlay("DELIGHT P1 RLVD") is None
     assert parse_overlay("99-99-2026 99:99:99") is None
 
     # A syntactically valid but impossible recording date must be rejected.
     # cam04 produced exactly this at confidence 0.02 and it would otherwise
     # have mis-timed every sighting on that camera.
     assert not is_plausible(parse_overlay("16-05-0921 20:11:34"))
     assert is_plausible(parse_overlay("13-06-2026 23:22:47"))
     assert not is_plausible(None)
+    # Single-digit year misreads observed on the live grid. These passed the
+    # old 2015-2035 window and mis-dated entire streams.
+    assert not is_plausible(parse_overlay("13-06-2028 21:23:31"))
+    assert not is_plausible(parse_overlay("14-06-2015 02:29:47"))
+    assert is_plausible(parse_overlay("14-06-2025 02:29:47")) is True
 
     # PTS carries the clock forward from the anchor.
     anchor = ClockAnchor("cam04", datetime(2026, 6, 13, 23, 22, 47,
                                            tzinfo=timezone.utc), 1000.0, 0.9)
     assert anchor.at(61000.0) == datetime(2026, 6, 13, 23, 23, 47,
                                           tzinfo=timezone.utc), anchor.at(61000.0)
     assert anchor.at(1000.0) == anchor.scene_time
 
     # Anchor age is measured in stream time, from the frame it was read on.
     assert anchor.age_s(1000.0) == 0.0, anchor.age_s(1000.0)
diff --git a/netra/api/app.py b/netra/api/app.py
index 11af75e..0542703 100644
--- a/netra/api/app.py
+++ b/netra/api/app.py
@@ -1,22 +1,24 @@
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
@@ -151,20 +153,106 @@ def gap_analysis():
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
+#: One lock per camera, so concurrent callers for the same camera wait on the
+#: grab already running instead of each starting their own. Without it the
+#: cache only protects the warm path: five clicks on "Load still frame" before
+#: the first returns would be five ffmpeg processes holding five threadpool
+#: threads for seventeen seconds apiece, which is how a snapshot request ends
+#: up starving /api/pipeline/status. The registry of locks needs its own lock
+#: because it is filled lazily from several request threads.
+_snapshot_locks: dict[str, threading.Lock] = {}
+_snapshot_locks_guard = threading.Lock()
+
+
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
+    import os
+    import subprocess
+    import tempfile
+    import time as _time
+
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
+    with SessionLocal() as db:
+        if not db.get(Camera, camera_id):
+            raise HTTPException(404, "camera not found")
+
+    data = None if refresh else _cached_snapshot(camera_id)
+    if data is None:
+        with _snapshot_lock(camera_id):
+            # Re-checked inside the lock: whoever we queued behind has just
+            # filled the cache, and using their frame is the whole point of
+            # having queued.
+            data = _cached_snapshot(camera_id) or _grab_snapshot(camera_id)
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
@@ -183,21 +271,24 @@ def list_detections(camera_id: str | None = None, plate: str | None = None,
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
