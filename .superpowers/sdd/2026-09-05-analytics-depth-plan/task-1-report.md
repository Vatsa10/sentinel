# Task 1 report - Multi-frame plate voting

**Status:** DONE

## What was built

### `netra/analytics/plate_vote.py` (new)

- `PlateObservation(text, confidence, pts_ms)` dataclass.
- `PlateVoter` - one instance per camera, holding observations per `track_id`.
  - `add(track_id, text, confidence, pts_ms)` - normalises to uppercase
    alphanumerics, discards reads under `MIN_PLATE_CHARS = 4`, appends, and
    drops the oldest past `MAX_OBSERVATIONS_PER_TRACK = 20`.
  - `consensus(track_id) -> (text, confidence, observation_count) | None`.
  - `forget(track_id)`, `retain(live_track_ids)`, `reset()`, `stats()`.

Voting algorithm, as specified:

1. Observations are bucketed by length; the modal length wins, ties broken by
   summed confidence. Reads of other lengths are excluded from the vote (they
   are misaligned) but still counted in `observation_count`.
2. Per position, confidence is accumulated per *confusion-folded* candidate
   (`matching.CONFUSIONS`). The emitted character is the highest-confidence
   **raw** observation within the winning group, so `GJ01AB1234` emits `G`,
   never `6`.
3. Confidence is the mean per-position winning share - unanimous ~1.0,
   contested lower.
4. Fewer than 2 observations returns the single read as-is with its own
   confidence.

If every read disagreed on length (so no cohort reaches 2), the most confident
single read is returned rather than a fabricated consensus. That fallback is
marked `ponytail:` with its ceiling named.

### `netra/analytics/inference.py` (wired)

- `self._plate_voters: dict` alongside `self.trackers`, following the existing
  per-camera dict pattern (`self._clocks`, `self._clock_attempts`).
- New `_vote_plates(frame, tracker, detections)`, called from `_process`
  immediately after `tracker.update(...)` on `anpr` cameras only - it must run
  after tracking because `track_id` does not exist before that point.
  It feeds each read to the voter, then overwrites `det.plate_text`,
  `det.plate_conf` and `det.plate_chars` with the consensus when the track has
  2+ observations.
- `plate_votes` added to `self.stats`.
- `reset_camera_state` pops the camera's voter.
- `voter.retain(tracker.tracks.keys())` after each frame forgets tracks the
  tracker has already expired, so voter memory follows track lifetime.

## Design notes

- **Consensus propagates to frames where OCR failed.** A detection on a track
  that already has a consensus receives it even if this frame produced no
  read. This is deliberate: it is the same tracked vehicle, and it gives
  watchlist matching a plate on frames where the plate was momentarily
  occluded. The confidence carried is the vote's, not a fabricated 1.0.
- **Cost.** `consensus()` is at most 20 observations x ~10 positions of dict
  arithmetic per detection per frame, and runs only on `anpr` cameras where
  `PLATE_MAX_PER_FRAME` already caps work. No model calls, no I/O. This cannot
  starve detection.
- **Memory.** Bounded twice over: 20 observations per track, and tracks are
  dropped as soon as the tracker expires them.

## Verification

```
.venv/Scripts/python.exe -m netra.analytics.plate_vote   -> plate_vote self-check passed
.venv/Scripts/python.exe -m netra.analytics.tracking     -> tracking self-check passed
.venv/Scripts/python.exe -m netra.analytics.matching     -> matching self-check passed
.venv/Scripts/python.exe -c "from netra.analytics.inference import InferenceEngine; print('ok')" -> ok
```

Self-check covers: unanimous reads; a minority misread outvoted; a *confident*
minority outvoted by a consistent majority; differing lengths not corrupting
positions; confusion-folded grouping (`GJO1AB1234` + `GJ01AB1234` agreeing to
1.0 while emitting a real plate with no `6`/`8`); folding not rewriting a
letter-dominant position; single observation returned unchanged; sub-4-char,
empty and `None` reads ignored; separator/case normalisation; the 20-cap
enforced oldest-first; unknown track returns `None`; `forget`/`retain`/`reset`.

## Concerns

- The length-mismatch fallback is the known ceiling (marked `ponytail:`): a
  track whose reads each dropped a different character gains nothing from
  voting. Edit-distance alignment would fix it and is not implemented.
- `_vote_plates` is exercised only through the module self-check on
  `PlateVoter`; the inference wiring itself has no runnable check, as
  `_process` needs models and frames. Consistent with the rest of that module.
