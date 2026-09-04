# Review package — Task 3

## Commits
a0021c6 Bound tracker state and guard three silent-corruption paths

## Stat
 .../task-3-report.md                               | 139 +++++++++++++++++++++
 netra/analytics/inference.py                       |  34 ++++-
 netra/analytics/reid.py                            |  65 +++++++++-
 netra/analytics/scene_clock.py                     |  16 +++
 netra/analytics/tracking.py                        |  72 ++++++++++-
 netra/api/app.py                                   |   9 +-
 6 files changed, 327 insertions(+), 8 deletions(-)

## Diff
diff --git a/netra/analytics/inference.py b/netra/analytics/inference.py
index bec80f8..d856ba6 100644
--- a/netra/analytics/inference.py
+++ b/netra/analytics/inference.py
@@ -25,20 +25,27 @@ from dataclasses import dataclass, field
 import numpy as np
 
 from netra import config
 
 log = logging.getLogger(__name__)
 
 #: How many frames to spend looking for a timestamp overlay before accepting
 #: that a camera has none. Roughly half the grid has no legible overlay.
 CLOCK_ATTEMPT_LIMIT = 4
 
+#: Stream seconds after which an anchor is re-read from the overlay. One
+#: reading extrapolated indefinitely drifts with the decoder's timing, so
+#: timestamps on a long-lived connection grow silently wrong. Fifteen minutes
+#: is far more often than drift becomes material, and far rarer than the OCR
+#: cost would justify doing it any more eagerly.
+CLOCK_REANCHOR_AFTER_S = 900.0
+
 #: Minimum crop height worth embedding. Below this an appearance vector cannot
 #: distinguish one vehicle from another, so it is cost without information.
 REID_MIN_CROP_PX = 64
 #: Cap on embeddings per frame. Busy junction cameras routinely show 20+
 #: vehicles; embedding every one saturates the queue and starves detection,
 #: which matters more. Largest vehicles are embedded first.
 REID_MAX_PER_FRAME = 8
 
 #: Minimum vehicle height before a plate read is even attempted. A plate on a
 #: vehicle smaller than this spans a handful of pixels and cannot be resolved,
@@ -220,30 +227,39 @@ class InferenceEngine:
         describes this stream and must be read again.
         """
         self._clocks.pop(camera_id, None)
         self._clock_attempts.pop(camera_id, None)
         self.trackers.reset(camera_id)
         self._plate_voters.pop(camera_id, None)
         if self.zone_engine is not None:
             self.zone_engine.reset_camera(camera_id)
 
     def _anchor_clock(self, frame) -> None:
-        """Read the burned-in timestamp once per connection, then extrapolate.
+        """Read the burned-in timestamp, then extrapolate until it goes stale.
 
         Attempts are capped. Reading an overlay costs several OCR passes over
         upscaled crops, and about half the cameras on this grid have no legible
         overlay at all - retrying every frame on those saturates the queue and
         starves detection, which matters far more than scene time. Measured
         without the cap: 83% of frames dropped.
+
+        The anchor is re-read once it has been extrapolated for
+        CLOCK_REANCHOR_AFTER_S of stream time, because decoder timing drift
+        accumulates and an hours-old anchor times sightings wrongly without
+        ever saying so.
         """
         cam = frame.camera_id
-        if self._ocr is None or cam in self._clocks:
+        if self._ocr is None:
+            return
+        existing = self._clocks.get(cam)
+        if (existing is not None
+                and existing.age_s(frame.pts_ms) < CLOCK_REANCHOR_AFTER_S):
             return
         attempts = self._clock_attempts.get(cam, 0)
         if attempts >= CLOCK_ATTEMPT_LIMIT:
             return
 
         # Anchoring costs roughly a second of OCR per attempt. Detection is the
         # primary duty and must not queue behind it, so scene time is enriched
         # opportunistically: attempted only while the pipeline has slack, and
         # skipped whenever frames are backing up. A camera simply anchors a
         # little later instead of the whole pipeline stalling.
