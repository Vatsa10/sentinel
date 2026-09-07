# NETRA Frontend, Deployment and Submission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Next.js console + landing page on Vercel talking to the laptop backend through a Cloudflare tunnel, with live MJPEG/HLS video, RBAC UI, and the complete submission pack, by 15 Sept 2026.

**Architecture:** Static Next.js 15 export in `frontend/` calls the existing FastAPI backend (`netra.api.app`, port 8080) over HTTPS via `cloudflared`. Backend gains CORS, an annotated live-frame store + MJPEG endpoint, on-demand HLS relay, anonymous-viewer auth, health/notify/whoami endpoints. Legacy console stays at `/legacy`. Track A (backend) and Track B (frontend) run in parallel; Track C (docs/deck/scripts) follows once screenshots exist.

**Tech Stack:** Python 3.13 / FastAPI / OpenCV / ffmpeg; Next.js 15 (App Router, `output: "export"`), TypeScript, Tailwind v4, shadcn/ui, TanStack Query v5, react-leaflet v5, hls.js, lucide-react, Recharts; Playwright; python-pptx (docs only).

**Spec:** `docs/superpowers/specs/2026-09-07-frontend-and-submission-design.md`

## Global Constraints

- Backend port is **8080** (`python run.py`, `run.py:75`). Not 5050.
- Every new Python module has a `_self_check()` runnable with `python -m netra.<module>`; keep the existing style (`assert`, prints, exit non-zero on failure).
- No new runtime Python dependency. ffmpeg on PATH is already required for snapshots. `python-pptx` and `playwright` are docs/test-only.
- Nothing may starve detection: no work on the inference thread beyond a guarded `cv2.imencode` when a subscriber exists.
- Timing: never present wall time as scene time. `scene_time` shown only when `scene_time_corroborated` is true; otherwise show PTS-relative with a "stream time" badge.
- Frontend: all backend calls through `frontend/lib/api.ts`. No credentials in the bundle. `X-API-Key` only from localStorage.
- Design tokens exactly as in spec §4.2. Palette: bg `#070d1a`, surface `#0e1830`, surface-2 `#142140`, border `#22304f`, text `#e6ecf7`, muted `#8fa1c2`, accent `#FF6B00`, ok `#22c55e`, warn `#f59e0b`, bad `#ef4444`, info `#38bdf8`. Fonts: Plus Jakarta Sans (headings/body), JetBrains Mono (ids, plates, timestamps, counts). Icons: lucide-react only, no emoji icons.
- British spelling in UI copy and docs. `ponytail:` comments mark deliberate simplifications.
- Commit after every task with a normal message ending in `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never commit `data/`, `.env`, `*.pt`, `*.log`. Never push unless Vatsa asks.
- Auto-committer hazard: something with identity `Vatsa10` may commit working trees under invented messages. Before starting a task run `git status`; if unexpected commits appear, note the SHA in the task report and continue. Never rebase or rewrite history.
- Windows host. Shell examples are PowerShell/Git-Bash compatible; use forward slashes.

---

## File Structure

Backend (modify unless marked new):
- `netra/core/auth.py` — anonymous viewer in enforced mode.
- `netra/api/app.py` — CORS, whoami, `/legacy`, root redirect, MJPEG, HLS routes, health, notify routes, report print CSS hook.
- `netra/analytics/live_frames.py` (new) — `LiveFrameStore`: latest annotated JPEG per camera, subscriber counting.
- `netra/analytics/inference.py` — call store at end of `_process`.
- `netra/api/hls.py` (new) — on-demand ffmpeg HLS relay manager.
- `netra/api/health.py` (new) — camera health aggregation.
- `netra/core/notify.py` — `send_test()` + masked config.
- `netra/api/report.py` — print CSS, plate filter.
- `tools/make_keys.py` (new), `tools/smoke_api.py` (new), `run.py` (`--check` additions).
- `docs/deploy.md` (new).

Frontend (all new under `frontend/`): see spec §4.1; each task lists its files.

Docs: `docs/high-level-design.md` §16, `docs/submission/*`, `tools/build_deck.py`, `requirements-docs.txt`.

---

# Track A — Backend

### Task A1: CORS, anonymous viewer, whoami, legacy mount, key tooling

**Files:**
- Modify: `netra/core/auth.py:85-97` (`resolve`), `netra/core/auth.py:112+` (`_self_check`)
- Modify: `netra/api/app.py:35-60` (app creation), `netra/api/app.py:604-617` (web mounts)
- Modify: `netra/config.py` (two env settings)
- Create: `tools/make_keys.py`

**Interfaces:**
- Produces: `GET /api/auth/whoami -> {"role": str, "enabled": bool, "name": str}`; env `NETRA_CORS_ORIGINS`, `NETRA_FRONTEND_URL`; `auth.ANONYMOUS_VIEWER`.

- [ ] **Step 1: Add anonymous-viewer test to `auth._self_check`**

Append inside `_self_check()` in `netra/core/auth.py` (after existing asserts):

```python
    # Enforced mode: no key is a viewer, wrong key is refused.
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp()) / "keys.json"
    tmp.write_text(json.dumps({"k1": {"name": "op", "role": "operator"}}))
    global KEYS_PATH
    saved, KEYS_PATH = KEYS_PATH, tmp
    try:
        assert resolve(None).role == "viewer", "no key must be anonymous viewer"
        assert resolve("bogus") is None, "unknown key must be refused"
        assert resolve("k1").role == "operator"
    finally:
        KEYS_PATH = saved
    print("auth self-check ok")
```

- [ ] **Step 2: Run it, expect failure**

Run: `python -m netra.core.auth`
Expected: `AssertionError: no key must be anonymous viewer` (currently returns None).

- [ ] **Step 3: Implement**

In `netra/core/auth.py` after `ANONYMOUS = ...` add:

```python
#: Enforced mode caller with no key. Read-only; the screening committee opens
#: the console without a credential and signs in only to change things.
ANONYMOUS_VIEWER = Principal(name="anonymous", role="viewer", fingerprint="anon")
```

Change `resolve`:

```python
def resolve(api_key: str | None) -> Principal | None:
    """Identify the caller. None means a key was supplied but is not valid."""
    keys = load_keys()
    if not keys:
        return ANONYMOUS          # open mode
    if not api_key:
        return ANONYMOUS_VIEWER   # enforced mode, read-only without a key
    meta = keys.get(api_key)
    if not meta:
        return None
    return Principal(name=meta["name"], role=meta["role"],
                     fingerprint=_fingerprint(api_key))
```

`load_keys()` reads `KEYS_PATH` at call time already, so the test's rebinding works.

- [ ] **Step 4: Run self-check, expect pass**

Run: `python -m netra.core.auth` → `auth self-check ok`.

- [ ] **Step 5: Config + CORS + whoami + mounts**

`netra/config.py`, near other env reads:

```python
# Browser origins allowed to call the API (the Vercel console). Comma list.
CORS_ORIGINS = [o.strip() for o in
                os.getenv("NETRA_CORS_ORIGINS", "http://localhost:3000").split(",")
                if o.strip()]
# Where the hosted console lives; root "/" redirects there when set.
FRONTEND_URL = os.getenv("NETRA_FRONTEND_URL", "")
```

`netra/api/app.py` right after `app = FastAPI(...)`:

```python
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_methods=["*"],
    allow_headers=["X-API-Key", "Content-Type"],
    allow_credentials=False,
)
```

Add near the audit endpoint:

```python
@app.get("/api/auth/whoami")
def whoami(_p=Depends(require("read"))):
    return {"role": _p.role, "name": _p.name, "enabled": auth.enabled()}
```

Replace the `/` route and static mount block (`app.py:607-617`) with:

```python
from fastapi.responses import RedirectResponse

@app.get("/", include_in_schema=False)
def root():
    if config.FRONTEND_URL:
        return RedirectResponse(config.FRONTEND_URL, status_code=302)
    return RedirectResponse("/legacy/", status_code=302)


@app.get("/legacy/", response_class=HTMLResponse, include_in_schema=False)
def legacy_console():
    index = WEB_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>NETRA</h1><p>Console not built.</p>")
    return HTMLResponse(index.read_text(encoding="utf-8"))


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
```

Check `netra/web/index.html` references `/static/app.js` (absolute). If relative, change to absolute so `/legacy/` works.

- [ ] **Step 6: `tools/make_keys.py`**

```python
"""Generate API keys for the demo and write the credentials hand-out.

    python tools/make_keys.py

Writes data/api_keys.json (read by the server) and
data/submission-credentials.md (paste into the submission form). Both are
gitignored. Re-running rotates every key.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from netra import config          # noqa: E402
from netra.core import auth       # noqa: E402


def main() -> None:
    keys = auth.generate_keys()
    out = config.DATA / "submission-credentials.md"
    out.write_text(
        "# NETRA test credentials for the screening committee\n\n"
        "Console opens read-only without a key. Paste a key via Sign in "
        "(top right) to unlock the role.\n\n"
        f"- Operator key (recommended for judges): `{keys['operator']}`\n"
        f"- Admin key: `{keys['admin']}`\n"
        f"- Viewer key (not needed): `{keys['viewer']}`\n",
        encoding="utf-8")
    print(f"keys written to {auth.KEYS_PATH}\nhand-out written to {out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Verify live**

```bash
python tools/make_keys.py
python run.py --port 8080 &   # or start in a second terminal
curl -s localhost:8080/api/auth/whoami                       # {"role":"viewer",...}
curl -s -H "X-API-Key: $(python -c "import json;print([k for k,v in json.load(open('data/api_keys.json')).items() if v['role']=='operator'][0])")" localhost:8080/api/auth/whoami
curl -s -X DELETE localhost:8080/api/watchlist/999999 -o /dev/null -w "%{http_code}\n"   # 403
curl -s -o /dev/null -w "%{http_code}\n" -H "Origin: http://localhost:3000" -X OPTIONS -H "Access-Control-Request-Method: GET" localhost:8080/api/cameras   # 200
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" localhost:8080/    # 302 /legacy/
```

Then `rm data/api_keys.json` if you are not the final deploy (keeps other tasks' local testing in open mode), and say so in the report.

- [ ] **Step 8: Commit**

```bash
git add netra/core/auth.py netra/api/app.py netra/config.py tools/make_keys.py
git commit -m "Anonymous viewer, CORS, whoami and legacy mount for the hosted console"
```

---

### Task A2: LiveFrameStore + annotated frames from the engine

**Files:**
- Create: `netra/analytics/live_frames.py`
- Modify: `netra/analytics/inference.py:634-636` (end of `_process`), `netra/analytics/inference.py` engine `__init__` (~line 222)
- Modify: `netra/config.py`

**Interfaces:**
- Produces: `LIVE_FRAMES: LiveFrameStore` singleton with
  `put(camera_id: str, image, detections: list, pts_ms: float) -> None`,
  `get(camera_id: str) -> tuple[float, bytes] | None` (pts_ms, jpeg),
  `subscribe(camera_id) -> None`, `unsubscribe(camera_id) -> None`,
  `wanted(camera_id) -> bool`.
- Consumes: `VehicleDetection` fields `bbox [x1,y1,x2,y2]`, `vehicle_class`, `plate_text`, `confidence` (`inference.py:152`).

- [ ] **Step 1: Write the module with its self-check**

```python
"""Latest annotated frame per camera, for the console's live tiles.

The pipeline already decodes every sampled frame; this keeps the newest one
per camera as a JPEG with detection boxes drawn, so the console can show what
the AI is looking at without a second RTSP connection per viewer.

Encoding happens only while at least one MJPEG client is subscribed to that
camera, so an unwatched grid costs nothing. ponytail: the encode runs on the
inference thread (about 2 ms at 960 px); move it to a worker if infer_ms
rises measurably with many tiles open.
"""
from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from netra import config

_LABEL_FONT = cv2.FONT_HERSHEY_SIMPLEX
_BOX = (0, 107, 255)       # BGR of saffron #FF6B00
_TXT = (255, 255, 255)


def annotate(image, detections: list) -> "np.ndarray":
    """Draw boxes + labels. Returns a resized copy, never touches the input."""
    h, w = image.shape[:2]
    scale = min(1.0, config.LIVE_MAX_EDGE / max(h, w))
    out = cv2.resize(image, (int(w * scale), int(h * scale))) if scale < 1 else image.copy()
    for d in detections:
        x1, y1, x2, y2 = [int(v * scale) for v in d.bbox]
        cv2.rectangle(out, (x1, y1), (x2, y2), _BOX, 2)
        label = d.vehicle_class
        if d.plate_text:
            label += f" {d.plate_text}"
        (tw, th), _ = cv2.getTextSize(label, _LABEL_FONT, 0.5, 1)
        cv2.rectangle(out, (x1, max(0, y1 - th - 6)), (x1 + tw + 6, y1), _BOX, -1)
        cv2.putText(out, label, (x1 + 3, y1 - 4), _LABEL_FONT, 0.5, _TXT, 1, cv2.LINE_AA)
    return out


class LiveFrameStore:
    def __init__(self) -> None:
        self._frames: dict[str, tuple[float, bytes]] = {}
        self._subs: dict[str, int] = {}
        self._lock = threading.Lock()

    def subscribe(self, camera_id: str) -> None:
        with self._lock:
            self._subs[camera_id] = self._subs.get(camera_id, 0) + 1

    def unsubscribe(self, camera_id: str) -> None:
        with self._lock:
            n = self._subs.get(camera_id, 0) - 1
            if n <= 0:
                self._subs.pop(camera_id, None)
                self._frames.pop(camera_id, None)
            else:
                self._subs[camera_id] = n

    def wanted(self, camera_id: str) -> bool:
        return self._subs.get(camera_id, 0) > 0

    def put(self, camera_id: str, image, detections: list, pts_ms: float) -> None:
        if not self.wanted(camera_id):
            return
        ok, buf = cv2.imencode(".jpg", annotate(image, detections),
                               [cv2.IMWRITE_JPEG_QUALITY, config.LIVE_JPEG_QUALITY])
        if ok:
            with self._lock:
                self._frames[camera_id] = (pts_ms, buf.tobytes())

    def get(self, camera_id: str) -> tuple[float, bytes] | None:
        with self._lock:
            return self._frames.get(camera_id)


LIVE_FRAMES = LiveFrameStore()


def _self_check() -> None:
    from types import SimpleNamespace
    store = LiveFrameStore()
    img = np.zeros((1080, 1920, 3), dtype=np.uint8)
    det = SimpleNamespace(bbox=[100, 100, 400, 300], vehicle_class="car",
                          plate_text="GJ01AB1234", confidence=0.9)
    store.put("c1", img, [det], 1000.0)
    assert store.get("c1") is None, "must not encode without a subscriber"
    store.subscribe("c1")
    t0 = time.perf_counter()
    store.put("c1", img, [det], 2000.0)
    ms = (time.perf_counter() - t0) * 1000
    pts, jpeg = store.get("c1")
    assert pts == 2000.0 and jpeg[:2] == b"\xff\xd8", "expected a JPEG"
    decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert max(decoded.shape[:2]) <= config.LIVE_MAX_EDGE
    store.unsubscribe("c1")
    assert store.get("c1") is None, "frame dropped when last subscriber leaves"
    print(f"live_frames self-check ok (encode {ms:.1f} ms)")
    assert ms < 25, "encode too slow for the inference thread"


if __name__ == "__main__":
    _self_check()
```

`netra/config.py`:

```python
# Live MJPEG tiles for the console.
LIVE_MAX_EDGE = int(os.getenv("NETRA_LIVE_MAX_EDGE", "960"))
LIVE_JPEG_QUALITY = int(os.getenv("NETRA_LIVE_JPEG_QUALITY", "70"))
LIVE_MJPEG_FPS = float(os.getenv("NETRA_LIVE_MJPEG_FPS", "6"))
```

- [ ] **Step 2: Run** `python -m netra.analytics.live_frames` → ok line with encode ms.

- [ ] **Step 3: Hook into the engine**

`netra/analytics/inference.py`: import `from netra.analytics.live_frames import LIVE_FRAMES` at top. At the end of `_process`, just before `self.stats["processed"] += 1`:

```python
        try:
            LIVE_FRAMES.put(frame.camera_id, img, detections, frame.pts_ms)
        except Exception:
            log.exception("live frame encode failed for %s", frame.camera_id)
```

Note: `detections` here is the list built in `_process`; confirm its name at `inference.py:630-634` (`for det in detections`). Frames that return early (degraded/dark) publish nothing; the MJPEG endpoint falls back to snapshot.

- [ ] **Step 4: Verify** `python -m netra.analytics.inference` still passes its self-check. Then a 30 s live run: `python verify.py --seconds 30 --cameras cam13,cam14` shows `dropped=0` as before (no subscribers, so zero cost).

- [ ] **Step 5: Commit**

```bash
git add netra/analytics/live_frames.py netra/analytics/inference.py netra/config.py
git commit -m "Live annotated frame store fed by the inference pass"
```

---

### Task A3: MJPEG endpoint

**Files:**
- Modify: `netra/api/app.py` (after the snapshot endpoint, ~line 268)

**Interfaces:**
- Produces: `GET /api/cameras/{camera_id}/live.mjpg` (multipart/x-mixed-replace). Viewer role.
- Consumes: `LIVE_FRAMES` from A2, `_cached_snapshot`, `_snapshot_lock`, `_grab_snapshot` (`app.py:186-243`).

- [ ] **Step 1: Implement**

```python
from netra.analytics.live_frames import LIVE_FRAMES

_MJPEG_BOUNDARY = b"--netraframe"


def _mjpeg_part(jpeg: bytes) -> bytes:
    return (_MJPEG_BOUNDARY + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
            + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")


@app.get("/api/cameras/{camera_id}/live.mjpg")
async def camera_live_mjpeg(camera_id: str, request: Request,
                            _p=Depends(require("read"))):
    """Latest annotated frames as a motion-JPEG stream, at most LIVE_MJPEG_FPS.

    While the pipeline is processing this camera the frames carry detection
    boxes. Otherwise the cached snapshot is re-sent every two seconds so the
    tile still shows the scene. A frame is re-sent every two seconds even when
    unchanged so browsers and the tunnel never see an idle connection.
    """
    with SessionLocal() as db:
        if not db.get(Camera, camera_id):
            raise HTTPException(404, "camera not found")

    async def gen():
        import time as _time
        LIVE_FRAMES.subscribe(camera_id)
        last_pts, last_sent, interval = None, 0.0, 1.0 / config.LIVE_MJPEG_FPS
        try:
            while True:
                if await request.is_disconnected():
                    break
                now = _time.time()
                item = LIVE_FRAMES.get(camera_id)
                if item and item[0] != last_pts:
                    last_pts = item[0]
                    yield _mjpeg_part(item[1]); last_sent = now
                elif now - last_sent >= 2.0:
                    jpeg = item[1] if item else _cached_snapshot(camera_id)
                    if jpeg is None:
                        try:
                            with _snapshot_lock(camera_id):
                                jpeg = _cached_snapshot(camera_id) or _grab_snapshot(camera_id)
                        except HTTPException:
                            jpeg = None
                    if jpeg:
                        yield _mjpeg_part(jpeg); last_sent = now
                await asyncio.sleep(interval)
        finally:
            LIVE_FRAMES.unsubscribe(camera_id)

    return StreamingResponse(
        gen(), media_type="multipart/x-mixed-replace; boundary=netraframe",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
```

`_grab_snapshot` blocks up to `SNAPSHOT_TIMEOUT_S`; wrap that call in `await asyncio.to_thread(...)` so one slow camera does not stall the event loop:

```python
                            jpeg = await asyncio.to_thread(_snapshot_sync, camera_id)
```
with
```python
def _snapshot_sync(camera_id: str) -> bytes | None:
    try:
        with _snapshot_lock(camera_id):
            return _cached_snapshot(camera_id) or _grab_snapshot(camera_id)
    except HTTPException:
        return None
```

- [ ] **Step 2: Verify**

Start server, start pipeline on two cameras (`curl -X POST "localhost:8080/api/pipeline/start?cameras=cam13,cam14"`), then:

```bash
curl -s -m 5 localhost:8080/api/cameras/cam13/live.mjpg | grep -ac "Content-Type: image/jpeg"
```
Expected: ≥ 5 parts in 5 s. Open `http://localhost:8080/api/cameras/cam13/live.mjpg` in a browser: boxes visible on vehicles. Stop the pipeline; the same URL still shows a snapshot refreshing every ~2 s. `curl -s localhost:8080/api/pipeline/status | python -m json.tool | grep infer_ms` before and with three tiles open: difference < 3 ms.

- [ ] **Step 3: Commit** `git commit -am "MJPEG live tiles with detection overlays"`

---

### Task A4: On-demand HLS relay

**Files:**
- Create: `netra/api/hls.py`
- Modify: `netra/api/app.py` (routes + static mount), `run.py` (`--check` ffmpeg line), `netra/config.py`

**Interfaces:**
- Produces: `HLS.start(camera_id) -> dict`, `HLS.stop(camera_id) -> bool`, `HLS.status(camera_id) -> dict`, `HLS.touch(camera_id)`; routes `POST /api/cameras/{id}/hls/start`, `POST .../hls/stop`, `GET .../hls/status`, static `/hls/{id}/index.m3u8`.
- Consumes: `config.rtsp_url(camera_id)`, `Camera.codec` column (check `netra/core/models.py` for the field name; the registry records codec from probing).

- [ ] **Step 1: Write module + self-check (self-check uses a synthetic source)**

```python
"""On-demand HLS relay: one ffmpeg per watched camera, capped, self-cleaning.

The console's default tile is MJPEG with overlays. When an operator wants
smooth full-rate video for one camera they toggle "Smooth", which starts a
relay here. Relays die 60 s after the last playlist fetch, and at most
HLS_MAX_CONCURRENT run at once so the tunnel is never saturated.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

from netra import config

log = logging.getLogger(__name__)
HLS_DIR = config.DATA / "hls"


class _Relay:
    def __init__(self, camera_id: str, proc: subprocess.Popen):
        self.camera_id = camera_id
        self.proc = proc
        self.started = time.time()
        self.last_touch = time.time()


class HlsManager:
    def __init__(self, max_concurrent: int = None, idle_s: float = None):
        self.max_concurrent = max_concurrent or config.HLS_MAX_CONCURRENT
        self.idle_s = idle_s or config.HLS_IDLE_S
        self._relays: dict[str, _Relay] = {}
        self._lock = threading.Lock()
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True,
                                        name="hls-reaper")
        self._reaper.start()

    # -- command -------------------------------------------------------------
    @staticmethod
    def command(source: str, out_dir: Path, hevc: bool) -> list[str]:
        video = (["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                  "-g", "50"] if hevc else ["-c:v", "copy"])
        return ["ffmpeg", "-v", "error", "-nostdin",
                *(["-rtsp_transport", "tcp"] if source.startswith("rtsp") else
                  ["-re", "-stream_loop", "-1"]),
                "-i", source, *video, "-an",
                "-f", "hls", "-hls_time", "2", "-hls_list_size", "6",
                "-hls_flags", "delete_segments+omit_endlist",
                str(out_dir / "index.m3u8")]

    def start(self, camera_id: str, source: str, hevc: bool) -> dict:
        with self._lock:
            live = self._relays.get(camera_id)
            if live and live.proc.poll() is None:
                live.last_touch = time.time()
                return self.status(camera_id)
            running = [r for r in self._relays.values() if r.proc.poll() is None]
            if len(running) >= self.max_concurrent:
                return {"camera_id": camera_id, "running": False,
                        "error": f"at most {self.max_concurrent} smooth streams",
                        "concurrent": len(running)}
            out_dir = HLS_DIR / camera_id
            shutil.rmtree(out_dir, ignore_errors=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen(self.command(source, out_dir, hevc),
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE)
            self._relays[camera_id] = _Relay(camera_id, proc)
        return self.status(camera_id)

    def stop(self, camera_id: str) -> bool:
        with self._lock:
            r = self._relays.pop(camera_id, None)
        if not r:
            return False
        if r.proc.poll() is None:
            r.proc.terminate()
            try:
                r.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                r.proc.kill()
        shutil.rmtree(HLS_DIR / camera_id, ignore_errors=True)
        return True

    def touch(self, camera_id: str) -> None:
        r = self._relays.get(camera_id)
        if r:
            r.last_touch = time.time()

    def status(self, camera_id: str) -> dict:
        r = self._relays.get(camera_id)
        running = bool(r and r.proc.poll() is None)
        playlist = HLS_DIR / camera_id / "index.m3u8"
        return {"camera_id": camera_id, "running": running,
                "ready": running and playlist.exists(),
                "url": f"/hls/{camera_id}/index.m3u8",
                "age_s": round(time.time() - r.started, 1) if r else None,
                "concurrent": sum(1 for x in self._relays.values()
                                  if x.proc.poll() is None),
                "max_concurrent": self.max_concurrent}

    def stop_all(self) -> None:
        for cid in list(self._relays):
            self.stop(cid)

    # -- reaper --------------------------------------------------------------
    def _reap_loop(self) -> None:
        while True:
            time.sleep(5)
            now = time.time()
            for cid, r in list(self._relays.items()):
                dead = r.proc.poll() is not None
                idle = now - r.last_touch > self.idle_s
                if dead or idle:
                    if dead:
                        err = (r.proc.stderr.read() or b"").decode(errors="replace")[-300:]
                        log.warning("hls relay %s exited: %s", cid, err.strip())
                    self.stop(cid)


HLS = HlsManager()


def _self_check() -> None:
    import tempfile
    assert shutil.which("ffmpeg"), "ffmpeg not on PATH"
    # Synthetic source: 3 s of colour bars as a file, relayed as HLS.
    tmp = Path(tempfile.mkdtemp())
    src = tmp / "bars.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=10", "-t", "3", "-pix_fmt",
                    "yuv420p", str(src)], check=True)
    m = HlsManager(max_concurrent=1, idle_s=4)
    st = m.start("selfcheck", str(src), hevc=False)
    assert st["running"], st
    for _ in range(40):
        if m.status("selfcheck")["ready"]:
            break
        time.sleep(0.25)
    assert m.status("selfcheck")["ready"], "playlist never appeared"
    denied = m.start("second", str(src), hevc=False)
    assert not denied["running"] and "at most" in denied["error"]
    time.sleep(10)          # idle > 4 s: reaper must have stopped it
    assert not m.status("selfcheck")["running"], "reaper did not stop idle relay"
    assert not (HLS_DIR / "selfcheck").exists()
    print("hls self-check ok")


if __name__ == "__main__":
    _self_check()
```

`netra/config.py`:
```python
HLS_MAX_CONCURRENT = int(os.getenv("NETRA_HLS_MAX_CONCURRENT", "4"))
HLS_IDLE_S = float(os.getenv("NETRA_HLS_IDLE_S", "60"))
```

- [ ] **Step 2: Run** `python -m netra.api.hls` → `hls self-check ok` (takes ~15 s).

- [ ] **Step 3: Routes + mount in `app.py`**

```python
from netra.api.hls import HLS, HLS_DIR

HLS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/hls", StaticFiles(directory=str(HLS_DIR)), name="hls")


def _camera_or_404(db, camera_id: str) -> Camera:
    cam = db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(404, "camera not found")
    return cam


@app.post("/api/cameras/{camera_id}/hls/start")
def hls_start(camera_id: str, _p=Depends(require("read"))):
    with SessionLocal() as db:
        cam = _camera_or_404(db, camera_id)
        hevc = (getattr(cam, "codec", "") or "").lower() in ("hevc", "h265")
        source = config.rtsp_url(camera_id)
    st = HLS.start(camera_id, source, hevc)
    if not st["running"]:
        raise HTTPException(429, st.get("error", "relay unavailable"))
    return st


@app.post("/api/cameras/{camera_id}/hls/stop")
def hls_stop(camera_id: str, _p=Depends(require("read"))):
    return {"stopped": HLS.stop(camera_id)}


@app.get("/api/cameras/{camera_id}/hls/status")
def hls_status(camera_id: str, _p=Depends(require("read"))):
    HLS.touch(camera_id)          # the player polls this while it plays
    return HLS.status(camera_id)
```

Static playlist fetches do not pass through `touch()`; the frontend polls `hls/status` every 20 s while a Smooth tile is open (Task B4). Own-feed cameras (source spec = file) must relay the file: look at how `source_specs` are stored for own-feed cameras in `app.py:812-869` and use that path instead of `rtsp_url` when present (the `command()` handles file sources with `-re -stream_loop -1`).

Add to `run.py` `check()` after the module loop:
```python
    import shutil
    print(f"  ffmpeg     {'ok' if shutil.which('ffmpeg') else 'MISSING (snapshots, HLS)'}")
```

- [ ] **Step 4: Verify live**

```bash
curl -s -X POST localhost:8080/api/cameras/cam13/hls/start
sleep 6; curl -s localhost:8080/hls/cam13/index.m3u8 | head
for c in cam14 cam15 cam01 cam02; do curl -s -o /dev/null -w "%{http_code} " -X POST localhost:8080/api/cameras/$c/hls/start; done; echo   # 200 200 200 429
```
Play `http://localhost:8080/hls/cam13/index.m3u8` in VLC or hls.js demo page. Wait 70 s without status polls: `hls/status` shows `running:false`, directory gone.

- [ ] **Step 5: Commit** `git add netra/api/hls.py netra/api/app.py netra/config.py run.py && git commit -m "On-demand HLS relay, capped and self-reaping"`

---

### Task A5: Camera health endpoint

**Files:**
- Create: `netra/api/health.py`
- Modify: `netra/api/app.py` (route)

**Interfaces:**
- Produces: `GET /api/cameras/health -> list[CameraHealth]` where each item is
  `{camera_id, name, city, capability, state: "online"|"degraded"|"offline"|"not-started", fps, stale_s, dropped_pct, reconnects, loop_cuts, escalated, codec, resolution, last_detection_at, last_error}`.
- Consumes: `PIPELINE.supervisor.health()` (`stream.py:246`), `PIPELINE.engine.stats`, `PIPELINE.engine.dark_cameras()`, `Camera` columns (open `netra/core/models.py` and use the actual codec/width/height/fps field names).

- [ ] **Step 1: Implement `netra/api/health.py`**

```python
"""Per-camera health for the console: registry facts + live ingest state."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func

from netra.core.db import SessionLocal
from netra.core.models import Camera, Detection

STALE_DEGRADED_S = 5.0
STALE_OFFLINE_S = 30.0


def classify(cam: Camera, live: dict | None, dark: set[str]) -> str:
    if cam.capability == "degraded":
        return "degraded"
    if live is None:
        return "not-started"
    stale = live.get("stale_s")
    if not live.get("connected") or stale is None or stale > STALE_OFFLINE_S:
        return "offline"
    if stale > STALE_DEGRADED_S or cam.id in dark:
        return "degraded"
    return "online"


def camera_health(pipeline) -> list[dict]:
    live = {h["camera_id"]: h for h in pipeline.supervisor.health()}
    dark = set(pipeline.engine.dark_cameras() or [])
    with SessionLocal() as db:
        cams = db.query(Camera).order_by(Camera.id).all()
        last = dict(db.query(Detection.camera_id, func.max(Detection.wall_time))
                    .group_by(Detection.camera_id).all())
    out = []
    for cam in cams:
        h = live.get(cam.id)
        seen, emitted = (h or {}).get("frames_seen", 0), (h or {}).get("frames_emitted", 0)
        out.append({
            "camera_id": cam.id, "name": cam.name, "city": cam.city,
            "capability": cam.capability,
            "state": classify(cam, h, dark),
            "fps": (h or {}).get("measured_fps"),
            "stale_s": (h or {}).get("stale_s"),
            "dropped_pct": round(100 * (1 - emitted / seen), 1) if seen else None,
            "reconnects": (h or {}).get("reconnects", 0),
            "loop_cuts": (h or {}).get("loop_cuts", 0),
            "escalated": (h or {}).get("escalated", False),
            "codec": getattr(cam, "codec", None),
            "resolution": f"{cam.width}x{cam.height}" if getattr(cam, "width", None) else None,
            "last_detection_at": last[cam.id].isoformat() if last.get(cam.id) else None,
            "last_error": (h or {}).get("last_error"),
            "dark": cam.id in dark,
        })
    return out


def _self_check() -> None:
    from types import SimpleNamespace as NS
    cam = NS(id="c", capability="vehicle")
    assert classify(cam, None, set()) == "not-started"
    assert classify(cam, {"connected": True, "stale_s": 1.0}, set()) == "online"
    assert classify(cam, {"connected": True, "stale_s": 9.0}, set()) == "degraded"
    assert classify(cam, {"connected": True, "stale_s": 1.0}, {"c"}) == "degraded"
    assert classify(cam, {"connected": False, "stale_s": 1.0}, set()) == "offline"
    assert classify(NS(id="d", capability="degraded"), None, set()) == "degraded"
    print("health self-check ok")


if __name__ == "__main__":
    _self_check()
```

Note `dropped_pct` here is ingest sampling ratio, not inference drops; label it `sampled_pct` instead if `frames_emitted/frames_seen` means "sampled" (read `stream.py:120-150` to confirm) and expose engine-level drops from `pipeline.status()["inference"]["dropped"]` in the Overview instead. Pick the truthful name; report it.

Route in `app.py` (must be declared **before** `/api/cameras/{camera_id}/snapshot` so `health` is not captured as a camera id):

```python
from netra.api.health import camera_health

@app.get("/api/cameras/health")
def cameras_health(_p=Depends(require("read"))):
    return camera_health(PIPELINE)
```

- [ ] **Step 2: Verify** `python -m netra.api.health`; `curl -s localhost:8080/api/cameras/health | python -m json.tool | head -30` with and without the pipeline running.

- [ ] **Step 3: Commit** `git add netra/api/health.py netra/api/app.py && git commit -m "Camera health endpoint for the console"`

---

### Task A6: Notification test + masked config

**Files:**
- Modify: `netra/core/notify.py`, `netra/api/app.py`

**Interfaces:**
- Produces: `GET /api/notify/config -> {email: bool, webhook: bool, smtp_host, smtp_user_masked, to, min_severity}`; `POST /api/notify/test -> {email: {"sent": bool, "error": str|null}, webhook: {...}}` (operator).
- Consumes: `NotifyConfig` fields (`notify.py:34-52`), `render_email`, `Notifier._send_email/_send_webhook`.

- [ ] **Step 1: Add to `notify.py`**

```python
def masked(cfg: NotifyConfig) -> dict:
    def mask(s: str) -> str:
        return s if len(s) < 4 else s[:2] + "…" + s[-2:]
    return {"email": bool(cfg.smtp_host and cfg.email_to),
            "webhook": bool(cfg.webhook_url),
            "smtp_host": cfg.smtp_host, "smtp_user": mask(cfg.smtp_user),
            "to": cfg.email_to, "min_severity": cfg.min_severity}


def send_test(notifier: "Notifier") -> dict:
    """Send one synthetic critical alert through every configured channel, synchronously."""
    alert = {"plate": "GJ01AB1234", "severity": "critical", "category": "stolen",
             "camera_id": "cam13", "camera_name": "Test camera",
             "score": 0.97, "detected_at": datetime.now(timezone.utc).isoformat(),
             "reason": "NETRA notification test", "test": True}
    out = {}
    for name, fn, on in (("email", notifier._send_email, masked(notifier.cfg)["email"]),
                         ("webhook", notifier._send_webhook, masked(notifier.cfg)["webhook"])):
        if not on:
            out[name] = {"sent": False, "error": "not configured"}
            continue
        try:
            fn(alert); out[name] = {"sent": True, "error": None}
        except Exception as exc:   # report, never raise: this is a diagnostic
            out[name] = {"sent": False, "error": str(exc)[:200]}
    return out
```

Use the real attribute names from `NotifyConfig` (`email_to`, `webhook_url`, `min_severity` may differ; open the dataclass and match). Extend `_self_check` with `assert masked(NotifyConfig(smtp_user="alice@x"))["smtp_user"] == "al…@x"` style assertion using actual defaults, and `assert send_test(Notifier(NotifyConfig()))["email"]["error"] == "not configured"`.

Routes:

```python
from netra.core import notify as notify_mod
from netra.core.notify import NOTIFIER

@app.get("/api/notify/config")
def notify_config(_p=Depends(require("read"))):
    return notify_mod.masked(NOTIFIER.cfg)


@app.post("/api/notify/test")
def notify_test(_p=Depends(require("acknowledge"))):
    result = notify_mod.send_test(NOTIFIER)
    _audit(_p, "notify.test", "-", json.dumps(result))   # use the existing audit helper name at app.py:50-57
    return result
```

- [ ] **Step 2: Verify** `python -m netra.core.notify`; with `NETRA_SMTP_*` + `NETRA_EMAIL_TO` set to Vatsa's Gmail app password (documented in `docs/deploy.md`, Task A7), `curl -X POST localhost:8080/api/notify/test` returns `sent:true` and the mail arrives. Without config: `not configured`.

- [ ] **Step 3: Commit** `git commit -am "Notification test endpoint and masked config"`

---

### Task A7: Report polish, smoke script, run.py check, deploy docs

**Files:**
- Modify: `netra/api/report.py`, `netra/api/app.py:1247-1257`, `run.py`
- Create: `tools/smoke_api.py`, `docs/deploy.md`

- [ ] **Step 1: Report**

In `build_report` add a `plate: str | None = None` parameter filtering `plate_rows` with `Detection.plate_text.ilike(f"%{plate}%")`, a `cameras: list[str] | None` filter, and in the HTML `<head>` add print CSS:

```css
@media print { body{background:#fff;color:#000} .no-print{display:none} table{page-break-inside:auto} tr{page-break-inside:avoid} }
@page { size: A4; margin: 14mm }
```
plus a top-right `<button class="no-print" onclick="window.print()">Save as PDF</button>` and a footer line "Generated by NETRA at {generated} UTC. Times labelled 'scene' are corroborated overlay clocks; 'stream' times are PTS-relative." Each timestamp cell must show which it is. Pass `plate` and `cameras` through from the route (`?plate=&cameras=cam01,cam02&hours=`).

- [ ] **Step 2: `tools/smoke_api.py`**

```python
"""Hit every console endpoint as viewer and operator; assert expected status.

    python tools/smoke_api.py [--base http://localhost:8080]
"""
import argparse, json, sys, urllib.request, urllib.error
from pathlib import Path

CHECKS = [  # (method, path, role, expected)
    ("GET", "/api/auth/whoami", None, 200),
    ("GET", "/api/cameras", None, 200),
    ("GET", "/api/cameras/health", None, 200),
    ("GET", "/api/cameras/gap-analysis", None, 200),
    ("GET", "/api/detections?limit=5", None, 200),
    ("GET", "/api/detections/stats", None, 200),
    ("GET", "/api/watchlist", None, 200),
    ("GET", "/api/alerts", None, 200),
    ("GET", "/api/pipeline/status", None, 200),
    ("GET", "/api/zones", None, 200),
    ("GET", "/api/zones/events", None, 200),
    ("GET", "/api/traffic/live", None, 200),
    ("GET", "/api/traffic/history", None, 200),
    ("GET", "/api/analytics/baselines", None, 200),
    ("GET", "/api/analytics/anomalies", None, 200),
    ("GET", "/api/analytics/cloned-plates", None, 200),
    ("GET", "/api/analytics/journeys", None, 200),
    ("GET", "/api/storage", None, 200),
    ("GET", "/api/audit", None, 200),
    ("GET", "/api/notify/config", None, 200),
    ("GET", "/api/report?hours=1", None, 200),
    ("GET", "/api/cameras/cam13/hls/status", None, 200),
    ("POST", "/api/watchlist/seed", None, 403),
    ("POST", "/api/watchlist/seed", "operator", 200),
    ("POST", "/api/pipeline/stop", "operator", 403),
    ("POST", "/api/notify/test", "operator", 200),
    ("POST", "/api/storage/prune?dry_run=true", "admin", 200),
]


def call(base, method, path, key):
    req = urllib.request.Request(base + path, method=method)
    if key:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default="http://localhost:8080")
    a = ap.parse_args()
    keys = {}
    kp = Path("data/api_keys.json")
    if kp.exists():
        for k, v in json.load(open(kp)).items():
            keys[v["role"]] = k
    else:
        print("no data/api_keys.json: open mode, role checks expect 200 everywhere")
    bad = 0
    for method, path, role, expected in CHECKS:
        if role and role not in keys and not kp.exists():
            expected = 200
        if not kp.exists() and expected == 403:
            expected = 200
        got = call(a.base, method, path, keys.get(role) if role else None)
        mark = "ok " if got == expected else "BAD"
        bad += got != expected
        print(f"{mark} {method:4} {path:45} as {role or 'anon':8} -> {got} (want {expected})")
    print(f"{len(CHECKS)-bad}/{len(CHECKS)} passed"); sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: `run.py --check` additions** after the port loop:

```python
    from netra.core import auth as _auth
    print(f"  api keys   {'enforced' if _auth.enabled() else 'OPEN MODE (run tools/make_keys.py before hosting)'}")
    print(f"  cors       {', '.join(config.CORS_ORIGINS)}")
    print(f"  frontend   {config.FRONTEND_URL or '(unset: / serves /legacy/)'}")
    print("  tunnel     cloudflared tunnel --url http://localhost:8080")
```

- [ ] **Step 4: `docs/deploy.md`** with these sections, fully written: Prerequisites (Python venv, ffmpeg, cloudflared via `winget install Cloudflare.cloudflared`, Node 22); Environment variables table (`NETRA_GRID_EMAIL/PASSWORD` or `data/credentials.json`, `NETRA_CORS_ORIGINS`, `NETRA_FRONTEND_URL`, `NETRA_SMTP_HOST/PORT/USER/PASSWORD`, `NETRA_EMAIL_TO`, `NETRA_WEBHOOK_URL`, `NETRA_HLS_MAX_CONCURRENT`); Start sequence (`python tools/make_keys.py` once → `python run.py --port 8080` → `cloudflared tunnel --url http://localhost:8080` → copy the `https://….trycloudflare.com` URL → open `https://<vercel-app>/console?api=https://….trycloudflare.com` once); Vercel setup (import repo, root dir `frontend`, framework Next.js, env `NEXT_PUBLIC_API_BASE` optional because of the `?api=` override); ngrok fallback (`ngrok http 8080`, note free ngrok interstitial page breaks `<img>` MJPEG unless `ngrok-skip-browser-warning` header; so Cloudflare first); Pre-demo checklist (12 lines: GPU visible, pipeline started on the 8 ahmedabad/junagadh cams + own feed, whoami viewer via tunnel, one MJPEG tile via tunnel from phone, alert WS connected, keys file present, credentials hand-out ready, OBS ready). Production path paragraph: named tunnel + domain, or the A5000 box.

- [ ] **Step 5: Verify** `python tools/smoke_api.py` all pass in both modes; `python run.py --check` shows new lines; open `/api/report?hours=24&plate=GJ` and print preview looks clean.

- [ ] **Step 6: Commit** `git add -A tools docs/deploy.md run.py netra/api/report.py netra/api/app.py && git commit -m "Report print view, API smoke script, deploy guide"`

---

# Track B — Frontend (`frontend/`)

Runs in parallel with Track A against a local backend on `http://localhost:8080`. Until A-tasks land, endpoints not yet present (`/api/auth/whoami`, `/api/cameras/health`, `live.mjpg`, `hls/*`, `/api/notify/*`) must degrade gracefully: the UI shows "not available" rather than breaking. Shapes for existing endpoints: read the route in `netra/api/app.py` at the line given in each task and mirror the JSON keys in `lib/types.ts`.

### Task B1: Scaffold, design tokens, API client, auth context

**Files:**
- Create: `frontend/` via `create-next-app`, then `frontend/app/globals.css`, `frontend/app/layout.tsx`, `frontend/lib/api.ts`, `frontend/lib/auth.tsx`, `frontend/lib/time.ts`, `frontend/lib/types.ts`, `frontend/lib/query.tsx`, `frontend/design-system/MASTER.md`, `frontend/.env.example`, `frontend/next.config.ts`, `frontend/vercel.json`
- Modify: root `.gitignore` (add `frontend/node_modules`, `frontend/.next`, `frontend/out`, `frontend/test-results`)

**Interfaces:**
- Produces:
  - `api<T>(path: string, init?: RequestInit & {json?: unknown}): Promise<T>` throwing `ApiError {status, message}`; `apiBase(): string`; `apiUrl(path): string` (for `<img>`/hls); `setApiBase(url)`.
  - `useAuth(): {role: Role, name: string, enabled: boolean, key: string|null, signIn(key): Promise<Role>, signOut(): void, can(min: Role): boolean, refresh(): void}` where `type Role = "viewer"|"operator"|"admin"`.
  - `fmtTime(det: {scene_time?: string|null; scene_time_corroborated?: boolean; pts_ms: number; wall_time: string}): {label: string, basis: "scene"|"stream"}`.
  - `<QueryProvider>` wrapping TanStack Query with `refetchInterval` default 4000 and `retry: 1`.

- [ ] **Step 1: Scaffold**

```bash
cd "D:/Files/Vatsa/Projects/Senitel Gujarat Hackathon"
npx --yes create-next-app@15 frontend --ts --tailwind --eslint --app --src-dir=false --import-alias "@/*" --use-npm --no-turbopack
cd frontend
npx --yes shadcn@latest init -d
npx --yes shadcn@latest add button card badge dialog input label table tabs tooltip sheet select separator skeleton toast dropdown-menu scroll-area switch textarea alert
npm i @tanstack/react-query lucide-react hls.js leaflet react-leaflet @types/leaflet recharts clsx
npm i -D @playwright/test
```

`next.config.ts`:
```ts
import type { NextConfig } from "next";
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
  reactStrictMode: true,
};
export default nextConfig;
```
`vercel.json`: `{ "framework": "nextjs", "outputDirectory": "out" }`.
`.env.example`: `NEXT_PUBLIC_API_BASE=http://localhost:8080`.

- [ ] **Step 2: Tokens + fonts**

`app/globals.css` (Tailwind v4 `@theme`):
```css
@import "tailwindcss";
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');

@theme {
  --color-bg: #070d1a; --color-surface: #0e1830; --color-surface-2: #142140;
  --color-border: #22304f; --color-text: #e6ecf7; --color-muted: #8fa1c2;
  --color-accent: #FF6B00; --color-accent-fg: #0a1628;
  --color-ok: #22c55e; --color-warn: #f59e0b; --color-bad: #ef4444; --color-info: #38bdf8;
  --font-sans: "Plus Jakarta Sans", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;
  --radius-card: 10px; --radius-ctl: 6px;
}
html { color-scheme: dark; }
body { background: var(--color-bg); color: var(--color-text); font-family: var(--font-sans); font-size: 16px; line-height: 1.5; }
.mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
:focus-visible { outline: 3px solid color-mix(in srgb, var(--color-accent) 70%, transparent); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; } }
```
Map shadcn CSS variables (`--background`, `--card`, `--primary`, `--border`, `--muted-foreground`, `--destructive`) onto these tokens in the same file so shadcn components inherit the palette (primary = accent, primary-foreground = accent-fg, background = bg, card = surface, border = border, muted-foreground = muted).

`design-system/MASTER.md`: write the token table above, spacing scale 4/8/12/16/24/32/48, type scale 12/14/16/18/24/32/44, motion 150–250 ms ease-out, rules: one primary CTA per view, lucide icons only, numbers in `.mono`, status colours never carry meaning alone (icon or text alongside), contrast ≥ 4.5:1 (muted `#8fa1c2` on surface `#0e1830` is 6.9:1).

- [ ] **Step 3: `lib/api.ts`**

```ts
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
const KEY = "NETRA_API_KEY", BASE = "NETRA_API_BASE";

export function apiBase(): string {
  if (typeof window !== "undefined") {
    const q = new URLSearchParams(window.location.search).get("api");
    if (q) { try { localStorage.setItem(BASE, q.replace(/\/$/, "")); } catch {} }
    try { const s = localStorage.getItem(BASE); if (s) return s; } catch {}
  }
  return (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8080").replace(/\/$/, "");
}
export function setApiBase(url: string) { try { localStorage.setItem(BASE, url.replace(/\/$/, "")); } catch {} }
export function apiUrl(path: string): string { return apiBase() + path; }
export function wsUrl(path: string): string { return apiBase().replace(/^http/, "ws") + path; }
export function getKey(): string | null { try { return localStorage.getItem(KEY); } catch { return null; } }
export function setKey(k: string | null) { try { k ? localStorage.setItem(KEY, k) : localStorage.removeItem(KEY); } catch {} }

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const key = getKey(); if (key) headers.set("X-API-Key", key);
  let body = init.body;
  if (init.json !== undefined) { headers.set("Content-Type", "application/json"); body = JSON.stringify(init.json); }
  let res: Response;
  try { res = await fetch(apiUrl(path), { ...init, headers, body, cache: "no-store" }); }
  catch { throw new ApiError(0, "Backend unreachable"); }
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.detail ?? j.error ?? msg; } catch {}
    throw new ApiError(res.status, String(msg));
  }
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("json") ? res.json() : res.text()) as Promise<T>;
}
```

- [ ] **Step 4: `lib/auth.tsx`**

```tsx
"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError, getKey, setKey } from "./api";

export type Role = "viewer" | "operator" | "admin";
const RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };
type Who = { role: Role; name: string; enabled: boolean };
type Ctx = Who & { key: string | null; can: (min: Role) => boolean; signIn: (k: string) => Promise<Role>; signOut: () => void; refresh: () => void };
const AuthCtx = createContext<Ctx | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [who, setWho] = useState<Who>({ role: "viewer", name: "anonymous", enabled: false });
  const [key, setK] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    setK(getKey());
    try { setWho(await api<Who>("/api/auth/whoami")); }
    catch (e) {
      // Backend older than Task A1 (no whoami) or unreachable: assume open mode as admin
      // so nothing is gated, matching the server's open-mode behaviour.
      if (e instanceof ApiError && e.status === 404) setWho({ role: "admin", name: "open mode", enabled: false });
      if (e instanceof ApiError && e.status === 401) { setKey(null); setK(null); setWho({ role: "viewer", name: "anonymous", enabled: true }); }
    }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  const signIn = async (k: string) => {
    setKey(k.trim());
    try { const w = await api<Who>("/api/auth/whoami"); setWho(w); setK(k.trim()); return w.role; }
    catch (e) { setKey(null); throw e; }
  };
  const signOut = () => { setKey(null); refresh(); };
  const can = (min: Role) => RANK[who.role] >= RANK[min];
  return <AuthCtx.Provider value={{ ...who, key, can, signIn, signOut, refresh }}>{children}</AuthCtx.Provider>;
}
export function useAuth() { const c = useContext(AuthCtx); if (!c) throw new Error("AuthProvider missing"); return c; }
```

- [ ] **Step 5: `lib/time.ts`, `lib/query.tsx`, `lib/types.ts`**

```ts
// time.ts
export function fmtTime(d: { scene_time?: string | null; scene_time_corroborated?: boolean | null; pts_ms: number; wall_time?: string }) {
  if (d.scene_time && d.scene_time_corroborated) {
    return { label: new Date(d.scene_time).toLocaleString("en-IN", { hour12: false }), basis: "scene" as const };
  }
  const s = d.pts_ms / 1000, h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return { label: `T+${h ? h + ":" : ""}${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`, basis: "stream" as const };
}
export const ago = (iso: string) => { const s = (Date.now() - new Date(iso).getTime()) / 1000; return s < 60 ? `${s | 0}s ago` : s < 3600 ? `${(s / 60) | 0}m ago` : `${(s / 3600) | 0}h ago`; };
```
```tsx
// query.tsx
"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [qc] = useState(() => new QueryClient({ defaultOptions: { queries: { refetchInterval: 4000, retry: 1, staleTime: 2000 } } }));
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}
```
`types.ts`: declare `Camera`, `Detection`, `Alert`, `WatchlistEntry`, `Zone`, `ZoneEvent`, `TrafficLive`, `Baseline`, `Anomaly`, `Journey`, `ClonedPlate`, `PipelineStatus`, `CameraHealth`, `Who` from the JSON the routes emit (`app.py` lines: cameras 79, detections 269, alerts 478, watchlist 428, zones 887, zones/events 955, traffic/live 980, baselines 1032, anomalies 1052, cloned-plates 1122, journeys 1157, pipeline/status 560 + `pipeline.py:587`). Use optional fields where a key may be null. Start the local backend and `curl` each endpoint to confirm names; do not guess.

`app/layout.tsx`: html lang `en`, `<body>` wraps `<QueryProvider><AuthProvider>{children}<Toaster/></AuthProvider></QueryProvider>`, metadata title "NETRA", description "Unified CCTV viewing and vehicle intelligence for Gujarat Police".

- [ ] **Step 6: Verify** `npm run build` succeeds with `output: export`; `npm run dev` shows the default page with dark bg and Jakarta font. Root `.gitignore` updated.

- [ ] **Step 7: Commit** `git add frontend .gitignore && git commit -m "Frontend scaffold: tokens, API client, auth context"` (confirm `frontend/node_modules` is not staged: `git status --short | head`).

---

### Task B2: Console shell, RBAC UI, live socket, unreachable state

**Files:**
- Create: `frontend/app/console/layout.tsx`, `frontend/components/Shell.tsx`, `frontend/components/RoleBadge.tsx`, `frontend/components/SignInDialog.tsx`, `frontend/components/Gate.tsx`, `frontend/components/Unreachable.tsx`, `frontend/components/Brand.tsx`, `frontend/components/TimeBadge.tsx`, `frontend/components/EmptyState.tsx`, `frontend/lib/ws.ts`, `frontend/lib/live.tsx`

**Interfaces:**
- Produces: `<Gate min="operator" reason="Sign in as operator to edit the watchlist">{children}</Gate>` (renders children disabled + tooltip when `!can(min)`); `useLive(): {alerts: AlertEvent[], status: PipelineStatus|null, connected: boolean}` fed by `/ws/alerts` and 4 s polling of `/api/pipeline/status`; `<TimeBadge det={...}/>`; `<Brand size="sm"|"lg"/>` renders `नेत्र` mark + NETRA wordmark; `<EmptyState icon title body action?/>`.
- Consumes: B1.

- [ ] **Step 1: Shell layout** (`components/Shell.tsx`, client)

Sidebar nav items with lucide icons: `LayoutDashboard` Overview `/console`, `Grid2x2` Video Wall `/console/wall`, `Map` GIS Map `/console/map`, `Car` Vehicles `/console/vehicles`, `Bell` Alerts `/console/alerts`, `ListChecks` Watchlist `/console/watchlist`, `Shapes` Zones & Intrusion `/console/zones`, `Activity` Traffic `/console/traffic`, `Network` Intelligence `/console/intelligence`, `MessageSquare` Assistant `/console/assistant`, `ShieldCheck` Admin `/console/admin`. Active item: `bg-surface-2 text-text border-l-2 border-accent`. Collapsible to icons on `<lg`; on `<md` sidebar becomes a shadcn `Sheet` opened by a menu button.

Top bar: `<Brand size="sm"/>`, pipeline pill (from `useLive().status`: green dot "Running · 8 cams · 13 ms" or grey "Stopped"), backend dot (green when last query ok, red when `ApiError 0`), time-basis legend chip ("scene = corroborated clock · T+ = stream time" tooltip), `<RoleBadge/>` + Sign in/out button.

`RoleBadge`: viewer grey, operator info, admin accent; label text + `Eye|Wrench|Shield` icon. `SignInDialog`: shadcn Dialog, one `Input type="password"` labelled "API key", helper text "Keys are held only in this browser", submit → `signIn`, toast "Signed in as operator"; 401 → inline error "Key not recognised".

`Gate`:
```tsx
export function Gate({ min, reason, children }: { min: Role; reason: string; children: React.ReactElement<{ disabled?: boolean }> }) {
  const { can } = useAuth();
  if (can(min)) return children;
  return (
    <Tooltip><TooltipTrigger asChild><span tabIndex={0} className="inline-flex cursor-not-allowed">{cloneElement(children, { disabled: true })}</span></TooltipTrigger>
    <TooltipContent>{reason}</TooltipContent></Tooltip>
  );
}
```

- [ ] **Step 2: `lib/ws.ts` + `lib/live.tsx`**

```ts
// ws.ts: reconnecting socket with backoff 1s..15s, ignores {"type":"ping"}
export function connect(path: string, onMsg: (m: any) => void, onState: (up: boolean) => void) {
  let ws: WebSocket | null = null, delay = 1000, closed = false;
  const open = () => {
    ws = new WebSocket(wsUrl(path));
    ws.onopen = () => { delay = 1000; onState(true); };
    ws.onmessage = (e) => { try { const m = JSON.parse(e.data); if (m.type !== "ping") onMsg(m); } catch {} };
    ws.onclose = () => { onState(false); if (!closed) setTimeout(open, delay = Math.min(delay * 2, 15000)); };
    ws.onerror = () => ws?.close();
  };
  open();
  return () => { closed = true; ws?.close(); };
}
```
`live.tsx`: `LiveProvider` keeps last 200 socket messages in state, `useQuery(["pipeline"], () => api<PipelineStatus>("/api/pipeline/status"))`, exposes `useLive()`. Messages with `kind === "attributes"` update a `descriptions` map; everything else is an alert event (see `pipeline.py:523-566` `_raise_alert` payload for keys: `plate`, `camera_id`, `score`, `severity`, `alert_id`, `detection_id`, `evidence`).

- [ ] **Step 3: Unreachable state**

`Unreachable.tsx`: full-panel card "Backend unreachable" showing `apiBase()`, an input to change it (calls `setApiBase`, then `location.reload()`), retry button, auto-retry every 5 s via `useQuery(["ping"], () => api("/api/pipeline/status"))`. `console/layout.tsx` renders `<Unreachable/>` instead of the page when the ping query errored with status 0 twice in a row; otherwise `<Shell>{children}</Shell>` inside `<LiveProvider>`.

- [ ] **Step 4: Verify** `npm run dev`; `/console` shows shell with skeleton page; stop backend → Unreachable panel appears within 10 s, start backend → recovers. With `data/api_keys.json` present: badge "Viewer", sign in with operator key → badge "Operator". Keyboard: Tab reaches every nav item and the sign-in button with visible focus ring. 375 px: sheet nav works.

- [ ] **Step 5: Commit** `git add frontend && git commit -m "Console shell with RBAC controls, live socket and unreachable state"`

---

### Task B3: Overview page

**Files:** `frontend/app/console/page.tsx`, `frontend/components/KpiStat.tsx`, `frontend/components/AlertFeed.tsx`, `frontend/components/HealthStrip.tsx`, `frontend/components/MiniMap.tsx` (dynamic import, `ssr:false`)

**Data:** `/api/detections/stats` (`app.py:396`), `/api/cameras/health` (A5; on 404 fall back to `pipeline/status.cameras`), `/api/alerts?limit=20` (478), `useLive()`.

- [ ] **Step 1: Build**
  - KPI row (5 `KpiStat`: Cameras online/total, Detections total, Plates read (with % rate), Active alerts (unacknowledged count from alerts list), Pipeline `infer_ms`). Numbers in `.mono`, 32 px, label 12 px muted, subtle trend text only when data exists.
  - Left 2/3: `AlertFeed` (live from socket, merged with fetched history by `alert_id`; new item animates in with 200 ms slide+fade; each row: severity chip with icon, plate mono, camera name, score, `TimeBadge`, evidence thumbnail from `apiUrl("/evidence/"+path)`, buttons "Acknowledge" inside `<Gate min="operator">` and "View").
  - Right 1/3: `HealthStrip` (30 small squares, colour by `state`, tooltip with fps/stale/codec; legend with text labels under it), then `MiniMap` (Leaflet, `CartoDB dark_matter` tiles `https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png` attribution `© OpenStreetMap contributors © CARTO`, markers coloured by state, click → `/console/map?camera=ID`).
  - Bottom: pipeline controls: "Start pipeline" / "Stop" (`POST /api/pipeline/start|stop`, admin per `require("pipeline")`) inside `<Gate min="admin">`, camera multi-select from `/api/cameras`, shows `scheduling.escalated` and `dark_cameras` as chips.
  - Empty states: no detections yet → EmptyState "No detections yet. Start the pipeline to begin analysing feeds." with the start button.
- [ ] **Step 2: Verify** against running pipeline: KPIs update every 4 s, a seeded watchlist hit appears in the feed live (use `POST /api/watchlist/seed` then start own-feed test: `POST /api/pipeline/start-own-feed` per `app.py:852`). Viewer sees Acknowledge disabled with tooltip.
- [ ] **Step 3: Commit** `git commit -am "Overview: KPIs, live alert feed, camera health, mini map, pipeline controls"`

---

### Task B4: Video wall

**Files:** `frontend/app/console/wall/page.tsx`, `frontend/components/CameraTile.tsx`, `frontend/components/CameraPicker.tsx`, `frontend/components/HlsPlayer.tsx`

**Data:** `/api/cameras` (79), `apiUrl("/api/cameras/{id}/live.mjpg")`, `POST /api/cameras/{id}/hls/start|stop`, `GET .../hls/status`, `apiUrl("/api/cameras/{id}/snapshot")`, `/api/cameras/health`.

- [ ] **Step 1: `CameraTile`**

```tsx
"use client";
type Mode = "live" | "smooth" | "still";
export function CameraTile({ cam, health, onRemove }: { cam: Camera; health?: CameraHealth; onRemove: () => void }) {
  const [mode, setMode] = useState<Mode>("live");
  const [hlsUrl, setHlsUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [bust, setBust] = useState(Date.now());
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (mode !== "smooth") { setHlsUrl(null); return; }
    let alive = true, poll: any;
    api<{ ready: boolean; url: string }>(`/api/cameras/${cam.id}/hls/start`, { method: "POST" })
      .then(async () => {
        for (let i = 0; i < 40 && alive; i++) {
          const st = await api<{ ready: boolean; url: string }>(`/api/cameras/${cam.id}/hls/status`);
          if (st.ready) { setHlsUrl(apiUrl(st.url)); break; }
          await new Promise(r => setTimeout(r, 500));
        }
        poll = setInterval(() => api(`/api/cameras/${cam.id}/hls/status`).catch(() => {}), 20000); // keeps the relay alive
      })
      .catch((e: ApiError) => { setErr(e.status === 429 ? "4 smooth streams max. Close one first." : e.message); setMode("live"); });
    return () => { alive = false; clearInterval(poll); api(`/api/cameras/${cam.id}/hls/stop`, { method: "POST" }).catch(() => {}); };
  }, [mode, cam.id]);
  const src = mode === "live" ? apiUrl(`/api/cameras/${cam.id}/live.mjpg?t=${bust}`) : apiUrl(`/api/cameras/${cam.id}/snapshot?t=${bust}`);
  return (
    <div ref={ref} className="relative bg-black rounded-[10px] overflow-hidden border border-border aspect-video group">
      {mode === "smooth" && hlsUrl ? <HlsPlayer src={hlsUrl} /> : mode === "smooth" ? <Skeleton className="w-full h-full" /> :
        <img src={src} alt={`Live view of ${cam.name}`} className="w-full h-full object-contain" onError={() => setTimeout(() => setBust(Date.now()), 2000)} />}
      <header className="absolute top-0 inset-x-0 flex items-center gap-2 px-2 py-1 bg-gradient-to-b from-black/70 to-transparent text-xs">
        <span className="mono">{cam.id}</span><span className="truncate">{cam.name}</span>
        {health && <span className={clsx("ml-auto inline-flex items-center gap-1", health.state === "online" ? "text-ok" : health.state === "degraded" ? "text-warn" : "text-bad")}><Circle className="size-2 fill-current" />{health.state}{health.fps ? ` · ${health.fps} fps` : ""}</span>}
        {cam.codec && <span className="mono opacity-70">{cam.codec}</span>}
      </header>
      <footer className="absolute bottom-0 inset-x-0 flex gap-1 p-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity bg-gradient-to-t from-black/70">
        <Button size="sm" variant={mode === "live" ? "default" : "secondary"} onClick={() => setMode("live")}><ScanEye className="size-4" />AI live</Button>
        <Button size="sm" variant={mode === "smooth" ? "default" : "secondary"} onClick={() => setMode("smooth")}><Film className="size-4" />Smooth</Button>
        <Button size="sm" variant="secondary" onClick={() => setMode("still")}><Camera className="size-4" />Still</Button>
        <Button size="sm" variant="ghost" className="ml-auto" aria-label="Fullscreen" onClick={() => ref.current?.requestFullscreen()}><Maximize2 className="size-4" /></Button>
        <Button size="sm" variant="ghost" aria-label="Remove tile" onClick={onRemove}><X className="size-4" /></Button>
      </footer>
      {err && <div role="alert" className="absolute inset-x-2 bottom-12 text-xs bg-bad/90 text-white rounded px-2 py-1">{err}</div>}
    </div>
  );
}
```
`HlsPlayer`: `<video autoPlay muted playsInline controls>`; if `Hls.isSupported()` attach `new Hls({ lowLatencyMode: true, liveSyncDurationCount: 3 })`, else set `video.src` (Safari). Destroy on unmount.

- [ ] **Step 2: Page**: layout selector 1/4/9/16 (`grid-cols-1|2|3|4`), tile list in `localStorage["NETRA_WALL"]`, `CameraPicker` (Sheet with search, group chips from `city`/time group, capability filter, "Add the 8 time-aligned Ahmedabad+Junagadh cams" preset: cam01–05,13,14,15 and cam08–11). Mobile defaults to 1×1. Each tile subscribes only while mounted (MJPEG closes on unmount because the `<img>` is removed; add `img.src = ""` in cleanup to force the connection to close).
- [ ] **Step 3: Verify**: 9 tiles animate with overlays; pipeline `infer_ms` not visibly affected; Smooth on 5th tile shows the 429 message; removing a tile closes its connection (server log shows unsubscribe / `netstat` count drops). 375 px: single column, controls reachable by touch (buttons ≥ 44 px tall: use `h-11` on mobile).
- [ ] **Step 4: Commit** `git commit -am "Video wall with MJPEG overlay tiles and on-demand smooth HLS"`

---

### Task B5: GIS map (Model 1)

**Files:** `frontend/app/console/map/page.tsx`, `frontend/components/MapView.tsx` (dynamic `ssr:false`), `frontend/components/CameraDrawer.tsx`, `frontend/components/OnboardDrawer.tsx`, `frontend/components/GapPanel.tsx`

**Data:** `/api/cameras` (79) incl. `lat`, `lon`, `city`, `department` (check field), `capability`, codec/resolution; `/api/cameras/gap-analysis` (115); `POST /api/cameras/onboard` (106, admin) and `POST /api/cameras/own-feed` (812, admin); `/api/cameras/health`; `/api/export/detections.csv`.

- [ ] **Step 1: Build**
  - Full-height Leaflet map, dark tiles, `L.circleMarker` per camera coloured by health state (fallback capability), radius 7, white 1 px stroke; cluster with plain zoom-dependent grouping (`ponytail: no clustering lib; 30–60 cams do not need one`).
  - Layer panel (top-left card): toggles by department/city, by capability, by state, coverage circles (150 m radius, accent 10 % fill), gap zones from gap-analysis (dashed `bad` polygons/circles as the endpoint provides; read its JSON).
  - `CameraDrawer` (right Sheet on click or `?camera=`): metadata table (mono values), snapshot `<img>` with refresh, health facts, buttons "Open in wall" (adds to `NETRA_WALL` and routes), "Trace here" → `/console/vehicles?camera=ID`.
  - `OnboardDrawer` (admin, `Gate`): tabs Manual (fields the onboard route accepts; read `app.py:106-114` and the `Camera` model), CSV bulk (client parses CSV, posts rows one by one with progress, shows per-row result), API (shows `curl` example with the current base URL). Own feed tab: file path + name → `/api/cameras/own-feed`.
  - `GapPanel`: table of gaps with "Show on map" focusing the map; "Export CSV" link to `apiUrl("/api/export/detections.csv")`.
  - Search box filters markers by id/name.
- [ ] **Step 2: Verify**: all 30 markers placed in Gujarat, drawer opens, onboarding a test camera as admin appears live on the map; viewer sees Onboard disabled with tooltip; keyboard: markers focusable (`keyboard: true` in Leaflet) with `title`.
- [ ] **Step 3: Commit** `git commit -am "GIS map: layers, camera drawer, onboarding and gap analysis"`

---

### Task B6: Vehicles search and trace

**Files:** `frontend/app/console/vehicles/page.tsx`, `frontend/components/DetectionTable.tsx`, `frontend/components/VehicleDetail.tsx`, `frontend/components/RouteMap.tsx`

**Data:** `/api/detections?plate=&camera=&vehicle_class=&colour=&since=&until=&limit=` (269; read the accepted query params), `/api/route?plate=` (416), `/api/vehicles/{id}/similar` (661), `/api/vehicles/{id}/track` (784), `POST /api/detections/{id}/describe` (349), `/api/analytics/journeys` (1157).

- [ ] **Step 1: Build**
  - Search bar: plate (mono input, uppercase, fuzzy hint), attributes text (colour/type/make; matched against `colour`, `vehicle_class` and VLM `description` via the params the route offers; if the route has no description filter, filter client-side over the returned page and say so in a helper line), camera select, time range, class chips.
  - "Trace registration number" primary action: calls `/api/route?plate=`; result panel shows ordered sightings with camera, `TimeBadge`, distance/elapsed between hops (only when both scene times corroborated; otherwise show "—" with tooltip "stream time only"), map polyline in `RouteMap`, and a plain-language verdict line: "n sightings on k cameras" or "No sightings of this number in the indexed period." Never invent a journey.
  - Results table: crop thumbnail (`/evidence/...`), plate mono with `plate_votes` badge, class, colour, camera, TimeBadge, confidence bar, description snippet. Row click → `VehicleDetail` sheet: big crop, all fields, "Similar vehicles" (ReID, show `ambiguous` flags as warn chip with reason), "Track" timeline, "Describe" button (operator) → posts and shows VLM text; live update via socket `kind: attributes`.
  - Export current filter as CSV link.
- [ ] **Step 2: Verify**: search `GJ01AB1234` after own-feed run returns rows and a route; a plate with no hits shows the honest empty verdict; similar-vehicles works for a detection with embedding; mobile layout stacks.
- [ ] **Step 3: Commit** `git commit -am "Vehicles: search, trace by registration, ReID and VLM detail"`

---

### Task B7: Alerts and Watchlist

**Files:** `frontend/app/console/alerts/page.tsx`, `frontend/app/console/watchlist/page.tsx`, `frontend/components/WatchlistForm.tsx`

**Data:** `/api/alerts` (478), `POST /api/alerts/{id}/acknowledge` (512, operator), `/api/watchlist` (428), `POST /api/watchlist` (441, operator; read body keys from the route), `DELETE /api/watchlist/{id}` (465), `POST /api/watchlist/seed` (636).

- [ ] **Step 1: Alerts page**: filter bar (severity, camera, acknowledged, plate), table with live prepend from socket, row expands to evidence image + snapshot + matched watchlist entry + score breakdown (whatever `score_match` returns; show fields present), actions Acknowledge (Gate operator), "Open camera", "Trace plate". Severity uses icon + text + colour. Sound toggle (small `Audio` beep on critical, off by default, stored in localStorage).
- [ ] **Step 2: Watchlist page**: table (plate mono, category chip, severity, description, source, FIR ref, created), "Add entry" dialog (`WatchlistForm` with visible labels, validation on blur: plate `^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$` with helper text "Indian format, e.g. GJ01AB1234; partial plates allowed with *"), delete with confirm dialog + undo toast (re-POST the entry within 5 s), "Seed demonstration set" button, CSV import (client parse → sequential POST with progress), "Hits" column linking to alerts filtered by plate.
- [ ] **Step 3: Verify**: viewer: add/delete disabled with tooltips; operator: add, delete, undo works; seed populates 10 rows; alert acknowledgement updates the Overview count.
- [ ] **Step 4: Commit** `git commit -am "Alerts and watchlist management with RBAC gating"`

---

### Task B8: Zones & Intrusion editor

**Files:** `frontend/app/console/zones/page.tsx`, `frontend/components/ZoneEditor.tsx`, `frontend/components/ZoneEventList.tsx`

**Data:** `/api/zones` (887), `POST /api/zones` (902, admin `require("onboard")`; body per route: camera_id, name, kind, points normalised 0–1, params), `DELETE /api/zones/{id}` (941), `/api/zones/events` (955), snapshot `/api/cameras/{id}/snapshot?refresh=true`.

- [ ] **Step 1: Editor**: camera select → snapshot on a `<canvas>` sized to container; click to add polygon points (rendered as accent dots + lines), double-click/Enter to close, Escape to cancel, Backspace removes last point; for `line` kinds two points; rule form beside canvas: name, kind select (labels: "Intrusion (restricted area)", "Loitering", "Wrong-way line", "Count line"; map to the backend `kind` values read from `netra/analytics/zones.py`), params (dwell seconds etc. per kind), Save (Gate admin). Existing zones drawn in muted colour with names; select to delete.
- [ ] **Step 2: Events**: list with crop, zone name, camera, kind, TimeBadge, live prepend from socket if zone events are broadcast (check `pipeline.py:170-226`; if not broadcast, poll 4 s).
- [ ] **Step 3: Verify**: draw an intrusion polygon on the own-feed camera, run own feed, events appear; viewer cannot save.
- [ ] **Step 4: Commit** `git commit -am "Zones and intrusion editor with live events"`

---

### Task B9: Traffic and Intelligence

**Files:** `frontend/app/console/traffic/page.tsx`, `frontend/app/console/intelligence/page.tsx`, `frontend/components/charts/CountsChart.tsx`, `frontend/components/JourneyCard.tsx`, `frontend/components/ClonePairCard.tsx`

**Data:** `/api/traffic/live` (980), `/api/traffic/history?camera=&hours=` (995), `/api/analytics/baselines` (1032), `/api/analytics/anomalies` (1052), `/api/analytics/journeys` (1157), `/api/analytics/cloned-plates` (1122).

- [ ] **Step 1: Traffic**: per-camera live count cards (vehicles per class, `loops_seen`, cumulative), Recharts `AreaChart` of history per selected camera (gridlines `#22304f`, area accent 25 %, tooltip, legend, `aria-label` summary), baseline band (mean ± z) overlay, anomaly table (camera, bucket, observed vs expected, z-score, `stale` shown as muted with tooltip "baseline too old to judge"). Time axis labelled "stream time buckets" unless scene time.
- [ ] **Step 2: Intelligence**: Journeys tab: cards with hop chain (camera → camera with elapsed), confidence, `truncated` chip, map preview, honest banner at top: "Cross-camera journeys require corroborated clocks on both cameras; in the sandbox only cam13 corroborates, so this list is empty unless your own feeds provide overlays." rendered when the list is empty. Cloned plates tab: pair cards with both crops, cameras, times, distance, implied speed, "impossible" reason. Anomalies tab reuses the traffic anomaly table.
- [ ] **Step 3: Verify** with the indexed DB: traffic history renders for cam13; cloned-plate pairs from own-feed test render; empty journeys shows the banner.
- [ ] **Step 4: Commit** `git commit -am "Traffic baselines and intelligence views"`

---

### Task B10: Assistant, Admin, Report

**Files:** `frontend/app/console/assistant/page.tsx`, `frontend/app/console/admin/page.tsx`, `frontend/app/report/page.tsx`

**Data:** `POST /api/assistant` (870; body `{question}`; read response keys: answer, intent, sources/facts), `/api/audit` (595), `/api/storage` (1094), `POST /api/storage/prune?dry_run=` (1101, admin), `/api/notify/config`, `POST /api/notify/test` (operator), `/api/cameras/health`, `/api/auth/whoami`, `/api/report?hours=&plate=&cameras=`.

- [ ] **Step 1: Assistant**: chat column (user right / NETRA left with Brand avatar), quick prompt chips ("Where was GJ01AB1234 last seen?", "How many trucks on cam13 in the last hour?", "Any cloned plates today?", "Which cameras are offline?"), each answer shows an "intent" chip and a collapsible "Sources" list (entity matches, SQL facts, ReID ids with links into Vehicles/Alerts). Input with Enter to send, Shift+Enter newline, loading dots ≤ 300 ms before skeleton.
- [ ] **Step 2: Admin**: cards: Access (whoami, role matrix table viewer/operator/admin × read/acknowledge/watchlist/onboard/pipeline/manage from `auth.PERMISSIONS`, current key fingerprint not shown, sign-out), API base (current URL, change + reload), Health table (all columns from A5, sortable by state), Audit log (admin; table with `at`, actor, action, target, detail; filter box), Storage & retention (usage from `/api/storage`, "Dry-run prune" and "Prune" with confirm, admin), Notifications (masked config, "Send test" operator, result inline), Links (legacy console `apiUrl("/legacy/")`, OpenAPI `apiUrl("/docs")`, repo).
- [ ] **Step 3: Report**: form hours/plate/cameras → button opens `apiUrl("/api/report?...")` in a new tab (the backend page has Save as PDF); below it, an inline `<iframe>` preview of the same URL.
- [ ] **Step 4: Verify**: assistant answers a plate question with sources; audit shows the notify test; report iframe renders.
- [ ] **Step 5: Commit** `git commit -am "Assistant, admin and report pages"`

---

### Task B11: Landing page

**Files:** `frontend/app/page.tsx`, `frontend/components/landing/*.tsx` (Hero, LiveStrip, Problem, LivePreview, Capabilities, HowItWorks, Measured, Scale, Security, Cta, Footer), `frontend/public/netra-mark.svg`, `frontend/public/og.png`

**Data (all optional, skeleton on failure):** `/api/detections/stats`, `/api/cameras/health`, `/api/pipeline/status`, one MJPEG tile for the preview (camera chosen: first `online` from health, else `cam13`).

- [ ] **Step 1: Mark**: `netra-mark.svg`: a 48×48 rounded square, navy fill, `नेत्र` in Plus Jakarta-compatible Devanagari (use `Noto Sans Devanagari` via Google Fonts import for the mark only) in saffron, or a stylised eye outline with the word beneath at large size. `Brand` component uses it.
- [ ] **Step 2: Sections** exactly as spec §4.5, with these copy anchors:
  - H1: "See every camera. Trace every vehicle." Sub: "NETRA unifies departmental CCTV into one console with ANPR, watchlist alerts and cross-camera vehicle intelligence. Built on Model 1 (Registry & GIS) + Model 2 (Unified Viewing & Metadata Analytics)."
  - Live strip: 4 mono counters from real endpoints with a pulsing `ok` dot and caption "Live from the NETRA backend"; if unreachable: "Backend offline — numbers appear when the pipeline is up."
  - Problem: "26 departments · ~80,000 cameras · no common view".
  - Live preview: MJPEG tile + `MiniMap`, caption "This is live, not a mockup."
  - Capabilities: 8 cards (lucide icon, title, one line): Unified viewing; Registry & GIS; ANPR with multi-frame voting; Watchlist alerts in real time; Cross-camera re-identification & journeys; Cloned-plate detection; Zones, intrusion & traffic baselines; Vision-language attributes & assistant.
  - How it works: 5 nodes with arrows (CSS grid; vertical on mobile), callout "159× less bandwidth than streaming video to a centre".
  - Measured: 4 stats "0 % dropped frames on 8 cameras", "~13 ms per frame (FP16)", "0.95 match score on own-feed plates", "15/30 grid cameras anchor a clock", and one honest sentence about grid plate legibility.
  - Scale: three columns Edge / Regional / Central with one-line roles, link "Read the HLD".
  - Security: RBAC roles, audit trail, no central video storage, keys never in the bundle.
  - CTA: "Open Console" (accent) + "Watch the demo" (secondary, links to the unlisted video once available; until then to `/console/wall`).
  - Footer: "Built for the Gujarat Police Innovation Challenge 2026 · Sentinel · Vatsa Joshi", repo link, HLD link, contact.
  - Sticky top nav with Brand + "Open Console".
- [ ] **Step 3: Quality**: `prefers-reduced-motion` respected (the only motion: hero fade-up 250 ms, counter tick, dot pulse), Lighthouse a11y ≥ 95 and performance ≥ 85 on desktop, images `loading="lazy"` below fold, headings h1→h2→h3 in order, all icons `aria-hidden` with text labels.
- [ ] **Step 4: Verify** at 375/768/1024/1440; with backend down the page still looks complete.
- [ ] **Step 5: Commit** `git commit -am "Landing page"`

---

### Task B12: Playwright smoke, build gate, Vercel readiness

**Files:** `frontend/playwright.config.ts`, `frontend/tests/console.spec.ts`, `frontend/package.json` scripts (`test:e2e`, `build`), `frontend/README.md`

- [ ] **Step 1: Tests** (run against `npm run dev` on :3000 and backend on :8080 with the pipeline started on cam13,cam14 and `data/api_keys.json` present; operator key read from that file via `fs` in the test):

```ts
import { test, expect } from "@playwright/test";
import fs from "node:fs";
const keys = JSON.parse(fs.readFileSync("../data/api_keys.json", "utf8")) as Record<string, { role: string }>;
const operator = Object.entries(keys).find(([, v]) => v.role === "operator")![0];
const routes = ["", "wall", "map", "vehicles", "alerts", "watchlist", "zones", "traffic", "intelligence", "assistant", "admin"];
for (const r of routes) test(`console/${r} renders`, async ({ page }) => {
  await page.goto(`/console/${r}`); await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});
test("landing has live strip and CTA", async ({ page }) => {
  await page.goto("/"); await expect(page.getByRole("link", { name: /open console/i })).toBeVisible();
});
test("wall shows an MJPEG tile", async ({ page }) => {
  await page.goto("/console/wall/"); await page.getByRole("button", { name: /add camera/i }).click();
  await page.getByText("cam13").click();
  await expect(page.locator("img[src*='live.mjpg']").first()).toBeVisible();
});
test("viewer is gated, operator is not", async ({ page }) => {
  await page.goto("/console/watchlist/");
  await expect(page.getByRole("button", { name: /add entry/i })).toBeDisabled();
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.getByLabel("API key").fill(operator); await page.getByRole("button", { name: /^sign in$/i }).click();
  await expect(page.getByText(/operator/i).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /add entry/i })).toBeEnabled();
});
```
- [ ] **Step 2: `npm run build` clean (zero type errors, zero lint errors), `npx playwright test` green. `frontend/README.md`: dev, build, env, `?api=` override, Vercel steps.
- [ ] **Step 3: Commit** `git commit -am "Frontend smoke tests and build gate"`

---

# Track C — Submission pack (starts after B3 + B4 + B5 exist for screenshots)

### Task C1: HLD §16 and deployment appendix

**Files:** Modify `docs/high-level-design.md` (insert `## 16. Hosted console, tunnel and access control` before `## Appendices`; update §15 Deployment with a pointer to `docs/deploy.md`).

- [ ] **Step 1: Write §16** (400–700 words + one ASCII sequence diagram): architecture (Vercel static console → Cloudflare tunnel → FastAPI on GPU host), why the split (GPU stays where the video is; console is a CDN artefact), streaming modes table (MJPEG overlay ≤6 fps ~0.3 Mbit/s per tile vs HLS relay 25 fps 1–3 Mbit/s, cap 4, idle reap 60 s), RBAC UX (anonymous viewer, key sign-in, three roles, audit), CORS policy, what is not sent to the browser (no RTSP credentials, no keys in bundle), production path (named tunnel + domain or on-prem reverse proxy; identical code). Sequence: judge opens console → whoami → tiles subscribe → alert over WS → acknowledge with key → audit row.
- [ ] **Step 2: Verify** headings numbered consistently; run `python - <<'EOF'` to assert the file contains "## 16." and no "TBD".
- [ ] **Step 3: Commit** `git commit -am "HLD: hosted console, tunnel and access control"`

### Task C2: Presentation deck

**Files:** Create `tools/build_deck.py`, `requirements-docs.txt` (`python-pptx>=1.0`), `docs/submission/presentation.pptx`, `docs/submission/screenshots/*.png`

- [ ] **Step 1: Screenshots** with Playwright (`npx playwright screenshot --viewport-size=1440,900 --full-page`) of: landing hero, console overview, video wall 3×3, GIS map with drawer, vehicles trace result, alerts with a critical hit, admin role matrix. Save under `docs/submission/screenshots/`.
- [ ] **Step 2: `tools/build_deck.py`**: python-pptx, 16:9, dark theme matching tokens (bg `#070d1a`, text `#e6ecf7`, accent `#FF6B00`), Plus Jakarta Sans if installed else Calibri, builds the 16 slides from `docs/presentation-outline.md` headings and bullets (parse `## Slide N — Title` blocks; bullets are `- ` lines; a line starting `img:` inserts the named screenshot right-aligned). Add to the outline: slide 6 `img: console-overview.png`, slide 10 `img: vehicles-trace.png`, slide 11 `img: alerts-critical.png`, slide 12 `img: wall.png`, slide 14 `img: admin-roles.png`, and a new slide after 14: "Hosted demo & access" with the Vercel URL, tunnel note, operator-key sentence, `img: landing-hero.png`. Speaker notes = the outline's "Say:" lines if present.
- [ ] **Step 3: Run** `pip install -r requirements-docs.txt && python tools/build_deck.py` → `docs/submission/presentation.pptx`; open it, check every slide fits (no overflow: cap bullets at 6 per slide, font 20 pt body / 36 pt title). Also export PDF via PowerPoint (manual, Vatsa) or `soffice --headless --convert-to pdf` if LibreOffice exists.
- [ ] **Step 4: Commit** `git add tools/build_deck.py requirements-docs.txt docs/submission docs/presentation-outline.md && git commit -m "Presentation deck generator and screenshots"`

### Task C3: Demo scripts, report procedure, form answers

**Files:** Create `docs/submission/demo-own-feed-script.md`, `docs/submission/demo-gov-feed-script.md`, `docs/submission/form-answers.md`, `docs/submission/README.md`

- [ ] **Step 1: Own-feed script (2:30 target)** as a shot table `time | screen | action | say`. Beats: 0:00 landing (5 s) → console overview with pipeline running (10 s) → onboard `data/own_feed_test.mp4` via map Onboard drawer as admin (20 s) → wall tile with boxes + plate labels (15 s) → watchlist: show `GJ01AB1234` entry (10 s) → alert fires live in feed, open evidence (20 s) → vehicles: trace the plate, route + timeline (20 s) → zones: intrusion polygon event (15 s) → admin: sign out, show viewer cannot delete; sign in, notify test → email arrives on phone (20 s) → report page: Save as PDF (10 s) → close on landing measured strip (5 s). Include OBS settings (1080p30, mic on, cursor highlight), a pre-flight checklist (pipeline started 2 min before, watchlist seeded, api keys present, email configured, tunnel URL set), and the "what not to say" list (no claims about grid plates).
- [ ] **Step 2: Government-feed script (2:30)**: landing → map with 30 grid cameras, health strip → wall 3×3 on the 8 time-aligned cameras with overlays, Smooth toggle on cam13 → vehicles filtered to cam13 showing detections with scene times (corroborated badge) and stream times elsewhere, honest 1-line about plate legibility → traffic baselines for cam13 → intelligence: cloned plates / empty journeys banner explained → report generated for last 24 h, Save as PDF, show the PDF. Report procedure: exact URL, filename `netra-government-feed-report-YYYYMMDD.pdf`, what columns the committee should look at.
- [ ] **Step 3: `form-answers.md`**: every field of the Google Form (open the form, copy field labels verbatim): team/participant details (from `docs/registration-form-answers.md`), model chosen (Model 2 on Model 1), solution summary (≤150 words), hosted URL `https://<vercel>.vercel.app/console?api=https://<tunnel>.trycloudflare.com` with note that the `?api=` parameter is only needed once, test credentials line pointing to the operator key (value left as `<<paste from data/submission-credentials.md>>`), YouTube unlisted links placeholders, Drive folder placeholder (contents list: PPT, PDF, HLD PDF, two videos, output report PDF), repo link, contact.
- [ ] **Step 4: `docs/submission/README.md`**: order of operations for the final day (build deck → record videos → export PDFs → upload → fill form → keep laptop + tunnel running through 15 Sept evening and again for 22–23 Sept).
- [ ] **Step 5: Commit** `git add docs/submission && git commit -m "Demo scripts, report procedure and submission form answers"`

---

# Final — Integration and hand-over

### Task F1: End-to-end through the tunnel

- [ ] **Step 1:** `python tools/make_keys.py` (keep the file this time), `python run.py --port 8080`, start pipeline on cam01–05,13,14,15,08–11 + own feed, `cloudflared tunnel --url http://localhost:8080`, note URL.
- [ ] **Step 2:** Set `NETRA_CORS_ORIGINS` to the Vercel origin (and `NETRA_FRONTEND_URL`), restart backend. Deploy `frontend/` to Vercel (Vatsa's account; CLI `npx vercel --prod` from `frontend/` or Git import). Open `https://<app>.vercel.app/console/?api=https://<tunnel>`.
- [ ] **Step 3:** From a phone on mobile data: landing loads < 3 s, live strip has numbers, wall tile animates, alert appears after a seeded own-feed hit, sign-in as operator works, HLS smooth plays on cam13, report opens.
- [ ] **Step 4:** `python tools/smoke_api.py --base https://<tunnel>` all pass. Record results (latency of MJPEG first frame, HLS start time) into `docs/deploy.md` "Measured through tunnel".
- [ ] **Step 5:** Commit docs; run `superpowers:finishing-a-development-branch`.

---

## Self-review notes (done while writing)

- Spec §3.1–3.9 → A1–A7. §3.8 PDF: satisfied via print view (spec already chose HTML→Save as PDF). §3.9 root redirect + `/legacy` → A1.
- Spec §4.1–4.6 → B1–B12; §4.5 landing → B11; §4.6 auth UX → B2 + B12 test.
- Spec §5 → C1–C3 (+ `docs/deploy.md` in A7). §5 runtime `?api=` override → B1 `apiBase()`.
- Spec §6 testing → A7 smoke, B12 Playwright, F1 e2e.
- Type consistency: `Role`, `useAuth().can`, `Gate min`, `apiUrl`, `wsUrl`, `TimeBadge`, `CameraHealth.state` values (`online|degraded|offline|not-started`) used identically in A5, B2, B3, B4, B5, B10.
- Port everywhere: 8080.
