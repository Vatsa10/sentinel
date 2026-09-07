# NETRA — Deployment Guide

This document covers taking NETRA from a development checkout to a demoable,
internet-reachable deployment for the submission and the live demo. It targets
a single Windows host with an NVIDIA GPU running the backend, with the
frontend hosted separately on Vercel and reached through a tunnel.

## Prerequisites

- **Python** 3.11+ in a virtual environment (`.venv`). Install dependencies
  with `pip install -r requirements.txt` from the repository root.
- **ffmpeg** on `PATH`. Required for evidence snapshots and HLS packaging.
  Verify with `ffmpeg -version`; install via `winget install Gyan.FFmpeg` or
  by downloading a static build and adding its `bin/` directory to `PATH`.
- **cloudflared** for exposing the backend to the internet without opening a
  router port. Install with:

  ```
  winget install Cloudflare.cloudflared
  ```

- **Node.js 22** (LTS) for building and running the `frontend/` Next.js app
  locally, and for the Vercel CLI if used. Verify with `node --version`.
- An NVIDIA GPU with a working CUDA driver is strongly recommended — run
  `python run.py --check` to confirm `torch` reports `cuda=True` before
  relying on real-time inference.

## Environment variables

All variables are read by `netra/config.py`; anything left unset falls back
to a documented default. Set them in the shell before `python run.py`, or via
a `.env` file (not committed) if your process manager loads one.

