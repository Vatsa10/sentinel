# Re-review — Task 5 fix round 1

5a360a0 Stop the quiet branch calling an ordinary empty minute a blockage

diff --git a/netra/analytics/baseline.py b/netra/analytics/baseline.py
index 8a2853a..34baa07 100644
--- a/netra/analytics/baseline.py
+++ b/netra/analytics/baseline.py
@@ -119,35 +119,58 @@ def _hour_of(row) -> int | None:
         return None
     try:
         from datetime import timezone
         if ts.tzinfo is not None:
             ts = ts.astimezone(timezone.utc)
         return ts.hour
     except AttributeError:
         return None
 
 
+def _is_legacy_cumulative(row, total) -> bool:
+    """True for a row written before `total` became a per-bucket figure.
+
+    Those rows carry a running count spanning every replay of the recording,
+    and a single one poisons the hour it lands in: measured on cam15 at hour
+    18, one legacy row moved the mean from 3.4 to 14.0 and the standard
+    deviation from 1.1 to 26.0, so a genuine ten-fold spike read as normal.
+    They cannot be aged out - nothing prunes traffic_stats and the learner has
+    no time window - so they are excluded here instead.
+
+    They are identifiable because the migration that added `cumulative_total`
+    defaults it to 0, while any row written since carries the real cumulative,
+    which is at least as large as the bucket's own delta. A genuine empty
+    bucket has `total = 0` and is deliberately kept: a road that is normally
+    quiet is exactly what the baseline needs to learn. A source with no
+    `cumulative_total` field at all (a synthetic dict) is trusted as given.
+    """
+    cumulative = _field(row, "cumulative_total")
+    return cumulative is not None and cumulative == 0 and total > 0
+
+
 def learn(rows) -> dict[tuple[str, int], Baseline]:
     """Learn per-(camera, hour) norms from `TrafficStat` rows.
 
     `total` must be the traffic *during* that bucket. A cumulative counter would
     make the learned mean a function of how long the platform has been running
     rather than of how busy the road is, and every judgement drawn from it
     meaningless.
     """
     grouped: dict[tuple[str, int], list[float]] = {}
     for row in rows:
         camera_id = _field(row, "camera_id")
         hour = _hour_of(row)
         total = _field(row, "total")
         if camera_id is None or hour is None or total is None:
             continue
+        if _is_legacy_cumulative(row, total):
+            continue
         grouped.setdefault((camera_id, int(hour)), []).append(float(total))
 
     baselines: dict[tuple[str, int], Baseline] = {}
     for (camera_id, hour), values in grouped.items():
         mean = statistics.fmean(values)
         # Sample standard deviation needs two points; one observation has no
         # dispersion to speak of, and is below MIN_SAMPLES anyway.
         stdev = statistics.stdev(values) if len(values) > 1 else 0.0
         baselines[(camera_id, hour)] = Baseline(
             camera_id=camera_id, hour=hour, mean=mean, stdev=stdev,
@@ -176,25 +199,32 @@ def assess(baseline: Baseline | None, observed: int) -> Assessment:
                 f"at hour {baseline.hour:02d}:00 UTC; "
                 f"{MIN_SAMPLES} are required before this platform will call a "
                 f"reading normal or abnormal. Observed {observed}."))
 
     z = (observed - baseline.mean) / baseline.effective_stdev
     z = round(z, 2)
     norm = (f"the norm for {baseline.camera_id} at hour {baseline.hour:02d}:00 "
             f"UTC is {baseline.mean:.1f} "
             f"(sd {baseline.effective_stdev:.1f}, {baseline.samples} samples)")
 
-    if observed == 0 and baseline.mean >= 1.0:
+    # `quiet` is a more strongly worded `low`, never an override of the bands.
+    # Gating it on the z-score as well as on zero matters because a genuinely
+    # quiet road counts zero routinely: a camera whose history is
+    # (0, 2, 1, 0, 3, 1, 0, 2) has a mean of 1.1, and reporting its next empty
+    # minute as a possible blockage would assert an abnormality on evidence
+    # that says the reading is ordinary - and would do so every few minutes.
+    if observed == 0 and z <= Z_LOW:
         status = "quiet"
