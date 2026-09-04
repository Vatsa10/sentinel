# Task 2 report — Cloned-plate detection

**Status: DONE**

## What was built

- `netra/core/timing.py` (new): `sighting_time(det)` — the scene-time-then-wall-time
  preference, moved out of `route.py` so route reconstruction and clone detection
  order the same rows identically. `route.py` now imports it as `_sighting_time`;
  its self-check still passes unchanged.
- `netra/analytics/cloned_plate.py` (new): `CloneFinding` dataclass and
  `find_clones(detections, min_confidence=0.6)`.
- `netra/api/app.py`: `GET /api/analytics/cloned-plates` — read-only, no new
  permission, `min_confidence` and `limit` bounded via `Query`, audited as
  `analytics.cloned_plates`.
- `netra/api/assistant.py`: `_cloned_plates` intent, placed first in `INTENTS`.

## Design decisions

**Time groups.** A pair is compared only when both cameras resolve to the *same*
non-`None` `time_group`. `None` is treated as incomparable rather than as its own
group: we cannot demonstrate the clocks agree, so we must not assert a speed
between them. Two self-check cases pin this — cross-session and unlisted-camera
pairs with arithmetic that would otherwise be flagrantly impossible produce no
finding.

**Confidence.** `violation = 1 - (MAX_PLAUSIBLE_KMH / implied_kmh)`, scaled by
`0.5 + 0.5 * min(plate_conf_a, plate_conf_b)`, capped at `MAX_CONFIDENCE = 0.99`.
The violation term is near 0 just over the 120 km/h ceiling and approaches 1 as
the implied speed runs away, so a 200 km/h pair falls below the 0.6 default
threshold while a 3,500 km/h pair scores ~0.92. Simultaneous sightings at
separated cameras (elapsed 0) take violation 1.0. Plate quality can halve a score
but never zero it.

**Reported plate.** Grouping keys on `normalise_plate` (confusion-folded), but the
finding reports the plate *as read*, following the precedent in `matching.py`:
an officer shown "6J01A81234" would think the wrong vehicle had been flagged.
Where the two reads differ only by a folded confusion, both are shown and the
reason says so.

**Guards.** Same camera; partial reads under 6 characters; missing coordinates at
either end; zero distance between co-located cameras; single-sighting plates.

**Assistant ordering.** The clone intent precedes `_find_plate` because "find
cloned plates" contains "find". It cannot shadow the plate trace: `ask()` matches
`PLATE_RE` and dispatches to `_find_plate` before the keyword loop runs. Asserted
both ways in the assistant self-check.

## ponytail ceilings

- `find_clones` compares consecutive pairs only. A clone active across three
  cameras is reported as its adjacent impossible hops rather than as one cluster
  — the officer reads two findings instead of one; nothing is missed.
- Reused `netra/core/geo.py`'s hand-built coordinate table, whose ceiling is
  already documented there. Distances are junction-level approximations, which is
  why the confidence curve is near zero for marginal violations.

## Verification

All five required commands pass:
`cloned_plate`, `route`, `matching`, `assistant` self-checks, and the app import.
No network, no GPU, no database in the detector's self-check — synthetic camera
and detection objects, as `route.py` does.

## Concerns

None blocking. The detector loads every plate-bearing detection per request; at
the current dataset size that is trivial, but at deployment scale the endpoint
would want a time window or a per-plate index.
