# Task 8 report — Console UI for the new analytics

## What was built

Three new tabs in the operator console, plus the snapshot endpoint the zone
editor needs, plus the 8d additions to the existing tabs. No framework, no
build step; the existing `.view`/`nav a[data-view]`, `card()`, `.panel`,
`.finding`, `.tag`, `.hop` and `esc()` conventions are used throughout, and
every interpolated value passes through `esc()`.

### 8a — Zones tab
- **Editor.** A camera select, a "Load still frame" button fetching
  `/api/cameras/{id}/snapshot`, and an `<img>` with an overlay `<canvas>`.
  Clicks are recorded as normalised `[x/width, y/height]`; the polygon (or
  the two-point line for `crossing`) is drawn live as points are placed, with
  numbered vertices. Rule type, class filter, severity and dwell seconds are
  plain `<select>`/`<input>` controls. "Clear points" and "Save rule".
  Save POSTs to `/api/zones` and surfaces the server's rejection text verbatim
  when the rule is refused.
- **Configured rules** panel lists every rule with camera, rule type, point
  count, class filter, severity and dwell, each with a Delete button wired to
  `DELETE /api/zones/{id}`.
- **Live zone events** panel renders `/api/zones/events` and receives new
  events over the existing alert WebSocket.

### 8b — Traffic tab
Six stat tiles (cameras counting, active tracks, **counted this loop**, total
counted, **loops seen**, zone events) and a per-camera table carrying active
tracks, counted-this-loop, total counted, loops seen, dropped tracks, class
mix, direction split, mean dwell and a bar sparkline of the last 30 buckets.
`counted_this_loop` sits immediately beside `total_counted` and `loops_seen`
with the note that the cumulative figure spans every replay of the recording,
so an inflated headline count is visible as such. A "Write traffic snapshot"
button calls `POST /api/traffic/snapshot`.

History is fetched once as `/api/traffic/history?limit=1000` and grouped by
camera in the browser rather than one request per camera.

### 8c — Intelligence tab
- **Cloned plates**: the endpoint's `note`, then each finding with plate,
  confidence, both sightings, distance/elapsed/implied speed, and the
  `reason` string rendered verbatim (it carries the arithmetic). Where
  `implied_kmh` is null the panel says "speed not computable" rather than
  printing a blank.
- **Anomalies**: header with cameras assessed, buckets read and count flagged.
  Each assessment shows `status`, hour, observed, z-score and `explanation`,
  with the learned baseline mean/stdev/samples where one exists.
  `insufficient_data` rows are rendered muted (`.muted`, 55% opacity) rather
  than hidden.
- **Journeys**: index breakdown (detections in group, comparable, excluded for
  no scene clock, excluded for no embedding), the endpoint `note`, and when
  `mining_skipped` is true an explicit statement that mining was skipped
  because the group was mined already and held nothing. An empty result after
  a real mine renders "No cross-camera journey found in the indexed
  recordings". Journeys render as `.hop` rows with per-hop similarity, leg km
  and implied speed, plus a `truncated` badge and the journey `note`.

### 8d — Existing tabs
- Detections table gained **Track** and **Scene time** columns, and the plate
  cell shows the recovered character count beside the plate where present.
- Appearance traces show an `ambiguous` badge and the `ambiguity_note` on any
  hop the re-identifier flagged.
- Zone events arriving on the alert WebSocket (`kind: "zone"`) render through
  a distinct `zoneEventHtml()` with a warning-coloured rail and an amber
  toast, so a rule breach is never presented as a watchlist identity match.

### New endpoint
`GET /api/cameras/{camera_id}/snapshot` — one JPEG via
`ffmpeg -rtsp_transport tcp -frames:v 1`, 25 s hard timeout, result cached in
process for 30 s per camera (`?refresh=true` bypasses the cache). 404 for an
unknown camera, 503 when the grab produces nothing usable. This is the only
change to `app.py` besides the three extra detection fields.

## Endpoint verification

Server started with `.venv/Scripts/python.exe run.py --port 8080`, left
running. Every endpoint the console calls:

| Endpoint | Status |
| --- | --- |
| `GET /` | 200 |
| `GET /static/app.js` | 200 |
| `GET /api/cameras` | 200 |
| `GET /api/zones` | 200 |
| `GET /api/zones/events` | 200 |
| `GET /api/traffic/live` | 200 |
| `GET /api/traffic/history` | 200 |
| `GET /api/analytics/cloned-plates` | 200 |
| `GET /api/analytics/anomalies` | 200 |
| `GET /api/analytics/baselines` | 200 |
| `GET /api/analytics/journeys?group=ahmedabad-13jun` | 200 |
| `GET /api/cameras/cam04/snapshot` | 200 (`image/jpeg`, 386 833 bytes) |
| `GET /api/cameras/nope/snapshot` | 404 (as expected) |
| `GET /api/detections?limit=5` | 200 |
| `GET /api/pipeline/status` | 200 |
| `POST /api/zones` (round trip) | 200 |
| `DELETE /api/zones/{id}` | 200 |

`node --check netra/web/app.js` passes.

Snapshot timing measured: first grab on cam04 took 18.5 s (RTSP connect
dominates); the immediately following request was served from cache in 46 ms.
The timeout was raised from 20 s to 25 s on that measurement — a 20 s bound
would have been within a second of failing on a healthy camera.

## Where the endpoint shape differed from the task description

Trusted the endpoint in each case:

- **`/api/analytics/journeys`** returns no `next_mine_in_s`. It returns
  `next_mine` (the string "only on request, with refresh=true"),
  `last_mined_at`, `stored`, `count`, `filters_applied` and
  `mined_at_similarity`. The console renders `next_mine` verbatim, which is
  the honest statement: nothing re-mines on a timer, so there is no countdown
  to show.
- **`/api/analytics/anomalies`** returns `{buckets_read, cameras_assessed,
  anomalies, assessments}`; each assessment carries `hour`, `anomalous` and a
  nested `baseline` object (`mean`, `stdev`, `effective_stdev`, `samples`,
  `sufficient`) rather than baseline fields at top level.
- **`/api/detections`** did **not** carry `track_id` or `scene_time` — both
  columns exist on the `Detection` model but were never serialised. Added,
  together with `plate_chars`.
- **Plate consensus observation count is not persisted.** `plate_vote` returns
  a `voter_count` but nothing stores it on the detection row, so the console
  cannot show it. What it shows instead is `plate_chars`, the number of
  characters actually recovered. Surfacing the vote count properly needs a
  column on `Detection` — out of scope here, and flagged rather than faked.
- **`/api/traffic/live`** matches: `{cameras: [...], zone_events}` with the
  documented per-camera fields.
- **Journey hops** carry `evidence_path` (not `evidence`), `similarity`,
  `leg_km`, `leg_seconds`, `implied_kmh` and `reason`.

## Concerns

- The traffic sparkline is only as long as the number of snapshots an operator
  has written; with no snapshots the cell says "no snapshots" rather than
  drawing a flat line. Baselines and anomalies have the same dependency, and
  the anomalies panel says so.
- The snapshot cache is a plain process dict keyed by camera id. Bounded in
  practice by the 30-camera grid; it is not evicted on age, only refreshed.
- The zone editor places points only. There is no vertex drag, no undo of a
  single point (only Clear), and no rendering of existing rules over the
  still — deliberately, per the brief.
