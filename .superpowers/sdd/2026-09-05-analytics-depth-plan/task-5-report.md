# Task 5 report — Per-camera behavioural baselines

## What was built

`netra/analytics/baseline.py`

- `Baseline(camera_id, hour, mean, stdev, samples)` with `sufficient` and
  `effective_stdev` properties, and `Assessment` carrying `status`, `z_score`,
  an `explanation` sentence and the baseline it was judged against.
- `learn(rows)` groups `TrafficStat` rows by `(camera_id, hour_of_day)` and
  returns mean, sample standard deviation and sample count. Hour of day is
  taken from `bucket_start` in **UTC** throughout; naive timestamps are read as
  UTC, aware ones converted to it. Rows may be ORM objects or plain dicts, which
  is what keeps the self-check free of the database.
- `assess(baseline, observed)` returns `insufficient_data` (no z-score, no
  verdict) below `MIN_SAMPLES = 5`, and otherwise one of
  `quiet | low | normal | elevated | high`. Zero traffic against a baseline of
  one or more reads `quiet`, with the explanation naming a blockage or an
  obstructed view as the thing worth checking.
- `STDEV_FLOOR = 1.0`: dispersion is floored before any z-score is taken,
  because a camera whose counts have been identical has zero variance and one
  extra vehicle against zero variance is an infinite deviation — arithmetically
  true, operationally absurd. One vehicle is the smallest difference the counter
  can express, so it is the smallest dispersion worth believing.
- `detect_anomalies(baselines, current_stats, include_normal=False)` sorts most
  deviant first and pushes `insufficient_data` entries last, since those are
  information about the platform's own coverage rather than about the road.

## The carried-forward fix: `TrafficStat.total` was cumulative

`Pipeline.flush_traffic_stats` wrote `stats["total_counted"]` — the running
count across every replay of a looping recording — into every bucket. Learning a
"norm" from a monotonically rising figure would have described uptime, not
traffic.

- `TrafficStat.total` is now the traffic counted **during that bucket**,
  obtained by differencing the tracker's cumulative counter against the value at
  the previous flush. A tracker recreated mid-run restarts its counter; a
  smaller cumulative than last time is taken whole rather than persisted as a
  negative count.
- `counts_by_class` is differenced the same way. It was cumulative for the same
  reason, and a bucket whose class breakdown summed to more than its total would
  be visibly incoherent to an analyst.
- The cumulative figure is preserved in two new columns,
  `TrafficStat.cumulative_total` and `TrafficStat.loops_seen`, so the headline
  count is still available and can be read honestly beside the number of
  replays. `/api/traffic/history` now returns both.
- Buckets with no traffic are now written rather than skipped. "This camera saw
  nothing" is exactly the observation a quiet-road baseline needs; dropping it
  would teach the baseline that the road is never empty.

### Database migration — no operator action required

`Base.metadata.create_all` creates missing *tables*, not missing columns, so the
two new columns would be absent from an existing `data/netra.db` and every ORM
read of `traffic_stats` would fail with `no such column`. `netra/core/db.py`
gains a small `_ADDED_COLUMNS` list applied additively by `init_db()` at API
start-up, so an existing database is migrated in place with `ALTER TABLE ... ADD
COLUMN` and nobody has to delete their evidence database.

`data/netra.db` was not touched during this task; it will be altered additively
the next time the API starts.

`ponytail:` that list is hand-kept, not a migration tool. Its ceiling is
additive, defaulted columns on SQLite; a type change or a column drop needs
Alembic.

Note that buckets written *before* this change still hold cumulative totals, so
baselines learned from them are meaningless until those rows age out or are
cleared.

## API

- `GET /api/analytics/baselines?camera_id=&limit=` — learned norms per camera
  and hour, each marked `sufficient` or not. Insufficient hours are returned
  rather than hidden: knowing the platform cannot yet judge an hour is itself
  operational information, and omitting those rows would imply coverage that
  does not exist.
- `GET /api/analytics/anomalies?camera_id=&limit=&include_normal=` — the most
  recent completed bucket per camera judged against the norms. The current
  reading is a completed bucket, not a live partial count, because comparing a
  partial count against full-bucket norms would manufacture false quiets.

Both are `require("read")` and bounded by `Query(..., le=20000)` with a 5000-row
default, so a dashboard refresh cannot pull the whole traffic table and starve
the detection threads of the database.

## Assistant

New `_unusual` intent placed first in `INTENTS`. An operator phrases this
question with words later intents already claim — "which camera is busier than
normal" contains "camera", "where is it unusual" contains "where" — so lower
down it would have been answered by camera health or the plate trace instead.

Routing was split out of `ask` into a new `route(question) -> handler | None`,
so the self-check can pin the mapping without executing a handler and therefore
without a database. It asserts both directions: five natural phrasings of
"anything unusual?" resolve to `_unusual`, and camera health, plate trace, clone
detection, alerts, detections and pipeline status all keep their own questions.

The handler reports how many cameras are flagged, the leading three
explanations, and — always — how many cameras have too little history for any
judgement to be honest.

## Verification

All four run clean. None needs the network, a GPU or the project database; the
baseline and assistant self-checks are entirely synthetic.

