# Task 3 report — Bounded state and correctness guards

Status: DONE

## 3a. Bounded tracker state (`netra/analytics/tracking.py`)

`MAX_TRACKS_PER_CAMERA = 300`. Enforcement lives at the end of `_expire`, so it
runs on the same path as timeout expiry and nowhere else. Once the timeout pass
has run, any excess over the cap is removed by ascending `last_pts_ms` — least
recently seen first — and the number removed is added to a new `dropped_tracks`
counter, surfaced by `stats()`.

Placing it in `_expire` means the cap is applied *before* the frame's own
detections are associated, so `len(self.tracks)` can briefly sit above the cap
until the next update settles it. That is deliberate: trimming after
association could drop a track the current frame just matched, discarding a
live vehicle in favour of a bookkeeping invariant. The self-check calls
`_expire` once after the loop to assert the settled state, with a comment
saying why.

## 3b. Loop-boundary duplicate guard (`netra/analytics/tracking.py`)

`CameraTracker` gains `counted_this_loop` and `loops_seen`. `update()`
increments `counted_this_loop` alongside `total_count`. `reset()` clears the
tracks, zeroes `counted_this_loop` and increments `loops_seen`; `total_count`
is untouched, because those vehicles genuinely were observed. `stats()` exposes
`total_counted`, `counted_this_loop` and `loops_seen` together, so a headline
figure can always be divided by the number of replays that produced it. The
`reset()` docstring states the distinction explicitly, in the terms the brief
asked for — an evaluator reading "4,893 vehicles" can see whether that is one
playthrough or six.

## 3c. Scene-clock drift re-anchoring

`ClockAnchor.age_s(pts_ms)` returns `(pts_ms - self.pts_ms) / 1000.0` — stream
seconds of extrapolation since the reading. It is signed, and the self-check
pins the negative case (a stream that rewound before `reset_camera_state` ran
must not read as old and trigger a re-anchor).

`CLOCK_REANCHOR_AFTER_S = 900.0` in `inference.py`. `_anchor_clock` no longer
returns early merely because the camera has an anchor; it returns early when
the existing anchor is *younger* than the threshold. Everything after that
point is unchanged: the queue-slack check and `CLOCK_ATTEMPT_LIMIT` gate both
apply to a re-anchor exactly as to a first anchor, so re-anchoring cannot
starve detection.

Failure semantics, per the resolved ambiguity: a failed read leaves
`self._clocks[cam]` alone (a stale anchor beats no anchor) and the attempt
still counts, so a camera whose overlay has become unreadable stops retrying.
On **success** the attempt counter is reset to zero. This makes
`CLOCK_ATTEMPT_LIMIT` a per-anchoring-window budget rather than a
per-connection one — otherwise a camera that anchored on its fourth attempt
could never re-anchor at all, and the drift the task exists to fix would go
uncorrected on exactly the cameras whose overlays are hardest to read. The
budget stays bounded either way: at most four OCR attempts per 900s window.

The "no legible overlay" log line now distinguishes the two cases, saying
whether it is keeping an existing anchor or leaving the camera with no scene
time.

## 3d. Re-identification ambiguity guard

`AMBIGUITY_MARGIN = 0.02` and a new `flag_ambiguity(scored)` helper in
`reid.py`, which takes a similarity-sorted list of dicts and sets `ambiguous`
and `ambiguity_note` on every row. A row is ambiguous when the runner-up is
within the margin of the top score *and* that row is itself within the margin
of the top — so the whole confused cluster is flagged and a clearly-lower
candidate further down is not. Nothing is dropped. Both keys are always
written, including on a single-result list, so no consumer has to tell
"unambiguous" apart from "never checked". Comparison uses a `1e-9` epsilon so a
gap of exactly the margin resolves to ambiguous rather than to whichever side
binary floats happen to round it.

`rank_candidates` truncates to `top_k` and then flags, because ambiguity is a
statement about what the caller is shown.

### API route chosen

I applied the same rule in the endpoint via the shared helper, rather than
rewriting `GET /api/vehicles/{detection_id}/similar` to call
`rank_candidates`.

Reason: the endpoint is not a thin wrapper around ranking. It takes a
caller-supplied `min_similarity` (`rank_candidates` uses the module-level
`SIMILARITY_THRESHOLD`), it joins each candidate's camera and computes
`distance_km`, `elapsed_s`, `plausible`/`plausibility` and `same_time_group`,
and it returns flat JSON dicts rather than `{"detection": ORM row}`. Switching
to `rank_candidates` would mean ranking, then re-walking the results to attach
all of that anyway — the same work with an extra pass and a divergent
threshold. Factoring the ambiguity rule into `flag_ambiguity` keeps the single
source of truth the concern actually needs (the margin and the note live in one
place, in `reid.py`) without forcing the endpoint through a ranking function
whose shape does not fit it. If the endpoint's enrichment is ever pushed down
into `reid`, calling `rank_candidates` there becomes the right move.

The response now carries `ambiguous` and `ambiguity_note` on each match plus a
top-level `ambiguous` boolean, so the console can flag the whole result set
without scanning. `/api/vehicles/{id}/track` consumes the same dicts and
inherits the fields for free.

## Self-checks

Added to the existing `_self_check()` in each module:

- `tracking`: cap enforced at exactly `MAX_TRACKS_PER_CAMERA`, `dropped_tracks`
  equals the overflow, the surviving tracks are the most recently seen
  (`min(last_pts_ms) == 50.0`), and the counter is surfaced by `stats()`.
  Separately: `total_counted` survives `reset()`, `counted_this_loop` returns
  to zero and then counts the second loop independently, `loops_seen`
  increments.
- `scene_clock`: `age_s` at the anchor frame, forward, and negative (rewound).
- `reid`: lone result not flagged; two near-identical scores both flagged with
  a note while a distant third is not; a clear winner not flagged; a gap of
  exactly the margin flagged.

## Verification

All five commands pass:

```
python -m netra.analytics.tracking     -> tracking self-check passed
python -m netra.analytics.scene_clock  -> scene clock self-check passed
python -m netra.analytics.reid         -> reid self-check passed
python -m netra.analytics.plate_vote   -> plate_vote self-check passed
python -c "from netra.api.app import app"  -> app ok
```

`plate_vote` and `_plate_voters` were not touched.

## Concerns

- The `CLOCK_ATTEMPT_LIMIT` reset-on-success described in 3c is a change of
  meaning for that constant (per-window, not per-connection). It is documented
  in a comment at the assignment. Flagging it explicitly in case the intent was
  a hard per-connection ceiling on OCR attempts.
- `stats()` grew three keys. Nothing in `netra/web/` reads tracker stats by
  fixed key order, so this is additive, but the console does not yet *display*
  `loops_seen` / `counted_this_loop`. The data is exposed; surfacing it in the
  UI is a separate change and was not in scope.
