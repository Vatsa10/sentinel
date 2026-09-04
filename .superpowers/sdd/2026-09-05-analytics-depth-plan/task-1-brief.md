# Task 1 brief

## Task 1 — Multi-frame plate voting

**Why:** plates are currently read independently per frame and the best single
read wins. A track seen across ten frames gives ten noisy reads of the same
plate; voting per character position is how production ANPR reaches usable
accuracy. This directly serves evaluation criterion 5.

**Create `netra/analytics/plate_vote.py`:**

- `PlateObservation` dataclass: `text: str`, `confidence: float`,
  `pts_ms: float`.
- `PlateVoter` class, one instance per camera, holding observations per
  `track_id`:
  - `add(track_id, text, confidence, pts_ms) -> None` — record one read.
    Ignore reads shorter than 4 characters.
  - `consensus(track_id) -> tuple[str | None, float, int] | None` — returns
    `(text, confidence, observation_count)`.
  - `forget(track_id)` and `reset()`.
- Voting algorithm:
  - Group observations by length; the modal length wins, because a read of
    the wrong length is misaligned and voting across lengths corrupts
    positions. Break ties by summed confidence.
  - For each character position among the winning-length observations,
    accumulate confidence per candidate character. Fold OCR confusions using
    `matching.CONFUSIONS` **only for grouping votes**, then emit the
    highest-confidence raw character among that group — folding for
    comparison must not corrupt the output text.
  - Consensus confidence is the mean per-position winning share, so a plate
    where every position was unanimous scores near 1.0 and a plate where
    positions disagreed scores lower.
  - Require at least 2 observations to return a consensus; a single read has
    nothing to vote on and is returned as-is with its own confidence.
- `MAX_OBSERVATIONS_PER_TRACK = 20`, oldest dropped, so a long-dwelling
  vehicle cannot grow memory without bound.

**Wire into `netra/analytics/inference.py`:**

- One `PlateVoter` per camera, held alongside `self.trackers`.
- After tracking has assigned `track_id` and after `_read_plate` produced a
  read, feed the read to the voter and replace `det.plate_text` /
  `det.plate_conf` with the consensus when one exists, setting
  `det.plate_chars` to the consensus length.
- Add `plate_votes` to `self.stats`.
- `reset_camera_state` must clear the voter for that camera.
- Voter entries for expired tracks must be forgotten. The tracker expires
  tracks internally; after `tracker.update(...)`, forget voter entries whose
  `track_id` is no longer in `tracker.tracks`.

**Self-check must cover:** unanimous reads; a minority misread character
outvoted by the majority; observations of differing lengths not corrupting
each other; confusion-folded votes grouping (`GJ01AB1234` and `GJO1AB1234`
agreeing) while the emitted text stays a real plate; single observation
returned unchanged; observation cap enforced; unknown track returns None.

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
