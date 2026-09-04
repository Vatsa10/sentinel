# Task 9 report — vision-language vehicle attributes

**Status: complete.** Florence-2-base now turns a vehicle evidence crop into a
sentence an officer can read, search in plain language and testify to, on a
grid where no plate is recoverable. Extraction is tiered exactly as the brief
requires; the live pipeline still drops 0% of frames with it enabled.

## What was built

| Piece | Where |
| --- | --- |
| `VehicleAttributes`, `AttributeExtractor`, deterministic caption parser | `netra/analytics/attributes.py` (new) |
| `VehicleAttributeRow` (new table, one row per detection) | `netra/core/models.py` |
| `ATTRIBUTE_MODEL`, `ATTRIBUTES_ENABLED`, `ATTRIBUTE_ESCALATED_INTERVAL_S`, `ATTRIBUTE_QUEUE_SIZE` | `netra/config.py` |
| Bounded, dropping background worker; alert and zone hooks; escalated-camera tier | `netra/pipeline.py` |
| `POST /api/detections/{id}/describe`; attributes on the detections list, alerts list and `/similar` | `netra/api/app.py` |
| `vehicle` as a fourth BM25 entity kind | `netra/api/retrieval.py` |
| `_vehicle_facts` — facts from SQL, the description only resolves the mention | `netra/api/assistant.py` |
| `attribute_agreement()` — the third re-identification signal | `netra/analytics/reid.py` |
| Description under the evidence crop, "Describe" button, live fold-in of a late description | `netra/web/app.js`, `index.html` |

`netra/analytics/inference.py` was **not** touched. The escalated-camera tier
is driven entirely from `Pipeline._flush`, which already has both the persisted
detection ids and the supervisor's escalation state.

## The three tiers, and what stops each one costing anything

1. **Alerts and zone events** — extraction is submitted *after* the alert has
   been persisted, broadcast and handed to the notifier, so a slow model cannot
   delay an alert by a millisecond. The description reaches the console as a
   separate `kind: "attributes"` message and is folded into the card already on
   screen, but only if it completes within `ATTRIBUTE_BROADCAST_BOUND_S = 3.0`;
   past that it is persisted and the console fetches it.
2. **Operator request** — `POST /api/detections/{id}/describe` (permission
   `read`, audited). Synchronous, because the officer is waiting.
3. **Largest vehicle per escalated camera**, at most once per
   `ATTRIBUTE_ESCALATED_INTERVAL_S = 30` per camera. The interval is stamped on
   the *attempt*, so a camera whose crop is dropped by a full queue still waits
   its turn rather than retrying on every flush.

All three go through one `queue.Queue(maxsize=32)` that **drops when full** and
counts the drops, drained by one daemon thread. The worker reads crops back
from `evidence_path` on disk rather than holding frames, so a queue of pending
descriptions costs 32 dictionaries rather than 32 decoded frames. Nothing in
this path can block the inference thread or the writer thread.

## Measurements (RTX 5050, 8 GB, transformers 4.57.6)

**Model load:** 9.86 s. **VRAM after load:** 453 MiB allocated / 472 MiB
reserved. **Peak during generation:** 936 MiB — comfortable beside YOLOv8m,
ReID and OCR.

**Per-crop `<MORE_DETAILED_CAPTION>`:** 2,406 ms first call (warm-up),
**908–985 ms** thereafter. **Batch of 3:** 2,378 ms total, **793 ms/crop** — the
batch saving is real but modest, because beam search dominates.

Both workarounds from the brief were required and are in the code with the
reason on them: `attn_implementation="eager"` at load (the remote code declares
no `_supports_sdpa`) and `use_cache=False` at generate (its cache handling
raises during beam search otherwise).

### A real caption from the grid, verbatim

Crop `data/evidence/cam13_1788555803209_313.jpg` (788×926), 956 ms:

> The image is a still from a CCTV footage of a truck parked on the side of a
> street. The truck is orange and white in color and has a sign on the front
> that reads "Cafe". The truck appears to be in motion, as it is moving towards
> the right side of the image. The street is lined with buildings and there are
> a few cars parked on both sides of the road. The image is taken from a low
> angle, looking up at the

Parsed:

```
body_type: truck   colour: yellow   tinted_windows: None   wheels: unknown
roof_rack: None    markings: ['Cafe']   damage: []
description: "yellow truck; markings: Cafe"
confidence: 0.429
```

The transcribed lettering is the point: on this grid "the truck with *Cafe* on
the front" narrows a search far harder than a 512-dimension vector can be made
to, and an officer can say it out loud.

A second real crop, `cam13_1788555867194_163.jpg`, gave *"white bus; markings:
logo"* at confidence 0.429.

### A false positive found and fixed against real data

Florence-2 opens a great many night captions with *"The image is a black and
white photograph of a street..."*. The first parser read `black` out of that as
the **vehicle's** colour — which would put black vehicles in front of an
operator on the strength of the image being monochrome. Those phrases are now
masked before colour matching (`_COLOUR_MASK`), with a self-check for both the
false positive and the case where a genuine colour appears in the same
sentence.

## Live check — 5 grid cameras, ~95 s, attributes enabled

`POST /api/pipeline/start?cameras=cam04,cam13,cam14,cam15,cam01`

