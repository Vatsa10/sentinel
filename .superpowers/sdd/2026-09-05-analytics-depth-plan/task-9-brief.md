# Task 9 brief — VLM vehicle attributes

## Task 9 — Vision-language vehicle attributes

**Why:** the platform's cross-camera re-identification is a 512-dimension
vector. It works, but an officer cannot read it, search it, or testify to it.
A vision-language model turns the same crop into *"black SUV, heavily tinted
windows, aftermarket alloy wheels, roof rack, dent on rear left"* — which is
searchable in plain language, explainable in court, and matchable across
cameras without a plate. On this grid, where plates are measured to be
unreadable, that is the most useful description of a vehicle the platform can
produce.

**Model:** Florence-2 (`microsoft/Florence-2-base`, ~0.23B parameters, MIT
licence). Loaded via `transformers` with `trust_remote_code=True`. Weights
download on first use into the Hugging Face cache; on this machine that has
already been confirmed to work. Runs in fp16 on the existing 8 GB GPU beside
YOLOv8m, ReID and OCR. This task introduces `transformers`, `timm` and
`einops` as dependencies — a deliberate, recorded exception to the plan's
no-new-heavyweight-dependencies constraint, justified because there is no
stdlib route to a vision-language model.

**Feasibility, already proven on this machine (transformers 4.57.6) — use these
exact settings, both are required or generation fails:**

```python
model = AutoModelForCausalLM.from_pretrained(
    "microsoft/Florence-2-base", trust_remote_code=True,
    torch_dtype=torch.float16, attn_implementation="eager")   # else: no _supports_sdpa
...
model.generate(..., max_new_tokens=96, num_beams=3, do_sample=False,
               use_cache=False)                                # else: NoneType .shape
```

Measured: load 8.1 s, 452 MiB VRAM after load, per-crop `<MORE_DETAILED_CAPTION>`
888–2,578 ms (first call is warm-up). Sample real caption from cam13:
*"...a truck parked on the side of a street. The truck is orange and white in
color and has a sign on the front that reads 'Cafe'..."* — colour, body type
and markings all present in free text, which is what the parser extracts.

**Create `netra/analytics/attributes.py`:**

- `VehicleAttributes` dataclass: `body_type` (hatchback | sedan | suv | van |
  pickup | truck | bus | auto_rickshaw | motorcycle | unknown), `colour`,
  `tinted_windows: bool | None`, `wheels` (stock | alloy | unknown),
  `roof_rack: bool | None`, `markings: list[str]` (stickers, livery, text),
  `damage: list[str]`, `description: str` (one natural-language sentence),
  `raw_caption: str`, `model: str`, `confidence: float`.
- `AttributeExtractor` class, lazy-loading, single instance shared with the
  inference engine's device lock: `load()`, `ready`, `describe(crop) ->
  VehicleAttributes`, `describe_batch(crops) -> list[VehicleAttributes]`.
- Structured fields are parsed from Florence-2's output using two prompts:
  `<MORE_DETAILED_CAPTION>` for the free-text description and `<OD>` /
  `<DENSE_REGION_CAPTION>` where helpful. Parsing is keyword-based over the
  caption (deterministic, testable): colour vocabulary, body-type vocabulary,
  "tint", "alloy", "roof rack", "sticker", "dent", "scratch", "broken".
  Where the caption gives no signal, the field is `None`/`unknown` — never a
  guess.
- `confidence` reflects how many structured fields were actually populated
  from the caption, capped at 0.9. A description is evidence for an operator,
  not an identification, and the module says so in its docstring.
- Every cost-control rule the rest of the pipeline follows applies here: the
  extractor never runs on the inference thread's hot path for every
  detection. See wiring below.

**Wire in (the tiering discipline is the whole point):**

- New table `VehicleAttributeRow` in `netra/core/models.py` keyed by
  `detection_id` (one-to-one), storing every structured field, `description`,
  `raw_caption`, `model`, `confidence`, `created_at`. Add via the existing
  additive-column mechanism only if altering an existing table; a new table
  needs nothing extra.
- Extraction runs in **three places only**:
  1. On every watchlist **alert** and **zone event** — the vehicle already
     matters, so the cost is justified. Wire in `Pipeline._raise_alert` and
     `_handle_zone_event`, on the writer/alert path, never the inference
     thread.
  2. On **operator request**: `POST /api/detections/{id}/describe`
     (permission `read`) which extracts if absent and returns the attributes.
  3. On the **largest vehicle per escalated camera**, at most once per
     `ATTRIBUTE_ESCALATED_INTERVAL_S = 30` per camera, on a background worker
     fed by a bounded queue that drops when full. Detection must never wait on
     it. Measured precedent for why: unbounded overlay OCR cost 71% of frames.
- Attributes join the assistant's BM25 entity corpus (`netra/api/retrieval.py`)
  as a fourth kind, `vehicle`, indexed on `description` and `markings`, so
  *"find the black SUV with a roof rack"* resolves to detections. Facts about
  a resolved vehicle still come from SQL, never from the index.
- Attributes become a **third signal** in `netra/analytics/reid.py`'s
  cross-camera candidate ranking: when both detections carry attributes,
  agreement on `body_type` and `colour` raises the presented confidence a
  little and disagreement lowers it, with the reasoning attached. Attributes
  alone never establish a match — the same rule that binds appearance and
  colour in `matching.py`.
- `GET /api/detections/{id}` (or the detections list) surfaces the attributes
  where present; the console shows `description` under the evidence crop in
  the Detections table and in alert cards, and offers a "Describe" button that
  calls the operator-request endpoint.

**Config (`netra/config.py`):** `ATTRIBUTE_MODEL` (default
`microsoft/Florence-2-base`), `ATTRIBUTES_ENABLED` (default on),
`ATTRIBUTE_ESCALATED_INTERVAL_S`, `ATTRIBUTE_QUEUE_SIZE = 32`.

**Self-check (`python -m netra.analytics.attributes`) must be model-free and
GPU-free.** It tests the caption parser only, with synthetic captions: a rich
caption populates body type, colour, tint, wheels, roof rack, damage; a bare
caption yields `unknown`/`None` rather than guesses; confidence is 0 for an
empty caption and never exceeds 0.9; a caption mentioning "not tinted" does
not set `tinted_windows` true. Real model inference is verified separately by
the implementer against a live crop and reported with timing.

**Verification to report:** model load time; per-crop and per-batch latency
in ms on the RTX 5050; VRAM after load; one real caption from a grid crop
verbatim with its parsed attributes; confirmation the live pipeline still
runs with 0% dropped frames on 5 cameras with attribute extraction enabled.

---

## Global Constraints (from the plan)

- Every non-trivial module carries a runnable `_self_check()` invoked via
  `python -m netra.<module>`, plain `assert`, no test framework.
- No new heavyweight dependencies **except the recorded exception above**.
- Nothing may starve detection. Detection is the primary duty; enrichment must
  be bounded, capped, or opportunistic.
- Honesty over impressiveness. Never present inference as fact. Confidence
  and reasoning travel with every alert.
- All timing from PTS or scene time, never arrival time.
- Style: match surrounding code; comments explain *why*; `ponytail:` comments
  name deliberate simplifications and their ceiling. British spelling.
- Commit per task with a descriptive body explaining the reasoning.
