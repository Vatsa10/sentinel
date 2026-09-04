# Re-review — Task 2 fix round 1

e191cb2 Partition clone candidates by session before pairing

diff --git a/netra/analytics/cloned_plate.py b/netra/analytics/cloned_plate.py
index 6ddb59e..0712850 100644
--- a/netra/analytics/cloned_plate.py
+++ b/netra/analytics/cloned_plate.py
@@ -31,20 +31,27 @@ from netra.analytics.matching import (MAX_PLAUSIBLE_KMH, normalise_plate,
 from netra.core.geo import haversine_km, time_group
 from netra.core.timing import sighting_time
 
 # Plate confidence assumed when a detection carries none. Deliberately middling:
 # an unscored read must neither inflate nor destroy a finding.
 DEFAULT_PLATE_CONF = 0.5
 
 # A finding can never be certain - see the module docstring.
 MAX_CONFIDENCE = 0.99
 
+# Violation credited when the two sightings carry the same timestamp. Scene time
+# is OCR of an overlay clock at second resolution, so a genuine sub-second gap is
+# routinely stamped as 0s and no speed can be computed at all. Held below the
+# strongest measured violations deliberately: a finding whose arithmetic cannot
+# be shown must not outrank one whose arithmetic can.
+ZERO_ELAPSED_VIOLATION = 0.9
+
 
 @dataclass
 class CloneFinding:
     plate: str
     sighting_a: dict
     sighting_b: dict
     distance_km: float
     elapsed_s: float
     implied_kmh: float | None
     confidence: float
@@ -65,79 +72,89 @@ def _sighting_dict(det) -> dict:
         "lon": cam.lon if cam else None,
         "at": at.isoformat() if isinstance(at, datetime) else at,
     }
 
 
 def _confidence(implied_kmh: float | None, conf_a: float, conf_b: float) -> float:
     """How strongly this pair argues that two vehicles share one plate.
 
     Two independent factors:
 
-      * How badly the pair violates plausibility. A pair implying 200 km/h is
+      * How badly the pair violates plausibility, where it can be measured. A pair implying 200 km/h is
         weak - a motorway run, a slightly wrong timestamp or an approximate
         camera coordinate could all produce it. One implying 5,000 km/h has no
         innocent explanation. Scored as 1 - (limit / implied), which approaches
         1 as the implied speed runs away and is near 0 just over the limit.
       * How well the plate was read at both ends. The weaker read governs: a
         confident read paired with a guess is still a guess.
     """
-    if implied_kmh is None or implied_kmh <= MAX_PLAUSIBLE_KMH:
-        # Simultaneous sightings at separated cameras imply infinite speed.
-        violation = 1.0 if implied_kmh is None else 0.0
+    if implied_kmh is None:
+        # Sightings stamped at the same second: the gap is below the clock's
+        # resolution, so the implied speed is unbounded but unmeasured.
+        violation = ZERO_ELAPSED_VIOLATION
+    elif implied_kmh <= MAX_PLAUSIBLE_KMH:
+        violation = 0.0
     else:
         violation = 1.0 - (MAX_PLAUSIBLE_KMH / implied_kmh)
 
     weakest = min(conf_a, conf_b)
     # Plate quality can halve the score but never zero it: even a poor read of
     # the same string in two impossible places is worth an officer's attention.
     return round(min(MAX_CONFIDENCE, violation * (0.5 + 0.5 * weakest)), 3)
 
 
 def find_clones(detections: list, min_confidence: float = 0.6) -> list[CloneFinding]:
     """Report registration numbers seen in physically incompatible places.
 
     `detections` are ORM Detection rows with `.camera` loaded.
 
-    ponytail: consecutive pairs only, after ordering by time. A clone active
-    across three cameras is reported as its adjacent impossible hops rather
-    than as one multi-camera cluster; the ceiling is that the officer reads two
-    findings instead of one, not that anything is missed.
+    ponytail: within each session, consecutive pairs only after ordering by
+    time. Every comparable adjacent pair is examined, so a clone active across
+    three cameras is reported as its two adjacent impossible hops rather than
+    as one multi-camera cluster. What this does not do is compare
+    non-consecutive sightings: a pair that is impossible but has a plausible
+    sighting between them is not reported, since the intervening hop is the
+    stronger explanation and reporting the outer pair would double-count it.
     """
-    groups: dict[str, list] = {}
+    # Partition by (plate, recording session) before ordering. Sightings from
+    # different sessions are not comparable, so they must not merely be skipped
+    # when they fall adjacent - a cross-session row sorting between two
+    # same-session sightings would otherwise break the chain and hide a real
+    # clone, and the two sandbox sessions have overlapping wall clocks, so that
+    # interleaving is expected rather than hypothetical. Cameras in no known
+    # session are dropped entirely: we cannot show their clock agrees with
+    # anything, including another unlisted camera's.
+    groups: dict[tuple[str, str], list] = {}
     for det in detections:
         plate = normalise_plate(det.plate_text)
         # A partial read cannot identify a vehicle, so it cannot evidence a
         # clone either: "AB12" is shared by thousands of legitimate plates.
         if len(plate) < 6:
             continue
         if sighting_time(det) is None:
             continue
-        groups.setdefault(plate, []).append(det)
+        group = time_group(det.camera_id)
+        if group is None:
+            continue
+        groups.setdefault((plate, group), []).append(det)
 
     findings: list[CloneFinding] = []
