# Review package — Task 4

6e32b94 Bound evidence, detections and watchlist scoring

 .../task-4-report.md                               | 117 ++++++
 netra/analytics/inference.py                       | 159 ++++++++
 netra/analytics/matching.py                        | 155 ++++++++
 netra/api/app.py                                   |  29 ++
 netra/config.py                                    |  18 +
 netra/core/retention.py                            | 414 +++++++++++++++++++++
 netra/pipeline.py                                  |  21 +-
 7 files changed, 911 insertions(+), 2 deletions(-)

diff --git a/netra/analytics/inference.py b/netra/analytics/inference.py
index d856ba6..81cf70e 100644
--- a/netra/analytics/inference.py
+++ b/netra/analytics/inference.py
@@ -49,20 +49,60 @@ REID_MAX_PER_FRAME = 8
 
 #: Minimum vehicle height before a plate read is even attempted. A plate on a
 #: vehicle smaller than this spans a handful of pixels and cannot be resolved,
 #: so the OCR call is pure cost. Measured on this grid, no plate was readable
 #: on any vehicle at all - see docs/feed-recon-findings.md.
 PLATE_MIN_VEHICLE_PX = 110
 #: Cap on plate reads per frame. OCR is the most expensive operation in the
 #: pipeline at roughly 50ms per vehicle.
 PLATE_MAX_PER_FRAME = 4
 
+#: Mean luma below which a frame carries no scene at all. The registry
+#: classifies dead cameras at onboarding, but a camera can go dark afterwards -
+#: nightfall, a failed IR illuminator, a lens cover - and a black feed still
+#: costs a full YOLO pass per frame, forever, on a GPU that is the scarcest
+#: resource here. Measured across this grid, usable night frames sit well above
+#: 18 while dead feeds sit near zero.
+DARK_LUMA_THRESHOLD = 18
+#: Consecutive dark frames with nothing detected before a camera is skipped.
+#: Sixty at tier-1 rate is about a minute, which is long enough that a lorry
+#: parked against the lens or a passing cloud cannot trip it.
+DARK_FRAME_LIMIT = 60
+#: A dark camera is re-tested every this many frames so it recovers on its own
+#: at dawn. At tier-1 rate that is a probe roughly every five minutes: cheap
+#: against the ~99.7% of inference passes it saves, and quick enough that no
+#: real traffic is missed for long.
+DARK_RECHECK_FRAMES = 300
+#: Stride used to downscale a frame before measuring luma. Sampling every 8th
+#: pixel is a numpy view rather than a copy, so the measurement costs
+#: microseconds - it must not become a cost of its own.
+LUMA_SAMPLE_STRIDE = 8
+
+
+def mean_luma(image) -> float:
+    """Mean brightness of a frame, measured on a strided sample.
+
+    BT.601 weights on BGR. Deliberately not cv2.cvtColor over the full frame:
+    that allocates a greyscale copy of every frame on every camera, which is
+    real cost to answer a question about darkness.
+    """
+    if image is None or getattr(image, "size", 0) == 0:
+        return 0.0
+    sample = image[::LUMA_SAMPLE_STRIDE, ::LUMA_SAMPLE_STRIDE]
+    if sample.size == 0:
+        return 0.0
+    if sample.ndim == 3 and sample.shape[2] >= 3:
+        b, g, r = (sample[:, :, 0].mean(), sample[:, :, 1].mean(),
+                   sample[:, :, 2].mean())
+        return float(0.114 * b + 0.587 * g + 0.299 * r)
+    return float(sample.mean())
+
 
 @dataclass
 class VehicleDetection:
     camera_id: str
     pts_ms: float
     wall_time: float
     vehicle_class: str
     confidence: float
     bbox: list[int]
     colour: str | None = None
@@ -136,28 +176,35 @@ class InferenceEngine:
         self._clocks: dict = {}
         #: how many overlay reads have been attempted per camera
         self._clock_attempts: dict = {}
         #: per-camera trackers; tracking is what counting, direction, dwell
         #: and zone rules are all built on
         from netra.analytics.tracking import TrackerRegistry
         self.trackers = TrackerRegistry()
         #: camera_id -> PlateVoter; plate reads from one tracked vehicle vote
         #: together, because a single frame's read is a guess
         self._plate_voters: dict = {}
+        #: camera_id -> consecutive dark, empty frames seen
+        self._dark_streak: dict = {}
+        #: camera_id -> when it was marked dark; presence means "skip"
+        self._dark_cameras: dict = {}
+        #: camera_id -> frames skipped since the last probe
+        self._dark_skipped: dict = {}
         #: set by the pipeline so zone rules can be evaluated here, where the
         #: tracks live
         self.zone_engine = None
         self.on_zone_event = None
 
         self.stats = {"submitted": 0, "dropped": 0, "processed": 0,
                       "vehicles": 0, "plates": 0, "embedded": 0,
                       "clocks_anchored": 0, "plate_votes": 0,
+                      "dark_cameras": 0, "dark_frames_skipped": 0,
                       "infer_ms": 0.0}
 
     # -- model loading -------------------------------------------------------
     def load(self) -> None:
         from ultralytics import YOLO
         log.info("loading vehicle model on %s", config.DEVICE)
         self._vehicle_model = YOLO(config.VEHICLE_MODEL)
         self._vehicle_model.to(config.DEVICE)
 
         import os
@@ -223,20 +270,24 @@ class InferenceEngine:
     def reset_camera_state(self, camera_id: str) -> None:
         """Discard per-camera state after a loop cut.
 
         The recording restarted, so the previous scene-time anchor no longer
         describes this stream and must be read again.
         """
         self._clocks.pop(camera_id, None)
         self._clock_attempts.pop(camera_id, None)
         self.trackers.reset(camera_id)
         self._plate_voters.pop(camera_id, None)
