# NETRA: Next.js console, landing page, tunnel deployment and submission pack

Date: 2026-09-07. Deadline: submission 15 Sept 2026, event 22–23 Sept 2026.

## 1. Decisions (all approved by Vatsa)

| Topic | Decision |
|---|---|
| Frontend | Full rewrite in Next.js 15 (App Router, static export), Tailwind, shadcn/ui, TanStack Query, react-leaflet, hls.js, lucide icons. Lives in `frontend/` of the same repo. Deployed on Vercel (root dir `frontend`). |
| Backend | Unchanged FastAPI + GPU pipeline on the laptop (RTX 5050), exposed via Cloudflare Tunnel (`cloudflared tunnel --url http://localhost:8080`); ngrok as fallback. |
| Access | Console opens as **viewer with no login**. Top-right Sign in accepts an API key, role badge becomes operator/admin, unlocks mutations. `data/api_keys.json` shipped with operator + admin keys; viewer requests need no key. Screening committee receives the operator key. |
| Live video | Default tile = MJPEG from backend with detection boxes drawn (≤6 fps). Per-tile "Smooth" toggle = HLS relay via on-demand ffmpeg, max 4 concurrent, idle-kill 60 s. |
| Branding | Gujarat Police palette: navy `#0a1628` / near-black base, saffron `#FF6B00` single accent, status green/amber/red for data only. Wordmark "NETRA" with Devanagari `नेत्र` as the logo mark. Headings: Plus Jakarta Sans (matches portal). Data/numbers: JetBrains Mono. |
| Time | Scene time shown only when corroborated; otherwise PTS-relative with an explicit badge. Never wall clock for cross-camera claims. |
| Scope | Everything in this spec is built now. Nothing deferred. |

Rubric gap audit that produced the scope (mandatory items marked M, bonus B):

- M Hosted URL + test credentials: this spec (tunnel + Vercel + RBAC UI).
- M Own-feed demo video 2–3 min: script + recording checklist in this spec, recorded by Vatsa.
- M Government-feed demo video + output report: report PDF endpoint + script here.
- M PPT/PDF presentation: built from `docs/presentation-outline.md` into a PPTX.
- M HLD refresh: new §16 Frontend/tunnel/RBAC UI.
- B RBAC/audit UI, camera-health view, email/webhook notification demo, intrusion naming for zones: all in scope.
- Not built, stated honestly in deck: facial recognition. Person detection + appearance ReID is the answer.

## 2. Architecture

```
Browser (Vercel, HTTPS)  ──HTTPS──▶  Cloudflare Tunnel  ──HTTP──▶  FastAPI :5050 (laptop, GPU)
   Next.js static bundle              *.trycloudflare.com          netra.api.app
   NEXT_PUBLIC_API_BASE=https://…                                   NETRA_CORS_ORIGINS=https://<vercel>.app
```

- All browser → backend calls go through `frontend/lib/api.ts`: prefixes base URL, adds `X-API-Key` from `localStorage["NETRA_API_KEY"]` when present, throws typed `ApiError` with status.
- Streams: MJPEG via `<img src>`, HLS via hls.js, alerts + pipeline status via existing `/ws/alerts` WebSocket (`wss://` through tunnel; Cloudflare supports WS).
- Legacy console remains mounted at `/legacy` as fallback if Vercel is down during judging.

## 3. Backend changes (Track A)

All in `netra/api/` unless stated. Each new module has `_self_check()` runnable via `python -m`.

### 3.1 CORS
`CORSMiddleware`, origins from env `NETRA_CORS_ORIGINS` (comma list; default `http://localhost:3000`). Allow headers `X-API-Key, Content-Type`, methods all, credentials off.

### 3.2 Annotated frame ring (`netra/pipeline.py`)
Pipeline keeps `latest_frames: dict[camera_id, LatestFrame(ts_pts, jpeg: bytes, annotated: bool)]`. Updated after each inference pass with boxes + labels (class, plate if voted, track id) drawn with OpenCV; for cameras sampled but not escalated, raw frame stored with `annotated=False`. JPEG quality 70, long edge ≤ 960 px. Encoding runs in the writer thread, never on the inference thread. Cameras with no subscriber in the last 10 s skip encoding (subscriber count maintained by the MJPEG endpoint).

