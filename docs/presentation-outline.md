# Solution Presentation — slide-by-slide content

For submission deliverable 1 (Solution Presentation, PPT/PDF). Each section
below is one slide. Text is written to be used close to as-is.

The presentation's argument is deliberately different from what most entries
will claim, and the difference is the reason to select this entry: **we measured
what the State's cameras can actually deliver, and built for that.**

---

## Slide 1 — Title

**NETRA**
Networked Evidence, Tracking & Recognition for Analytics

Unified CCTV Viewing and Analytics Platform for Gujarat Police
Gujarat Police Innovation Challenge 2026

Solution model: **Model 2** (Unified Viewing and Selective Analytics)
built on **Model 1** (Centralised Registry and GIS Mapping)

*Speaker note: state up front that every figure in this deck is measured on the
Government grid, not projected.*

---

## Slide 2 — The problem in one line

26 departments. Thousands of cameras. No shared view, no shared registry, and
no way to ask a single question of the whole network.

An officer tracing a vehicle today queries disconnected control rooms one at a
time, over days.

---

## Slide 3 — What we found when we measured the grid

Before designing anything, we profiled all 30 Government cameras.

| Finding | Measured |
|---|---|
| Codecs | 24 × H.264, 6 × H.265 |
| Resolutions | 5 distinct, a 7× spread in pixel count |
| Frame rates | 30 / 25 / 20 / 12 / 10 fps — **and 2 cameras declaring `0/0`** |
| Metadata supplied | id and name only. No codec, geography, health or ownership |
| Cameras unable to support analytics | **7 of 30** — corrupt, black, or indoors with no traffic |

*Speaker note: this slide establishes credibility. Nobody else will have this.*

---

## Slide 4 — The finding that changed the design

We attempted number plate recognition on the three best-positioned cameras.

| Camera | Frames | Vehicles | Large enough to attempt | **Plates read** |
|---|---|---|---|---|
| cam12 Adalaj toll | 899 | ~70 | ~18 | **0** |
| cam17 Rajkot | 890 | 109 | 32 | **0** |
| cam18 Rajkot | 902 | 25 | 18 | **0** |

**2,691 frames. 200+ vehicles. Zero readable plates.**

These are wide-area junction overview cameras at night. A plate spans 10–20
pixels under headlight glare. This is a property of the installed
infrastructure, not of any recognition model.

*Speaker note: say plainly — a system that claims ANPR on these feeds would be
misrepresenting what the State's cameras can do. We designed for what they can.*

---

## Slide 5 — So we built a platform that does not depend on plates

Four independent signals, fused, with the reasoning preserved:

| Signal | Role |
|---|---|
| **Exact plate** | Decisive where OCR resolves it. A registration number identifies a vehicle. |
| **Partial plate** | 4 of 10 characters is a strong constraint, not a failure |
| **Appearance** | 512-d re-identification embedding, colour, class — carries the night cameras |
| **Space-time** | Vetoes pairs requiring impossible travel speed |

Two rules enforced in code and covered by tests:
- Appearance alone **never** raises an alert. Two silver hatchbacks are not evidence.
- A matched plate is **not** argued down by disagreeing colour on a night camera.

Every alert shows the operator which signals fired and at what confidence.

---

## Slide 6 — Architecture

*(Insert the diagram from `docs/high-level-design.md` §3.1)*

- **Registry (Model 1)** — identity, geography, and capability discovered by probing
- **Source adapters** — RTSP / HLS / file behind one interface; new protocol = one adapter
- **Ingest supervisor** — PTS-driven timing, backoff reconnection, loop-cut handling
- **Inference engine** — sole GPU owner, two-tier scheduling
- **Correlation** — fusion matching, cross-camera tracing
- **Console** — GIS map, video wall, alerts, search, assistant

Existing departmental systems are untouched. No centralised video recording.

---

## Slide 7 — Two-tier scheduling: how it scales

Every camera scanned at ~1 fps to answer only *"are there vehicles here?"*
Cameras with traffic escalate to ~5 fps for the expensive work, then decay back.

GPU budget follows traffic instead of being spread across cameras watching an
empty road at 3am.

**The same tiering is the 80,000-camera answer.** Regional edge nodes run this
unchanged and transmit metadata, not video — which is what makes statewide
coverage viable on existing backbone capacity.

---

## Slide 8 — Recovering time, so tracing means something

The grid replays recordings. Two cameras watched at the same moment show scenes
recorded at different times, so capture time cannot order sightings.

Every camera burns a timestamp into the frame. NETRA reads it once per
connection and carries it forward on PTS.

