# Task 10 report — issues sweep

**Status:** complete. Tier A, Tier B and all four Tier C items done.

| Tier | Commit | Subject |
|---|---|---|
| A | `b995bfb` | close the correctness, honesty and access-control holes |
| B | `04c14d6` | freshness, stability, precision and the duplicated block |
| C | `8d8cb28` | traffic auto-poll, a retention ceiling, and the stray marker file |

Nothing was pushed. `git status --porcelain data/` was empty at every commit,
and `data/api_keys.json` is absent.

---

## Tier A

**A0 — the scene-clock attempt budget.** `self._clock_attempts[cam] = 0` ran on
every *successful* overlay read, including one that then contradicted the
pending candidate, so a camera whose overlay never reads the same number twice
never anchored and never gave up. The budget is now refunded only by a
corroborated anchor, and a contradiction consumes an attempt like any other
read. Giving up is not permanent: a camera silent for `CLOCK_REANCHOR_AFTER_S`
of stream time gets one fresh budget (its pending half-reading dropped with the
old one, since a reading a quarter of an hour old is not a corroborating
partner for one taken now) — the same rule a corroborated anchor already lived
under. `_note_clock_exhausted` is called from every path that consumes an
attempt and logs exactly once per budget.

Pinned in `inference._self_check`: 200 mutually-contradicting readings on the
opportunistic path with queue slack now issue at most `CLOCK_ATTEMPT_LIMIT`
OCR calls and anchor nothing; advancing past the re-anchor window, an agreeing
pair anchors and leaves the counter at zero.

**A1 — pre-corroboration scene times.** Additive `scene_time_corroborated`
(Boolean, default false) on `detections` via `_ADDED_COLUMNS`, carried from the
engine through `VehicleDetection` to `_flush`. `core/timing.py` gains
`scene_time()`, the single place deciding what "has a scene clock" means, and
`sighting_time()` is built on it. Consumers:

- `route.py` — a sighting with no corroborated clock is listed in `rejected`,
  not chained. It is reported rather than hidden.
- `cloned_plate.py` — a clone finding is entirely an elapsed-time claim, so no
  clock means no claim.
- `loop_index.py` — `_minable`, `_load_group_detections` and
  `exclusion_report` all require the flag; the published note says so.
- Nothing falls back to wall time for a cross-camera claim.

`tools/purge_scene_times.py` is `--dry-run` by default (`--apply` to write).
**Dry-run count: 13,271 rows** of 13,275 carrying a scene time. It has only
ever been run in dry-run mode here. The other 4 are corroborated rows written
by this task's own live runs, which confirms the write path end to end:

```
190994 cam15 2026-06-14 04:23:46.213000 True
```

**A2 — plate vote count.** `plate_votes` (Integer, nullable) added additively,
carried from `VehicleDetection` to `_flush`, serialised by `/api/detections`,
and shown in the Detections table as "· N reads" with a tooltip saying one read
is a guess. A fallback single read stores 1. Nullable rather than defaulted to
1, because an existing row's plate was read an unknown number of times.
Wiring pinned in `inference._self_check` (seven agreeing reads → `plate_votes
== 7`). Live runs stored none, because no plate was readable on this grid —
which is the documented finding, not a regression.

**A3 — console snapshot under access control.** The still is now fetched with
`fetch()` carrying `X-API-Key` from `localStorage["NETRA_API_KEY"]` and handed
to the `<img>` as an object URL (previous blob revoked). A one-line password
input in the header reads and writes that key. `api()` and the four raw
watchlist/zone fetches carry the header too, so the whole console works under
auth rather than only the still. Verified with a real `data/api_keys.json`:

| call | result |
|---|---|
| `GET /api/cameras/cam04/snapshot`, no header | 401 |
| same, empty header | 401 |
| same, viewer key | 200 `image/jpeg`, 226 908 bytes |

`data/api_keys.json` was then deleted; confirmed absent. In open mode the
header is sent blank and everything behaves as before.

**A4 — traffic class breakdown on restart.** The restart branch took the whole
cumulative as the bucket total but still differenced classes against the larger
pre-restart snapshot, so every class delta was ≤ 0 and the row read "N
vehicles, composition unknown". The class snapshot now resets on the same
condition. Arithmetic factored into `Pipeline._bucket_deltas` and pinned by a
new `pipeline._self_check`: first bucket, steady state, empty bucket, restart
(non-empty breakdown, `sum(counts_by_class) <= total`), and the flush after a
restart.

## Tier B

**B1 — anomalies freshness.** `ANOMALY_MAX_BUCKET_AGE_S = 900`. A bucket older
than that is reported `stale` with its own timestamp in the explanation, and
`stale` is deliberately excluded from `Assessment.anomalous`. This was live on
this store: five cameras had last reported eleven hours earlier and were being
published as "no traffic counted, the road may be blocked". The endpoint now
returns `stale` and `max_bucket_age_s`; the assistant names them separately;
the console mutes them rather than colouring a dropped feed green.

**B2 — `next_mine` / stale-store disclosure.** No change needed; round 4 closed
it. `next_mine` reads "only on request, with refresh=true" and the note states
the non-empty-store behaviour explicitly. Verified against the live endpoint.

**B3 — snapshot cache eviction.** Evicts on read at `SNAPSHOT_TTL_S * 4`. The
per-camera lock registry is dropped alongside, but only where `lock.locked()`
is false — replacing a held lock would let a second ffmpeg start against the
same camera, the exact thing the registry prevents.

