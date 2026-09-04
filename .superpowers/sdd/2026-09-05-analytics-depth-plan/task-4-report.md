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

---

# Fix round 1 — 4b completeness and a double-counted retention figure

## Finding 1 — the prefilter dropped genuine alerts (fixed)

The reviewer is right and my first report understated it. Two errors suffice,
not three, and the pair does alert: 8/10 positional agreement gives
`plate = 0.45`, and with class and colour agreeing — `car`/`white`, the common
case — appearance is 1.0 and `fused = 0.6·0.45 + 0.4 = 0.67`, over the 0.55
threshold. My assertion picked a *three*-error pair that happened not to alert
and I generalised from it. That was the real error: I asserted the ceiling I
had guessed instead of measuring the ceiling that existed.

**Root cause, derived rather than guessed.** An alert needs
`fused >= ALERT_THRESHOLD`; appearance contributes at most
`WEIGHTS["appearance"]`, so the plate score must reach
`p_min = (0.55 − 0.40)/0.60 = 0.25`, which on `plate_similarity`'s positional
curve is a minimum agreement of `_MIN_ALERTING_FRAC = 0.7111`. Over a span of
n characters that permits m mismatches, and m mismatches cut the agreeing
characters into at most m+1 runs, so the longest shared run is at least
`ceil((n−m)/(m+1))`. A shared q-character window is therefore guaranteed only
when `n > q·m + q − 1`. Evaluated over every span 4–199:

| window | spans where it is *not* guaranteed |
|---|---|
| q = 4 | **every span** — the original design was unsound at all lengths |
| q = 3 | 4, 5, 7, 8, 11, 14 only |
| q = 2 | none |

**Fix.** `INDEX_WINDOW` is now 3, with `FALLBACK_WINDOW = 2` used for exactly
those reads whose length the 3-window cannot prove, plus a `_forced` bucket of
entries whose own plate length is unsafe (the span is capped by the shorter of
the two plates, so a short *entry* is as dangerous as a short read). Reads
under 4 folded characters return only the short bucket, which is complete
because `plate_similarity` scores nothing below span 4 except an exact match
between two equally short plates. `_min_alerting_fraction()` is computed from
`ALERT_THRESHOLD` and `WEIGHTS`, so retuning the scoring cannot silently
invalidate the index.

**Brute-force verification, in `matching._self_check()`.** A 302-entry
watchlist (including a 3-character entry and an 8-character one, so the short
and forced buckets are exercised), swept over the *complete* one- and
two-error variant spaces of a plate, a 150-sample of the three-error space, the
two-error space of the awkward-length entry, and contiguous partial reads of 40
plates. Every `(observed, entry)` pair is scored, and every pair with
`is_alert=True` is asserted to be in `candidates(observed)`:

```
1,004 lookups, 1,383 alerting pairs, 0 dropped
```

Zero, not "few". The same sweep run against the old 4-character index loses
**48 of 761 alerting pairs (6.3%)** — consistent with the reviewer's 6.1%, and
proof the sweep has teeth. The derivation table above is itself asserted, q=4
included, so this cannot regress quietly.

## Selectivity traded away

Synthetic watchlist of 10,000 distinct Gujarat plates, 300 sampled lookups:

| window | largest bucket | mean candidates / lookup | share of list |
|---|---|---|---|
| q = 4 (before, lossy) | 298 | 270 | 2.7% |
| **q = 3 (after)** | **2,676** | **2,634** | **26.3%** |
| q = 2 (fallback path) | 10,000 | 10,000 | 100% |

End-to-end on the same watchlist: a full unfiltered scan of 10,000 entries
costs **55.1 ms**; the prefiltered check costs **14.6 ms** — a 3.8× reduction,
where the lossy version would have given roughly 35×. `candidates()` itself
costs 0.24 ms.

**Stated plainly:** for a read of 4, 5, 7 or 8 characters the filter returns
the entire list — every Gujarat plate shares the "GJ" 2-gram — so those reads
cost a full scan plus 1.4 ms of index work. Partial reads that short are not
rare on this grid, so a meaningful share of lookups get no benefit at all. That
is the price of not losing hits, and it is the right way round: a correct slow
filter is acceptable, a filter that quietly loses a watchlist hit is not. The
`ponytail:` comment on `WatchlistIndex` now says exactly this instead of the
claim it made before.

## Finding 2 — `retained_protected` double-counted (fixed)

A protected file reached by both rules in one call passed through `_remove`
twice and was counted twice; with `max_bytes=0, max_age_days=0` and two
protected files the figure read 4, and the bytes doubled. Nothing was ever
deleted wrongly, but the number is audited into `storage.prune`. `_remove` now
counts each protected basename once, and the self-check gained a case that
exercises both rules in a single call — the omission that let it through.

## Commands run

```
$ .venv/Scripts/python.exe -m netra.core.retention
retention self-check passed
$ .venv/Scripts/python.exe -m netra.analytics.matching
matching self-check passed
$ .venv/Scripts/python.exe -m netra.analytics.plate_vote
plate_vote self-check passed
$ .venv/Scripts/python.exe -m netra.analytics.tracking
tracking self-check passed
$ .venv/Scripts/python.exe -m netra.analytics.inference
inference self-check passed
$ .venv/Scripts/python.exe -c "from netra.api.app import app; print('app ok')"
app ok
$ git status --porcelain data/
(no output)
```

`data/netra.db` and `data/evidence` are untouched: the retention self-check
runs entirely inside a `tempfile.TemporaryDirectory` with its own engine, and
`git status --porcelain data/` is empty after the full run. The matching
self-check takes 2.7 s and touches no I/O.
