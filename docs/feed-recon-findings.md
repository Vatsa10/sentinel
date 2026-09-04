# Sentinel Camera Grid — Reconnaissance Findings
*Recorded 2026-09-01. Source: live probing of the official sandbox grid.*

## Access

| Item | Value |
|---|---|
| Portal | `https://cctv.corp8.cloud/` (access password gated) |
| Catalogue | `https://cctv.corp8.cloud/cameras.json` — **public, no auth**, but 403s a default `urllib` User-Agent. Send a browser UA. |
| HLS | `https://cctv.corp8.cloud/<id>/index.m3u8` — CDN, password |
| RTSP | `rtsp://103.250.160.189:8554/stream/<id>` — direct public IP |
| WHEP | `http://103.250.160.189:8889/stream/<id>/whep` |
| Camera ids | `cam01` … `cam30` (**30 cameras, not ~50**) |

Ports 8554 and 8889 confirmed reachable from the dev machine (~33 ms). Gateway is
MediaMTX (default port signature 8554 / 8889 / 8189).

## Authentication (verified working)

`POST https://cctv.corp8.cloud/auth/login` with form field `password`.
Returns `302` and sets cookie `sentinel=<token>`, `Max-Age=31536000` (one year),
`HttpOnly; Secure; SameSite=Lax`. Send that cookie on subsequent CDN requests.

```bash
COOKIE=$(curl -s -X POST https://cctv.corp8.cloud/auth/login   -H "User-Agent: Mozilla/5.0"   --data-urlencode "password=<ACCESS_PASSWORD>"   -D - -o /dev/null | grep -i '^set-cookie:' | sed 's/.*sentinel=/sentinel=/;s/;.*//')
curl -s https://cctv.corp8.cloud/cameras.json -H "Cookie: $COOKIE"
```

Notes:
- A default `urllib`/`python-requests` User-Agent is rejected by Cloudflare. Send a browser UA.
- Do not follow redirects on the POST; `-L` re-issues it as a GET and yields `405`.
- **`/api/ingest` returns `404` on this host.** It appears in an earlier revision of the
  integrator guide but does not exist here. The real contract is `cameras.json`,
  which returns only `id` and `name` — no codec, resolution, geo or live status.
- **RTSP on `103.250.160.189:8554` requires no authentication.** Only the CDN/HLS
  host is password-gated.

**Consequence for Model 1:** per-camera technical metadata is not supplied and must be
discovered by probing each stream with `ffprobe` at onboarding time, then persisted in
the registry. This is a genuine registry capability rather than a workaround, and it
mirrors how a real deployment would enrol cameras of unknown provenance.

## Verified stream properties

| Camera | Codec | Resolution | Declared fps |
|---|---|---|---|
| cam01 Chimanbhai Bridge | h264 | 1920×1080 | 30/1 |
| cam06 Timbavadi Gate, Junagadh | **hevc** | 1920×1080 | **0/0 (invalid)** |
| cam17 Rajkot Bus Port | hevc | 1920×1080 | 25/1 |
| cam30 Gandhidham Rambaugh | h264 | 1920×1080 | 25/1 |

Mixed H.264/HEVC confirmed. `cam06` declaring `0/0` is direct evidence for the
brief's warning against trusting reported frame rate — a pipeline that divides by
declared fps crashes or silently corrupts timing on that camera.

## Geographic clustering

Camera names carry real Gujarat locations, geocodable to genuine coordinates.

| Cluster | Cameras | Count |
|---|---|---|
| Ahmedabad | cam01 Chimanbhai Bridge, cam02 Janpath, cam03 ONGC, cam04 Paldi Circle, cam05 Visat, cam13 CN Vidhyalaya, cam14 Delight RLVD, cam15 Suvidha Park, cam16 Visat P2 | 9 |
| Junagadh | cam06 Timbavadi, cam08 Majewadi, cam09 New Bypass, cam10 Char Chowk, cam11 Dolatpara | 5 |
| Bilimora / Navsari | cam19 Khapariya, cam27, cam28, cam29 | 4 |
| Rajkot | cam17, cam18 | 2 |
| Singletons | cam07 Gir Somnath, cam12 Adalaj Toll, cam20 Mohanpura, cam21 Patan, cam22 BK Mervada, cam23 Kheram, cam24 Dehgam, cam25 Dhanori, cam26 Tankal, cam30 Gandhidham | 10 |

**Why this matters:** dense clusters (Ahmedabad 9, Junagadh 5) make cross-camera
route reconstruction physically plausible. A vehicle genuinely traverses several
cameras within a cluster. Scattered singletons could not support a route claim.

## Critical finding — these are overview cameras, not ANPR cameras