| Variable | Purpose | Default |
|---|---|---|
| `NETRA_GRID_EMAIL` / `NETRA_GRID_PASSWORD` | Credentials for the Government CCTV grid portal. Alternatively place them in `data/credentials.json` (gitignored) as `{"email": "...", "password": "..."}`. | none — required |
| `NETRA_CORS_ORIGINS` | Comma-separated list of origins allowed to call the API from a browser (the deployed frontend's URL, plus `http://localhost:3000` for local dev). | `http://localhost:3000` |
| `NETRA_FRONTEND_URL` | The frontend's public URL. When set, `/` on the backend redirects there instead of serving the bundled `/legacy/` console. | unset (serves `/legacy/`) |
| `NETRA_SMTP_HOST` / `NETRA_SMTP_PORT` / `NETRA_SMTP_USER` / `NETRA_SMTP_PASSWORD` | SMTP relay used to email alert notifications. For Gmail, see **Gmail app passwords** below. | unset (email notify disabled) |
| `NETRA_EMAIL_TO` | Comma-separated recipient list for alert emails. | unset |
| `NETRA_WEBHOOK_URL` | Optional webhook (e.g. a Slack incoming webhook) that receives a POST for each qualifying alert. | unset |
| `NETRA_HLS_MAX_CONCURRENT` | Ceiling on concurrent ffmpeg HLS transcodes, to protect the inference GPU/CPU budget. | `4` |

### Gmail app passwords

Gmail requires an app password rather than the account password for SMTP:

1. Enable 2-Step Verification on the sending Google account
   (myaccount.google.com/security).
2. Visit myaccount.google.com/apppasswords, create a new app password named
   e.g. "NETRA alerts", and copy the 16-character password shown once.
3. Set `NETRA_SMTP_HOST=smtp.gmail.com`, `NETRA_SMTP_PORT=587`,
   `NETRA_SMTP_USER=<the gmail address>`, `NETRA_SMTP_PASSWORD=<the app
   password, no spaces>`.
4. Verify with `POST /api/notify/test` (operator role or above) once the
   server is running — it sends a test alert through the configured channels.

Never commit the app password. It lives only in the shell environment or an
untracked `.env`.

## Start sequence

Run these once per demo session, in order, from the repository root with the
virtual environment activated:

1. **Generate API keys** (once, or whenever keys need rotating):

   ```
   python tools/make_keys.py
   ```

   This writes `data/api_keys.json` (read by the server) and
   `data/submission-credentials.md` (the hand-out for judges/operators).
   Both are gitignored — never commit them.

2. **Start the backend** on the port the frontend expects:

   ```
   python run.py --port 8080
   ```

3. **Open a tunnel** to that port in a second terminal:

   ```
   cloudflared tunnel --url http://localhost:8080
   ```

   cloudflared prints a `https://<random-words>.trycloudflare.com` URL. This
   is the backend's public address for the duration of the tunnel process —
   it changes every time you restart cloudflared unless you use a named
   tunnel (see **Production path** below).

4. **Point the deployed frontend at that URL** by opening it once with the
   `api` override query parameter:

   ```
   https://<vercel-app>.vercel.app/console?api=https://<random-words>.trycloudflare.com
   ```

   The frontend persists this override (e.g. to `localStorage`) so
   subsequent navigation within the app keeps using it without the query
   parameter.

## Vercel setup

1. Import the repository into Vercel (vercel.com/new).
2. Set the project **root directory** to `frontend`.
3. Framework preset: **Next.js** (auto-detected).
4. Environment variable `NEXT_PUBLIC_API_BASE` is **optional** — the
   `?api=` query override described above takes precedence at runtime, so a
   fresh cloudflared URL each demo does not require a redeploy. Set
   `NEXT_PUBLIC_API_BASE` only if you want a stable default for local
   development or a longer-lived deployment behind a named tunnel.
5. Deploy. Vercel builds and serves the frontend on its own domain; the
   backend stays on the Windows/GPU host and is reached only through the
   tunnel URL.

## ngrok fallback

If cloudflared is unavailable, `ngrok` works as a fallback:

```
ngrok http 8080
```

Caveat: the free ngrok tier serves an interstitial "you are about to visit"
warning page on first access from a given browser/IP, and that interstitial
breaks any request that isn't a top-level navigation — including the `<img>`
tags this app uses for MJPEG live tiles, which fail silently instead of
streaming. Two ways around it if ngrok must be used:

- Send the `ngrok-skip-browser-warning` header on every request (the
  frontend's `lib/api.ts` can add it), which suppresses the interstitial for
  requests carrying it.
- Or simply open the ngrok URL once in a normal browser tab first, dismiss
  the interstitial, and rely on it not reappearing for that browser session.

Because of this rough edge, prefer cloudflared for anything demoed live.

## Pre-demo checklist

Run through this in order, ideally 15–20 minutes before the demo starts:

1. `python run.py --check` shows `cuda=True` and a named GPU.
2. Pipeline started and running against the 8 Ahmedabad/Junagadh grid cameras
   plus your own test feed (`POST /api/pipeline/start` or the console button).
3. `GET /api/pipeline/status` shows all expected cameras active, no
   unexpected `stopped`/`error` states.
4. `data/api_keys.json` exists and `python run.py --check` reports
   `api keys enforced`.
5. `data/submission-credentials.md` is present and matches the keys just
   generated — this is the hand-out for judges.
6. `cloudflared tunnel --url http://localhost:8080` is running and its URL
   is copied into the frontend via `?api=...` once.
7. `GET /api/auth/whoami` through the tunnel URL, unauthenticated, returns a
   viewer-level response (confirms the tunnel and CORS both work).
8. From a phone on mobile data (not the venue Wi-Fi), open one camera's
   `live.mjpg` tile through the tunnel URL and confirm it streams.
9. The alerts page's WebSocket shows connected (green) in the console, not
   reconnecting.
10. `GET /api/notify/config` shows the intended channel(s) configured, and
    `/api/notify/test` was fired at least once to confirm delivery.
11. OBS (or equivalent) is set up and tested for screen recording/casting,
    with the console and one live tile visible in frame.
12. A second charged phone/laptop is on hand as a spare demo device, in case
    the venue Wi-Fi drops the primary one mid-demo.

## Production path

For anything longer-lived than a single demo session, replace the ad-hoc
`cloudflared tunnel --url` with a **named Cloudflare Tunnel** bound to a real
domain (`cloudflared tunnel create netra`, then a DNS CNAME and a persistent
`cloudflared tunnel run` service) so the backend URL is stable across
restarts and the frontend's `NEXT_PUBLIC_API_BASE` can be set once and left
alone. For sustained GPU load beyond a laptop's thermal/power budget, move
the backend onto a dedicated workstation (e.g. an RTX A5000 box) on a wired
connection, run it as a Windows service or under a process supervisor so it
survives reboots, and keep the named tunnel pointed at that host instead of
a development machine.
