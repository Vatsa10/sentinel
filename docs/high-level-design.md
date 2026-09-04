# NETRA — Technical Proposal (High-Level Design)

**Networked Evidence, Tracking & Recognition for Analytics**
Gujarat Police Innovation Challenge 2026 · Sentinel CCTV Hackathon

**Solution model:** Model 2 (Unified Viewing and Selective Analytics), built on
the mandatory Model 1 foundation (Centralised CCTV Registry and GIS Mapping),
with a documented Model 3 adapter path for future federation.

---

## 1. Executive summary

NETRA connects directly to heterogeneous departmental CCTV systems over RTSP,
ONVIF and HLS without altering existing infrastructure, applies GPU-accelerated
video analytics, correlates every detection against watchlist databases, and
raises prioritised real-time alerts on a map-based operator console.

The platform is running against the Government-provided Sentinel grid. Measured
results from that grid are given throughout this document rather than projected
figures, including where the grid's own condition limits what any system can
achieve.

---

## 2. Problem context and measured baseline

Before designing the analytics, all thirty cameras of the Government grid were
profiled. This materially changed the design, so it is reported first.

### 2.1 What the grid supplies

The catalogue endpoint returns only `{id, name}`. No codec, resolution,
geography, ownership or health information is provided. Everything else must be
discovered.

### 2.2 Measured heterogeneity

| Property | Observed |
|---|---|
| Codecs | 24 × H.264, 6 × H.265 |
| Resolutions | 2560×1440, 1920×1080, 1280×960, 1280×720, 960×576 — a 7× spread in pixel count |
| Declared frame rates | 30, 25, 20, 12, 10 fps, and `0/0` on two cameras |

A fixed-shape inference batch cannot span this grid. Two cameras declare an
invalid frame rate, confirming the integrator brief's warning that the reported
rate must never drive timing logic.

### 2.3 Measured camera condition

| Condition | Cameras | Count |
|---|---|---|
| Serviceable, vehicle analytics | cam01–06, 10, 13–16, 19, 23–27, 30 | 18 |
| Plate geometry adequate for ANPR | cam12, cam17, cam18, cam20 | 4 |
| Indoor, no vehicle traffic | cam28, cam29 | 2 |
| Degraded — corrupt, black, or overexposed | cam07, cam08, cam09, cam11, cam21, cam22 | 6 |

**20% of the Government grid cannot support video analytics in its present
state.** This is produced automatically by the registry at onboarding, not by
manual inspection, and is reported as the Model 1 gap analysis.

### 2.4 Plate recognition is not achievable on this grid

The majority of these cameras are wide-area junction overview cameras operating
at night. A number plate occupies roughly 10–20 pixels and is further degraded
by headlight glare and motion blur.

This was measured rather than assumed. Across the three best-positioned
cameras:

| Camera | Frames sampled | Vehicles detected | Vehicles large enough to attempt | Plates read |
|---|---|---|---|---|
| cam12 (Adalaj toll) | 899 | ~70 | ~18 | **0** |
| cam17 (Rajkot) | 890 | 109 | 32 | **0** |
| cam18 (Rajkot) | 902 | 25 | 18 | **0** |

**2,691 frames, 200+ vehicles, zero readable plates.** The only text recoverable
is the cameras' own burned-in timestamp overlay.

This is a property of the installed infrastructure, not of the recognition
model. It is stated plainly because a platform that claims plate recognition on
these feeds would be misrepresenting what the State's cameras can deliver, and
because it drives two central design decisions: matching cannot depend on plate
text, and vehicles must be followable by appearance.

---

## 3. Architecture

### 3.1 Component overview

```
   Sentinel grid            NETRA platform                      Consumers
   ─────────────            ──────────────                      ─────────
                   ┌──────────────────────────────┐
  cameras.json ───▶│  Registry (Model 1)          │
                   │  identity · geography ·      │──┐
                   │  capability profiling        │  │
                   └──────────────────────────────┘  │ camera set +
                                                     │ capability
                   ┌──────────────────────────────┐  │
  RTSP / ONVIF ───▶│  Source adapters             │◀─┘
  HLS          ───▶│  rtsp · hls · file           │
  file         ───▶└──────────────┬───────────────┘
                                  │ frames
                   ┌──────────────▼───────────────┐
                   │  Ingest supervisor           │
                   │  PTS clock · backoff ·       │
                   │  loop-cut detection ·        │
                   │  tier-1/tier-2 sampling      │
                   └──────────────┬───────────────┘
                                  │ bounded queue
                   ┌──────────────▼───────────────┐
                   │  Inference engine (sole GPU  │
                   │  owner)                      │
                   │  vehicle detection · colour ·│
                   │  ANPR · ReID embedding ·     │
                   │  scene-time anchoring        │
                   └──────────────┬───────────────┘
                                  │ detections
                   ┌──────────────▼───────────────┐
                   │  Correlation & matching      │
                   │  plate · partial · appearance│──▶ alerts ──▶ console
                   │  · space-time feasibility    │           ──▶ email
                   └──────────────┬───────────────┘           ──▶ webhook
                                  │
                   ┌──────────────▼───────────────┐
                   │  PostgreSQL / PostGIS        │──▶ REST + WebSocket API
                   │  registry · detections ·     │──▶ operator console
                   │  watchlist · alerts · audit  │──▶ CSV export
                   └──────────────────────────────┘
```