-        text = (f"No traffic counted, where {norm}. A road that normally carries "
-                f"vehicles and now carries none may be blocked, closed, or the "
-                f"camera's view obstructed.")
+        text = (f"No traffic counted, {z:+.1f} standard deviations below "
+                f"normal, where {norm}. A road that normally carries vehicles "
+                f"and now carries none may be blocked, closed, or the camera's "
+                f"view obstructed.")
     elif z >= Z_HIGH:
         status = "high"
         text = (f"{observed} vehicles, {z:+.1f} standard deviations above "
                 f"normal: {norm}.")
     elif z >= Z_ELEVATED:
         status = "elevated"
         text = (f"{observed} vehicles, {z:+.1f} standard deviations above "
                 f"normal: {norm}.")
     elif z <= Z_LOW:
         status = "low"
@@ -288,20 +318,64 @@ def _self_check() -> None:
     assert moderate.status in ("elevated", "high"), moderate
 
     # Zero traffic against a busy baseline is quiet, not merely low.
     dead = assess(b[("cam01", 9)], 0)
     assert dead.status == "quiet", dead
     assert "blocked" in dead.explanation
 
     # A genuinely low but non-zero reading is low.
     assert assess(b[("cam01", 9)], 30).status == "low"
 
+    # ...but a road that is *normally* quiet must not have its ordinary empty
+    # minute reported as a blockage. Three of these eight samples are
+    # themselves zero, so an observed zero sits inside the normal band and the
+    # `quiet` wording must not override that. An alert here would fire on most
+    # quiet cameras every few minutes and cost an officer's attention each time.
+    quiet_road = learn([row("cam05", 2, n)
+                        for n in (0, 2, 1, 0, 3, 1, 0, 2)])[("cam05", 2)]
+    calm = assess(quiet_road, 0)
+    assert calm.status == "normal", calm
+    assert not calm.anomalous, calm
+    assert abs(calm.z_score) <= abs(Z_LOW), calm
+    # The same camera stopping dead is still not abnormal; a genuine surge is.
+    assert assess(quiet_road, 10).status in ("elevated", "high")
+
+    # Rows written before `total` became a per-bucket figure carry a cumulative
+    # count and must never be learned from: one alone inflates the mean and
+    # standard deviation enough to hide a ten-fold spike.
+    legacy = {"camera_id": "cam06", "total": 67, "cumulative_total": 0,
+              "bucket_start": datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)}
+    genuine_empty = {"camera_id": "cam06", "total": 0, "cumulative_total": 0,
+                     "bucket_start": datetime(2026, 9, 1, 18, 0,
+                                              tzinfo=timezone.utc)}
+    modern = [{"camera_id": "cam06", "total": t, "cumulative_total": 100 + t,
+               "bucket_start": datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)}
+              for t in (3, 4, 2, 5, 3)]
+
+    # The legacy row is excluded...
+    with_legacy = learn(modern + [legacy])[("cam06", 18)]
+    assert with_legacy.samples == 5, with_legacy
+    assert with_legacy.mean < 4, with_legacy
+    # ...and the spike it would otherwise have hidden is still seen.
+    assert assess(with_legacy, 30).status == "high"
+
+    # ...while a genuine empty bucket, which looks similar, is kept: a quiet
+    # road is exactly what the baseline needs to learn.
+    with_empty = learn(modern + [genuine_empty])[("cam06", 18)]
+    assert with_empty.samples == 6, with_empty
+    assert with_empty.mean < with_legacy.mean, with_empty
+
+    # A source that carries no cumulative_total at all is trusted as given.
+    assert learn([{"camera_id": "cam07", "total": 9,
+                   "bucket_start": datetime(2026, 9, 1, 18, 0,
+                                            tzinfo=timezone.utc)}])
+
     # Zero variance must not produce an infinite or absurd z-score.
     flat = learn([row("cam03", 5, 20) for _ in range(8)])[("cam03", 5)]
     assert flat.stdev == 0.0 and flat.effective_stdev == STDEV_FLOOR
     one_more = assess(flat, 21)
     assert one_more.z_score == 1.0, one_more
     assert one_more.status == "normal", one_more
     far = assess(flat, 25)
     assert far.z_score == 5.0 and far.status == "high", far
 
     # A quiet night camera is not swamped by the floor either: 2 vehicles
