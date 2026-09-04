# Task 4 — Retention and watchlist scale — report

Status: **DONE**

## 4a. Retention (`netra/core/retention.py`, new)

`prune_evidence(max_bytes, max_age_days, evidence_dir, session_factory,
dry_run)` scans the evidence directory once with `os.scandir`, sorts by mtime
oldest-first, applies the age rule and then the size budget. `prune_detections
(max_rows, keep_days, ...)` deletes the oldest rows beyond the cap.
`storage_report()` returns evidence file count/bytes, detection/alert/zone
counts and the configured budgets with percent-of-budget figures.

**The protection rule.** `protected_evidence()` collects the basenames of
`Detection.evidence_path` joined to unacknowledged `Alert` rows, plus
unacknowledged `ZoneEventRow.evidence_path`. `evidence_path` is a URL path
(`/evidence/<file>`) while files live in `config.EVIDENCE`, so the join is on
basename. A protected file is skipped by *both* rules and counted in
`retained_protected` / `retained_protected_bytes`, separately from
`deleted_expired` and `deleted_over_budget` — an operator can see the rule
working, and can see when the budget cannot be met because of it.

`prune_detections` applies its two protections (any alert-referenced detection;
anything inside `keep_days`) as SQL filters rather than post-selection, so a
table that is entirely protected deletes nothing and reports `still_over_cap`
honestly instead of looping. Both pruners take `dry_run`.

Config added to `netra/config.py`, each with a comment giving the reasoning:
`EVIDENCE_MAX_BYTES` (5 GiB), `EVIDENCE_MAX_AGE_DAYS` (7),
`DETECTION_MAX_ROWS` (2,000,000), and `DETECTION_KEEP_DAYS` (1) — the last is
the floor `keep_days` needs and was not in the brief's list.

Endpoints: `GET /api/storage` (`read`), `POST /api/storage/prune?dry_run=`
(`manage`, audited with the deleted and retained counts).

`_self_check()` builds a temporary directory and a throwaway SQLite file
(`tempfile.TemporaryDirectory` + a fresh engine — never `data/netra.db` and
never `data/evidence`), and covers: the protected set; the age rule with two
protected files surviving; the size budget deleting oldest-unprotected-first
and stopping; a dry run leaving the disk untouched; the alert-referenced
protection in `prune_detections` (cap of 1 against 3 rows deletes exactly the
one unreferenced row); a prune with nothing eligible; `keep_days` protecting
recent rows far over the cap; and `storage_report`. The engine is disposed in a
`finally` so a failed assert still releases the SQLite file on Windows.

## 4b. Watchlist prefilter (`netra/analytics/matching.py`, `netra/pipeline.py`)

`plate_windows()` + `WatchlistIndex` live in `matching.py`, next to the
similarity function whose behaviour they must not change. The index is keyed on
the **confusion-folded** plate (comparison happens after folding, so an index on
raw text would miss every OCR confusion the matcher exists to absorb) and on
**every 4-character window**, not the first four (`plate_similarity` matches an
observed read that is a *substring* of the target). A detection is scored
against the buckets of every window of its own folded plate, plus a short
bucket of entries under four folded characters that is always considered.

The pipeline rebuilds the index inside the same 30-second reload block that
rebuilds the cache, so the index can never describe a stale watchlist.
`_check_watchlist` now iterates `candidates(det.plate_text)`; full `score_match`
still decides every candidate. `WatchlistIndex.stats()` is surfaced in pipeline
status.

Self-check in `matching.py` proves the superset property with the brief's own
case — observing `AB1234` against watchlist `GJ01AB1234`, which a naive
first-four-characters index buckets under `GJ01` and would silently lose — plus
a folded case (`GJ0IAB1Z34`), the short-read and short-entry paths, and a
deterministic 200-entry / 40-read sweep asserting that every pair `score_match`
would alert on survives the prefilter, while fewer than half the entries are
scanned.

**Honest ceiling, pinned by an assertion.** `plate_similarity` also has a
positional-agreement branch, and two plates can agree on 70% of characters with
the mismatches spread so they share no 4-character window (`GJX1AX12X4` vs
`GJ01AB1234`). Such a pair scores above zero and is not a candidate. It needs
three scattered OCR errors in one read, and that pair does not clear the alert
threshold — asserted in the self-check so the trade-off is visible rather than
discovered. The alternative, a 2-character index, is provably complete but
buckets 10,000 entries into 1,296 keys and prefilters almost nothing.

## 4c. Black-frame short-circuit (`netra/analytics/inference.py`)

`DARK_LUMA_THRESHOLD = 18`, `DARK_FRAME_LIMIT = 60`, `DARK_RECHECK_FRAMES =
300`. `mean_luma()` uses BT.601 weights over a strided sample
(`img[::8, ::8]`, a numpy view rather than a greyscale copy of every frame), so
measuring darkness costs microseconds.

`_dark_gate(camera_id)` runs immediately after the `degraded` check and returns
False while a dark camera is not due for its probe; `_note_luma(camera_id, img,
found)` is called on both the empty-boxes path and the has-detections path. A
frame counts towards the streak only if it is dark *and* empty — a dark scene
still yielding vehicles is a camera doing its job. Any lit or productive frame
clears the streak and un-marks the camera, so recovery at dawn is automatic and
needs no operator action. State lives in `_dark_streak` / `_dark_cameras` /
`_dark_skipped`, cleared by `reset_camera_state`. `stats["dark_cameras"]` and
`stats["dark_frames_skipped"]` are counters; `dark_cameras()` is surfaced in
pipeline status as `dark_cameras`.

A new `_self_check()` in `inference.py` (no model, no GPU, no threads) covers
the luma measurement, the limit boundary, the recheck cadence, recovery on a
probe frame, a dark-but-productive camera never being short-circuited, and a
broken streak restarting from zero.

## Verification

All green:

```
python -m netra.core.retention        retention self-check passed
python -m netra.analytics.matching    matching self-check passed
python -m netra.analytics.plate_vote  plate_vote self-check passed
python -m netra.analytics.tracking    tracking self-check passed
python -m netra.analytics.inference   inference self-check passed
python -c "from netra.api.app import app"   app ok
```

`PIPELINE.status()` and `retention.storage_report()` were also called against
the live database read-only; nothing under `data/` was modified.
