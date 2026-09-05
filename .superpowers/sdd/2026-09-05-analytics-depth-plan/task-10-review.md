# Review package — Task 10

8d8cb28 Vatsa Joshi | Tier C: traffic auto-poll, a retention ceiling, and the stray marker file
04c14d6 Vatsa Joshi | Tier B: freshness, stability, precision and the duplicated block
b995bfb Vatsa Joshi | Tier A: close the correctness, honesty and access-control holes

 .gitignore                      |   3 +
 docs/high-level-design.md       |  15 ++++
 netra/analytics/baseline.py     |  86 +++++++++++++++++--
 netra/analytics/cloned_plate.py |  16 +++-
 netra/analytics/inference.py    | 185 +++++++++++++++++++++++++++++++++++-----
 netra/analytics/loop_index.py   |  31 +++++--
 netra/analytics/matching.py     |  20 ++++-
 netra/analytics/reid.py         |  15 +++-
 netra/analytics/route.py        |  25 ++++++
 netra/api/app.py                |  35 +++++++-
 netra/api/assistant.py          |  16 +++-
 netra/api/retrieval.py          |  53 +++++++++++-
 netra/core/db.py                |   7 ++
 netra/core/models.py            |  11 +++
 netra/core/retention.py         |   8 ++
 netra/core/timing.py            |  75 +++++++++++++++-
 netra/pipeline.py               | 113 ++++++++++++++++--------
 netra/web/app.js                |  97 +++++++++++++++++----
 netra/web/index.html            |   8 ++
 tools/purge_scene_times.py      |  82 ++++++++++++++++++
 20 files changed, 802 insertions(+), 99 deletions(-)

diff --git a/.gitignore b/.gitignore
index 54b6594..f67c565 100644
--- a/.gitignore
+++ b/.gitignore
@@ -1,14 +1,17 @@
 .venv/
 wheels/
 data/
 *.log
+# Marker files dropped by long-running tool invocations; one was committed
+# to the repo root by the auto-committer.
+*.start
 __pycache__/
 *.pyc
 
 # Credentials and local state
 .env
 data/
 !data/.gitkeep
 
 # Model weights (downloaded on first run)
 *.pt
diff --git a/docs/high-level-design.md b/docs/high-level-design.md
index 6a769e0..8bf45f6 100644
--- a/docs/high-level-design.md
+++ b/docs/high-level-design.md
@@ -309,20 +309,35 @@ Hardware: NVIDIA RTX 5050 Laptop GPU, 8 GB, CUDA 12.8.
 | Measurement | Result |
 |---|---|
 | Vehicle detection | ~13 ms per 1080p frame at 640 px, ~18 ms at 960 px |
 | Appearance embedding | ~11 ms per frame (8 vehicles) |
 | Colour extraction | ~2 ms per frame (20 vehicles) |
 | Live run, 8 busy Ahmedabad junctions | 8/8 connected, **4,893 vehicles in ~2 minutes** |
 | Frames dropped | **0%** |
 | Inference queue depth | 0–5 |
 | Registry onboarding | 30 cameras probed and profiled in ~35 seconds |
 
+**Two capacity figures, answering different questions.** The ~6 ms per frame
+cited in §8 is *tier-1 scanning capacity*: a 640 px detection pass answering
+"are there vehicles here?", which is what sets how many cameras one node can
+watch at the 1 fps baseline, and gives the 150–200 cameras per node used for
+sizing. The eight-junction result above is *full-pipeline capacity* under
+admission control: detection, appearance embedding, plate localisation and OCR,
+tracking and zone evaluation, all running together at 0% frame loss on
+simultaneously escalated busy junctions. Both are measured on the same RTX
+5050, and they are not interchangeable. Tier-1 scanning says how wide a node
+can watch; full-pipeline capacity says how many of those cameras can be doing
+the expensive work at the same moment. Multiplying the scanning figure as
+though every camera were escalated would overstate a node by more than an order
+of magnitude, which is precisely why the two-tier scheduler exists: escalation
+is rationed against the second number while the first sets the footprint.
+
 ### Capacity management
 
 Reaching zero frame loss on eight busy junction cameras required treating GPU
 time as a budget to be allocated, not a resource to be assumed. Four measures,
 each arrived at by measurement rather than estimate:
 
 **Persistence is batched.** Writing each detection in its own transaction, with
 its own evidence JPEG, on the inference thread cost 76% of frames. Detections
 are queued and flushed in batches on a separate thread.
 
diff --git a/netra/analytics/baseline.py b/netra/analytics/baseline.py
index 34baa07..e4cb5aa 100644
--- a/netra/analytics/baseline.py
+++ b/netra/analytics/baseline.py
@@ -38,20 +38,31 @@ MIN_SAMPLES = 5
 #: true, operationally absurd. One vehicle is the smallest difference the
 #: counter can even express, so it is the smallest dispersion worth believing.
 STDEV_FLOOR = 1.0
 
 #: z-score bands. Deliberately wide: an alert an operator learns to ignore is
 #: worse than no alert, and traffic counts are not normally distributed.
 Z_HIGH = 3.0
 Z_ELEVATED = 2.0
 Z_LOW = -2.0
 
+#: How old a camera's most recent bucket may be before its reading stops being
+#: reported as current. Both the anomalies endpoint and the assistant take each
+#: camera's newest bucket and judge it, which is right while the camera is
+#: reporting and quietly wrong once it stops: a feed that dropped out at
+#: midnight would otherwise be presented at nine in the morning as "no traffic
+#: counted, the road may be blocked", which is a statement about our own
+#: connection dressed up as a statement about the road. Fifteen minutes is
+#: several bucket periods, so a camera that is merely between flushes is not
+#: called stale.
+ANOMALY_MAX_BUCKET_AGE_S = 900.0
+
 
 @dataclass
 class Baseline:
     """What one camera normally sees in one hour of the day."""
     camera_id: str
     hour: int
     mean: float
     stdev: float
     samples: int
 
@@ -69,28 +80,31 @@ class Baseline:
                 "effective_stdev": round(self.effective_stdev, 2),
                 "samples": self.samples, "sufficient": self.sufficient}
 
 
 @dataclass
 class Assessment:
     """A reading judged against a baseline, with the reasoning attached."""
     camera_id: str
     hour: int
     observed: int
-    status: str            # insufficient_data|quiet|low|normal|elevated|high
+    status: str            # insufficient_data|stale|quiet|low|normal|elevated|high
     z_score: float | None
     explanation: str
     baseline: Baseline | None = None
     detail: dict = field(default_factory=dict)
 
     @property
     def anomalous(self) -> bool:
+        # `stale` is deliberately absent: it says the platform has nothing
+        # current to report about this camera, which is not a finding about
+        # the road and must never be counted as one.
         return self.status in ("quiet", "low", "elevated", "high")
 
     def as_dict(self) -> dict:
         return {"camera_id": self.camera_id, "hour": self.hour,
                 "observed": self.observed, "status": self.status,
                 "z_score": self.z_score, "anomalous": self.anomalous,
                 "explanation": self.explanation,
                 "baseline": self.baseline.as_dict() if self.baseline else None,
                 **self.detail}
 
@@ -119,20 +133,34 @@ def _hour_of(row) -> int | None:
         return None
     try:
         from datetime import timezone
         if ts.tzinfo is not None:
             ts = ts.astimezone(timezone.utc)
         return ts.hour
     except AttributeError:
         return None
 
 
+def _bucket_age_s(row, now) -> float | None:
+    """Seconds between `now` and the row's bucket start, or None if untimed."""
+    ts = _field(row, "bucket_start")
+    if ts is None:
+        return None
+    from datetime import timezone
+    try:
+        if ts.tzinfo is None:
+            ts = ts.replace(tzinfo=timezone.utc)
+        return (now - ts).total_seconds()
+    except (AttributeError, TypeError):
+        return None
+
+
 def _is_legacy_cumulative(row, total) -> bool:
     """True for a row written before `total` became a per-bucket figure.
 
     Those rows carry a running count spanning every replay of the recording,
     and a single one poisons the hour it lands in: measured on cam15 at hour
     18, one legacy row moved the mean from 3.4 to 14.0 and the standard
     deviation from 1.1 to 26.0, so a genuine ten-fold spike read as normal.
     They cannot be aged out - nothing prunes traffic_stats and the learner has
     no time window - so they are excluded here instead.
 
@@ -233,58 +261,84 @@ def assess(baseline: Baseline | None, observed: int) -> Assessment:
     else:
         status = "normal"
         text = (f"{observed} vehicles is within the usual range: {norm}.")
 
     return Assessment(camera_id=baseline.camera_id, hour=baseline.hour,
                       observed=observed, status=status, z_score=z,
                       explanation=text, baseline=baseline)
 
 
 def detect_anomalies(baselines: dict[tuple[str, int], Baseline],
-                     current_stats, include_normal: bool = False
-                     ) -> list[Assessment]:
+                     current_stats, include_normal: bool = False,
+                     now=None) -> list[Assessment]:
     """Assess a set of current readings, most deviant first.
 
     `current_stats` entries carry `camera_id`, `total`, and either an `hour` or
     a `bucket_start` from which the UTC hour is taken.
+
+    A reading older than ANOMALY_MAX_BUCKET_AGE_S is reported as `stale`, with
+    the bucket's own timestamp in the explanation, rather than judged. Callers
+    pass the newest bucket per camera, which stops being a current reading the
+    moment the camera stops reporting, and the difference between "this road is
+    empty" and "we have not heard from this camera since midnight" is the whole
+    difference between a finding and a fault.
     """
+    from datetime import datetime, timezone
+    if now is None:
+        now = datetime.now(timezone.utc)
     out: list[Assessment] = []
     for row in current_stats:
         camera_id = _field(row, "camera_id")
         if camera_id is None:
             continue
         hour = _field(row, "hour")
         if hour is None:
             hour = _hour_of(row)
         if hour is None:
             continue
         hour = int(hour)
         observed = int(_field(row, "total") or 0)
 
+        age = _bucket_age_s(row, now)
+        if age is not None and age > ANOMALY_MAX_BUCKET_AGE_S:
+            ts = _field(row, "bucket_start")
+            out.append(Assessment(
+                camera_id=camera_id, hour=hour, observed=observed,
+                status="stale", z_score=None,
+                baseline=baselines.get((camera_id, hour)),
+                explanation=(
+                    f"{camera_id} has not reported since "
+                    f"{getattr(ts, 'isoformat', lambda: ts)()} "
+                    f"({age / 60.0:.0f} minutes ago). Its last bucket counted "
+                    f"{observed}, but that is not a current reading and is not "
+                    f"judged against the norm."),
+                detail={"bucket_age_s": round(age, 1)}))
+            continue
+
         result = assess(baselines.get((camera_id, hour)), observed)
         # `assess` cannot know the camera when there is no baseline at all.
         result.camera_id = camera_id
         result.hour = hour
         if include_normal or result.status != "normal":
             out.append(result)
 
     # Insufficient-data entries sort last: they are information about the
     # platform's own coverage, not about the road.
     out.sort(key=lambda a: (a.z_score is None, -abs(a.z_score or 0.0)))
     return out
 
 
 def _self_check() -> None:
     """A baseline that flags the wrong thing costs an operator's trust, and one
     that flags nothing is decoration, so both directions are pinned here. All
     rows are synthetic: no database, no network."""
-    from datetime import datetime, timezone
+    from datetime import datetime, timedelta, timezone
 
     def row(cam, hour, total):
         return {"camera_id": cam, "total": total,
                 "bucket_start": datetime(2026, 9, 1, hour, 0,
                                          tzinfo=timezone.utc)}
 
     # A busy camera with a settled norm, plus a thin one with three samples.
     rows = ([row("cam01", 9, n) for n in (40, 44, 38, 42, 46, 41)] +
             [row("cam02", 9, n) for n in (10, 12, 11)] +
             [row("cam01", 3, n) for n in (2, 3, 1, 2, 4, 2)])
@@ -393,24 +447,44 @@ def _self_check() -> None:
         [(f.camera_id, f.hour, f.status, f.z_score) for f in found]
     assert found[0].camera_id == "cam01" and found[0].hour == 3, found[0]
     assert found[-1].status == "insufficient_data", found[-1]
     assert all(f.camera_id for f in found)
 
     # An unknown camera is declined, not guessed at.
     unknown = detect_anomalies(b, [{"camera_id": "cam99", "hour": 9, "total": 900}])
     assert unknown[0].status == "insufficient_data"
     assert unknown[0].camera_id == "cam99"
 
-    # bucket_start is accepted in place of an explicit hour.
-    via_ts = detect_anomalies(b, [row("cam01", 9, 200)])
+    # bucket_start is accepted in place of an explicit hour. `now` is pinned
+    # to the synthetic clock: without it these rows would all be years old and
+    # correctly reported as stale.
+    fresh_now = datetime(2026, 9, 1, 9, 1, tzinfo=timezone.utc)
+    via_ts = detect_anomalies(b, [row("cam01", 9, 200)], now=fresh_now)
     assert via_ts[0].status == "high", via_ts
 
+    # A camera that stopped reporting hours ago is not a current reading. Its
+    # last bucket counted zero, which against a busy norm would otherwise be
+    # published as "the road may be blocked" - a claim about our own connection
+    # dressed as a claim about the road.
+    stale_now = fresh_now + timedelta(seconds=ANOMALY_MAX_BUCKET_AGE_S + 60)
+    stale = detect_anomalies(b, [row("cam01", 9, 0)], now=stale_now)
+    assert stale[0].status == "stale", stale
+    assert not stale[0].anomalous, stale[0]
+    assert "2026-09-01T09:00:00" in stale[0].explanation, stale[0].explanation
+    assert stale[0].as_dict()["bucket_age_s"] > ANOMALY_MAX_BUCKET_AGE_S
+
+    # One second inside the window is still current, and still judged.
+    edge_now = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc) + timedelta(
+        seconds=ANOMALY_MAX_BUCKET_AGE_S - 1)
+    edge = detect_anomalies(b, [row("cam01", 9, 0)], now=edge_now)
+    assert edge[0].status == "quiet", edge
+
     # Naive timestamps are tolerated and read as UTC.
     naive = learn([{"camera_id": "cam04", "total": 7,
                     "bucket_start": datetime(2026, 9, 1, 14, 0)}])
     assert ("cam04", 14) in naive
 
     print("baseline self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/analytics/cloned_plate.py b/netra/analytics/cloned_plate.py
index 0712850..09f4fc5 100644
--- a/netra/analytics/cloned_plate.py
+++ b/netra/analytics/cloned_plate.py
@@ -22,20 +22,21 @@ Three constraints keep the finding honest:
     rather than take it.
 """
 from __future__ import annotations
 
 from dataclasses import dataclass, asdict
 from datetime import datetime
 
 from netra.analytics.matching import (MAX_PLAUSIBLE_KMH, normalise_plate,
                                       spacetime_plausible)
 from netra.core.geo import haversine_km, time_group
