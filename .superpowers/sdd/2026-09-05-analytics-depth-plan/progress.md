# SDD ledger — plan: docs/superpowers/plans/2026-09-05-analytics-depth-plan.md

Ruling: implementing on main rather than a worktree — every commit this
session has gone to main at the user's direction, and data/ holds the SQLite
database, onboarded registry and model weights that a worktree would not see.
Cost if wrong: no branch isolation; mitigated by one commit per task.

## Pre-flight conflict scan

| Pair / task             | Shared surface                                     | Produces vs consumes                                           | Finding                                                                                                                |
| ----------------------- | -------------------------------------------------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| 1 & 6                   | inference.py plate path                            | 1 produces consensus plate_text; 6 consumes indexed detections | Clean — 6 ordered after 1                                                                                             |
| 1 & 3                   | inference.py per-camera state dicts                | 1 adds voter dict; 3 adds dark-frame + re-anchor state         | Both touch`reset_camera_state`; ordered 1 then 3, task 3 brief notes the voter exists                                |
| 2 & 6                   | matching.spacetime_plausible, geo.time_group       | both consume; neither mutates                                  | Clean                                                                                                                  |
| 3a & 6                  | tracking.py MAX_TRACKS vs indexing volume          | 3a caps tracks; 6 replays a full loop                          | Cap is per-camera live state, not stored detections — no conflict                                                     |
| 3b & 8b                 | counted_this_loop vs console counters              | 3b produces; 8b consumes                                       | Clean — 8 ordered last                                                                                                |
| 4b & 1                  | pipeline watchlist prefilter vs consensus plate    | 1 changes plate_text before matching                           | Clean — prefilter operates on whatever text arrives                                                                   |
| 4a & 2                  | retention deletes evidence; clones cite detections | 4a must not delete alert-referenced evidence                   | Constraint already stated in task 4a                                                                                   |
| 5 & 8c                  | baselines endpoint vs console panel                | 5 produces; 8c consumes                                        | Clean — ordered                                                                                                       |
| 7 & assistant intents   | retrieval.resolve before intent routing            | 7 modifies existing routing                                    | Risk: could regress existing intents; task 7 brief mandates all existing intents keep working and self-check covers it |
| Task 1 self-consistency | voter vs inference wiring                          | consistent                                                     | Clean                                                                                                                  |
| Task 2 self-consistency | same-camera guard vs time-group guard              | both stated                                                    | Clean                                                                                                                  |
| Task 3 self-consistency | 4 sub-items each with self-check                   | consistent                                                     | Clean                                                                                                                  |
| Task 4 self-consistency | 3 sub-items, config additions stated               | consistent                                                     | Clean                                                                                                                  |
| Task 5 self-consistency | MIN_SAMPLES vs stdev floor both stated             | consistent                                                     | Clean                                                                                                                  |
| Task 6 self-consistency | network-free self-check vs network indexing        | explicitly separated                                           | Clean                                                                                                                  |
| Task 7 self-consistency | BM25 vs trigram fallback both specified            | consistent                                                     | Clean                                                                                                                  |
| Task 8 self-consistency | needs snapshot endpoint, which it creates          | stated in 8a                                                   | Clean                                                                                                                  |

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
Task 5: review spec OK, carried-forward differencing fix verified correct
  (per-bucket totals, counts_by_class <= total invariant holds, loop reset and
  tracker restart both non-negative). Quality: Changes needed.
Task 5: fix round 1 dispatched — Critical (the `quiet` branch short-circuits
  the z-score and reports "road may be blocked" on readings its own evidence
  places inside the normal band; fires on most quiet cameras every few
  minutes) plus Important (5 legacy cumulative traffic_stats rows in the live
  database inflate cam15's hour-18 mean 4x and stdev 23x, so a genuine
  ten-fold spike reads as normal — on exactly the demo cameras and hour).
Task 5: minor (deferred): class breakdown lost on the tracker-restart branch
  (total takes the whole cumulative, class deltas all <= 0 and filtered out).
Task 5: minor (deferred): anomalies endpoint judges the most recent bucket per
  camera with no freshness filter, so a stale bucket is labelled current.
Task 5: minor (deferred): the observed reading is included in the history its
  own baseline is learned from; negligible at n>=5.