### 3.2 Integration approach — heterogeneous cameras, NVRs and VMS

Sources are reached through an adapter interface. Adding a protocol, a vendor
SDK or a new VMS means adding one adapter; ingestion, inference, matching and
storage are unchanged. Three adapters ship: RTSP (forced over TCP), HLS (for
networks where the RTSP port is blocked), and file.

Departmental systems are not modified, not proxied and not replaced. No video
is centrally recorded. The platform is a consumer of existing feeds, which is
what allows onboarding without disturbing departmental operations or AMC
arrangements.

### 3.3 Ingestion and timing

All timing derives from presentation timestamps. Neither arrival time nor the
declared frame rate is trusted anywhere in the pipeline.

This matters concretely. On connection the gateway replays a buffered
group-of-pictures, so the first second of frames arrives faster than real time;
any tracker timestamping by arrival computes impossible velocities immediately
after every connection. Two cameras on this grid declare `0/0` fps, so any
calculation dividing by the declared rate fails outright.

Each camera has a supervised worker that reconnects with exponential backoff
(2 s doubling to a 30 s cap, reset only on a genuine connection), tolerates
inter-frame gaps without treating them as disconnection, treats join-time
decoder warnings as informational, and detects the loop point as a backwards
PTS jump — resetting tracker state, re-identification galleries and the
scene-time anchor rather than inventing motion across the cut.

### 3.4 Two-tier analytics scheduling

Every serviceable camera is scanned at approximately 1 fps to answer only
whether vehicles are present. Cameras with traffic escalate to approximately
5 fps with a larger inference input for the expensive work, then decay back
after ten seconds without a sighting.

GPU budget therefore follows traffic rather than being spread evenly across
cameras watching empty roads. This is also the mechanism that makes the design
scale: the same tiering runs at regional edge nodes, which transmit structured
metadata instead of video.

### 3.5 Scene-time recovery

Correlating a vehicle across cameras requires knowing when each sighting
occurred. Capture time cannot supply this, because the sandbox replays
recordings and each loop begins whenever a client connects.

Every camera burns a date and time into the frame. NETRA reads this overlay once
per connection and carries it forward using PTS:

```
scene_time(frame) = anchor_scene_time + (frame.pts_ms − anchor_pts_ms)
```

Readings below a confidence floor are discarded, and parsed dates outside a
plausible recording range are rejected — an early misread produced the year 0921
at confidence 0.02, which would have mis-timed every sighting on that camera.
No scene time is preferable to a wrong one.

### 3.6 AI-powered video analytics

| Capability | Approach | Applied to |
|---|---|---|
| Vehicle detection & classification | YOLOv8m, confidence 0.20 | all serviceable cameras |
| Vehicle colour | Reference-colour matching on the body band | all detections |
| Number plate recognition | Plate localisation with OCR | ANPR-capable cameras only |
| Appearance re-identification | ResNet-18, 512-d L2-normalised embedding | all detections |
| Person analytics | YOLOv8m person class | indoor cameras |
| Camera health | Luminance, banding and frame-availability analysis | all cameras |

Model selection was measured, not assumed. On thirty real frames from this grid,
YOLOv8m at confidence 0.20 detected 131 vehicles across 20 cameras against
YOLOv8n's 76 across 17, at 21 ms per frame — comfortably affordable given the
available headroom.

### 3.7 Watchlist correlation and alerting

Because plate text is frequently unavailable, a detection is scored against each
watchlist record on four independent signals:

| Signal | Contribution |
|---|---|
| Exact plate match | Decisive when OCR resolved the full plate |
| Partial plate match | Weighted by characters recovered; a 4-of-10 read is a strong constraint, not a failure |
| Appearance | Vehicle class and colour agreement |
| Space-time feasibility | Vetoes pairs requiring implausible travel speed |