@@ -253,25 +269,35 @@ class InferenceEngine:
         self._clock_attempts[cam] = attempts + 1
         from netra.analytics.scene_clock import read_scene_time
         try:
             anchor = read_scene_time(self._ocr, frame.image, frame.pts_ms, cam)
         except Exception:
             log.debug("scene clock read failed for %s", cam, exc_info=True)
             return
 
         if anchor:
             self._clocks[cam] = anchor
+            # The budget is per anchoring window, not per connection, so a
+            # camera that anchors successfully may re-anchor again when this
+            # reading in turn goes stale.
+            self._clock_attempts[cam] = 0
             self.stats["clocks_anchored"] = len(self._clocks)
         elif self._clock_attempts[cam] >= CLOCK_ATTEMPT_LIMIT:
+            # A failed re-anchor leaves the existing anchor alone: an anchor
+            # carrying some drift still times sightings far better than none.
+            # The attempt still counts, so a camera whose overlay has become
+            # unreadable (night, rain, a moved caption) stops retrying instead
+            # of burning OCR on every frame for the rest of the connection.
             log.info("%s has no legible timestamp overlay after %d attempts; "
-                     "sightings on this camera carry no scene time",
-                     cam, CLOCK_ATTEMPT_LIMIT)
+                     "%s", cam, CLOCK_ATTEMPT_LIMIT,
+                     "keeping the existing anchor despite its age" if existing
+                     else "sightings on this camera carry no scene time")
 
     def _process(self, frame) -> None:
         t0 = time.time()
         img = frame.image
         capability = self.camera_capability.get(frame.camera_id, "vehicle")
 
         if capability == "degraded":
             return  # corrupt or unusable feed; health monitoring only
 
         self._anchor_clock(frame)
diff --git a/netra/analytics/reid.py b/netra/analytics/reid.py
index 0d0fd72..5b99bad 100644
--- a/netra/analytics/reid.py
+++ b/netra/analytics/reid.py
@@ -29,20 +29,58 @@ import threading
 import numpy as np
 
 from netra import config
 
 log = logging.getLogger(__name__)
 
 EMBED_DIM = 512
 #: Cosine similarity above which two crops are considered a plausible match.
 #: Tuned to be permissive: this produces candidates for review, not verdicts.
 SIMILARITY_THRESHOLD = 0.80
+#: When the runner-up scores within this of the top match, the two cannot be
+#: told apart on appearance and neither may be presented as the answer.
+AMBIGUITY_MARGIN = 0.02
+
+_AMBIGUITY_NOTE = (
+    "Near-identical appearance scores: other candidates are within "
+    f"{AMBIGUITY_MARGIN:.2f} of the top match, so appearance alone cannot "
+    "separate them. Confirm against another signal before acting.")
+
+
+def flag_ambiguity(scored: list[dict]) -> list[dict]:
+    """Mark results the appearance evidence cannot actually separate.
+
+    Two silver hatchbacks embed almost identically, so a ranked list whose top
+    scores are nearly equal has picked a winner the evidence does not support.
+    The ambiguous candidates are kept rather than dropped - an operator shown
+    "three near-identical candidates" is better served than one shown a single
+    confident wrong answer - but every result carries the flag so the console
+    can never render the top hit as if it stood alone.
+
+    Mutates and returns the list in place; it is expected to be sorted with the
+    highest similarity first.
+
+    ponytail: ambiguity is judged against the top score only, so a tight
+    cluster further down the list is not flagged. That cluster is not competing
+    to be the answer, so it does not mislead in the same way.
+    """
+    # An epsilon, because a gap of exactly the margin must land on the
+    # cautious side rather than on whichever side binary floats round it to.
+    limit = AMBIGUITY_MARGIN + 1e-9
+    # Both keys are set on every result, including a lone one, so no consumer
+    # has to distinguish "unambiguous" from "never checked".
+    top = scored[0]["similarity"] if scored else 0.0
+    ambiguous = len(scored) >= 2 and (top - scored[1]["similarity"]) <= limit
+    for row in scored:
+        row["ambiguous"] = ambiguous and (top - row["similarity"]) <= limit
+        row["ambiguity_note"] = _AMBIGUITY_NOTE if row["ambiguous"] else None
+    return scored
 
 
 class ReIdEncoder:
     """Turns vehicle crops into comparable appearance vectors."""
 
     def __init__(self):
         self._model = None
         self._transform = None
         self._lock = threading.Lock()
 