Sample frames from cam04 (Paldi Circle) and cam14 (Delight RLVD) show wide-area
junction surveillance at night: number plates occupy roughly 10–20 px, degraded
further by headlight glare and motion blur.

**Consequence:** an off-the-shelf plate-detector-plus-OCR pipeline will have very
low recall on this grid. Any design whose watchlist matching depends solely on a
clean plate string will demonstrate an almost empty result set.

## Second critical finding — the loops are time-aligned *within groups only*

An initial two-camera sample suggested the whole grid shared a clock. A full
30-camera survey shows that is **not** true. Burned-in timestamps fall into
separate recording sessions:

| Group | Cameras | Recorded timestamp | Cross-camera tracing |
|---|---|---|---|
| Ahmedabad CSITMS | cam01–cam05, cam13, cam14, cam15 | 13-06-2026 ~23:27–23:29 | **Yes — 8 cameras, shared clock** |
| Junagadh | cam08–cam11 | 13-06-2026 ~23:28 | Yes, within cluster |
| Unaligned singletons | cam06 (17-06), cam16 (13-06 17:16), cam20 (04-08), cam26 (03-08), cam23/cam27/cam30 (08-08), cam17, cam18, cam21 | various | No |

**Consequence:** genuine multi-hop route reconstruction is demonstrable, but on the
**Ahmedabad group of eight**, not across all thirty. The evaluation demo should be
built around that group. Claims of statewide cross-camera tracing must be framed as
architectural capability, not as something the sandbox data can evidence.

## Design implications

1. Matching must fuse multiple signals, not rely on plate text alone:
   ANPR where geometry permits; partial-plate fuzzy matching with confidence;
   vehicle appearance re-identification (type, colour, embedding); and
   space-time feasibility filtering using registry geometry.
2. Every timing calculation must derive from PTS. Never arrival time, never
   `CAP_PROP_FPS` — `cam06` proves the declared value can be meaningless.
3. Decoders must accept mixed H.264/HEVC and treat join-time reference-frame
   warnings as informational.
4. Loop discontinuity must reset tracker state, re-identification galleries and
   background models without being mistaken for a disconnect.
5. Per-stream supervision with exponential backoff, 2 s rising to a 30 s cap.
6. Concurrency must be paced — each client receives its own copy of the stream.

## Open items

- Geocode all 30 camera names to latitude/longitude for the GIS layer.
- Measure actual plate pixel height per camera to set the ANPR eligibility threshold
  quantitatively rather than by visual inspection.

## Full 30-camera survey (measured 2026-09-01)

All 30 streams returned a frame. Format spread:

- **Codecs:** 24 × H.264, 6 × HEVC (cam06, cam12, cam17, cam18, cam22, cam26)
- **Resolutions:** 2560×1440, 1920×1080, 1280×960, 1280×720, 960×576 — a ~7× spread in pixel count
- **Declared frame rates:** 30, 25, 20, 12, 10, and `0/0` on cam06 and cam30

A fixed-shape inference batch cannot span this grid. Letterboxing to a common
network input and per-camera buffer sizing are mandatory.

### Camera condition classification

| Condition | Cameras | Implication |
|---|---|---|
| Corrupt feed | cam08 (vertical colour bars), cam11 (smear banding), cam22 (fully overexposed) | No recoverable content; report as infrastructure fault |
| Effectively black | cam03, cam07, cam09 | Negligible usable signal at night |
| Indoor, no vehicles | cam28, cam29 (Bilimora bus terminals) | Plate recognition not applicable; person analytics only |
| Daylight, good quality | cam17 (06:45), cam18 (11:28), cam20 (06:26) | Highest-quality frames in the grid |
| Best ANPR candidate | **cam12 Adalaj Tollnaka** | Toll booth, close range, vehicles fill the frame |
| Night, workable overview | cam01, cam04, cam10, cam13, cam14, cam15, cam16, cam25, cam30 | Vehicle detection reliable; plates marginal |

Approximately **8 of 30 cameras cannot support plate recognition at all.**

### Design consequence — registry capability profiling

Because per-camera metadata is not supplied and camera quality varies this widely,
the registry derives a **capability profile** for every camera at onboarding:
signal health, illumination level, vehicle presence rate, and estimated plate pixel
height. The inference scheduler then allocates GPU budget by measured capability —
full ANPR only where plate geometry permits, vehicle detection and re-identification
on workable night cameras, person analytics on the indoor pair, and health alerting
only on the corrupt feeds.

This satisfies the Model 1 requirement for *gap-analysis reports covering uncovered
zones and ageing infrastructure* using measured evidence rather than declared
metadata, and prevents GPU time being spent on cameras that cannot produce a result.