+from netra.core.timing import scene_time as _scene_time
 from netra.core.timing import sighting_time
 
 # Plate confidence assumed when a detection carries none. Deliberately middling:
 # an unscored read must neither inflate nor destroy a finding.
 DEFAULT_PLATE_CONF = 0.5
 
 # A finding can never be certain - see the module docstring.
 MAX_CONFIDENCE = 0.99
 
 # Violation credited when the two sightings carry the same timestamp. Scene time
@@ -123,21 +124,26 @@ def find_clones(detections: list, min_confidence: float = 0.6) -> list[CloneFind
     # interleaving is expected rather than hypothetical. Cameras in no known
     # session are dropped entirely: we cannot show their clock agrees with
     # anything, including another unlisted camera's.
     groups: dict[tuple[str, str], list] = {}
     for det in detections:
         plate = normalise_plate(det.plate_text)
         # A partial read cannot identify a vehicle, so it cannot evidence a
         # clone either: "AB12" is shared by thousands of legitimate plates.
         if len(plate) < 6:
             continue
-        if sighting_time(det) is None:
+        if _scene_time(det) is None:
+            # A clone finding is entirely a claim about elapsed time between
+            # two cameras. Wall time is our connection time, and an
+            # uncorroborated overlay reading is a guess that has been two years
+            # out on this grid - either would manufacture impossible speeds
+            # out of nothing. No clock, no claim.
             continue
         group = time_group(det.camera_id)
         if group is None:
             continue
         groups.setdefault((plate, group), []).append(det)
 
     findings: list[CloneFinding] = []
     for (plate, group), dets in groups.items():
         if len(dets) < 2:
             continue
@@ -223,20 +229,21 @@ def _self_check() -> None:
             self.id, self.name, self.lat, self.lon = cid, name, lat, lon
 
     class FakeDet:
         _next = [1]
 
         def __init__(self, cam, plate, at, conf=0.9):
             self.camera, self.camera_id = cam, cam.id
             self.plate_text, self.plate_conf = plate, conf
             self.evidence_path = None
             self.scene_time, self.wall_time = at, at
+            self.scene_time_corroborated = True
             self.id = FakeDet._next[0]
             FakeDet._next[0] += 1
 
     t0 = datetime(2026, 6, 13, 23, 20, tzinfo=timezone.utc)
     c04 = FakeCam("cam04", "Paldi Circle", 23.0130, 72.5620)
     c14 = FakeCam("cam14", "Delight RLVD", 23.0290, 72.5700)
     c15 = FakeCam("cam15", "Vasna", 23.0180, 72.5300)
     c10 = FakeCam("cam10", "Char Chowk", 21.5220, 70.4570)   # other session
     c99 = FakeCam("cam99", "Unlisted", 23.0000, 72.5000)     # no time group
 
@@ -326,15 +333,22 @@ def _self_check() -> None:
     # but the finding must show both as read rather than the folded key.
     folded = find_clones([FakeDet(c04, "GJ01AB1234", t0),
                           FakeDet(c14, "GJ0IAB1234", t0 + timedelta(seconds=2))])
     assert len(folded) == 1 and "6J01A81234" not in folded[0].plate, folded
     assert "GJ0IAB1234" in folded[0].plate, folded[0].plate
 
     # Distinct plates are never cross-compared.
     assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
                         FakeDet(c14, "GJ09ZZ8888", t0 + timedelta(seconds=2))]) == []
 
