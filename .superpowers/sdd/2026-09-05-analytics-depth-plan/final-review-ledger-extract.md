# Ledger extract for the final review — deferred minors, parked findings, rulings

3:Ruling: implementing on main rather than a worktree — every commit this
30:Ruling: no conflict requires a plan change before execution. The one risk
37:Task 1: Ruling: minor 2 (fallback reports total observation count, so an
41:Task 1: minor (deferred): retain() does not run on frames with zero
44:Task 1: minor (deferred): stats["plate_votes"] counts detection-frames that
46:Task 1: minor (deferred): plate_bbox left None on frames where consensus is
51:Task 3: minor (deferred): flag_ambiguity measures against the top score only,
53:Task 3: minor (deferred): /track lacks the set-level `ambiguous` summary that
55:Task 3: Ruling: minor 2 (pipeline.flush_traffic_stats persists the cumulative,
58:  Carried into Task 5's dispatch as a required fix rather than parked.
66:Task 2: minor (deferred): core/timing.py carries no _self_check of its own,
69:Task 2: minor (deferred): len(plate) < 6 guard is undocumented in the
71:Task 2: parked — Ruling: clock-anchor confidence is not surfaced in a clone
88:Task 4: minor (deferred): acknowledged-alert evidence can be deleted while its
96:Task 4: minor (deferred): known throughput ceiling — correct prefilter costs
101:Task 4: minor (deferred): candidates() documents "Order is stable" while
112:Task 5: minor (deferred): class breakdown lost on the tracker-restart branch
114:Task 5: minor (deferred): anomalies endpoint judges the most recent bucket per
116:Task 5: minor (deferred): the observed reading is included in the history its
118:Task 5: minor (deferred): _apply_added_columns reuses one cached Inspector
125:Task 5: minor (deferred): the legacy-row discriminator depends on the
145:Task 7: minor (deferred): watchlist ids are bare integers, so in principle a
184:Task 6: Ruling: I ran the real indexing pass and it anchored the scene clock
190:  parked. Cost if wrong: the live path's measured 71%-frame-loss protection
196:Task 6: Ruling: two problems surfaced from the real data. First, a single OCR
207:Task 6: Ruling: rounds 4-5 normally take a fresh implementer on a higher tier.
216:Ruling: dispatching Task 8 while Task 6's round-4 report is pending. The file
224:Ruling: Task 9 introduces transformers 4.57.6, timm, einops, accelerate,
239:Task 8: minor (deferred, cross-task): plate_vote's voter_count is computed but
243:Task 8: minor (deferred): /api/detections was not serialising track_id or
254:Task 8: minor (deferred): snapshot cache never age-evicts (~10 MB steady state
266:Task 8: minor (deferred, platform-wide): the zone editor loads the still via
281:Task 6: Ruling: NO cross-camera journey exists in this sandbox. Verdict is a
291:Task 6: minor (deferred): corroboration cannot catch a systematic misread
293:Task 6: minor (deferred): clocked-detection coverage fell from 69-100% to
299:  reviewer. Ruling: a read-only reviewer is not an implementer, so this does
325:  Ruling: Cloudflare tunnel on the local 5050 is the deployment route when the
342:Task 9: minor (deferred): three unescaped numeric interpolations in app.js
