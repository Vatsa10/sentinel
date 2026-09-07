# NETRA — Submission Form Answers

Verify field names against the live form before pasting — the labels below
are reconstructed from the portal's described submission form fields, not
copied from an opened form (the form could not be opened while writing this
document). Confirm the wording matches exactly and adjust before submitting.

Deadline: submission 15 September 2026; live event 22–23 September 2026.

## Team / participant details

- **Team/Participant name:** Vatsa Joshi
- **Email:** vatsajoshi2@gmail.com
- **Phone:** [fill in — not present in repository sources]
- **Institution:** [fill in — not present in repository sources]
- **Participation type:** Solo (individual/student entry)

## Chosen model

- **Solution model:** Model 2 — Unified Viewing and Selective Analytics, built on Model 1 (Centralised Registry and GIS Mapping) as its mandatory foundation. See `docs/registration-form-answers.md` for the full Model 1 confirmation and Field 1–3 text already prepared for the registration stage.

## Solution title

NETRA — Networked Evidence, Tracking & Recognition for Analytics

## Solution summary (≤150 words)

NETRA is a unified viewing and analytics platform for Gujarat's CCTV network,
built on a centralised camera registry (Model 1) and a pluggable
analytics layer (Model 2). Before designing anything, we profiled all 30
Government grid cameras: 7 cannot support analytics in their present
condition, and across 2,691 frames on the three best-positioned cameras,
zero number plates were legible — a measured property of the installed
infrastructure, not the recognition model. NETRA is built for that reality:
vehicle detection and appearance-based re-identification carry the platform
where plate text cannot, watchlist matching raises evidence-backed alerts in
real time, and every timestamp is labelled honestly — corroborated scene
time where verified, stream time everywhere else. A single console gives
operators live video, GIS coverage, alerts, vehicle tracing, zone
intrusion detection and audited role-based access, replacing today's
disconnected, per-department viewers. (139 words)

## Hosted platform URL

`https://<<VERCEL_URL>>/console?api=https://<<TUNNEL_URL>>`

Note for the committee: the `?api=...` query parameter only needs to be
opened once — the console stores the backend tunnel address to
`localStorage` and reuses it on every subsequent visit to the same browser,
so links shared after the first open can drop the parameter.

## Test login credentials

Operator-role API key: `<<OPERATOR_KEY from data/submission-credentials.md>>`

Sign in from the console's Admin page (or the sign-in prompt on any
gated action) with this key to exercise operator actions (acknowledge
alerts, maintain the watchlist). No sign-in is required to view the console
as an anonymous viewer — read access to cameras, detections, alerts and
traces needs no credential at all.

## Own-feed demo video (unlisted YouTube)

`<<YOUTUBE_OWN>>`

## Government-feed demo video (unlisted YouTube)

`<<YOUTUBE_GOV>>`

## Drive/OneDrive folder link

`<<DRIVE_FOLDER>>`

Folder contents:
- Solution presentation (PPT and PDF)
- High-level design document (PDF export of `docs/high-level-design.md`)
- Own-feed demo video (or a copy alongside the YouTube link)
- Government-feed demo video (or a copy alongside the YouTube link)
- Output report PDF (`netra-government-feed-report-YYYYMMDD.pdf`)

## Repository link

`https://github.com/Vatsa10/sentinel`

## Output report link

`<<DRIVE_FOLDER>>/netra-government-feed-report-YYYYMMDD.pdf` — see
`docs/submission/demo-gov-feed-script.md` for the exact generation and
Save-as-PDF procedure and the filename convention.

## Additional notes

The Government grid's measured condition (30 cameras, 20% unable to support
analytics, zero legible plates across 2,691 sampled frames) is documented in
`docs/high-level-design.md` §2 and `docs/feed-recon-findings.md`, and drove
the design decision to base matching on vehicle appearance and watchlist
rules rather than plate text alone. The hosted console and backend tunnel
above are demonstration infrastructure only; `docs/deploy.md` describes the
identical, unchanged code path for an on-premise deployment behind a
district-issued domain.
