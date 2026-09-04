# Task 5 brief

## Task 5 — Per-camera behavioural baselines

**Why:** 17,000 detections is data, not information. A control room needs to
know when a camera's traffic is abnormal — a blocked road, a forming crowd,
unusual night movement.

**Create `netra/analytics/baseline.py`:**

- Learn from `TrafficStat` rows: for each `(camera_id, hour_of_day)`, the
  mean and standard deviation of `total`, and the sample count.
- `learn(rows) -> dict[tuple[str, int], Baseline]` where
  `Baseline(mean, stdev, samples)`.
- `assess(baseline, observed) -> Assessment` with `z_score`, `status` in
  `normal | elevated | high | low | quiet`, and a sentence explaining it.
- **Require `MIN_SAMPLES = 5` before making any judgement.** With fewer
  samples the baseline is noise and asserting an anomaly from it would be
  fabrication. Below the threshold `status` is `insufficient_data`, stated
  plainly.
- Use a robust dispersion floor: when stdev is near zero (a camera with
  identical counts) a small deviation would otherwise produce an enormous
  z-score. Floor stdev at `max(stdev, 1.0)` and say so in a comment.
- `detect_anomalies(baselines, current_stats) -> list[Assessment]` for the
  live path.

**Expose:** `GET /api/analytics/baselines` (learned norms per camera and hour)
and `GET /api/analytics/anomalies` (current deviations). Add an assistant
intent for "anything unusual?".

**Self-check must cover:** insufficient samples never yields a verdict; a
clear spike is flagged elevated/high; a normal reading is not flagged; zero
traffic against a busy baseline is flagged quiet/low; zero-variance baseline
does not produce an infinite or absurd z-score.

---

---

## Global Constraints

- **Every non-trivial module carries a runnable `_self_check()`** invoked via
  `python -m netra.<module>`, using plain `assert`. No test framework. This
  matches every existing module in the repo.
- **No new heavyweight dependencies.** stdlib, or something already
  installed (numpy, opencv, sqlalchemy, torch, ultralytics, easyocr,
  requests, fastapi). BM25 is ~40 lines of stdlib; do not add `rank_bm25`.
- **Nothing may starve detection.** Detection is the primary duty. Any
  enrichment (OCR, embedding, indexing, baselines) must be bounded, capped,
  or opportunistic. Measured precedent: unbounded overlay OCR cost 71% of
  frames; unbounded per-detection DB writes cost 76%.
- **Honesty over impressiveness.** Never present inference as fact. Confidence
  and reasoning travel with every alert. Where the platform cannot do
  something, it says so with the measurement behind it.
- **All timing from PTS or scene time, never arrival time.** Two grid cameras
  declare `0/0` fps.
- **Style:** match surrounding code. Comments explain *why*, not *what*.
  Mark deliberate simplifications and their ceiling with a `ponytail:` comment.
- **British spelling** in prose and comments, as the existing code uses.
- Commit per task with a descriptive message body explaining the reasoning.

## Existing interfaces the tasks depend on

```
netra/analytics/inference.py   InferenceEngine, VehicleDetection(camera_id,
                               pts_ms, wall_time, vehicle_class, confidence,
                               bbox, colour, plate_text, plate_conf,
                               plate_chars, plate_bbox, scene_time, track_id,
                               embedding, evidence)
                               CLOCK_ATTEMPT_LIMIT, REID_*, PLATE_*
netra/analytics/tracking.py    Track(track_id, camera_id, vehicle_class, bbox,
                               first_pts_ms, last_pts_ms, embedding, path,
                               sightings, counted, zones_triggered), dwell_s,
                               direction(); CameraTracker.update(dets, pts_ms)
                               -> newly counted; TrackerRegistry.get/reset/stats
netra/analytics/matching.py    normalise_plate, plate_similarity,
                               spacetime_plausible, score_match, CONFUSIONS
netra/analytics/reid.py        ReIdEncoder.encode(crops) -> (n,512), similarity
netra/analytics/zones.py       ZoneEngine, Zone, ZoneEvent
netra/analytics/scene_clock.py ClockAnchor(camera_id, scene_time, pts_ms,
                               confidence).at(pts_ms), read_scene_time
netra/core/models.py           Camera, Detection, WatchlistEntry, Alert,
                               AuditLog, ZoneRule, ZoneEventRow, TrafficStat
netra/core/geo.py              CAMERA_GEO, TIME_GROUPS, time_group,
                               haversine_km
netra/pipeline.py              Pipeline (PIPELINE singleton), _flush batching,
                               _broadcast, _check_watchlist, _raise_alert
netra/api/app.py               FastAPI app, require(permission) dependency,
                               _audit
netra/api/assistant.py         ask(question) -> {answer, data, actions}
netra/web/index.html, app.js   operator console
```

---