Character confusions common to night-time OCR (`O`/`0`, `I`/`1`, `S`/`5`,
`B`/`8`) are folded before comparison, so a misread character does not discard
an otherwise correct match.

Space-time feasibility acts only as a veto: it can reduce confidence, never
manufacture it. **Appearance alone never raises an alert** — two silver
hatchbacks are not evidence — and this rule is enforced in code and covered by
tests.

Every alert stores the per-signal breakdown that produced it. The console shows
the operator which signals fired, at what confidence, with evidence imagery,
so a machine judgement can be reviewed and overruled rather than merely obeyed.

### 3.8 Cross-camera vehicle tracing

Two mechanisms, sharing one output format:

1. **By registration number** — sightings matching the plate, ordered by scene
   time, validated leg by leg for physical plausibility.
2. **By appearance** — cosine similarity over ReID embeddings, filtered by
   space-time feasibility and restricted to cameras sharing a recording session.

The second is what makes tracing possible at all on this grid. It is presented
as ranked candidates for operator confirmation, never as identification, which
is the only defensible use of appearance evidence in a policing context.

Sightings from different recording sessions are excluded with the reason stated,
rather than being silently chained into a route that looks convincing and is not.

### 3.9 Alert delivery

Alerts reach the console over WebSocket, and outward by email and webhook, with
severity gating and repeat suppression so a vehicle dwelling in view produces
one notification rather than one per frame. Delivery runs on a worker thread
and cannot stall or fail detection.

---

## 4. Data model

| Entity | Purpose |
|---|---|
| `cameras` | Registry: identity, geography, technical profile, capability, health |
| `detections` | One vehicle at one camera at one instant: PTS, scene time, class, colour, plate, embedding, evidence |
| `watchlist` | Entities of interest, shaped to VAHAN / eGujCop record structure |
| `alerts` | Detection ↔ watchlist match with score, type and full per-signal reasoning |
| `audit_log` | Every operator query, for accountability |

SQLAlchemy throughout. SQLite by default so the platform runs with no external
services; setting a connection URL runs it unchanged on PostgreSQL with PostGIS.

---

## 5. Integration with State databases

The watchlist schema mirrors the record structure of VAHAN, SARTHI, eGujCop
(CCTNS), AFIS and NAFIS: registration number, owner, make, colour, class, case
reference and originating database. Production integration is therefore a
connector against the authoritative source rather than a schema change.

All integration is outbound and read-only. NETRA queries watchlist records and
never writes to departmental systems.

---

## 6. Security, privacy and accountability

| Control | Implementation |
|---|---|
| Credential handling | Never committed; supplied by environment or an ignored local file |
| Access control | Role-based, department-scoped camera visibility |
| Audit trail | Every registry, route and vehicle query recorded with actor, target and time |
| Data minimisation | No centralised video recording; evidence crops only, tied to a detection |
| Transport | RTSP forced over TCP; HTTPS termination at the reverse proxy |
| Decision transparency | Every alert carries its reasoning; no unexplained machine judgements |
| Failure containment | A broken camera, slow consumer or failed delivery cannot stall the pipeline |

Privacy note: the platform stores vehicle crops and structured metadata, not
continuous video. Retention is bounded by the evidence store, and every access is
audited.

---

## 7. Performance, measured

Hardware: NVIDIA RTX 5050 Laptop GPU, 8 GB, CUDA 12.8.

| Measurement | Result |
|---|---|
| Vehicle detection | ~13 ms per 1080p frame at 640 px, ~18 ms at 960 px |
| Appearance embedding | ~11 ms per frame (8 vehicles) |
| Colour extraction | ~2 ms per frame (20 vehicles) |
| Live run, 8 busy Ahmedabad junctions | 8/8 connected, **4,893 vehicles in ~2 minutes** |
| Frames dropped | **0%** |
| Inference queue depth | 0–5 |
| Registry onboarding | 30 cameras probed and profiled in ~35 seconds |

### Capacity management

Reaching zero frame loss on eight busy junction cameras required treating GPU
time as a budget to be allocated, not a resource to be assumed. Four measures,
each arrived at by measurement rather than estimate:

**Persistence is batched.** Writing each detection in its own transaction, with
its own evidence JPEG, on the inference thread cost 76% of frames. Detections
are queued and flushed in batches on a separate thread.

**Expensive analytics are applied only where they can pay off.** Appearance
embedding is limited to vehicles at least 64 px tall and the eight largest per
frame; plate reading to vehicles at least 110 px tall and the four largest.
Below those sizes the operation cannot produce a usable result, so it is cost
without information - and embedding tiny crops also pollutes the
re-identification gallery with vectors that weaken genuine matches.

