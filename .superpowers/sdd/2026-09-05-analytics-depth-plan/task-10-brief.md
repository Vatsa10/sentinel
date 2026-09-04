# Task 10 brief — issues sweep

## Task 10 — Sweep of deferred findings before submission

**Why:** eight tasks of review produced 27 deferred or carried items. Most are
minor. A few are load-bearing for what the submission claims, and one is a
correctness hole in the data the demo will run over. This task closes the
ones that matter and records a ruling on the ones that do not.

Work through the tiers in order. Tier A is mandatory. Tier B is expected.
Tier C is only if time remains and must not expand scope.

### Tier A — must fix (correctness, honesty, security)

**A1. Purge or null the pre-corroboration scene times.** ~11,000 `Detection`
rows carry `scene_time` values written before corroborated anchoring landed,
including provably wrong spans (`2025-06-14`, `2026-06-24`, `2028-06-13`).
Route reconstruction, cloned-plate detection and journey mining all reason
over elapsed time, so any claim from the current store is partly built on
known-bad timestamps. Add an additive boolean column `scene_time_corroborated`
(default false) via `_ADDED_COLUMNS`; set it true on every row the pipeline
writes from now on (the inference engine knows whether the anchor was
corroborated — carry that flag through `VehicleDetection` to `_flush`). Then
make `route.py`, `cloned_plate.py`, `loop_index.py` and `core/timing.py`
**ignore uncorroborated scene times** (fall back to "no scene time", never
to wall time for cross-camera reasoning). Provide `tools/purge_scene_times.py`
that nulls `scene_time` on rows where the flag is false, with `--dry-run`
default and a printed count. Do not run it destructively yourself — report the
dry-run count. Self-checks in each consumer must pin that an uncorroborated
scene time is not used.

**A2. Persist the plate vote count.** `plate_vote.consensus()` returns a
voter count that is never stored. Add `plate_votes: int | None` to `Detection`
via `_ADDED_COLUMNS`, carry it from `VehicleDetection` through `_flush`,
serialise it in `/api/detections`, and show it in the Detections table beside
the plate (e.g. "GJ01AB1234 · 7 reads"). A fallback single read stores `1`.
Self-check: the wiring assertion in `inference._self_check` or `plate_vote`.

**A3. Console snapshot under access control.** The zone editor loads stills via
`<img src>`, which cannot send `X-API-Key`; with keys configured the still
401s while the rest of the console works. Minimal fix that keeps `require`
on the endpoint: the console fetches the snapshot with `fetch()` carrying the
header (read the key from `localStorage` the way the rest of the console
would once auth is wired — if there is no console-side key store yet, add a
single `NETRA_API_KEY` entry in `localStorage` and a one-line settings input)
and sets the `<img>` to an object URL. Verify: with `data/api_keys.json`
present, the still loads in a browser-equivalent `fetch` with the header and
404/401 without it. Then remove the keys file so the demo stays in open mode.

**A4. Traffic class breakdown on tracker restart.** `flush_traffic_stats`
takes the whole cumulative as `total` on a restart but differences
`counts_by_class` against the larger previous snapshot, so every class delta
is `<= 0` and the bucket has a total with an empty breakdown. Reset the
class snapshot on the same condition that resets the total. Self-check pins
`sum(counts_by_class) <= total` **and** non-empty breakdown on a restart
bucket with detections.

### Tier B — should fix

**B1. Anomalies freshness.** `/api/analytics/anomalies` and the assistant's
`_unusual` judge each camera's most recent bucket with no max-age, so a camera
that stopped reporting hours ago is presented as a current reading. Add
`ANOMALY_MAX_BUCKET_AGE_S` (default 900); older buckets are reported as
`stale` with the bucket timestamp in the explanation, never as current.

**B2. `next_mine_in_s` / stale-store disclosure.** Confirm the journeys
endpoint's `next_mine` field and note say exactly what will happen on a
non-empty store; if round 4 already closed this, verify and note it.

**B3. Snapshot cache eviction.** Add age-based eviction on read (drop entries
older than `SNAPSHOT_TTL_S * 4`) so the dict cannot grow past the camera set
even if camera ids churn. Trivial.

**B4. Stats naming.** Rename `stats["plate_votes"]` (which counts
detection-frames that received a consensus) to `plate_consensus_applied`, and
surface the new persisted `plate_votes` column separately. Update the console
tile label.

**B5. `candidates()` order stability.** `WatchlistIndex.candidates` documents
"Order is stable" while iterating a `set` of window keys. Sort the keys.

### Tier C — only if time remains

**C1.** Traffic tab auto-poll every 5 s while visible.
**C2.** `retention.py` `ponytail:` line for the acknowledged-alert evidence
case.
**C3.** `core/timing.py` gets its own `_self_check()` pinning
scene-time-over-wall-time preference (currently pinned nowhere).

### Explicitly not in scope — ruled, do not touch

- Day-of-week baselines; VeRi-776 ReID backbone; edit-distance plate
  alignment; beam-search journey chaining; systematic-misread detection for
  the clock; Postgres migration. All documented as ceilings with `ponytail:`
  comments and in the HLD roadmap.

**Verification to report:** every module self-check that exists (`python -m`
over `netra.analytics.*`, `netra.core.*`, `netra.api.*`) passes; `run.py
--check` is READY; the server boots; the console endpoints all return 200;
`node --check` passes; `git status --porcelain data/` is empty; the A1
dry-run count.

---

## Global Constraints (from the plan)

- Every non-trivial module carries a runnable `_self_check()` via
  `python -m netra.<module>`, plain `assert`, no test framework.
- No new heavyweight dependencies.
- Nothing may starve detection.
- Honesty over impressiveness. Never present inference as fact.
- All timing from PTS or scene time, never arrival time — and now, never an
  uncorroborated scene time for cross-camera reasoning.
- British spelling; comments explain *why*; `ponytail:` marks simplifications
  and their ceiling.
- Commit per tier with a descriptive body. Stage specific paths only.
