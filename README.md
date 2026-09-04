# NETRA

**Networked Evidence, Tracking & Recognition for Analytics**
Gujarat Police Innovation Challenge 2026 — Sentinel CCTV Hackathon

A unified viewing and analytics platform for heterogeneous government CCTV
infrastructure. Implements **Model 2** (unified viewing with selective
analytics) on the mandatory **Model 1** foundation (centralised registry and
GIS mapping).

---

## What it does

Connects directly to heterogeneous departmental cameras over RTSP/ONVIF without
disturbing existing infrastructure, runs GPU-accelerated vehicle detection and
plate recognition against them, cross-references every detection with a
watchlist database, and raises real-time alerts on a map-based operator console.

| Capability | Where |
|---|---|
| Camera registry, geography, capability profiling | `netra/core/registry.py` |
| Source adapters (RTSP / HLS / file) | `netra/ingest/sources.py` |
| Stream ingestion, PTS timing, reconnection, loop handling | `netra/ingest/stream.py` |
| Two-tier GPU inference, ANPR, colour extraction | `netra/analytics/inference.py` |
| Watchlist fusion matching | `netra/analytics/matching.py` |
| Cross-camera route reconstruction | `netra/analytics/route.py` |
| Appearance re-identification | `netra/analytics/reid.py` |
| Scene-time recovery from overlays | `netra/analytics/scene_clock.py` |
| Email and webhook alerting | `netra/core/notify.py` |
| Role-based access control | `netra/core/auth.py` |
| Control-room assistant | `netra/api/assistant.py` |
| Pipeline wiring, alerting | `netra/pipeline.py` |
| REST + WebSocket API | `netra/api/app.py` |
| Operator console | `netra/web/` |

---

## Running it

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # see note on PyTorch below
python run.py --check                            # verify GPU + grid reachability
python run.py --onboard                          # profile all cameras into the registry
python run.py                                    # console on http://localhost:8080
```

Then in the console: **Start pipeline**, load the sample watchlist from the
Watchlist tab, and detections and alerts begin appearing.

### GPU

All inference runs on the GPU. Measured on an RTX 5050 (8 GB, Blackwell):
**~6 ms per 1080p frame, ~52 frames/second**, which is roughly 1.7x the load
of scanning all 30 cameras at the tier-1 rate.

Blackwell is `sm_120` and needs a CUDA 12.8 build. The default PyPI wheel
installs but fails at runtime, so PyTorch must come from the cu128 index:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
```

Verify with `python run.py --check`, which prints the detected device.
`NETRA_DEVICE=cpu` exists only as a fallback for machines without a GPU.

### Configuration

Everything is environment-overridable; see `netra/config.py`.

| Variable | Default | Purpose |
|---|---|---|
| `NETRA_DB` | SQLite in `data/` | Set a `postgresql+psycopg://` URL to run on PostgreSQL |
| `NETRA_DEVICE` | `cuda` | `cpu` to run without a GPU |
| `NETRA_TIER1_FPS` | `1.0` | Baseline sampling rate, every camera |
| `NETRA_TIER2_FPS` | `5.0` | Escalated rate when vehicles are present |
| `NETRA_GRID_PASSWORD` | — | Sentinel portal access password |

SQLite is the default so the stack runs with no external services. The data
layer is SQLAlchemy throughout, so pointing `NETRA_DB` at PostgreSQL/PostGIS
requires no code change.

### Access control

The platform runs open by default so a demonstration needs no credential setup,
and warns loudly at startup that it is doing so. Generating keys switches the
entire surface to enforced access:

```bash
python run.py --make-keys      # writes data/api_keys.json, prints one key per role
```

| Role | May |
|---|---|
| `viewer` | Read cameras, detections, alerts, traces |
| `operator` | Viewer, plus acknowledging alerts and maintaining the watchlist |
| `admin` | Operator, plus onboarding cameras and controlling the pipeline |

Send the key as an `X-API-Key` header. Keys are never logged; only the role and
a short fingerprint reach the audit trail.

### Outward alerting