**B4 — stats naming.** `stats["plate_votes"]` → `plate_consensus_applied`,
surfaced as its own overview tile ("Plate consensus applied · frames given a
voted plate"), distinct from the per-detection `plate_votes` column.

**B5 — `candidates()` order stability.** Window keys are sorted before the
buckets are walked; the docstring now says why. Repeat calls pinned identical.

**B6 — FP16.** Measured cleanly rather than through the noisy `infer_ms`
sample, which mixes OCR, embedding, tracking and zones into one per-frame
figure and varies by 100× between frames. Sixty warm passes at `TIER2_IMGSZ`
on the vehicle model:

| precision | mean | median |
|---|---|---|
| fp32 | 17.28 ms | 17.18 ms |
| fp16 | 12.05 ms | 11.95 ms |

Live `infer_ms` over 30 samples on a 5-camera run: 44.8 ms mean / 42.4 ms
median before, 27.2 ms mean / 26.2 ms median after — but that measure is too
noisy to carry the claim on its own (an FP32 control run gave a 102.5 ms mean
against a 31.0 ms median, dominated by outliers), which is why the controlled
figure above is the one reported. `dropped = 0` in every run.

Spelled `quantize=16`, not `half=True`: this ultralytics (8.4.138) deprecates
`half` and forwards it to exactly that with a warning. ReID backbone runs
`.half()` with the input cast to match, normalising in fp32; embeddings agree
to the third decimal against a 0.80 threshold. No regression, so B6 stands.

**B7 — HLD capacity numbers.** One paragraph added at the head of §7
distinguishing tier-1 scanning capacity (the ~6 ms figure, which sets 150–200
cameras per node) from full-pipeline capacity under admission control (the
eight-junction 0%-loss result), saying which question each answers and why
multiplying the first would overstate a node by an order of magnitude.

**B8 — duplicated block.** First copy of `ATTRIBUTE_BROADCAST_BOUND_S` and of
the attribute-worker `__init__` block removed. `/api/pipeline/status` on a live
run still reports `{"queued": 11, "processed": 11, "dropped": 0, "failed": 0,
"broadcast": 2, "enabled": true, "queue_depth": 0}`.

**B9 — single-word descriptions.** `_worth_indexing` gates the BM25 `vehicle`
corpus on two populated structured fields or a description of ≥ 2 tokens.
Pinned: `"yellow"` is not indexed, `"yellow van"` is, one structured field is
not enough, two are.

## Tier C — all four done

- **C1** Traffic tab polls every 5 s while it is the visible view.
- **C2** `protected_evidence` carries a `ponytail:` line naming the
  acknowledged-alert case and the case-linked hold that would fix it.
- **C3** `core/timing.py` gained its own `_self_check()` in Tier A.
- **C4** `index_run3.start` removed from the index; `*.start` added to
  `.gitignore` (`*.log` was already there, not duplicated).

---

## Verification

**Self-checks — all pass** (`python -m netra.<module>`):
`pipeline`, `analytics.attributes`, `analytics.baseline`,
`analytics.cloned_plate`, `analytics.inference`, `analytics.loop_index`,
`analytics.matching`, `analytics.plate_vote`, `analytics.reid`,
`analytics.route`, `analytics.scene_clock`, `analytics.tracking`,
`analytics.zones`, `api.assistant`, `api.retrieval`, `core.auth`,
`core.notify`, `core.retention`, `core.timing`.
Two are new this task: `pipeline` and `core.timing`.

`python run.py --check` → **READY**.
`node --check netra/web/app.js` → passes.

**Console endpoints, live server:**

| endpoint | status |
|---|---|
| `GET /api/cameras` | 200 |
| `GET /api/zones` | 200 |
| `GET /api/zones/events` | 200 |
| `GET /api/traffic/live` | 200 |
| `GET /api/traffic/history` | 200 |
| `GET /api/analytics/cloned-plates` | 200 |
| `GET /api/analytics/anomalies` | 200 |
| `GET /api/analytics/baselines` | 200 |
| `GET /api/analytics/journeys?group=ahmedabad-13jun` | 200 |
| `GET /api/detections?limit=5` | 200 |
| `GET /api/pipeline/status` | 200 |
| `GET /api/storage` | 200 |
| `GET /api/report` | 200 |
| `GET /api/cameras/cam04/snapshot` | 200 `image/jpeg` |
| `GET /api/cameras/nope/snapshot` | 404 |

**Live runs, 5 cameras (cam01, cam04, cam14, cam15, cam10), 30 s+:**
`dropped = 0` after A0 and again after B6.

**A1 dry-run count:** 13,271.

`git status --porcelain data/` empty at every commit. `data/api_keys.json`
absent.

## Concerns

1. A stale server from an earlier session was holding port 8080 and running
   pre-Tier-A code. It was killed to test this build. Nothing else was
   disturbed.
2. `plate_votes` is populated by the code path but every live run on this grid
   stores `None`, because no plate is readable here. The column is exercised
   only by the self-check, not by live data.
3. B6's live `infer_ms` before/after is reported but is not load-bearing: the
   metric samples one frame of a distribution spanning 16 ms to 2 100 ms. The
   controlled 60-pass benchmark is the number to quote.
4. A1 changes what `route.py` and `cloned_plate.py` will produce from the
   current store: sightings whose scene time was never corroborated no longer
   chain. This is the intended correction, but the demo's route and clone
   views will show fewer results than before, with the excluded sightings
   listed as rejected and the reason given.
