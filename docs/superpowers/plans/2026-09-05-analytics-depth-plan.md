# Implementation plan — analytics depth, edge-case hardening, hybrid retrieval

**Project:** NETRA, Gujarat Police Innovation Challenge 2026
**Repo:** https://github.com/Vatsa10/sentinel
**Branch:** main
**Deadline:** ~53 hours from 2026-09-05

## Context

NETRA is a working CCTV analytics platform running against the Government
Sentinel grid: 30 cameras onboarded and capability-profiled, two-tier GPU
inference, watchlist fusion matching, appearance re-identification,
scene-time recovery, zone rules, per-camera tracking, RBAC, alerting, and an
operator console. Measured: 0% frame loss on 8 busy cameras, ~4,900 vehicles
in two minutes.

Three facts about the grid drive this plan and must not be forgotten:

1. **Plate recognition is not achievable on most of the grid.** Measured over
   2,691 frames on the three best-positioned cameras: 200+ vehicles, zero
   readable plates. Wide-area night overview cameras, plates 10-20 px.
2. **The recordings loop and are finite.** Each camera replays a fixed
   recording. This is the largest unexploited property of the dataset.
3. **Loops are time-aligned within groups only.** Ahmedabad (cam01-05, 13,
   14, 15) share a clock; Junagadh (cam08-11) share another. Cross-group
   timestamp comparison is meaningless.

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

## Task ordering and dependencies

```
1 (plate voting)  ─┬─> 2 (cloned plates, better plate quality helps)
                   └─> 6 (loop indexing benefits from voting)
3 (bounded state) ──> independent, do early for stability
4 (retention)     ──> independent
5 (baselines)     ──> needs TrafficStat rows, which exist
6 (loop mining)   ──> needs 1; uses geo time groups
7 (hybrid)        ──> independent
8 (console)       ──> needs 2, 5, 6 endpoints to exist
```

Execution order: **1, 3, 2, 4, 5, 7, 6, 8.**

Task 3 moves early because it is stability work and the platform is running.
Task 8 is last because it consumes the others' endpoints. Task 6 is second to
last because it is the largest and its value is a demo artifact rather than a
dependency.

## Definition of done

- Every module's `python -m netra.<module>` self-check passes.
- `python run.py --check` reports READY.
- Server boots; every endpoint the console calls returns 200.
- A live run over at least 5 grid cameras shows 0% dropped frames.
- The output report at `/api/report` renders with the new sections.
- No credentials in any commit.