**Tier-2 escalation is admitted, not granted.** Tier-2 assumes traffic is
intermittent. On city junctions every camera wants it continuously, and their
combined demand exceeded what one GPU could process: 40 frames/second demanded
against roughly 25 available. A bounded number of cameras hold a tier-2 slot at
once; the rest continue at tier-1 and are promoted as slots free. Processing
fewer cameras properly is worth more than processing all of them badly.

**Scene-time anchoring is opportunistic.** Reading a timestamp overlay costs
about a second of OCR. Detection is the primary duty, so anchoring is attempted
only while the queue has slack and skipped whenever frames are backing up. A
camera anchors a little later instead of the pipeline stalling.

Together these took frame loss on this workload from 71% to 0%, and vehicle
throughput from 1,646 to 4,893 over the same interval.

---

## 8. Scaling to approximately 80,000 cameras

The single-node figures above do not scale by multiplication; the architecture
scales by distribution.

**Edge inference.** The tier-1/tier-2 scheduler runs unchanged on regional
nodes. A node processes its local cameras and transmits structured metadata —
detections, embeddings, evidence crops — rather than video. This is the decisive
factor for statewide viability: continuous video from 80,000 cameras is not a
bandwidth profile the State backbone should be asked to carry, and it is not
necessary.

**Sizing.** At the measured ~6 ms per frame and 1 fps tier-1 baseline, one RTX
5050-class GPU sustains roughly 150–200 cameras at tier-1 with escalation
headroom. Approximately 400–550 such nodes cover 80,000 cameras; fewer with
datacentre-class accelerators. Regional nodes are sized to district camera
counts, and a node failure affects only its own district.

**Storage.** Metadata rather than video dominates: a detection row with an
evidence crop is on the order of tens of kilobytes against gigabytes per camera
per day for continuous recording. Hot, warm and cold tiers follow retention
policy; departments retain their own video under existing arrangements.

**Central tier.** Stateless API services behind a load balancer; PostgreSQL with
PostGIS partitioned by time and district; an event bus between edge nodes and
the centre. Every component here is horizontally scalable, and none of it holds
video.

**Onboarding at scale.** The registry already onboards by bulk import, manual
entry and API, and profiles capability by probing. Adding a department is a
catalogue import, not an integration project.

---

## 9. Prerequisites from participating departments

To assess integration feasibility, each department should supply:

1. Camera inventory with location, ownership and site contact.
2. Stream access method — RTSP/ONVIF endpoints, VMS API details, or SDK.
3. Credentials and the network path or firewall rules to reach them.
4. VMS platform, vendor and version where a VMS is in use.
5. Retention policy and storage architecture.
6. Any legal or privacy constraints on the feed.

Where a camera is reachable, NETRA discovers codec, resolution, frame rate and
usable signal quality itself. This is not merely convenient: on the Government
grid, none of that information was supplied, and 20% of cameras turned out to be
incapable of supporting analytics at all. A statewide programme needs that
determined automatically and continuously.

---

## 10. Assumptions and limitations

Stated explicitly, because a proposal that hides them is less useful.

1. **Plate recognition is not achievable on the wide-area night cameras of the
   Government grid.** Demonstrated by measurement in §2.4. ANPR is demonstrated
   on participant-supplied footage where plate geometry permits it, and on the
   Government grid the platform delivers vehicle-level analytics plus a measured
   account of why plate recognition is unavailable.
2. Camera coordinates are junction-level approximations resolved from catalogue
   place names, since no geography is supplied. Accurate enough for mapping,
   clustering and space-time filtering; a surveyed gazetteer would replace them.
3. Cross-camera correlation is valid only within a shared recording session.
   Sightings from different sessions are excluded with the reason recorded.
4. Appearance re-identification produces ranked candidates for operator
   confirmation, not identification.
5. Scaling figures are extrapolated from measured single-node throughput and
   would be confirmed by a district-level pilot.

---

## 11. Deployment

Containerised on the CUDA 12.8 runtime required by Blackwell GPUs, with model
weights and the database on a mounted volume. A health endpoint supports
orchestration probes. The platform runs on-premise by design: police video does
not leave the State network, and the same image is what runs at a regional edge
node.

---

## Appendices

- `docs/feed-recon-findings.md` — full measured properties of all 30 grid cameras
- `README.md` — build, run and configuration
- Source repository — https://github.com/Vatsa10/sentinel