### 3.3 MJPEG endpoint
`GET /api/cameras/{id}/live.mjpg` → `multipart/x-mixed-replace`, yields the latest frame when its `ts_pts` changes, max 6 fps, heartbeat re-send every 2 s if unchanged so the `<img>` never stalls. If the camera is not running in the pipeline, falls back to the cached snapshot every 2 s. Viewer role allowed.

### 3.4 HLS on demand (`netra/api/hls.py`)
- `POST /api/cameras/{id}/hls/start` → spawns `ffmpeg -rtsp_transport tcp -i <rtsp> -c copy -f hls -hls_time 2 -hls_list_size 5 -hls_flags delete_segments data/hls/{id}/index.m3u8` (HEVC sources transcode to H.264 `-c:v libx264 -preset veryfast -tune zerolatency` because browsers will not play HEVC in HLS). Returns `{url, started, concurrent}`. Refuses with 429 when 4 already running.
- `POST /api/cameras/{id}/hls/stop`, `GET /api/cameras/{id}/hls/status`.
- Static mount `/hls` → `data/hls`. Reaper thread kills processes with no playlist fetch for 60 s.
- Requires ffmpeg on PATH; `run.py --check` reports it.

### 3.5 Auth surface
- `GET /api/auth/whoami` → `{role, enabled, label}`; role `viewer` when no key or auth disabled.
- All existing mutation endpoints already gated by `auth.require(...)`; verify and list them in the plan (watchlist create/delete/seed, zones CRUD, pipeline start/stop, cameras onboard, export, retention, storage purge).
- `data/api_keys.json` generated by existing `auth.generate_keys()`; the operator and admin keys are written to `data/submission-credentials.md` (gitignored) for pasting into the form; never into `docs/`.

### 3.6 Camera health
`GET /api/cameras/health` → per camera: `state` (online/degraded/offline/not-started), `fps`, `stale_s`, `dropped_pct`, `reconnects`, `codec`, `resolution`, `last_detection_ts`. Sourced from `IngestSupervisor` state plus detection table. Used by Overview and Admin.

### 3.7 Notifications demo
`netra/core/notify.py` already has SMTP + webhook. Add `POST /api/notify/test` (operator) that sends one alert-formatted email/webhook and returns delivery result, and `GET /api/notify/config` (masked). Env vars documented in `docs/deploy.md`. Demo video shows a real email arriving.

### 3.8 Output report
Existing `/api/report` returns JSON; add `format=pdf` producing a PDF (reportlab is not installed → use HTML → print-CSS page served at `/api/report?format=html` and the frontend opens it for browser "Save as PDF"; no new heavy dependency). Report: camera list, detections with timestamps (scene/PTS labelled), plates where read, alerts, journeys, watermark "Generated by NETRA".

### 3.9 Housekeeping
- Mount legacy console at `/legacy`; root `/` redirects to the Vercel URL when `NETRA_FRONTEND_URL` is set, else to `/legacy`.
- `run.py --check` adds: ffmpeg present, CORS origins set, api keys present, tunnel hint.
- `docs/deploy.md`: cloudflared install, start commands (Windows), env vars, Vercel env, ngrok fallback, pre-demo checklist.

## 4. Frontend (Track B)

