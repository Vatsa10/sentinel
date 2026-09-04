# Task 2 brief

## Task 2 — Cloned-plate detection

**Why:** the same registration number appearing in two places too far apart
for one vehicle to have travelled is a forged or cloned plate. This is a real,
named policing problem, it falls directly out of the space-time feasibility
check already in `matching.py`, and it demonstrates domain understanding
rather than model plumbing.

**Create `netra/analytics/cloned_plate.py`:**

- `CloneFinding` dataclass: `plate`, `sighting_a` and `sighting_b` (each a
  dict with `detection_id`, `camera_id`, `camera_name`, `lat`, `lon`, `at`),
  `distance_km`, `elapsed_s`, `implied_kmh`, `confidence`, `reason`.
- `find_clones(detections, min_confidence=0.6) -> list[CloneFinding]`:
  - Accepts ORM `Detection` rows with `.camera` loaded.
  - Group by `normalise_plate(plate_text)`; ignore groups of one.
  - Order each group by scene time where available, else wall time — reuse
    the same preference logic `route.py` uses.
  - For each consecutive pair, compute distance via `haversine_km` and
    elapsed time. A pair that fails `spacetime_plausible` is a clone
    candidate.
  - **Only compare sightings within one time group** (`geo.time_group`).
    Two sightings from different recording sessions are not simultaneous in
    reality and must never be reported as a clone — this is the single most
    important correctness constraint in this task.
  - Confidence rises with how badly the pair violates plausibility and with
    the plate-read confidence of both sightings. A pair implying 200 km/h is
    weaker evidence than one implying 5,000 km/h. Cap at 0.99 — never claim
    certainty.
  - `reason` states the arithmetic in words an officer can check.
- Guard against the obvious false positive: two sightings on the **same
  camera** seconds apart are not a clone, they are one vehicle seen twice.

**Expose it:**

- `GET /api/analytics/cloned-plates` — runs the detector over stored
  detections, returns findings with the reasoning. Read-only, no new
  permission.
- Add a `cloned_plate` intent to the assistant so "any cloned plates?" is
  answerable.

**Self-check must cover:** an impossible pair flagged; a plausible pair not
flagged; two sightings on the same camera not flagged; sightings in different
time groups never compared; a single sighting yielding nothing; confidence
ordering (worse violation scores higher); missing coordinates handled without
crashing.

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
