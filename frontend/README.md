# NETRA frontend

Next.js 15 (App Router, Turbopack), statically exported (`output: "export"`), for the
NETRA console UI and public landing page.

## Development

```bash
npm install
npm run dev
```

Serves at http://localhost:3000. The dev server proxies nothing itself — the app talks
directly to the FastAPI backend (default `http://localhost:8080`) from the browser, so
CORS on the backend must allow the frontend's origin.

## Backend URL override

The API base URL resolves in this order:

1. A `?api=<url>` query parameter on any page — e.g.
   `http://localhost:3000/console/?api=http://localhost:9000`. The value is written to
   `localStorage` and then used on every subsequent request/page until cleared.
2. A previously stored `localStorage` value (set by #1, or by `setApiBase()`).
3. The `NEXT_PUBLIC_API_BASE` build-time environment variable.
4. `http://localhost:8080` as the final fallback.

Sign-in keys are stored client-side only (also `localStorage`) and are never baked into
the build.

## Build

```bash
npm run build
```

Runs `next build --turbopack`, which also type-checks and lints the project, then
statically exports the site to `out/` (`trailingSlash: true`, `images.unoptimized: true`
per `next.config.ts`). A clean build has zero type errors and zero lint errors.

To smoke-test the exported output locally: `npx serve out` (or any static file server).

## Tests

End-to-end smoke tests (Playwright, Chromium only) live in `tests/console.spec.ts`.
They assume:

- The backend is running on `http://localhost:8080` with the pipeline started for at
  least `cam13`, `cam14`, `cam01` (so the video wall and vehicle trace have data), and
  the watchlist seeded (`POST /api/watchlist/seed`).
- `data/api_keys.json` exists at the repo root (`python tools/make_keys.py`) so the
  backend is running in enforced auth mode — the operator-role sign-in test reads its
  key from that file directly.
- The frontend dev server is reachable at `http://localhost:3000` (Playwright's
  `webServer` config reuses an already-running `npm run dev` instead of starting a
  second one).

Run them with:

```bash
npm run test:e2e
```

Coverage: every `/console/*` route renders an `<h1>`; the landing page's "Open Console"
CTA is visible; the video wall shows a `live.mjpg` tile; the watchlist's "Add entry"
button is disabled for an anonymous viewer and becomes enabled after signing in with an
operator key (and the role badge updates); the GIS map renders at least 20 camera
markers; and a vehicle registration trace shows either sighting/camera counts or the
honest "no sightings" text (never a silent blank state).

## Deploying to Vercel

1. Import the repository into Vercel.
2. Set the project's **Root Directory** to `frontend`.
3. Framework preset: **Next.js** (auto-detected).
4. Output directory: `out` (already set in `vercel.json`; Vercel serves the static
   export directly rather than running a Next.js server function).
5. Optionally set `NEXT_PUBLIC_API_BASE` as an environment variable to point the
   deployed build at a specific backend by default — end users can still override it
   per-session with `?api=`.
6. Deploy. No server-side runtime is required; the entire app ships as static HTML/JS.
