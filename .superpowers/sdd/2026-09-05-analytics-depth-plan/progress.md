# SDD ledger — plan: docs/superpowers/plans/2026-09-05-analytics-depth-plan.md

Ruling: implementing on main rather than a worktree — every commit this
session has gone to main at the user's direction, and data/ holds the SQLite
database, onboarded registry and model weights that a worktree would not see.
Cost if wrong: no branch isolation; mitigated by one commit per task.

## Pre-flight conflict scan

| Pair / task | Shared surface | Produces vs consumes | Finding |
|---|---|---|---|
| 1 & 6 | inference.py plate path | 1 produces consensus plate_text; 6 consumes indexed detections | Clean — 6 ordered after 1 |
| 1 & 3 | inference.py per-camera state dicts | 1 adds voter dict; 3 adds dark-frame + re-anchor state | Both touch `reset_camera_state`; ordered 1 then 3, task 3 brief notes the voter exists |
| 2 & 6 | matching.spacetime_plausible, geo.time_group | both consume; neither mutates | Clean |
| 3a & 6 | tracking.py MAX_TRACKS vs indexing volume | 3a caps tracks; 6 replays a full loop | Cap is per-camera live state, not stored detections — no conflict |
| 3b & 8b | counted_this_loop vs console counters | 3b produces; 8b consumes | Clean — 8 ordered last |
| 4b & 1 | pipeline watchlist prefilter vs consensus plate | 1 changes plate_text before matching | Clean — prefilter operates on whatever text arrives |
| 4a & 2 | retention deletes evidence; clones cite detections | 4a must not delete alert-referenced evidence | Constraint already stated in task 4a |
| 5 & 8c | baselines endpoint vs console panel | 5 produces; 8c consumes | Clean — ordered |
| 7 & assistant intents | retrieval.resolve before intent routing | 7 modifies existing routing | Risk: could regress existing intents; task 7 brief mandates all existing intents keep working and self-check covers it |
| Task 1 self-consistency | voter vs inference wiring | consistent | Clean |
| Task 2 self-consistency | same-camera guard vs time-group guard | both stated | Clean |
| Task 3 self-consistency | 4 sub-items each with self-check | consistent | Clean |
| Task 4 self-consistency | 3 sub-items, config additions stated | consistent | Clean |
| Task 5 self-consistency | MIN_SAMPLES vs stdev floor both stated | consistent | Clean |
| Task 6 self-consistency | network-free self-check vs network indexing | explicitly separated | Clean |
| Task 7 self-consistency | BM25 vs trigram fallback both specified | consistent | Clean |
| Task 8 self-consistency | needs snapshot endpoint, which it creates | stated in 8a | Clean |

Ruling: no conflict requires a plan change before execution. The one risk
(task 7 regressing assistant intents) is covered by that task's self-check
requirement. Cost if wrong: a regression caught at task review.

## Progress

Task 1: review spec OK, quality approved, 4 minors.
Task 1: Ruling: minor 2 (fallback reports total observation count, so an
  unvoted single read is indistinguishable from a real consensus) elevated to
  Important — it contradicts the plan's binding honesty constraint. One fix
  round dispatched. Cost if wrong: one extra round.
Task 1: minor (deferred): retain() does not run on frames with zero
  detections, so voter entries for dead tracks survive until the next frame
  with any detection. Pre-existing pattern, bounded at 20 x in-flight tracks.
Task 1: minor (deferred): stats["plate_votes"] counts detection-frames that
  received a consensus, not votes cast; name invites misreading.
Task 1: minor (deferred): plate_bbox left None on frames where consensus is
  applied without a fresh read, so a row can carry plate text with no box.
Task 1: fix round 1/5 (1 addressed, 0 open; commits 1dd960f..00d2a08)
Task 1: complete (commits 85b6515..00d2a08, review clean)
Task 3: complete (commits 00d2a08..a0021c6, review clean)
Task 3: minor (deferred): flag_ambiguity measures against the top score only,
  so a chained cluster is cut off mid-way. Documented with a ponytail ceiling.
Task 3: minor (deferred): /track lacks the set-level `ambiguous` summary that
  /similar has; per-row flags are present on both.
Task 3: Ruling: minor 2 (pipeline.flush_traffic_stats persists the cumulative,
  replay-inflated total_counted rather than counted_this_loop) is load-bearing
  for Task 5 — baselines learn from TrafficStat and would learn inflated norms.
  Carried into Task 5's dispatch as a required fix rather than parked.
  Cost if wrong: baselines trained on inflated counts, silently useless.
Task 2: review spec OK, quality approved, 1 Important + 3 minors.
Task 2: fix round 1 dispatched — Important (cross-group rows break the
  consecutive-pair chain, hiding same-session clones; reviewer reproduced a
  genuine clone vanishing when a cross-session row sorts between the pair)
  plus Minor (zero elapsed time scores above the worst measured violation,
  so the most confident findings have the least determinate arithmetic).
Task 2: minor (deferred): core/timing.py carries no _self_check of its own,
  and route.py's FakeDet sets scene_time == wall_time so the shared
  scene-time-over-wall-time preference is pinned nowhere. Pre-existing.
Task 2: minor (deferred): len(plate) < 6 guard is undocumented in the
  endpoint note; it silently drops short legitimate reads.
Task 2: parked — Ruling: clock-anchor confidence is not surfaced in a clone
  finding's reason, though it is as load-bearing for the speed claim as the
  plate-read confidence that is surfaced. Real, but ClockAnchor confidence is
  not persisted on Detection, so plumbing it through is a schema change for a
  wording improvement. Deferred. Cost if wrong: a reason line slightly
  overstates how established the timestamps are; mitigated by the existing
  "verify against the evidence images before acting" caveat.
Task 2: fix round 1/5 (2 addressed, 0 open; commits 71c0e99..e191cb2)
Task 2: complete (commits a0021c6..e191cb2, review clean)
Task 4: review spec FAILED on 4b (prefilter is not a superset and loses real
  alerts: two OCR errors suffice, fused 0.67 > 0.55 threshold, 6.1% of
  alerting two-error pairs dropped in a 4,000-pair sweep). 4a and 4c met.
  Data-destruction audit came back clean: self-check leaves data/netra.db and
  data/evidence byte-identical; unacknowledged-alert evidence never deleted.
Task 4: fix round 1 dispatched — Important (prefilter loses alerts; brute-force
  verification over the two-error variant space now mandated) plus Minor
  (retained_protected double-counts when both prune rules bite).
Task 4: minor (deferred): acknowledged-alert evidence can be deleted while its
  Detection row survives, leaving a dangling evidence_path. Follows the brief;
  worth a ponytail line rather than a change.
Task 4: fix round 1/5 (2 addressed, 0 open; commits 6e32b94..e3a54a8).
  Re-review independently brute-forced 21,464,352 pairs with its own generator
  and found 0 losses; the run-length derivation was re-derived and is sound.
  Original INDEX_WINDOW=4 was unsound at every span, not just a corner.
Task 4: complete (commits e191cb2..e3a54a8, review clean)
Task 4: minor (deferred): known throughput ceiling — correct prefilter costs
  13.5ms per detection against a 10,000-entry watchlist versus 51.8ms
  unfiltered (3.8x, was ~20x when lossy). ~0.7 CPU-seconds per wall second at
  thousands of detections/minute. Remedy if it bites is a cheaper exact path
  or sharding, never a lossier window.
Task 4: minor (deferred): candidates() documents "Order is stable" while
  iterating a set, so ordering varies with PYTHONHASHSEED. Pre-existing.