### 4.1 Stack and structure
```
frontend/
  app/layout.tsx              fonts, theme, QueryClientProvider, AuthProvider
  app/page.tsx                landing
  app/console/layout.tsx      shell
  app/console/page.tsx        overview
  app/console/{map,wall,vehicles,watchlist,alerts,zones,traffic,intelligence,assistant,admin}/page.tsx
  app/report/page.tsx         printable report view (opens backend HTML or renders JSON)
  lib/api.ts  lib/auth.tsx  lib/time.ts  lib/types.ts  lib/ws.ts
  components/ui/*             shadcn
  components/{Shell,RoleBadge,SignInDialog,CameraTile,VideoWall,MapView,AlertFeed,KpiStat,TimeBadge,EmptyState,ErrorBoundary,Gate}.tsx
  design-system/MASTER.md     tokens + rules (from ui-ux-pro-max output, adjusted)
  next.config.ts              output: "export", images unoptimized
  .env.example                NEXT_PUBLIC_API_BASE
```

### 4.2 Design tokens
```
--bg:        #070d1a   (page)        --surface: #0e1830   --surface-2: #142140
--border:    #22304f   --text: #e6ecf7   --muted: #8fa1c2
--accent:    #FF6B00   --accent-fg: #0a1628
--ok: #22c55e  --warn: #f59e0b  --bad: #ef4444  --info: #38bdf8
radius 10px cards / 6px controls; shadows minimal (1px border + subtle inner glow on hover only)
type: Plus Jakarta Sans 700 headings, 400/500 body 16px, JetBrains Mono for ids, plates, timestamps, counts
motion: 150–250 ms ease-out, respects prefers-reduced-motion; only alert arrival and tile hover animate
```
No emoji icons. Lucide only. Contrast ≥ 4.5:1 checked for muted text on surface.

### 4.3 Shell
Left sidebar (collapsible, icons+labels): Overview, Video Wall, GIS Map, Vehicles, Alerts, Watchlist, Zones & Intrusion, Traffic, Intelligence, Assistant, Admin. Top bar: नेत्र NETRA wordmark, pipeline status pill (running/stopped, cameras, fps, dropped %), backend latency dot, time-basis indicator, role badge + Sign in/out. Mobile: sidebar becomes a sheet; wall defaults to 1×1.

Backend unreachable → full-panel state with tunnel URL shown, retry button, auto-retry 5 s. Never a blank tab.

### 4.4 Pages
- **Overview**: KPI row (cameras online/total, detections last hour, active alerts, watchlist size, plates read), live alert feed (WS), camera health strip (30 tiles coloured by state), mini map, "Start pipeline" (operator).
- **Video Wall**: layouts 1/4/9/16, camera picker with search + group filter, per-tile: MJPEG default, Smooth (HLS) toggle, snapshot, fullscreen, detection overlay toggle, tile header with camera id, codec, fps, time badge. HLS start failure (429) → toast "4 smooth streams max".
- **GIS Map**: Leaflet dark tiles (CartoDB dark_matter), markers by department/type/status, cluster at low zoom, layer toggles, camera drawer (metadata, snapshot, open in wall), coverage circles, gap-analysis panel (list + highlight), onboarding drawer: manual form + CSV bulk import + API doc link, export CSV.
- **Vehicles**: search by plate (fuzzy), attributes (colour/type/make text), time range, camera; results table with crops; row → detail: sightings timeline, route on map (only corroborated), ReID matches with ambiguity flags, VLM description, "Describe" action.
- **Alerts**: live list + history, filters (type, camera, severity), acknowledge (operator), evidence crop + snapshot, jump to camera, jump to vehicle.
- **Watchlist**: table, add/edit/delete (operator), seed demo set, hit history per entry, import CSV.
- **Zones & Intrusion**: camera pick → snapshot canvas polygon editor (operator), rule type (intrusion/loitering/wrong-way/count line), event list with crops.
- **Traffic**: per-camera counts chart (Recharts), baselines, anomaly list with z-score, time-basis badge.
- **Intelligence**: journeys (chain view + map), cloned plates (pair cards with both crops + distance/time impossibility), anomalies.
- **Assistant**: chat UI over `/api/assistant`, shows intent + sources (entity, SQL facts, ReID), quick prompts.
- **Admin**: whoami, role matrix, audit log table (admin), API key info, storage + retention controls (admin), notify test (operator), health table, legacy console link.
- **Report** (`/report`): parameters (time range, cameras) → opens backend HTML report in new tab for Save-as-PDF.