+        self._dark_streak.pop(camera_id, None)
+        self._dark_cameras.pop(camera_id, None)
+        self._dark_skipped.pop(camera_id, None)
+        self.stats["dark_cameras"] = len(self._dark_cameras)
         if self.zone_engine is not None:
             self.zone_engine.reset_camera(camera_id)
 
     def _anchor_clock(self, frame) -> None:
         """Read the burned-in timestamp, then extrapolate until it goes stale.
 
         Attempts are capped. Reading an overlay costs several OCR passes over
         upscaled crops, and about half the cameras on this grid have no legible
         overlay at all - retrying every frame on those saturates the queue and
         starves detection, which matters far more than scene time. Measured
@@ -293,20 +344,23 @@ class InferenceEngine:
                      else "sightings on this camera carry no scene time")
 
     def _process(self, frame) -> None:
         t0 = time.time()
         img = frame.image
         capability = self.camera_capability.get(frame.camera_id, "vehicle")
 
         if capability == "degraded":
             return  # corrupt or unusable feed; health monitoring only
 
+        if not self._dark_gate(frame.camera_id):
+            return  # feed has gone dark; skipping until the next probe frame
+
         self._anchor_clock(frame)
         anchor = self._clocks.get(frame.camera_id)
         scene_time = anchor.at(frame.pts_ms) if anchor else None
 
         classes = None if capability == "person" else list(config.VEHICLE_CLASSES)
         if capability == "person":
             classes = [0]  # COCO person
 
         # Escalated cameras get the larger input size: they have traffic worth
         # resolving properly, and small distant vehicles are what a 640px pass
@@ -315,22 +369,24 @@ class InferenceEngine:
             else config.TIER1_IMGSZ
 
         results = self._vehicle_model.predict(
             img, device=config.DEVICE, verbose=False,
             conf=config.CONF_THRESHOLD, imgsz=imgsz, classes=classes)
 
         if not results:
             return
         boxes = results[0].boxes
         if boxes is None or len(boxes) == 0:
+            self._note_luma(frame.camera_id, img, found=False)
             self.stats["processed"] += 1
             return
+        self._note_luma(frame.camera_id, img, found=True)
 
         detections: list[VehicleDetection] = []
         for box in boxes:
             cls_id = int(box.cls.item())
             name = config.VEHICLE_CLASSES.get(cls_id, "person" if cls_id == 0 else str(cls_id))
             conf = float(box.conf.item())
             x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
             crop = img[max(y1, 0):y2, max(x1, 0):x2]
 
             det = VehicleDetection(
@@ -402,20 +458,67 @@ class InferenceEngine:
 
         if detections and self.on_vehicles_present:
             self.on_vehicles_present(frame.camera_id)
 
         for det in detections:
             self.on_detection(det)
 
         self.stats["processed"] += 1
         self.stats["infer_ms"] = round((time.time() - t0) * 1000, 1)
 
+    # -- dark feeds ----------------------------------------------------------
+    def _dark_gate(self, camera_id: str) -> bool:
+        """False while this camera is dark and not due for its probe frame.
+
+        A camera marked dark is never abandoned: one frame in every
+        DARK_RECHECK_FRAMES goes through the full pass, so dawn, a restored
+        illuminator or an uncovered lens brings it back with no operator
+        action. Recovery is decided by that frame's own result, in _note_luma.
+        """
+        if camera_id not in self._dark_cameras:
+            return True
+        seen = self._dark_skipped.get(camera_id, 0) + 1
+        if seen < DARK_RECHECK_FRAMES:
+            self._dark_skipped[camera_id] = seen
+            self.stats["dark_frames_skipped"] += 1
+            return False
+        self._dark_skipped[camera_id] = 0
+        return True
+
+    def _note_luma(self, camera_id: str, img, found: bool) -> None:
+        """Track the dark-frame streak for one camera.
+
+        Darkness alone is not enough to stop looking: a genuinely dark scene
+        that still yields detections is a camera doing its job. Only frames
+        that are both dark *and* empty count towards the streak, and either
+        condition failing clears it and restores the camera.
+        """
+        if not found and mean_luma(img) < DARK_LUMA_THRESHOLD:
+            streak = self._dark_streak.get(camera_id, 0) + 1
+            self._dark_streak[camera_id] = streak
+            if streak >= DARK_FRAME_LIMIT and camera_id not in self._dark_cameras:
+                self._dark_cameras[camera_id] = time.time()
+                self._dark_skipped[camera_id] = 0
+                log.warning("%s has produced %d dark, empty frames - skipping "
+                            "inference, re-testing every %d frames",
+                            camera_id, streak, DARK_RECHECK_FRAMES)
+        else:
+            self._dark_streak.pop(camera_id, None)
+            if self._dark_cameras.pop(camera_id, None) is not None:
+                self._dark_skipped.pop(camera_id, None)
+                log.info("%s is no longer dark - resuming inference", camera_id)
+        self.stats["dark_cameras"] = len(self._dark_cameras)
+
+    def dark_cameras(self) -> list[str]:
+        """Cameras currently being skipped, for pipeline status."""
+        return sorted(self._dark_cameras)
+
     def _vote_plates(self, frame, tracker, detections: list) -> None:
         """Fold this frame's plate reads into each track's running vote."""
         voter = self._plate_voters.get(frame.camera_id)
         if voter is None:
             from netra.analytics.plate_vote import PlateVoter
             voter = self._plate_voters[frame.camera_id] = PlateVoter()
 
         for det in detections:
             if det.track_id is None:
                 continue
@@ -499,10 +602,66 @@ def _run_ocr(reader, crop) -> tuple[str | None, float | None]:
 
     results = reader.readtext(grey, allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                               detail=1, paragraph=False)
     if not results:
         return None, None
     best = max(results, key=lambda r: r[2])
     text = "".join(ch for ch in best[1].upper() if ch.isalnum())
     if len(text) < 4:
         return None, None
     return text, float(best[2])
+
+
+def _self_check() -> None:
+    """Check the dark-feed short-circuit without loading a model or a GPU."""
+    engine = InferenceEngine.__new__(InferenceEngine)  # no models, no threads
+    engine._dark_streak, engine._dark_cameras, engine._dark_skipped = {}, {}, {}
+    engine.stats = {"dark_cameras": 0, "dark_frames_skipped": 0}
+
+    black = np.zeros((240, 320, 3), dtype=np.uint8)
+    lit = np.full((240, 320, 3), 90, dtype=np.uint8)
+    assert mean_luma(black) == 0.0
+    assert mean_luma(lit) > DARK_LUMA_THRESHOLD
+    assert mean_luma(None) == 0.0
+
+    # Dark but not yet decided: one frame short of the limit still runs.
+    for _ in range(DARK_FRAME_LIMIT - 1):
+        engine._note_luma("CAM1", black, found=False)
+    assert engine._dark_cameras == {}, engine._dark_streak
+    assert engine._dark_gate("CAM1") is True
+
+    engine._note_luma("CAM1", black, found=False)
+    assert "CAM1" in engine._dark_cameras
+    assert engine.dark_cameras() == ["CAM1"]
+    assert engine.stats["dark_cameras"] == 1
+
+    # Skipped until the probe frame, then one frame goes through.
+    for _ in range(DARK_RECHECK_FRAMES - 1):
+        assert engine._dark_gate("CAM1") is False
+    assert engine.stats["dark_frames_skipped"] == DARK_RECHECK_FRAMES - 1
+    assert engine._dark_gate("CAM1") is True          # the probe
+    assert engine._dark_gate("CAM1") is False         # back to skipping
+
+    # A probe that finds light must restore the camera by itself.
+    engine._note_luma("CAM1", lit, found=False)
+    assert engine._dark_cameras == {} and engine._dark_streak == {}
+    assert engine.stats["dark_cameras"] == 0
+
+    # A dark scene that still yields detections is a camera doing its job and
+    # must never be short-circuited, however long it stays dark.
+    for _ in range(DARK_FRAME_LIMIT * 2):
+        engine._note_luma("CAM2", black, found=True)
+    assert "CAM2" not in engine._dark_cameras
+    assert engine._dark_gate("CAM2") is True
+
+    # A streak broken before the limit starts again from zero.
+    for _ in range(DARK_FRAME_LIMIT - 1):
+        engine._note_luma("CAM3", black, found=False)
+    engine._note_luma("CAM3", black, found=True)
+    engine._note_luma("CAM3", black, found=False)
+    assert engine._dark_streak["CAM3"] == 1 and "CAM3" not in engine._dark_cameras
+
+    print("inference self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/analytics/matching.py b/netra/analytics/matching.py
index 1981937..8e8dc5e 100644
--- a/netra/analytics/matching.py
+++ b/netra/analytics/matching.py
@@ -76,20 +76,109 @@ def plate_similarity(observed: str, target: str) -> tuple[float, str]:
         return 0.0, "too few characters recovered to constrain"
     agree = sum(1 for a, b in zip(obs, tgt) if a == b)
     frac = agree / span
     if frac < 0.6:
         return 0.0, f"plate mismatch ({agree}/{span} characters)"
     # 60% agreement scores 0; full agreement over a short read approaches 0.9.
     score = 0.9 * (frac - 0.6) / 0.4
     return score, f"{agree}/{span} characters agree ({obs} vs {tgt})"
 
 
+# --- watchlist prefilter -----------------------------------------------------
+# `score_match` is cheap, but a watchlist of 10,000 entries scored against
+# every detection at thousands of detections per minute is tens of millions of
+# comparisons per minute, on the thread that also has to persist detections.
+# The prefilter's only job is to drop entries that cannot possibly match, so
+# full scoring still decides every candidate and the matching behaviour
+# downstream is unchanged.
+INDEX_WINDOW = 4
+
+
+def plate_windows(plate: str | None) -> set[str]:
+    """Every INDEX_WINDOW-character window of the confusion-folded plate.
+
+    Windows, not a prefix: `plate_similarity` matches an observed read that is
+    a *substring* of the watchlist plate, so "AB1234" must find "GJ01AB1234"
+    even though their first four characters have nothing in common. Folding
+    first is equally load-bearing - comparison happens on folded text, so an
+    index built on raw text would miss every OCR confusion the matcher is
+    specifically designed to absorb.
+    """
+    folded = normalise_plate(plate)
+    if len(folded) < INDEX_WINDOW:
+        return set()
+    return {folded[i:i + INDEX_WINDOW]
+            for i in range(len(folded) - INDEX_WINDOW + 1)}
+
+
+class WatchlistIndex:
+    """Entries bucketed by the windows of their plate, for candidate lookup.
+
+    Built once per watchlist reload and thrown away with it; nothing here is
+    incremental, because a rebuild over 10,000 entries is a few milliseconds
+    every thirty seconds.
+
+    ponytail: an entry whose plate folds to fewer than INDEX_WINDOW characters
+    goes in a bucket that is always considered, since no window can be formed
+    from it and `plate_similarity` may still score it positionally. If a
+    watchlist were mostly two-character stubs the prefilter would degrade to
+    the full scan it replaces - which is correct, just not fast.
+    """
+
+    def __init__(self, entries: list[dict] | None = None):
+        self.entries: list[dict] = list(entries or [])
+        self._buckets: dict[str, list[dict]] = {}
+        self._short: list[dict] = []
+        for entry in self.entries:
+            windows = plate_windows(entry.get("plate"))
+            if not windows:
+                self._short.append(entry)
+                continue
+            for key in windows:
+                self._buckets.setdefault(key, []).append(entry)
+
+    def candidates(self, plate_text: str | None) -> list[dict]:
+        """Entries worth scoring against this observed plate.
+
+        A detection is tested against the buckets of every window of its own
+        folded plate, so a partial read matches wherever it sits inside the
+        target. Order is stable so alert ordering does not change with the
+        prefilter's internals.
+        """
+        windows = plate_windows(plate_text)
+        if not windows:
+            # Too little recovered to index on. `plate_similarity` refuses to
+            # score reads this short anyway, but the short bucket is returned
+            # rather than nothing so the filter never invents a rule the
+            # scorer does not have.
+            return list(self._short)
+
+        seen: set[int] = set()
+        out: list[dict] = []
+        for key in windows:
+            for entry in self._buckets.get(key, ()):
+                marker = id(entry)
+                if marker not in seen:
+                    seen.add(marker)
+                    out.append(entry)
+        for entry in self._short:
+            if id(entry) not in seen:
+                out.append(entry)
+        return out
+
+    def stats(self) -> dict:
+        return {"entries": len(self.entries), "buckets": len(self._buckets),
+                "unindexed": len(self._short),
+                "largest_bucket": max((len(v) for v in self._buckets.values()),
+                                      default=0)}
+
+
 def appearance_similarity(det_class: str | None, det_colour: str | None,
                           wl_class: str | None, wl_colour: str | None) -> tuple[float, str]:
     """Score vehicle class and colour agreement.
 
     Deliberately coarse. Colour under sodium and LED street lighting is not
     reliable enough to carry more weight than this, and pretending otherwise
     would manufacture false confidence.
     """
     parts, score, possible = [], 0.0, 0.0
 
@@ -236,15 +325,81 @@ def _self_check() -> None:
                     {"plate": "GJ01AB1234", "vehicle_class": "car",
                      "vehicle_colour": "white"})
     assert not r.is_alert, r
 
     # Space-time veto.
     ok, why = spacetime_plausible(distance_km=300.0, elapsed_s=60)
     assert not ok, why
     ok, why = spacetime_plausible(distance_km=2.0, elapsed_s=180)
     assert ok, why
 
+    # --- watchlist prefilter ------------------------------------------------
+    entries = [{"id": 1, "plate": "GJ01AB1234"},
+               {"id": 2, "plate": "MH12XY9999"},
+               {"id": 3, "plate": "GJ18CD5678"},
+               {"id": 4, "plate": "XY9"}]          # too short to index
+    index = WatchlistIndex(entries)
+
+    # The property the prefilter exists to preserve: a partial read that full
+    # scoring would match must survive it. A naive first-four-characters index
+    # would bucket entry 1 under "GJ01" and never consider it for "AB1234",
+    # silently losing an alert - which is why the index is built on windows.
+    got = {e["id"] for e in index.candidates("AB1234")}
+    assert 1 in got, got
+    assert score_match({"plate_text": "AB1234"}, entries[0]).reasons["plate"]["score"] > 0.5
+
+    # Confusion folding happens before bucketing, so an OCR read of "AB1Z34"
+    # still finds the entry written "AB1234".
+    assert 1 in {e["id"] for e in index.candidates("GJ0IAB1Z34")}
+
+    # An exact read reaches its own entry and skips the unrelated ones.
+    got = {e["id"] for e in index.candidates("GJ01AB1234")}
+    assert got == {1, 4}, got          # 4 is the always-considered short bucket
+    assert 2 not in got and 3 not in got, got
+
+    # A read too short to index still sees the short bucket rather than
+    # nothing, so the prefilter never enforces a rule the scorer does not.
+    assert {e["id"] for e in index.candidates("G1")} == {4}
+
+    # Superset property over realistic degradations: contiguous partial reads
+    # and OCR confusions, which is what this grid actually produces.
+    import random
+    rng = random.Random(7)
+    plates = [f"GJ{rng.randint(1, 38):02d}{chr(65 + rng.randrange(26))}"
+              f"{chr(65 + rng.randrange(26))}{rng.randint(0, 9999):04d}"
+              for _ in range(200)]
+    corpus = [{"id": i, "plate": p} for i, p in enumerate(plates)]
+    big = WatchlistIndex(corpus)
+    scanned = 0
+    for target in plates[:40]:
+        start = rng.randrange(0, len(target) - 4)
+        observed = target[start:start + rng.randint(4, len(target) - start)]
+        observed = "".join(
+            {"0": "O", "1": "I", "5": "S"}.get(ch, ch) for ch in observed)
+        candidates = big.candidates(observed)
+        scanned += len(candidates)
+        keep = {id(e) for e in candidates}
+        for entry in corpus:
+            if score_match({"plate_text": observed}, entry).is_alert:
+                assert id(entry) in keep, (observed, entry)
+    # It must actually filter, or it is a scan with extra steps.
+    assert scanned < 40 * len(corpus) * 0.5, scanned
+
+    # The honest ceiling. `plate_similarity` also scores positional agreement,
+    # and two plates can agree on 70% of their characters with the mismatches
+    # spread out so that they share no 4-character window at all. Such a pair
+    # is not a candidate. It needs three scattered OCR errors in one read -
+    # degraded enough that the fused score rarely clears the alert threshold -
+    # and the alternative, a 2-character index, buckets 10,000 entries into
+    # 1,296 keys and prefilters almost nothing. Pinned here so the trade-off
+    # is visible rather than discovered.
+    missed_obs, missed_tgt = "GJX1AX12X4", "GJ01AB1234"
+    assert plate_similarity(missed_obs, missed_tgt)[0] > 0
+    assert not (plate_windows(missed_obs) & plate_windows(missed_tgt))
+    assert not score_match({"plate_text": missed_obs},
+                           {"plate": missed_tgt}).is_alert
+
     print("matching self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/api/app.py b/netra/api/app.py
index e8bddf2..c2f1862 100644
--- a/netra/api/app.py
+++ b/netra/api/app.py
@@ -766,20 +766,49 @@ def traffic_history(camera_id: str | None = None, limit: int = Query(200, le=100
         if camera_id:
             q = q.filter(TrafficStat.camera_id == camera_id)
         rows = q.order_by(TrafficStat.bucket_start.desc()).limit(limit).all()
         return [{
             "camera_id": r.camera_id, "at": r.bucket_start.isoformat(),
             "total": r.total, "counts_by_class": r.counts_by_class,
             "directions": r.directions, "mean_dwell_s": r.mean_dwell_s,
         } for r in rows]
 
 
+# ---------------------------------------------------------------- storage --
+@app.get("/api/storage")
+def storage(_p=Depends(require("read"))):
+    """What the evidence directory and detections table hold, against budget."""
+    from netra.core import retention
+    return retention.storage_report()
+
+
+@app.post("/api/storage/prune")
+def storage_prune(dry_run: bool = False, _p=Depends(require("manage"))):
+    """Bring evidence and detections back inside their configured budgets.
+
+    Guarded by `manage` and audited: this deletes evidence, and who asked for
+    that deletion is exactly the kind of thing an enquiry later asks about.
+    `dry_run` reports what would go without touching anything.
+    """
+    from netra.core import retention
+    evidence = retention.prune_evidence(dry_run=dry_run)
+    detections = retention.prune_detections(dry_run=dry_run)
+    _audit("storage.prune", detail={"dry_run": dry_run,
+                                    "files_deleted": evidence["deleted"],
+                                    "bytes_freed": evidence["bytes_freed"],
+                                    "rows_deleted": detections["deleted"],
+                                    "retained_protected":
+                                        evidence["retained_protected"]})
+    return {"evidence": evidence, "detections": detections,
+            "storage": retention.storage_report()}
+
+
 @app.get("/api/analytics/cloned-plates")
 def cloned_plates(min_confidence: float = Query(0.6, ge=0.0, le=0.99),
                   limit: int = Query(50, ge=1, le=500)):
     """Registration numbers seen in two places one vehicle could not have reached.
 
     Read-only analysis over stored detections; every finding carries the
     distance, elapsed time and implied speed behind it so an officer can check
     the claim rather than take it on trust.
     """
     from netra.analytics.cloned_plate import find_clones
diff --git a/netra/config.py b/netra/config.py
index 5224f5d..e8e0b1c 100644
--- a/netra/config.py
+++ b/netra/config.py
@@ -102,10 +102,28 @@ TIER2_IMGSZ = int(os.getenv("NETRA_TIER2_IMGSZ", "960"))
 CONF_THRESHOLD = float(os.getenv("NETRA_CONF", "0.20"))
 
 # COCO classes we treat as vehicles
 VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
 
 # --- stream handling ---------------------------------------------------------
 RECONNECT_BASE_S = 2.0
 RECONNECT_MAX_S = 30.0
 # A backwards PTS jump larger than this means the loop restarted, not jitter.
 LOOP_CUT_THRESHOLD_MS = 2000.0
+
+# --- retention ---------------------------------------------------------------
+# Evidence and detections both grow without limit while the pipeline runs: one
+# JPEG and one row per observed vehicle, forever. These are the ceilings the
+# pruner enforces so a long deployment cannot fill the disk or the database.
+# 5 GiB is roughly a fortnight of evidence at the measured crop size on this
+# grid, and leaves room on the smallest edge node we target.
+EVIDENCE_MAX_BYTES = int(os.getenv("NETRA_EVIDENCE_MAX_BYTES", str(5 * 1024**3)))
+# Beyond a week an evidence crop is no longer operationally useful; the
+# detection row and its metadata survive far longer for trend and route work.
+EVIDENCE_MAX_AGE_DAYS = int(os.getenv("NETRA_EVIDENCE_MAX_AGE_DAYS", "7"))
+# Row cap on the detections table. SQLite query plans on the indexed columns
+# stay comfortable to a few million rows; past that the console's time-window
+# queries start to be felt.
+DETECTION_MAX_ROWS = int(os.getenv("NETRA_DETECTION_MAX_ROWS", "2000000"))
+# Floor under the row cap: recent detections are never pruned however far over
+# the cap the table is, because they are what an operator is actively querying.
+DETECTION_KEEP_DAYS = int(os.getenv("NETRA_DETECTION_KEEP_DAYS", "1"))
diff --git a/netra/core/retention.py b/netra/core/retention.py
new file mode 100644
index 0000000..c1fe06a
--- /dev/null
+++ b/netra/core/retention.py
@@ -0,0 +1,414 @@
+"""Retention: keeping two unbounded stores inside a fixed budget.
+
+The pipeline writes one evidence JPEG and one detection row for every vehicle
+it sees. On this grid that is thousands per minute, forever, and nothing in the
+platform previously deleted any of it. A deployment left running therefore ends
+with a full disk or a detections table too large to query - both of which take
+the whole system down rather than degrading it.
+
+Two pruners, with one rule they share: evidence attached to an unacknowledged
+alert or zone event is never deleted, and a detection an alert points at is
+never deleted. That evidence is the reason the alert is actionable - pruning it
+would leave an operator an alert they cannot act on, which is worse than
+keeping the file. Both pruners therefore report what they *retained* by that
+rule alongside what they removed, so the ceiling being hit is visible rather
+than silent.
+
+ponytail: pruning is invoked on demand (an endpoint, or a scheduled call),
+not by a background thread. A thread deleting files while inference runs is one
+more thing competing for I/O with the primary duty, and the operator - or
+cron - knows better than we do when the quiet hour is. The ceiling is that a
+platform nobody ever calls this on still fills its disk.
+"""
+from __future__ import annotations
+
+import logging
+import os
+from datetime import datetime, timedelta, timezone
+from pathlib import Path
+
+from netra import config
+
+log = logging.getLogger(__name__)
+
+
+def _session_factory():
+    """Resolved lazily so a self-check can substitute a throwaway database."""
+    from netra.core.db import SessionLocal
+    return SessionLocal
+
+
+def _basename(url_path: str | None) -> str | None:
+    """Evidence is stored as the URL path `/evidence/<file>`; files are not."""
+    if not url_path:
+        return None
+    return url_path.rsplit("/", 1)[-1]
+
+
+def protected_evidence(session_factory=None) -> set[str]:
+    """Evidence filenames that must survive any prune.
+
+    An alert or zone event an operator has not yet acknowledged is still open
+    police work; its picture is the evidence.
+    """
+    from netra.core.models import Alert, Detection, ZoneEventRow
+
+    sf = session_factory or _session_factory()
+    keep: set[str] = set()
+    with sf() as db:
+        rows = (db.query(Detection.evidence_path)
+                .join(Alert, Alert.detection_id == Detection.id)
+                .filter(Alert.acknowledged.is_(False))
+                .filter(Detection.evidence_path.isnot(None)).all())
+        keep.update(n for n in (_basename(r[0]) for r in rows) if n)
+
+        rows = (db.query(ZoneEventRow.evidence_path)
+                .filter(ZoneEventRow.acknowledged.is_(False))
+                .filter(ZoneEventRow.evidence_path.isnot(None)).all())
+        keep.update(n for n in (_basename(r[0]) for r in rows) if n)
+    return keep
+
+
+def prune_evidence(max_bytes: int | None = None, max_age_days: int | None = None,
+                   evidence_dir: Path | None = None,
+                   session_factory=None, dry_run: bool = False) -> dict:
+    """Delete evidence files oldest-first until inside the age and size budget.
+
+    Age is applied first, then the size budget, because an expired file is
+    worthless regardless of how much room is left. Files referenced by an
+    unacknowledged alert or zone event are skipped by both rules and counted
+    separately.
+    """
+    max_bytes = config.EVIDENCE_MAX_BYTES if max_bytes is None else max_bytes
+    max_age_days = (config.EVIDENCE_MAX_AGE_DAYS if max_age_days is None
+                    else max_age_days)
+    directory = Path(evidence_dir) if evidence_dir else config.EVIDENCE
+
+    keep = protected_evidence(session_factory)
+
+    # (mtime, size, path) oldest first. Statting every file is the whole cost
+    # of this pass; at the 5 GiB ceiling that is a few tens of thousands of
+    # entries, which is a fraction of a second.
+    files: list[tuple[float, int, Path]] = []
+    total = 0
+    for entry in os.scandir(directory) if directory.exists() else []:
+        if not entry.is_file():
+            continue
+        try:
+            st = entry.stat()
+        except OSError:
+            continue
+        files.append((st.st_mtime, st.st_size, Path(entry.path)))
+        total += st.st_size
+    files.sort()
+
+    report = {
+        "scanned": len(files), "bytes_before": total,
+        "deleted_expired": 0, "deleted_over_budget": 0,
+        "bytes_freed": 0, "retained_protected": 0,
+        "retained_protected_bytes": 0, "failed": 0,
+        "max_bytes": max_bytes, "max_age_days": max_age_days,
+        "dry_run": dry_run,
+    }
+
+    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).timestamp()
+    remaining: list[tuple[float, int, Path]] = []
+
+    def _remove(item, reason: str) -> None:
+        mtime, size, path = item
+        if path.name in keep:
+            report["retained_protected"] += 1
+            report["retained_protected_bytes"] += size
+            remaining.append(item)
+            return
+        if not dry_run:
+            try:
+                path.unlink()
+            except OSError:
+                report["failed"] += 1
+                remaining.append(item)
+                return
+        report[reason] += 1
+        report["bytes_freed"] += size
+
+    for item in files:
+        if item[0] < cutoff:
+            _remove(item, "deleted_expired")
+        else:
+            remaining.append(item)
+
+    # Size budget over what age did not already take, still oldest-first.
+    live = total - report["bytes_freed"]
+    if live > max_bytes:
+        over_budget, remaining = remaining, []
+        for item in over_budget:
+            if live <= max_bytes:
+                remaining.append(item)
+                continue
+            before = report["bytes_freed"]
+            _remove(item, "deleted_over_budget")
+            live -= report["bytes_freed"] - before
+
+    report["bytes_after"] = total - report["bytes_freed"]
+    report["deleted"] = report["deleted_expired"] + report["deleted_over_budget"]
+    if report["deleted"] or report["retained_protected"]:
+        log.info("evidence prune: removed %d files (%.1f MiB), retained %d "
+                 "attached to open alerts", report["deleted"],
+                 report["bytes_freed"] / 1024**2, report["retained_protected"])
+    return report
+
+
+def prune_detections(max_rows: int | None = None, keep_days: int | None = None,
+                     session_factory=None, dry_run: bool = False) -> dict:
+    """Delete the oldest detections beyond the row cap.
+
+    Two things are never deleted: a detection any alert points at (the alert's
+    foreign key would dangle, and the alert would lose the sighting it was
+    raised on), and anything inside `keep_days`, which is the window an
+    operator is actively querying.
+    """
+    from netra.core.models import Alert, Detection
+
+    max_rows = config.DETECTION_MAX_ROWS if max_rows is None else max_rows
+    keep_days = config.DETECTION_KEEP_DAYS if keep_days is None else keep_days
+    sf = session_factory or _session_factory()
+    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
+
+    report = {"rows_before": 0, "deleted": 0, "retained_alerted": 0,
+              "retained_recent": 0, "max_rows": max_rows,
+              "keep_days": keep_days, "dry_run": dry_run}
+
+    with sf() as db:
+        total = db.query(Detection.id).count()
+        report["rows_before"] = total
+        excess = total - max_rows
+        if excess <= 0:
+            report["rows_after"] = total
+            return report
+
+        # Oldest-first, take only as many as the cap demands. The two
+        # protections are applied as filters rather than after selection, so a
+        # table that is entirely alert-referenced simply deletes nothing
+        # instead of looping.
+        alerted = db.query(Alert.detection_id).distinct().subquery()
+        candidates = (db.query(Detection.id)
+                      .filter(Detection.wall_time < cutoff)
+                      .filter(Detection.id.notin_(db.query(alerted.c.detection_id)))
+                      .order_by(Detection.wall_time.asc())
+                      .limit(excess).all())
+        ids = [row[0] for row in candidates]
+        report["retained_alerted"] = db.query(Alert.detection_id).distinct().count()
+        report["retained_recent"] = (db.query(Detection.id)
+                                     .filter(Detection.wall_time >= cutoff).count())
+
+        if ids and not dry_run:
+            db.query(Detection).filter(Detection.id.in_(ids)).delete(
+                synchronize_session=False)
+            db.commit()
+        report["deleted"] = len(ids)
+        report["rows_after"] = total - report["deleted"]
+        report["still_over_cap"] = max(0, report["rows_after"] - max_rows)
+
+    if report["deleted"]:
+        log.info("detection prune: removed %d rows, %d retained by an alert",
+                 report["deleted"], report["retained_alerted"])
+    return report
+
+
+def storage_report(evidence_dir: Path | None = None, session_factory=None) -> dict:
+    """What the two stores currently hold, against what they are allowed to."""
+    from netra.core.models import Alert, Detection, ZoneEventRow
+
+    directory = Path(evidence_dir) if evidence_dir else config.EVIDENCE
+    count = 0
+    total = 0
+    for entry in os.scandir(directory) if directory.exists() else []:
+        if entry.is_file():
+            try:
+                total += entry.stat().st_size
+            except OSError:
+                continue
+            count += 1
+
+    sf = session_factory or _session_factory()
+    with sf() as db:
+        detections = db.query(Detection.id).count()
+        alerts = db.query(Alert.id).count()
+        open_alerts = db.query(Alert.id).filter(Alert.acknowledged.is_(False)).count()
+        open_zone = (db.query(ZoneEventRow.id)
+                     .filter(ZoneEventRow.acknowledged.is_(False)).count())
+
+    return {
+        "evidence": {
+            "files": count,
+            "bytes": total,
+            "mib": round(total / 1024**2, 1),
+            "max_bytes": config.EVIDENCE_MAX_BYTES,
+            "max_age_days": config.EVIDENCE_MAX_AGE_DAYS,
+            "percent_of_budget": round(
+                100.0 * total / config.EVIDENCE_MAX_BYTES, 1)
+            if config.EVIDENCE_MAX_BYTES else None,
+        },
+        "detections": {
+            "rows": detections,
+            "max_rows": config.DETECTION_MAX_ROWS,
+            "keep_days": config.DETECTION_KEEP_DAYS,
+            "percent_of_cap": round(
+                100.0 * detections / config.DETECTION_MAX_ROWS, 1)
+            if config.DETECTION_MAX_ROWS else None,
+        },
+        "alerts": {"rows": alerts, "unacknowledged": open_alerts},
+        "zone_events": {"unacknowledged": open_zone},
+    }
+
+
+def _self_check() -> None:
+    """Exercise both pruners against a temporary directory and database.
+
+    Deliberately never touches data/netra.db or data/evidence: this runs on a
+    developer's machine with a live pipeline's evidence sitting on disk, and a
+    self-check that deletes real evidence would be worse than no self-check.
+    """
+    import tempfile
+
+    from sqlalchemy import create_engine
+    from sqlalchemy.orm import sessionmaker
+
+    from netra.core.db import Base
+    from netra.core import models  # noqa: F401  (registers the mappers)
+
+    with tempfile.TemporaryDirectory() as tmp:
+        root = Path(tmp)
+        evidence = root / "evidence"
+        evidence.mkdir()
+        engine = create_engine(f"sqlite:///{root / 'check.db'}")
+        Base.metadata.create_all(engine)
+        sf = sessionmaker(bind=engine, expire_on_commit=False)
+        try:
+            _check_body(evidence, sf)
+        finally:
+            # Windows will not remove the temporary directory while SQLite
+            # still holds the file open, which would mask the real failure.
+            engine.dispose()
+
+    print("retention self-check passed")
+
+
+def _check_body(evidence, sf) -> None:
+        """The body of the self-check, over a temporary directory and database."""
+        from netra.core.models import Alert, Camera, Detection, ZoneEventRow
+
+        now = datetime.now(timezone.utc)
+        old = now - timedelta(days=30)
+
+        def write(name: str, size: int, age_days: float) -> Path:
+            path = evidence / name
+            path.write_bytes(b"\0" * size)
+            stamp = (now - timedelta(days=age_days)).timestamp()
+            os.utime(path, (stamp, stamp))
+            return path
+
+        with sf() as db:
+            db.add(Camera(id="CAM1", name="check"))
+            db.flush()
+            # d1 is old and attached to an OPEN alert - must survive both
+            # pruners. d2 is old and attached to an ACKNOWLEDGED alert - its
+            # file may go, but the row may not (the alert's key points at it).
+            # d3 is old and unreferenced - the only row eligible for deletion.
+            for i, (path, when) in enumerate(
+                    [("/evidence/open.jpg", old), ("/evidence/ack.jpg", old),
+                     ("/evidence/plain.jpg", old)], start=1):
+                db.add(Detection(id=i, camera_id="CAM1", pts_ms=0.0,
+                                 wall_time=when, vehicle_class="car",
+                                 confidence=0.9, bbox=[0, 0, 10, 10],
+                                 evidence_path=path))
+            db.add(Alert(detection_id=1, watchlist_id=1, camera_id="CAM1",
+                         score=0.9, match_type="exact", reasons={},
+                         acknowledged=False))
+            db.add(Alert(detection_id=2, watchlist_id=1, camera_id="CAM1",
+                         score=0.9, match_type="exact", reasons={},
+                         acknowledged=True))
+            db.add(ZoneEventRow(zone_rule_id=1, camera_id="CAM1",
+                                rule="intrusion", detail="check",
+                                evidence_path="/evidence/zone_open.jpg",
+                                acknowledged=False))
+            db.add(ZoneEventRow(zone_rule_id=1, camera_id="CAM1",
+                                rule="intrusion", detail="check",
+                                evidence_path="/evidence/zone_ack.jpg",
+                                acknowledged=True))
+            db.commit()
+
+        for name in ("open.jpg", "ack.jpg", "plain.jpg",
+                     "zone_open.jpg", "zone_ack.jpg"):
+            write(name, 1000, age_days=30)
+
+        keep = protected_evidence(sf)
+        assert keep == {"open.jpg", "zone_open.jpg"}, keep
+
+        # --- age rule, with the protection in force -------------------------
+        r = prune_evidence(max_bytes=10**9, max_age_days=7,
+                           evidence_dir=evidence, session_factory=sf)
+        assert r["deleted_expired"] == 3, r
+        assert r["retained_protected"] == 2, r
+        assert r["retained_protected_bytes"] == 2000, r
+        survivors = {p.name for p in evidence.iterdir()}
+        assert survivors == {"open.jpg", "zone_open.jpg"}, survivors
+
+        # --- size budget, oldest first --------------------------------------
+        for i in range(5):
+            write(f"recent{i}.jpg", 1000, age_days=i)  # recent0 newest
+        # Seven 1000-byte files against a 4500-byte budget. The two protected
+        # ones are the oldest, so they are visited first and free nothing;
+        # three unprotected files then go before the budget is met.
+        r = prune_evidence(max_bytes=4500, max_age_days=365,
+                           evidence_dir=evidence, session_factory=sf)
+        left = {p.name for p in evidence.iterdir()}
+        # Protected files count against the budget but cannot be removed, so
+        # the budget is honoured only as far as the protection allows.
+        assert "open.jpg" in left and "zone_open.jpg" in left, left
+        assert "recent4.jpg" not in left, left   # oldest unprotected went first
+        assert "recent0.jpg" in left, left       # newest survived
+        assert r["deleted_over_budget"] == 3, r
+        assert r["retained_protected"] == 2, r
+
+        # A dry run must report the same intent without touching the disk.
+        before = sorted(p.name for p in evidence.iterdir())
+        r = prune_evidence(max_bytes=0, max_age_days=365, evidence_dir=evidence,
+                           session_factory=sf, dry_run=True)
+        assert sorted(p.name for p in evidence.iterdir()) == before
+        assert r["deleted"] >= 1 and r["retained_protected"] == 2, r
+
+        # --- detection rows -------------------------------------------------
+        # Cap of 1 against 3 rows: two are alert-referenced and must survive,
+        # so exactly one row goes and the table stays above its cap. Reporting
+        # that honestly matters more than forcing the cap.
+        r = prune_detections(max_rows=1, keep_days=1, session_factory=sf)
+        assert r["deleted"] == 1, r
+        assert r["retained_alerted"] == 2, r
+        assert r["still_over_cap"] == 1, r
+        with sf() as db:
+            left_ids = sorted(i for (i,) in db.query(Detection.id).all())
+        assert left_ids == [1, 2], left_ids
+
+        # Nothing left to take: the remaining rows are all alert-referenced.
+        r = prune_detections(max_rows=0, keep_days=1, session_factory=sf)
+        assert r["deleted"] == 0, r
+
+        # keep_days protects recent rows even far over the cap.
+        with sf() as db:
+            db.add(Detection(id=99, camera_id="CAM1", pts_ms=0.0,
+                             wall_time=now, vehicle_class="car",
+                             confidence=0.5, bbox=[0, 0, 1, 1]))
+            db.commit()
+        r = prune_detections(max_rows=0, keep_days=7, session_factory=sf)
+        assert r["deleted"] == 0 and r["retained_recent"] == 1, r
+
+        rep = storage_report(evidence_dir=evidence, session_factory=sf)
+        assert rep["detections"]["rows"] == 3, rep
+        assert rep["alerts"]["unacknowledged"] == 1, rep
+        assert rep["zone_events"]["unacknowledged"] == 1, rep
+        assert rep["evidence"]["files"] == len(list(evidence.iterdir())), rep
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/pipeline.py b/netra/pipeline.py
index 5c2fae6..b25af66 100644
--- a/netra/pipeline.py
+++ b/netra/pipeline.py
@@ -8,21 +8,21 @@ from __future__ import annotations
 import logging
 import queue
 import threading
 import time
 from datetime import datetime, timezone
 
 import cv2
 
 from netra import config
 from netra.analytics.inference import InferenceEngine
-from netra.analytics.matching import score_match
+from netra.analytics.matching import WatchlistIndex, score_match
 from netra.core.db import SessionLocal
 from netra.core.models import (Alert, Camera, Detection, TrafficStat,
                                WatchlistEntry, ZoneEventRow, ZoneRule)
 from netra.core.notify import NOTIFIER
 from netra.ingest.stream import IngestSupervisor
 
 log = logging.getLogger(__name__)
 
 #: Detections are persisted in batches rather than one transaction each.
 WRITE_BATCH_SIZE = 50
@@ -36,20 +36,21 @@ class Pipeline:
             on_vehicles_present=self._handle_vehicles_present)
         self.supervisor = IngestSupervisor(
             sink=self.engine.submit,
             on_discontinuity=self._handle_discontinuity)
 
         # Alerts are pushed to connected consoles from here.
         self.alert_subscribers: list[queue.Queue] = []
         self._lock = threading.Lock()
 
         self._watchlist_cache: list[dict] = []
+        self._watchlist_index = WatchlistIndex([])
         self._watchlist_loaded_at = 0.0
         self.running = False
         self.started_at: datetime | None = None
 
         # Persistence runs off the inference thread.
         self._write_queue: queue.Queue = queue.Queue(maxsize=4000)
         self._stop_writer = threading.Event()
         self._writer: threading.Thread | None = None
         self.stats = {"written": 0, "write_dropped": 0, "zone_events": 0,
                       "traffic_buckets": 0}
@@ -287,31 +288,42 @@ class Pipeline:
         if time.time() - self._watchlist_loaded_at > 30:
             with SessionLocal() as db:
                 entries = db.query(WatchlistEntry).filter(
                     WatchlistEntry.active.is_(True)).all()
                 self._watchlist_cache = [{
                     "id": e.id, "plate": e.plate, "category": e.category,
                     "severity": e.severity, "vehicle_class": e.vehicle_class,
                     "vehicle_colour": e.vehicle_colour, "case_ref": e.case_ref,
                     "owner_name": e.owner_name, "source_db": e.source_db,
                 } for e in entries]
+            # Rebuilt with the cache, never separately: an index describing a
+            # watchlist that has already changed would silently stop
+            # considering entries that were just added.
+            self._watchlist_index = WatchlistIndex(self._watchlist_cache)
             self._watchlist_loaded_at = time.time()
         return self._watchlist_cache
 
     def _check_watchlist(self, detection_id: int, det) -> None:
         candidate = {
             "plate_text": det.plate_text,
             "plate_chars": det.plate_chars,
             "vehicle_class": det.vehicle_class,
             "colour": det.colour,
         }
-        for entry in self._watchlist():
+        # Refresh the cache, then score only the entries whose plate shares a
+        # character window with this read. Full scoring still decides each
+        # candidate, so partial and confusion-folded matching is unchanged;
+        # this only avoids scoring entries that could not match. At 10,000
+        # entries that is the difference between 10,000 comparisons per
+        # detection and a few dozen, on the thread that also persists rows.
+        self._watchlist()
+        for entry in self._watchlist_index.candidates(det.plate_text):
             result = score_match(candidate, entry)
             if not result.is_alert:
                 continue
             self._raise_alert(detection_id, det, entry, result)
 
     def _raise_alert(self, detection_id: int, det, entry: dict, result) -> None:
         with SessionLocal() as db:
             alert = Alert(
                 detection_id=detection_id,
                 watchlist_id=entry["id"],
@@ -369,16 +381,21 @@ class Pipeline:
     def status(self) -> dict:
         return {
             "running": self.running,
             "started_at": self.started_at.isoformat() if self.started_at else None,
             "inference": self.engine.stats,
             "queue_depth": self.engine.queue.qsize(),
             "write_queue_depth": self._write_queue.qsize(),
             "scheduling": self.supervisor.scheduling(),
             "traffic": self.engine.trackers.stats(),
             "zone_events": self.stats["zone_events"],
+            "watchlist_index": self._watchlist_index.stats(),
+            # Cameras the engine has stopped inferring on because their feed
+            # went black. Surfaced rather than silent: a control room must be
+            # able to see that a camera is no longer being looked at.
+            "dark_cameras": self.engine.dark_cameras(),
             "persistence": self.stats,
             "cameras": self.supervisor.health(),
         }
 
 
 PIPELINE = Pipeline()