```
.venv/Scripts/python.exe -m netra.analytics.baseline   -> baseline self-check passed
.venv/Scripts/python.exe -m netra.api.assistant        -> assistant self-check passed
.venv/Scripts/python.exe -m netra.analytics.tracking   -> tracking self-check passed
.venv/Scripts/python.exe -c "from netra.api.app import app; print('app ok')" -> app ok
```

The baseline self-check covers every case the brief names: insufficient samples
never yield a verdict however extreme the reading; a clear spike is `high`; a
normal reading is not flagged; zero traffic against a busy baseline is `quiet`;
a zero-variance baseline gives z = 1.0 for one extra vehicle rather than
infinity; hours are keyed separately so a night norm does not absorb a day norm;
and an unknown camera is declined rather than guessed at.

## Concerns

- A per-hour Gaussian ignores day of week. A market street or stadium approach
  whose traffic is weekly rather than daily would read a busy Saturday as an
  anomaly against a weekday norm. Adding day-of-week to the key needs roughly
  seven times the observation history before it earns its place.
- Pre-existing `traffic_stats` rows carry cumulative totals and will poison the
  learned norms until they age out or are cleared.
- Baselines are relearned on each request from up to 5000 rows rather than
  cached. That is cheap at the current data volume and it is bounded, but it is
  a repeated scan and would want a cache before the history grows large.

---

# Fix round 1

## Critical — the `quiet` branch bypassed the z-score

`assess` short-circuited to `quiet` whenever `observed == 0 and mean >= 1.0`,
ahead of any band comparison. One vehicle per bucket is not a busy road, and on
a genuinely quiet camera — history `(0, 2, 1, 0, 3, 1, 0, 2)`, mean 1.12, sd
1.13 — an observed zero has z = -1.0, which the module's own bands call normal.
It asserted a possible blockage anyway, and with 60-second buckets would have
done so on most quiet cameras every few minutes. That was the one place in the
module that told a control room something was abnormal on evidence saying the
reading was ordinary.

`quiet` is now gated on `observed == 0 and z <= Z_LOW`, making it a more
strongly worded `low` rather than an override of the bands, and its sentence
now carries the z-score like every other verdict. A reading inside the normal
band can no longer be reported as abnormal by any branch.

Measured after the fix, on exactly the reviewer's history:

```
mean 1.12 sd 1.13 -> normal z=-1.0
0 vehicles is within the usual range: the norm for x at hour 02:00 UTC is 1.1
(sd 1.1, 8 samples).
```

Covering assertions (`_self_check`, using that history verbatim):

```python
quiet_road = learn([row("cam05", 2, n)
                    for n in (0, 2, 1, 0, 3, 1, 0, 2)])[("cam05", 2)]
calm = assess(quiet_road, 0)
assert calm.status == "normal", calm
assert not calm.anomalous, calm
assert abs(calm.z_score) <= abs(Z_LOW), calm
assert assess(quiet_road, 10).status in ("elevated", "high")
```

The busy-road case is unaffected and still asserted: zero against cam01's
hour-9 norm of 41.8 (sd 2.9) is z = -14 and still reads `quiet`.

## Important — legacy cumulative rows are now excluded in code

The five pre-fix rows in `data/netra.db` carry cumulative totals, nothing prunes
`traffic_stats`, and `learn()` has no time window, so they would have survived
the demo. On cam15 at hour 18 one such row moved the mean from 3.40 to 14.0 and
the standard deviation from 1.14 to 25.98, and a ten-fold spike then read
`normal`.

New `_is_legacy_cumulative(row, total)`, applied inside `learn()`, skips a row
where `cumulative_total == 0 and total > 0`. The migration that added
`cumulative_total` defaults it to 0, while any row written since carries the
real cumulative, which is always at least as large as that bucket's own delta —
so the two are cleanly separable without touching the database. A genuine empty
bucket has `total = 0` and is deliberately kept, since a normally quiet road is
exactly what the baseline needs to learn. A source carrying no
`cumulative_total` field at all (a synthetic dict) is trusted as given.

Chosen over a one-time cleanup in the migration because it destroys no
observation and needs no write to the operator's evidence database.

Covering assertions:

```python
with_legacy = learn(modern + [legacy])[("cam06", 18)]
assert with_legacy.samples == 5, with_legacy      # the legacy row is excluded
assert with_legacy.mean < 4, with_legacy
assert assess(with_legacy, 30).status == "high"   # the hidden spike is seen

with_empty = learn(modern + [genuine_empty])[("cam06", 18)]
assert with_empty.samples == 6, with_empty        # a real empty bucket is kept
assert with_empty.mean < with_legacy.mean, with_empty
```

## Commands run

```
.venv/Scripts/python.exe -m netra.analytics.baseline  -> baseline self-check passed
.venv/Scripts/python.exe -m netra.api.assistant       -> assistant self-check passed
.venv/Scripts/python.exe -c "from netra.api.app import app; print('app ok')" -> app ok
git status --porcelain data/                          -> (empty)
```

`data/netra.db` was not modified. Its five legacy rows remain on disk and are
excluded at learn time.
