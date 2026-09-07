# NETRA — Submission Pack

This folder holds everything needed to submit and demonstrate NETRA for the
Gujarat Police Innovation Challenge 2026 (Sentinel CCTV track): the two demo
scripts, the report-generation procedure, and the form answers. Submission
deadline is 15 September 2026; the live event is 22–23 September 2026.

## Contents

- `demo-own-feed-script.md` — 2:30 shot table for the own-feed recording (onboarding, detection, watchlist, alerting, tracing, zones, RBAC, report).
- `demo-gov-feed-script.md` — 2:30 shot table for the Government-grid recording, plus the exact report-generation and Save-as-PDF procedure.
- `form-answers.md` — every submission form field, written out, with placeholders only for values that do not exist yet (hosted URL, tunnel URL, YouTube links, Drive folder, operator key).
- `presentation.pptx` (built separately, per `docs/presentation-outline.md`) — the 14–16 slide solution presentation.

## Order of operations for the final submission day

Work through these in order. Each step depends on the one before it, so do
not skip ahead — a video recorded before the pipeline is warmed up, or a PDF
exported before the tunnel is stable, has to be redone.

1. **Confirm the environment is ready.** Run through `docs/deploy.md`'s
   pre-demo checklist in full: `python run.py --check` shows `cuda=True`,
   the pipeline is running against the 8 Ahmedabad/Junagadh grid cameras
   plus `data/own_feed_test.mp4`, `data/api_keys.json` and
   `data/submission-credentials.md` are present and matching, and the
   cloudflared tunnel is up with its URL noted.
2. **Build the presentation deck.** Generate `presentation.pptx` from
   `docs/presentation-outline.md`, taking screenshots from the now-hosted
   console (not local dev) so the URLs and data in the slides match what
   the committee will actually see. Export a PDF copy alongside it.
3. **Record the two demo videos**, in this order:
   - `demo-own-feed-script.md` first, since it exercises sign-in, seeding
     and onboarding that leave the system in a known state for the next
     recording.
   - `demo-gov-feed-script.md` second, against the grid only, with no own
     footage in frame.
   Do a dry run of each script once, unrecorded, before the take that will
   be uploaded — this catches stalled tiles, missing seed data or a tunnel
   URL that expired between the checklist and the recording.
4. **Upload both videos to YouTube as unlisted**, and paste the resulting
   links into `form-answers.md` in place of `<<YOUTUBE_OWN>>` and
   `<<YOUTUBE_GOV>>`.
5. **Generate the output report.** Follow `demo-gov-feed-script.md`'s report
   procedure exactly: last-24-hours window, Save as PDF with background
   graphics on, filename `netra-government-feed-report-YYYYMMDD.pdf` using
   the actual generation date.
6. **Export the HLD to PDF** (`docs/high-level-design.md`) for the Drive
   folder alongside the presentation.
7. **Upload to Drive/OneDrive**: the presentation (PPT and PDF), the HLD
   PDF, both demo videos (or copies alongside the YouTube links), and the
   output report PDF. Set the folder to link-shareable and paste the link
   into `form-answers.md` in place of `<<DRIVE_FOLDER>>`.
8. **Fill in the remaining placeholders** in `form-answers.md`: the Vercel
   URL and tunnel URL that make up the hosted platform URL, and the
   operator key copied from `data/submission-credentials.md`. Re-read the
   whole file once more before pasting into the live form, and verify the
   form's actual field labels match the section headings used here —
   adjust wording if the portal's live form differs.
9. **Submit the form**, pasting each answer from `form-answers.md` into the
   corresponding live field.
10. **Keep the laptop, backend and tunnel running** through the evening of
    15 September 2026, in case the committee opens the hosted URL shortly
    after submission to spot-check it. Do not shut down or restart the
    tunnel process during this window — a restart changes the quick
    tunnel's URL, breaking the link already submitted, unless the
    production named-tunnel path in `docs/deploy.md` has been adopted by
    then.
11. **Repeat the same start sequence for the live event**, 22–23 September
    2026: regenerate API keys if they were rotated in the interim, restart
    the pipeline, confirm the tunnel URL (it will very likely differ from
    the one submitted on 15 September, since quick tunnels are
    per-session), and update the console's `?api=` override to the new URL
    once at the venue before judges arrive.

## What is authoritative

Every measured number quoted across the demo scripts and form answers
traces back to `docs/high-level-design.md` (particularly §2 and §7) and
`docs/feed-recon-findings.md`. If a number in this folder ever disagrees
with those documents, the HLD and the recon findings are correct and this
folder should be updated to match — not the other way round.
