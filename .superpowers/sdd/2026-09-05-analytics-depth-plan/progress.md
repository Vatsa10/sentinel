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

