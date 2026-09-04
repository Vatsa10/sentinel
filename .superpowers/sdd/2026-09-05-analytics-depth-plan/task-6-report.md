# Task 6 report — Loop indexing and real journey mining

## Status
Complete. Self-checks pass; the API surface imports and serves.

## What was built

`netra/analytics/loop_index.py`

- `estimate_loop_length(camera_id, timeout_s, spec)` — opens the source, reads
  PTS until it jumps backwards, and returns the observed recording length in
  seconds. Returns `None` on a failed open, on a stalled stream (60 consecutive
  failed reads) or on timeout, rather than blocking. The grid publishes no
  duration and two cameras declare `0/0` fps, so this is measured, not asked
  for.
- `index_camera(camera_id, engine, max_seconds, spec, persist)` — one complete
  pass of a loop through the live `InferenceEngine`. The engine's
  `on_detection` is borrowed for the duration and restored afterwards, so the
  index is produced by exactly the detection, embedding and scene-clock code
  the live pipeline runs. Frames are submitted *blocking* rather than dropped:
  the live path drops deliberately because a control room wants the newest
  frame, but the point of a finite loop is that it can be processed
  completely, so the reader slows to the model's pace. Persistence is routed
  through `Pipeline._flush` so indexed rows are byte-for-byte the same shape as
  live rows, including evidence crops and the watchlist check.
- `find_journeys(time_group, min_similarity=0.84, min_hops=2, detections=None,
  limit=50)` — mines one group's index for vehicles that genuinely appear on
  more than one camera. Returns `Journey` objects carrying hops, total
  distance, elapsed recorded time, mean similarity, a capped confidence and an
  explicit note that this is an appearance-based candidate for operator
  confirmation.
- `persist_journeys` / `stored_journeys` — replace-and-read for the new
  `MinedJourney` table. Replaced rather than appended, because mining is
  deterministic over the index and appending would show an operator the same
  journey several times.

`netra/core/models.py` — new `MinedJourney` model (new table, so `create_all`
covers it; no `_ADDED_COLUMNS` entry needed).

`netra/api/app.py` — `GET /api/analytics/journeys?group=…` behind
`require("read")`, audited, with `min_similarity`, `min_hops`, `refresh` and
`limit`. Serves the stored mining; mines and persists when the store is empty
or `refresh` is set. An unknown group is a 400 naming the known groups.

`tools/index_loops.py` — `--cameras cam01,cam04 --group ahmedabad-13jun`,
loading models once for all cameras, with `--probe-loops`, `--mine-only`
(no network) and `--no-persist`.

## The three constraints, as implemented

- **Scene time only.** A detection with no parsed scene time is excluded from
  mining and counted as excluded — never silently demoted to wall time. Wall
  time records when we connected to a loop, so a journey built on it would be
  an artefact of our client, not of a vehicle.
- **Never across time groups.** Candidates are drawn only from the group's
  member cameras, and the chaining loop asserts each candidate's
  `time_group(camera_id)` matches. Two self-check cases prove a Junagadh
  sighting never joins an Ahmedabad chain in either direction.
- **Never same-camera hops.** One camera twice is one vehicle seen twice.

## Bounding (the `ponytail:` ceiling)

Stated in the module: at most `MAX_MINED_DETECTIONS = 4000` rows mined,
`MAX_CANDIDATES_PER_DETECTION = 8` scoring candidates and `MAX_SCAN_AHEAD =
200` rows scanned per hop, `MAX_HOP_SECONDS = 1800` reach, and `MAX_JOURNEYS =
50` returned. Chains extend greedily by the single best next hop with no
backtracking, so a vehicle whose true next sighting scored second is followed
down the wrong branch and not recovered. That is the cost, and it is named.

## Honesty

`confidence` is mean similarity with a small length bonus, capped at 0.95 —
never certainty. Every journey and every API response carries the note that
these are candidates for confirmation, not identifications, and every hop
carries its similarity, distance, elapsed time and implied speed so an officer
can check the claim instead of taking it.

## Self-check

`python -m netra.analytics.loop_index`, on synthetic detections only — no
network, no GPU, no model load. Covers: a genuine three-camera journey with
per-hop arithmetic; scene-time ordering preserved from shuffled input; no
chaining across time groups (both directions); an implausible hop (1.3 km in
2 s) rejected; a same-camera pair is not a journey; `min_hops=3` suppresses a
two-hop journey; a sighting with no scene time cannot participate; dissimilar
vehicles are not chained; an unknown group returns nothing rather than raising.

## Verification run

```
python -m netra.analytics.loop_index   -> loop_index self-check passed
python -m netra.analytics.reid         -> reid self-check passed
python -m netra.analytics.route        -> route self-check passed
python -c "from netra.api.app import app" -> app ok
tools/index_loops.py --mine-only       -> runs, reports no journeys yet
GET /api/analytics/journeys?group=ahmedabad-13jun -> 200
GET /api/analytics/journeys?group=nope            -> 400
```

Persistence round-trip was exercised against `data/netra.db` with synthetic
journeys and the test rows were then cleared.

## Concerns

- Mining currently finds nothing on the stored database because no camera has
  yet been indexed with both a scene clock and embeddings on two cameras of one
  group. The mining is correct; it is waiting on an indexing run. Indexing
  `cam04` and `cam14` (the two Ahmedabad cameras with the best overlay clocks)
  is what turns this from a capability into the discovered fact.
- `index_camera` needs an engine that has been `load()`ed and `start()`ed; the
  CLI does this, a caller doing it by hand must too.
- Greedy chaining has no backtracking, by design and by budget. If a mined
  journey later proves to have taken a wrong branch, the fix is a beam of width
  2–3, not an exhaustive search.