@@ -109,21 +147,23 @@ def rank_candidates(query_embedding, detections: list, top_k: int = 25) -> list[
     presenting a match as fact.
     """
     scored = []
     for det in detections:
         if not det.embedding:
             continue
         s = similarity(query_embedding, det.embedding)
         if s >= SIMILARITY_THRESHOLD:
             scored.append({"detection": det, "similarity": round(s, 4)})
     scored.sort(key=lambda x: x["similarity"], reverse=True)
-    return scored[:top_k]
+    # Truncate first: ambiguity is about what the caller is shown, so it is
+    # judged over the returned list rather than over candidates it never sees.
+    return flag_ambiguity(scored[:top_k])
 
 
 def _self_check() -> None:
     """Check the similarity maths without requiring the model or a GPU."""
     a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
     b = np.array([1.0, 0.0, 0.0], dtype=np.float32)
     c = np.array([0.0, 1.0, 0.0], dtype=np.float32)
 
     assert abs(similarity(a, b) - 1.0) < 1e-6, similarity(a, b)
     assert abs(similarity(a, c)) < 1e-6, similarity(a, c)
@@ -131,16 +171,39 @@ def _self_check() -> None:
     assert similarity(a, np.array([1.0, 0.0], dtype=np.float32)) == 0.0  # shape guard
 
     class FakeDet:
         def __init__(self, emb, name):
             self.embedding, self.name = emb, name
 
     dets = [FakeDet(list(a), "same"), FakeDet(list(c), "orthogonal"),
             FakeDet(None, "no-embedding")]
     ranked = rank_candidates(a, dets)
     assert len(ranked) == 1 and ranked[0]["detection"].name == "same", ranked
+    # A lone result has nothing to be confused with.
+    assert ranked[0]["ambiguous"] is False and ranked[0]["ambiguity_note"] is None
+
+    # Two candidates scoring within the margin are both flagged: this is the
+    # two-silver-hatchbacks case, where presenting the top hit implies a
+    # confidence the evidence does not support.
+    close = [{"similarity": 0.91}, {"similarity": 0.90}, {"similarity": 0.82}]
+    flag_ambiguity(close)
+    assert close[0]["ambiguous"] and close[1]["ambiguous"], close
+    assert close[0]["ambiguity_note"], close[0]
+    # The distant third is not part of the confusion and is not flagged.
+    assert close[2]["ambiguous"] is False and close[2]["ambiguity_note"] is None
+
+    # A clear winner is reported as one.
+    clear = [{"similarity": 0.95}, {"similarity": 0.84}]
+    flag_ambiguity(clear)
+    assert not any(r["ambiguous"] for r in clear), clear
+
+    # Exactly on the margin counts as ambiguous: the boundary should not be
+    # resolved in favour of false confidence.
+    edge = [{"similarity": 0.90}, {"similarity": 0.88}]
+    flag_ambiguity(edge)
+    assert edge[0]["ambiguous"] and edge[1]["ambiguous"], edge
 
     print("reid self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/analytics/scene_clock.py b/netra/analytics/scene_clock.py
index 268e2b7..7fc1b13 100644
--- a/netra/analytics/scene_clock.py
+++ b/netra/analytics/scene_clock.py
@@ -100,20 +100,30 @@ class ClockAnchor:
     """Ties one camera's stream clock to real scene time."""
     camera_id: str
     scene_time: datetime
     pts_ms: float
     confidence: float
 
     def at(self, pts_ms: float) -> datetime:
         """Scene time for any frame, carried forward by PTS."""
         return self.scene_time + timedelta(milliseconds=pts_ms - self.pts_ms)
 
+    def age_s(self, pts_ms: float) -> float:
+        """How far, in stream seconds, this anchor is being extrapolated.
+
+        Extrapolation is only as good as the decoder's timing. Small errors in
+        PTS accumulate, so an anchor read hours ago is quietly less trustworthy
+        than one read a minute ago; callers use this to decide when to re-read
+        the overlay rather than extrapolating from one reading forever.
+        """
+        return (pts_ms - self.pts_ms) / 1000.0
+
 
 def parse_overlay(text: str) -> datetime | None:
     """Interpret one OCR reading of a timestamp overlay.
 
     Returns None rather than guessing: a wrong scene time would corrupt every
     correlation downstream, which is worse than having none.
     """
     if not text:
         return None
     cleaned = text.strip().upper()
@@ -229,15 +239,21 @@ def _self_check() -> None:
     assert is_plausible(parse_overlay("13-06-2026 23:22:47"))
     assert not is_plausible(None)
 
     # PTS carries the clock forward from the anchor.
     anchor = ClockAnchor("cam04", datetime(2026, 6, 13, 23, 22, 47,
                                            tzinfo=timezone.utc), 1000.0, 0.9)
     assert anchor.at(61000.0) == datetime(2026, 6, 13, 23, 23, 47,
                                           tzinfo=timezone.utc), anchor.at(61000.0)
     assert anchor.at(1000.0) == anchor.scene_time
 
+    # Anchor age is measured in stream time, from the frame it was read on.
+    assert anchor.age_s(1000.0) == 0.0, anchor.age_s(1000.0)
+    assert anchor.age_s(61000.0) == 60.0, anchor.age_s(61000.0)
+    # A stream that has rewound (a loop cut before reset) must not read as old.
+    assert anchor.age_s(0.0) == -1.0, anchor.age_s(0.0)
+
     print("scene clock self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/analytics/tracking.py b/netra/analytics/tracking.py
index 641c9b4..0a3ba5a 100644
--- a/netra/analytics/tracking.py
+++ b/netra/analytics/tracking.py
@@ -26,20 +26,24 @@ import logging
 from dataclasses import dataclass, field
 
 log = logging.getLogger(__name__)
 
 #: Minimum overlap to associate a detection with an existing track at 1s apart.
 BASE_IOU_THRESHOLD = 0.25
 #: Appearance similarity that can rescue an association with poor overlap.
 APPEARANCE_RESCUE = 0.86
 #: A track with no sighting for this long in stream time is closed.
 TRACK_TIMEOUT_S = 6.0
+#: Hard ceiling on live tracks per camera. Timeout alone is not a bound: a busy
+#: junction can open tracks faster than they expire, and the platform is meant
+#: to run for hours, so the dictionary needs a ceiling as well as an age limit.
+MAX_TRACKS_PER_CAMERA = 300
 
 
 def iou(a: list[int], b: list[int]) -> float:
     """Intersection over union of two [x1, y1, x2, y2] boxes."""
     ax1, ay1, ax2, ay2 = a
     bx1, by1, bx2, by2 = b
     ix1, iy1 = max(ax1, bx1), max(ay1, by1)
     ix2, iy2 = min(ax2, bx2), min(ay2, by2)
     iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
     inter = iw * ih
@@ -97,24 +101,42 @@ class Track:
 class CameraTracker:
     """Tracks vehicles on one camera."""
 
     def __init__(self, camera_id: str):
         self.camera_id = camera_id
         self.tracks: dict[int, Track] = {}
         self._next_id = 1
         #: cumulative count of distinct vehicles seen, by class
         self.counts: dict[str, int] = {}
         self.total_count = 0
+        #: vehicles counted since the last loop cut, so a headline figure can be
+        #: reported per playthrough as well as cumulatively
+        self.counted_this_loop = 0
+        #: how many times the recording has restarted under this tracker
+        self.loops_seen = 0
+        #: tracks discarded by the cap rather than by timeout
+        self.dropped_tracks = 0
 
     def reset(self) -> None:
-        """Discard all state. Called at a loop cut, where continuity is void."""
+        """Discard track state at a loop cut, where continuity is void.
+
+        `total_count` deliberately survives: those vehicles really were
+        observed, and zeroing the figure would throw away real observation.
+        But the recording replays, so the same vehicles are counted again on
+        every loop, and a cumulative total taken alone reads as far more
+        traffic than the footage contains. `counted_this_loop` is therefore
+        reset here and `loops_seen` incremented, so anyone reading a count of
+        "4,893 vehicles" can see whether that is one playthrough or six.
+        """
         self.tracks.clear()
+        self.counted_this_loop = 0
+        self.loops_seen += 1
 
     def _match(self, det, pts_ms: float) -> Track | None:
         """Best existing track for this detection, or None."""
         best, best_score = None, 0.0
         for track in self.tracks.values():
             if track.vehicle_class != det.vehicle_class:
                 continue
 
             gap_s = max((pts_ms - track.last_pts_ms) / 1000.0, 0.0)
             # A longer gap means the vehicle moved further, so overlap alone is
@@ -168,40 +190,57 @@ class CameraTracker:
 
         # A vehicle is counted once it has been seen twice, which filters the
         # single-frame false positives that a busy night scene produces.
         newly_counted = []
         for track in self.tracks.values():
             if not track.counted and track.sightings >= 2:
                 track.counted = True
                 self.counts[track.vehicle_class] = \
                     self.counts.get(track.vehicle_class, 0) + 1
                 self.total_count += 1
+                self.counted_this_loop += 1
                 newly_counted.append(track)
         return newly_counted
 
     def _expire(self, pts_ms: float) -> None:
         stale = [tid for tid, t in self.tracks.items()
                  if (pts_ms - t.last_pts_ms) / 1000.0 > TRACK_TIMEOUT_S]
         for tid in stale:
             del self.tracks[tid]
 
+        # Timeout is an age limit, not a bound. Under rapid turnover the
+        # dictionary can still grow without limit, so the least recently seen
+        # tracks are dropped once the cap is passed: a track unseen for longest
+        # is the one least likely to receive another detection, so it is the
+        # cheapest to lose. Drops are counted rather than hidden - a rising
+        # figure means the camera is busier than the tracker can follow.
+        excess = len(self.tracks) - MAX_TRACKS_PER_CAMERA
+        if excess > 0:
+            oldest = sorted(self.tracks.items(), key=lambda kv: kv[1].last_pts_ms)
+            for tid, _ in oldest[:excess]:
+                del self.tracks[tid]
+            self.dropped_tracks += excess
+
     def stats(self) -> dict:
         active = list(self.tracks.values())
         directions: dict[str, int] = {}
         for t in active:
             d = t.direction()
             if d:
                 directions[d] = directions.get(d, 0) + 1
         return {
             "camera_id": self.camera_id,
             "active_tracks": len(active),
             "total_counted": self.total_count,
+            "counted_this_loop": self.counted_this_loop,
+            "loops_seen": self.loops_seen,
+            "dropped_tracks": self.dropped_tracks,
             "counts_by_class": dict(self.counts),
             "directions": directions,
             "mean_dwell_s": round(
                 sum(t.dwell_s for t in active) / len(active), 1) if active else 0.0,
         }
 
 
 class TrackerRegistry:
     """One tracker per camera."""
 
@@ -288,15 +327,46 @@ def _self_check() -> None:
     t7.update([Det([0, 0, 100, 100])], 0.0)
     t7.update([Det([2, 1, 102, 101])], 1000.0)
     assert list(t7.tracks.values())[0].direction() is None
 
     # Stale tracks expire rather than accumulating forever.
     t8 = CameraTracker("cam08")
     t8.update([Det([0, 0, 100, 100])], 0.0)
     t8.update([Det([900, 900, 950, 950])], 30_000.0)
     assert len(t8.tracks) == 1, "the original track should have expired"
 
+    # The cap bounds live tracks, and it is the least recently seen that go.
+    t9 = CameraTracker("cam09")
+    for i in range(MAX_TRACKS_PER_CAMERA + 50):
+        # Boxes far enough apart never associate, so each detection opens a
+        # track; staggered PTS gives them a well-defined recency order.
+        t9.update([Det([i * 200, 0, i * 200 + 20, 20])], float(i))
+    # Trimming happens on expiry, so the cap may be exceeded by the current
+    # frame's own detections until the next update; one expiry settles it.
+    t9._expire(float(MAX_TRACKS_PER_CAMERA + 50))
+    assert len(t9.tracks) == MAX_TRACKS_PER_CAMERA, len(t9.tracks)
+    assert t9.dropped_tracks == 50, t9.dropped_tracks
+    assert min(tr.last_pts_ms for tr in t9.tracks.values()) == 50.0, (
+        "the oldest tracks should be the ones dropped")
+    assert t9.stats()["dropped_tracks"] == 50
+
+    # A loop cut keeps cumulative observation but restarts the per-loop figure,
+    # so a count inflated by replays is visible rather than silent.
+    t10 = CameraTracker("cam10")
+    t10.update([Det([100, 100, 200, 200])], 0.0)
+    t10.update([Det([110, 100, 210, 200])], 1000.0)
+    assert t10.total_count == 1 and t10.counted_this_loop == 1
+    t10.reset()
+    assert t10.total_count == 1, "cumulative observation is real and must survive"
+    assert t10.counted_this_loop == 0, t10.counted_this_loop
+    assert t10.loops_seen == 1, t10.loops_seen
+    t10.update([Det([100, 100, 200, 200])], 0.0)
+    t10.update([Det([110, 100, 210, 200])], 1000.0)
+    st = t10.stats()
+    assert st["total_counted"] == 2 and st["counted_this_loop"] == 1, st
+    assert st["loops_seen"] == 1, st
+
     print("tracking self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/api/app.py b/netra/api/app.py
index 9cafcfa..d2fecaa 100644
--- a/netra/api/app.py
+++ b/netra/api/app.py
@@ -457,21 +457,21 @@ def seed_watchlist(_p=Depends(require("watchlist"))):
 @app.get("/api/vehicles/{detection_id}/similar")
 def similar_vehicles(detection_id: int, limit: int = Query(25, le=100),
                      min_similarity: float = 0.80):
     """Find the same vehicle on other cameras by appearance.
 
     This is the answer to "trace this vehicle" when no plate is readable, which
     on this grid is the normal case. Results are ranked candidates carrying
     their similarity score, ordered in time, and filtered for space-time
     plausibility - not assertions of identity.
     """
-    from netra.analytics.reid import similarity
+    from netra.analytics.reid import flag_ambiguity, similarity
     from netra.core.geo import haversine_km, time_group
     from netra.analytics.matching import spacetime_plausible
 
     with SessionLocal() as db:
         query = db.get(Detection, detection_id)
         if query is None:
             raise HTTPException(404, "detection not found")
         if not query.embedding:
             raise HTTPException(
                 400, "this detection has no appearance embedding")
@@ -511,38 +511,43 @@ def similar_vehicles(detection_id: int, limit: int = Query(25, le=100),
                 "evidence": det.evidence_path,
                 "similarity": round(sim, 4),
                 "distance_km": round(km, 2),
                 "elapsed_s": round(secs, 1),
                 "plausible": ok,
                 "plausibility": why,
                 "same_time_group": time_group(det.camera_id) == time_group(query.camera_id),
             })
 
         scored.sort(key=lambda x: x["similarity"], reverse=True)
-        matches = scored[:limit]
+        # Two vehicles that look alike score alike, so where the top results
+        # are separated by less than the appearance model can resolve, every
+        # one of them is flagged. The console needs this to avoid rendering a
+        # coin-toss as an identification.
+        matches = flag_ambiguity(scored[:limit])
 
         origin = {
             "detection_id": query.id, "camera_id": query.camera_id,
             "camera_name": qcam.name if qcam else None,
             "lat": qcam.lat if qcam else None, "lon": qcam.lon if qcam else None,
             "at": query.wall_time.isoformat(),
             "vehicle_class": query.vehicle_class, "colour": query.colour,
             "evidence": query.evidence_path,
         }
 
     _audit("vehicle.similar", target=str(detection_id),
            detail={"matches": len(matches)})
     return {
         "query": origin,
         "matches": matches,
         "plausible_matches": [m for m in matches if m["plausible"]],
         "method": "appearance re-identification (ResNet-18 512-d, cosine)",
+        "ambiguous": any(m.get("ambiguous") for m in matches),
         "note": ("Ranked candidates for operator confirmation, not identification. "
                  "Appearance evidence alone does not establish that two sightings "
                  "are the same vehicle."),
     }
 
 
 @app.get("/api/vehicles/{detection_id}/track")
 def appearance_track(detection_id: int, min_similarity: float = 0.82):
     """Build a movement path for a vehicle using appearance alone.
 
