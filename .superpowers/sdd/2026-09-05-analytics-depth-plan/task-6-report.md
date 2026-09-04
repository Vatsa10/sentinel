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

---

# Fix round 1/5

All four Important findings and all three Minors are fixed.

## Important 1 — unbounded chain length at maximum confidence

The reviewer's 1,500-hop, twelve-hour, 0.95-confidence "journey" is the worst
thing this module could produce, because the number contradicts the prose. Two
ceilings and a changed confidence model:

- `MAX_CHAIN_HOPS = 12` sightings and `MAX_JOURNEY_SECONDS = 3600.0` of
  recorded time. A chain cut at either sets `truncated` on the `Journey`, in
  the stored row and in the API payload, so a bounded slice is never shown as
  a complete one.
- `_confidence` no longer *rewards* length. Mean similarity is attenuated by
  `1 / (1 + CHAIN_DECAY_PER_HOP * (hops - 2))` with `CHAIN_DECAY_PER_HOP =
  0.08`. A chain of many hops is a chain of many chances to have stepped onto
  a different vehicle that merely looks the same, and a greedy search takes
  the best-scoring step whether or not it is the right one, so length must
  cost confidence. Measured: an identical-embedding two-hop journey scores
  0.95; the same embedding over a full twelve-hop chain scores 0.528.
- Both ceilings and the reasoning are named in the module's `ponytail:` block,
  including the reviewer's 20,000-detection result as the evidence for them.

Self-check additions (400 synthetic detections, every leg individually
feasible): the longest mined journey is `<= MAX_CHAIN_HOPS`, every journey is
`<= MAX_JOURNEY_SECONDS`, the longest is flagged `truncated`, its confidence is
strictly below the two-hop journey built from the identical embedding, and no
journey of more than two hops reaches `MAX_CONFIDENCE`.

## Important 2 — zero results re-mined on every request

`JOURNEY_REMINE_COOLDOWN_S = 300.0` with a per-group timestamp in
`netra/api/app.py`. Mining now runs only under an explicit `refresh`, or when
the store is empty *and* the cooldown has elapsed. The empty result is
precisely the case that needed it: with nothing stored, every poll re-mined and
re-wrote while detection was running. Verified by three consecutive requests:
`mined_now` is `True`, then `False`, then `False`.

## Important 3 — query parameters silently ignored

`stored_journeys` now takes `min_hops` and `min_similarity` and filters the
store: exactly on `hop_count`, and on similarity by keeping journeys whose
weakest hop clears the bar. `refresh` re-mines at the requested thresholds. The
response carries `filters_applied: {min_hops, min_similarity, applied_by}`
where `applied_by` is `"mining"` or `"filter"`, and the note says a stricter
threshold can change which chains *form*, not merely which survive, so a caller
who needs that must refresh. `MinedJourney` gained `min_similarity` and
`truncated`, both registered in `_ADDED_COLUMNS`.

## Important 4 — exclusion report computed and discarded

Two surfaces, because the docstring's promise was caller-visible:

- `find_journeys(..., report=dict)` fills in `considered`, `supplied` and the
  `excluded` breakdown (`no_scene_time`, `no_embedding`, `wrong_group`).
- `exclusion_report(group)` counts the same from the database, so the figures
  are available when serving from the store and not only when mining. It is in
  the API response under `index` and printed by the CLI.

Real output on the current database: 23,102 detections on the Ahmedabad group's
cameras, 714 comparable, 634 usable, 80 excluded for having no appearance
vector. A reader now knows the journeys are drawn from 3% of the index.

## Minors

- `estimate_loop_length` now measures restart-to-restart: the first backwards
  PTS jump starts the clock, the second stops it, and the partial loop we
  joined is discarded. Docstring and CLI wording say so; a probe that times out
  after one restart returns `None` rather than a lower bound dressed up as a
  measurement.
- `_minable`'s "Newest first" comment corrected to describe the ascending sort
  and tail slice. `_load_group_detections` was ordering ascending before its
  `LIMIT`, so the two layers kept opposite ends of a long index; it now orders
  descending to match.
- `index_camera` raises `RuntimeError` if the engine is not `load()`ed or not
  `start()`ed. An unloaded engine accepts frames and produces nothing, which
  looks exactly like a camera with no traffic.

## Commands run

```
python -m netra.analytics.loop_index   -> loop_index self-check passed
python -m netra.analytics.reid         -> reid self-check passed
python -m netra.analytics.route        -> route self-check passed
python -c "from netra.api.app import app; print('app ok')" -> app ok

python tools/index_loops.py --group ahmedabad-13jun --mine-only
    634 sightings comparable, 0 excluded for no scene clock, 80 for no
    appearance vector, 0 outside the group
    index holds 23102 detections on these cameras, 714 of them comparable

GET /api/analytics/journeys?group=ahmedabad-13jun  x3 -> mined_now True, False, False
GET ...&refresh=true&min_similarity=0.95 -> mined_now True, applied_by "mining"
GET /api/analytics/journeys?group=nope   -> 400

_confidence([1.0], 2) = 0.95 ; _confidence([1.0]*11, 12) = 0.528
stored-row filtering: min_hops=3 keeps a 3-hop journey, min_hops=4 drops it

git status --porcelain data/  -> empty
```

The self-check remains network-free and GPU-free: it constructs synthetic
detections and numpy vectors and touches neither a source, a model, nor the
database.
