# Task 4 brief

## Task 4 — Retention and watchlist scale

**Why:** two unbounded stores and one unindexed hot path.

**4a. Retention (`netra/core/retention.py`, new).**
- `prune_evidence(max_bytes, max_age_days) -> dict` — delete evidence files
  oldest-first until under budget. Never delete a file referenced by an
  unacknowledged `Alert` or `ZoneEventRow`: evidence attached to a live alert
  is the whole point of keeping it. Report what was removed and what was
  retained by that rule.
- `prune_detections(max_rows, keep_days) -> dict` — delete oldest detections
  beyond a row cap. Never delete a detection referenced by an `Alert`.
- `storage_report() -> dict` — evidence file count and bytes, detection and
  alert row counts, plus the configured budgets.
- Config in `netra/config.py`: `EVIDENCE_MAX_BYTES` (default 5 GiB),
  `EVIDENCE_MAX_AGE_DAYS` (default 7), `DETECTION_MAX_ROWS` (default
  2,000,000).
- Endpoints: `GET /api/storage` (read), `POST /api/storage/prune`
  (permission `manage`).

**4b. Watchlist scale (`netra/pipeline.py`).**
`_check_watchlist` scores every detection against every active watchlist
entry. At 10,000 entries and thousands of detections per minute this is the
next bottleneck. Add a prefilter: index the cached watchlist by the first four
characters of the normalised plate, plus a bucket of entries whose plate is
too short to index. A detection is scored only against entries sharing a
prefix bucket with any 4-character window of its own normalised plate, plus
the short bucket. Full scoring still runs on candidates, so partial and
confusion-folded matching behaviour is unchanged — this only avoids scoring
entries that cannot possibly match.

Correctness requirement: the prefilter must be a **superset** of what full
scoring would match. Prove it in the self-check with a partial read that a
naive exact-prefix index would miss.

**4c. Black-frame short-circuit (`netra/analytics/inference.py`).**
A camera that returns frames which are entirely black burns GPU forever. The
registry already classifies such cameras at onboarding, but a camera can go
dark after onboarding (nightfall). Track consecutive frames with no
detections and mean luma below `DARK_LUMA_THRESHOLD = 18`; after
`DARK_FRAME_LIMIT = 60` such frames, mark the camera dark and skip inference
on it, re-testing one frame every `DARK_RECHECK_FRAMES = 300` so it recovers
automatically at dawn. Surface dark cameras in pipeline status. Luma is
computed on a downscaled frame — measuring darkness must not itself cost
meaningful time.

**Self-checks:** retention module gets a `_self_check()` using a temporary
directory and an in-memory database, covering the alert-referenced protection
in both pruners. Watchlist prefilter and dark-frame counter get assertions in
their modules.

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