Anchoring succeeds on **15 of 30** cameras, most above 0.9 confidence. Readings
below a confidence floor, or outside a plausible recording year, are rejected —
an early misread produced the year 0921 and would have mis-timed every sighting
on that camera. **No scene time is better than a wrong one.**

---

## Slide 9 — Measured performance

Hardware: single RTX 5050 laptop GPU, 8 GB.

| Measurement | Result |
|---|---|
| Inference latency | **~6 ms** per 1080p frame |
| Throughput | **~52 fps** |
| Live run | 6/6 cameras, **5,846 vehicles** detected and embedded in 2 minutes |
| Frames dropped | **0** |
| Registry onboarding | 30 cameras probed and profiled in ~35 s |

Tier-1 across all 30 cameras needs ~30 fps against ~52 fps measured.

---

## Slide 10 — Demonstration: Government feed

Live, on the Sentinel grid:

1. Registry onboards 30 cameras and profiles what each can deliver
2. GIS map — cameras by capability and live health
3. Video wall — WebRTC direct from the gateway
4. Vehicle detection, classification and colour across the grid
5. **Gap analysis** — which cameras cannot deliver analytics, and why
6. Cross-camera appearance tracing

---

## Slide 11 — Demonstration: own feed

Full plate recognition where plate geometry permits it:

1. Video onboarded through the same adapter as a live camera
2. ANPR reads plates; OCR confusions (G→6, 0→O) folded automatically
3. Match against watchlist (stolen / wanted / blacklisted / missing)
4. **Real-time alert at 0.95 confidence** with full reasoning and evidence image
5. Route reconstruction with timestamped movement history

Measured on the validation run: 329 vehicles, 44 plates, 5 alerts, correct
severity and case reference on every one.

---

## Slide 12 — Beyond the mandatory requirements

| Capability | Why it matters |
|---|---|
| Automatic camera capability profiling | The State's first evidence-based view of which cameras actually work |
| Appearance re-identification | Tracing works where ANPR cannot — most of this grid |
| Scene-time recovery from overlays | Makes cross-camera correlation provable rather than plausible |
| Explainable alerts | Every machine judgement can be reviewed and overruled |
| Control-room assistant | Plain-language questions answered from live data, never invented |
| Email and webhook alerting | Reaches an officer who is not watching the screen |
| Full audit trail | Every query recorded with actor, target and time |

---

## Slide 13 — Scaling to 80,000 cameras

- **Edge inference** — regional nodes ship metadata, not video
- **Sizing** — ~150–200 cameras per RTX 5050-class GPU at tier-1; ~400–550 nodes statewide, fewer with datacentre accelerators
- **Storage** — detection rows and evidence crops, tens of kilobytes, against gigabytes per camera per day for continuous recording
- **Central tier** — stateless APIs, PostgreSQL/PostGIS partitioned by time and district
- **Onboarding** — adding a department is a catalogue import, not an integration project
- **Failure domain** — a node outage affects one district

---

## Slide 14 — Security, privacy and accountability

- No centralised video recording. Evidence crops only, tied to a detection.
- Role-based access with department-scoped camera visibility
- Every registry, route and vehicle query written to an audit log
- Credentials never committed; supplied by environment
- RTSP forced over TCP; on-premise by design — police video does not leave the State network
- **No unexplained machine judgements.** Every alert carries its reasoning.

---

## Slide 15 — What we are not claiming

*Speaker note: this slide wins trust. Deliver it confidently, not apologetically.*

- Plate recognition is **not achievable** on the wide-area night cameras of this grid, by any system. We measured it and we say so.
- Appearance matching produces **ranked candidates for operator confirmation**, not identification.
- Camera coordinates are junction-level approximations; no geography was supplied.
- Scaling figures are extrapolated from measured single-node throughput and would be confirmed by a district pilot.

A platform that overstates what it can do is worse than useless to an
investigating officer.

---

## Slide 16 — Close

**What NETRA delivers today, measured on your grid:**

30 cameras onboarded and profiled · 5,846 vehicles detected in 2 minutes ·
0 frames dropped · 7 faulty cameras identified automatically ·
end-to-end ANPR to alert verified · cross-camera tracing without plates

**Built by a solo student researcher.**

Repository: github.com/Vatsa10/sentinel

---

## Appendix slides (hold in reserve for questions)

- **A1** Per-camera measured properties (from `docs/feed-recon-findings.md`)
- **A2** Integrator-brief compliance table (from `README.md`)
- **A3** Data model and API surface
- **A4** Why SQLite by default and PostgreSQL/PostGIS in production
