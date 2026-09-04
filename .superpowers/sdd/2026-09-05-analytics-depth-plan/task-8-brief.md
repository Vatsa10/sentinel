# Task 8 brief

## Task 8 — Console UI for the new analytics

**Why:** every capability added in tasks 1-7 is invisible in the demo, and
evaluators watch the screen. This task is what makes the rest scoreable.

Follow the existing console conventions exactly: the `.view` / `nav a`
tab pattern, `card()` for stat tiles, `.panel`, `.finding`, `.tag`, `.hop`,
the `esc()` helper on every interpolated value, and the existing dark palette
tokens. Add no framework and no build step.

**8a. Zones tab.** List configured rules per camera with rule type, class
filter and severity. Create a rule by clicking points on a **live still frame
from the camera** (fetch a snapshot, click to place normalised points, choose
rule type, submit). Delete a rule. Live zone-event feed with evidence
thumbnails.

Add the snapshot endpoint this needs: `GET /api/cameras/{id}/snapshot`
returning a single JPEG from the camera. Bound it with a timeout and cache
briefly — an operator clicking around a zone editor must not open a new RTSP
connection per click.

**8b. Traffic tab.** Per-camera live counters: total counted, counted this
loop, active tracks, class mix, direction split, mean dwell. Sparkline or
simple bar history from `/api/traffic/history`. Snapshot button.

**8c. Intelligence tab.** Three panels: cloned-plate findings with their
arithmetic; behavioural anomalies with z-scores and the plain-language
explanation; mined cross-camera journeys with hops, similarity and evidence.

**8d. Existing tabs.** Show `track_id` and scene time in the detections
table. Show plate consensus observation count where present. Show the
re-identification ambiguity flag in appearance traces.

**Verification:** every endpoint the console calls must be verified to return
200 with the shape the JavaScript expects. `node --check netra/web/app.js`
must pass. State in the report which endpoints were exercised and their
status codes.

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
