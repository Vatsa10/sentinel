# Task 3 brief

## Task 3 — Bounded state and correctness guards

**Why:** the platform is intended to run for hours. Four unbounded or
unguarded structures will degrade or silently corrupt results.

**3a. Bounded tracker state (`netra/analytics/tracking.py`).**
`CameraTracker.tracks` is expired only by timeout. A busy junction with rapid
turnover can still accumulate. Add `MAX_TRACKS_PER_CAMERA = 300`; when
exceeded after expiry, drop the least recently seen tracks first and count
the drops in a `dropped_tracks` counter surfaced by `stats()`. Dropping the
oldest is correct here: a track not seen for longest is the least likely to
receive another detection.

**3b. Loop-boundary duplicate guard (`netra/analytics/tracking.py`).**
At a loop cut the recording restarts, tracker state resets, and the same
vehicles are counted again — inflating counts every loop. Add to
`CameraTracker`: a `loops_seen` counter incremented by `reset()`, and
`stats()` gains `counted_this_loop` alongside the cumulative `total_counted`.
`reset()` must **not** zero `total_counted` (cumulative observation is real)
but must expose the per-loop figure so the console can show counts that are
not silently multiplied by however many times the recording has replayed.
Document this distinction in the docstring — an evaluator reading a count of
"4,893 vehicles" needs to know whether the recording played twice.

**3c. Scene-clock drift re-anchoring (`netra/analytics/inference.py`,
`netra/analytics/scene_clock.py`).**
An anchor extrapolates indefinitely from one reading. Decoder timing drift
accumulates, so timestamps grow silently wrong on long connections. Add
`ClockAnchor.age_s(pts_ms)`. In `inference._anchor_clock`, re-anchor when the
existing anchor is older than `CLOCK_REANCHOR_AFTER_S = 900` of stream time,
subject to the same opportunistic queue-slack rule and attempt cap already in
place. A re-anchor that fails leaves the existing anchor in place — a stale
anchor beats no anchor.

**3d. Re-identification ambiguity guard (`netra/analytics/reid.py`).**
`rank_candidates` returns the closest matches, but when the top several
candidates are near-identical in similarity the match is ambiguous — two
similar silver hatchbacks — and presenting a top hit implies confidence the
evidence does not support. Add to each result an `ambiguous: bool`, true when
the runner-up is within `AMBIGUITY_MARGIN = 0.02` of the top score, and an
`ambiguity_note` explaining it. The API must pass this through so the console
can show it. Do not silently drop ambiguous matches — an operator seeing "3
near-identical candidates" is better served than one shown a single confident
wrong answer.

**Self-checks:** each of 3a-3d gets assertions in its module's existing
`_self_check()`. Cover: track cap enforced and oldest dropped; `total_counted`
survives reset while `counted_this_loop` resets and `loops_seen` increments;
`age_s` arithmetic; ambiguity flagged when scores are close and not flagged
when the top match is clear.

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
