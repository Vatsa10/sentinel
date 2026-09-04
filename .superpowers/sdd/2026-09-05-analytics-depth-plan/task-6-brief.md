# Task 6 brief

## Task 6 — Loop indexing and real journey mining

**Why:** this is the largest unexploited property of the dataset. Each camera
replays a finite recording, and within a time group the recordings share a
clock. Processing a loop completely, once, yields an exhaustive index of every
vehicle in it — and mining across time-aligned cameras finds vehicles that
genuinely appear on more than one camera. That converts cross-camera tracing
from a demonstrated capability into a discovered fact from the Government's
own footage.

**Create `netra/analytics/loop_index.py`:**

- `estimate_loop_length(camera_id, ...)` — connect, read PTS until it jumps
  backwards, return the observed loop duration. Cap the attempt with a
  timeout and return None on failure rather than blocking forever.
- `index_camera(camera_id, engine, max_seconds)` — run one full pass of a
  camera's loop through detection, embedding and scene-time anchoring,
  persisting detections as the live path does. Reuse the existing
  `InferenceEngine`; do not duplicate inference logic.
- `find_journeys(time_group, min_similarity=0.84, min_hops=2) -> list[Journey]`
  — over indexed detections belonging to one time group:
  - Candidate pairs by appearance similarity across *different* cameras.
  - Validate each pair with `spacetime_plausible` using **scene time**, not
    wall time. Cross-camera journeys are only meaningful on the recorded
    clock.
  - Chain validated pairs into ordered journeys; report hops, total distance,
    elapsed time, mean similarity, and each hop's evidence path.
  - `Journey` carries a `confidence` and an explicit note that it is an
    appearance-based candidate for operator confirmation.
- Chaining must be greedy and bounded — no exhaustive search over thousands
  of detections. Cap candidates per detection and journeys returned. State the
  ceiling in a `ponytail:` comment.

**CLI:** `tools/index_loops.py --cameras cam01,cam04 --group ahmedabad-13jun`
which indexes the named cameras then mines and prints journeys.

**Expose:** `GET /api/analytics/journeys?group=<name>` returning mined
journeys, and persist them in a new `MinedJourney` model so the console can
show them without re-running the mining.

**Self-check must cover:** journeys never chain across time groups; an
implausible hop is rejected; a same-camera pair is not a journey; ordering is
by scene time; a journey below `min_hops` is not returned. Use synthetic
detections — this self-check must not require the network.

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
