# Task 10 brief — issues sweep

## Task 10 — Sweep of deferred findings before submission

**Why:** eight tasks of review produced 27 deferred or carried items. Most are
minor. A few are load-bearing for what the submission claims, and one is a
correctness hole in the data the demo will run over. This task closes the
ones that matter and records a ruling on the ones that do not.

Work through the tiers in order. Tier A is mandatory. Tier B is expected.
Tier C is only if time remains and must not expand scope.

### Tier A — must fix (correctness, honesty, security)

**A0. The scene-clock attempt budget is never consumed (Task 6 round-5
carry-over, load-bearing).** In `InferenceEngine._anchor_clock`,
`self._clock_attempts[cam] = 0` runs on *every* successful overlay read,
including one that then contradicts the pending candidate. Measured: a
camera fed 200 mutually-contradicting readings produced 200 OCR calls, ended
with `_clock_attempts == 0`, and never gave up. On the live (opportunistic)
path there is no spacing gate, only the queue-slack check, so a jittery or
partially-occluded overlay can OCR on every slack frame indefinitely — the
exact starvation the cap exists to prevent (its comment cites 83% frames
dropped without it). Fix: reset the attempt counter only on a **corroborated**
anchor, and count contradictions against the budget so a camera that never
agrees with itself gives up within `CLOCK_ATTEMPT_LIMIT` (live) /
`INDEX_CLOCK_ATTEMPT_LIMIT` (exhaustive). Keep the existing behaviour that a
re-anchor after `CLOCK_REANCHOR_AFTER_S` gets a fresh budget. Self-check in
`inference._self_check`: 200 contradicting reads on the live path must stop
issuing OCR calls after the limit, and a later agreeing pair must still be
able to anchor once the re-anchor window opens.

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

**B6. FP16 inference, two flags.** Pass `half=True` on every
`self._vehicle_model.predict(...)` call in `inference.py` when
`config.DEVICE == "cuda"`, and call `.half()` on the ReID backbone in
`reid.py` (cast the input tensor to match). No INT8, no TensorRT — detection
is not the bottleneck (13 ms/frame measured; OCR, writes and escalation were),
and TensorRT on sm_120 is a day's risk for a number that does not move the
demo. Verify: `inference` and `reid` self-checks still pass; a 30-second live
run on 5 cameras still shows 0% dropped; report before/after `infer_ms`.

**B7. HLD capacity numbers, say which is which.** `docs/high-level-design.md`
§7–8 cite ~6 ms/frame and 150–200 cameras per node at tier-1. Add one
paragraph distinguishing *tier-1 scanning capacity* (that figure) from
*full-pipeline capacity* under admission control — detection + ReID + OCR +
zones at 0% frame loss, measured at roughly 8 simultaneously escalated busy
junctions on an RTX 5050. Both are measured; they answer different questions,
and the scale story must not let a reader multiply the wrong one.

**B8. Duplicated block in `netra/pipeline.py`.** `ATTRIBUTE_BROADCAST_BOUND_S`
is defined twice (with its comment), and the attribute-worker `__init__`
block (`_attr_queue`, `_attr_stop`, `_attr_thread`, `_attr_last`,
`attribute_stats` + comment) is duplicated verbatim. Behaviourally harmless;
remove the first copy of each. Verify the attribute self-check and a live
`/api/pipeline/status` still show the worker stats.

**B9. Single-word descriptions as retrieval keys.** Descriptions like
`"yellow"` enter the BM25 `vehicle` corpus and tie arbitrarily. Gate indexing
on at least two populated structured fields or a description of ≥ 2 tokens;
pin in `retrieval._self_check` that a one-word description is not indexed
and a two-token one is.

### Tier C — only if time remains

**C1.** Traffic tab auto-poll every 5 s while visible.
**C2.** `retention.py` `ponytail:` line for the acknowledged-alert evidence
case.
**C3.** `core/timing.py` gets its own `_self_check()` pinning
scene-time-over-wall-time preference (currently pinned nowhere).
**C4.** `index_run3.start` was committed to the repo root by the
auto-committer. `git rm` it and add `*.start` to `.gitignore` (`*.log` is
already there — verify, do not duplicate).

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
