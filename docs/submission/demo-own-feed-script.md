# NETRA — Own-Feed Demo Script (target 2:30)

This is the video recorded against `data/own_feed_test.mp4` onboarded live as
a fourth stream alongside the Government grid cameras. Its purpose is to show
the full pipeline — onboarding, detection, watchlist matching, alerting,
tracing, zones and RBAC — on footage where NETRA controls the shot, so the
committee sees every capability working end to end. It is deliberately not
used to make claims about the Government grid; that is the other script's job.

## Shot table

| Time | Screen | Action | Say |
|---|---|---|---|
| 0:00–0:05 | Landing (`/`) | Load the landing page, measured strip visible | "NETRA — a unified viewing and analytics console for Gujarat's CCTV network." |
| 0:05–0:15 | Console overview (`/console`) | Show KPI row and camera health strip with the pipeline already running | "This is the operator console. The pipeline has been running for two minutes — cameras online, detections counting up, no manual step in between." |
| 0:15–0:35 | GIS map (`/console/map`), signed in as admin | Open the Onboard drawer, fill in the manual form for `data/own_feed_test.mp4` as a file source, submit | "I'm signed in as admin. Onboarding a new camera is a form or a CSV import — here I'm adding our own test feed as a fourth source, no code change." |
| 0:35–0:50 | Video Wall (`/console/wall`) | Select the new tile; overlay shows detection boxes and plate labels burned in | "Detection starts immediately — vehicle boxes, and here plate text, because this is our own footage shot for legibility." |
| 0:50–1:00 | Watchlist (`/console/watchlist`) | Show the seeded entry for `GJ01AB1234` | "This plate is seeded on the watchlist as a stolen-vehicle entry before the demo starts." |
| 1:00–1:20 | Alerts (`/console/alerts`), live | The seeded vehicle passes the camera; an alert fires on the live feed; open it | "The moment that plate is read against the watchlist, an alert raises here — camera, timestamp, evidence crop, map pin — no manual lookup." |
| 1:20–1:40 | Vehicles (`/console/vehicles`) | Search `GJ01AB1234`; open the detail view; show route and sightings timeline | "From the plate, an operator gets the sighting timeline and the route reconstructed from camera geometry — this is the cross-camera trace." |
| 1:40–1:55 | Zones & Intrusion (`/console/zones`) | Trigger the pre-drawn intrusion polygon on the test feed; event appears with crop | "Zones are drawn once as a polygon on a snapshot. Crossing it raises an intrusion event the same way a watchlist hit does." |
| 1:55–2:15 | Admin (`/console/admin`) | Sign out, reload as anonymous/viewer, attempt to delete a watchlist entry (blocked); sign back in as admin; fire `/api/notify/test`; cut to phone showing the email | "Role checks are enforced, not cosmetic — a viewer cannot touch the watchlist. Signing back in as admin, I fire a test notification, and it lands in email within seconds." |
| 2:15–2:25 | Report (`/report`) | Set a short time range, generate, click Save as PDF | "Every session can be closed out with a report — generated on demand, saved as a PDF." |
| 2:25–2:30 | Landing (`/`) | Return to the landing page, measured strip in frame | "That's the full loop, on our own feed, in under three minutes." |

## OBS settings

- Canvas / output: 1920×1080, 30 fps.
- Microphone: on, levels checked before recording (aim for -12 dB peak, no clipping).
- Cursor: enable click/cursor highlight so viewers can see where the pointer lands during form fills and drawer opens.
- Scene: single browser-window capture, full-screen console tab (no browser chrome, no bookmarks bar).
- Record locally first; upload only after reviewing the take for dropped audio or a stalled tile.

## Pre-flight checklist

1. Pipeline started at least 2 minutes before recording begins (`POST /api/pipeline/start` or the console button), so the overview's KPI row already shows non-zero counts at 0:05.
2. Watchlist seeded with `GJ01AB1234` (and any other demo entries) before recording starts — do not seed on camera.
3. `data/api_keys.json` present; admin and viewer keys ready to hand for the sign-in/sign-out beat.
4. Email notification configured (`NETRA_SMTP_*`, `NETRA_EMAIL_TO`) and confirmed working via a prior `/api/notify/test` call, so the on-camera test is a repeat of something already known to work.
5. Tunnel URL set on the console via `?api=https://<tunnel>.trycloudflare.com` before recording, so no query-parameter fumbling is visible on screen.
6. `data/own_feed_test.mp4` present and playable; confirm it loops or is long enough to cover the onboarding-to-alert window without needing a manual restart mid-take.
7. Second phone charged and on hand for the email-arrival cutaway, with the mail app already signed in and pull-to-refresh rehearsed.

## What not to say

- Do not say or imply that plates are read on the Government grid cameras — this script's plate-legible footage is the own-feed test video, not any of the 30 grid cameras. Keep the two claims separated on camera as they are separated in the HLD.
- Do not claim facial recognition — NETRA performs vehicle detection, ANPR and vehicle ReID only; there is no face-matching capability to show or narrate.
- Do not describe the cross-camera route on the own-feed test as evidence of what the Government grid can do; it demonstrates the trace *feature*, which depends on plate or appearance continuity being present in the source footage, not the grid's condition.
- Do not narrate the watchlist match as "real-time" without qualification if there is any recording delay; say "as it happens" and let the on-screen timestamp carry the precision claim.
- Do not skip the RBAC-denial beat even if pressed for time — it is the only shot proving role checks are enforced rather than cosmetic, and the brief treats it as load-bearing.