Set `NETRA_NOTIFY=1` with SMTP settings (`NETRA_SMTP_HOST`, `NETRA_MAIL_TO`, …)
or `NETRA_WEBHOOK` to deliver alerts beyond the console. Severity gating
(`NETRA_NOTIFY_MIN`) and repeat suppression (`NETRA_NOTIFY_COOLDOWN`) keep the
channel readable: a vehicle dwelling in view produces one notification, not one
per frame.

### Demonstrating on your own footage

The Government grid cannot exercise plate recognition (see below), so ANPR is
demonstrated on supplied video:

```bash
curl -X POST localhost:8080/api/cameras/own-feed   -H "Content-Type: application/json"   -d '{"path":"/absolute/path/to/video.mp4","camera_id":"own001"}'
curl -X POST "localhost:8080/api/pipeline/start-own-feed?camera_id=own001"
```

The file runs through the identical adapter, detection, matching and alerting
path as a live camera.

---

## Design decisions worth knowing

**One process owns the GPU.** Thirty ingest threads feed a single inference
worker through a bounded queue. Thirty processes each holding a CUDA context
would exhaust 8 GB immediately. A full queue drops frames rather than blocking
ingestion — the streams are live and unpauseable, so a frame that cannot be
processed now is worth less than the one arriving next.

**Two-tier scheduling.** Every camera is scanned at ~1 fps purely to answer
"are there vehicles here?". Cameras that say yes escalate to ~5 fps for
plate recognition, then decay back. GPU budget follows traffic instead of being
spread evenly across cameras watching an empty road. The same tiering is what
lets the design scale to regional edge nodes shipping metadata rather than video.

**Plate text is one signal, not the pipeline.** Most cameras on this grid are
wide-area night overviews where a plate occupies a few dozen pixels. A system
that matches only on clean OCR output finds almost nothing. Detections are
scored on four independent signals — exact plate, partial plate, appearance,
and space-time feasibility — and fused, with the per-signal reasoning stored on
every alert so an operator can see why the system believes what it believes.
Appearance alone never raises an alert: two silver hatchbacks are not evidence.

**Vehicles are followed by appearance, not only by plate.** Measured over 2,691
frames on the three best-positioned grid cameras, 200+ detected vehicles yielded
no readable plate at all. Every detection therefore carries a 512-dimension
appearance embedding, and cross-camera tracing works without OCR. Results are
ranked candidates for operator confirmation, never identification.

**Scene time is recovered from the burned-in overlay.** The grid replays
recordings, so capture time cannot order sightings across cameras. The overlay
is read once per connection and carried forward on PTS. Readings below a
confidence floor or outside a plausible recording year are rejected - an early
misread produced the year 0921 and would have mis-timed every sighting on that
camera.

**Capability profiling drives everything.** The grid supplies only `{id, name}`,
so codec, resolution, geography and usable signal quality are all discovered by
probing. Roughly a quarter of the cameras turn out to be corrupt, effectively
black, or indoors with no vehicles at all. Those are classified and reported
rather than silently producing noise — which is also the Model 1 gap-analysis
deliverable, measured rather than declared.

---

## Compliance with the Sentinel integrator brief

| Requirement | Implementation |
|---|---|
| RTSP forced over TCP | `OPENCV_FFMPEG_CAPTURE_OPTIONS` set before `cv2` import |
| No timing from `CAP_PROP_FPS` or arrival time | All timing from `CAP_PROP_POS_MSEC`; real rate measured independently |
| Inter-frame gaps tolerated | Sampling is PTS-interval based; gaps are not disconnects |
| Reconnect with backoff | 2 s doubling to a 30 s cap, reset only on a real connection |
| Join-time decoder warnings non-fatal | Read errors trigger reconnection, not process exit |
| Camera list from the catalogue | `cameras.json` fetched at onboarding; URLs derived, never hard-coded |
| Mixed H.264/HEVC and resolutions | Per-camera properties probed and stored; inference letterboxes |
| Sane across scene discontinuity | Backwards PTS jump detected, downstream state reset |
| Load paced | Connections staggered; only non-degraded cameras opened |

---

## Documentation

- `docs/feed-recon-findings.md` — measured properties of all 30 grid cameras
- `docs/registration-form-answers.md` — submitted proposal text
- `docs/tech-stack-form.md` — declared technology stack