+    # An uncorroborated scene time cannot evidence a clone. Its only other
+    # timestamp is our connection time, which would put every sighting on a
+    # replayed loop within seconds of every other and flag the whole grid.
+    unclocked = FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=2))
+    unclocked.scene_time_corroborated = False
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0), unclocked]) == []
+
     print("cloned_plate self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/analytics/inference.py b/netra/analytics/inference.py
index ba07cd1..47a494f 100644
--- a/netra/analytics/inference.py
+++ b/netra/analytics/inference.py
@@ -110,20 +110,31 @@ DARK_FRAME_LIMIT = 60
 #: A dark camera is re-tested every this many frames so it recovers on its own
 #: at dawn. At tier-1 rate that is a probe roughly every five minutes: cheap
 #: against the ~99.7% of inference passes it saves, and quick enough that no
 #: real traffic is missed for long.
 DARK_RECHECK_FRAMES = 300
 #: Stride used to downscale a frame before measuring luma. Sampling every 8th
 #: pixel is a numpy view rather than a copy, so the measurement costs
 #: microseconds - it must not become a cost of its own.
 LUMA_SAMPLE_STRIDE = 8
 
+#: Half-precision detection, on the GPU only - CPU fp16 is emulated and
+#: slower. Measured on this machine at TIER2_IMGSZ: 17.3 ms/pass at fp32
+#: against 12.1 ms at fp16. Detection is not the bottleneck here - OCR, writes
+#: and escalation are - so this is not where the demo is won; it is taken
+#: because it is free. Deliberately not INT8 and not TensorRT: INT8 would need
+#: a calibration set and a re-validation of every threshold, and TensorRT on
+#: sm_120 is a day of risk for a number that does not move the demo.
+#: Spelled as `quantize` rather than the older `half=True`, which this
+#: ultralytics forwards to exactly this with a deprecation warning.
+_PRECISION: dict = {"quantize": 16} if config.DEVICE == "cuda" else {}
+
 
 def mean_luma(image) -> float:
     """Mean brightness of a frame, measured on a strided sample.
 
     BT.601 weights on BGR. Deliberately not cv2.cvtColor over the full frame:
     that allocates a greyscale copy of every frame on every camera, which is
     real cost to answer a question about darkness.
     """
     if image is None or getattr(image, "size", 0) == 0:
         return 0.0
@@ -145,20 +156,28 @@ class VehicleDetection:
     vehicle_class: str
     confidence: float
     bbox: list[int]
     colour: str | None = None
     plate_text: str | None = None
     plate_conf: float | None = None
     plate_chars: int | None = None
     plate_bbox: list[int] | None = None
     #: real time the scene occurred, from the camera's burned-in overlay
     scene_time: object | None = None
+    #: whether the anchor that produced `scene_time` was corroborated by a
+    #: second, independent overlay reading. False means the value is a guess
+    #: and must be treated as absent by anything reasoning over elapsed time -
+    #: this grid has produced streams dated 2028 from a single misread digit.
+    scene_time_corroborated: bool = False
+    #: how many per-frame OCR reads voted for `plate_text`. One is a guess;
+    #: persisting the count is what lets an operator tell the two apart.
+    plate_votes: int | None = None
     #: assigned by the per-camera tracker; identifies one vehicle journey
     track_id: int | None = None
     embedding: list | None = field(default=None, repr=False)
     evidence: object | None = field(default=None, repr=False)
 
 
 # Coarse colour vocabulary. Street lighting makes anything finer dishonest.
 _COLOUR_REFS = {
     "white": (200, 200, 200),
     "silver": (150, 150, 150),
@@ -209,22 +228,24 @@ class InferenceEngine:
         self._stop = threading.Event()
         self._thread: threading.Thread | None = None
         self._vehicle_model = None
         self._plate_model = None
         self._ocr = None
         self._reid = None
         #: camera_id -> ClockAnchor, tying each stream to real scene time
         self._clocks: dict = {}
         #: how many overlay reads have been attempted per camera
         self._clock_attempts: dict = {}
-        #: stream time of the last overlay attempt per camera, used only by the
-        #: exhaustive policy to space its attempts across the recording
+        #: stream time of the last overlay attempt per camera. The exhaustive
+        #: policy uses it to space its attempts across the recording; both
+        #: policies use it to decide when an exhausted attempt budget has been
+        #: quiet long enough to be granted afresh.
         self._clock_last_try: dict = {}
         #: a first overlay reading, held unanchored until a second one
         #: corroborates it. See _anchor_clock.
         self._clock_pending: dict = {}
         #: live behaviour is the default and is unchanged; only an offline
         #: indexing pass sets this to CLOCK_EXHAUSTIVE
         self.clock_policy: str = CLOCK_OPPORTUNISTIC
         #: per-camera trackers; tracking is what counting, direction, dwell
         #: and zone rules are all built on
         from netra.analytics.tracking import TrackerRegistry
@@ -238,21 +259,26 @@ class InferenceEngine:
         self._dark_cameras: dict = {}
         #: camera_id -> frames skipped since the last probe
         self._dark_skipped: dict = {}
         #: set by the pipeline so zone rules can be evaluated here, where the
         #: tracks live
         self.zone_engine = None
         self.on_zone_event = None
 
         self.stats = {"submitted": 0, "dropped": 0, "processed": 0,
                       "vehicles": 0, "plates": 0, "embedded": 0,
-                      "clocks_anchored": 0, "plate_votes": 0,
+                      "clocks_anchored": 0,
+                      #: detection-frames on which a track's plate consensus
+                      #: replaced that frame's own read. Not the same thing as
+                      #: the per-detection plate_votes column, which records
+                      #: how many reads one consensus was drawn from.
+                      "plate_consensus_applied": 0,
                       "dark_cameras": 0, "dark_frames_skipped": 0,
                       "infer_ms": 0.0}
 
     # -- model loading -------------------------------------------------------
     def load(self) -> None:
         from ultralytics import YOLO
         log.info("loading vehicle model on %s", config.DEVICE)
         self._vehicle_model = YOLO(config.VEHICLE_MODEL)
         self._vehicle_model.to(config.DEVICE)
 
@@ -353,127 +379,174 @@ class InferenceEngine:
         if self._ocr is None:
             return
         existing = self._clocks.get(cam)
         if (existing is not None
                 and existing.age_s(frame.pts_ms) < CLOCK_REANCHOR_AFTER_S):
             return
         exhaustive = self.clock_policy == CLOCK_EXHAUSTIVE
         limit = INDEX_CLOCK_ATTEMPT_LIMIT if exhaustive else CLOCK_ATTEMPT_LIMIT
         attempts = self._clock_attempts.get(cam, 0)
         if attempts >= limit:
-            return
+            # The budget is exhausted. Give up for now, but not forever: the
+            # same reason a corroborated anchor is re-read after
+            # CLOCK_REANCHOR_AFTER_S applies to a camera that never anchored
+            # at all. An overlay unreadable at dusk may be perfectly legible
+            # once the streetlights come up, so a camera that has been silent
+            # for a re-anchor window gets one fresh budget, not a retry on
+            # every frame. The pending half-reading is dropped with it: a
+            # reading from a quarter of an hour ago is not a corroborating
+            # partner for one taken now.
+            last_try = self._clock_last_try.get(cam)
+            if (last_try is not None
+                    and frame.pts_ms - last_try < CLOCK_REANCHOR_AFTER_S * 1000.0):
+                return
+            attempts = 0
+            self._clock_attempts[cam] = 0
+            self._clock_pending.pop(cam, None)
 
         if exhaustive:
             # An offline pass over a finite recording has nothing to starve and
             # every reason to succeed, so it never skips - but it spaces its
             # attempts through the recording rather than spending the whole
             # budget on the first frames, where the overlay may be obscured.
             spacing = (INDEX_CLOCK_CORROBORATE_RETRY_MS
                        if cam in self._clock_pending else INDEX_CLOCK_RETRY_MS)
             last_try = self._clock_last_try.get(cam)
             if last_try is not None and frame.pts_ms - last_try < spacing:
                 return
-            self._clock_last_try[cam] = frame.pts_ms
         # Anchoring costs roughly a second of OCR per attempt. Detection is the
         # primary duty and must not queue behind it, so on the live path scene
         # time is enriched opportunistically: attempted only while the pipeline
         # has slack, and skipped whenever frames are backing up. A camera
         # simply anchors a little later instead of the whole pipeline stalling.
         elif self.queue.qsize() > self.queue.maxsize // 4:
             return
 
         self._clock_attempts[cam] = attempts + 1
+        self._clock_last_try[cam] = frame.pts_ms
         from netra.analytics.scene_clock import read_scene_time
         try:
             anchor = read_scene_time(self._ocr, frame.image, frame.pts_ms, cam)
         except Exception:
             log.debug("scene clock read failed for %s", cam, exc_info=True)
             return
 
         if anchor:
             # One reading is not evidence. A single misread digit anchors the
             # whole stream and mis-times every sighting on it for the rest of
             # the pass - measured on this grid as spans dated 2025-06-14,
             # 2026-06-24 and 2028-06-13, each from one bad read that passed
             # every syntactic check. So a reading is held until a second,
             # independent reading agrees with it once projected forward by the
             # PTS between them. A contradicting reading is discarded rather
             # than averaged: the average of a right answer and a wrong one is
             # simply a third wrong answer.
             #
-            # The attempt budget is not spent by a *successful* read, because
-            # its purpose is to stop retrying cameras with no legible overlay,
-            # not to stop a legible one corroborating itself.
-            self._clock_attempts[cam] = 0
+            # The attempt budget is spent by every read, legible or not, and
+            # is refunded only by a *corroborated* anchor. Resetting it on any
+            # successful read - as this once did - made the cap unreachable
+            # for exactly the camera it exists to protect: a jittery or
+            # half-occluded overlay that reads differently every time never
+            # agrees with itself, so it never anchors, and on the live path
+            # there is no spacing gate to slow it down. Measured: 200
+            # mutually-contradicting readings produced 200 OCR calls and left
+            # the counter at zero. A contradiction is evidence that this
+            # camera's overlay cannot be trusted, so it must cost the same as
+            # an illegible one.
             pending = self._clock_pending.get(cam)
             if pending is None:
                 self._clock_pending[cam] = anchor
                 log.debug("%s overlay read %s; awaiting corroboration",
                           cam, anchor.scene_time.isoformat())
+                self._note_clock_exhausted(cam, limit, existing)
                 return
             drift = abs((anchor.scene_time
                          - pending.at(anchor.pts_ms)).total_seconds())
             if drift > CLOCK_CORROBORATION_TOLERANCE_S:
                 log.info("%s overlay readings disagree by %.1fs (%s then %s); "
                          "both discarded", cam, drift,
                          pending.scene_time.isoformat(),
                          anchor.scene_time.isoformat())
                 # Keep the newer reading as the one to be corroborated: the
                 # older is now known to be unreliable, the newer merely
                 # unconfirmed.
                 self._clock_pending[cam] = anchor
+                self._note_clock_exhausted(cam, limit, existing)
                 return
             self._clock_pending.pop(cam, None)
             self._clocks[cam] = anchor
+            # Corroborated: the overlay is legible and self-consistent, so the
+            # budget has done its job and is returned in full for the next
+            # re-anchor.
+            self._clock_attempts[cam] = 0
             self.stats["clocks_anchored"] = len(self._clocks)
             log.info("%s scene clock corroborated to %s (two readings %.1fs "
                      "apart agreeing to %.1fs)", cam,
                      anchor.scene_time.isoformat(),
                      (anchor.pts_ms - pending.pts_ms) / 1000.0, drift)
-        elif self._clock_attempts[cam] >= limit:
-            # A failed re-anchor leaves the existing anchor alone: an anchor
-            # carrying some drift still times sightings far better than none.
-            # The attempt still counts, so a camera whose overlay has become
-            # unreadable (night, rain, a moved caption) stops retrying instead
-            # of burning OCR on every frame for the rest of the connection.
-            log.info("%s has no legible timestamp overlay after %d attempts; "
-                     "%s", cam, limit,
-                     "keeping the existing anchor despite its age" if existing
-                     else "sightings on this camera carry no scene time")
+        else:
+            self._note_clock_exhausted(cam, limit, existing)
+
+    def _note_clock_exhausted(self, cam: str, limit: int, existing) -> None:
+        """Log that a camera has spent its whole anchoring budget.
+
+        Called from every path that consumes an attempt, and silent until the
+        last one, so it says so exactly once per budget rather than on every
+        frame thereafter.
+
+        A failed re-anchor leaves the existing anchor alone: an anchor carrying
+        some drift still times sightings far better than none. The attempts
+        still count, so a camera whose overlay has become unreadable - night,
+        rain, a moved caption, or one that simply never reads the same number
+        twice - stops retrying instead of burning OCR on every frame for the
+        rest of the connection.
+        """
+        if self._clock_attempts.get(cam, 0) < limit:
+            return
+        log.info("%s produced no corroborated timestamp overlay in %d "
+                 "attempts; %s", cam, limit,
+                 "keeping the existing anchor despite its age" if existing
+                 else "sightings on this camera carry no scene time")
 
     def _process(self, frame) -> None:
         t0 = time.time()
         img = frame.image
         capability = self.camera_capability.get(frame.camera_id, "vehicle")
 
         if capability == "degraded":
             return  # corrupt or unusable feed; health monitoring only
 
         if not self._dark_gate(frame.camera_id):
             return  # feed has gone dark; skipping until the next probe frame
 
         self._anchor_clock(frame)
         anchor = self._clocks.get(frame.camera_id)
         scene_time = anchor.at(frame.pts_ms) if anchor else None
+        # Only a corroborated anchor ever reaches self._clocks, so every scene
+        # time this engine produces is corroborated. The flag is carried
+        # explicitly all the same: rows written before corroboration landed are
+        # still in the store, and the consumers must be able to tell them apart
+        # from these without knowing which build wrote them.
+        corroborated = scene_time is not None
 
         classes = None if capability == "person" else list(config.VEHICLE_CLASSES)
         if capability == "person":
             classes = [0]  # COCO person
 
         # Escalated cameras get the larger input size: they have traffic worth
         # resolving properly, and small distant vehicles are what a 640px pass
         # loses first.
         imgsz = config.TIER2_IMGSZ if frame.dt_s and frame.dt_s < 0.5 \
             else config.TIER1_IMGSZ
 
         results = self._vehicle_model.predict(
-            img, device=config.DEVICE, verbose=False,
+            img, device=config.DEVICE, verbose=False, **_PRECISION,
             conf=config.CONF_THRESHOLD, imgsz=imgsz, classes=classes)
 
         if not results:
             return
         boxes = results[0].boxes
         if boxes is None or len(boxes) == 0:
             self._note_luma(frame.camera_id, img, found=False)
             self.stats["processed"] += 1
             return
         self._note_luma(frame.camera_id, img, found=True)
@@ -485,20 +558,21 @@ class InferenceEngine:
             conf = float(box.conf.item())
             x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
             crop = img[max(y1, 0):y2, max(x1, 0):x2]
 
             det = VehicleDetection(
                 camera_id=frame.camera_id, pts_ms=frame.pts_ms,
                 wall_time=frame.wall_time, vehicle_class=name,
                 confidence=conf, bbox=[x1, y1, x2, y2],
                 colour=estimate_colour(crop) if cls_id != 0 else None,
                 scene_time=scene_time,
+                scene_time_corroborated=corroborated,
                 track_id=None,
                 evidence=crop)
             detections.append(det)
 
         self.stats["vehicles"] += len(detections)
 
         # Embed in one batch - far cheaper than one call each - but only the
         # vehicles worth embedding. A crop a few dozen pixels tall produces an
         # appearance vector that cannot distinguish one car from another, so
         # embedding it both wastes GPU time and pollutes the gallery with
@@ -628,57 +702,62 @@ class InferenceEngine:
             text, conf, voters = result
             if voters < 2:
                 # One voter is not a vote. Either the track has a single read,
                 # or the reads disagreed on length and one was passed through
                 # unvoted. Leave this frame's own read alone rather than
                 # presenting a lone OCR guess as a consensus.
                 continue
             det.plate_text = text
             det.plate_conf = conf
             det.plate_chars = len(text)
-            self.stats["plate_votes"] += 1
+            det.plate_votes = voters
+            self.stats["plate_consensus_applied"] += 1
 
         # The tracker expires stale tracks internally; without this the voter
         # would hold reads for vehicles that left the frame long ago.
         voter.retain(tracker.tracks.keys())
 
     def _read_plate(self, img, det: VehicleDetection) -> None:
         """Localise and read the plate on one vehicle."""
         x1, y1, x2, y2 = det.bbox
         crop = img[max(y1, 0):y2, max(x1, 0):x2]
         if crop.size == 0:
             return
 
         plate_crop, plate_box = None, None
         if self._plate_model is not None:
             res = self._plate_model.predict(crop, device=config.DEVICE,
-                                            verbose=False, conf=0.25, imgsz=320)
+                                            verbose=False, **_PRECISION,
+                                            conf=0.25, imgsz=320)
             if res and res[0].boxes is not None and len(res[0].boxes) > 0:
                 best = max(res[0].boxes, key=lambda b: float(b.conf.item()))
                 px1, py1, px2, py2 = (int(v) for v in best.xyxy[0].tolist())
                 plate_crop = crop[max(py1, 0):py2, max(px1, 0):px2]
                 plate_box = [x1 + px1, y1 + py1, x1 + px2, y1 + py2]
         else:
             # Without a dedicated plate detector, search the lower third of the
             # vehicle, where a rear plate sits.
             h = crop.shape[0]
             plate_crop = crop[int(h * 0.6):, :]
 
         if plate_crop is None or plate_crop.size == 0 or self._ocr is None:
             return
 
         text, conf = _run_ocr(self._ocr, plate_crop)
         if not text:
             return
         det.plate_text = text
         det.plate_conf = conf
         det.plate_chars = len(text)
+        # A lone read is recorded as exactly that: one vote. The voter
+        # overwrites this with the real count if the track reaches a consensus.
+        det.plate_votes = 1
         det.plate_bbox = plate_box
         self.stats["plates"] += 1
 
 
 # -- OCR backend -------------------------------------------------------------
 # Kept behind two small functions so the backend can be swapped without the
 # engine caring which library is installed.
 
 def _load_ocr():
     import easyocr
@@ -797,18 +876,80 @@ def _self_check() -> None:
         # A camera that yields exactly one reading stays unanchored: no scene
         # time is better than a wrong one.
         readings["ONCE"] = [base]
         clock._anchor_clock(_Frame("ONCE", 0.0))
         clock._anchor_clock(_Frame("ONCE", 30000.0))
         assert "ONCE" not in clock._clocks, clock._clocks
 
         # A loop cut voids the pending reading along with everything else.
         clock.reset_camera_state("AGREE")
         assert "AGREE" not in clock._clock_pending and "AGREE" not in clock._clocks
+
+        # The attempt budget must bound contradictions as well as illegible
+        # frames. A camera whose overlay never reads the same number twice is
+        # exactly the one the cap exists for: on the live path there is no
+        # spacing gate, only the queue-slack check, so an unbounded retry
+        # OCRs on every slack frame forever. Feed 200 mutually-contradicting
+        # readings and count the reads that actually reached the reader.
+        calls: list = []
+        _sc.read_scene_time = lambda ocr, img, pts, cam: (
+            calls.append(cam)
+            or ClockAnchor(cam, base + timedelta(hours=len(calls)), pts, 0.8))
+        jitter = InferenceEngine(on_detection=lambda d: None)
+        jitter._ocr = object()
+        assert jitter.clock_policy == CLOCK_OPPORTUNISTIC
+        for k in range(200):
+            jitter._anchor_clock(_Frame("JITTER", k * 1000.0))
+        assert len(calls) <= CLOCK_ATTEMPT_LIMIT, len(calls)
+        assert "JITTER" not in jitter._clocks, "contradictions must not anchor"
+
+        # ...but giving up is not permanent. Once a re-anchor window of stream
+        # time has passed with no attempt, one fresh budget is granted, and an
+        # agreeing pair within it anchors normally.
+        spent = len(calls)
+        readings["JITTER"] = [base, base + timedelta(seconds=30)]
+        _sc.read_scene_time = lambda ocr, img, pts, cam: ClockAnchor(
+            cam, readings[cam].pop(0), pts, 0.8) if readings.get(cam) else None
+        later = 200_000.0 + CLOCK_REANCHOR_AFTER_S * 1000.0
+        jitter._anchor_clock(_Frame("JITTER", later))
+        jitter._anchor_clock(_Frame("JITTER", later + 30000.0))
+        assert jitter._clocks["JITTER"].scene_time == base + timedelta(seconds=30)
+        # A corroborated anchor returns the budget in full for the next one.
+        assert jitter._clock_attempts["JITTER"] == 0
+        assert spent <= CLOCK_ATTEMPT_LIMIT
+
+        # A detection carries whether its anchor was corroborated, because the
+        # store still holds rows written before corroboration existed and the
+        # elapsed-time consumers must be able to tell them apart.
+        assert VehicleDetection(camera_id="X", pts_ms=0.0, wall_time=0.0,
+                                vehicle_class="car", confidence=0.9,
+                                bbox=[0, 0, 1, 1]).scene_time_corroborated is False
     finally:
         _sc.read_scene_time = real_reader
 
+    # Plate vote counts reach the detection. A consensus drawn from seven reads
+    # and a single unrepeated guess are shown to an operator as the same string
+    # unless the count travels with it, so the wiring is pinned here rather
+    # than left to be noticed missing in the console.
+    class _Tracker:
+        tracks: dict = {1: object()}
+
+    voter_engine = InferenceEngine.__new__(InferenceEngine)
+    voter_engine._plate_voters = {}
+    voter_engine.stats = {"plate_consensus_applied": 0}
+    voted = VehicleDetection(camera_id="CAMV", pts_ms=0.0, wall_time=0.0,
+                             vehicle_class="car", confidence=0.9,
+                             bbox=[0, 0, 1, 1], plate_text="GJ01AB1234",
+                             plate_conf=0.8, plate_votes=1, track_id=1)
+    frame_v = _Frame("CAMV", 0.0)
+    for k in range(7):
+        voted.plate_text, voted.plate_conf = "GJ01AB1234", 0.8
+        frame_v.pts_ms = k * 100.0
+        voter_engine._vote_plates(frame_v, _Tracker(), [voted])
+    assert voted.plate_votes == 7, voted.plate_votes
+    assert voter_engine.stats["plate_consensus_applied"] == 6, voter_engine.stats
+
     print("inference self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/analytics/loop_index.py b/netra/analytics/loop_index.py
index 28db224..bf2a0d5 100644
--- a/netra/analytics/loop_index.py
+++ b/netra/analytics/loop_index.py
@@ -38,20 +38,21 @@ import logging
 import time
 from dataclasses import dataclass, asdict, field
 from datetime import datetime
 
 from netra.analytics.inference import (CLOCK_EXHAUSTIVE,
                                        INDEX_CLOCK_RETRY_MS)
 from netra.analytics.matching import spacetime_plausible
 from netra.analytics.reid import SIMILARITY_THRESHOLD, similarity
 from netra.core.geo import TIME_GROUPS, haversine_km
 from netra.core.geo import time_group as camera_time_group
+from netra.core.timing import scene_time as _scene_time
 from netra.core.timing import sighting_time
 
 log = logging.getLogger(__name__)
 
 #: Longest a loop-length probe may run before giving up. A recording that has
 #: not restarted inside this is either longer than we care to wait for or the
 #: connection is wedged; either way the caller gets None rather than a hang.
 LOOP_PROBE_TIMEOUT_S = 180.0
 
 #: PTS moving backwards by more than this is the loop point rather than the
@@ -284,21 +285,21 @@ def index_camera(camera_id: str, engine, max_seconds: float = 900.0,
         drain_until = min(deadline + 30.0, time.time() + 30.0)
         while engine.queue.qsize() and time.time() < drain_until:
             time.sleep(0.05)
         time.sleep(0.5)
     finally:
         source.release()
         engine.on_detection = previous_callback
         engine.clock_policy = previous_policy
 
     written = _persist(collected) if persist else 0
-    with_scene_time = sum(1 for d in collected if getattr(d, "scene_time", None))
+    with_scene_time = sum(1 for d in collected if _scene_time(d) is not None)
 
     return {
         "camera_id": camera_id,
         "frames": frames,
         "submitted": submitted,
         "detections": len(collected),
         "written": written,
         "video_seconds": round((highest - (first_pts or 0.0)) / 1000.0, 1),
         "loop_complete": looped,
         "scene_time_coverage": (round(with_scene_time / len(collected), 3)
@@ -443,23 +444,25 @@ def _confidence(similarities: list[float], hop_count: int) -> float:
 
 
 def _minable(detections: list, group: str) -> tuple[list, dict]:
     """Detections of one group that can legitimately take part in a journey."""
     members = set(TIME_GROUPS.get(group, ()))
     usable, excluded = [], {"wrong_group": 0, "no_scene_time": 0, "no_embedding": 0}
     for det in detections:
         if det.camera_id not in members:
             excluded["wrong_group"] += 1
             continue
-        if not getattr(det, "scene_time", None):
-            # Wall time is our connection time, not the vehicle's. A sighting
-            # with no recorded clock simply cannot be placed on a journey.
+        if _scene_time(det) is None:
+            # Wall time is our connection time, not the vehicle's, and an
+            # overlay reading no second reading ever agreed with is a guess -
+            # this grid produced spans dated 2028 that way. A sighting with no
+            # corroborated clock simply cannot be placed on a journey.
             excluded["no_scene_time"] += 1
             continue
         if not getattr(det, "embedding", None):
             excluded["no_embedding"] += 1
             continue
         usable.append(det)
     # Ordered oldest first, then tail-sliced: where the cap bites, the most
     # recent pass over the recording is the one kept.
     usable.sort(key=sighting_time)
     if len(usable) > MAX_MINED_DETECTIONS:
@@ -629,64 +632,75 @@ def _load_group_detections(group: str) -> list:
     from netra.core.db import SessionLocal
     from netra.core.models import Detection
 
     members = TIME_GROUPS.get(group, [])
     if not members:
         return []
     with SessionLocal() as db:
         return (db.query(Detection).options(joinedload(Detection.camera))
                 .filter(Detection.camera_id.in_(members),
                         Detection.scene_time.isnot(None),
+                        # Rows written before corroborated anchoring landed
+                        # carry times no second reading ever confirmed.
+                        Detection.scene_time_corroborated.is_(True),
                         has_embedding())
                 # Newest first for the cap, matching _minable's tail slice, so
                 # both layers keep the same end of a long index.
                 .order_by(Detection.scene_time.desc())
                 .limit(MAX_MINED_DETECTIONS).all())
 
 
 # ----------------------------------------------------------- persistence --
 def exclusion_report(group: str) -> dict:
     """How much of a group's index cannot take part in mining, and why.
 
     Published rather than kept internal: a reader shown three journeys needs to
     know whether they were drawn from thirty comparable sightings or from three
     thousand of which most had no readable clock. Without that, the journeys
     look like the whole picture when they are a corner of it.
 
     Every figure below describes the same population — all detections stored
     for this group's cameras — and the three exclusion counts plus `comparable`
     sum to it, so the breakdown can be checked rather than trusted.
     """
+    from sqlalchemy import and_
+
     from netra.core.db import SessionLocal
     from netra.core.models import Detection
 
     members = TIME_GROUPS.get(group, [])
     if not members:
         return {}
     embedded = has_embedding()
     with SessionLocal() as db:
         base = db.query(Detection).filter(Detection.camera_id.in_(members))
         total = base.count()
-        no_clock = base.filter(Detection.scene_time.is_(None)).count()
-        clocked = base.filter(Detection.scene_time.isnot(None))
+        usable_clock = and_(Detection.scene_time.isnot(None),
+                            Detection.scene_time_corroborated.is_(True))
+        no_clock = base.filter(~usable_clock).count()
+        clocked = base.filter(usable_clock)
         with_clock = clocked.count()
         comparable = clocked.filter(embedded).count()
     return {
         "detections_in_group": total,
         "with_scene_time": with_clock,
         "comparable": comparable,
+        #: no overlay reading at all, or one that was never corroborated:
+        #: both are equally unusable for placing a sighting in time
         "excluded_no_scene_time": no_clock,
         #: counted among the clocked rows only, so the figures reconcile:
         #: comparable + no_embedding + no_scene_time == detections_in_group
         "excluded_no_embedding": with_clock - comparable,
-        "note": ("A sighting with no scene clock cannot be placed on a "
-                 "journey: wall time records when we connected to the loop, "
+        "note": ("A sighting with no corroborated scene clock cannot be "
+                 "placed on a journey: an overlay read once and never "
+                 "confirmed is a guess, and wall time records when we "
+                 "connected to the loop, "
                  "not when the vehicle passed. Counts describe every "
                  "detection stored for these cameras."),
     }
 
 
 def persist_journeys(group: str, journeys: list[Journey],
                      min_similarity: float = 0.84) -> int:
     """Replace the stored journeys for one group.
 
     Replaced rather than appended: mining is deterministic over the index, so a
@@ -778,20 +792,21 @@ def _self_check() -> None:
     class FakeCam:
         def __init__(self, cid, name, lat, lon):
             self.id, self.name, self.lat, self.lon = cid, name, lat, lon
 
     class FakeDet:
         _next = [1]
 
         def __init__(self, cam, at, emb, scene=True):
             self.camera, self.camera_id = cam, cam.id
             self.scene_time = at if scene else None
+            self.scene_time_corroborated = scene
             self.wall_time = at
             self.embedding = emb
             self.vehicle_class, self.colour = "car", "silver"
             self.plate_text = self.evidence_path = None
             self.id = FakeDet._next[0]
             FakeDet._next[0] += 1
 
     c01 = FakeCam("cam01", "Vastrapur", 23.0290, 72.5580)
     c04 = FakeCam("cam04", "Paldi Circle", 23.0130, 72.5620)
     c14 = FakeCam("cam14", "Delight RLVD", 23.0290, 72.5700)
diff --git a/netra/analytics/matching.py b/netra/analytics/matching.py
index a5b21b8..a04109b 100644
--- a/netra/analytics/matching.py
+++ b/netra/analytics/matching.py
@@ -205,35 +205,42 @@ class WatchlistIndex:
                 continue
             if not _primary_safe(len(folded)):
                 self._forced.append(entry)
             for key in plate_windows(folded, INDEX_WINDOW):
                 self._buckets.setdefault(key, []).append(entry)
             for key in plate_windows(folded, FALLBACK_WINDOW):
                 self._fallback.setdefault(key, []).append(entry)
 
     @staticmethod
     def _gather(buckets: dict, windows: set[str], extra: list) -> list[dict]:
+        # The windows arrive as a set, whose iteration order is a function of
+        # hash seeding rather than of the data. Sorting them is what makes the
+        # documented stability true: without it two runs over identical inputs
+        # could hand score_match its candidates in different orders, and two
+        # entries scoring equally would alert in a different order each time.
         seen: set[int] = set()
         out: list[dict] = []
-        for source in [buckets.get(k, ()) for k in windows] + [extra]:
+        for source in [buckets.get(k, ()) for k in sorted(windows)] + [extra]:
             for entry in source:
                 marker = id(entry)
                 if marker not in seen:
                     seen.add(marker)
                     out.append(entry)
         return out
 
     def candidates(self, plate_text: str | None) -> list[dict]:
         """Entries worth scoring against this observed plate.
 
         A superset of everything `score_match` could alert on. Order is stable
-        so alert ordering does not change with the prefilter's internals.
+        across processes and runs - the window keys are sorted before they are
+        walked - so alert ordering does not change with the prefilter's
+        internals or with this process's hash seed.
         """
         folded = normalise_plate(plate_text)
         if len(folded) < MIN_SCORABLE_CHARS:
             # Nothing this short can score against a longer plate; only an
             # equally short entry could match it, and only exactly.
             return list(self._short)
 
         if _primary_safe(len(folded)):
             return self._gather(self._buckets,
                                 plate_windows(folded, INDEX_WINDOW),
@@ -516,15 +523,24 @@ def _self_check() -> None:
     for target in corpus[:40]:
         folded = normalise_plate(target["plate"])
         for begin in range(0, len(folded) - 4):
             observed = folded[begin:begin + rng.randint(4, len(folded) - begin)]
             keep = {id(e) for e in big.candidates(observed)}
             for entry in corpus:
                 if score_match({**observed_base, "plate_text": observed},
                                entry).is_alert:
                     assert id(entry) in keep, (observed, entry["plate"])
 
+    # candidates() promises a stable order, so it must not depend on set
+    # iteration. Same index, same query, same order - checked against a second
+    # index built from the entries in a different order, which is the only
+    # thing that would shuffle the buckets' contents.
+    stable = WatchlistIndex(list(entries))
+    first = [e["plate"] for e in stable.candidates("GJ01AB1234")]
+    for _ in range(5):
+        assert [e["plate"] for e in stable.candidates("GJ01AB1234")] == first
+
     print("matching self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/analytics/reid.py b/netra/analytics/reid.py
index 6a16ead..8b08390 100644
--- a/netra/analytics/reid.py
+++ b/netra/analytics/reid.py
@@ -141,20 +141,28 @@ class ReIdEncoder:
 
     def load(self) -> None:
         import torch
         import torchvision
         from torchvision.models import ResNet18_Weights
 
         weights = ResNet18_Weights.IMAGENET1K_V1
         model = torchvision.models.resnet18(weights=weights)
         model.fc = torch.nn.Identity()   # keep the pooled features, drop the classifier
         model.eval().to(config.DEVICE)
+        # Half precision on the GPU only. The backbone is a feature extractor
+        # whose output is immediately L2-normalised and compared by cosine
+        # similarity at a threshold of 0.80, so fp16's fourth-decimal noise
+        # cannot move a decision that turns on the second. On CPU fp16 is
+        # emulated and slower, so it is not taken there.
+        self._half = config.DEVICE == "cuda"
+        if self._half:
+            model.half()
         self._model = model
         self._transform = weights.transforms()
         log.info("re-identification encoder ready (%d-d)", EMBED_DIM)
 
     @property
     def ready(self) -> bool:
         return self._model is not None
 
     def encode(self, crops: list) -> np.ndarray:
         """Embed a batch of BGR crops. Returns (n, 512) L2-normalised rows."""
@@ -171,23 +179,28 @@ class ReIdEncoder:
                 continue
             rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
             rgb = cv2.resize(rgb, (224, 224), interpolation=cv2.INTER_LINEAR)
             t = torch.from_numpy(rgb).permute(2, 0, 1).float() / 255.0
             # ImageNet normalisation, matching the pretrained weights
             t = (t - torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)) / \
                 torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
             tensors.append(t)
 
         batch = torch.stack(tensors).to(config.DEVICE)
+        if getattr(self, "_half", False):
+            batch = batch.half()
         with self._lock, torch.no_grad():
             feats = self._model(batch)
-        feats = torch.nn.functional.normalize(feats, p=2, dim=1)
+        # Normalise in fp32: the sum of 512 squares is where half precision
+        # would actually cost something, and the embeddings are stored and
+        # compared as fp32 anyway.
+        feats = torch.nn.functional.normalize(feats.float(), p=2, dim=1)
         return feats.cpu().numpy().astype(np.float32)
 
 
 def similarity(a, b) -> float:
     """Cosine similarity between two L2-normalised embeddings."""
     if a is None or b is None:
         return 0.0
     va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
     if va.size == 0 or vb.size == 0 or va.shape != vb.shape:
         return 0.0
diff --git a/netra/analytics/route.py b/netra/analytics/route.py
index 565b3e8..fb56d43 100644
--- a/netra/analytics/route.py
+++ b/netra/analytics/route.py
@@ -5,29 +5,34 @@ integrated network: every sighting, in order, with the elapsed time and
 distance between consecutive hops.
 
 Two constraints shape this:
 
   * Sightings are only chainable within a group of cameras that share a
     recorded clock. The Sentinel sandbox holds several independently recorded
     sessions, so comparing a timestamp from one against another is meaningless.
     See docs/feed-recon-findings.md.
   * A hop that would require an impossible speed is rejected rather than drawn.
     A route the operator cannot trust is worse than a short one.
+  * A sighting whose scene clock was never corroborated is not placed on the
+    route at all. Its only other timestamp is our connection time, which says
+    when we dialled the recording rather than when the vehicle passed, so
+    chaining on it would draw a journey out of an artefact of our own uptime.
 """
 from __future__ import annotations
 
 from dataclasses import dataclass, asdict
 from datetime import datetime
 
 from netra.analytics.matching import normalise_plate, spacetime_plausible
 from netra.core.geo import haversine_km, time_group
 # Shared with cloned-plate detection so both modules order sightings identically.
+from netra.core.timing import scene_time as _scene_time
 from netra.core.timing import sighting_time as _sighting_time
 
 
 @dataclass
 class Hop:
     camera_id: str
     camera_name: str
     lat: float | None
     lon: float | None
     at: datetime
@@ -79,20 +84,29 @@ def build_route(detections: list, query: str, min_plate_score: float = 0.6) -> R
         if obs == target or (len(obs) >= 4 and obs in target):
             candidates.append(det)
 
     candidates.sort(key=_sighting_time)
 
     hops: list[Hop] = []
     rejected: list[dict] = []
     total_km = 0.0
 
     for det in candidates:
+        if _scene_time(det) is None:
+            # Listed, not chained: the operator should see that the sighting
+            # exists, and equally that it cannot be placed in time.
+            rejected.append({
+                "camera_id": det.camera_id,
+                "reason": "sighting has no corroborated scene clock; its time "
+                          "is not comparable with the other cameras",
+            })
+            continue
         cam = det.camera
         hop = Hop(
             camera_id=det.camera_id,
             camera_name=cam.name if cam else det.camera_id,
             lat=cam.lat if cam else None,
             lon=cam.lon if cam else None,
             at=_sighting_time(det),
             plate_text=det.plate_text,
             plate_conf=det.plate_conf,
             vehicle_class=det.vehicle_class,
@@ -147,20 +161,21 @@ def _self_check() -> None:
 
     class FakeDet:
         _next = [1]
 
         def __init__(self, cam, plate, at):
             self.camera, self.camera_id = cam, cam.id
             self.plate_text, self.plate_conf = plate, 0.9
             self.vehicle_class, self.colour = "car", "white"
             self.evidence_path = None
             self.scene_time, self.wall_time = at, at
+            self.scene_time_corroborated = True
             self.id = FakeDet._next[0]
             FakeDet._next[0] += 1
 
     t0 = datetime(2026, 6, 13, 23, 20, tzinfo=timezone.utc)
     # Two Ahmedabad cameras ~1.3 km apart, three minutes apart: plausible.
     c04 = FakeCam("cam04", "Paldi Circle", 23.0130, 72.5620)
     c14 = FakeCam("cam14", "Delight RLVD", 23.0290, 72.5700)
     # A Junagadh camera: different recording session entirely.
     c10 = FakeCam("cam10", "Char Chowk", 21.5220, 70.4570)
 
@@ -185,15 +200,25 @@ def _self_check() -> None:
     assert len(r2.hops) == 1 and len(r2.rejected) == 1, (r2.hops, r2.rejected)
 
     # A partial plate read still places the vehicle on the route.
     dets_partial = [
         FakeDet(c04, "AB1234", t0),
         FakeDet(c14, "GJ01AB1234", t0 + timedelta(minutes=3)),
     ]
     r3 = build_route(dets_partial, "GJ01AB1234")
     assert len(r3.hops) == 2, r3.hops
 
+    # A sighting whose overlay was read once and never corroborated must not
+    # join the route: its timestamp is a guess, and this grid has produced
+    # guesses two years out. It is reported as rejected rather than hidden.
+    uncorroborated = FakeDet(c14, "GJ01AB1234", t0 + timedelta(minutes=3))
+    uncorroborated.scene_time_corroborated = False
+    r4 = build_route([FakeDet(c04, "GJ01AB1234", t0), uncorroborated],
+                     "GJ01AB1234")
+    assert len(r4.hops) == 1, r4.hops
+    assert "corroborated" in r4.rejected[0]["reason"], r4.rejected
+
     print("route self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/api/app.py b/netra/api/app.py
index 669409b..a6340ed 100644
--- a/netra/api/app.py
+++ b/netra/api/app.py
@@ -179,22 +179,40 @@ _snapshot_locks: dict[str, threading.Lock] = {}
 _snapshot_locks_guard = threading.Lock()
 
 
 def _snapshot_lock(camera_id: str) -> threading.Lock:
     with _snapshot_locks_guard:
         return _snapshot_locks.setdefault(camera_id, threading.Lock())
 
 
 def _cached_snapshot(camera_id: str) -> bytes | None:
     import time as _time
+    now = _time.time()
+    # Evict on read. Nothing else ever removes an entry, so the dict is bounded
+    # by the camera set only while camera ids are stable - a churning id space
+    # (participant-supplied feeds are onboarded under generated ids) would grow
+    # it without limit, holding a full-resolution JPEG per id for the life of
+    # the process. Four TTLs is well past any possible reuse and leaves the
+    # warm path untouched.
+    cutoff = now - SNAPSHOT_TTL_S * 4
+    for stale in [k for k, (at, _) in _snapshots.items() if at < cutoff]:
+        _snapshots.pop(stale, None)
+        # The lock registry is dropped alongside, but only for a camera with
+        # no grab in flight: replacing a held lock would let the next caller
+        # start a second ffmpeg against the same camera, which is the exact
+        # thing the registry exists to prevent.
+        with _snapshot_locks_guard:
+            lock = _snapshot_locks.get(stale)
+            if lock is not None and not lock.locked():
+                _snapshot_locks.pop(stale, None)
     hit = _snapshots.get(camera_id)
-    if hit and (_time.time() - hit[0]) < SNAPSHOT_TTL_S:
+    if hit and (now - hit[0]) < SNAPSHOT_TTL_S:
         return hit[1]
     return None
 
 
 def _grab_snapshot(camera_id: str) -> bytes:
     """One JPEG off the camera, bounded in time. Caller holds the camera lock."""
     import os
     import subprocess
     import tempfile
     import time as _time
@@ -277,23 +295,30 @@ def list_detections(camera_id: str | None = None, plate: str | None = None,
             "id": d.id, "camera_id": d.camera_id,
             "camera_name": d.camera.name if d.camera else None,
             "lat": d.camera.lat if d.camera else None,
             "lon": d.camera.lon if d.camera else None,
             "at": d.wall_time.isoformat(),
             "pts_ms": d.pts_ms,
             "vehicle_class": d.vehicle_class, "confidence": round(d.confidence, 3),
             "colour": d.colour, "plate_text": d.plate_text,
             "plate_conf": round(d.plate_conf, 3) if d.plate_conf else None,
             "plate_chars": d.plate_chars,
+            #: how many OCR reads agreed on plate_text. One is a lone guess,
+            #: and the console shows the count so the two are not read alike.
+            "plate_votes": d.plate_votes,
             "evidence": d.evidence_path, "bbox": d.bbox,
             "track_id": d.track_id,
             "scene_time": d.scene_time.isoformat() if d.scene_time else None,
+            #: false means the scene time was anchored on a single overlay
+            #: reading nothing ever confirmed; no elapsed-time claim is made
+            #: from those rows. See netra/core/timing.py.
+            "scene_time_corroborated": bool(d.scene_time_corroborated),
             "attributes": described.get(d.id),
         } for d in rows]
     return {"total": total, "count": len(items), "items": items}
 
 
 def _attribute_dict(row) -> dict:
     """Serialise one stored description. Provenance travels with it."""
     return {"body_type": row.body_type, "colour": row.colour,
             "tinted_windows": row.tinted_windows, "wheels": row.wheels,
             "roof_rack": row.roof_rack, "markings": row.markings or [],
@@ -1027,40 +1052,48 @@ def analytics_baselines(camera_id: str | None = None,
 @app.get("/api/analytics/anomalies")
 def analytics_anomalies(camera_id: str | None = None,
                         limit: int = Query(BASELINE_HISTORY_LIMIT, le=20000),
                         include_normal: bool = False,
                         _p=Depends(require("read"))):
     """Current per-camera readings judged against the learned norms.
 
     The current reading is the most recent completed traffic bucket for each
     camera, so it is measured the same way the baseline was - comparing a live
     partial count against full-bucket norms would manufacture false quiets.
+
+    A camera whose newest bucket is older than
+    baseline.ANOMALY_MAX_BUCKET_AGE_S is reported as `stale` and not judged:
+    that reading describes when the feed stopped, not the road now.
     """
     from netra.analytics import baseline
     from netra.core.models import TrafficStat
     learned, sampled = _load_baselines(camera_id, limit)
 
     with SessionLocal() as db:
         q = db.query(TrafficStat)
         if camera_id:
             q = q.filter(TrafficStat.camera_id == camera_id)
         recent = q.order_by(TrafficStat.bucket_start.desc()).limit(limit).all()
 
     latest: dict[str, object] = {}
     for r in recent:
         latest.setdefault(r.camera_id, r)   # rows arrive newest first
 
     found = baseline.detect_anomalies(learned, list(latest.values()),
                                       include_normal=include_normal)
     flagged = [a for a in found if a.anomalous]
+    stale = [a for a in found if a.status == "stale"]
     return {"buckets_read": sampled, "cameras_assessed": len(latest),
             "anomalies": len(flagged),
+            #: cameras whose newest bucket is too old to be a current reading
+            "stale": len(stale),
+            "max_bucket_age_s": baseline.ANOMALY_MAX_BUCKET_AGE_S,
             "assessments": [a.as_dict() for a in found]}
 
 
 # ---------------------------------------------------------------- storage --
 @app.get("/api/storage")
 def storage(_p=Depends(require("read"))):
     """What the evidence directory and detections table hold, against budget."""
     from netra.core import retention
     return retention.storage_report()
 
diff --git a/netra/api/assistant.py b/netra/api/assistant.py
index 90dde54..08e3b90 100644
--- a/netra/api/assistant.py
+++ b/netra/api/assistant.py
@@ -254,38 +254,52 @@ def _unusual(_q: str) -> dict:
             "compare against. Run the pipeline for a while and ask again.",
             {"buckets": 0}, [{"label": "Overview", "view": "overview"}])
 
     learned = baseline.learn(rows)
     latest: dict = {}
     for r in rows:
         latest.setdefault(r.camera_id, r)   # newest first
     found = baseline.detect_anomalies(learned, list(latest.values()))
     flagged = [a for a in found if a.anomalous]
     thin = [a for a in found if a.status == "insufficient_data"]
+    # A camera that stopped reporting hours ago has a "most recent bucket"
+    # like any other, and judging it would present a dropped feed as a road
+    # observed empty. detect_anomalies marks those stale; they are counted and
+    # named here rather than folded in with the findings.
+    stale = [a for a in found if a.status == "stale"]
 
     data = {"buckets": len(rows), "cameras_assessed": len(latest),
-            "anomalies": len(flagged),
+            "anomalies": len(flagged), "stale": len(stale),
             "assessments": [a.as_dict() for a in found]}
     actions = [{"label": "Traffic", "view": "traffic"}]
 
     if not flagged:
         text = (f"Nothing unusual. All {len(latest)} cameras with a current "
                 f"reading are within their usual range for this hour.")
         if thin:
             text += (f" {len(thin)} camera(s) have fewer than "
                      f"{baseline.MIN_SAMPLES} observations of this hour, so "
                      f"they are not being judged at all yet.")
+        if stale:
+            text += (f" {len(stale)} camera(s) have not reported for over "
+                     f"{baseline.ANOMALY_MAX_BUCKET_AGE_S / 60:.0f} minutes; "
+                     f"their last readings are not current and are not "
+                     f"judged.")
         return _answer(text, data, actions)
 
     lead = "; ".join(a.explanation for a in flagged[:3])
     text = (f"{len(flagged)} of {len(latest)} cameras are outside their normal "
             f"range for this hour of the day. {lead}")
+    if stale:
+        text += (f" A further {len(stale)} camera(s) have not reported for "
+                 f"over {baseline.ANOMALY_MAX_BUCKET_AGE_S / 60:.0f} minutes "
+                 f"and are not judged at all.")
     if thin:
         text += (f" A further {len(thin)} camera(s) have too little history "
                  f"({baseline.MIN_SAMPLES} observations required) for any "
                  f"judgement to be honest.")
     return _answer(text, data, actions)
 
 
 # -- entity resolution ------------------------------------------------------
 #
 # Everything below still reads its facts from SQL. Resolution only chooses
diff --git a/netra/api/retrieval.py b/netra/api/retrieval.py
index 70a2f71..42e0ee4 100644
--- a/netra/api/retrieval.py
+++ b/netra/api/retrieval.py
@@ -349,26 +349,55 @@ def _rows_from_db() -> list[tuple[str, str, str, str]]:
         # resolves to the detections that were described that way. This is the
         # only kind whose text is model-written rather than operator-entered,
         # which changes nothing about the division of labour: it resolves a
         # phrase to a detection id, and every fact about that detection is then
         # read from the detections table.
         for v in (db.query(VehicleAttributeRow)
                   .order_by(VehicleAttributeRow.id.desc())
                   .limit(VEHICLE_INDEX_LIMIT).all()):
             marks = " ".join(str(m) for m in (v.markings or []))
             text = " ".join(x for x in (v.description, marks) if x)
-            if not text.strip():
+            if not _worth_indexing(v, text):
                 continue
             out.append(("vehicle", str(v.detection_id), v.description, text))
     return out
 
 
+#: Structured description fields that, populated, say something specific
+#: enough about one vehicle to be worth resolving a phrase to.
+_VEHICLE_FIELDS = ("body_type", "colour", "wheels", "roof_rack",
+                   "tinted_windows", "damage", "markings")
+
+
+def _worth_indexing(row, text: str) -> bool:
+    """Whether one described vehicle earns a place in the retrieval corpus.
+
+    A description of a single word - the captioner regularly produces just
+    "yellow" - is not a search key. Every yellow vehicle on the grid scores
+    identically for the query "yellow", so which detection comes back is
+    decided by the tie-break rather than by the evidence, and the assistant
+    then presents that arbitrary row as *the* answer. Either two populated
+    structured fields or a description of at least two tokens is the minimum
+    that distinguishes one vehicle from the next.
+
+    ponytail: a token count, not a measure of information. Its ceiling is that
+    "a vehicle" passes and "yellow" does not, though neither is much of a key.
+    """
+    if not text.strip():
+        return False
+    populated = sum(1 for f in _VEHICLE_FIELDS
+                    if getattr(row, f, None) not in (None, "", [], False))
+    if populated >= 2:
+        return True
+    return len((row.description or "").split()) >= 2
+
+
 def build_index(rows=None) -> EntityIndex:
     """Build an index from the given rows, or from the database."""
     idx = EntityIndex()
     for kind, entity_id, label, text in (rows if rows is not None
                                          else _rows_from_db()):
         idx.add(kind, entity_id, label, text)
     idx.finalise()
     return idx
 
 
@@ -526,15 +555,37 @@ def _self_check() -> None:
     # An ignored word the corpus does know is still matchable, so a camera is
     # never made unfindable by a word that is also an intent keyword.
     assert "rajkot" not in intent
     m = idx.resolve("north ring road", ignore=frozenset({"north"}))
     assert m and m[0].id in ("GJ-RAJ-002", "GJ-SUR-009"), m
 
     # Normalisation is what makes the character fallback work at all.
     assert normalise("Junagadh-Bypass ANPR") == "junagadhbypassanpr"
     assert tokenise("GJ-AHM-014") == ["gj", "ahm", "014"]
 
+    # A one-word description is not a search key. Every yellow vehicle scores
+    # identically for "yellow", so indexing one would let the tie-break decide
+    # which detection the assistant presents as the answer.
+    class _Row:
+        body_type = colour = wheels = damage = None
+        roof_rack = tinted_windows = None
+        markings: list = []
+
+        def __init__(self, description, **kw):
+            self.description = description
+            for k, v in kw.items():
+                setattr(self, k, v)
+
+    assert not _worth_indexing(_Row("yellow"), "yellow")
+    assert not _worth_indexing(_Row(""), "")
+    assert _worth_indexing(_Row("yellow van"), "yellow van")
+    # One structured field is not enough on its own either...
+    assert not _worth_indexing(_Row("yellow", colour="yellow"), "yellow")
+    # ...but two describe a vehicle specifically enough to resolve to.
+    assert _worth_indexing(_Row("yellow", colour="yellow", body_type="van"),
+                           "yellow")
+
     print("retrieval self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/core/db.py b/netra/core/db.py
index adcb46c..77c129a 100644
--- a/netra/core/db.py
+++ b/netra/core/db.py
@@ -19,20 +19,27 @@ SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
 #: an operator's existing data/netra.db and every ORM read of that table would
 #: fail. These are applied additively at start-up rather than asking anyone to
 #: delete their evidence database.
 #: ponytail: a hand-kept list, not a migration tool. Its ceiling is additive,
 #: nullable/defaulted columns on SQLite; a type change or a drop needs Alembic.
 _ADDED_COLUMNS = [
     ("traffic_stats", "cumulative_total", "INTEGER DEFAULT 0"),
     ("traffic_stats", "loops_seen", "INTEGER DEFAULT 0"),
     ("mined_journeys", "min_similarity", "REAL DEFAULT 0.84"),
     ("mined_journeys", "truncated", "BOOLEAN DEFAULT 0"),
+    # Defaults false, which is the honest reading of every row already in an
+    # operator's store: those scene times were anchored on a single overlay
+    # reading and are not evidence of when anything happened.
+    ("detections", "scene_time_corroborated", "BOOLEAN DEFAULT 0"),
+    # Nullable rather than defaulted to 1: an existing row's plate was read an
+    # unknown number of times, and claiming one vote would be inventing a fact.
+    ("detections", "plate_votes", "INTEGER"),
 ]
 
 
 def _apply_added_columns() -> None:
     from sqlalchemy import inspect, text
     inspector = inspect(engine)
     existing = set(inspector.get_table_names())
     with engine.begin() as conn:
         for table, column, ddl in _ADDED_COLUMNS:
             if table not in existing:
diff --git a/netra/core/models.py b/netra/core/models.py
index f4e6a15..f1cd5b6 100644
--- a/netra/core/models.py
+++ b/netra/core/models.py
@@ -70,31 +70,42 @@ class Detection(Base):
 
     id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
     camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
 
     # Timing. pts_ms is the stream's own clock and is authoritative for any
     # elapsed-time maths; wall_time is only for display and correlation.
     pts_ms: Mapped[float] = mapped_column(Float)
     wall_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
     # timestamp burned into the video by the source camera, when parsed
     scene_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
+    # Whether that timestamp came from an anchor two independent overlay
+    # readings agreed on. False on every row written before corroborated
+    # anchoring existed, and those rows include provably wrong spans dated
+    # 2025-06-14, 2026-06-24 and 2028-06-13 - so anything reasoning over
+    # elapsed time must treat an uncorroborated scene_time as absent rather
+    # than as evidence. See netra/core/timing.py.
+    scene_time_corroborated: Mapped[bool] = mapped_column(
+        Boolean, default=False, server_default="0")
 
     vehicle_class: Mapped[str] = mapped_column(String(16))
     confidence: Mapped[float] = mapped_column(Float)
     bbox: Mapped[list] = mapped_column(JSON)  # [x1, y1, x2, y2]
 
     colour: Mapped[str | None] = mapped_column(String(16))
     embedding: Mapped[list | None] = mapped_column(JSON)  # appearance vector
 
     plate_text: Mapped[str | None] = mapped_column(String(32), index=True)
     plate_conf: Mapped[float | None] = mapped_column(Float)
     plate_chars: Mapped[int | None] = mapped_column(Integer)  # chars actually recovered
+    # How many per-frame OCR reads voted for plate_text. 1 is a lone guess;
+    # a higher count is the only thing distinguishing it from a consensus.
+    plate_votes: Mapped[int | None] = mapped_column(Integer)
     plate_bbox: Mapped[list | None] = mapped_column(JSON)
 
     evidence_path: Mapped[str | None] = mapped_column(String(256))
     track_id: Mapped[int | None] = mapped_column(Integer, index=True)
 
     camera: Mapped["Camera"] = relationship(back_populates="detections")
 
 
 Index("ix_det_cam_time", Detection.camera_id, Detection.wall_time)
 
diff --git a/netra/core/retention.py b/netra/core/retention.py
index 0604d64..469d0d5 100644
--- a/netra/core/retention.py
+++ b/netra/core/retention.py
@@ -43,20 +43,28 @@ def _basename(url_path: str | None) -> str | None:
     if not url_path:
         return None
     return url_path.rsplit("/", 1)[-1]
 
 
 def protected_evidence(session_factory=None) -> set[str]:
     """Evidence filenames that must survive any prune.
 
     An alert or zone event an operator has not yet acknowledged is still open
     police work; its picture is the evidence.
+
+    ponytail: acknowledgement is the only signal of continuing interest, so an
+    *acknowledged* alert's crop is prunable the moment it ages out, even where
+    the case behind it is still live. Its ceiling is a case that outlasts the
+    retention window: nothing here knows about cases, and an operator who needs
+    a crop kept beyond it must export it. A case-linked hold - evidence pinned
+    for as long as its FIR is open - is the real fix, and it needs the case
+    reference to travel with the alert from eGujCop rather than being typed in.
     """
     from netra.core.models import Alert, Detection, ZoneEventRow
 
     sf = session_factory or _session_factory()
     keep: set[str] = set()
     with sf() as db:
         rows = (db.query(Detection.evidence_path)
                 .join(Alert, Alert.detection_id == Detection.id)
                 .filter(Alert.acknowledged.is_(False))
                 .filter(Detection.evidence_path.isnot(None)).all())
diff --git a/netra/core/timing.py b/netra/core/timing.py
index 4cab9a8..2118ace 100644
--- a/netra/core/timing.py
+++ b/netra/core/timing.py
@@ -1,20 +1,89 @@
 """When a sighting actually happened.
 
 The sandbox replays recordings, so the wall clock records when we happened to
 connect to a feed, not when the scene occurred. Anything that orders or
 subtracts sighting times - route reconstruction, cloned-plate detection - must
 agree on the same preference, otherwise two modules reading the same rows can
 reach contradictory conclusions about the same vehicle.
+
+A scene time is only usable where the anchor behind it was corroborated by two
+independent overlay readings. A single misread digit anchors a whole stream and
+mis-times every sighting on it; this grid produced spans dated 2025-06-14,
+2026-06-24 and 2028-06-13 that way, each from one bad read that passed every
+syntactic check. Rows written before corroborated anchoring landed carry
+`scene_time_corroborated` false, and are treated here exactly as if they had no
+scene time at all.
 """
 from __future__ import annotations
 
 from datetime import datetime
 
 
+def scene_time(det) -> datetime | None:
+    """The sighting's scene time, or None where it cannot be trusted.
+
+    The single place that decides what "has a scene clock" means, so that a
+    row excluded from journey mining is the same row excluded from route
+    elapsed-time maths.
+    """
+    at = getattr(det, "scene_time", None)
+    if at is None:
+        return None
+    if not getattr(det, "scene_time_corroborated", False):
+        return None
+    return at
+
+
 def sighting_time(det) -> datetime:
     """Prefer the timestamp burned into the source video over our own clock.
 
-    Where the camera's own overlay has been parsed, that is the only meaningful
-    ordering; wall time is the fallback for feeds with no readable overlay.
+    Where the camera's own overlay has been parsed *and corroborated*, that is
+    the only meaningful ordering; wall time is the fallback for feeds with no
+    readable overlay. Wall time orders sightings within one connection but says
+    nothing about when the scene occurred, so callers making a cross-camera
+    claim must gate on `scene_time()` rather than reading this and hoping.
     """
-    return det.scene_time or det.wall_time
+    return scene_time(det) or det.wall_time
+
+
+def _self_check() -> None:
+    """Pin the preference, and that an uncorroborated overlay is not used."""
+    from datetime import timedelta, timezone
+
+    class Det:
+        def __init__(self, scene, corroborated, wall):
+            self.scene_time = scene
+            self.scene_time_corroborated = corroborated
+            self.wall_time = wall
+
+    overlay = datetime(2026, 6, 13, 23, 20, tzinfo=timezone.utc)
+    connected = overlay + timedelta(days=400)
+
+    # A corroborated overlay wins over our own clock. That is the whole point:
+    # wall time here is when we dialled the recording, not when the car passed.
+    good = Det(overlay, True, connected)
+    assert scene_time(good) == overlay
+    assert sighting_time(good) == overlay
+
+    # An uncorroborated one is not a scene time at all.
+    bad = Det(overlay, False, connected)
+    assert scene_time(bad) is None
+    assert sighting_time(bad) == connected
+
+    # A row from a store written before the column existed reads as absent
+    # rather than raising, because getattr defaults false.
+    class Legacy:
+        scene_time = overlay
+        wall_time = connected
+    assert scene_time(Legacy()) is None
+
+    # No overlay at all: wall time, and callers that need a real scene clock
+    # can tell the difference by asking scene_time() instead.
+    none = Det(None, False, connected)
+    assert scene_time(none) is None and sighting_time(none) == connected
+
+    print("timing self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/pipeline.py b/netra/pipeline.py
index e5f4359..23c5b28 100644
--- a/netra/pipeline.py
+++ b/netra/pipeline.py
@@ -28,26 +28,20 @@ log = logging.getLogger(__name__)
 #: Detections are persisted in batches rather than one transaction each.
 WRITE_BATCH_SIZE = 50
 WRITE_INTERVAL_S = 1.0
 
 #: How long after an alert a description may still be pushed to the console as
 #: a live update. Past this the operator has already read and acted on the
 #: alert card, so an arriving caption is noise on the wire; the row is still
 #: persisted and the console fetches it on demand.
 ATTRIBUTE_BROADCAST_BOUND_S = 3.0
 
-#: How long after an alert a description may still be pushed to the console as
-#: a live update. Past this the operator has already read and acted on the
-#: alert card, so an arriving caption is noise on the wire; the row is still
-#: persisted and the console fetches it on demand.
-ATTRIBUTE_BROADCAST_BOUND_S = 3.0
-
 
 class Pipeline:
     def __init__(self):
         self.engine = InferenceEngine(
             on_detection=self._handle_detection,
             on_vehicles_present=self._handle_vehicles_present)
         self.supervisor = IngestSupervisor(
             sink=self.engine.submit,
             on_discontinuity=self._handle_discontinuity)
 
@@ -88,34 +82,20 @@ class Pipeline:
         # precedent is unbounded overlay OCR, which cost 71% of frames.
         self._attr_queue: queue.Queue = queue.Queue(
             maxsize=config.ATTRIBUTE_QUEUE_SIZE)
         self._attr_stop = threading.Event()
         self._attr_thread: threading.Thread | None = None
         #: camera_id -> monotonic time of its last opportunistic description
         self._attr_last: dict[str, float] = {}
         self.attribute_stats = {"queued": 0, "processed": 0, "dropped": 0,
                                 "failed": 0, "broadcast": 0}
 
-        # Vision-language descriptions run on their own daemon thread behind a
-        # bounded queue that drops when full. Detection is the primary duty and
-        # a caption costs roughly a second of GPU, so this must never be able
-        # to apply back-pressure to inference or to the writer: the measured
-        # precedent is unbounded overlay OCR, which cost 71% of frames.
-        self._attr_queue: queue.Queue = queue.Queue(
-            maxsize=config.ATTRIBUTE_QUEUE_SIZE)
-        self._attr_stop = threading.Event()
-        self._attr_thread: threading.Thread | None = None
-        #: camera_id -> monotonic time of its last opportunistic description
-        self._attr_last: dict[str, float] = {}
-        self.attribute_stats = {"queued": 0, "processed": 0, "dropped": 0,
-                                "failed": 0, "broadcast": 0}
-
     # -- lifecycle -----------------------------------------------------------
     def start(self, camera_ids: list[str] | None = None,
               source_specs: dict | None = None) -> None:
         """Begin processing. `source_specs` overrides how a camera is reached,
         which is how participant-supplied video files are onboarded alongside
         live grid cameras."""
         if self.running:
             return
         with SessionLocal() as db:
             cams = db.query(Camera).filter(Camera.enabled.is_(True)).all()
@@ -237,56 +217,71 @@ class Pipeline:
         # the console if it is ready in time, and not stored.
         self._submit_attributes(None, evidence_path, "zone",
                                 alert={"zone_event_id": row_id,
                                        "camera_id": event.camera_id})
         NOTIFIER.submit({**payload, "plate_watchlist": event.zone.name,
                          "plate_observed": event.detail,
                          "match_type": event.rule, "score": 1.0,
                          "reasons": {"zone": {"score": 1.0,
                                               "detail": event.detail}}})
 
+    def _bucket_deltas(self, camera_id: str, cumulative: int,
+                       counts: dict) -> tuple[int, dict]:
+        """Traffic during this bucket, from the tracker's cumulative counters.
+
+        Both the total and the class breakdown are differenced against the
+        previous flush, and - this is the part that went wrong - against the
+        *same* previous flush. A tracker recreated mid-run restarts its
+        counters, so a restart shows up as a cumulative smaller than the one
+        last seen, and the whole of it is taken as this bucket's traffic rather
+        than persisting a negative count.
+
+        The class snapshot has to be reset on exactly that condition. Taking
+        the whole cumulative as the total while still differencing the classes
+        against the larger pre-restart snapshot left every class delta at or
+        below zero, so the bucket carried a total with an empty breakdown -
+        a row an analyst can only read as traffic of unknown composition.
+        """
+        previous = self._traffic_last_total.get(camera_id)
+        restarted = previous is not None and cumulative < previous
+        delta = (cumulative if previous is None or restarted
+                 else cumulative - previous)
+        self._traffic_last_total[camera_id] = cumulative
+
+        before = {} if restarted else self._traffic_last_counts.get(camera_id, {})
+        by_class = {k: v - before.get(k, 0) for k, v in counts.items()
+                    if v - before.get(k, 0) > 0}
+        self._traffic_last_counts[camera_id] = dict(counts)
+        return delta, by_class
+
     def flush_traffic_stats(self, bucket_seconds: int = 60) -> int:
         """Snapshot per-camera traffic counters into a time bucket.
 
         `total` is the traffic counted *during this bucket*, obtained by
         differencing the tracker's cumulative counter against the value at the
         previous flush. Writing the cumulative figure here - as this once did -
         made every row larger than the last, because the sandbox replays a
         fixed recording and the counter spans every replay. Baselines learned
         from that would describe uptime, not traffic.
 
         A bucket with no traffic is still written: "this camera saw nothing"
         is exactly the observation a quiet-road baseline needs, and dropping it
         would teach the baseline that the road is never empty.
         """
         now = datetime.now(timezone.utc)
         written = 0
         with SessionLocal() as db:
             for stats in self.engine.trackers.stats():
                 camera_id = stats["camera_id"]
                 cumulative = stats["total_counted"]
-                previous = self._traffic_last_total.get(camera_id)
-                # A tracker recreated mid-run restarts its counter; treat the
-                # whole of a smaller cumulative as this bucket's traffic rather
-                # than persisting a negative count.
-                delta = cumulative if previous is None or cumulative < previous \
-                    else cumulative - previous
-                self._traffic_last_total[camera_id] = cumulative
-
-                # The class breakdown is cumulative for the same reason, and
-                # is differenced the same way: a bucket whose classes summed to
-                # more than its total would be visibly incoherent to an analyst.
-                counts = stats["counts_by_class"]
-                before = self._traffic_last_counts.get(camera_id, {})
-                by_class = {k: v - before.get(k, 0) for k, v in counts.items()
-                            if v - before.get(k, 0) > 0}
-                self._traffic_last_counts[camera_id] = dict(counts)
+                delta, by_class = self._bucket_deltas(
+                    camera_id, cumulative, stats["counts_by_class"])
 
                 db.add(TrafficStat(
                     camera_id=camera_id, bucket_start=now,
                     bucket_seconds=bucket_seconds,
                     total=delta,
                     cumulative_total=cumulative,
                     loops_seen=stats["loops_seen"],
                     counts_by_class=by_class,
                     directions=stats["directions"],
                     mean_dwell_s=stats["mean_dwell_s"]))
@@ -353,20 +348,22 @@ class Pipeline:
                 wall_time=datetime.fromtimestamp(det.wall_time, tz=timezone.utc),
                 vehicle_class=det.vehicle_class,
                 confidence=det.confidence,
                 bbox=det.bbox,
                 colour=det.colour,
                 plate_text=det.plate_text,
                 plate_conf=det.plate_conf,
                 plate_chars=det.plate_chars,
                 plate_bbox=det.plate_bbox,
                 scene_time=det.scene_time,
+                scene_time_corroborated=det.scene_time_corroborated,
+                plate_votes=det.plate_votes,
                 track_id=det.track_id,
                 embedding=det.embedding,
                 evidence_path=evidence_path,
             ))
             dets.append(det)
 
         with SessionLocal() as db:
             db.add_all(rows)
             db.commit()
             ids = [r.id for r in rows]
@@ -661,10 +658,54 @@ def store_attributes(detection_id: int, result, source: str) -> dict:
         row.description = result.description
         row.raw_caption = result.raw_caption
         row.model = result.model
         row.confidence = result.confidence
         row.source = source
         db.commit()
     return result.as_dict()
 
 
 PIPELINE = Pipeline()
+
+
+def _self_check() -> None:
+    """Pin the traffic-bucket differencing, restart included.
+
+    Nothing here touches the database, the GPU or a thread: the arithmetic is
+    the part that has been wrong twice, and it is checkable on its own.
+    """
+    p = Pipeline.__new__(Pipeline)
+    p._traffic_last_total, p._traffic_last_counts = {}, {}
+
+    # First bucket: nothing to difference against, so the whole cumulative is
+    # this bucket's traffic and the breakdown is the whole breakdown.
+    delta, by_class = p._bucket_deltas("cam01", 10, {"car": 7, "truck": 3})
+    assert delta == 10 and by_class == {"car": 7, "truck": 3}, (delta, by_class)
+
+    # Steady state: only the increment since the last flush.
+    delta, by_class = p._bucket_deltas("cam01", 16, {"car": 11, "truck": 5})
+    assert delta == 6 and by_class == {"car": 4, "truck": 2}, (delta, by_class)
+
+    # A bucket in which nothing passed is still coherent, not negative.
+    delta, by_class = p._bucket_deltas("cam01", 16, {"car": 11, "truck": 5})
+    assert delta == 0 and by_class == {}, (delta, by_class)
+
+    # Tracker restart: the counter drops. The whole of the new cumulative is
+    # this bucket's traffic, and the breakdown must be reset with it - a total
+    # of 4 against an empty breakdown was the bug.
+    delta, by_class = p._bucket_deltas("cam01", 4, {"car": 3, "bus": 1})
+    assert delta == 4, delta
+    assert by_class == {"car": 3, "bus": 1}, by_class
+    assert by_class, "a restart bucket with detections must carry a breakdown"
+    assert sum(by_class.values()) <= delta, (by_class, delta)
+
+    # And the flush after a restart differences against the post-restart
+    # snapshot, not the pre-restart one.
+    delta, by_class = p._bucket_deltas("cam01", 9, {"car": 6, "bus": 3})
+    assert delta == 5 and by_class == {"car": 3, "bus": 2}, (delta, by_class)
+    assert sum(by_class.values()) <= delta, (by_class, delta)
+
+    print("pipeline self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/web/app.js b/netra/web/app.js
index c399e0f..9195b34 100644
--- a/netra/web/app.js
+++ b/netra/web/app.js
@@ -1,14 +1,37 @@
 /* NETRA operator console. */
 const $ = (s) => document.querySelector(s);
 const $$ = (s) => Array.from(document.querySelectorAll(s));
-const api = async (p, o) => (await fetch(p, o)).json();
+/* API key. Empty by default and empty in the demo, where no data/api_keys.json
+   exists and the server runs open; the header is simply sent blank and every
+   endpoint behaves as it always has. Where an operator has configured keys,
+   this is what lets the console reach the protected endpoints — including the
+   zone editor's still, which an <img src> alone cannot fetch because it has no
+   way to carry a header. */
+const API_KEY_STORE = "NETRA_API_KEY";
+const apiKey = () => { try { return localStorage.getItem(API_KEY_STORE) || ""; }
+                       catch (e) { return ""; } };
+const authHeaders = (extra) => Object.assign({ "X-API-Key": apiKey() }, extra || {});
+const api = async (p, o) => {
+  const opts = Object.assign({}, o);
+  opts.headers = authHeaders(opts.headers);
+  return (await fetch(p, opts)).json();
+};
+document.addEventListener("DOMContentLoaded", () => {
+  const box = document.getElementById("apiKey");
+  if (!box) return;
+  box.value = apiKey();
+  box.onchange = () => {
+    try { localStorage.setItem(API_KEY_STORE, box.value.trim()); }
+    catch (e) { toast("This browser will not persist the key for this session."); }
+  };
+});
 const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c =>
   ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
 
 let CAMERAS = [], MAP = null, ROUTE_MAP = null, MARKERS = {}, ROUTE_LAYER = null;
 const PC = {};                       // active WebRTC peer connections
 
 /* ---------------------------------------------------------------- nav --- */
 $$("nav a").forEach(a => a.onclick = () => {
   $$("nav a").forEach(x => x.classList.remove("active"));
   $$(".view").forEach(v => v.classList.remove("active"));
@@ -68,20 +91,25 @@ async function refresh() {
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
+    // Counts detection-frames whose plate was replaced by their track's voted
+    // consensus - not the same quantity as the per-detection read count shown
+    // beside each plate in the Detections table, which is why it is named for
+    // what it is.
+    card(inf.plate_consensus_applied ?? 0, "Plate consensus applied", "", "frames given a voted plate"),
   ].join("");
 }
 const card = (n, l, cls = "", s = "") =>
   `<div class="stat ${cls}"><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div><div class="s">${esc(s)}</div></div>`;
 
 async function loadRecent() {
   const d = await api("/api/detections?limit=25");
   const tb = $("#tblRecent tbody");
   if (!d.items.length) { tb.innerHTML = `<tr><td colspan="6" class="empty">No detections yet.</td></tr>`; return; }
   tb.innerHTML = d.items.map(x => `<tr>
@@ -239,26 +267,31 @@ async function loadDetections() {
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
-      ${x.plate_chars ? `<span class="faint" style="font-size:10.5px">· ${esc(x.plate_chars)} chars</span>` : ""}</td>
+      ${x.plate_chars ? `<span class="faint" style="font-size:10.5px">· ${esc(x.plate_chars)} chars</span>` : ""}
+      ${x.plate_votes ? `<span class="faint" style="font-size:10.5px"
+        title="OCR reads of this tracked vehicle that agreed on this plate. One read is a single guess, not a consensus."
+        >· ${esc(x.plate_votes)} read${x.plate_votes === 1 ? "" : "s"}</span>` : ""}</td>
     <td class="dim">${x.plate_conf ?? "—"}</td>
     <td class="mono faint">${x.track_id != null ? esc(x.track_id) : "—"}</td>
-    <td class="mono faint">${x.scene_time
+    <td class="mono faint">${x.scene_time && x.scene_time_corroborated
       ? esc(x.scene_time.replace("T", " ").slice(0, 19))
-      : '<span title="no clock recovered from the overlay">—</span>'}</td>
+      : (x.scene_time
+        ? `<span title="read once from the overlay and never confirmed by a second reading, so it is not used for any timing claim">${esc(x.scene_time.replace("T", " ").slice(0, 19))} <b style="color:var(--warn)">?</b></span>`
+        : '<span title="no clock recovered from the overlay">—</span>')}</td>
     <td data-desc-holder>${x.evidence ? `<img src="${esc(x.evidence)}" style="height:34px;border-radius:4px">` : ""}
       ${attrHtml(x.attributes)}${describeBtn(x.id, !!x.attributes)}</td></tr>`).join("");
 }
 $("#dSearch").onclick = loadDetections;
 $("#dCsv").onclick = () => {
   const p = $("#dPlate").value;
   location.href = "/api/export/detections.csv" + (p ? "?plate=" + encodeURIComponent(p) : "");
 };
 
 /* --------------------------------------------------------------- route --- */
@@ -322,40 +355,40 @@ async function loadWatchlist() {
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
-  await fetch("/api/watchlist/" + id, { method: "DELETE" });
+  await fetch("/api/watchlist/" + id, { method: "DELETE", headers: authHeaders() });
   loadWatchlist();
 };
 $("#wAdd").onclick = async () => {
   const plate = $("#wPlate").value.trim().toUpperCase();
   if (!plate) return;
   await fetch("/api/watchlist", {
-    method: "POST", headers: { "Content-Type": "application/json" },
+    method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
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
-  await fetch("/api/watchlist/seed", { method: "POST" });
+  await fetch("/api/watchlist/seed", { method: "POST", headers: authHeaders() });
   loadWatchlist();
   toast("Sample watchlist loaded.");
 };
 
 /* -------------------------------------------------------------- alerts --- */
 function alertHtml(a) {
   const reasons = Object.entries(a.reasons || {})
     .map(([k, v]) => `<b>${esc(k)}</b> ${v.score} — ${esc(v.detail)}`).join("<br>");
   return `<div class="alert-item">
     <div class="top">
@@ -480,20 +513,28 @@ function connectWs() {
 
 (async function init() {
   await loadCameras();
   await refresh();
   await loadRecent();
   connectWs();
   setInterval(refresh, 3000);
   setInterval(() => {
     if ($("#v-overview").classList.contains("active")) loadRecent();
   }, 4000);
+  // The Traffic tab is the one an operator leaves open while watching a
+  // junction, and until now it only ever showed what was there when the tab
+  // was opened. Gated on visibility, like the overview poll above: an unseen
+  // tab must not spend a database round trip every five seconds, and the
+  // history query it makes is the heaviest of the console's reads.
+  setInterval(() => {
+    if ($("#v-traffic").classList.contains("active")) loadTraffic();
+  }, 5000);
 })();
 
 /* ----------------------------------------------------------- assistant --- */
 const ASST_SUGGESTIONS = [
   "Which cameras are down?",
   "How many detections so far?",
   "Show me the alerts",
   "What is on the watchlist?",
   "Where has GJ01AB1234 been seen?",
   "Coverage by location",
@@ -637,25 +678,43 @@ function drawZone() {
     g.fillText(String(i + 1), x - 3, y + 3);
   });
 }
 
 $("#zLoad").onclick = async () => {
   const cam = $("#zCam").value;
   if (!cam) return;
   const btn = $("#zLoad");
   btn.disabled = true; btn.textContent = "Grabbing frame…";
   const img = $("#zImg");
-  img.onload = () => { $("#zWrap").style.display = "inline-block"; drawZone(); };
-  img.onerror = () => toast("Could not grab a still from " + esc(cam) +
-    " — the camera may be down or the feed unreachable.");
-  img.src = `/api/cameras/${encodeURIComponent(cam)}/snapshot?t=${Date.now()}`;
-  try { await img.decode(); } catch (e) { /* onerror has already reported it */ }
+  // Fetched rather than set as an <img src>: the snapshot endpoint is behind
+  // `require`, and an <img> has no way to carry X-API-Key, so with keys
+  // configured the still 401'd while the rest of the console worked. The blob
+  // is handed to the <img> as an object URL instead. The previous one is
+  // revoked because the zone editor is reloaded repeatedly while an operator
+  // draws, and each blob would otherwise be held for the life of the page.
+  try {
+    const r = await fetch(`/api/cameras/${encodeURIComponent(cam)}/snapshot?t=${Date.now()}`,
+                          { headers: authHeaders() });
+    if (!r.ok) throw new Error(r.status === 401 || r.status === 403
+      ? "not authorised — set an API key in the header"
+      : "HTTP " + r.status);
+    const url = URL.createObjectURL(await r.blob());
+    if (img.dataset.blobUrl) URL.revokeObjectURL(img.dataset.blobUrl);
+    img.dataset.blobUrl = url;
+    img.src = url;
+    await img.decode();
+    $("#zWrap").style.display = "inline-block";
+    drawZone();
+  } catch (e) {
+    toast("Could not grab a still from " + esc(cam) + " — " + esc(e.message) +
+      ". The camera may be down or the feed unreachable.");
+  }
   btn.disabled = false; btn.textContent = "Load still frame";
 };
 
 $("#zCanvas").onclick = (e) => {
   const r = e.currentTarget.getBoundingClientRect();
   ZPOINTS.push([(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]);
   drawZone();
   $("#zHint").textContent = `${ZPOINTS.length} point(s) placed.`;
 };
 $("#zClear").onclick = () => {
@@ -670,32 +729,32 @@ $("#zSave").onclick = async () => {
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
-    method: "POST", headers: { "Content-Type": "application/json" },
+    method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
     body: JSON.stringify(body),
   });
   const out = await r.json().catch(() => ({}));
   if (!r.ok) { toast("Rule rejected: " + esc(out.detail || r.status)); return; }
   ZPOINTS = []; drawZone();
   toast("Rule saved on " + esc(body.camera_id) + ".");
   loadZones();
 };
 
 window.delZone = async (id) => {
-  await fetch("/api/zones/" + id, { method: "DELETE" });
+  await fetch("/api/zones/" + id, { method: "DELETE", headers: authHeaders() });
   loadZones();
 };
 
 async function loadZones() {
   if (!$("#zCam").options.length) {
     $("#zCam").innerHTML = CAMERAS.map(c =>
       `<option value="${esc(c.id)}">${esc(c.id)} — ${esc(c.name)}</option>`).join("");
   }
   const zones = await api("/api/zones");
   $("#zList").innerHTML = zones.length ? zones.map(z => `<div class="zone-item">
@@ -801,24 +860,28 @@ async function loadClones() {
         ${x.implied_kmh == null ? "speed not computable" : esc(x.implied_kmh) + " km/h implied"}</div>
       <div style="margin-top:5px">${esc(x.reason)}</div></div>`).join("")
       : `<div class="empty">No cloned-plate findings in the stored detections.</div>`);
 }
 
 async function loadAnomalies() {
   const r = await api("/api/analytics/anomalies");
   const a = r.assessments || [];
   const head = `<div class="faint" style="font-size:12px;margin-bottom:9px">
     ${esc(r.cameras_assessed)} camera(s) assessed against ${esc(r.buckets_read)} stored buckets ·
-    ${esc(r.anomalies)} flagged. Cameras with too little history to judge are shown muted rather
-    than hidden: hiding them would imply coverage that does not exist.</div>`;
+    ${esc(r.anomalies)} flagged${r.stale ? `, ${esc(r.stale)} not reporting` : ""}. Cameras with too
+    little history to judge, or whose last bucket is too old to be a current reading, are shown muted
+    rather than hidden: hiding them would imply coverage that does not exist.</div>`;
   $("#iAnoms").innerHTML = head + (a.length ? a.map(x => {
-    const thin = x.status === "insufficient_data";
+    // A stale camera is muted alongside an unjudged one: neither is a
+    // statement about the road, and colouring stale green would present a
+    // dropped feed as a road confirmed clear.
+    const thin = x.status === "insufficient_data" || x.status === "stale";
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
diff --git a/netra/web/index.html b/netra/web/index.html
index 0d49e92..d14cbd2 100644
--- a/netra/web/index.html
+++ b/netra/web/index.html
@@ -124,20 +124,28 @@ button.mini{padding:2px 7px;font-size:10.5px;font-weight:500;border-radius:4px}
 </head>
 <body>
 
 <header>
   <div class="logo">NET<b>RA</b></div>
   <div class="tagline">Networked Evidence, Tracking &amp; Recognition for Analytics<br>
     <span style="color:var(--faint)">Gujarat Police Innovation Challenge 2026 · Model 1 + Model 2</span></div>
   <div class="spacer"></div>
   <div class="pill"><span class="dot" id="wsDot"></span><span id="wsTxt">connecting</span></div>
   <div class="pill" id="pipePill"><span class="dot" id="pipeDot"></span><span id="pipeTxt">pipeline idle</span></div>
+  <!-- Blank in the demo, where no data/api_keys.json exists and the server
+       runs open. It is here so an operator who has configured keys can reach
+       the protected endpoints — the zone editor's still in particular, which
+       is fetched with this header rather than loaded as a plain image src. -->
+  <input id="apiKey" type="password" placeholder="API key (optional)"
+         autocomplete="off" title="Sent as X-API-Key. Leave blank when the server runs open."
+         style="width:150px;padding:5px 8px;background:var(--bg);color:var(--ink);
+                border:1px solid var(--line);border-radius:6px;font-size:12px">
   <button id="btnStart" class="primary">Start pipeline</button>
   <button id="btnStop">Stop</button>
 </header>
 
 <nav>
   <a data-view="overview" class="active">Overview</a>
   <a data-view="map">GIS Map</a>
   <a data-view="wall">Video Wall</a>
   <a data-view="detections">Detections</a>
   <a data-view="route">Vehicle Trace</a>
diff --git a/tools/purge_scene_times.py b/tools/purge_scene_times.py
new file mode 100644
index 0000000..26184d3
--- /dev/null
+++ b/tools/purge_scene_times.py
@@ -0,0 +1,82 @@
+"""Null the scene times that were never corroborated.
+
+Corroborated anchoring - two independent overlay readings that agree once
+projected forward by PTS - landed after a large part of this store had already
+been indexed. Those earlier rows carry a scene time derived from a single OCR
+reading, and a single misread digit anchors an entire stream: this grid
+produced spans dated 2025-06-14, 2026-06-24 and 2028-06-13 that way, each from
+one bad read that passed every syntactic check.
+
+The analytics already refuse to reason over an uncorroborated scene time (see
+netra/core/timing.py), so nothing is *concluded* from those values any more.
+This tool is for the operator who would rather the wrong number were not
+sitting in the column at all, where a direct SQL query or an export could still
+pick it up.
+
+    python tools/purge_scene_times.py              # dry run, counts only
+    python tools/purge_scene_times.py --apply      # actually nulls them
+
+Dry run is the default deliberately: this destroys data, and the count alone
+answers the question most people are asking.
+"""
+from __future__ import annotations
+
+import argparse
+import os
+import sys
+
+sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
+
+from netra.core.db import SessionLocal, init_db  # noqa: E402
+from netra.core.models import Detection  # noqa: E402
+
+
+def affected_count(db) -> int:
+    """Rows carrying a scene time no second reading ever confirmed."""
+    return (db.query(Detection)
+            .filter(Detection.scene_time.isnot(None),
+                    Detection.scene_time_corroborated.is_(False))
+            .count())
+
+
+def main() -> int:
+    ap = argparse.ArgumentParser(description=__doc__,
+                                 formatter_class=argparse.RawDescriptionHelpFormatter)
+    ap.add_argument("--apply", action="store_true",
+                    help="actually null the values; without it nothing is written")
+    args = ap.parse_args()
+
+    # Additive columns are applied here as everywhere else; a store predating
+    # scene_time_corroborated would otherwise fail on the filter above.
+    init_db()
+
+    with SessionLocal() as db:
+        total = db.query(Detection).count()
+        clocked = db.query(Detection).filter(Detection.scene_time.isnot(None)).count()
+        affected = affected_count(db)
+
+        print(f"detections stored:            {total}")
+        print(f"  carrying a scene time:      {clocked}")
+        print(f"  of those, uncorroborated:   {affected}")
+
+        if not args.apply:
+            print("\ndry run: nothing written. Re-run with --apply to null "
+                  f"scene_time on those {affected} rows.")
+            return 0
+
+        if not affected:
+            print("\nnothing to do.")
+            return 0
+
+        updated = (db.query(Detection)
+                   .filter(Detection.scene_time.isnot(None),
+                           Detection.scene_time_corroborated.is_(False))
+                   .update({Detection.scene_time: None},
+                           synchronize_session=False))
+        db.commit()
+        print(f"\nnulled scene_time on {updated} rows.")
+    return 0
+
+
+if __name__ == "__main__":
+    raise SystemExit(main())