-    for plate, dets in groups.items():
+    for (plate, group), dets in groups.items():
         if len(dets) < 2:
             continue
         dets.sort(key=sighting_time)
 
         for prev, cur in zip(dets, dets[1:]):
             # One vehicle passing the same camera twice is not a clone.
             if prev.camera_id == cur.camera_id:
                 continue
 
-            # Different recording sessions are not simultaneous in reality.
-            # Unknown group (None) is also not comparable - we cannot show the
-            # two clocks agree, so we must not claim the speed between them.
-            group = time_group(prev.camera_id)
-            if group is None or group != time_group(cur.camera_id):
-                continue
-
             cam_a, cam_b = getattr(prev, "camera", None), getattr(cur, "camera", None)
             coords = (getattr(cam_a, "lat", None), getattr(cam_a, "lon", None),
                       getattr(cam_b, "lat", None), getattr(cam_b, "lon", None))
             if None in coords:
                 # Without both positions there is no distance and therefore no
                 # impossibility to assert.
                 continue
 
             km = haversine_km(*coords)
             secs = (sighting_time(cur) - sighting_time(prev)).total_seconds()
@@ -159,22 +176,25 @@ def find_clones(detections: list, min_confidence: float = 0.6) -> list[CloneFind
             implied = km / (secs / 3600.0) if secs > 0 else None
             conf = _confidence(implied,
                                prev.plate_conf if prev.plate_conf is not None else DEFAULT_PLATE_CONF,
                                cur.plate_conf if cur.plate_conf is not None else DEFAULT_PLATE_CONF)
             if conf < min_confidence:
                 continue
 
             a, b = _sighting_dict(prev), _sighting_dict(cur)
             if implied is None:
                 arithmetic = (f"{shown} was recorded at {a['camera_name']} and "
-                              f"{b['camera_name']}, {km:.1f} km apart, with no "
-                              f"time between the two sightings")
+                              f"{b['camera_name']}, {km:.1f} km apart, both "
+                              f"stamped at the same second - the gap is below "
+                              f"the overlay clock's resolution, so the implied "
+                              f"speed could not be computed, only bounded below "
+                              f"by {km * 3600:.0f} km/h")
             else:
                 arithmetic = (f"{shown} was recorded at {a['camera_name']} and "
                               f"{b['camera_name']}, {km:.1f} km apart, "
                               f"{secs:.0f}s apart - implying {implied:.0f} km/h "
                               f"against a {MAX_PLAUSIBLE_KMH:.0f} km/h ceiling")
             reason = (f"{arithmetic}. Both cameras share the {group} recording "
                       f"session, so the timestamps are comparable. One vehicle "
                       f"cannot have made this journey, so the plate is likely "
                       f"cloned or forged. Plate reads scored "
                       f"{prev.plate_conf if prev.plate_conf is not None else 'unscored'} "
@@ -274,20 +294,41 @@ def _self_check() -> None:
     assert find_clones([FakeDet(c04, "AB12", t0),
                         FakeDet(c14, "AB12", t0 + timedelta(seconds=2))]) == []
 
     # Three cameras, two impossible hops: both are reported.
     chain = find_clones([FakeDet(c04, "GJ01AB1234", t0),
                          FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=2)),
                          FakeDet(c15, "GJ01AB1234", t0 + timedelta(seconds=4))])
     assert len(chain) == 2, chain
     assert chain[0].confidence >= chain[1].confidence, chain
 
+    # A cross-session sighting sorting between two same-session ones must not
+    # break the chain: partitioning happens before ordering, so the real
+    # cam04 -> cam14 clone is still found with cam10 interleaved.
+    interleaved = find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                               FakeDet(c10, "GJ01AB1234", t0 + timedelta(seconds=1)),
+                               FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=2))],
+                              min_confidence=0.0)
+    assert len(interleaved) == 1, interleaved
+    assert {interleaved[0].sighting_a["camera_id"],
+            interleaved[0].sighting_b["camera_id"]} == {"cam04", "cam14"}, interleaved[0]
+
+    # An unmeasurable gap must not outrank a flagrant measured violation. Scene
+    # time is second-resolution OCR, so a same-second pair is the least
+    # determinate finding available, not the strongest.
+    same_second = find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                               FakeDet(c14, "GJ01AB1234", t0)], min_confidence=0.0)
+    assert len(same_second) == 1, same_second
+    assert same_second[0].implied_kmh is None, same_second[0]
+    assert "resolution" in same_second[0].reason, same_second[0].reason
+    assert same_second[0].confidence < severe[0].confidence, (same_second[0], severe[0])
+
     # Two reads that differ only by a known OCR confusion are the same plate,
     # but the finding must show both as read rather than the folded key.
     folded = find_clones([FakeDet(c04, "GJ01AB1234", t0),
                           FakeDet(c14, "GJ0IAB1234", t0 + timedelta(seconds=2))])
     assert len(folded) == 1 and "6J01A81234" not in folded[0].plate, folded
     assert "GJ0IAB1234" in folded[0].plate, folded[0].plate
 
     # Distinct plates are never cross-compared.
     assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
                         FakeDet(c14, "GJ09ZZ8888", t0 + timedelta(seconds=2))]) == []