Task 5: minor (deferred): _apply_added_columns reuses one cached Inspector
  across the loop; harmless for two independent columns, would misbehave if a
  future column depended on seeing a prior ALTER.
Task 5: fix round 1/5 (2 addressed, 0 open; commits 52f518e..5a360a0).
  Re-review swept observed 0..100 and confirmed bands are monotonic with no
  gap; the useful busy-road quiet case still fires at z=-18.
Task 5: complete (commits e3a54a8..5a360a0, review clean)
Task 5: minor (deferred): the legacy-row discriminator depends on the
  migration's DEFAULT 0; a future repopulation from another source leaving
  that column zero would silently drop real rows. Documented in the docstring.
Task 7: review spec OK, architectural rule upheld (no answer text is built
  from index contents; every fact comes from a SessionLocal read, and _search
  drops matches whose row cannot be read rather than describing them).
  Honesty verified in live answer strings. All 10 existing intents regression-
  checked, none broken. Quality: Approved with 1 Important.
Task 7: fix round 1 dispatched — Important (intent words carry maximum idf into
  the coverage denominator and sink the mention, so "is cam11 down" does not
  scope even though the resolution note instructs the operator to name the id
  directly; "dolatpara" alone resolves cleanly).
Task 7: minor (folded into fix round 1): _resolution_note uses the index-cached
  label rather than the freshly read row; resolve() swallows all exceptions
  with no log line.
Task 7: fix round 1/5 (1 Important + 2 minors addressed, 0 open;
  commits 016f82f..d76e632). Re-review confirmed the safety properties held:
  unrelated and generic strings still resolve to nothing, and all ten existing
  intents still answer estate-wide without wrongly scoping to one camera.
Task 7: complete (commits 5a360a0..d76e632, review clean)
Task 7: minor (deferred): watchlist ids are bare integers, so in principle a
  query that is essentially one digit could exact-match a watchlist row. Did
  not manifest in testing; both confidence floors must pass first.
Task 6: review spec OK; all three correctness properties independently
  verified (scene-time-only; no cross-group leak under adversarial
  interleaving cam04-cam08-cam14-cam09-...; caps bite, 6,000 detections mined
  in 0.16s, 20,000 truncated to 4,000). Quality: Changes needed, 4 Important.
Task 6: fix round 1 dispatched — Important 1 (unbounded chain length: 20,000
  synthetic detections produced a single 1,500-hop journey spanning 12.5h of
  recorded time at confidence 0.95, which would weld distinct vehicles into
  one maximum-confidence claim on real footage); Important 2 (a zero mining
  result re-mines plus DELETE+commit on every request, live now because
  mining currently returns nothing); Important 3 (min_similarity/min_hops/
  limit silently ignored when the store is non-empty); Important 4 (the
  exclusion report is computed then discarded, so the docstring's honesty
  claim has no caller-visible surface).
Task 6: minor (folded into fix round 1): estimate_loop_length returns
  join-to-loop-point presented as the loop length; a stale comment at
  loop_index.py:399; index_camera does not fail fast on an unstarted engine.
Task 6: note: console does not yet render journeys; Task 8 covers that.
Task 6: fix round 1/5 (4 Important + 3 minors addressed; 2 NEW Important
  found in the fix diff, commits 32076d3..04e5517). Chain cap verified:
  12-hop chain now 0.502 vs two-hop 0.95, truncation flagged, all of
  properties 6-9 re-verified without regression.