### 4.5 Landing (`/`)
1. Hero: नेत्र mark, "NETRA", one-line "Unified CCTV viewing and vehicle intelligence for Gujarat Police. Model 1 + Model 2.", live status strip with real numbers from `/api/detections/stats` + health (graceful skeleton if backend down), CTA "Open Console", secondary "Read the HLD".
2. Problem strip: 26 departments, ~80,000 cameras, no common view.
3. Live preview: one MJPEG tile + mini map, captioned "This is live, not a mockup".
4. Capabilities grid (8): unified viewing, registry+GIS, ANPR with plate voting, watchlist alerts, cross-camera ReID + journeys, cloned-plate detection, zones/intrusion + traffic baselines, VLM attributes + assistant.
5. How it works: five-node pipeline diagram (feeds → adaptive sampling → detection/ANPR/ReID → metadata store → console/alerts), bandwidth 159× callout.
6. Measured, not promised: 0% dropped frames on 5–8 cams, ~13 ms/frame, alerts at 0.95 on own feed, honest note on grid plate legibility.
7. Scale plan: 80k cameras, regional edge nodes, cost band, DR (summary from HLD §).
8. Security: RBAC roles, audit trail, no video stored centrally, credentials never in bundle.
9. CTA + links: console, repo, HLD, demo videos.
Responsive at 375/768/1024/1440. Lighthouse a11y ≥ 95.

### 4.6 Auth UX
`AuthProvider` loads key from localStorage, calls whoami, exposes `role`. `<Gate min="operator">` wraps mutation controls: renders disabled with tooltip "Sign in as operator" for viewer. 401/403 from API → toast + opens SignInDialog. Sign out clears key.

## 5. Submission pack (Track C)

- `docs/high-level-design.md` §16: frontend, tunnel, RBAC UI, streaming modes, sequence diagram.
- `docs/submission/presentation.pptx` built with python-pptx, listed in `requirements-docs.txt` (docs-only, not runtime). 14–16 slides following `docs/presentation-outline.md`, screenshots from the new console.
- `docs/submission/demo-own-feed-script.md` (2–3 min, shot list, what to say, RBAC 10 s, email alert 10 s).
- `docs/submission/demo-gov-feed-script.md` + output report generation steps.
- `docs/submission/form-answers.md`: every Google Form field text, links placeholders for YouTube/Drive, hosted URL, operator key.
- `docs/deploy.md` pre-demo checklist: start backend, start tunnel, set Vercel env (or use `NEXT_PUBLIC_API_BASE` runtime override via `?api=` query param stored to localStorage so a new tunnel URL does not need a redeploy), verify `/api/auth/whoami`, verify one MJPEG tile.

Runtime API override: because free Cloudflare quick tunnels change URL on each start, the frontend reads `?api=https://…` once, stores to localStorage, and prefers it over the build-time env. Admin page shows and lets you change it.

## 6. Testing

- Backend: `_self_check()` for `hls.py`, health, notify test (dry-run mode), report HTML; curl smoke script `tools/smoke_api.sh` hitting all 36+ endpoints as viewer and operator, asserting expected 200/403.
- Frontend: `npm run build` clean (static export), ESLint clean, Playwright smoke: each console route renders heading, wall shows an `<img>` with mjpg src, sign-in changes badge, viewer sees disabled delete. Run against local backend.
- End-to-end: laptop backend + tunnel + Vercel preview from a phone on mobile data: landing loads < 3 s, wall tile animates, alert arrives in feed.

## 7. Parallel tracks and order

Track A (backend) and Track B (frontend) run concurrently from the start; B uses local backend on :5050. Track C starts once B has Overview + Wall + Map to screenshot. Final: deploy, e2e, record videos (Vatsa), fill form (Vatsa).

## 8. Out of scope, stated

Facial recognition. Native mobile apps. Multi-tenant department accounts beyond the three roles. Persistent named tunnel (needs a domain; documented as the production path).
