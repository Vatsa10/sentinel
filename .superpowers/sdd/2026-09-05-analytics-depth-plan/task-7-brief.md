# Task 7 brief

## Task 7 — Hybrid retrieval for the assistant

**Why:** the assistant answers from SQL, which cannot hallucinate a count.
But it fails on fuzzy phrasing — "the Junagadh bypass camera", a misspelled
place, "that toll camera". Two retrieval modes already exist in the platform
and one is missing.

The design is explicit about which mode owns what, because getting this wrong
is how a system starts inventing facts:

| Mode | Owns | Never used for |
|---|---|---|
| **SQL** | every fact: counts, timestamps, plates, statuses | fuzzy matching |
| **BM25** | resolving a fuzzy mention to an entity id | producing facts |
| **Vector** | appearance similarity between vehicles (exists in `reid.py`) | text |

So: **BM25 resolves what the user meant; SQL answers what is true; vector
answers what looks alike.** No mode ever produces a fact it did not read from
the database.

**Create `netra/api/retrieval.py`:**

- A small BM25 implementation over stdlib (~40 lines): tokenise on
  non-alphanumerics, lowercase, `k1=1.5`, `b=0.75`.
- `EntityIndex` built from camera ids and names, city and district, zone
  names, watchlist plates, case references and notes. Rebuilt on demand with
  a short TTL — the corpus is hundreds of rows, not millions.
- `resolve(query, kind=None, limit=5) -> list[EntityMatch]` where
  `EntityMatch(kind, id, label, score)`.
- Character-level fallback: when BM25 returns nothing (a misspelling shares no
  token), fall back to a normalised trigram overlap so "junagad" still
  resolves "Junagadh". Say why in a comment.

**Wire into `netra/api/assistant.py`:**

- Before keyword intent routing, resolve entity mentions. When a camera or
  zone resolves confidently, scope the SQL query to it and **say in the answer
  which entity was resolved and that it was inferred** — a user who meant a
  different camera must be able to see the substitution.
- Add an explicit `search` intent: a free-text query returns resolved entities
  and, for each, the SQL facts about it.
- Keep every existing intent working. The assistant's guarantee that it never
  invents an answer must survive this change.

**Self-check must cover:** exact camera name resolves; a partial name
resolves; a misspelling resolves via the trigram fallback; an unrelated string
resolves to nothing rather than to a spurious best match; BM25 scoring ranks a
better lexical match higher; resolution never returns an id that is not in the
corpus.

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