Task 6: fix round 2 dispatched — NEW Important A (SQLAlchemy JSON columns
  store Python None as JSON 'null', so embedding.is_(None) matches nothing;
  15,710 of 32,785 live detections affected; the new honesty surface
  publishes comparable=714/excluded=0 when the truth is 634/80, and the CLI
  contradicts itself a line apart). NEW Important B (a refresh with narrow
  parameters deletes and replaces the whole group, so one caller's
  min_hops=9 left every other reader's default view at 0).
  Plus three honesty consequences and a repo-wide grep for the same JSON-null
  pattern on other JSON columns.
Task 6: fix round 2/5 (2 Important + 3 honesty + 2 minors addressed, 0 open;
  commits 04e5517..7695b0c). Re-review independently confirmed the JSON-null
  grep was complete (only Detection.embedding is ever NULL-tested among JSON
  columns; a third instance in /api/vehicles/{id}/similar was also fixed,
  which had been loading 15,710 vectorless rows per call), and that a narrow
  refresh no longer shrinks the shared store.
Task 6: Ruling: I ran the real indexing pass and it anchored the scene clock
  on 0% of 27,000 indexed detections, so no journey can form on real data.
  Cause is InferenceEngine._anchor_clock's opportunistic queue-slack skip,
  which is correct for the live path but always fires during indexing because
  index_camera submits blocking and keeps the queue full. This is load-bearing
  for the whole task, so it enters the fix loop as round 3 rather than being
  parked. Cost if wrong: the live path's measured 71%-frame-loss protection
  must not regress, so the fix must preserve it provably.
Task 6: fix round 3/5 — the clock policy split works: index went from 0 to
  10,331 clocked detections, live path provably unchanged, pinned in both
  directions. Auto-committer captured the work as 659cf78 (content verified
  correct and complete, self-checks pass).
Task 6: Ruling: two problems surfaced from the real data. First, a single OCR
  misread anchors a whole stream (spans show 2025-06-14, 2026-06-24,
  2028-06-13 where the recordings cluster in June 2026), which mis-times every
  detection on that camera and feeds route, clone and journey reasoning. That
  enters fix round 4 as corroborated anchoring. Second, only cam04 and cam13
  share a clock-readable window and it is ~4 minutes wide, so the sandbox may
  simply not contain a demonstrable cross-camera journey. I have instructed the
  implementer to measure and report that honestly rather than engineer around
  it. Cost if wrong: we ship "no journey found, here is why" instead of a
  discovered journey - which is consistent with everything else we claim about
  this grid.
Task 6: Ruling: rounds 4-5 normally take a fresh implementer on a higher tier.
  Resuming the same agent instead - it is already on the most capable model
  and holds the indexing context, and the loop is not stuck (each round has
  fixed real defects and surfaced new ones from real data). Cost if wrong:
  loses the fresh-eyes benefit the escalation is meant to buy.
Task 6: fix round 4/5 code landed as 808fdef (inference.py, scene_clock.py:
  corroborated anchoring + tightened plausibility). Report pending — the
  implementer is running the cam04/cam13 indexing pass to answer the journey
  question honestly.
Ruling: dispatching Task 8 while Task 6's round-4 report is pending. The file
  sets are disjoint (Task 8 = netra/web/* + one snapshot endpoint in app.py;
  round 4 = inference.py + scene_clock.py, already committed). Cost if wrong:
  a merge conflict on app.py that git will surface, not silently corrupt.
User direction (05 Sep): skip deployment; order is Task 8 -> VLM attributes as
  Task 9 -> sweep remaining issues.
Task 9 (VLM attributes) added at user direction; brief written to
  task-9-brief.md. Ordered after Task 8.
Ruling: Task 9 introduces transformers 4.57.6, timm, einops, accelerate,
  safetensors — a deliberate exception to the no-new-heavyweight-deps
  constraint. There is no stdlib route to a vision-language model, and the
  user explicitly chose this feature. Installed into .venv. Cost if wrong:
  ~2 GB of dependencies and a model download; no effect on any existing
  module's self-check, all of which import nothing from these packages.
Florence-2-base feasibility probe dispatched (load + caption on real grid
  crops) before Task 9 is dispatched, so the task is not sent against an
  unproven model.
Florence-2-base probe: PASSED. Load 8.1s, 452 MiB, 0.9-2.6s per crop on
  RTX 5050. Two mandatory workarounds for transformers 4.57.6 recorded in the
  Task 9 brief: attn_implementation="eager" and generate(use_cache=False).
  Real captions from cam13 crops carry colour, body type and markings.
Task 8: report landed, commit pending. All 17 console endpoints verified 200
  (snapshot 404 on unknown camera as expected); node --check passes.
Task 8: minor (deferred, cross-task): plate_vote's voter_count is computed but
  never persisted on Detection, so the console shows plate_chars instead.
  Surfacing it needs one additive column. Belongs to the issues sweep after
  Task 9, not to Task 8.
Task 8: minor (deferred): /api/detections was not serialising track_id or
  scene_time despite the columns existing — fixed in this task, noted here so
  the final review knows it was a latent gap from Task 3/6, not new scope.
Task 8: review spec OK (8a-8d). XSS audit: every API-sourced interpolation
  escaped; a real injection (<img onerror> as a zone name, attribute breakout
  in classes) rendered inert. Shape verification: every field the JS reads
  exists in the live JSON. Quality: Approved with 2 Important.
Task 8: fix round 1 dispatched — Important 1 (no in-flight de-duplication on
  the cold snapshot grab; N clicks = N 17-second ffmpeg processes on a 40-thread
  pool) and Important 2 (the expensive snapshot GET carries no require()),
  plus Minor (delZone(${z.id}) not esc()'d).
Task 8: minor (deferred): snapshot cache never age-evicts (~10 MB steady state
  at 30 cameras); UI cannot force refresh=true within the TTL; Traffic tab is
  refresh-on-demand, not auto-polled.
Auto-committer note: 77c2519 "feat: add vision-language model..." is a FALSE
  message. It contains no VLM code — only the ledger, the Task 8 review
  package, the Task 9 brief, and 17 lines of Task 6's in-flight round-4
  inference.py. The message was invented from the brief's filename. cd29d31
  is ledger-only. Neither is a task commit. Self-checks re-run after the sweep.
Task 8: fix round 1 landed as 90600c3 (per-camera lock + cache re-check inside
  the lock; require("read") on the snapshot GET; delZone esc()'d).
  Implementer measured 4 concurrent cold requests -> exactly 1 ffmpeg.
  Scoped re-review dispatched.
Task 8: minor (deferred, platform-wide): the zone editor loads the still via
  <img src>, which cannot send X-API-Key, so with access control enabled the
  snapshot 401s in the browser while the rest of the console works. Needs a
  cookie session or signed short-lived URL for the console. Belongs to the
  issues sweep after Task 9.
Task 8: fix round 1/5 (3 addressed, 0 open; commits ddf5225..90600c3).
  Re-review independently measured max 1 concurrent ffmpeg for 4 cold requests
  on cam13, and 2 for two different cameras (per-camera lock, not global).
  Lock releases on exception via `with`; cache re-check inside the lock cannot
  serve a frame past TTL.
Task 8: complete (commits 808fdef..90600c3, review clean)
Task 6: fix round 4/5 landed as 84888d7 (corroborated anchoring; year window
  tightened; loop cut voids a pending reading). Measured on 4 cameras x 200s:
  cam13 corroborated to 2026-06-14T07:49:15 from two readings 1.2s apart;
  cam04's single 0.43-confidence read refused; cam14/cam15 0%.
Task 6: Ruling: NO cross-camera journey exists in this sandbox. Verdict is a
  data limitation, not a defect - the miner finds a three-camera journey in
  synthetic data; on the grid only ~1 camera in 4 has a legible clock and the
  legible windows do not overlap. The submission states this plainly.
  Cost if wrong: we forgo one demo moment; we do not fabricate one.
Task 6: Ruling (carried to the sweep, load-bearing): ~11,000 detections hold
  pre-fix uncorroborated scene_time values including the 2025 / 2026-06-24 /
  2028 spans. Route, clone and journey reasoning over the existing store is
  partly over known-bad timestamps. They must be nulled or re-indexed before
  any elapsed-time claim is made from this store. Not deleted by the agent.
Task 6: minor (deferred): corroboration cannot catch a systematic misread
  (same OCR error twice); only an independent clock could, and none exists.
Task 6: minor (deferred): clocked-detection coverage fell from 69-100% to
  0-23% as the price of correctness; the old figures included provably wrong
  spans. Console viewers must not read the drop as a regression.
Task 6: round-4 scoped re-review dispatched over 659cf78..84888d7 (the
  auto-committer split round 4 across 808fdef, 77c2519 and 84888d7).
Task 9 (VLM attributes): implementer dispatched in parallel with the Task 6
  reviewer. Ruling: a read-only reviewer is not an implementer, so this does
  not breach the one-implementer rule; the reviewer was told which files
  Task 9 may touch. Cost if wrong: the reviewer reads an uncommitted edit in
  a file it was told to ignore.
Task 10 (issues sweep) brief written from the 27 deferred/carried ledger
  items, triaged A (must: uncorroborated scene_time isolation + purge tool,
  plate vote persistence, console snapshot under auth, tracker-restart class
  breakdown) / B (should) / C (if time). Gated on Task 9 closing.
History rewrite: the Task 9 implementer rebased the auto-committer's sweep
  (32e93a1) away. Earlier ledger SHAs from 808fdef onward are stale; the
  commits exist under new hashes (round-4 corroboration is now acaa072).
  Content verified intact via self-checks. The uncommitted A0/C4 edits to
  task-10-brief.md were lost in that reset and have been re-applied.
Task 9: landed as c189737 (sweep) + 200d315 + c89d4d1. Report present. Live
  check: 5 cameras ~95s, 0 dropped frames, attributes queued 14 / processed
  14 / dropped 0, model load 9.9s, 453 MiB, 908-985 ms per crop warm.
  Review package generated over acaa072..c89d4d1.
Session restart: previous Claude Code process exited with the Task 9 reviewer
  mid-run (no result). Task 9 implementer had already landed (c89d4d1) and
  reported. Recovery per ledger + git: HEAD c89d4d1, tree clean, all
  self-checks pass. Task 9 review re-dispatched fresh.
Task 10 brief: added B6 (FP16 via half=True on YOLO predict and .half() on
  the ReID backbone; TensorRT/INT8 explicitly declined - detection is not the
  bottleneck and sm_120 TensorRT is a day's risk) and B7 (HLD must distinguish
  tier-1 scanning capacity, 150-200 cams/node, from full-pipeline capacity
  under admission control, ~8 escalated busy junctions at 0% loss).
  Ruling: Cloudflare tunnel on the local 5050 is the deployment route when the
  user asks for it; deferred per their "skip deployment for now".
Task 9: review spec OK, quality Approved, 0 Critical / 0 Important / 3 Minor.
  Tiering verified airtight (inference.py untouched; alert-path extraction
  after persist+broadcast+notify; queue maxsize=2 with 5 submits -> 3 dropped,
  0.0000s blocking). Parser honesty verified incl. monochrome mask. ReID third
  signal bounded +0.03/-0.05, applied only after threshold/sort/ambiguity on
  raw scores. Retrieval 5/5 exact vehicle resolutions from live DB, facts from
  SQL. Live: 5 cameras ~100s, dropped 0, attributes 8/8, queue drained.
Task 9: complete (commits acaa072..c89d4d1, review clean)
Task 9: minor (carried to Task 10 B8): pipeline.py has ATTRIBUTE_BROADCAST_
  BOUND_S and the whole attribute-worker __init__ block duplicated verbatim
  (bad patch application). Harmless but dead code in the file that most
  needs to be readable.
Task 9: minor (carried to Task 10 B9): single-word descriptions ("yellow")
  enter the BM25 vehicle corpus as noise keys; add a minimum-informativeness
  gate.
Task 9: minor (deferred): three unescaped numeric interpolations in app.js
  (Float confidence, two Integer PK selectors) - not exploitable.
Task 10: dispatched. BASE c89d4d1. Brief: A0-A4 mandatory, B1-B9 expected, C1-C4 if time.
Final review merge base pinned: ca2eac1 ('Eliminate frame loss under load', last pre-plan commit; 85b6515 no longer exists after the history rewrite).
Task 10: landed as b995bfb (Tier A) + 04c14d6 (Tier B) + 8d8cb28 (Tier C), all
  four C items done. 19 self-checks pass (pipeline and core.timing new);
  run.py --check READY; 15 console endpoints 200; node --check clean; 30s live
  runs dropped=0 after A0 and after B6. A1 dry-run: 13,271 rows would be
  nulled, 4 corroborated rows written by the new path. B6 controlled bench
  17.3 -> 12.1 ms/pass at TIER2_IMGSZ (uses quantize=16; half=True is
  deprecated in this ultralytics). A3 verified 401/200 against a real keys
  file; data/api_keys.json deleted and absent. No auto-committer interference.
  Review package generated over c89d4d1..HEAD.
Task 10: review dispatched. Final whole-branch package pre-generated over ca2eac1..HEAD (34 commits) - regenerate if Task 10 needs a fix round.