```
inference:   submitted 501  dropped 0  processed 501  vehicles 3077
             embedded 1753  infer_ms 35.1
dropped:     0  (0.00%)
attributes:  queued 14  processed 14  dropped 0  failed 0  broadcast 1
             queue_depth 0  enabled true
scheduling:  escalated [cam04, cam13, cam15]  escalation_denied 109
persistence: written 3053  write_dropped 0  zone_events 5
```

**Zero dropped frames**, and zero attribute-queue drops: 14 descriptions in 95
seconds is roughly 14 seconds of GPU against 95 seconds of wall clock across
three escalated cameras, which is what the 30-second per-camera interval is
sized to deliver.

Descriptions the escalated tier produced unprompted during that run:

```
143917 escalated 0.714 | white suv; tinted windows; alloy wheels; roof rack
144684 escalated 0.714 | white suv; alloy wheels; roof rack; markings: 14-06-2026 08:16
144132 escalated 0.286 | white van
145148 escalated 0.143 | black
```

## Operator request, live

```
$ curl -X POST http://127.0.0.1:8080/api/detections/147300/describe
{
  "detection_id": 147300, "cached": false,
  "attributes": {
    "body_type": "unknown", "colour": "white", "tinted_windows": null,
    "wheels": "unknown", "roof_rack": null, "markings": [], "damage": [],
    "description": "white",
    "raw_caption": "The image shows a close-up of a machine in a factory or
      workshop. The machine appears to be a type of printing press, with a
      white body and a green base. ...",
    "model": "microsoft/Florence-2-base", "confidence": 0.143,
    "source": "operator",
    "note": "A vision-language description of the evidence crop, ... It
      describes what the vehicle looks like; it does not identify the vehicle."
  }
}
```

That crop is a small, dark, distant vehicle and the model made nonsense of it —
which is exactly the case the honesty rules exist for. `body_type` stays
`unknown` rather than being guessed, confidence reports itself as 0.14, and the
raw caption travels with the answer so an operator can see precisely what was
said before believing any of it.

## Plain-language search, live

```
$ curl -X POST /api/assistant -d '{"question":"search for a white suv with a roof rack and alloy wheels"}'

2 match(es) ... Detection 143917 - a silver car on 04 Paldi Circle at
2026-09-04 22:30:50. Described by microsoft/Florence-2-base as "white suv;
tinted windows; alloy wheels; roof rack" (confidence 0.71) - a description of
the crop, not an identification. Detection 144684 - ...
```

The division of labour holds: BM25 over the descriptions decided *which
detections were meant*; the camera, the time and the vehicle class in the
answer are all read from the detections table. Note that the SQL colour says
`silver` where the caption says `white` — the two signals are shown side by
side rather than reconciled, which is the honest presentation.

## Honesty properties, enforced in code

- No field is ever guessed. Unmentioned → `unknown`/`None`; negated ("not
  tinted", "no roof rack") → `False`, never the positive.
- `confidence` is the fraction of seven fields the caption actually populated,
  capped at 0.9.
- `raw_caption`, `model` and `source` are stored and returned with every
  description, and every API response carries a note saying it describes the
  crop and does not identify the vehicle.
- Attributes as a third ReID signal move the *presented* similarity by at most
  **+0.03** on agreement and **−0.05** on disagreement, with a `reasons`-style
  string saying so; the raw cosine is returned unchanged alongside it, the
  ranking and the ambiguity flag are still decided on raw appearance, and the
  adjustment only ever applies to candidates appearance had already selected.
  Attributes alone never create a candidate.
- A load failure degrades to "attributes unavailable" with one warning; the
  pipeline runs on without descriptions.

## Verification run

```
python -m netra.analytics.attributes   attributes self-check passed   (no model, no GPU, no network)
python -m netra.analytics.reid         reid self-check passed
python -m netra.analytics.matching     matching self-check passed
python -m netra.analytics.tracking     tracking self-check passed
python -m netra.api.retrieval          retrieval self-check passed
python -m netra.api.assistant          assistant self-check passed
python -c "from netra.api.app import app"   app ok
node --check netra/web/app.js               ok
git status --porcelain data/                empty
```

## Known limits

- `ponytail:` one prompt only. `<OD>` and `<DENSE_REGION_CAPTION>` would
  localise a roof rack or a dent instead of merely noting it, at another full
  generation pass per crop. Ceiling: an attribute the model does not say out
  loud is absent, and is reported honestly as unknown.
- `ponytail:` the description index carries the newest
  `VEHICLE_INDEX_LIMIT = 5000` described vehicles. Older ones stay findable by
  camera, time or plate, but not by description.
- The captioner reads burned-in overlay timestamps as vehicle markings
  (`markings: 14-06-2026 08:16` above). It is genuinely text in the crop, so
  the parser is not wrong, but it is noise in the markings field.
- Colour is folded onto the coarse seven-word palette the pipeline already
  uses, so "orange and white" becomes `yellow`. That keeps the caption's colour
  comparable with `estimate_colour`'s, which is what `attribute_agreement`
  needs, at the cost of precision the lighting would not support anyway.
- A zone event describes a whole frame rather than one vehicle and has no
  detection row to key attributes to, so its description is pushed to the
  console and not stored.
