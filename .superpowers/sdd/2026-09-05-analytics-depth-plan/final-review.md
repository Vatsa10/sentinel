# Final whole-branch review package — ca2eac1..HEAD

## Commits (34)
8d8cb28 Vatsa Joshi | Tier C: traffic auto-poll, a retention ceiling, and the stray marker file
04c14d6 Vatsa Joshi | Tier B: freshness, stability, precision and the duplicated block
b995bfb Vatsa Joshi | Tier A: close the correctness, honesty and access-control holes
c89d4d1 Vatsa Joshi | Look attributes up only for the candidates that are actually returned
200d315 Vatsa Joshi | Show the vehicle description in the console, and stop reading colour off a monochrome caption
c189737 Vatsa10 | feat: add vehicle attribute extraction and description capabilities
acaa072 Vatsa Joshi | Require a second reading before trusting the scene clock
21f987b Vatsa10 | feat: add Task 8 re-review documentation and fix snapshot handling
b271f56 Vatsa Joshi | De-duplicate concurrent snapshot grabs and gate the endpoint on read
111faa2 Vatsa10 | fix: address serialization issues and enhance API security in Task 8
a7799d3 Vatsa10 | feat: add vision-language model for vehicle attribute extraction
03da258 Vatsa Joshi | Surface zones, traffic and intelligence in the operator console
47cead0 Vatsa10 | Refine scene clock anchoring logic; implement corroboration for overlay readings and tighten plausible year range
8f6eab5 Vatsa10 | Enhance indexing and anchoring logic for scene clock; improve journey mining response handling
8036c3a Vatsa10 | Add detailed reporting for journey mining and enhance embedding checks
810e7a9 Vatsa10 | Implement fuzzy entity resolution for the assistant
9791274 Vatsa Joshi | Count embeddings honestly, and stop one caller shrinking the shared store
9f16d81 Vatsa Joshi | Stop a greedy chain welding dozens of vehicles into one journey
99927bd Vatsa Joshi | Mine real cross-camera journeys from the grid's looping recordings
40648f7 Vatsa Joshi | Stop intent words from sinking the entity they qualify
e5e4825 Vatsa Joshi | Resolve fuzzy entity mentions before the assistant queries
a439b76 Vatsa Joshi | Stop the quiet branch calling an ordinary empty minute a blockage
87c19b2 Vatsa Joshi | Learn what normal looks like per camera, per hour
0880add Vatsa10 | Refactor code structure for improved readability and maintainability
bdb43cb Vatsa Joshi | Stop the watchlist prefilter losing genuine alerts
845e460 Vatsa Joshi | Bound evidence, detections and watchlist scoring
2dedd63 Vatsa Joshi | Partition clone candidates by session before pairing
8cd2dfc Vatsa Joshi | Detect cloned plates from space-time impossibility
36777e4 Vatsa Joshi | Bound tracker state and guard three silent-corruption paths
fe40372 Vatsa Joshi | Report how many reads actually voted, not how many were held
c770844 Vatsa Joshi | Vote plate reads across frames instead of trusting one
3dc205a Vatsa Joshi | Measure the bandwidth case and complete the technical proposal
c106b63 Vatsa Joshi | Add tracking, zone rules, traffic analytics and the output report
04171f9 Vatsa10 | Add zone rule management and tracking capabilities

## Stat
 .gitignore                                         |   3 +
 docs/high-level-design.md                          | 209 ++++-
 .../plans/2026-09-05-analytics-depth-plan.md       | 496 +++++++++++
 netra/analytics/attributes.py                      | 595 +++++++++++++
 netra/analytics/baseline.py                        | 490 +++++++++++
 netra/analytics/cloned_plate.py                    | 354 ++++++++
 netra/analytics/inference.py                       | 580 +++++++++++-
 netra/analytics/loop_index.py                      | 970 +++++++++++++++++++++
 netra/analytics/matching.py                        | 296 +++++++
 netra/analytics/plate_vote.py                      | 285 ++++++
 netra/analytics/reid.py                            | 159 +++-
 netra/analytics/route.py                           |  37 +-
 netra/analytics/scene_clock.py                     |  33 +-
 netra/analytics/tracking.py                        | 372 ++++++++
 netra/analytics/zones.py                           | 259 ++++++
 netra/api/app.py                                   | 625 ++++++++++++-
 netra/api/assistant.py                             | 512 ++++++++++-
 netra/api/report.py                                | 258 ++++++
 netra/api/retrieval.py                             | 591 +++++++++++++
 netra/config.py                                    |  34 +
 netra/core/db.py                                   |  37 +
 netra/core/models.py                               | 150 ++++
 netra/core/retention.py                            | 439 ++++++++++
 netra/core/timing.py                               |  89 ++
 netra/pipeline.py                                  | 441 +++++++++-
 netra/web/app.js                                   | 458 +++++++++-
 netra/web/index.html                               | 102 ++-
 tools/index_loops.py                               | 146 ++++
 tools/measure_bandwidth.py                         | 135 +++
 tools/purge_scene_times.py                         |  82 ++
 30 files changed, 9167 insertions(+), 70 deletions(-)

## Diff
diff --git a/.gitignore b/.gitignore
index 54b6594..f67c565 100644
--- a/.gitignore
+++ b/.gitignore
@@ -1,10 +1,13 @@
 .venv/
 wheels/
 data/
 *.log
+# Marker files dropped by long-running tool invocations; one was committed
+# to the repo root by the auto-committer.
+*.start
 __pycache__/
 *.pyc
 
 # Credentials and local state
 .env
 data/
diff --git a/netra/analytics/attributes.py b/netra/analytics/attributes.py
new file mode 100644
index 0000000..8ad8ba0
--- /dev/null
+++ b/netra/analytics/attributes.py
@@ -0,0 +1,595 @@
+"""Vision-language vehicle attributes: a description an officer can read.
+
+Cross-camera re-identification on this grid runs on a 512-dimension appearance
+vector (`reid.py`), because plates are not recoverable here - measured over
+2,691 frames, more than two hundred vehicles, zero readable plates. That vector
+works, but an operator cannot read it, cannot search it, and cannot testify to
+it. "Candidate 4, cosine 0.87" is not something anyone can act on alone.
+
+A vision-language model turns the same crop into words: *"black SUV, heavily
+tinted windows, aftermarket alloy wheels, roof rack, dent on the rear left"*.
+That is searchable in plain language, explainable in a courtroom, and
+comparable across cameras without a plate.
+
+Two halves, deliberately separated:
+
+    Florence-2   produces free text about the crop. Probabilistic, and the only
+                 part that needs a GPU or a download.
+    the parser   turns that text into structured fields by exact keyword
+                 matching. Deterministic, testable, and model-free - which is
+                 why `python -m netra.analytics.attributes` needs neither
+                 network nor GPU.
+
+**A description is evidence for an operator, never an identification.** The
+model describes what a crop looks like; it does not know what vehicle it is.
+Where the caption gives no signal the field stays `unknown`/`None` rather than
+being guessed, `confidence` never exceeds 0.9, and attributes may only nudge a
+re-identification score that appearance already produced - never create one.
+
+ponytail: one prompt (`<MORE_DETAILED_CAPTION>`) and a hand-written keyword
+vocabulary. Florence-2 also offers `<OD>` and `<DENSE_REGION_CAPTION>`, which
+would localise a roof rack or a dent rather than merely noting it, at roughly
+another full generation pass per crop. The ceiling of the present approach is
+whatever the caption happens to mention: an attribute the model does not say
+out loud is simply absent, and the parser reports that honestly as unknown.
+"""
+from __future__ import annotations
+
+import logging
+import re
+import threading
+from dataclasses import dataclass, field, asdict
+
+from netra import config
+
+log = logging.getLogger(__name__)
+
+#: The prompt Florence-2 answers with a paragraph of free text. The plain
+#: `<CAPTION>` task returns a single clause ("a car on a road") that carries
+#: none of the detail this module exists to extract.
+CAPTION_TASK = "<MORE_DETAILED_CAPTION>"
+
+#: Generation settings. Both are load-bearing on transformers 4.57.6 with the
+#: Florence-2 remote code: beam search without `use_cache=False` raises inside
+#: the model's own cache handling, and the default sdpa attention selection
+#: fails because the remote module declares no `_supports_sdpa`. See
+#: `AttributeExtractor.load`.
+MAX_NEW_TOKENS = 96
+NUM_BEAMS = 3
+
+#: Ceiling on reported confidence. Every field here is inferred from a sentence
+#: a model wrote about a night-time CCTV crop; there is no reading of this
+#: evidence that deserves to be presented as near-certain.
+MAX_CONFIDENCE = 0.9
+
+BODY_TYPES = ("hatchback", "sedan", "suv", "van", "pickup", "truck", "bus",
+              "auto_rickshaw", "motorcycle")
+
+#: Caption phrase -> body type. Longest phrases are matched first (see
+#: `_match_vocabulary`), so "pick-up truck" cannot be claimed by "truck" and
+#: "sport utility vehicle" cannot be claimed by "vehicle".
+_BODY_VOCAB = {
+    "auto rickshaw": "auto_rickshaw", "auto-rickshaw": "auto_rickshaw",
+    "autorickshaw": "auto_rickshaw", "tuk tuk": "auto_rickshaw",
+    "tuk-tuk": "auto_rickshaw", "three wheeler": "auto_rickshaw",
+    "three-wheeler": "auto_rickshaw", "rickshaw": "auto_rickshaw",
+    "sport utility vehicle": "suv", "suv": "suv", "jeep": "suv",
+    "pick-up truck": "pickup", "pickup truck": "pickup", "pick up truck": "pickup",
+    "pickup": "pickup", "pick-up": "pickup", "ute": "pickup",
+    "hatchback": "hatchback", "estate car": "hatchback",
+    "sedan": "sedan", "saloon": "sedan",
+    "minivan": "van", "mini-van": "van", "van": "van",
+    "lorry": "truck", "tanker": "truck", "tipper": "truck",
+    "semi truck": "truck", "semi-truck": "truck", "truck": "truck",
+    "coach": "bus", "minibus": "bus", "bus": "bus",
+    "motorcycle": "motorcycle", "motorbike": "motorcycle",
+    "scooter": "motorcycle", "moped": "motorcycle",
+}
+
+#: Colour vocabulary, matching `inference.estimate_colour`'s coarse palette
+#: plus the handful of words a captioner reaches for that map onto it. Street
+#: lighting makes anything finer dishonest, and the two colour signals have to
+#: be comparable for `reid.attribute_agreement` to compare them at all.
+_COLOUR_VOCAB = {
+    "white": "white", "off-white": "white", "cream": "white",
+    "silver": "silver", "grey": "silver", "gray": "silver",
+    "black": "black", "dark grey": "black", "dark gray": "black",
+    "red": "red", "maroon": "red", "crimson": "red",
+    "blue": "blue", "navy": "blue", "teal": "blue",
+    "yellow": "yellow", "golden": "yellow", "gold": "yellow", "orange": "yellow",
+    "green": "green", "olive": "green",
+    "brown": "brown", "beige": "brown", "tan": "brown",
+}
+
+#: Phrases in which a colour word is not the vehicle's colour. Florence-2
+#: routinely opens with "a black and white photograph of a street", and a
+#: parser that read "black" out of that would put a black vehicle in front of
+#: an operator on the strength of the image being monochrome.
+_COLOUR_MASK = ("black and white", "white and black", "black-and-white")
+
+_TINT_PHRASES = ("tinted", "tint", "blacked out window", "blacked-out window",
+                 "smoked glass", "dark windows", "darkened windows")
+_ALLOY_PHRASES = ("alloy", "alloys", "chrome wheel", "chrome rim",
+                  "aftermarket wheel", "spoked wheel", "mag wheel")
+_STOCK_WHEEL_PHRASES = ("steel wheel", "hubcap", "hub cap", "stock wheel",
+                        "wheel cover", "plain wheel")
+_ROOF_RACK_PHRASES = ("roof rack", "roof-rack", "luggage rack", "roof rails",
+                      "roof bars", "carrier on the roof", "roof carrier")
+
+_MARKING_PHRASES = ("sticker", "decal", "livery", "logo", "sign", "lettering",
+                    "writing", "advertisement", "banner", "emblem", "graphic",
+                    "text")
+_DAMAGE_PHRASES = ("dent", "dented", "scratch", "scratched", "broken",
+                   "cracked", "smashed", "damaged", "rust", "rusted",
+                   "rusty", "missing bumper", "crumpled")
+
+#: Words that, standing close in front of a phrase, invert it. A caption
+#: saying "the windows are not tinted" must never set `tinted_windows` true -
+#: an operator filtering for tinted windows would be shown the one vehicle the
+#: model explicitly ruled out.
+_NEGATIONS = ("no", "not", "non", "without", "lacks", "lacking", "never",
+              "isn't", "aren't", "doesn't", "does not", "is not", "are not",
+              "free of", "devoid of")
+#: How many characters before a phrase are searched for a negation. Long enough
+#: for "the windows do not appear to be tinted", short enough that a negation
+#: about a different clause in the same sentence does not reach across.
+_NEGATION_WINDOW = 40
+
+#: Fields the confidence fraction is measured over. Seven, so one populated
+#: field is worth ~0.13 - a caption that mentions only a colour is reported as
+#: weak evidence, which is what it is.
+_SCORED_FIELDS = ("body_type", "colour", "tinted_windows", "wheels",
+                  "roof_rack", "markings", "damage")
+
+
+@dataclass
+class VehicleAttributes:
+    """A readable description of one vehicle crop, with its provenance."""
+    body_type: str = "unknown"
+    colour: str | None = None
+    tinted_windows: bool | None = None
+    wheels: str = "unknown"          # stock | alloy | unknown
+    roof_rack: bool | None = None
+    markings: list[str] = field(default_factory=list)
+    damage: list[str] = field(default_factory=list)
+    description: str = ""
+    raw_caption: str = ""
+    model: str = ""
+    confidence: float = 0.0
+
+    def as_dict(self) -> dict:
+        return asdict(self)
+
+
+def _normalise(caption: str) -> str:
+    """Lowercase with runs of whitespace collapsed, for phrase matching."""
+    return re.sub(r"\s+", " ", (caption or "").lower()).strip()
+
+
+def _negated(text: str, at: int) -> bool:
+    """True when a negation sits in the window just before position `at`."""
+    window = text[max(0, at - _NEGATION_WINDOW):at]
+    # Word-boundary anchored: "nowhere" must not negate, "no" must.
+    return any(re.search(rf"\b{re.escape(n)}\b", window) for n in _NEGATIONS)
+
+
+def _find_phrase(text: str, phrases) -> bool | None:
+    """Tri-state phrase presence: True, False if negated, None if unmentioned.
+
+    None and False are different answers and are kept apart everywhere: "the
+    caption said nothing about a roof rack" is not "the model saw no roof
+    rack", and an operator ruling vehicles in or out needs to know which.
+    """
+    seen = False
+    for phrase in phrases:
+        for m in re.finditer(rf"\b{re.escape(phrase)}", text):
+            seen = True
+            if not _negated(text, m.start()):
+                return True
+    return False if seen else None
+
+
+def _mask(text: str, phrases) -> str:
+    """Blank out phrases, keeping length so positions elsewhere still hold."""
+    for phrase in phrases:
+        text = re.sub(re.escape(phrase), " " * len(phrase), text)
+    return text
+
+
+def _match_vocabulary(text: str, vocab: dict) -> str | None:
+    """First vocabulary hit in the caption, longest phrase first.
+
+    Earliest position wins because a captioner leads with its subject: "a black
+    SUV parked behind a bus" is about the SUV. Longest-first within that stops
+    a specific phrase being swallowed by a substring of itself.
+    """
+    best_at, best_value = None, None
+    for phrase in sorted(vocab, key=len, reverse=True):
+        m = re.search(rf"\b{re.escape(phrase)}\b", text)
+        if m is None or _negated(text, m.start()):
+            continue
+        if best_at is None or m.start() < best_at:
+            best_at, best_value = m.start(), vocab[phrase]
+    return best_value
+
+
+def _quoted_text(caption: str) -> list[str]:
+    """Any lettering the model transcribed, e.g. reads 'Cafe'.
+
+    Text on a vehicle is the single most identifying attribute available when
+    the plate is not - a fleet name or a phone number narrows a search far
+    harder than "white van" does.
+    """
+    out = []
+    for m in re.finditer(r"['\"‘“]([^'\"’”]{2,40})"
+                         r"['\"’”]", caption or ""):
+        value = m.group(1).strip()
+        if value and value.lower() not in out:
+            out.append(value)
+    return out
+
+
+def _collect(text: str, phrases) -> list[str]:
+    """Every non-negated phrase from a vocabulary that the caption mentions."""
+    found = []
+    for phrase in phrases:
+        for m in re.finditer(rf"\b{re.escape(phrase)}", text):
+            if not _negated(text, m.start()) and phrase not in found:
+                found.append(phrase)
+            break
+    return found
+
+
+def _compose(attrs: VehicleAttributes, caption: str) -> str:
+    """One sentence an operator can read, built only from parsed fields."""
+    parts = []
+    head = " ".join(x for x in (attrs.colour,
+                                attrs.body_type.replace("_", " ")
+                                if attrs.body_type != "unknown" else None)
+                    if x)
+    if head:
+        parts.append(head)
+    if attrs.tinted_windows:
+        parts.append("tinted windows")
+    if attrs.wheels == "alloy":
+        parts.append("alloy wheels")
+    elif attrs.wheels == "stock":
+        parts.append("stock wheels")
+    if attrs.roof_rack:
+        parts.append("roof rack")
+    if attrs.markings:
+        parts.append("markings: " + ", ".join(attrs.markings))
+    if attrs.damage:
+        parts.append("damage: " + ", ".join(attrs.damage))
+    if parts:
+        return "; ".join(parts)
+    # Nothing structured survived the parse. The model's own first sentence is
+    # still the most useful thing to show, and showing it verbatim keeps the
+    # distinction between what was extracted and what was merely said.
+    first = re.split(r"(?<=[.!?])\s", (caption or "").strip())[0].strip()
+    return first
+
+
+def parse_caption(caption: str, model: str = "") -> VehicleAttributes:
+    """Turn a free-text caption into structured attributes. Deterministic.
+
+    Nothing here guesses. A field the caption does not speak to comes back
+    `unknown` or `None`, and `confidence` is the share of fields that the
+    caption actually populated - so a bare "a car on a road" reports itself as
+    almost worthless, which is the correct thing for it to do.
+    """
+    text = _normalise(caption)
+    attrs = VehicleAttributes(raw_caption=(caption or "").strip(), model=model)
+    if not text:
+        return attrs
+
+    attrs.body_type = _match_vocabulary(text, _BODY_VOCAB) or "unknown"
+    attrs.colour = _match_vocabulary(_mask(text, _COLOUR_MASK), _COLOUR_VOCAB)
+    attrs.tinted_windows = _find_phrase(text, _TINT_PHRASES)
+
+    if _find_phrase(text, _ALLOY_PHRASES) is True:
+        attrs.wheels = "alloy"
+    elif _find_phrase(text, _STOCK_WHEEL_PHRASES) is True:
+        attrs.wheels = "stock"
+
+    attrs.roof_rack = _find_phrase(text, _ROOF_RACK_PHRASES)
+
+    # Transcribed lettering first: it is the specific evidence, and the generic
+    # word ("a sign") is only worth recording when nothing was actually read.
+    attrs.markings = _quoted_text(caption) or _collect(text, _MARKING_PHRASES)
+    attrs.damage = _collect(text, _DAMAGE_PHRASES)
+
+    populated = sum(1 for f in _SCORED_FIELDS
+                    if _is_populated(getattr(attrs, f)))
+    attrs.confidence = round(
+        min(MAX_CONFIDENCE, populated / len(_SCORED_FIELDS)), 3)
+    attrs.description = _compose(attrs, caption)
+    return attrs
+
+
+def _is_populated(value) -> bool:
+    """A field counts towards confidence only when the caption spoke to it.
+
+    `False` counts: "the windows are not tinted" is a real observation. An
+    empty list and `None` and "unknown" do not.
+    """
+    if value is None:
+        return False
+    if isinstance(value, str):
+        return value not in ("", "unknown")
+    if isinstance(value, list):
+        return bool(value)
+    return True
+
+
+def unavailable(reason: str) -> VehicleAttributes:
+    """What every caller gets when the model cannot run.
+
+    Attribute extraction is enrichment. It failing must degrade to "we do not
+    know" and must never be mistaken for "there is nothing to say" - hence a
+    description that states the outage rather than an empty one.
+    """
+    return VehicleAttributes(description=f"attributes unavailable ({reason})")
+
+
+class AttributeExtractor:
+    """Florence-2, loaded lazily and shared through one device lock.
+
+    Mirrors `ReIdEncoder`: the model is never loaded at import, and every
+    forward pass is serialised behind `self._lock` so this cannot contend with
+    itself for the 8 GB the detector, ReID and OCR already share.
+    """
+
+    def __init__(self, model_name: str | None = None):
+        self.model_name = model_name or config.ATTRIBUTE_MODEL
+        self._model = None
+        self._processor = None
+        self._lock = threading.Lock()
+        #: set once a load has failed, so a broken install is not retried on
+        #: every alert - the first warning is the useful one
+        self._failed: str | None = None
+
+    @property
+    def ready(self) -> bool:
+        return self._model is not None
+
+    def load(self) -> bool:
+        """Load the weights. Returns False rather than raising.
+
+        A missing download, a torch/transformers mismatch or an out-of-memory
+        GPU must cost the platform its descriptions and nothing else: detection
+        is the primary duty and it does not depend on this.
+        """
+        if self._model is not None:
+            return True
+        if self._failed is not None:
+            return False
+        try:
+            import torch
+            from transformers import AutoModelForCausalLM, AutoProcessor
+
+            cuda = str(config.DEVICE).startswith("cuda") and torch.cuda.is_available()
+            dtype = torch.float16 if cuda else torch.float32
+            # attn_implementation="eager" is required: Florence-2 ships as
+            # remote code that predates the `_supports_sdpa` flag, and the
+            # default attention selection in transformers 4.57 raises on it.
+            model = AutoModelForCausalLM.from_pretrained(
+                self.model_name, trust_remote_code=True,
+                torch_dtype=dtype, attn_implementation="eager")
+            model.eval().to(config.DEVICE if cuda else "cpu")
+            self._processor = AutoProcessor.from_pretrained(
+                self.model_name, trust_remote_code=True)
+            self._model = model
+            self._dtype = dtype
+            self._device = config.DEVICE if cuda else "cpu"
+            log.info("attribute extractor ready (%s, %s)",
+                     self.model_name, self._device)
+            return True
+        except Exception as exc:
+            self._failed = str(exc)
+            log.warning("attribute extraction unavailable (%s) - the pipeline "
+                        "runs without descriptions", exc)
+            return False
+
+    # -- inference -----------------------------------------------------------
+    def describe(self, crop) -> VehicleAttributes:
+        """Describe one BGR crop."""
+        out = self.describe_batch([crop])
+        return out[0] if out else unavailable("no crop")
+
+    def describe_batch(self, crops: list) -> list[VehicleAttributes]:
+        """Describe several crops in one generation pass.
+
+        Batched because beam search dominates the cost: three beams over 96
+        tokens is the same decode whether it runs on one crop or four.
+        """
+        usable = [c for c in crops
+                  if c is not None and getattr(c, "size", 0) > 0]
+        if not crops:
+            return []
+        if not usable or not self.load():
+            reason = self._failed or "empty crop"
+            return [unavailable(reason) for _ in crops]
+
+        import cv2
+        import torch
+        from PIL import Image
+
+        images = [Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB))
+                  for c in usable]
+        prompts = [CAPTION_TASK] * len(images)
+        try:
+            with self._lock:
+                inputs = self._processor(text=prompts, images=images,
+                                         return_tensors="pt")
+                pixel_values = inputs["pixel_values"].to(self._device,
+                                                         self._dtype)
+                with torch.no_grad():
+                    generated = self._model.generate(
+                        input_ids=inputs["input_ids"].to(self._device),
+                        pixel_values=pixel_values,
+                        max_new_tokens=MAX_NEW_TOKENS, num_beams=NUM_BEAMS,
+                        do_sample=False,
+                        # Required: the remote code's cache handling raises on
+                        # a None past-key-value during beam search.
+                        use_cache=False)
+                texts = self._processor.batch_decode(generated,
+                                                     skip_special_tokens=False)
+        except Exception:
+            log.exception("attribute generation failed")
+            return [unavailable("generation failed") for _ in crops]
+
+        parsed = []
+        for text, image in zip(texts, images):
+            try:
+                answer = self._processor.post_process_generation(
+                    text, task=CAPTION_TASK,
+                    image_size=(image.width, image.height))
+                caption = answer.get(CAPTION_TASK, "") if isinstance(answer, dict) \
+                    else str(answer)
+            except Exception:
+                caption = text
+            parsed.append(parse_caption(caption, model=self.model_name))
+
+        # Re-align with the caller's list so an unusable crop still gets an
+        # answer in its own position rather than shifting everything after it.
+        out, it = [], iter(parsed)
+        for c in crops:
+            out.append(next(it) if (c is not None and getattr(c, "size", 0) > 0)
+                       else unavailable("empty crop"))
+        return out
+
+
+_EXTRACTOR: AttributeExtractor | None = None
+_EXTRACTOR_LOCK = threading.Lock()
+
+
+def get_extractor() -> AttributeExtractor:
+    """The one shared extractor. Constructed here, loaded on first describe."""
+    global _EXTRACTOR
+    with _EXTRACTOR_LOCK:
+        if _EXTRACTOR is None:
+            _EXTRACTOR = AttributeExtractor()
+        return _EXTRACTOR
+
+
+def describe_image_file(path) -> VehicleAttributes:
+    """Describe a crop already written to disk.
+
+    The background worker reads evidence from disk rather than holding frames:
+    a queue of decoded frames is megabytes each and would put memory pressure
+    behind a feature that is allowed to be dropped.
+    """
+    import cv2
+    img = cv2.imread(str(path))
+    if img is None:
+        return unavailable("evidence crop unreadable")
+    return get_extractor().describe(img)
+
+
+# -- self-check --------------------------------------------------------------
+
+def _self_check() -> None:
+    """Parser only: no model, no GPU, no network.
+
+    The generation half is verified separately against real grid crops; what
+    is worth pinning down here is that the caption is never over-read, because
+    an invented attribute is how a description stops being evidence.
+    """
+    rich = ("The image shows a black SUV parked on the side of a street. The "
+            "vehicle has heavily tinted windows and aftermarket alloy wheels, "
+            "with a roof rack on top. There is a large dent on the rear left "
+            "door and a sticker on the rear window that reads 'Om Travels'.")
+    a = parse_caption(rich, model="test")
+    assert a.body_type == "suv", a
+    assert a.colour == "black", a
+    assert a.tinted_windows is True, a
+    assert a.wheels == "alloy", a
+    assert a.roof_rack is True, a
+    assert "dent" in a.damage, a
+    assert "Om Travels" in a.markings, a
+    assert a.model == "test" and a.raw_caption.startswith("The image shows")
+    assert 0.0 < a.confidence <= MAX_CONFIDENCE, a.confidence
+    assert "black suv" in a.description.lower(), a.description
+
+    # A bare caption must yield unknowns, not plausible-sounding defaults.
+    bare = parse_caption("a car on a road at night")
+    assert bare.body_type == "unknown", bare
+    assert bare.colour is None and bare.tinted_windows is None, bare
+    assert bare.wheels == "unknown" and bare.roof_rack is None, bare
+    assert bare.markings == [] and bare.damage == [], bare
+    assert bare.confidence == 0.0, bare.confidence
+    # ...and with nothing structured to say, it shows the model's own words.
+    assert bare.description == "a car on a road at night", bare.description
+
+    # An empty caption is not an observation.
+    empty = parse_caption("")
+    assert empty.confidence == 0.0 and empty.description == "", empty
+    assert parse_caption(None).body_type == "unknown"
+
+    # Negation must never set the positive - this is the failure that would
+    # put the one explicitly-excluded vehicle in front of an operator.
+    for phrase, attr in (("the windows are not tinted", "tinted_windows"),
+                         ("there is no roof rack on the vehicle", "roof_rack")):
+        got = parse_caption(f"a white van; {phrase}")
+        assert getattr(got, attr) is False, (phrase, got)
+    assert parse_caption("a van with no alloy wheels").wheels == "unknown"
+    # False is still an observation and still counts as evidence.
+    assert parse_caption("a van with no roof rack").confidence > 0
+
+    # Confidence is capped even when every field is populated.
+    everything = parse_caption(
+        "a red sedan with tinted windows, alloy wheels, a roof rack, a "
+        "sticker on the boot and a deep scratch along the door")
+    assert everything.confidence <= MAX_CONFIDENCE, everything.confidence
+
+    # Longest-phrase-first: the specific body type must win over its substring.
+    assert parse_caption("a white pick-up truck").body_type == "pickup"
+    assert parse_caption("a yellow auto rickshaw").body_type == "auto_rickshaw"
+    assert parse_caption("a sport utility vehicle").body_type == "suv"
+    # Earliest mention wins: a captioner leads with its subject.
+    assert parse_caption("a black sedan parked behind a bus").body_type == "sedan"
+    # Colour synonyms fold onto the coarse palette the pipeline already uses.
+    assert parse_caption("a grey hatchback").colour == "silver"
+    assert parse_caption("a navy motorbike").colour == "blue"
+    assert parse_caption("a navy motorbike").body_type == "motorcycle"
+
+    # Every body type the dataclass documents must be reachable from the
+    # vocabulary, or the field promises a value nothing can ever produce.
+    reachable = set(_BODY_VOCAB.values())
+    assert reachable == set(BODY_TYPES), reachable ^ set(BODY_TYPES)
+
+    # Florence-2 opens a great many night captions with "a black and white
+    # photograph of...". That is the image, not the vehicle.
+    mono = parse_caption("The image is a black and white photograph of a "
+                         "street at night with a sedan on it.")
+    assert mono.colour is None, mono
+    assert mono.body_type == "sedan", mono
+    # ...but a real black vehicle in the same caption is still read.
+    both = parse_caption("a black and white photograph showing a red truck")
+    assert both.colour == "red", both
+
+    # A real Florence-2 caption from cam13, verbatim.
+    real = parse_caption(
+        "The image shows a truck parked on the side of a street. The truck is "
+        "orange and white in color and has a sign on the front that reads "
+        "'Cafe'.")
+    assert real.body_type == "truck", real
+    assert real.colour in ("yellow", "white"), real
+    assert real.markings == ["Cafe"], real
+    assert real.confidence > 0, real
+
+    # The outage path is a real answer, not an exception.
+    down = unavailable("model not installed")
+    assert down.body_type == "unknown" and down.confidence == 0.0
+    assert "unavailable" in down.description
+
+    # Serialisation, because the API and the database both round-trip this.
+    d = a.as_dict()
+    assert d["body_type"] == "suv" and isinstance(d["markings"], list), d
+
+    print("attributes self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/analytics/baseline.py b/netra/analytics/baseline.py
new file mode 100644
index 0000000..e4cb5aa
--- /dev/null
+++ b/netra/analytics/baseline.py
@@ -0,0 +1,490 @@
+"""Per-camera behavioural baselines: what is normal here, at this hour.
+
+Tens of thousands of detections are data, not information. A control room does
+not need to be told that camera 12 saw 41 vehicles; it needs to be told that 41
+is four times what that camera normally sees at 03:00, because that is what a
+blocked road, a diverted convoy or a forming crowd looks like from the outside.
+
+The model is deliberately the simplest one that can be defended in an enquiry:
+for each (camera, hour of day) the mean and standard deviation of the per-bucket
+vehicle count, and a z-score of the current reading against it. Two honesty
+constraints shape it:
+
+  * Below `MIN_SAMPLES` observations no judgement is offered at all. A "norm"
+    built from three buckets is noise, and calling a reading anomalous against
+    noise is fabrication dressed as analysis.
+  * Dispersion is floored, so a camera that has happened to see the same count
+    every time cannot generate an infinite z-score from a single extra vehicle.
+
+ponytail: a per-hour Gaussian ignores the difference between a Tuesday and a
+Sunday, and treats an hour boundary as a hard edge. Its ceiling is a camera
+whose traffic is strongly weekly rather than daily - a market street, a stadium
+approach - where a busy Saturday would read as an anomaly against a weekday
+norm. Adding day-of-week to the key is the next step, and needs roughly seven
+times the observation history before it earns its place.
+"""
+from __future__ import annotations
+
+import statistics
+from dataclasses import dataclass, field
+
+#: Observations required before any deviation judgement is offered. A hard
+#: floor, not a preference: below it the answer is "I do not know yet".
+MIN_SAMPLES = 5
+
+#: Standard deviation is floored here before any z-score is taken. A camera
+#: whose counts are identical every bucket has zero variance, and one extra
+#: vehicle against zero variance is an infinite deviation - arithmetically
+#: true, operationally absurd. One vehicle is the smallest difference the
+#: counter can even express, so it is the smallest dispersion worth believing.
+STDEV_FLOOR = 1.0
+
+#: z-score bands. Deliberately wide: an alert an operator learns to ignore is
+#: worse than no alert, and traffic counts are not normally distributed.
+Z_HIGH = 3.0
+Z_ELEVATED = 2.0
+Z_LOW = -2.0
+
+#: How old a camera's most recent bucket may be before its reading stops being
+#: reported as current. Both the anomalies endpoint and the assistant take each
+#: camera's newest bucket and judge it, which is right while the camera is
+#: reporting and quietly wrong once it stops: a feed that dropped out at
+#: midnight would otherwise be presented at nine in the morning as "no traffic
+#: counted, the road may be blocked", which is a statement about our own
+#: connection dressed up as a statement about the road. Fifteen minutes is
+#: several bucket periods, so a camera that is merely between flushes is not
+#: called stale.
+ANOMALY_MAX_BUCKET_AGE_S = 900.0
+
+
+@dataclass
+class Baseline:
+    """What one camera normally sees in one hour of the day."""
+    camera_id: str
+    hour: int
+    mean: float
+    stdev: float
+    samples: int
+
+    @property
+    def sufficient(self) -> bool:
+        return self.samples >= MIN_SAMPLES
+
+    @property
+    def effective_stdev(self) -> float:
+        return max(self.stdev, STDEV_FLOOR)
+
+    def as_dict(self) -> dict:
+        return {"camera_id": self.camera_id, "hour": self.hour,
+                "mean": round(self.mean, 2), "stdev": round(self.stdev, 2),
+                "effective_stdev": round(self.effective_stdev, 2),
+                "samples": self.samples, "sufficient": self.sufficient}
+
+
+@dataclass
+class Assessment:
+    """A reading judged against a baseline, with the reasoning attached."""
+    camera_id: str
+    hour: int
+    observed: int
+    status: str            # insufficient_data|stale|quiet|low|normal|elevated|high
+    z_score: float | None
+    explanation: str
+    baseline: Baseline | None = None
+    detail: dict = field(default_factory=dict)
+
+    @property
+    def anomalous(self) -> bool:
+        # `stale` is deliberately absent: it says the platform has nothing
+        # current to report about this camera, which is not a finding about
+        # the road and must never be counted as one.
+        return self.status in ("quiet", "low", "elevated", "high")
+
+    def as_dict(self) -> dict:
+        return {"camera_id": self.camera_id, "hour": self.hour,
+                "observed": self.observed, "status": self.status,
+                "z_score": self.z_score, "anomalous": self.anomalous,
+                "explanation": self.explanation,
+                "baseline": self.baseline.as_dict() if self.baseline else None,
+                **self.detail}
+
+
+def _field(row, name: str, default=None):
+    """Read a field from either an ORM row or a plain dict.
+
+    The learner is fed `TrafficStat` rows in the running platform and synthetic
+    dicts in the self-check, and keeping it indifferent to which means the
+    self-check needs no database.
+    """
+    if isinstance(row, dict):
+        return row.get(name, default)
+    return getattr(row, name, default)
+
+
+def _hour_of(row) -> int | None:
+    """Hour of day from `bucket_start`, in UTC throughout.
+
+    Every stored timestamp on this platform is UTC, so the baseline is learned
+    and assessed in UTC. Mixing in a local hour would silently shift a norm by
+    the offset and make the 03:00 night baseline the 08:30 rush-hour one.
+    """
+    ts = _field(row, "bucket_start")
+    if ts is None:
+        return None
+    try:
+        from datetime import timezone
+        if ts.tzinfo is not None:
+            ts = ts.astimezone(timezone.utc)
+        return ts.hour
+    except AttributeError:
+        return None
+
+
+def _bucket_age_s(row, now) -> float | None:
+    """Seconds between `now` and the row's bucket start, or None if untimed."""
+    ts = _field(row, "bucket_start")
+    if ts is None:
+        return None
+    from datetime import timezone
+    try:
+        if ts.tzinfo is None:
+            ts = ts.replace(tzinfo=timezone.utc)
+        return (now - ts).total_seconds()
+    except (AttributeError, TypeError):
+        return None
+
+
+def _is_legacy_cumulative(row, total) -> bool:
+    """True for a row written before `total` became a per-bucket figure.
+
+    Those rows carry a running count spanning every replay of the recording,
+    and a single one poisons the hour it lands in: measured on cam15 at hour
+    18, one legacy row moved the mean from 3.4 to 14.0 and the standard
+    deviation from 1.1 to 26.0, so a genuine ten-fold spike read as normal.
+    They cannot be aged out - nothing prunes traffic_stats and the learner has
+    no time window - so they are excluded here instead.
+
+    They are identifiable because the migration that added `cumulative_total`
+    defaults it to 0, while any row written since carries the real cumulative,
+    which is at least as large as the bucket's own delta. A genuine empty
+    bucket has `total = 0` and is deliberately kept: a road that is normally
+    quiet is exactly what the baseline needs to learn. A source with no
+    `cumulative_total` field at all (a synthetic dict) is trusted as given.
+    """
+    cumulative = _field(row, "cumulative_total")
+    return cumulative is not None and cumulative == 0 and total > 0
+
+
+def learn(rows) -> dict[tuple[str, int], Baseline]:
+    """Learn per-(camera, hour) norms from `TrafficStat` rows.
+
+    `total` must be the traffic *during* that bucket. A cumulative counter would
+    make the learned mean a function of how long the platform has been running
+    rather than of how busy the road is, and every judgement drawn from it
+    meaningless.
+    """
+    grouped: dict[tuple[str, int], list[float]] = {}
+    for row in rows:
+        camera_id = _field(row, "camera_id")
+        hour = _hour_of(row)
+        total = _field(row, "total")
+        if camera_id is None or hour is None or total is None:
+            continue
+        if _is_legacy_cumulative(row, total):
+            continue
+        grouped.setdefault((camera_id, int(hour)), []).append(float(total))
+
+    baselines: dict[tuple[str, int], Baseline] = {}
+    for (camera_id, hour), values in grouped.items():
+        mean = statistics.fmean(values)
+        # Sample standard deviation needs two points; one observation has no
+        # dispersion to speak of, and is below MIN_SAMPLES anyway.
+        stdev = statistics.stdev(values) if len(values) > 1 else 0.0
+        baselines[(camera_id, hour)] = Baseline(
+            camera_id=camera_id, hour=hour, mean=mean, stdev=stdev,
+            samples=len(values))
+    return baselines
+
+
+def assess(baseline: Baseline | None, observed: int) -> Assessment:
+    """Judge one reading against a baseline, or decline to."""
+    observed = int(observed)
+
+    if baseline is None:
+        return Assessment(
+            camera_id="", hour=-1, observed=observed,
+            status="insufficient_data", z_score=None,
+            explanation=("No baseline has been learned for this camera and "
+                         "hour yet, so this reading cannot be judged."))
+
+    if not baseline.sufficient:
+        return Assessment(
+            camera_id=baseline.camera_id, hour=baseline.hour, observed=observed,
+            status="insufficient_data", z_score=None, baseline=baseline,
+            explanation=(
+                f"Only {baseline.samples} observation"
+                f"{'' if baseline.samples == 1 else 's'} of {baseline.camera_id} "
+                f"at hour {baseline.hour:02d}:00 UTC; "
+                f"{MIN_SAMPLES} are required before this platform will call a "
+                f"reading normal or abnormal. Observed {observed}."))
+
+    z = (observed - baseline.mean) / baseline.effective_stdev
+    z = round(z, 2)
+    norm = (f"the norm for {baseline.camera_id} at hour {baseline.hour:02d}:00 "
+            f"UTC is {baseline.mean:.1f} "
+            f"(sd {baseline.effective_stdev:.1f}, {baseline.samples} samples)")
+
+    # `quiet` is a more strongly worded `low`, never an override of the bands.
+    # Gating it on the z-score as well as on zero matters because a genuinely
+    # quiet road counts zero routinely: a camera whose history is
+    # (0, 2, 1, 0, 3, 1, 0, 2) has a mean of 1.1, and reporting its next empty
+    # minute as a possible blockage would assert an abnormality on evidence
+    # that says the reading is ordinary - and would do so every few minutes.
+    if observed == 0 and z <= Z_LOW:
+        status = "quiet"
+        text = (f"No traffic counted, {z:+.1f} standard deviations below "
+                f"normal, where {norm}. A road that normally carries vehicles "
+                f"and now carries none may be blocked, closed, or the camera's "
+                f"view obstructed.")
+    elif z >= Z_HIGH:
+        status = "high"
+        text = (f"{observed} vehicles, {z:+.1f} standard deviations above "
+                f"normal: {norm}.")
+    elif z >= Z_ELEVATED:
+        status = "elevated"
+        text = (f"{observed} vehicles, {z:+.1f} standard deviations above "
+                f"normal: {norm}.")
+    elif z <= Z_LOW:
+        status = "low"
+        text = (f"{observed} vehicles, {z:+.1f} standard deviations below "
+                f"normal: {norm}.")
+    else:
+        status = "normal"
+        text = (f"{observed} vehicles is within the usual range: {norm}.")
+
+    return Assessment(camera_id=baseline.camera_id, hour=baseline.hour,
+                      observed=observed, status=status, z_score=z,
+                      explanation=text, baseline=baseline)
+
+
+def detect_anomalies(baselines: dict[tuple[str, int], Baseline],
+                     current_stats, include_normal: bool = False,
+                     now=None) -> list[Assessment]:
+    """Assess a set of current readings, most deviant first.
+
+    `current_stats` entries carry `camera_id`, `total`, and either an `hour` or
+    a `bucket_start` from which the UTC hour is taken.
+
+    A reading older than ANOMALY_MAX_BUCKET_AGE_S is reported as `stale`, with
+    the bucket's own timestamp in the explanation, rather than judged. Callers
+    pass the newest bucket per camera, which stops being a current reading the
+    moment the camera stops reporting, and the difference between "this road is
+    empty" and "we have not heard from this camera since midnight" is the whole
+    difference between a finding and a fault.
+    """
+    from datetime import datetime, timezone
+    if now is None:
+        now = datetime.now(timezone.utc)
+    out: list[Assessment] = []
+    for row in current_stats:
+        camera_id = _field(row, "camera_id")
+        if camera_id is None:
+            continue
+        hour = _field(row, "hour")
+        if hour is None:
+            hour = _hour_of(row)
+        if hour is None:
+            continue
+        hour = int(hour)
+        observed = int(_field(row, "total") or 0)
+
+        age = _bucket_age_s(row, now)
+        if age is not None and age > ANOMALY_MAX_BUCKET_AGE_S:
+            ts = _field(row, "bucket_start")
+            out.append(Assessment(
+                camera_id=camera_id, hour=hour, observed=observed,
+                status="stale", z_score=None,
+                baseline=baselines.get((camera_id, hour)),
+                explanation=(
+                    f"{camera_id} has not reported since "
+                    f"{getattr(ts, 'isoformat', lambda: ts)()} "
+                    f"({age / 60.0:.0f} minutes ago). Its last bucket counted "
+                    f"{observed}, but that is not a current reading and is not "
+                    f"judged against the norm."),
+                detail={"bucket_age_s": round(age, 1)}))
+            continue
+
+        result = assess(baselines.get((camera_id, hour)), observed)
+        # `assess` cannot know the camera when there is no baseline at all.
+        result.camera_id = camera_id
+        result.hour = hour
+        if include_normal or result.status != "normal":
+            out.append(result)
+
+    # Insufficient-data entries sort last: they are information about the
+    # platform's own coverage, not about the road.
+    out.sort(key=lambda a: (a.z_score is None, -abs(a.z_score or 0.0)))
+    return out
+
+
+def _self_check() -> None:
+    """A baseline that flags the wrong thing costs an operator's trust, and one
+    that flags nothing is decoration, so both directions are pinned here. All
+    rows are synthetic: no database, no network."""
+    from datetime import datetime, timedelta, timezone
+
+    def row(cam, hour, total):
+        return {"camera_id": cam, "total": total,
+                "bucket_start": datetime(2026, 9, 1, hour, 0,
+                                         tzinfo=timezone.utc)}
+
+    # A busy camera with a settled norm, plus a thin one with three samples.
+    rows = ([row("cam01", 9, n) for n in (40, 44, 38, 42, 46, 41)] +
+            [row("cam02", 9, n) for n in (10, 12, 11)] +
+            [row("cam01", 3, n) for n in (2, 3, 1, 2, 4, 2)])
+    b = learn(rows)
+
+    assert b[("cam01", 9)].samples == 6
+    assert 40 < b[("cam01", 9)].mean < 43, b[("cam01", 9)].mean
+    assert b[("cam02", 9)].samples == 3
+
+    # Hours are keyed separately: the night norm must not absorb the day norm.
+    assert b[("cam01", 3)].mean < 5, b[("cam01", 3)].mean
+
+    # Below MIN_SAMPLES no verdict is offered, however extreme the reading.
+    a = assess(b[("cam02", 9)], 500)
+    assert a.status == "insufficient_data", a
+    assert a.z_score is None and not a.anomalous, a
+    assert "5 are required" in a.explanation, a.explanation
+
+    # No baseline at all behaves the same way.
+    assert assess(None, 99).status == "insufficient_data"
+
+    # A normal reading is not flagged.
+    assert assess(b[("cam01", 9)], 42).status == "normal"
+
+    # A clear spike is flagged.
+    spike = assess(b[("cam01", 9)], 200)
+    assert spike.status == "high", spike
+    assert spike.z_score > Z_HIGH and spike.anomalous
+
+    moderate = assess(b[("cam01", 9)], 49)
+    assert moderate.status in ("elevated", "high"), moderate
+
+    # Zero traffic against a busy baseline is quiet, not merely low.
+    dead = assess(b[("cam01", 9)], 0)
+    assert dead.status == "quiet", dead
+    assert "blocked" in dead.explanation
+
+    # A genuinely low but non-zero reading is low.
+    assert assess(b[("cam01", 9)], 30).status == "low"
+
+    # ...but a road that is *normally* quiet must not have its ordinary empty
+    # minute reported as a blockage. Three of these eight samples are
+    # themselves zero, so an observed zero sits inside the normal band and the
+    # `quiet` wording must not override that. An alert here would fire on most
+    # quiet cameras every few minutes and cost an officer's attention each time.
+    quiet_road = learn([row("cam05", 2, n)
+                        for n in (0, 2, 1, 0, 3, 1, 0, 2)])[("cam05", 2)]
+    calm = assess(quiet_road, 0)
+    assert calm.status == "normal", calm
+    assert not calm.anomalous, calm
+    assert abs(calm.z_score) <= abs(Z_LOW), calm
+    # The same camera stopping dead is still not abnormal; a genuine surge is.
+    assert assess(quiet_road, 10).status in ("elevated", "high")
+
+    # Rows written before `total` became a per-bucket figure carry a cumulative
+    # count and must never be learned from: one alone inflates the mean and
+    # standard deviation enough to hide a ten-fold spike.
+    legacy = {"camera_id": "cam06", "total": 67, "cumulative_total": 0,
+              "bucket_start": datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)}
+    genuine_empty = {"camera_id": "cam06", "total": 0, "cumulative_total": 0,
+                     "bucket_start": datetime(2026, 9, 1, 18, 0,
+                                              tzinfo=timezone.utc)}
+    modern = [{"camera_id": "cam06", "total": t, "cumulative_total": 100 + t,
+               "bucket_start": datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)}
+              for t in (3, 4, 2, 5, 3)]
+
+    # The legacy row is excluded...
+    with_legacy = learn(modern + [legacy])[("cam06", 18)]
+    assert with_legacy.samples == 5, with_legacy
+    assert with_legacy.mean < 4, with_legacy
+    # ...and the spike it would otherwise have hidden is still seen.
+    assert assess(with_legacy, 30).status == "high"
+
+    # ...while a genuine empty bucket, which looks similar, is kept: a quiet
+    # road is exactly what the baseline needs to learn.
+    with_empty = learn(modern + [genuine_empty])[("cam06", 18)]
+    assert with_empty.samples == 6, with_empty
+    assert with_empty.mean < with_legacy.mean, with_empty
+
+    # A source that carries no cumulative_total at all is trusted as given.
+    assert learn([{"camera_id": "cam07", "total": 9,
+                   "bucket_start": datetime(2026, 9, 1, 18, 0,
+                                            tzinfo=timezone.utc)}])
+
+    # Zero variance must not produce an infinite or absurd z-score.
+    flat = learn([row("cam03", 5, 20) for _ in range(8)])[("cam03", 5)]
+    assert flat.stdev == 0.0 and flat.effective_stdev == STDEV_FLOOR
+    one_more = assess(flat, 21)
+    assert one_more.z_score == 1.0, one_more
+    assert one_more.status == "normal", one_more
+    far = assess(flat, 25)
+    assert far.z_score == 5.0 and far.status == "high", far
+
+    # A quiet night camera is not swamped by the floor either: 2 vehicles
+    # against a norm of ~2 stays normal.
+    assert assess(b[("cam01", 3)], 2).status == "normal"
+
+    # detect_anomalies ranks the most deviant first and suppresses the normal.
+    found = detect_anomalies(b, [
+        {"camera_id": "cam01", "hour": 9, "total": 42},    # normal, dropped
+        {"camera_id": "cam01", "hour": 3, "total": 30},    # wild spike
+        {"camera_id": "cam02", "hour": 9, "total": 500},   # no verdict
+        {"camera_id": "cam01", "hour": 9, "total": 55},    # elevated
+    ])
+    assert [f.status for f in found] == ["high", "high", "insufficient_data"], \
+        [(f.camera_id, f.hour, f.status, f.z_score) for f in found]
+    assert found[0].camera_id == "cam01" and found[0].hour == 3, found[0]
+    assert found[-1].status == "insufficient_data", found[-1]
+    assert all(f.camera_id for f in found)
+
+    # An unknown camera is declined, not guessed at.
+    unknown = detect_anomalies(b, [{"camera_id": "cam99", "hour": 9, "total": 900}])
+    assert unknown[0].status == "insufficient_data"
+    assert unknown[0].camera_id == "cam99"
+
+    # bucket_start is accepted in place of an explicit hour. `now` is pinned
+    # to the synthetic clock: without it these rows would all be years old and
+    # correctly reported as stale.
+    fresh_now = datetime(2026, 9, 1, 9, 1, tzinfo=timezone.utc)
+    via_ts = detect_anomalies(b, [row("cam01", 9, 200)], now=fresh_now)
+    assert via_ts[0].status == "high", via_ts
+
+    # A camera that stopped reporting hours ago is not a current reading. Its
+    # last bucket counted zero, which against a busy norm would otherwise be
+    # published as "the road may be blocked" - a claim about our own connection
+    # dressed as a claim about the road.
+    stale_now = fresh_now + timedelta(seconds=ANOMALY_MAX_BUCKET_AGE_S + 60)
+    stale = detect_anomalies(b, [row("cam01", 9, 0)], now=stale_now)
+    assert stale[0].status == "stale", stale
+    assert not stale[0].anomalous, stale[0]
+    assert "2026-09-01T09:00:00" in stale[0].explanation, stale[0].explanation
+    assert stale[0].as_dict()["bucket_age_s"] > ANOMALY_MAX_BUCKET_AGE_S
+
+    # One second inside the window is still current, and still judged.
+    edge_now = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc) + timedelta(
+        seconds=ANOMALY_MAX_BUCKET_AGE_S - 1)
+    edge = detect_anomalies(b, [row("cam01", 9, 0)], now=edge_now)
+    assert edge[0].status == "quiet", edge
+
+    # Naive timestamps are tolerated and read as UTC.
+    naive = learn([{"camera_id": "cam04", "total": 7,
+                    "bucket_start": datetime(2026, 9, 1, 14, 0)}])
+    assert ("cam04", 14) in naive
+
+    print("baseline self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/analytics/cloned_plate.py b/netra/analytics/cloned_plate.py
new file mode 100644
index 0000000..09f4fc5
--- /dev/null
+++ b/netra/analytics/cloned_plate.py
@@ -0,0 +1,354 @@
+"""Cloned- and forged-plate detection.
+
+The same registration number appearing in two places too far apart for one
+vehicle to have travelled is not a tracking failure - it is evidence that two
+vehicles are wearing the same plate. Plate cloning is a named offence and the
+detection falls straight out of the space-time feasibility check already used
+to veto impossible hops in `route.py`; here the same arithmetic is inverted and
+reported as a finding in its own right.
+
+Three constraints keep the finding honest:
+
+  * Only sightings within one recording session are ever compared. The Sentinel
+    sandbox holds several independently recorded feeds, so a timestamp from one
+    session says nothing about a timestamp from another. Comparing across them
+    would manufacture an accusation out of a clock offset - the single most
+    dangerous failure mode here.
+  * Two sightings on the same camera are never a clone. That is one vehicle
+    seen twice, whatever the interval.
+  * Confidence is capped below certainty. Every finding rests on OCR output
+    from wide-area night cameras, and OCR misreads plates into each other. The
+    `reason` field carries the arithmetic so an officer can check the claim
+    rather than take it.
+"""
+from __future__ import annotations
+
+from dataclasses import dataclass, asdict
+from datetime import datetime
+
+from netra.analytics.matching import (MAX_PLAUSIBLE_KMH, normalise_plate,
+                                      spacetime_plausible)
+from netra.core.geo import haversine_km, time_group
+from netra.core.timing import scene_time as _scene_time
+from netra.core.timing import sighting_time
+
+# Plate confidence assumed when a detection carries none. Deliberately middling:
+# an unscored read must neither inflate nor destroy a finding.
+DEFAULT_PLATE_CONF = 0.5
+
+# A finding can never be certain - see the module docstring.
+MAX_CONFIDENCE = 0.99
+
+# Violation credited when the two sightings carry the same timestamp. Scene time
+# is OCR of an overlay clock at second resolution, so a genuine sub-second gap is
+# routinely stamped as 0s and no speed can be computed at all. Held below the
+# strongest measured violations deliberately: a finding whose arithmetic cannot
+# be shown must not outrank one whose arithmetic can.
+ZERO_ELAPSED_VIOLATION = 0.9
+
+
+@dataclass
+class CloneFinding:
+    plate: str
+    sighting_a: dict
+    sighting_b: dict
+    distance_km: float
+    elapsed_s: float
+    implied_kmh: float | None
+    confidence: float
+    reason: str
+
+    def to_dict(self) -> dict:
+        return asdict(self)
+
+
+def _sighting_dict(det) -> dict:
+    cam = getattr(det, "camera", None)
+    at = sighting_time(det)
+    return {
+        "detection_id": det.id,
+        "camera_id": det.camera_id,
+        "camera_name": cam.name if cam else det.camera_id,
+        "lat": cam.lat if cam else None,
+        "lon": cam.lon if cam else None,
+        "at": at.isoformat() if isinstance(at, datetime) else at,
+    }
+
+
+def _confidence(implied_kmh: float | None, conf_a: float, conf_b: float) -> float:
+    """How strongly this pair argues that two vehicles share one plate.
+
+    Two independent factors:
+
+      * How badly the pair violates plausibility, where it can be measured. A pair implying 200 km/h is
+        weak - a motorway run, a slightly wrong timestamp or an approximate
+        camera coordinate could all produce it. One implying 5,000 km/h has no
+        innocent explanation. Scored as 1 - (limit / implied), which approaches
+        1 as the implied speed runs away and is near 0 just over the limit.
+      * How well the plate was read at both ends. The weaker read governs: a
+        confident read paired with a guess is still a guess.
+    """
+    if implied_kmh is None:
+        # Sightings stamped at the same second: the gap is below the clock's
+        # resolution, so the implied speed is unbounded but unmeasured.
+        violation = ZERO_ELAPSED_VIOLATION
+    elif implied_kmh <= MAX_PLAUSIBLE_KMH:
+        violation = 0.0
+    else:
+        violation = 1.0 - (MAX_PLAUSIBLE_KMH / implied_kmh)
+
+    weakest = min(conf_a, conf_b)
+    # Plate quality can halve the score but never zero it: even a poor read of
+    # the same string in two impossible places is worth an officer's attention.
+    return round(min(MAX_CONFIDENCE, violation * (0.5 + 0.5 * weakest)), 3)
+
+
+def find_clones(detections: list, min_confidence: float = 0.6) -> list[CloneFinding]:
+    """Report registration numbers seen in physically incompatible places.
+
+    `detections` are ORM Detection rows with `.camera` loaded.
+
+    ponytail: within each session, consecutive pairs only after ordering by
+    time. Every comparable adjacent pair is examined, so a clone active across
+    three cameras is reported as its two adjacent impossible hops rather than
+    as one multi-camera cluster. What this does not do is compare
+    non-consecutive sightings: a pair that is impossible but has a plausible
+    sighting between them is not reported, since the intervening hop is the
+    stronger explanation and reporting the outer pair would double-count it.
+    """
+    # Partition by (plate, recording session) before ordering. Sightings from
+    # different sessions are not comparable, so they must not merely be skipped
+    # when they fall adjacent - a cross-session row sorting between two
+    # same-session sightings would otherwise break the chain and hide a real
+    # clone, and the two sandbox sessions have overlapping wall clocks, so that
+    # interleaving is expected rather than hypothetical. Cameras in no known
+    # session are dropped entirely: we cannot show their clock agrees with
+    # anything, including another unlisted camera's.
+    groups: dict[tuple[str, str], list] = {}
+    for det in detections:
+        plate = normalise_plate(det.plate_text)
+        # A partial read cannot identify a vehicle, so it cannot evidence a
+        # clone either: "AB12" is shared by thousands of legitimate plates.
+        if len(plate) < 6:
+            continue
+        if _scene_time(det) is None:
+            # A clone finding is entirely a claim about elapsed time between
+            # two cameras. Wall time is our connection time, and an
+            # uncorroborated overlay reading is a guess that has been two years
+            # out on this grid - either would manufacture impossible speeds
+            # out of nothing. No clock, no claim.
+            continue
+        group = time_group(det.camera_id)
+        if group is None:
+            continue
+        groups.setdefault((plate, group), []).append(det)
+
+    findings: list[CloneFinding] = []
+    for (plate, group), dets in groups.items():
+        if len(dets) < 2:
+            continue
+        dets.sort(key=sighting_time)
+
+        for prev, cur in zip(dets, dets[1:]):
+            # One vehicle passing the same camera twice is not a clone.
+            if prev.camera_id == cur.camera_id:
+                continue
+
+            cam_a, cam_b = getattr(prev, "camera", None), getattr(cur, "camera", None)
+            coords = (getattr(cam_a, "lat", None), getattr(cam_a, "lon", None),
+                      getattr(cam_b, "lat", None), getattr(cam_b, "lon", None))
+            if None in coords:
+                # Without both positions there is no distance and therefore no
+                # impossibility to assert.
+                continue
+
+            km = haversine_km(*coords)
+            secs = (sighting_time(cur) - sighting_time(prev)).total_seconds()
+            ok, why = spacetime_plausible(km, secs)
+            if ok:
+                continue
+            if km <= 0.0:
+                # Co-located cameras: no distance was covered, so no speed is
+                # implied however close together the sightings fall.
+                continue
+
+            # Report the plate as OCR actually read it, not the confusion-folded
+            # key: an officer shown "6J01A81234" would reasonably think the
+            # system had flagged a different vehicle entirely.
+            read_a = (prev.plate_text or plate).upper()
+            read_b = (cur.plate_text or plate).upper()
+            shown = read_a if read_a == read_b else f"{read_a} / {read_b}"
+
+            implied = km / (secs / 3600.0) if secs > 0 else None
+            conf = _confidence(implied,
+                               prev.plate_conf if prev.plate_conf is not None else DEFAULT_PLATE_CONF,
+                               cur.plate_conf if cur.plate_conf is not None else DEFAULT_PLATE_CONF)
+            if conf < min_confidence:
+                continue
+
+            a, b = _sighting_dict(prev), _sighting_dict(cur)
+            if implied is None:
+                arithmetic = (f"{shown} was recorded at {a['camera_name']} and "
+                              f"{b['camera_name']}, {km:.1f} km apart, both "
+                              f"stamped at the same second - the gap is below "
+                              f"the overlay clock's resolution, so the implied "
+                              f"speed could not be computed, only bounded below "
+                              f"by {km * 3600:.0f} km/h")
+            else:
+                arithmetic = (f"{shown} was recorded at {a['camera_name']} and "
+                              f"{b['camera_name']}, {km:.1f} km apart, "
+                              f"{secs:.0f}s apart - implying {implied:.0f} km/h "
+                              f"against a {MAX_PLAUSIBLE_KMH:.0f} km/h ceiling")
+            reason = (f"{arithmetic}. Both cameras share the {group} recording "
+                      f"session, so the timestamps are comparable. One vehicle "
+                      f"cannot have made this journey, so the plate is likely "
+                      f"cloned or forged. Plate reads scored "
+                      f"{prev.plate_conf if prev.plate_conf is not None else 'unscored'} "
+                      f"and {cur.plate_conf if cur.plate_conf is not None else 'unscored'}; "
+                      f"verify against the evidence images before acting.")
+            if read_a != read_b:
+                reason += (f" The two reads differ by characters OCR is known to "
+                           f"confuse and were treated as the same plate.")
+
+            findings.append(CloneFinding(
+                plate=shown, sighting_a=a, sighting_b=b,
+                distance_km=round(km, 2), elapsed_s=round(secs, 1),
+                implied_kmh=round(implied, 1) if implied is not None else None,
+                confidence=conf, reason=reason))
+
+    findings.sort(key=lambda f: -f.confidence)
+    return findings
+
+
+def _self_check() -> None:
+    """A clone finding is an accusation, so every guard here protects someone."""
+    from datetime import timedelta, timezone
+
+    class FakeCam:
+        def __init__(self, cid, name, lat, lon):
+            self.id, self.name, self.lat, self.lon = cid, name, lat, lon
+
+    class FakeDet:
+        _next = [1]
+
+        def __init__(self, cam, plate, at, conf=0.9):
+            self.camera, self.camera_id = cam, cam.id
+            self.plate_text, self.plate_conf = plate, conf
+            self.evidence_path = None
+            self.scene_time, self.wall_time = at, at
+            self.scene_time_corroborated = True
+            self.id = FakeDet._next[0]
+            FakeDet._next[0] += 1
+
+    t0 = datetime(2026, 6, 13, 23, 20, tzinfo=timezone.utc)
+    c04 = FakeCam("cam04", "Paldi Circle", 23.0130, 72.5620)
+    c14 = FakeCam("cam14", "Delight RLVD", 23.0290, 72.5700)
+    c15 = FakeCam("cam15", "Vasna", 23.0180, 72.5300)
+    c10 = FakeCam("cam10", "Char Chowk", 21.5220, 70.4570)   # other session
+    c99 = FakeCam("cam99", "Unlisted", 23.0000, 72.5000)     # no time group
+
+    # Impossible pair: ~1.9 km in two seconds is flagged.
+    out = find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                       FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=2))])
+    assert len(out) == 1, out
+    assert out[0].plate == "GJ01AB1234" and out[0].confidence <= MAX_CONFIDENCE, out[0]
+    assert "km/h" in out[0].reason and "2.0 km" in out[0].reason, out[0].reason
+
+    # A plausible pair is not a clone.
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                        FakeDet(c14, "GJ01AB1234", t0 + timedelta(minutes=3))]) == []
+
+    # Same camera seconds apart: one vehicle seen twice, never a clone.
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                        FakeDet(c04, "GJ01AB1234", t0 + timedelta(seconds=1))]) == []
+
+    # Different recording sessions must never be compared, however impossible
+    # the arithmetic would look. This is the constraint that stops the platform
+    # accusing an innocent vehicle on the strength of a clock offset.
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                        FakeDet(c10, "GJ01AB1234", t0 + timedelta(seconds=5))]) == []
+    # A camera in no known session is equally incomparable.
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                        FakeDet(c99, "GJ01AB1234", t0 + timedelta(seconds=5))]) == []
+
+    # A single sighting yields nothing.
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0)]) == []
+
+    # Confidence ordering: the worse violation must score higher.
+    mild = find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                        FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=40))],
+                       min_confidence=0.0)
+    severe = find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                          FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=1))],
+                         min_confidence=0.0)
+    assert mild and severe, (mild, severe)
+    assert severe[0].confidence > mild[0].confidence, (severe[0], mild[0])
+    # ...and the mild one is weak enough that the default threshold hides it.
+    assert mild[0].confidence < 0.6, mild[0]
+
+    # A weaker plate read must not score as highly as a confident one.
+    weak = find_clones([FakeDet(c04, "GJ01AB1234", t0, conf=0.3),
+                        FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=1), conf=0.3)],
+                       min_confidence=0.0)
+    assert weak[0].confidence < severe[0].confidence, (weak[0], severe[0])
+
+    # Missing coordinates must not crash and must not produce a finding.
+    blind = FakeCam("cam15", "Vasna", None, None)
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                        FakeDet(blind, "GJ01AB1234", t0 + timedelta(seconds=2))]) == []
+
+    # Partial reads cannot identify a vehicle and must not accuse one.
+    assert find_clones([FakeDet(c04, "AB12", t0),
+                        FakeDet(c14, "AB12", t0 + timedelta(seconds=2))]) == []
+
+    # Three cameras, two impossible hops: both are reported.
+    chain = find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                         FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=2)),
+                         FakeDet(c15, "GJ01AB1234", t0 + timedelta(seconds=4))])
+    assert len(chain) == 2, chain
+    assert chain[0].confidence >= chain[1].confidence, chain
+
+    # A cross-session sighting sorting between two same-session ones must not
+    # break the chain: partitioning happens before ordering, so the real
+    # cam04 -> cam14 clone is still found with cam10 interleaved.
+    interleaved = find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                               FakeDet(c10, "GJ01AB1234", t0 + timedelta(seconds=1)),
+                               FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=2))],
+                              min_confidence=0.0)
+    assert len(interleaved) == 1, interleaved
+    assert {interleaved[0].sighting_a["camera_id"],
+            interleaved[0].sighting_b["camera_id"]} == {"cam04", "cam14"}, interleaved[0]
+
+    # An unmeasurable gap must not outrank a flagrant measured violation. Scene
+    # time is second-resolution OCR, so a same-second pair is the least
+    # determinate finding available, not the strongest.
+    same_second = find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                               FakeDet(c14, "GJ01AB1234", t0)], min_confidence=0.0)
+    assert len(same_second) == 1, same_second
+    assert same_second[0].implied_kmh is None, same_second[0]
+    assert "resolution" in same_second[0].reason, same_second[0].reason
+    assert same_second[0].confidence < severe[0].confidence, (same_second[0], severe[0])
+
+    # Two reads that differ only by a known OCR confusion are the same plate,
+    # but the finding must show both as read rather than the folded key.
+    folded = find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                          FakeDet(c14, "GJ0IAB1234", t0 + timedelta(seconds=2))])
+    assert len(folded) == 1 and "6J01A81234" not in folded[0].plate, folded
+    assert "GJ0IAB1234" in folded[0].plate, folded[0].plate
+
+    # Distinct plates are never cross-compared.
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                        FakeDet(c14, "GJ09ZZ8888", t0 + timedelta(seconds=2))]) == []
+
+    # An uncorroborated scene time cannot evidence a clone. Its only other
+    # timestamp is our connection time, which would put every sighting on a
+    # replayed loop within seconds of every other and flag the whole grid.
+    unclocked = FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=2))
+    unclocked.scene_time_corroborated = False
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0), unclocked]) == []
+
+    print("cloned_plate self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/analytics/inference.py b/netra/analytics/inference.py
index 38e81ea..47a494f 100644
--- a/netra/analytics/inference.py
+++ b/netra/analytics/inference.py
@@ -29,12 +29,59 @@ from netra import config
 log = logging.getLogger(__name__)
 
 #: How many frames to spend looking for a timestamp overlay before accepting
 #: that a camera has none. Roughly half the grid has no legible overlay.
 CLOCK_ATTEMPT_LIMIT = 4
 
+#: Stream seconds after which an anchor is re-read from the overlay. One
+#: reading extrapolated indefinitely drifts with the decoder's timing, so
+#: timestamps on a long-lived connection grow silently wrong. Fifteen minutes
+#: is far more often than drift becomes material, and far rarer than the OCR
+#: cost would justify doing it any more eagerly.
+CLOCK_REANCHOR_AFTER_S = 900.0
+
+#: Anchoring budget for an offline exhaustive pass over a finite recording.
+#: Far larger than the live limit because the two situations are opposites: a
+#: live camera competes with detection for the same second, an indexing pass
+#: competes with nothing and exists precisely to get this right. Attempts are
+#: spaced INDEX_CLOCK_RETRY_MS apart in *stream* time so the budget is spent
+#: across the recording rather than burnt on its first ten seconds - an overlay
+#: obscured by a passing lorry at the join may be perfectly legible a minute in.
+INDEX_CLOCK_ATTEMPT_LIMIT = 30
+INDEX_CLOCK_RETRY_MS = 20000.0
+
+#: Spacing between attempts while a first reading is waiting to be
+#: corroborated. Measured on cam13, only about one attempt in seven produces a
+#: legible overlay, so waiting a further twenty seconds of stream time for the
+#: confirming read means the pair almost never completes and the camera stays
+#: unanchored despite a readable clock. Close the gap while a candidate is
+#: pending: the overlay that was legible a moment ago probably still is.
+#: Not zero, though - a second reading of near-identical pixels tests very
+#: little. A second of stream time changes the seconds digit, so an agreeing
+#: pair has read a *different* number correctly twice. What it still cannot
+#: catch is a systematic misread that produces the same wrong digit every
+#: time; only a differently-derived clock could.
+INDEX_CLOCK_CORROBORATE_RETRY_MS = 1000.0
+
+#: How far a second overlay reading may fall from the first one projected
+#: forward by PTS and still corroborate it. Overlays read to the second and
+#: PTS is milliseconds, so a genuine pair agrees to within rounding; anything
+#: wider is a different reading of a different number.
+CLOCK_CORROBORATION_TOLERANCE_S = 2.5
+
+#: How the engine spends its scene-clock budget.
+#:   opportunistic  live: skip the read whenever frames are backing up, because
+#:                  detection is the primary duty. Measured: without this,
+#:                  83% of frames dropped.
+#:   exhaustive     offline indexing: never skip. An indexing pass feeds frames
+#:                  blocking, so the queue is always full and the opportunistic
+#:                  rule would skip every single attempt - which is exactly
+#:                  what it did, anchoring 0% of 27,000 indexed detections.
+CLOCK_OPPORTUNISTIC = "opportunistic"
+CLOCK_EXHAUSTIVE = "exhaustive"
+
 #: Minimum crop height worth embedding. Below this an appearance vector cannot
 #: distinguish one vehicle from another, so it is cost without information.
 REID_MIN_CROP_PX = 64
 #: Cap on embeddings per frame. Busy junction cameras routinely show 20+
 #: vehicles; embedding every one saturates the queue and starves detection,
 #: which matters more. Largest vehicles are embedded first.
@@ -46,12 +93,63 @@ REID_MAX_PER_FRAME = 8
 #: on any vehicle at all - see docs/feed-recon-findings.md.
 PLATE_MIN_VEHICLE_PX = 110
 #: Cap on plate reads per frame. OCR is the most expensive operation in the
 #: pipeline at roughly 50ms per vehicle.
 PLATE_MAX_PER_FRAME = 4
 
+#: Mean luma below which a frame carries no scene at all. The registry
+#: classifies dead cameras at onboarding, but a camera can go dark afterwards -
+#: nightfall, a failed IR illuminator, a lens cover - and a black feed still
+#: costs a full YOLO pass per frame, forever, on a GPU that is the scarcest
+#: resource here. Measured across this grid, usable night frames sit well above
+#: 18 while dead feeds sit near zero.
+DARK_LUMA_THRESHOLD = 18
+#: Consecutive dark frames with nothing detected before a camera is skipped.
+#: Sixty at tier-1 rate is about a minute, which is long enough that a lorry
+#: parked against the lens or a passing cloud cannot trip it.
+DARK_FRAME_LIMIT = 60
+#: A dark camera is re-tested every this many frames so it recovers on its own
+#: at dawn. At tier-1 rate that is a probe roughly every five minutes: cheap
+#: against the ~99.7% of inference passes it saves, and quick enough that no
+#: real traffic is missed for long.
+DARK_RECHECK_FRAMES = 300
+#: Stride used to downscale a frame before measuring luma. Sampling every 8th
+#: pixel is a numpy view rather than a copy, so the measurement costs
+#: microseconds - it must not become a cost of its own.
+LUMA_SAMPLE_STRIDE = 8
+
+#: Half-precision detection, on the GPU only - CPU fp16 is emulated and
+#: slower. Measured on this machine at TIER2_IMGSZ: 17.3 ms/pass at fp32
+#: against 12.1 ms at fp16. Detection is not the bottleneck here - OCR, writes
+#: and escalation are - so this is not where the demo is won; it is taken
+#: because it is free. Deliberately not INT8 and not TensorRT: INT8 would need
+#: a calibration set and a re-validation of every threshold, and TensorRT on
+#: sm_120 is a day of risk for a number that does not move the demo.
+#: Spelled as `quantize` rather than the older `half=True`, which this
+#: ultralytics forwards to exactly this with a deprecation warning.
+_PRECISION: dict = {"quantize": 16} if config.DEVICE == "cuda" else {}
+
+
+def mean_luma(image) -> float:
+    """Mean brightness of a frame, measured on a strided sample.
+
+    BT.601 weights on BGR. Deliberately not cv2.cvtColor over the full frame:
+    that allocates a greyscale copy of every frame on every camera, which is
+    real cost to answer a question about darkness.
+    """
+    if image is None or getattr(image, "size", 0) == 0:
+        return 0.0
+    sample = image[::LUMA_SAMPLE_STRIDE, ::LUMA_SAMPLE_STRIDE]
+    if sample.size == 0:
+        return 0.0
+    if sample.ndim == 3 and sample.shape[2] >= 3:
+        b, g, r = (sample[:, :, 0].mean(), sample[:, :, 1].mean(),
+                   sample[:, :, 2].mean())
+        return float(0.114 * b + 0.587 * g + 0.299 * r)
+    return float(sample.mean())
+
 
 @dataclass
 class VehicleDetection:
     camera_id: str
     pts_ms: float
     wall_time: float
@@ -62,12 +160,22 @@ class VehicleDetection:
     plate_text: str | None = None
     plate_conf: float | None = None
     plate_chars: int | None = None
     plate_bbox: list[int] | None = None
     #: real time the scene occurred, from the camera's burned-in overlay
     scene_time: object | None = None
+    #: whether the anchor that produced `scene_time` was corroborated by a
+    #: second, independent overlay reading. False means the value is a guess
+    #: and must be treated as absent by anything reasoning over elapsed time -
+    #: this grid has produced streams dated 2028 from a single misread digit.
+    scene_time_corroborated: bool = False
+    #: how many per-frame OCR reads voted for `plate_text`. One is a guess;
+    #: persisting the count is what lets an operator tell the two apart.
+    plate_votes: int | None = None
+    #: assigned by the per-camera tracker; identifies one vehicle journey
+    track_id: int | None = None
     embedding: list | None = field(default=None, repr=False)
     evidence: object | None = field(default=None, repr=False)
 
 
 # Coarse colour vocabulary. Street lighting makes anything finer dishonest.
 _COLOUR_REFS = {
@@ -124,16 +232,51 @@ class InferenceEngine:
         self._ocr = None
         self._reid = None
         #: camera_id -> ClockAnchor, tying each stream to real scene time
         self._clocks: dict = {}
         #: how many overlay reads have been attempted per camera
         self._clock_attempts: dict = {}
+        #: stream time of the last overlay attempt per camera. The exhaustive
+        #: policy uses it to space its attempts across the recording; both
+        #: policies use it to decide when an exhausted attempt budget has been
+        #: quiet long enough to be granted afresh.
+        self._clock_last_try: dict = {}
+        #: a first overlay reading, held unanchored until a second one
+        #: corroborates it. See _anchor_clock.
+        self._clock_pending: dict = {}
+        #: live behaviour is the default and is unchanged; only an offline
+        #: indexing pass sets this to CLOCK_EXHAUSTIVE
+        self.clock_policy: str = CLOCK_OPPORTUNISTIC
+        #: per-camera trackers; tracking is what counting, direction, dwell
+        #: and zone rules are all built on
+        from netra.analytics.tracking import TrackerRegistry
+        self.trackers = TrackerRegistry()
+        #: camera_id -> PlateVoter; plate reads from one tracked vehicle vote
+        #: together, because a single frame's read is a guess
+        self._plate_voters: dict = {}
+        #: camera_id -> consecutive dark, empty frames seen
+        self._dark_streak: dict = {}
+        #: camera_id -> when it was marked dark; presence means "skip"
+        self._dark_cameras: dict = {}
+        #: camera_id -> frames skipped since the last probe
+        self._dark_skipped: dict = {}
+        #: set by the pipeline so zone rules can be evaluated here, where the
+        #: tracks live
+        self.zone_engine = None
+        self.on_zone_event = None
 
         self.stats = {"submitted": 0, "dropped": 0, "processed": 0,
                       "vehicles": 0, "plates": 0, "embedded": 0,
-                      "clocks_anchored": 0, "infer_ms": 0.0}
+                      "clocks_anchored": 0,
+                      #: detection-frames on which a track's plate consensus
+                      #: replaced that frame's own read. Not the same thing as
+                      #: the per-detection plate_votes column, which records
+                      #: how many reads one consensus was drawn from.
+                      "plate_consensus_applied": 0,
+                      "dark_cameras": 0, "dark_frames_skipped": 0,
+                      "infer_ms": 0.0}
 
     # -- model loading -------------------------------------------------------
     def load(self) -> None:
         from ultralytics import YOLO
         log.info("loading vehicle model on %s", config.DEVICE)
         self._vehicle_model = YOLO(config.VEHICLE_MODEL)
@@ -204,85 +347,212 @@ class InferenceEngine:
 
         The recording restarted, so the previous scene-time anchor no longer
         describes this stream and must be read again.
         """
         self._clocks.pop(camera_id, None)
         self._clock_attempts.pop(camera_id, None)
+        self._clock_last_try.pop(camera_id, None)
+        self._clock_pending.pop(camera_id, None)
+        self.trackers.reset(camera_id)
+        self._plate_voters.pop(camera_id, None)
+        self._dark_streak.pop(camera_id, None)
+        self._dark_cameras.pop(camera_id, None)
+        self._dark_skipped.pop(camera_id, None)
+        self.stats["dark_cameras"] = len(self._dark_cameras)
+        if self.zone_engine is not None:
+            self.zone_engine.reset_camera(camera_id)
 
     def _anchor_clock(self, frame) -> None:
-        """Read the burned-in timestamp once per connection, then extrapolate.
+        """Read the burned-in timestamp, then extrapolate until it goes stale.
 
         Attempts are capped. Reading an overlay costs several OCR passes over
         upscaled crops, and about half the cameras on this grid have no legible
         overlay at all - retrying every frame on those saturates the queue and
         starves detection, which matters far more than scene time. Measured
         without the cap: 83% of frames dropped.
+
+        The anchor is re-read once it has been extrapolated for
+        CLOCK_REANCHOR_AFTER_S of stream time, because decoder timing drift
+        accumulates and an hours-old anchor times sightings wrongly without
+        ever saying so.
         """
         cam = frame.camera_id
-        if self._ocr is None or cam in self._clocks:
+        if self._ocr is None:
             return
-        attempts = self._clock_attempts.get(cam, 0)
-        if attempts >= CLOCK_ATTEMPT_LIMIT:
+        existing = self._clocks.get(cam)
+        if (existing is not None
+                and existing.age_s(frame.pts_ms) < CLOCK_REANCHOR_AFTER_S):
             return
-
+        exhaustive = self.clock_policy == CLOCK_EXHAUSTIVE
+        limit = INDEX_CLOCK_ATTEMPT_LIMIT if exhaustive else CLOCK_ATTEMPT_LIMIT
+        attempts = self._clock_attempts.get(cam, 0)
+        if attempts >= limit:
+            # The budget is exhausted. Give up for now, but not forever: the
+            # same reason a corroborated anchor is re-read after
+            # CLOCK_REANCHOR_AFTER_S applies to a camera that never anchored
+            # at all. An overlay unreadable at dusk may be perfectly legible
+            # once the streetlights come up, so a camera that has been silent
+            # for a re-anchor window gets one fresh budget, not a retry on
+            # every frame. The pending half-reading is dropped with it: a
+            # reading from a quarter of an hour ago is not a corroborating
+            # partner for one taken now.
+            last_try = self._clock_last_try.get(cam)
+            if (last_try is not None
+                    and frame.pts_ms - last_try < CLOCK_REANCHOR_AFTER_S * 1000.0):
+                return
+            attempts = 0
+            self._clock_attempts[cam] = 0
+            self._clock_pending.pop(cam, None)
+
+        if exhaustive:
+            # An offline pass over a finite recording has nothing to starve and
+            # every reason to succeed, so it never skips - but it spaces its
+            # attempts through the recording rather than spending the whole
+            # budget on the first frames, where the overlay may be obscured.
+            spacing = (INDEX_CLOCK_CORROBORATE_RETRY_MS
+                       if cam in self._clock_pending else INDEX_CLOCK_RETRY_MS)
+            last_try = self._clock_last_try.get(cam)
+            if last_try is not None and frame.pts_ms - last_try < spacing:
+                return
         # Anchoring costs roughly a second of OCR per attempt. Detection is the
-        # primary duty and must not queue behind it, so scene time is enriched
-        # opportunistically: attempted only while the pipeline has slack, and
-        # skipped whenever frames are backing up. A camera simply anchors a
-        # little later instead of the whole pipeline stalling.
-        if self.queue.qsize() > self.queue.maxsize // 4:
+        # primary duty and must not queue behind it, so on the live path scene
+        # time is enriched opportunistically: attempted only while the pipeline
+        # has slack, and skipped whenever frames are backing up. A camera
+        # simply anchors a little later instead of the whole pipeline stalling.
+        elif self.queue.qsize() > self.queue.maxsize // 4:
             return
 
         self._clock_attempts[cam] = attempts + 1
+        self._clock_last_try[cam] = frame.pts_ms
         from netra.analytics.scene_clock import read_scene_time
         try:
             anchor = read_scene_time(self._ocr, frame.image, frame.pts_ms, cam)
         except Exception:
             log.debug("scene clock read failed for %s", cam, exc_info=True)
             return
 
         if anchor:
+            # One reading is not evidence. A single misread digit anchors the
+            # whole stream and mis-times every sighting on it for the rest of
+            # the pass - measured on this grid as spans dated 2025-06-14,
+            # 2026-06-24 and 2028-06-13, each from one bad read that passed
+            # every syntactic check. So a reading is held until a second,
+            # independent reading agrees with it once projected forward by the
+            # PTS between them. A contradicting reading is discarded rather
+            # than averaged: the average of a right answer and a wrong one is
+            # simply a third wrong answer.
+            #
+            # The attempt budget is spent by every read, legible or not, and
+            # is refunded only by a *corroborated* anchor. Resetting it on any
+            # successful read - as this once did - made the cap unreachable
+            # for exactly the camera it exists to protect: a jittery or
+            # half-occluded overlay that reads differently every time never
+            # agrees with itself, so it never anchors, and on the live path
+            # there is no spacing gate to slow it down. Measured: 200
+            # mutually-contradicting readings produced 200 OCR calls and left
+            # the counter at zero. A contradiction is evidence that this
+            # camera's overlay cannot be trusted, so it must cost the same as
+            # an illegible one.
+            pending = self._clock_pending.get(cam)
+            if pending is None:
+                self._clock_pending[cam] = anchor
+                log.debug("%s overlay read %s; awaiting corroboration",
+                          cam, anchor.scene_time.isoformat())
+                self._note_clock_exhausted(cam, limit, existing)
+                return
+            drift = abs((anchor.scene_time
+                         - pending.at(anchor.pts_ms)).total_seconds())
+            if drift > CLOCK_CORROBORATION_TOLERANCE_S:
+                log.info("%s overlay readings disagree by %.1fs (%s then %s); "
+                         "both discarded", cam, drift,
+                         pending.scene_time.isoformat(),
+                         anchor.scene_time.isoformat())
+                # Keep the newer reading as the one to be corroborated: the
+                # older is now known to be unreliable, the newer merely
+                # unconfirmed.
+                self._clock_pending[cam] = anchor
+                self._note_clock_exhausted(cam, limit, existing)
+                return
+            self._clock_pending.pop(cam, None)
             self._clocks[cam] = anchor
+            # Corroborated: the overlay is legible and self-consistent, so the
+            # budget has done its job and is returned in full for the next
+            # re-anchor.
+            self._clock_attempts[cam] = 0
             self.stats["clocks_anchored"] = len(self._clocks)
-        elif self._clock_attempts[cam] >= CLOCK_ATTEMPT_LIMIT:
-            log.info("%s has no legible timestamp overlay after %d attempts; "
-                     "sightings on this camera carry no scene time",
-                     cam, CLOCK_ATTEMPT_LIMIT)
+            log.info("%s scene clock corroborated to %s (two readings %.1fs "
+                     "apart agreeing to %.1fs)", cam,
+                     anchor.scene_time.isoformat(),
+                     (anchor.pts_ms - pending.pts_ms) / 1000.0, drift)
+        else:
+            self._note_clock_exhausted(cam, limit, existing)
+
+    def _note_clock_exhausted(self, cam: str, limit: int, existing) -> None:
+        """Log that a camera has spent its whole anchoring budget.
+
+        Called from every path that consumes an attempt, and silent until the
+        last one, so it says so exactly once per budget rather than on every
+        frame thereafter.
+
+        A failed re-anchor leaves the existing anchor alone: an anchor carrying
+        some drift still times sightings far better than none. The attempts
+        still count, so a camera whose overlay has become unreadable - night,
+        rain, a moved caption, or one that simply never reads the same number
+        twice - stops retrying instead of burning OCR on every frame for the
+        rest of the connection.
+        """
+        if self._clock_attempts.get(cam, 0) < limit:
+            return
+        log.info("%s produced no corroborated timestamp overlay in %d "
+                 "attempts; %s", cam, limit,
+                 "keeping the existing anchor despite its age" if existing
+                 else "sightings on this camera carry no scene time")
 
     def _process(self, frame) -> None:
         t0 = time.time()
         img = frame.image
         capability = self.camera_capability.get(frame.camera_id, "vehicle")
 
         if capability == "degraded":
             return  # corrupt or unusable feed; health monitoring only
 
+        if not self._dark_gate(frame.camera_id):
+            return  # feed has gone dark; skipping until the next probe frame
+
         self._anchor_clock(frame)
         anchor = self._clocks.get(frame.camera_id)
         scene_time = anchor.at(frame.pts_ms) if anchor else None
+        # Only a corroborated anchor ever reaches self._clocks, so every scene
+        # time this engine produces is corroborated. The flag is carried
+        # explicitly all the same: rows written before corroboration landed are
+        # still in the store, and the consumers must be able to tell them apart
+        # from these without knowing which build wrote them.
+        corroborated = scene_time is not None
 
         classes = None if capability == "person" else list(config.VEHICLE_CLASSES)
         if capability == "person":
             classes = [0]  # COCO person
 
         # Escalated cameras get the larger input size: they have traffic worth
         # resolving properly, and small distant vehicles are what a 640px pass
         # loses first.
         imgsz = config.TIER2_IMGSZ if frame.dt_s and frame.dt_s < 0.5 \
             else config.TIER1_IMGSZ
 
         results = self._vehicle_model.predict(
-            img, device=config.DEVICE, verbose=False,
+            img, device=config.DEVICE, verbose=False, **_PRECISION,
             conf=config.CONF_THRESHOLD, imgsz=imgsz, classes=classes)
 
         if not results:
             return
         boxes = results[0].boxes
         if boxes is None or len(boxes) == 0:
+            self._note_luma(frame.camera_id, img, found=False)
             self.stats["processed"] += 1
             return
+        self._note_luma(frame.camera_id, img, found=True)
 
         detections: list[VehicleDetection] = []
         for box in boxes:
             cls_id = int(box.cls.item())
             name = config.VEHICLE_CLASSES.get(cls_id, "person" if cls_id == 0 else str(cls_id))
             conf = float(box.conf.item())
@@ -292,12 +562,14 @@ class InferenceEngine:
             det = VehicleDetection(
                 camera_id=frame.camera_id, pts_ms=frame.pts_ms,
                 wall_time=frame.wall_time, vehicle_class=name,
                 confidence=conf, bbox=[x1, y1, x2, y2],
                 colour=estimate_colour(crop) if cls_id != 0 else None,
                 scene_time=scene_time,
+                scene_time_corroborated=corroborated,
+                track_id=None,
                 evidence=crop)
             detections.append(det)
 
         self.stats["vehicles"] += len(detections)
 
         # Embed in one batch - far cheaper than one call each - but only the
@@ -330,32 +602,135 @@ class InferenceEngine:
             candidates = [d for d in detections
                           if (d.bbox[3] - d.bbox[1]) >= PLATE_MIN_VEHICLE_PX]
             candidates.sort(key=lambda d: -(d.bbox[3] - d.bbox[1]))
             for det in candidates[:PLATE_MAX_PER_FRAME]:
                 self._read_plate(img, det)
 
+        # Tracking turns independent detections into vehicle journeys, which
+        # is what counting, direction, dwell and zone rules all require.
+        tracker = self.trackers.get(frame.camera_id)
+        tracker.update(detections, frame.pts_ms)
+
+        # Tracking has now assigned track ids, so the per-frame plate reads
+        # taken above can be pooled per vehicle and voted on. A read from one
+        # frame is a guess; ten reads of the same track are evidence.
+        if capability == "anpr":
+            self._vote_plates(frame, tracker, detections)
+
+        if self.zone_engine is not None:
+            try:
+                h, w = img.shape[:2]
+                events = self.zone_engine.evaluate(
+                    frame.camera_id, list(tracker.tracks.values()), (w, h))
+                if events and self.on_zone_event:
+                    for event in events:
+                        self.on_zone_event(event, frame)
+            except Exception:
+                log.exception("zone evaluation failed for %s", frame.camera_id)
+
         if detections and self.on_vehicles_present:
             self.on_vehicles_present(frame.camera_id)
 
         for det in detections:
             self.on_detection(det)
 
         self.stats["processed"] += 1
         self.stats["infer_ms"] = round((time.time() - t0) * 1000, 1)
 
+    # -- dark feeds ----------------------------------------------------------
+    def _dark_gate(self, camera_id: str) -> bool:
+        """False while this camera is dark and not due for its probe frame.
+
+        A camera marked dark is never abandoned: one frame in every
+        DARK_RECHECK_FRAMES goes through the full pass, so dawn, a restored
+        illuminator or an uncovered lens brings it back with no operator
+        action. Recovery is decided by that frame's own result, in _note_luma.
+        """
+        if camera_id not in self._dark_cameras:
+            return True
+        seen = self._dark_skipped.get(camera_id, 0) + 1
+        if seen < DARK_RECHECK_FRAMES:
+            self._dark_skipped[camera_id] = seen
+            self.stats["dark_frames_skipped"] += 1
+            return False
+        self._dark_skipped[camera_id] = 0
+        return True
+
+    def _note_luma(self, camera_id: str, img, found: bool) -> None:
+        """Track the dark-frame streak for one camera.
+
+        Darkness alone is not enough to stop looking: a genuinely dark scene
+        that still yields detections is a camera doing its job. Only frames
+        that are both dark *and* empty count towards the streak, and either
+        condition failing clears it and restores the camera.
+        """
+        if not found and mean_luma(img) < DARK_LUMA_THRESHOLD:
+            streak = self._dark_streak.get(camera_id, 0) + 1
+            self._dark_streak[camera_id] = streak
+            if streak >= DARK_FRAME_LIMIT and camera_id not in self._dark_cameras:
+                self._dark_cameras[camera_id] = time.time()
+                self._dark_skipped[camera_id] = 0
+                log.warning("%s has produced %d dark, empty frames - skipping "
+                            "inference, re-testing every %d frames",
+                            camera_id, streak, DARK_RECHECK_FRAMES)
+        else:
+            self._dark_streak.pop(camera_id, None)
+            if self._dark_cameras.pop(camera_id, None) is not None:
+                self._dark_skipped.pop(camera_id, None)
+                log.info("%s is no longer dark - resuming inference", camera_id)
+        self.stats["dark_cameras"] = len(self._dark_cameras)
+
+    def dark_cameras(self) -> list[str]:
+        """Cameras currently being skipped, for pipeline status."""
+        return sorted(self._dark_cameras)
+
+    def _vote_plates(self, frame, tracker, detections: list) -> None:
+        """Fold this frame's plate reads into each track's running vote."""
+        voter = self._plate_voters.get(frame.camera_id)
+        if voter is None:
+            from netra.analytics.plate_vote import PlateVoter
+            voter = self._plate_voters[frame.camera_id] = PlateVoter()
+
+        for det in detections:
+            if det.track_id is None:
+                continue
+            if det.plate_text:
+                voter.add(det.track_id, det.plate_text,
+                          det.plate_conf or 0.0, frame.pts_ms)
+            result = voter.consensus(det.track_id)
+            if result is None:
+                continue
+            text, conf, voters = result
+            if voters < 2:
+                # One voter is not a vote. Either the track has a single read,
+                # or the reads disagreed on length and one was passed through
+                # unvoted. Leave this frame's own read alone rather than
+                # presenting a lone OCR guess as a consensus.
+                continue
+            det.plate_text = text
+            det.plate_conf = conf
+            det.plate_chars = len(text)
+            det.plate_votes = voters
+            self.stats["plate_consensus_applied"] += 1
+
+        # The tracker expires stale tracks internally; without this the voter
+        # would hold reads for vehicles that left the frame long ago.
+        voter.retain(tracker.tracks.keys())
+
     def _read_plate(self, img, det: VehicleDetection) -> None:
         """Localise and read the plate on one vehicle."""
         x1, y1, x2, y2 = det.bbox
         crop = img[max(y1, 0):y2, max(x1, 0):x2]
         if crop.size == 0:
             return
 
         plate_crop, plate_box = None, None
         if self._plate_model is not None:
             res = self._plate_model.predict(crop, device=config.DEVICE,
-                                            verbose=False, conf=0.25, imgsz=320)
+                                            verbose=False, **_PRECISION,
+                                            conf=0.25, imgsz=320)
             if res and res[0].boxes is not None and len(res[0].boxes) > 0:
                 best = max(res[0].boxes, key=lambda b: float(b.conf.item()))
                 px1, py1, px2, py2 = (int(v) for v in best.xyxy[0].tolist())
                 plate_crop = crop[max(py1, 0):py2, max(px1, 0):px2]
                 plate_box = [x1 + px1, y1 + py1, x1 + px2, y1 + py2]
         else:
@@ -370,12 +745,15 @@ class InferenceEngine:
         text, conf = _run_ocr(self._ocr, plate_crop)
         if not text:
             return
         det.plate_text = text
         det.plate_conf = conf
         det.plate_chars = len(text)
+        # A lone read is recorded as exactly that: one vote. The voter
+        # overwrites this with the real count if the track reaches a consensus.
+        det.plate_votes = 1
         det.plate_bbox = plate_box
         self.stats["plates"] += 1
 
 
 # -- OCR backend -------------------------------------------------------------
 # Kept behind two small functions so the backend can be swapped without the
@@ -404,6 +782,174 @@ def _run_ocr(reader, crop) -> tuple[str | None, float | None]:
         return None, None
     best = max(results, key=lambda r: r[2])
     text = "".join(ch for ch in best[1].upper() if ch.isalnum())
     if len(text) < 4:
         return None, None
     return text, float(best[2])
+
+
+def _self_check() -> None:
+    """Check the dark-feed short-circuit without loading a model or a GPU."""
+    engine = InferenceEngine.__new__(InferenceEngine)  # no models, no threads
+    engine._dark_streak, engine._dark_cameras, engine._dark_skipped = {}, {}, {}
+    engine.stats = {"dark_cameras": 0, "dark_frames_skipped": 0}
+
+    black = np.zeros((240, 320, 3), dtype=np.uint8)
+    lit = np.full((240, 320, 3), 90, dtype=np.uint8)
+    assert mean_luma(black) == 0.0
+    assert mean_luma(lit) > DARK_LUMA_THRESHOLD
+    assert mean_luma(None) == 0.0
+
+    # Dark but not yet decided: one frame short of the limit still runs.
+    for _ in range(DARK_FRAME_LIMIT - 1):
+        engine._note_luma("CAM1", black, found=False)
+    assert engine._dark_cameras == {}, engine._dark_streak
+    assert engine._dark_gate("CAM1") is True
+
+    engine._note_luma("CAM1", black, found=False)
+    assert "CAM1" in engine._dark_cameras
+    assert engine.dark_cameras() == ["CAM1"]
+    assert engine.stats["dark_cameras"] == 1
+
+    # Skipped until the probe frame, then one frame goes through.
+    for _ in range(DARK_RECHECK_FRAMES - 1):
+        assert engine._dark_gate("CAM1") is False
+    assert engine.stats["dark_frames_skipped"] == DARK_RECHECK_FRAMES - 1
+    assert engine._dark_gate("CAM1") is True          # the probe
+    assert engine._dark_gate("CAM1") is False         # back to skipping
+
+    # A probe that finds light must restore the camera by itself.
+    engine._note_luma("CAM1", lit, found=False)
+    assert engine._dark_cameras == {} and engine._dark_streak == {}
+    assert engine.stats["dark_cameras"] == 0
+
+    # A dark scene that still yields detections is a camera doing its job and
+    # must never be short-circuited, however long it stays dark.
+    for _ in range(DARK_FRAME_LIMIT * 2):
+        engine._note_luma("CAM2", black, found=True)
+    assert "CAM2" not in engine._dark_cameras
+    assert engine._dark_gate("CAM2") is True
+
+    # A streak broken before the limit starts again from zero.
+    for _ in range(DARK_FRAME_LIMIT - 1):
+        engine._note_luma("CAM3", black, found=False)
+    engine._note_luma("CAM3", black, found=True)
+    engine._note_luma("CAM3", black, found=False)
+    assert engine._dark_streak["CAM3"] == 1 and "CAM3" not in engine._dark_cameras
+
+    # Scene-clock corroboration. One reading never anchors: a single misread
+    # digit would mis-time every sighting on the camera for the rest of the
+    # pass, which is how this grid produced streams dated 2028. No model and no
+    # GPU: the OCR object and the overlay reader are stubs.
+    from datetime import datetime, timedelta, timezone
+
+    from netra.analytics import scene_clock as _sc
+    from netra.analytics.scene_clock import ClockAnchor
+
+    class _Frame:
+        def __init__(self, cam, pts):
+            self.camera_id, self.image, self.pts_ms = cam, black, pts
+
+    base = datetime(2026, 6, 14, 2, 32, 18, tzinfo=timezone.utc)
+    clock = InferenceEngine(on_detection=lambda d: None)
+    clock._ocr = object()
+    readings: dict = {}
+    real_reader = _sc.read_scene_time
+    _sc.read_scene_time = lambda ocr, img, pts, cam: ClockAnchor(
+        cam, readings[cam].pop(0), pts, 0.8) if readings.get(cam) else None
+    try:
+        # Two readings that agree once projected forward by PTS: anchored.
+        readings["AGREE"] = [base, base + timedelta(seconds=30)]
+        clock._anchor_clock(_Frame("AGREE", 0.0))
+        assert "AGREE" not in clock._clocks, "one reading must not anchor"
+        assert "AGREE" in clock._clock_pending
+        clock._anchor_clock(_Frame("AGREE", 30000.0))
+        assert clock._clocks["AGREE"].scene_time == base + timedelta(seconds=30)
+
+        # Two readings that contradict: neither anchors, and the later one is
+        # held as the next thing to be corroborated rather than trusted.
+        readings["DISAGREE"] = [base, base + timedelta(minutes=5)]
+        clock._anchor_clock(_Frame("DISAGREE", 0.0))
+        clock._anchor_clock(_Frame("DISAGREE", 30000.0))
+        assert "DISAGREE" not in clock._clocks, clock._clocks
+        assert clock._clock_pending["DISAGREE"].scene_time == base + timedelta(minutes=5)
+
+        # A camera that yields exactly one reading stays unanchored: no scene
+        # time is better than a wrong one.
+        readings["ONCE"] = [base]
+        clock._anchor_clock(_Frame("ONCE", 0.0))
+        clock._anchor_clock(_Frame("ONCE", 30000.0))
+        assert "ONCE" not in clock._clocks, clock._clocks
+
+        # A loop cut voids the pending reading along with everything else.
+        clock.reset_camera_state("AGREE")
+        assert "AGREE" not in clock._clock_pending and "AGREE" not in clock._clocks
+
+        # The attempt budget must bound contradictions as well as illegible
+        # frames. A camera whose overlay never reads the same number twice is
+        # exactly the one the cap exists for: on the live path there is no
+        # spacing gate, only the queue-slack check, so an unbounded retry
+        # OCRs on every slack frame forever. Feed 200 mutually-contradicting
+        # readings and count the reads that actually reached the reader.
+        calls: list = []
+        _sc.read_scene_time = lambda ocr, img, pts, cam: (
+            calls.append(cam)
+            or ClockAnchor(cam, base + timedelta(hours=len(calls)), pts, 0.8))
+        jitter = InferenceEngine(on_detection=lambda d: None)
+        jitter._ocr = object()
+        assert jitter.clock_policy == CLOCK_OPPORTUNISTIC
+        for k in range(200):
+            jitter._anchor_clock(_Frame("JITTER", k * 1000.0))
+        assert len(calls) <= CLOCK_ATTEMPT_LIMIT, len(calls)
+        assert "JITTER" not in jitter._clocks, "contradictions must not anchor"
+
+        # ...but giving up is not permanent. Once a re-anchor window of stream
+        # time has passed with no attempt, one fresh budget is granted, and an
+        # agreeing pair within it anchors normally.
+        spent = len(calls)
+        readings["JITTER"] = [base, base + timedelta(seconds=30)]
+        _sc.read_scene_time = lambda ocr, img, pts, cam: ClockAnchor(
+            cam, readings[cam].pop(0), pts, 0.8) if readings.get(cam) else None
+        later = 200_000.0 + CLOCK_REANCHOR_AFTER_S * 1000.0
+        jitter._anchor_clock(_Frame("JITTER", later))
+        jitter._anchor_clock(_Frame("JITTER", later + 30000.0))
+        assert jitter._clocks["JITTER"].scene_time == base + timedelta(seconds=30)
+        # A corroborated anchor returns the budget in full for the next one.
+        assert jitter._clock_attempts["JITTER"] == 0
+        assert spent <= CLOCK_ATTEMPT_LIMIT
+
+        # A detection carries whether its anchor was corroborated, because the
+        # store still holds rows written before corroboration existed and the
+        # elapsed-time consumers must be able to tell them apart.
+        assert VehicleDetection(camera_id="X", pts_ms=0.0, wall_time=0.0,
+                                vehicle_class="car", confidence=0.9,
+                                bbox=[0, 0, 1, 1]).scene_time_corroborated is False
+    finally:
+        _sc.read_scene_time = real_reader
+
+    # Plate vote counts reach the detection. A consensus drawn from seven reads
+    # and a single unrepeated guess are shown to an operator as the same string
+    # unless the count travels with it, so the wiring is pinned here rather
+    # than left to be noticed missing in the console.
+    class _Tracker:
+        tracks: dict = {1: object()}
+
+    voter_engine = InferenceEngine.__new__(InferenceEngine)
+    voter_engine._plate_voters = {}
+    voter_engine.stats = {"plate_consensus_applied": 0}
+    voted = VehicleDetection(camera_id="CAMV", pts_ms=0.0, wall_time=0.0,
+                             vehicle_class="car", confidence=0.9,
+                             bbox=[0, 0, 1, 1], plate_text="GJ01AB1234",
+                             plate_conf=0.8, plate_votes=1, track_id=1)
+    frame_v = _Frame("CAMV", 0.0)
+    for k in range(7):
+        voted.plate_text, voted.plate_conf = "GJ01AB1234", 0.8
+        frame_v.pts_ms = k * 100.0
+        voter_engine._vote_plates(frame_v, _Tracker(), [voted])
+    assert voted.plate_votes == 7, voted.plate_votes
+    assert voter_engine.stats["plate_consensus_applied"] == 6, voter_engine.stats
+
+    print("inference self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/analytics/loop_index.py b/netra/analytics/loop_index.py
new file mode 100644
index 0000000..bf2a0d5
--- /dev/null
+++ b/netra/analytics/loop_index.py
@@ -0,0 +1,970 @@
+"""Loop indexing and real journey mining.
+
+The Sentinel grid does not carry live cameras. Each endpoint replays one finite
+recording on an endless loop, and — this is the part that matters — the cameras
+inside a time group replay recordings made at the same time, so the clock burnt
+into their frames is a *shared* clock. See docs/feed-recon-findings.md.
+
+Two consequences follow, and this module is both of them:
+
+  * A camera's loop is finite, so it can be processed once, exhaustively, into
+    a complete index of every vehicle it ever shows. Live processing samples;
+    indexing does not have to. `index_camera` therefore runs slower than
+    real time on purpose rather than dropping frames.
+  * Once two cameras of one group are indexed, vehicles that genuinely appear
+    on both can be *found* rather than demonstrated. `find_journeys` mines the
+    index for them.
+
+The honesty rule that governs `reid.py` governs this too. A mined journey is a
+chain of appearance matches, not an identification. Every journey carries its
+mean similarity, the arithmetic of each hop, and a note saying plainly that it
+is a candidate for an operator to confirm.
+
+Three constraints keep the mining defensible:
+
+  * Scene time only. A journey validated on wall time would be fiction: each
+    loop starts whenever a client happened to connect, so wall time measures
+    our connection, not the vehicle. A detection with no parsed scene time
+    cannot take part in a journey, and is reported as excluded rather than
+    quietly falling back to the capture clock.
+  * Never across time groups, for the same reason route reconstruction and
+    clone detection refuse to: two recording sessions share no clock.
+  * Never two sightings of one camera in a row. That is one vehicle seen
+    twice, not a journey between places.
+"""
+from __future__ import annotations
+
+import logging
+import time
+from dataclasses import dataclass, asdict, field
+from datetime import datetime
+
+from netra.analytics.inference import (CLOCK_EXHAUSTIVE,
+                                       INDEX_CLOCK_RETRY_MS)
+from netra.analytics.matching import spacetime_plausible
+from netra.analytics.reid import SIMILARITY_THRESHOLD, similarity
+from netra.core.geo import TIME_GROUPS, haversine_km
+from netra.core.geo import time_group as camera_time_group
+from netra.core.timing import scene_time as _scene_time
+from netra.core.timing import sighting_time
+
+log = logging.getLogger(__name__)
+
+#: Longest a loop-length probe may run before giving up. A recording that has
+#: not restarted inside this is either longer than we care to wait for or the
+#: connection is wedged; either way the caller gets None rather than a hang.
+LOOP_PROBE_TIMEOUT_S = 180.0
+
+#: PTS moving backwards by more than this is the loop point rather than the
+#: ordinary out-of-order delivery of B-frames.
+LOOP_JUMP_TOLERANCE_MS = 250.0
+
+#: Consecutive failed reads tolerated before a probe or index gives up.
+READ_FAILURE_LIMIT = 60
+
+#: Ceiling on the mining itself. Appearance comparison is O(n²) in the worst
+#: case, and a fully indexed Ahmedabad group runs to tens of thousands of rows.
+#: ponytail: mining considers at most MAX_MINED_DETECTIONS of the most recent
+#: detections, examines at most MAX_CANDIDATES_PER_DETECTION forward matches
+#: for each one, extends each chain greedily by the single best next hop rather
+#: than searching alternatives, and returns at most MAX_JOURNEYS. So this finds
+#: strong journeys, not every journey: a vehicle whose true next sighting
+#: scored second is followed down the wrong branch and no backtracking recovers
+#: it. An exhaustive search over an indexed loop is not affordable inside an
+#: API request, and a bounded search that says so is the honest trade.
+#:
+#: A chain is capped at MAX_CHAIN_HOPS sightings and MAX_JOURNEY_SECONDS of
+#: recorded time, and confidence decays with every additional hop. Without
+#: those two ceilings a greedy chain welds itself onward indefinitely — 20,000
+#: synthetic detections produced one 1,500-hop "journey" spanning twelve hours
+#: at maximum confidence, because every individual leg is feasible. On real
+#: footage, where a hundred silver hatchbacks look alike, that is not one
+#: vehicle; it is dozens, presented as overwhelming evidence. The ceiling is
+#: therefore a correctness property, not a performance one, and it is
+#: deliberately tight: beyond a dozen transitive appearance links there is no
+#: honest reading of the chain as one vehicle.
+MAX_MINED_DETECTIONS = 4000
+MAX_CANDIDATES_PER_DETECTION = 8
+#: Rows looked at ahead of a hop before the search gives up on extending it.
+#: Bounds the inner loop even where nothing scores above the threshold.
+MAX_SCAN_AHEAD = 200
+MAX_JOURNEYS = 50
+
+#: How far ahead in scene time a hop may reach. Beyond this the appearance
+#: evidence is doing all the work and the space-time check none of it.
+MAX_HOP_SECONDS = 1800.0
+
+#: Sightings one journey may contain, and the recorded time it may span. A
+#: vehicle followed continuously for hours across a dozen transitive
+#: appearance links is not a claim this evidence supports.
+MAX_CHAIN_HOPS = 12
+MAX_JOURNEY_SECONDS = 3600.0
+
+#: The threshold journeys are always *mined* at. Callers may ask for a stricter
+#: one, but that filters what they are shown; it never re-mines the shared
+#: store at their setting, because one narrow request must not shrink what
+#: every other reader sees.
+DEFAULT_MIN_SIMILARITY = 0.84
+
+#: A journey can never be certain — see the module docstring.
+MAX_CONFIDENCE = 0.95
+
+#: Confidence lost per hop beyond the first pair. Every extra hop is another
+#: transitive appearance match, and the chance that one of them jumped to a
+#: different vehicle of the same colour compounds — so a long chain is weaker
+#: evidence than a short one, not stronger, and the arithmetic must say so.
+CHAIN_DECAY_PER_HOP = 0.08
+
+JOURNEY_NOTE = (
+    "Appearance-based candidate journey, not an identification. Each hop is a "
+    "cosine match between vehicle crops that also passes the space-time "
+    "feasibility check on the recorded clock. Confirm against plate, evidence "
+    "crops or another signal before acting on it.")
+
+
+# --------------------------------------------------------------- indexing --
+def estimate_loop_length(camera_id: str, timeout_s: float = LOOP_PROBE_TIMEOUT_S,
+                         spec=None) -> float | None:
+    """Length of one camera's recording in seconds, measured, or None.
+
+    Measured rather than asked for: the grid publishes no duration and two of
+    its cameras declare 0/0 fps, so nothing in the catalogue can be trusted to
+    describe timing.
+
+    The measurement is restart-to-restart. Joining mid-loop and timing to the
+    first restart would only ever see the tail of the recording, and reporting
+    that as *the* loop length would understate it by however long we happened
+    to arrive late — so the first restart starts the clock and the second stops
+    it. That costs up to two loops of patience, hence the timeout, and returns
+    None rather than a lower bound dressed up as a measurement.
+    """
+    from netra.ingest.sources import build, spec_for_camera
+
+    spec = spec or spec_for_camera(camera_id)
+    source = build(spec)
+    try:
+        source.open()
+    except Exception as exc:  # a probe must never take the caller down with it
+        log.warning("%s loop probe could not open source: %s", camera_id, exc)
+        return None
+
+    deadline = time.time() + timeout_s
+    restarts = 0
+    highest = 0.0
+    last = 0.0
+    failures = 0
+    try:
+        while time.time() < deadline:
+            ok, _img, pts = source.read()
+            if not ok:
+                failures += 1
+                if failures >= READ_FAILURE_LIMIT:
+                    log.warning("%s loop probe: stream stopped delivering", camera_id)
+                    return None
+                continue
+            failures = 0
+            if pts + LOOP_JUMP_TOLERANCE_MS < last:
+                restarts += 1
+                if restarts >= 2:
+                    # A complete pass, start to start.
+                    return round(highest / 1000.0, 2) if highest > 0 else None
+                highest = 0.0  # discard the partial loop we joined
+            last = pts
+            if restarts >= 1:
+                highest = max(highest, pts)
+    finally:
+        source.release()
+
+    log.warning("%s loop probe timed out after %.0fs having seen %d restart(s)",
+                camera_id, timeout_s, restarts)
+    return None
+
+
+def _submit_blocking(engine, frame, deadline: float) -> bool:
+    """Hand a frame to inference, waiting for room rather than dropping it.
+
+    The live path drops frames under load deliberately: a control room needs
+    the newest frame, not every frame. Indexing wants the opposite. The whole
+    value of a finite loop is that it can be processed *completely*, so here we
+    slow the reader down to the model's pace instead of losing vehicles.
+    """
+    while time.time() < deadline:
+        if engine.queue.qsize() < engine.queue.maxsize:
+            engine.submit(frame)
+            return True
+        time.sleep(0.01)
+    return False
+
+
+def index_camera(camera_id: str, engine, max_seconds: float = 900.0,
+                 spec=None, persist: bool = True) -> dict:
+    """Run one complete pass of a camera's loop through the live inference path.
+
+    `engine` is a loaded, started `InferenceEngine`. Its detection callback is
+    borrowed for the duration and restored afterwards, so indexing reuses the
+    identical detection, embedding and scene-time-anchoring code the live
+    pipeline runs — an index built by a second implementation would not be
+    comparable with the detections already in the database.
+
+    Stops at the loop point, at `max_seconds` of wall time, or when the stream
+    stops delivering, whichever comes first.
+    """
+    from netra.ingest.sources import build, spec_for_camera
+    from netra.ingest.stream import Frame
+
+    # Fail here rather than silently indexing nothing: an unloaded engine
+    # accepts frames and produces no detections at all, which looks exactly
+    # like a camera with no traffic.
+    if getattr(engine, "_vehicle_model", None) is None:
+        raise RuntimeError("engine must be load()ed before indexing")
+    if getattr(engine, "_thread", None) is None or not engine._thread.is_alive():
+        raise RuntimeError("engine must be start()ed before indexing")
+
+    spec = spec or spec_for_camera(camera_id)
+    source = build(spec)
+    try:
+        source.open()
+    except Exception as exc:
+        log.warning("%s index could not open source: %s", camera_id, exc)
+        return {"camera_id": camera_id, "error": str(exc), "frames": 0,
+                "detections": 0, "written": 0}
+
+    collected: list = []
+    previous_callback = engine.on_detection
+    previous_policy = engine.clock_policy
+    engine.on_detection = collected.append
+    # The live path reads the overlay clock only while the queue has slack,
+    # because detection must never queue behind a second of OCR. Indexing feeds
+    # frames blocking, so that queue is always full and the opportunistic rule
+    # skipped every attempt - 27,000 detections indexed with a scene clock on
+    # none of them, and no journey can form without one. An offline pass over a
+    # finite recording has nothing to starve, so it anchors unconditionally.
+    # The live default is untouched; only this pass changes policy, and it is
+    # restored below.
+    engine.clock_policy = CLOCK_EXHAUSTIVE
+    # Trackers and clock anchors from any earlier run describe a different pass
+    # over the same recording; carrying them in would invent motion across the
+    # join point.
+    engine.reset_camera_state(camera_id)
+
+    deadline = time.time() + max_seconds
+    first_pts: float | None = None
+    last_pts = 0.0
+    highest = 0.0
+    frames = 0
+    submitted = 0
+    failures = 0
+    looped = False
+
+    try:
+        while time.time() < deadline:
+            ok, img, pts = source.read()
+            if not ok:
+                failures += 1
+                if failures >= READ_FAILURE_LIMIT:
+                    break
+                continue
+            failures = 0
+            frames += 1
+            if first_pts is None:
+                first_pts = pts
+            if pts + LOOP_JUMP_TOLERANCE_MS < last_pts:
+                looped = True  # a full pass is done
+                break
+
+            dt_s = (pts - last_pts) / 1000.0 if frames > 1 else None
+            last_pts = pts
+            highest = max(highest, pts)
+            frame = Frame(camera_id=camera_id, image=img, pts_ms=pts,
+                          wall_time=time.time(), dt_s=dt_s, sequence=frames)
+            if _submit_blocking(engine, frame, deadline):
+                submitted += 1
+
+        # Let the queue drain so detections from the last frames are collected
+        # rather than discarded at the moment the pass is declared complete.
+        drain_until = min(deadline + 30.0, time.time() + 30.0)
+        while engine.queue.qsize() and time.time() < drain_until:
+            time.sleep(0.05)
+        time.sleep(0.5)
+    finally:
+        source.release()
+        engine.on_detection = previous_callback
+        engine.clock_policy = previous_policy
+
+    written = _persist(collected) if persist else 0
+    with_scene_time = sum(1 for d in collected if _scene_time(d) is not None)
+
+    return {
+        "camera_id": camera_id,
+        "frames": frames,
+        "submitted": submitted,
+        "detections": len(collected),
+        "written": written,
+        "video_seconds": round((highest - (first_pts or 0.0)) / 1000.0, 1),
+        "loop_complete": looped,
+        "scene_time_coverage": (round(with_scene_time / len(collected), 3)
+                                if collected else 0.0),
+    }
+
+
+def _persist(detections: list) -> int:
+    """Store indexed detections exactly as the live path stores them.
+
+    Deliberately routed through the pipeline's own batched flush rather than a
+    second writer: it is the code that names evidence crops, converts the wall
+    clock and runs the watchlist check, and an index whose rows differed from
+    live rows in any of those would be a second, subtly incompatible dataset.
+    """
+    if not detections:
+        return 0
+    from netra.pipeline import PIPELINE, WRITE_BATCH_SIZE
+
+    written = 0
+    for start in range(0, len(detections), WRITE_BATCH_SIZE):
+        batch = detections[start:start + WRITE_BATCH_SIZE]
+        try:
+            PIPELINE._flush(batch)
+            written += len(batch)
+        except Exception:
+            log.exception("could not persist a batch of %d indexed detections",
+                          len(batch))
+    return written
+
+
+# ---------------------------------------------------------------- mining --
+@dataclass
+class JourneyHop:
+    camera_id: str
+    camera_name: str
+    lat: float | None
+    lon: float | None
+    at: str
+    detection_id: int
+    vehicle_class: str | None
+    colour: str | None
+    plate_text: str | None
+    evidence_path: str | None
+    #: appearance agreement with the previous hop; None for the first sighting
+    similarity: float | None = None
+    leg_km: float | None = None
+    leg_seconds: float | None = None
+    implied_kmh: float | None = None
+    reason: str | None = None
+
+
+@dataclass
+class Journey:
+    time_group: str
+    hops: list[JourneyHop]
+    total_km: float
+    elapsed_s: float
+    mean_similarity: float
+    confidence: float
+    #: the chain hit MAX_CHAIN_HOPS or MAX_JOURNEY_SECONDS and was cut, so
+    #: what is shown is a bounded slice rather than the whole of what matched
+    truncated: bool = False
+    note: str = JOURNEY_NOTE
+    cameras: list[str] = field(default_factory=list)
+
+    @property
+    def hop_count(self) -> int:
+        return len(self.hops)
+
+    def to_dict(self) -> dict:
+        return {
+            "time_group": self.time_group,
+            "hops": [asdict(h) for h in self.hops],
+            "hop_count": len(self.hops),
+            "cameras": self.cameras,
+            "total_km": round(self.total_km, 2),
+            "elapsed_s": round(self.elapsed_s, 1),
+            "mean_similarity": round(self.mean_similarity, 3),
+            "confidence": self.confidence,
+            "truncated": self.truncated,
+            "note": self.note,
+        }
+
+
+def _hop_from(det, scene_at: datetime) -> JourneyHop:
+    cam = getattr(det, "camera", None)
+    return JourneyHop(
+        camera_id=det.camera_id,
+        camera_name=cam.name if cam else det.camera_id,
+        lat=cam.lat if cam else None,
+        lon=cam.lon if cam else None,
+        at=scene_at.isoformat(),
+        detection_id=det.id,
+        vehicle_class=det.vehicle_class,
+        colour=det.colour,
+        plate_text=det.plate_text,
+        evidence_path=det.evidence_path,
+    )
+
+
+def _leg(prev_det, det, prev_at: datetime, at: datetime) -> tuple[bool, dict]:
+    """Is this hop physically possible on the recorded clock?"""
+    seconds = (at - prev_at).total_seconds()
+    if seconds <= 0:
+        return False, {"reason": "sightings are simultaneous or out of order"}
+    if seconds > MAX_HOP_SECONDS:
+        return False, {"reason": f"gap of {seconds:.0f}s exceeds the "
+                                 f"{MAX_HOP_SECONDS:.0f}s hop limit"}
+
+    pcam, ncam = getattr(prev_det, "camera", None), getattr(det, "camera", None)
+    if None in (pcam, ncam) or None in (getattr(pcam, "lat", None),
+                                        getattr(pcam, "lon", None),
+                                        getattr(ncam, "lat", None),
+                                        getattr(ncam, "lon", None)):
+        km = 0.0
+    else:
+        km = haversine_km(pcam.lat, pcam.lon, ncam.lat, ncam.lon)
+
+    ok, why = spacetime_plausible(km, seconds)
+    return ok, {"km": km, "seconds": seconds, "reason": why,
+                "implied_kmh": km / (seconds / 3600.0)}
+
+
+def _confidence(similarities: list[float], hop_count: int) -> float:
+    """How strongly the appearance evidence supports this chain.
+
+    Mean similarity leads, because that is what the evidence actually is, and
+    it is then attenuated by chain length. A chain of many hops is a chain of
+    many chances to have stepped onto a different vehicle that merely looks the
+    same, and a greedy search takes the best-scoring step whether or not it is
+    the right one — so length must cost confidence rather than earn it. Only a
+    two-hop journey, the shortest thing that is a journey at all, can approach
+    the cap, and even that is capped below certainty: this is never an
+    identification.
+    """
+    if not similarities:
+        return 0.0
+    mean = sum(similarities) / len(similarities)
+    decay = 1.0 / (1.0 + CHAIN_DECAY_PER_HOP * max(0, hop_count - 2))
+    return round(min(MAX_CONFIDENCE, mean * 0.95 * decay), 3)
+
+
+def _minable(detections: list, group: str) -> tuple[list, dict]:
+    """Detections of one group that can legitimately take part in a journey."""
+    members = set(TIME_GROUPS.get(group, ()))
+    usable, excluded = [], {"wrong_group": 0, "no_scene_time": 0, "no_embedding": 0}
+    for det in detections:
+        if det.camera_id not in members:
+            excluded["wrong_group"] += 1
+            continue
+        if _scene_time(det) is None:
+            # Wall time is our connection time, not the vehicle's, and an
+            # overlay reading no second reading ever agreed with is a guess -
+            # this grid produced spans dated 2028 that way. A sighting with no
+            # corroborated clock simply cannot be placed on a journey.
+            excluded["no_scene_time"] += 1
+            continue
+        if not getattr(det, "embedding", None):
+            excluded["no_embedding"] += 1
+            continue
+        usable.append(det)
+    # Ordered oldest first, then tail-sliced: where the cap bites, the most
+    # recent pass over the recording is the one kept.
+    usable.sort(key=sighting_time)
+    if len(usable) > MAX_MINED_DETECTIONS:
+        usable = usable[-MAX_MINED_DETECTIONS:]
+    return usable, excluded
+
+
+def find_journeys(time_group: str, min_similarity: float = 0.84,
+                  min_hops: int = 2, detections: list | None = None,
+                  limit: int = MAX_JOURNEYS,
+                  report: dict | None = None) -> list[Journey]:
+    """Mine one time group's indexed detections for real cross-camera journeys.
+
+    `detections` are ORM Detection rows with `.camera` loaded; when omitted they
+    are read from the database for the group's cameras.
+
+    `report`, when supplied, is filled with how many sightings were considered
+    and how many were excluded and why. A reader cannot judge what the journeys
+    mean without knowing how much of the index could not take part.
+
+    Chaining is greedy and bounded — see MAX_MINED_DETECTIONS above for the
+    ceiling and what it costs.
+    """
+    if time_group not in TIME_GROUPS:
+        return []
+    from_db = detections is None
+    if from_db:
+        detections = _load_group_detections(time_group)
+
+    usable, excluded = _minable(detections, time_group)
+    if report is not None:
+        report.update({
+            "considered": len(usable), "excluded": excluded,
+            "supplied": len(detections),
+            # Rows read from the database were already filtered in SQL, so the
+            # exclusion counts above describe only what survived that filter -
+            # they are not the whole index. exclusion_report() is. Saying which
+            # population a number describes is the difference between an
+            # honest figure and a misleading one.
+            "population": ("rows already filtered in SQL for scene clock and "
+                           "embedding" if from_db else "the supplied list"),
+            "prefiltered_in_sql": from_db,
+        })
+    min_similarity = max(min_similarity, SIMILARITY_THRESHOLD)
+
+    used: set[int] = set()
+    journeys: list[Journey] = []
+
+    for i, seed in enumerate(usable):
+        if len(journeys) >= limit:
+            break
+        if seed.id in used:
+            continue
+
+        chain = [seed]
+        chain_times = [sighting_time(seed)]
+        sims: list[float] = []
+        legs: list[dict] = []
+
+        cursor = i
+        truncated = False
+        while True:
+            current = chain[-1]
+            current_at = chain_times[-1]
+            best = None
+            considered = 0
+            scanned = 0
+            for j in range(cursor + 1, len(usable)):
+                nxt = usable[j]
+                if considered >= MAX_CANDIDATES_PER_DETECTION or scanned >= MAX_SCAN_AHEAD:
+                    break
+                scanned += 1
+                if nxt.id in used or nxt.camera_id == current.camera_id:
+                    # Two sightings on one camera are one vehicle seen twice.
+                    continue
+                # Both cameras are group members by construction; asserted
+                # because chaining across recording sessions is the one error
+                # that would make every figure below meaningless.
+                assert camera_time_group(nxt.camera_id) == time_group, nxt.camera_id
+                score = similarity(current.embedding, nxt.embedding)
+                if score < min_similarity:
+                    continue
+                considered += 1
+                nxt_at = sighting_time(nxt)
+                ok, leg = _leg(current, nxt, current_at, nxt_at)
+                if not ok:
+                    continue
+                if best is None or score > best[0]:
+                    best = (score, j, nxt, nxt_at, leg)
+
+            if best is None:
+                break
+            score, j, nxt, nxt_at, leg = best
+            if len(chain) >= MAX_CHAIN_HOPS:
+                # A further hop was available and is being refused, which is
+                # what "truncated" should mean. A chain that simply runs out of
+                # candidates at exactly the ceiling is complete, not cut.
+                truncated = True
+                break
+            if (nxt_at - chain_times[0]).total_seconds() > MAX_JOURNEY_SECONDS:
+                # Beyond this the chain is no longer one journey; whatever
+                # follows is a separate claim and must be mined as one.
+                truncated = True
+                break
+            chain.append(nxt)
+            chain_times.append(nxt_at)
+            sims.append(score)
+            legs.append(leg)
+            cursor = j
+
+        if len(chain) < max(2, min_hops):
+            continue
+        if len({d.camera_id for d in chain}) < 2:
+            continue
+
+        hops = [_hop_from(chain[0], chain_times[0])]
+        for k in range(1, len(chain)):
+            hop = _hop_from(chain[k], chain_times[k])
+            leg = legs[k - 1]
+            hop.similarity = round(sims[k - 1], 3)
+            hop.leg_km = round(leg["km"], 2)
+            hop.leg_seconds = round(leg["seconds"], 1)
+            hop.implied_kmh = round(leg["implied_kmh"], 1)
+            hop.reason = leg["reason"]
+            hops.append(hop)
+
+        for det in chain:
+            used.add(det.id)
+
+        journeys.append(Journey(
+            time_group=time_group,
+            hops=hops,
+            total_km=sum(leg["km"] for leg in legs),
+            elapsed_s=(chain_times[-1] - chain_times[0]).total_seconds(),
+            mean_similarity=sum(sims) / len(sims),
+            confidence=_confidence(sims, len(chain)),
+            truncated=truncated,
+            cameras=sorted({d.camera_id for d in chain}),
+        ))
+
+    # Strongest evidence first: an operator reads from the top.
+    journeys.sort(key=lambda j: (j.confidence, j.hop_count), reverse=True)
+    return journeys[:limit]
+
+
+def has_embedding():
+    """SQL for "this detection actually carries an appearance vector".
+
+    `embedding.isnot(None)` is a trap on a JSON column: SQLAlchemy stores a
+    Python None as the JSON literal `null`, which is not SQL NULL, so the
+    obvious filter matches every row. On the live database that is 15,710 rows
+    of `null` counted as usable. Anything comparing embeddings, or counting how
+    many can be compared, must use this instead.
+    """
+    from sqlalchemy import JSON, String, and_, cast
+
+    from netra.core.models import Detection
+    return and_(Detection.embedding.isnot(None),
+                Detection.embedding != JSON.NULL,
+                # An empty vector is stored as `[]` and is equally unusable.
+                cast(Detection.embedding, String) != "[]")
+
+
+def _load_group_detections(group: str) -> list:
+    from sqlalchemy.orm import joinedload
+
+    from netra.core.db import SessionLocal
+    from netra.core.models import Detection
+
+    members = TIME_GROUPS.get(group, [])
+    if not members:
+        return []
+    with SessionLocal() as db:
+        return (db.query(Detection).options(joinedload(Detection.camera))
+                .filter(Detection.camera_id.in_(members),
+                        Detection.scene_time.isnot(None),
+                        # Rows written before corroborated anchoring landed
+                        # carry times no second reading ever confirmed.
+                        Detection.scene_time_corroborated.is_(True),
+                        has_embedding())
+                # Newest first for the cap, matching _minable's tail slice, so
+                # both layers keep the same end of a long index.
+                .order_by(Detection.scene_time.desc())
+                .limit(MAX_MINED_DETECTIONS).all())
+
+
+# ----------------------------------------------------------- persistence --
+def exclusion_report(group: str) -> dict:
+    """How much of a group's index cannot take part in mining, and why.
+
+    Published rather than kept internal: a reader shown three journeys needs to
+    know whether they were drawn from thirty comparable sightings or from three
+    thousand of which most had no readable clock. Without that, the journeys
+    look like the whole picture when they are a corner of it.
+
+    Every figure below describes the same population — all detections stored
+    for this group's cameras — and the three exclusion counts plus `comparable`
+    sum to it, so the breakdown can be checked rather than trusted.
+    """
+    from sqlalchemy import and_
+
+    from netra.core.db import SessionLocal
+    from netra.core.models import Detection
+
+    members = TIME_GROUPS.get(group, [])
+    if not members:
+        return {}
+    embedded = has_embedding()
+    with SessionLocal() as db:
+        base = db.query(Detection).filter(Detection.camera_id.in_(members))
+        total = base.count()
+        usable_clock = and_(Detection.scene_time.isnot(None),
+                            Detection.scene_time_corroborated.is_(True))
+        no_clock = base.filter(~usable_clock).count()
+        clocked = base.filter(usable_clock)
+        with_clock = clocked.count()
+        comparable = clocked.filter(embedded).count()
+    return {
+        "detections_in_group": total,
+        "with_scene_time": with_clock,
+        "comparable": comparable,
+        #: no overlay reading at all, or one that was never corroborated:
+        #: both are equally unusable for placing a sighting in time
+        "excluded_no_scene_time": no_clock,
+        #: counted among the clocked rows only, so the figures reconcile:
+        #: comparable + no_embedding + no_scene_time == detections_in_group
+        "excluded_no_embedding": with_clock - comparable,
+        "note": ("A sighting with no corroborated scene clock cannot be "
+                 "placed on a journey: an overlay read once and never "
+                 "confirmed is a guess, and wall time records when we "
+                 "connected to the loop, "
+                 "not when the vehicle passed. Counts describe every "
+                 "detection stored for these cameras."),
+    }
+
+
+def persist_journeys(group: str, journeys: list[Journey],
+                     min_similarity: float = 0.84) -> int:
+    """Replace the stored journeys for one group.
+
+    Replaced rather than appended: mining is deterministic over the index, so a
+    second run of the same group produces the same journeys and appending would
+    show an operator each one several times.
+    """
+    from netra.core.db import SessionLocal
+    from netra.core.models import MinedJourney
+
+    with SessionLocal() as db:
+        db.query(MinedJourney).filter(MinedJourney.time_group == group).delete()
+        for j in journeys:
+            first = datetime.fromisoformat(j.hops[0].at)
+            last = datetime.fromisoformat(j.hops[-1].at)
+            db.add(MinedJourney(
+                time_group=group, hop_count=len(j.hops), cameras=j.cameras,
+                total_km=round(j.total_km, 2), elapsed_s=round(j.elapsed_s, 1),
+                mean_similarity=round(j.mean_similarity, 3),
+                confidence=j.confidence, first_seen=first, last_seen=last,
+                min_similarity=min_similarity, truncated=j.truncated,
+                hops=[asdict(h) for h in j.hops], note=j.note))
+        db.commit()
+    return len(journeys)
+
+
+def stored_count(group: str) -> int:
+    """How many journeys are stored for a group, before any filtering.
+
+    Lets a caller tell "nothing has been mined yet" from "the filters removed
+    everything", which are different answers to different questions.
+    """
+    from netra.core.db import SessionLocal
+    from netra.core.models import MinedJourney
+
+    with SessionLocal() as db:
+        return (db.query(MinedJourney)
+                .filter(MinedJourney.time_group == group).count())
+
+
+def stored_journeys(group: str, limit: int = MAX_JOURNEYS,
+                    min_hops: int = 2, min_similarity: float = 0.0) -> list[dict]:
+    """Journeys mined earlier, so the console need not re-run the mining.
+
+    `min_hops` and `min_similarity` filter the stored rows rather than
+    re-mining. Filtering is exact for hop count; for similarity it keeps
+    journeys whose weakest hop clears the bar, which is a subset of what
+    re-mining at that threshold would produce — a stricter threshold can also
+    change which chains form, so a caller who needs that must ask for a
+    refresh. The endpoint says which of the two it did.
+    """
+    from netra.core.db import SessionLocal
+    from netra.core.models import MinedJourney
+
+    with SessionLocal() as db:
+        rows = (db.query(MinedJourney)
+                .filter(MinedJourney.time_group == group,
+                        MinedJourney.hop_count >= min_hops)
+                .order_by(MinedJourney.confidence.desc()).all())
+    if min_similarity > 0:
+        rows = [r for r in rows
+                if min(([h.get("similarity") or 1.0 for h in r.hops] or [0.0]))
+                >= min_similarity]
+    rows = rows[:limit]
+    return [{
+        "time_group": r.time_group, "hops": r.hops, "hop_count": r.hop_count,
+        "cameras": r.cameras, "total_km": r.total_km, "elapsed_s": r.elapsed_s,
+        "mean_similarity": r.mean_similarity, "confidence": r.confidence,
+        "truncated": bool(r.truncated), "mined_at_similarity": r.min_similarity,
+        "note": r.note, "mined_at": r.created_at.isoformat() if r.created_at else None,
+    } for r in rows]
+
+
+# --------------------------------------------------------------- self-check --
+def _self_check() -> None:
+    """Mining is checked on synthetic detections: no network, no GPU, no model."""
+    from datetime import timedelta, timezone
+
+    import numpy as np
+
+    def vec(seed: int):
+        rng = np.random.default_rng(seed)
+        v = rng.normal(size=32).astype(np.float32)
+        return (v / np.linalg.norm(v)).tolist()
+
+    L_MAX_HOPS = MAX_CHAIN_HOPS
+    silver, red = vec(1), vec(2)
+    assert similarity(silver, red) < 0.5, "test vectors must be distinguishable"
+
+    class FakeCam:
+        def __init__(self, cid, name, lat, lon):
+            self.id, self.name, self.lat, self.lon = cid, name, lat, lon
+
+    class FakeDet:
+        _next = [1]
+
+        def __init__(self, cam, at, emb, scene=True):
+            self.camera, self.camera_id = cam, cam.id
+            self.scene_time = at if scene else None
+            self.scene_time_corroborated = scene
+            self.wall_time = at
+            self.embedding = emb
+            self.vehicle_class, self.colour = "car", "silver"
+            self.plate_text = self.evidence_path = None
+            self.id = FakeDet._next[0]
+            FakeDet._next[0] += 1
+
+    c01 = FakeCam("cam01", "Vastrapur", 23.0290, 72.5580)
+    c04 = FakeCam("cam04", "Paldi Circle", 23.0130, 72.5620)
+    c14 = FakeCam("cam14", "Delight RLVD", 23.0290, 72.5700)
+    c10 = FakeCam("cam10", "Char Chowk", 21.5220, 70.4570)  # other group
+
+    t0 = datetime(2026, 6, 13, 23, 20, tzinfo=timezone.utc)
+
+    # A genuine three-camera journey, plus unrelated traffic.
+    dets = [
+        FakeDet(c04, t0, silver),
+        FakeDet(c14, t0 + timedelta(minutes=3), silver),
+        FakeDet(c01, t0 + timedelta(minutes=7), silver),
+        FakeDet(c04, t0 + timedelta(minutes=1), red),
+    ]
+    journeys = find_journeys("ahmedabad-13jun", detections=dets)
+    assert len(journeys) == 1, journeys
+    j = journeys[0]
+    assert j.hop_count == 3, j.hop_count
+    assert [h.camera_id for h in j.hops] == ["cam04", "cam14", "cam01"], j.hops
+    # Ordered by scene time, and each hop carries its arithmetic.
+    ats = [datetime.fromisoformat(h.at) for h in j.hops]
+    assert ats == sorted(ats), ats
+    assert all(h.leg_km is not None and h.implied_kmh is not None
+               for h in j.hops[1:]), j.hops
+    assert 0 < j.confidence < 1.0, j.confidence
+    assert "not an identification" in j.note
+
+    # Shuffled input must produce the same scene-time ordering.
+    shuffled = [dets[2], dets[0], dets[3], dets[1]]
+    j2 = find_journeys("ahmedabad-13jun", detections=shuffled)[0]
+    assert [h.camera_id for h in j2.hops] == ["cam04", "cam14", "cam01"], j2.hops
+
+    # Never chains across time groups: the Junagadh sighting shares no clock.
+    cross = [FakeDet(c04, t0, silver), FakeDet(c10, t0 + timedelta(minutes=4), silver)]
+    assert find_journeys("ahmedabad-13jun", detections=cross) == []
+    assert find_journeys("junagadh-13jun", detections=cross) == []
+
+    # An implausible hop is rejected: 1.3 km in two seconds.
+    fast = [FakeDet(c04, t0, silver), FakeDet(c14, t0 + timedelta(seconds=2), silver)]
+    assert find_journeys("ahmedabad-13jun", detections=fast) == []
+
+    # Two sightings on the same camera are not a journey.
+    same = [FakeDet(c04, t0, silver), FakeDet(c04, t0 + timedelta(minutes=3), silver)]
+    assert find_journeys("ahmedabad-13jun", detections=same) == []
+
+    # Below min_hops, nothing is returned.
+    two = [FakeDet(c04, t0, silver), FakeDet(c14, t0 + timedelta(minutes=3), silver)]
+    assert len(find_journeys("ahmedabad-13jun", detections=two, min_hops=2)) == 1
+    assert find_journeys("ahmedabad-13jun", detections=two, min_hops=3) == []
+
+    # A sighting with no scene time cannot take part.
+    no_clock = [FakeDet(c04, t0, silver),
+                FakeDet(c14, t0 + timedelta(minutes=3), silver, scene=False)]
+    assert find_journeys("ahmedabad-13jun", detections=no_clock) == []
+
+    # Dissimilar vehicles are not chained together.
+    unlike = [FakeDet(c04, t0, silver), FakeDet(c14, t0 + timedelta(minutes=3), red)]
+    assert find_journeys("ahmedabad-13jun", detections=unlike) == []
+
+    # An unknown group mines nothing rather than raising.
+    assert find_journeys("no-such-group", detections=dets) == []
+
+    # Exclusions are reported to the caller, not computed and discarded.
+    report: dict = {}
+    find_journeys("ahmedabad-13jun", detections=no_clock + [FakeDet(c10, t0, silver)],
+                  report=report)
+    assert report["excluded"]["no_scene_time"] == 1, report
+    assert report["excluded"]["wrong_group"] == 1, report
+    assert report["considered"] == 1, report
+
+    # A long chain must not become a maximum-confidence mega-journey. Every
+    # individual leg here is feasible, so nothing but the chain ceilings stops
+    # it running to a thousand hops.
+    long_run = []
+    for k in range(400):
+        cam = (c04, c14, c01)[k % 3]
+        long_run.append(FakeDet(cam, t0 + timedelta(minutes=3 * k), silver))
+    long_j = find_journeys("ahmedabad-13jun", detections=long_run)
+    assert long_j, "a long chain should still produce journeys"
+    longest = max(long_j, key=lambda j: j.hop_count)
+    assert longest.hop_count <= L_MAX_HOPS, longest.hop_count
+    assert all(j.elapsed_s <= MAX_JOURNEY_SECONDS for j in long_j),         [j.elapsed_s for j in long_j]
+    assert longest.truncated, "a chain cut at a ceiling must say so"
+    # Length costs confidence rather than earning it: the longest chain scores
+    # below a two-hop journey built from the identical embedding, and no
+    # journey of more than two hops can reach the cap.
+    two_hop = find_journeys("ahmedabad-13jun", detections=two)[0]
+    assert longest.confidence < two_hop.confidence, (longest.confidence,
+                                                     two_hop.confidence)
+    assert all(j.confidence < MAX_CONFIDENCE
+               for j in long_j if j.hop_count > 2),         [(j.hop_count, j.confidence) for j in long_j]
+
+    # The JSON-null trap: a Python None in a JSON column is stored as the JSON
+    # literal `null`, not SQL NULL, so `isnot(None)` matches it and every
+    # count built on that filter is wrong. Pinned against an in-memory SQLite
+    # so the honesty figures cannot silently regress. No network, no model.
+    from sqlalchemy import create_engine
+    from sqlalchemy.orm import sessionmaker
+
+    from netra.core.db import Base
+    from netra.core.models import Camera, Detection
+
+    mem = create_engine("sqlite://")
+    Base.metadata.create_all(mem)
+    with sessionmaker(bind=mem)() as db:
+        db.add(Camera(id="cam04", name="Paldi Circle"))
+        for emb in (None, [], [0.1, 0.2]):
+            db.add(Detection(camera_id="cam04", pts_ms=1.0, wall_time=t0,
+                             vehicle_class="car", confidence=0.5,
+                             bbox=[1, 2, 3, 4], embedding=emb))
+        db.commit()
+        rows = db.query(Detection)
+        assert rows.count() == 3
+        # The naive filter is the bug: it matches all three.
+        assert rows.filter(Detection.embedding.isnot(None)).count() == 3
+        assert rows.filter(has_embedding()).count() == 1,             rows.filter(has_embedding()).count()
+
+    # The anchoring policy, pinned in both directions. The live path must still
+    # skip the overlay read when frames are backing up - that rule was added
+    # after a measured 83% frame loss - and the indexing path must not, because
+    # skipping it there anchored 0% of 27,000 detections and no journey can
+    # form without a scene clock. No model is loaded: the OCR object and the
+    # reader are both stubs.
+    from netra.analytics import scene_clock as _sc
+    from netra.analytics.inference import (CLOCK_OPPORTUNISTIC,
+                                           InferenceEngine)
+
+    class FakeFrame:
+        camera_id, image, pts_ms = "cam04", None, 0.0
+
+    engine = InferenceEngine(on_detection=lambda d: None)
+    assert engine.clock_policy == CLOCK_OPPORTUNISTIC, engine.clock_policy
+    engine._ocr = object()  # only its presence is checked before the read
+    for _ in range(engine.queue.maxsize // 4 + 1):
+        engine.queue.put_nowait(None)  # frames backing up
+
+    tried: list = []
+    real_reader = _sc.read_scene_time
+    _sc.read_scene_time = lambda ocr, img, pts, cam: tried.append(cam)
+    try:
+        engine._anchor_clock(FakeFrame())
+        assert tried == [], "the live path must skip the read under load"
+        engine.clock_policy = CLOCK_EXHAUSTIVE
+        engine._anchor_clock(FakeFrame())
+        assert tried == ["cam04"], "indexing must anchor regardless of the queue"
+        # ...but exhaustive attempts are spaced through the recording, so an
+        # immediately following frame does not spend another attempt.
+        engine._anchor_clock(FakeFrame())
+        assert tried == ["cam04"], tried
+        later = FakeFrame()
+        later.pts_ms = INDEX_CLOCK_RETRY_MS + 1
+        engine._anchor_clock(later)
+        assert tried == ["cam04", "cam04"], tried
+    finally:
+        _sc.read_scene_time = real_reader
+
+    print("loop_index self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/analytics/matching.py b/netra/analytics/matching.py
index 1981937..a04109b 100644
--- a/netra/analytics/matching.py
+++ b/netra/analytics/matching.py
@@ -80,12 +80,189 @@ def plate_similarity(observed: str, target: str) -> tuple[float, str]:
         return 0.0, f"plate mismatch ({agree}/{span} characters)"
     # 60% agreement scores 0; full agreement over a short read approaches 0.9.
     score = 0.9 * (frac - 0.6) / 0.4
     return score, f"{agree}/{span} characters agree ({obs} vs {tgt})"
 
 
+# --- watchlist prefilter -----------------------------------------------------
+# `score_match` is cheap, but a watchlist of 10,000 entries scored against
+# every detection at thousands of detections per minute is tens of millions of
+# comparisons per minute, on the thread that also has to persist detections.
+# The prefilter's only job is to drop entries that cannot possibly alert, so
+# full scoring still decides every candidate.
+#
+# The hard constraint is that it must never drop a pair `score_match` would
+# alert on: a watchlist hit lost inside an optimisation is the worst failure
+# this platform can have, and unlike a slow scan nothing would ever reveal it.
+# So the window size is derived rather than chosen. `plate_similarity` has
+# three branches, and the awkward one is positional agreement: two plates can
+# agree on 8 of 10 characters with the two mismatches placed so that they share
+# no long common run at all. Indexing on four-character windows loses those -
+# measured at 6.1% of alerting two-error reads.
+#
+# Derivation. An alert needs `fused >= ALERT_THRESHOLD`; appearance can
+# contribute at most `WEIGHTS["appearance"]`, so the plate score must reach
+# `p_min`, which fixes the minimum positional agreement `_MIN_ALERTING_FRAC`.
+# Over a span of n characters that allows at most m mismatches, which cut the
+# agreeing characters into at most m+1 runs, so the longest shared run is at
+# least ceil((n-m)/(m+1)). A shared q-character window therefore exists
+# whenever n > q*m + q - 1. Evaluated over every span, q=2 is safe everywhere
+# and q=3 is safe except for spans of 4, 5, 7, 8, 11 and 14 - both facts are
+# asserted in the self-check rather than trusted.
+INDEX_WINDOW = 3
+#: Provably complete for every span, but far coarser: on a watchlist of
+#: Gujarat plates every entry shares the "GJ" bucket, so this degenerates to a
+#: full scan. Used only for the spans where INDEX_WINDOW is not safe.
+FALLBACK_WINDOW = 2
+#: Below this a read cannot score at all except against an equally short entry
+#: (`plate_similarity` refuses spans under 4), so those entries are held apart.
+MIN_SCORABLE_CHARS = 4
+
+
+def _min_alerting_fraction() -> float:
+    """Least positional agreement that could still clear ALERT_THRESHOLD.
+
+    Derived from the scoring constants rather than written down, so retuning
+    the weights or the threshold cannot silently invalidate the index. The 0.6
+    and 0.9/0.4 are `plate_similarity`'s own positional curve.
+    """
+    p_min = max(0.0, (ALERT_THRESHOLD - WEIGHTS["appearance"]) / WEIGHTS["plate"])
+    if p_min >= 0.9:
+        return 1.0  # no positional score could reach the threshold at all
+    return 0.6 + 0.4 * p_min / 0.9
+
+
+_MIN_ALERTING_FRAC = _min_alerting_fraction()
+_PRIMARY_SAFE: dict[int, bool] = {}
+
+
+def window_guaranteed(span: int, window: int) -> bool:
+    """Must two plates alerting positionally over `span` share a window?"""
+    import math
+    if span < window:
+        return False
+    agree = math.ceil(_MIN_ALERTING_FRAC * span - 1e-9)
+    mismatches = span - agree
+    return span > window * mismatches + window - 1
+
+
+def _primary_safe(length: int) -> bool:
+    """Cached: consulted once per entry at build and once per lookup."""
+    cached = _PRIMARY_SAFE.get(length)
+    if cached is None:
+        cached = _PRIMARY_SAFE[length] = window_guaranteed(length, INDEX_WINDOW)
+    return cached
+
+
+def plate_windows(plate: str | None, window: int = INDEX_WINDOW) -> set[str]:
+    """Every `window`-character window of the confusion-folded plate.
+
+    Windows, not a prefix: `plate_similarity` matches an observed read that is
+    a *substring* of the watchlist plate, so "AB1234" must find "GJ01AB1234"
+    even though their first characters have nothing in common. Folding first is
+    equally load-bearing - comparison happens on folded text, so an index built
+    on raw text would miss every OCR confusion the matcher exists to absorb.
+    """
+    folded = normalise_plate(plate)
+    if len(folded) < window:
+        return set()
+    return {folded[i:i + window] for i in range(len(folded) - window + 1)}
+
+
+class WatchlistIndex:
+    """Entries bucketed by the windows of their plate, for candidate lookup.
+
+    Built once per watchlist reload and thrown away with it; nothing here is
+    incremental, because a rebuild over 10,000 entries is a few milliseconds
+    every thirty seconds.
+
+    Both window sizes are indexed up front. The fallback is needed only for
+    short reads, but building it lazily would mean building it on the inference
+    thread at the moment a short read arrives, which is exactly when there is
+    no time for it.
+
+    ponytail: the fallback index is a full scan in disguise on a watchlist of
+    same-region plates, because every Gujarat plate shares the "GJ" bucket. A
+    read of 4, 5, 7 or 8 characters therefore costs what the prefilter was
+    written to avoid. That is deliberate - those are the spans where a
+    3-character window is not provably complete, and a slow correct answer
+    beats a fast one that loses a watchlist hit. The ceiling is that a grid
+    producing mostly short partial reads gets little benefit from any of this.
+    """
+
+    def __init__(self, entries: list[dict] | None = None):
+        self.entries: list[dict] = list(entries or [])
+        self._buckets: dict[str, list[dict]] = {}
+        self._fallback: dict[str, list[dict]] = {}
+        #: entries too short for any window to constrain
+        self._short: list[dict] = []
+        #: entries whose own length makes the primary window unsafe whatever
+        #: the read is, because the span is capped by the shorter of the two
+        self._forced: list[dict] = []
+        for entry in self.entries:
+            folded = normalise_plate(entry.get("plate"))
+            if len(folded) < MIN_SCORABLE_CHARS:
+                self._short.append(entry)
+                continue
+            if not _primary_safe(len(folded)):
+                self._forced.append(entry)
+            for key in plate_windows(folded, INDEX_WINDOW):
+                self._buckets.setdefault(key, []).append(entry)
+            for key in plate_windows(folded, FALLBACK_WINDOW):
+                self._fallback.setdefault(key, []).append(entry)
+
+    @staticmethod
+    def _gather(buckets: dict, windows: set[str], extra: list) -> list[dict]:
+        # The windows arrive as a set, whose iteration order is a function of
+        # hash seeding rather than of the data. Sorting them is what makes the
+        # documented stability true: without it two runs over identical inputs
+        # could hand score_match its candidates in different orders, and two
+        # entries scoring equally would alert in a different order each time.
+        seen: set[int] = set()
+        out: list[dict] = []
+        for source in [buckets.get(k, ()) for k in sorted(windows)] + [extra]:
+            for entry in source:
+                marker = id(entry)
+                if marker not in seen:
+                    seen.add(marker)
+                    out.append(entry)
+        return out
+
+    def candidates(self, plate_text: str | None) -> list[dict]:
+        """Entries worth scoring against this observed plate.
+
+        A superset of everything `score_match` could alert on. Order is stable
+        across processes and runs - the window keys are sorted before they are
+        walked - so alert ordering does not change with the prefilter's
+        internals or with this process's hash seed.
+        """
+        folded = normalise_plate(plate_text)
+        if len(folded) < MIN_SCORABLE_CHARS:
+            # Nothing this short can score against a longer plate; only an
+            # equally short entry could match it, and only exactly.
+            return list(self._short)
+
+        if _primary_safe(len(folded)):
+            return self._gather(self._buckets,
+                                plate_windows(folded, INDEX_WINDOW),
+                                self._short + self._forced)
+        # This read's span is one the primary window cannot prove. Fall back to
+        # the window that is complete for every span, and pay for it.
+        return self._gather(self._fallback,
+                            plate_windows(folded, FALLBACK_WINDOW),
+                            self._short)
+
+    def stats(self) -> dict:
+        return {"entries": len(self.entries),
+                "buckets": len(self._buckets),
+                "window": INDEX_WINDOW,
+                "unindexed": len(self._short) + len(self._forced),
+                "largest_bucket": max((len(v) for v in self._buckets.values()),
+                                      default=0)}
+
+
 def appearance_similarity(det_class: str | None, det_colour: str | None,
                           wl_class: str | None, wl_colour: str | None) -> tuple[float, str]:
     """Score vehicle class and colour agreement.
 
     Deliberately coarse. Colour under sodium and LED street lighting is not
     reliable enough to carry more weight than this, and pretending otherwise
@@ -240,11 +417,130 @@ def _self_check() -> None:
     # Space-time veto.
     ok, why = spacetime_plausible(distance_km=300.0, elapsed_s=60)
     assert not ok, why
     ok, why = spacetime_plausible(distance_km=2.0, elapsed_s=180)
     assert ok, why
 
+    # --- watchlist prefilter ------------------------------------------------
+    # The window size is derived from the scoring constants, so check the
+    # derivation itself before checking anything built on it.
+    assert abs(_MIN_ALERTING_FRAC - 0.7111111) < 1e-6, _MIN_ALERTING_FRAC
+    unsafe = [n for n in range(4, 200) if not window_guaranteed(n, INDEX_WINDOW)]
+    assert unsafe == [4, 5, 7, 8, 11, 14], unsafe
+    assert all(window_guaranteed(n, FALLBACK_WINDOW) for n in range(4, 200))
+    # And the four-character window the first cut of this used is unsafe at
+    # every span, which is why it lost alerts.
+    assert not any(window_guaranteed(n, 4) for n in range(4, 200))
+
+    entries = [{"id": 1, "plate": "GJ01AB1234"},
+               {"id": 2, "plate": "MH12XY9999"},
+               {"id": 3, "plate": "GJ18CD5678"},
+               {"id": 4, "plate": "XY9"}]          # too short to index
+    index = WatchlistIndex(entries)
+
+    # A partial read that full scoring matches must survive the prefilter. A
+    # naive first-characters index would bucket entry 1 under "GJ0" and never
+    # consider it for "AB1234", silently losing an alert.
+    got = {e["id"] for e in index.candidates("AB1234")}
+    assert 1 in got, got
+    assert score_match({"plate_text": "AB1234"}, entries[0]).reasons["plate"]["score"] > 0.5
+
+    # Confusion folding happens before bucketing, so an OCR read of "AB1Z34"
+    # still finds the entry written "AB1234".
+    assert 1 in {e["id"] for e in index.candidates("GJ0IAB1Z34")}
+
+    # The two-error positional reads that the four-character window dropped.
+    for observed in ("GJW1ABW234", "GJ0WABW234", "GJ0WAB1W34"):
+        assert 1 in {e["id"] for e in index.candidates(observed)}, observed
+
+    # A read too short to score at all sees only the short bucket.
+    assert {e["id"] for e in index.candidates("G1")} == {4}
+
+    # It must still actually filter, or it is a scan with extra steps.
+    got = {e["id"] for e in index.candidates("GJ01AB1234")}
+    assert 2 not in got, got
+
+    # --- brute force: every alerting two-error read must be returned ---------
+    # Not a hand-picked example. Every position pair, every substitution, over
+    # a watchlist large enough that the buckets are doing real work. Appearance
+    # attributes agree, which is the case that lets a weak positional plate
+    # score reach the threshold - and the case the first cut got wrong.
+    import itertools
+    import random
+
+    rng = random.Random(11)
+    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
+    plates = set()
+    while len(plates) < 300:
+        plates.add(f"GJ{rng.randint(1, 38):02d}{rng.choice(letters)}"
+                   f"{rng.choice(letters)}{rng.randint(0, 9999):04d}")
+    corpus = [{"id": i, "plate": p, "vehicle_class": "car",
+               "vehicle_colour": "white"} for i, p in enumerate(sorted(plates))]
+    # A short entry and an awkward-length one, so the short and forced buckets
+    # are exercised by the sweep rather than only by the cases above.
+    corpus.append({"id": -1, "plate": "GJ1", "vehicle_class": "car",
+                   "vehicle_colour": "white"})
+    corpus.append({"id": -2, "plate": "GJ01AB12", "vehicle_class": "car",
+                   "vehicle_colour": "white"})
+    big = WatchlistIndex(corpus)
+    observed_base = {"vehicle_class": "car", "colour": "white"}
+
+    def _variants(plate: str, errors: int):
+        folded = normalise_plate(plate)
+        for positions in itertools.combinations(range(len(folded)), errors):
+            for subs in itertools.product("WV5X", repeat=errors):
+                candidate = list(folded)
+                if any(candidate[i] == c for i, c in zip(positions, subs)):
+                    continue
+                for i, c in zip(positions, subs):
+                    candidate[i] = c
+                yield "".join(candidate)
+
+    # Bounded so the check stays a few seconds: the full one- and two-error
+    # spaces of one plate, plus a sample of the three-error space, each scored
+    # against every entry. That is the space the four-character window lost
+    # alerts in.
+    sweep = list(_variants(corpus[0]["plate"], 1))
+    sweep += list(_variants(corpus[0]["plate"], 2))
+    three = list(_variants(corpus[0]["plate"], 3))
+    sweep += rng.sample(three, 150)
+    sweep += list(_variants(corpus[-1]["plate"], 2))
+
+    alerting = lookups = 0
+    for observed in sweep:
+        keep = {id(e) for e in big.candidates(observed)}
+        lookups += 1
+        for entry in corpus:
+            if score_match({**observed_base, "plate_text": observed},
+                           entry).is_alert:
+                alerting += 1
+                # The whole point of the prefilter, asserted directly: zero
+                # losses, not "few".
+                assert id(entry) in keep, (observed, entry["plate"])
+    assert alerting > 300, alerting          # the sweep must reach real alerts
+    assert lookups > 900, lookups
+    # Partial reads longer than the truncated variants also matter: a
+    # contiguous substring is the other way OCR degrades on this grid.
+    for target in corpus[:40]:
+        folded = normalise_plate(target["plate"])
+        for begin in range(0, len(folded) - 4):
+            observed = folded[begin:begin + rng.randint(4, len(folded) - begin)]
+            keep = {id(e) for e in big.candidates(observed)}
+            for entry in corpus:
+                if score_match({**observed_base, "plate_text": observed},
+                               entry).is_alert:
+                    assert id(entry) in keep, (observed, entry["plate"])
+
+    # candidates() promises a stable order, so it must not depend on set
+    # iteration. Same index, same query, same order - checked against a second
+    # index built from the entries in a different order, which is the only
+    # thing that would shuffle the buckets' contents.
+    stable = WatchlistIndex(list(entries))
+    first = [e["plate"] for e in stable.candidates("GJ01AB1234")]
+    for _ in range(5):
+        assert [e["plate"] for e in stable.candidates("GJ01AB1234")] == first
+
     print("matching self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/analytics/plate_vote.py b/netra/analytics/plate_vote.py
new file mode 100644
index 0000000..ac5bc61
--- /dev/null
+++ b/netra/analytics/plate_vote.py
@@ -0,0 +1,285 @@
+"""Multi-frame plate voting.
+
+A plate read from a single frame is a guess: at night, at the sizes these
+cameras give us, one character in three can be wrong. But a tracked vehicle
+passes through the field of view for many frames, and each frame offers an
+independent guess at the same physical plate. Voting per character position
+across those guesses is how production ANPR turns a pile of noisy reads into
+one usable registration number.
+
+Two decisions are worth explaining:
+
+  length first    - reads of different lengths are misaligned. If one read
+                    dropped a character, its position 3 is the true position 4,
+                    and voting across the two corrupts every position after the
+                    gap. So the modal length wins and shorter or longer reads
+                    are set aside rather than blended in.
+  fold, then emit - OCR confuses G with 6 and O with 0 constantly, so votes for
+                    both must count towards the same candidate or the majority
+                    splits and a genuine minority read wins. But the character
+                    we emit is the best-supported *raw* observation in that
+                    group, never the folded form: a consensus of "6J01A81234"
+                    would read as a different vehicle to an operator.
+
+The reported confidence is the mean per-position share held by the winner, so
+a unanimous plate scores near 1.0 and one where positions were contested
+scores honestly lower. That number travels with the read, as everything
+inferred on this platform must.
+"""
+from __future__ import annotations
+
+from collections import defaultdict
+from dataclasses import dataclass
+
+from netra.analytics.matching import CONFUSIONS
+
+#: Cap per track. A vehicle stopped in view for a minute would otherwise
+#: accumulate reads without bound, and the first twenty already settle the
+#: vote - the twenty-first read has never changed an outcome in practice.
+MAX_OBSERVATIONS_PER_TRACK = 20
+
+#: Below this length a read is too fragmentary to align with anything.
+MIN_PLATE_CHARS = 4
+
+#: A vote needs at least two voters; one read has nothing to vote against.
+MIN_OBSERVATIONS_FOR_VOTE = 2
+
+
+@dataclass
+class PlateObservation:
+    """One OCR read of one tracked vehicle's plate."""
+    text: str
+    confidence: float
+    pts_ms: float
+
+
+def _fold(ch: str) -> str:
+    """Collapse a character onto its confusion class, for grouping only."""
+    return CONFUSIONS.get(ch, ch)
+
+
+class PlateVoter:
+    """Accumulates plate reads per track and votes them into a consensus.
+
+    One instance per camera; track ids are only unique within a camera.
+    """
+
+    def __init__(self) -> None:
+        self._obs: dict[int, list[PlateObservation]] = {}
+
+    def add(self, track_id: int, text: str | None,
+            confidence: float, pts_ms: float) -> None:
+        """Record one read. Short or empty reads are discarded."""
+        if not text:
+            return
+        cleaned = "".join(ch for ch in text.upper() if ch.isalnum())
+        if len(cleaned) < MIN_PLATE_CHARS:
+            return
+        bucket = self._obs.setdefault(track_id, [])
+        bucket.append(PlateObservation(cleaned, float(confidence), float(pts_ms)))
+        if len(bucket) > MAX_OBSERVATIONS_PER_TRACK:
+            # Drop the oldest: a plate gets larger and clearer as the vehicle
+            # approaches, so recent reads are the better evidence anyway.
+            del bucket[0]
+
+    def consensus(self, track_id: int) -> tuple[str | None, float, int] | None:
+        """Vote this track's reads into one plate.
+
+        Returns (text, confidence, voter_count), or None if the track has never
+        produced a usable read. `voter_count` is the number of observations
+        that actually contributed to the returned text, not the number held -
+        a caller must be able to tell a real vote from a lone read dressed up
+        as one, so the two fallback paths below both report 1.
+        """
+        observations = self._obs.get(track_id)
+        if not observations:
+            return None
+
+        if len(observations) < MIN_OBSERVATIONS_FOR_VOTE:
+            only = observations[0]
+            return only.text, only.confidence, 1
+
+        # Length first - see the module docstring. Ties go to the length whose
+        # reads OCR was most confident about.
+        by_length: dict[int, list[PlateObservation]] = defaultdict(list)
+        for obs in observations:
+            by_length[len(obs.text)].append(obs)
+        winning_length = max(
+            by_length,
+            key=lambda n: (len(by_length[n]), sum(o.confidence for o in by_length[n])))
+        cohort = by_length[winning_length]
+
+        # ponytail: reads that disagree on length are discarded rather than
+        # realigned. A proper implementation would align them by edit distance
+        # and let a dropped-character read still vote on the characters it did
+        # get right. Ceiling: on a track where every read lost a different
+        # character, we fall back to the single most confident read below and
+        # gain nothing from voting.
+        if len(cohort) < MIN_OBSERVATIONS_FOR_VOTE:
+            # Every read disagreed on length, so there is no majority to speak
+            # of. Return the single most confident read rather than inventing a
+            # consensus out of reads that never agreed.
+            best = max(observations, key=lambda o: o.confidence)
+            return best.text, best.confidence, 1
+
+        chars: list[str] = []
+        shares: list[float] = []
+        for pos in range(winning_length):
+            # group -> total confidence, and group -> best raw char seen.
+            group_conf: dict[str, float] = defaultdict(float)
+            group_best: dict[str, tuple[float, str]] = {}
+            for obs in cohort:
+                ch = obs.text[pos]
+                key = _fold(ch)
+                group_conf[key] += obs.confidence
+                prior = group_best.get(key)
+                if prior is None or obs.confidence > prior[0]:
+                    group_best[key] = (obs.confidence, ch)
+            total = sum(group_conf.values())
+            winner = max(group_conf, key=lambda k: group_conf[k])
+            chars.append(group_best[winner][1])
+            shares.append(group_conf[winner] / total if total > 0 else 0.0)
+
+        confidence = sum(shares) / len(shares) if shares else 0.0
+        return "".join(chars), round(confidence, 4), len(cohort)
+
+    def forget(self, track_id: int) -> None:
+        """Drop a track's reads once the tracker has expired it."""
+        self._obs.pop(track_id, None)
+
+    def retain(self, live_track_ids) -> None:
+        """Forget every track not in `live_track_ids`.
+
+        The tracker expires stale tracks internally, so this is how the voter
+        learns a vehicle has gone - otherwise its reads would outlive it and
+        leak memory for the lifetime of the process.
+        """
+        live = set(live_track_ids)
+        for tid in [t for t in self._obs if t not in live]:
+            del self._obs[tid]
+
+    def reset(self) -> None:
+        self._obs.clear()
+
+    def stats(self) -> dict:
+        return {"tracks": len(self._obs),
+                "observations": sum(len(v) for v in self._obs.values())}
+
+
+def _self_check() -> None:
+    """Runnable check on the voting that decides what plate police are shown."""
+    # Unanimous reads: consensus is the read, confidence near 1.0.
+    v = PlateVoter()
+    for i in range(3):
+        v.add(1, "GJ01AB1234", 0.8, i * 100.0)
+    text, conf, n = v.consensus(1)
+    assert text == "GJ01AB1234", text
+    assert conf > 0.99, conf
+    assert n == 3, n
+
+    # A minority misread is outvoted by the majority.
+    v = PlateVoter()
+    v.add(2, "GJ01AB1234", 0.8, 0.0)
+    v.add(2, "GJ01AB1234", 0.8, 100.0)
+    v.add(2, "GJ01AX1234", 0.8, 200.0)
+    text, conf, n = v.consensus(2)
+    assert text == "GJ01AB1234", text
+    assert conf < 1.0, conf  # the disagreement must show in the confidence
+
+    # A confident minority still loses to a consistent majority - two reads at
+    # 0.8 outweigh one at 0.95, which is the point of voting.
+    v = PlateVoter()
+    v.add(3, "GJ01AB1234", 0.8, 0.0)
+    v.add(3, "GJ01AB1234", 0.8, 100.0)
+    v.add(3, "GJ01AB9234", 0.95, 200.0)
+    assert v.consensus(3)[0] == "GJ01AB1234", v.consensus(3)
+
+    # Differing lengths must not corrupt each other: the short read is set
+    # aside entirely rather than shifting positions in the majority.
+    v = PlateVoter()
+    v.add(4, "GJ01AB1234", 0.8, 0.0)
+    v.add(4, "GJ01AB1234", 0.8, 100.0)
+    v.add(4, "J01AB1234", 0.9, 200.0)
+    text, conf, n = v.consensus(4)
+    assert text == "GJ01AB1234", text
+    assert len(text) == 10, text
+    assert n == 2, n  # only the two same-length reads voted, not all three
+
+    # Two reads of differing lengths mean no vote happened at all. The result
+    # is one read passed through, and it must not be reportable as a
+    # 2-observation consensus - a caller gating on "did enough voters agree"
+    # would otherwise write a lone raw OCR guess out as a voted plate.
+    v = PlateVoter()
+    v.add(41, "GJ01AB1234", 0.6, 0.0)
+    v.add(41, "J01AB1234", 0.9, 100.0)
+    text, conf, n = v.consensus(41)
+    assert n == 1, (text, conf, n)
+    assert text == "J01AB1234" and conf == 0.9, (text, conf)
+
+    # Confusion folding groups votes: O and 0 are the same vote, G and 6 too.
+    # Two reads of "GJO1AB1234" plus one of "GJ01AB1234" agree everywhere once
+    # folded, and the emitted text must remain a plausible plate rather than
+    # the folded form "6J01A81234".
+    v = PlateVoter()
+    v.add(5, "GJO1AB1234", 0.7, 0.0)
+    v.add(5, "GJ01AB1234", 0.9, 100.0)
+    v.add(5, "GJ01AB1234", 0.9, 200.0)
+    text, conf, n = v.consensus(5)
+    assert text == "GJ01AB1234", text
+    assert conf > 0.99, conf  # folded agreement is unanimity, not a dispute
+    assert "6" not in text and "8" not in text, text
+
+    # Folding must not silently rewrite a genuinely letter-dominant position.
+    v = PlateVoter()
+    v.add(6, "GJ01AB1234", 0.9, 0.0)
+    v.add(6, "6J01AB1234", 0.3, 100.0)
+    assert v.consensus(6)[0].startswith("GJ"), v.consensus(6)
+
+    # A single observation is returned as-is with its own confidence.
+    v = PlateVoter()
+    v.add(7, "GJ01AB1234", 0.42, 0.0)
+    assert v.consensus(7) == ("GJ01AB1234", 0.42, 1), v.consensus(7)
+
+    # Reads shorter than four characters carry no constraint and are ignored.
+    v = PlateVoter()
+    v.add(8, "GJ1", 0.9, 0.0)
+    v.add(8, "", 0.9, 100.0)
+    v.add(8, None, 0.9, 200.0)
+    assert v.consensus(8) is None, v.consensus(8)
+
+    # Separators and case are normalised before voting.
+    v = PlateVoter()
+    v.add(9, "gj-01 ab 1234", 0.8, 0.0)
+    v.add(9, "GJ01AB1234", 0.8, 100.0)
+    assert v.consensus(9)[0] == "GJ01AB1234", v.consensus(9)
+
+    # The observation cap is enforced, oldest first.
+    v = PlateVoter()
+    for i in range(MAX_OBSERVATIONS_PER_TRACK + 5):
+        v.add(10, "GJ01AB1234", 0.8, i * 10.0)
+    assert v.consensus(10)[2] == MAX_OBSERVATIONS_PER_TRACK, v.consensus(10)
+    assert v._obs[10][0].pts_ms == 50.0, v._obs[10][0]
+
+    # An unknown track has no consensus to offer.
+    assert PlateVoter().consensus(999) is None
+
+    # Forgetting and retaining both clear state.
+    v = PlateVoter()
+    v.add(11, "GJ01AB1234", 0.8, 0.0)
+    v.add(12, "GJ02CD5678", 0.8, 0.0)
+    v.forget(11)
+    assert v.consensus(11) is None
+    v.retain([99])
+    assert v.consensus(12) is None
+    assert v.stats()["tracks"] == 0, v.stats()
+
+    v = PlateVoter()
+    v.add(13, "GJ01AB1234", 0.8, 0.0)
+    v.reset()
+    assert v.consensus(13) is None
+
+    print("plate_vote self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/analytics/reid.py b/netra/analytics/reid.py
index 0d0fd72..8b08390 100644
--- a/netra/analytics/reid.py
+++ b/netra/analytics/reid.py
@@ -33,12 +33,105 @@ from netra import config
 log = logging.getLogger(__name__)
 
 EMBED_DIM = 512
 #: Cosine similarity above which two crops are considered a plausible match.
 #: Tuned to be permissive: this produces candidates for review, not verdicts.
 SIMILARITY_THRESHOLD = 0.80
+#: When the runner-up scores within this of the top match, the two cannot be
+#: told apart on appearance and neither may be presented as the answer.
+AMBIGUITY_MARGIN = 0.02
+
+_AMBIGUITY_NOTE = (
+    "Near-identical appearance scores: other candidates are within "
+    f"{AMBIGUITY_MARGIN:.2f} of the top match, so appearance alone cannot "
+    "separate them. Confirm against another signal before acting.")
+
+
+#: How far agreeing vision-language attributes may move a presented score.
+#: Deliberately tiny and asymmetric. Attributes are a caption a model wrote
+#: about a night-time crop, so they are corroboration, never the case: two
+#: sightings that already look alike and are also both described as a black SUV
+#: are marginally more likely to be the same vehicle, while being described as
+#: a black SUV and a white bus is much stronger evidence that they are not. The
+#: adjustment cannot promote a candidate appearance never produced, because it
+#: is applied after the similarity threshold has already selected the list.
+ATTRIBUTE_AGREE_BONUS = 0.03
+ATTRIBUTE_DISAGREE_PENALTY = 0.05
+
+
+def attribute_agreement(a: dict | None, b: dict | None) -> dict | None:
+    """Compare two attribute records as a third re-identification signal.
+
+    Returns `{"delta", "detail"}` or None when the comparison cannot be made -
+    which is the common case, because most detections carry no description.
+    Only `body_type` and `colour` are compared: they are the two fields the
+    parser recovers reliably and the two an operator would themselves check.
+
+    Attributes alone never establish a match. This mirrors the rule binding
+    appearance and colour in `matching.py`: a corroborating signal adjusts a
+    score that another signal already produced.
+    """
+    if not a or not b:
+        return None
+    fields = []
+    for key in ("body_type", "colour"):
+        x, y = a.get(key), b.get(key)
+        if not x or not y or "unknown" in (x, y):
+            continue
+        fields.append((key, x, y, x == y))
+    if not fields:
+        return None
+
+    disagreed = [f for f in fields if not f[3]]
+    if disagreed:
+        detail = "; ".join(f"{k}: described as {x} here and {y} there"
+                           for k, x, y, _ in disagreed)
+        return {"delta": -ATTRIBUTE_DISAGREE_PENALTY,
+                "detail": f"Vision-language descriptions disagree ({detail}), "
+                          f"so the presented score is reduced by "
+                          f"{ATTRIBUTE_DISAGREE_PENALTY:.2f}."}
+    # Both fields must be present and agree before anything is added: agreeing
+    # on colour alone is weak enough that it is not worth presenting as a lift.
+    if len(fields) < 2:
+        return None
+    described = ", ".join(f"{k} {x}" for k, x, _, _ in fields)
+    return {"delta": ATTRIBUTE_AGREE_BONUS,
+            "detail": f"Vision-language descriptions agree ({described}), "
+                      f"so the presented score is raised by "
+                      f"{ATTRIBUTE_AGREE_BONUS:.2f}. Attributes corroborate "
+                      f"an appearance match; they never establish one."}
+
+
+def flag_ambiguity(scored: list[dict]) -> list[dict]:
+    """Mark results the appearance evidence cannot actually separate.
+
+    Two silver hatchbacks embed almost identically, so a ranked list whose top
+    scores are nearly equal has picked a winner the evidence does not support.
+    The ambiguous candidates are kept rather than dropped - an operator shown
+    "three near-identical candidates" is better served than one shown a single
+    confident wrong answer - but every result carries the flag so the console
+    can never render the top hit as if it stood alone.
+
+    Mutates and returns the list in place; it is expected to be sorted with the
+    highest similarity first.
+
+    ponytail: ambiguity is judged against the top score only, so a tight
+    cluster further down the list is not flagged. That cluster is not competing
+    to be the answer, so it does not mislead in the same way.
+    """
+    # An epsilon, because a gap of exactly the margin must land on the
+    # cautious side rather than on whichever side binary floats round it to.
+    limit = AMBIGUITY_MARGIN + 1e-9
+    # Both keys are set on every result, including a lone one, so no consumer
+    # has to distinguish "unambiguous" from "never checked".
+    top = scored[0]["similarity"] if scored else 0.0
+    ambiguous = len(scored) >= 2 and (top - scored[1]["similarity"]) <= limit
+    for row in scored:
+        row["ambiguous"] = ambiguous and (top - row["similarity"]) <= limit
+        row["ambiguity_note"] = _AMBIGUITY_NOTE if row["ambiguous"] else None
+    return scored
 
 
 class ReIdEncoder:
     """Turns vehicle crops into comparable appearance vectors."""
 
     def __init__(self):
@@ -52,12 +145,20 @@ class ReIdEncoder:
         from torchvision.models import ResNet18_Weights
 
         weights = ResNet18_Weights.IMAGENET1K_V1
         model = torchvision.models.resnet18(weights=weights)
         model.fc = torch.nn.Identity()   # keep the pooled features, drop the classifier
         model.eval().to(config.DEVICE)
+        # Half precision on the GPU only. The backbone is a feature extractor
+        # whose output is immediately L2-normalised and compared by cosine
+        # similarity at a threshold of 0.80, so fp16's fourth-decimal noise
+        # cannot move a decision that turns on the second. On CPU fp16 is
+        # emulated and slower, so it is not taken there.
+        self._half = config.DEVICE == "cuda"
+        if self._half:
+            model.half()
         self._model = model
         self._transform = weights.transforms()
         log.info("re-identification encoder ready (%d-d)", EMBED_DIM)
 
     @property
     def ready(self) -> bool:
@@ -82,15 +183,20 @@ class ReIdEncoder:
             # ImageNet normalisation, matching the pretrained weights
             t = (t - torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)) / \
                 torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
             tensors.append(t)
 
         batch = torch.stack(tensors).to(config.DEVICE)
+        if getattr(self, "_half", False):
+            batch = batch.half()
         with self._lock, torch.no_grad():
             feats = self._model(batch)
-        feats = torch.nn.functional.normalize(feats, p=2, dim=1)
+        # Normalise in fp32: the sum of 512 squares is where half precision
+        # would actually cost something, and the embeddings are stored and
+        # compared as fp32 anyway.
+        feats = torch.nn.functional.normalize(feats.float(), p=2, dim=1)
         return feats.cpu().numpy().astype(np.float32)
 
 
 def similarity(a, b) -> float:
     """Cosine similarity between two L2-normalised embeddings."""
     if a is None or b is None:
@@ -113,13 +219,15 @@ def rank_candidates(query_embedding, detections: list, top_k: int = 25) -> list[
         if not det.embedding:
             continue
         s = similarity(query_embedding, det.embedding)
         if s >= SIMILARITY_THRESHOLD:
             scored.append({"detection": det, "similarity": round(s, 4)})
     scored.sort(key=lambda x: x["similarity"], reverse=True)
-    return scored[:top_k]
+    # Truncate first: ambiguity is about what the caller is shown, so it is
+    # judged over the returned list rather than over candidates it never sees.
+    return flag_ambiguity(scored[:top_k])
 
 
 def _self_check() -> None:
     """Check the similarity maths without requiring the model or a GPU."""
     a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
     b = np.array([1.0, 0.0, 0.0], dtype=np.float32)
@@ -135,12 +243,59 @@ def _self_check() -> None:
             self.embedding, self.name = emb, name
 
     dets = [FakeDet(list(a), "same"), FakeDet(list(c), "orthogonal"),
             FakeDet(None, "no-embedding")]
     ranked = rank_candidates(a, dets)
     assert len(ranked) == 1 and ranked[0]["detection"].name == "same", ranked
+    # A lone result has nothing to be confused with.
+    assert ranked[0]["ambiguous"] is False and ranked[0]["ambiguity_note"] is None
+
+    # Two candidates scoring within the margin are both flagged: this is the
+    # two-silver-hatchbacks case, where presenting the top hit implies a
+    # confidence the evidence does not support.
+    close = [{"similarity": 0.91}, {"similarity": 0.90}, {"similarity": 0.82}]
+    flag_ambiguity(close)
+    assert close[0]["ambiguous"] and close[1]["ambiguous"], close
+    assert close[0]["ambiguity_note"], close[0]
+    # The distant third is not part of the confusion and is not flagged.
+    assert close[2]["ambiguous"] is False and close[2]["ambiguity_note"] is None
+
+    # A clear winner is reported as one.
+    clear = [{"similarity": 0.95}, {"similarity": 0.84}]
+    flag_ambiguity(clear)
+    assert not any(r["ambiguous"] for r in clear), clear
+
+    # Exactly on the margin counts as ambiguous: the boundary should not be
+    # resolved in favour of false confidence.
+    edge = [{"similarity": 0.90}, {"similarity": 0.88}]
+    flag_ambiguity(edge)
+    assert edge[0]["ambiguous"] and edge[1]["ambiguous"], edge
+
+    # Attributes as a third signal: corroboration only, bounded both ways.
+    black_suv = {"body_type": "suv", "colour": "black"}
+    assert attribute_agreement(None, black_suv) is None
+    assert attribute_agreement({}, black_suv) is None
+    # Unknown fields are not agreement.
+    assert attribute_agreement({"body_type": "unknown", "colour": None},
+                               black_suv) is None
+    # Colour alone agreeing is too weak to present as a lift.
+    assert attribute_agreement({"colour": "black"}, black_suv) is None
+    agree = attribute_agreement(black_suv, dict(black_suv))
+    assert agree and agree["delta"] == ATTRIBUTE_AGREE_BONUS, agree
+    assert "never establish" in agree["detail"], agree
+    clash = attribute_agreement(black_suv, {"body_type": "bus",
+                                            "colour": "white"})
+    assert clash and clash["delta"] == -ATTRIBUTE_DISAGREE_PENALTY, clash
+    # A single disagreeing field is enough to lower the score.
+    half = attribute_agreement(black_suv, {"body_type": "suv",
+                                           "colour": "white"})
+    assert half and half["delta"] == -ATTRIBUTE_DISAGREE_PENALTY, half
+    # The adjustment is small enough that it can never carry a candidate on
+    # its own: it cannot lift anything over the threshold that appearance
+    # did not already place above it.
+    assert ATTRIBUTE_AGREE_BONUS < AMBIGUITY_MARGIN * 2
 
     print("reid self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/analytics/route.py b/netra/analytics/route.py
index 496b900..fb56d43 100644
--- a/netra/analytics/route.py
+++ b/netra/analytics/route.py
@@ -9,20 +9,27 @@ Two constraints shape this:
   * Sightings are only chainable within a group of cameras that share a
     recorded clock. The Sentinel sandbox holds several independently recorded
     sessions, so comparing a timestamp from one against another is meaningless.
     See docs/feed-recon-findings.md.
   * A hop that would require an impossible speed is rejected rather than drawn.
     A route the operator cannot trust is worse than a short one.
+  * A sighting whose scene clock was never corroborated is not placed on the
+    route at all. Its only other timestamp is our connection time, which says
+    when we dialled the recording rather than when the vehicle passed, so
+    chaining on it would draw a journey out of an artefact of our own uptime.
 """
 from __future__ import annotations
 
 from dataclasses import dataclass, asdict
 from datetime import datetime
 
 from netra.analytics.matching import normalise_plate, spacetime_plausible
 from netra.core.geo import haversine_km, time_group
+# Shared with cloned-plate detection so both modules order sightings identically.
+from netra.core.timing import scene_time as _scene_time
+from netra.core.timing import sighting_time as _sighting_time
 
 
 @dataclass
 class Hop:
     camera_id: str
     camera_name: str
@@ -59,22 +66,12 @@ class Route:
             "duration_s": round(self.duration_s, 1),
             "time_groups": self.time_groups,
             "hop_count": len(self.hops),
         }
 
 
-def _sighting_time(det) -> datetime:
-    """Prefer the timestamp burned into the source video over our own clock.
-
-    The sandbox replays recordings, so wall time reflects when we happened to
-    connect, not when the scene occurred. Where the camera's own overlay has
-    been parsed, that is the only meaningful ordering.
-    """
-    return det.scene_time or det.wall_time
-
-
 def build_route(detections: list, query: str, min_plate_score: float = 0.6) -> Route:
     """Chain detections of one vehicle into an ordered, validated route.
 
     `detections` are ORM Detection rows with `.camera` loaded.
     """
     target = normalise_plate(query)
@@ -91,12 +88,21 @@ def build_route(detections: list, query: str, min_plate_score: float = 0.6) -> R
 
     hops: list[Hop] = []
     rejected: list[dict] = []
     total_km = 0.0
 
     for det in candidates:
+        if _scene_time(det) is None:
+            # Listed, not chained: the operator should see that the sighting
+            # exists, and equally that it cannot be placed in time.
+            rejected.append({
+                "camera_id": det.camera_id,
+                "reason": "sighting has no corroborated scene clock; its time "
+                          "is not comparable with the other cameras",
+            })
+            continue
         cam = det.camera
         hop = Hop(
             camera_id=det.camera_id,
             camera_name=cam.name if cam else det.camera_id,
             lat=cam.lat if cam else None,
             lon=cam.lon if cam else None,
@@ -159,12 +165,13 @@ def _self_check() -> None:
         def __init__(self, cam, plate, at):
             self.camera, self.camera_id = cam, cam.id
             self.plate_text, self.plate_conf = plate, 0.9
             self.vehicle_class, self.colour = "car", "white"
             self.evidence_path = None
             self.scene_time, self.wall_time = at, at
+            self.scene_time_corroborated = True
             self.id = FakeDet._next[0]
             FakeDet._next[0] += 1
 
     t0 = datetime(2026, 6, 13, 23, 20, tzinfo=timezone.utc)
     # Two Ahmedabad cameras ~1.3 km apart, three minutes apart: plausible.
     c04 = FakeCam("cam04", "Paldi Circle", 23.0130, 72.5620)
@@ -197,11 +204,21 @@ def _self_check() -> None:
         FakeDet(c04, "AB1234", t0),
         FakeDet(c14, "GJ01AB1234", t0 + timedelta(minutes=3)),
     ]
     r3 = build_route(dets_partial, "GJ01AB1234")
     assert len(r3.hops) == 2, r3.hops
 
+    # A sighting whose overlay was read once and never corroborated must not
+    # join the route: its timestamp is a guess, and this grid has produced
+    # guesses two years out. It is reported as rejected rather than hidden.
+    uncorroborated = FakeDet(c14, "GJ01AB1234", t0 + timedelta(minutes=3))
+    uncorroborated.scene_time_corroborated = False
+    r4 = build_route([FakeDet(c04, "GJ01AB1234", t0), uncorroborated],
+                     "GJ01AB1234")
+    assert len(r4.hops) == 1, r4.hops
+    assert "corroborated" in r4.rejected[0]["reason"], r4.rejected
+
     print("route self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/analytics/scene_clock.py b/netra/analytics/scene_clock.py
index 268e2b7..52c057e 100644
--- a/netra/analytics/scene_clock.py
+++ b/netra/analytics/scene_clock.py
@@ -50,14 +50,22 @@ _DIGIT_LAYOUTS = {
 }
 
 
 # A parsed date must be a real recording date, not merely a valid datetime.
 # Without this, an OCR misread like "0921-05-16" is accepted and silently
 # corrupts every downstream correlation - observed on cam04 at confidence 0.02.
-MIN_PLAUSIBLE_YEAR = 2015
-MAX_PLAUSIBLE_YEAR = 2035
+#
+# The window is deliberately narrow. Every recording in this sandbox is dated
+# June 2026, and a wide window catches only the absurd misreads while passing
+# the dangerous ones: a single wrong digit turning 2026 into 2025 or 2028 sailed
+# through a 2015-2035 window and mis-dated whole streams. One year either side
+# tolerates footage re-recorded a season later while rejecting a year that
+# differs by a digit. It cannot catch a misread day or hour - only corroboration
+# between two readings can, and InferenceEngine._anchor_clock requires it.
+MIN_PLAUSIBLE_YEAR = 2025
+MAX_PLAUSIBLE_YEAR = 2027
 
 #: OCR readings below this confidence are discarded. No scene time is better
 #: than a wrong one: an incorrect anchor mis-times every sighting on a camera.
 MIN_OCR_CONFIDENCE = 0.25
 
 
@@ -104,12 +112,22 @@ class ClockAnchor:
     confidence: float
 
     def at(self, pts_ms: float) -> datetime:
         """Scene time for any frame, carried forward by PTS."""
         return self.scene_time + timedelta(milliseconds=pts_ms - self.pts_ms)
 
+    def age_s(self, pts_ms: float) -> float:
+        """How far, in stream seconds, this anchor is being extrapolated.
+
+        Extrapolation is only as good as the decoder's timing. Small errors in
+        PTS accumulate, so an anchor read hours ago is quietly less trustworthy
+        than one read a minute ago; callers use this to decide when to re-read
+        the overlay rather than extrapolating from one reading forever.
+        """
+        return (pts_ms - self.pts_ms) / 1000.0
+
 
 def parse_overlay(text: str) -> datetime | None:
     """Interpret one OCR reading of a timestamp overlay.
 
     Returns None rather than guessing: a wrong scene time would corrupt every
     correlation downstream, which is worse than having none.
@@ -225,19 +243,30 @@ def _self_check() -> None:
     # A syntactically valid but impossible recording date must be rejected.
     # cam04 produced exactly this at confidence 0.02 and it would otherwise
     # have mis-timed every sighting on that camera.
     assert not is_plausible(parse_overlay("16-05-0921 20:11:34"))
     assert is_plausible(parse_overlay("13-06-2026 23:22:47"))
     assert not is_plausible(None)
+    # Single-digit year misreads observed on the live grid. These passed the
+    # old 2015-2035 window and mis-dated entire streams.
+    assert not is_plausible(parse_overlay("13-06-2028 21:23:31"))
+    assert not is_plausible(parse_overlay("14-06-2015 02:29:47"))
+    assert is_plausible(parse_overlay("14-06-2025 02:29:47")) is True
 
     # PTS carries the clock forward from the anchor.
     anchor = ClockAnchor("cam04", datetime(2026, 6, 13, 23, 22, 47,
                                            tzinfo=timezone.utc), 1000.0, 0.9)
     assert anchor.at(61000.0) == datetime(2026, 6, 13, 23, 23, 47,
                                           tzinfo=timezone.utc), anchor.at(61000.0)
     assert anchor.at(1000.0) == anchor.scene_time
 
+    # Anchor age is measured in stream time, from the frame it was read on.
+    assert anchor.age_s(1000.0) == 0.0, anchor.age_s(1000.0)
+    assert anchor.age_s(61000.0) == 60.0, anchor.age_s(61000.0)
+    # A stream that has rewound (a loop cut before reset) must not read as old.
+    assert anchor.age_s(0.0) == -1.0, anchor.age_s(0.0)
+
     print("scene clock self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/analytics/tracking.py b/netra/analytics/tracking.py
new file mode 100644
index 0000000..0a3ba5a
--- /dev/null
+++ b/netra/analytics/tracking.py
@@ -0,0 +1,372 @@
+"""Per-camera multi-object tracking.
+
+Tracking turns a stream of independent detections into vehicle journeys, which
+is what counting, direction of travel, dwell time and zone intrusion all need.
+
+Two constraints rule out an off-the-shelf tracker here:
+
+  * One inference engine serves every camera, so tracker state must be kept per
+    camera. A single shared tracker would associate a vehicle on one camera
+    with a vehicle on another.
+  * Frames arrive at 1-5 fps, not 25. A vehicle can cross half the frame
+    between samples, so the constant-velocity Kalman assumption that ByteTrack
+    and similar trackers rely on does not hold.
+
+So association is by spatial overlap, widened by how much time actually passed
+(from PTS, never frame count), and broken ties are settled by the appearance
+embedding that the inference engine already computes. At low frame rates
+appearance is the stronger signal, and it costs nothing extra here.
+
+ponytail: no Kalman filter, no motion model. At 1-5 fps a motion model predicts
+badly and adds state to get wrong. Revisit only if sampling rises above ~10 fps.
+"""
+from __future__ import annotations
+
+import logging
+from dataclasses import dataclass, field
+
+log = logging.getLogger(__name__)
+
+#: Minimum overlap to associate a detection with an existing track at 1s apart.
+BASE_IOU_THRESHOLD = 0.25
+#: Appearance similarity that can rescue an association with poor overlap.
+APPEARANCE_RESCUE = 0.86
+#: A track with no sighting for this long in stream time is closed.
+TRACK_TIMEOUT_S = 6.0
+#: Hard ceiling on live tracks per camera. Timeout alone is not a bound: a busy
+#: junction can open tracks faster than they expire, and the platform is meant
+#: to run for hours, so the dictionary needs a ceiling as well as an age limit.
+MAX_TRACKS_PER_CAMERA = 300
+
+
+def iou(a: list[int], b: list[int]) -> float:
+    """Intersection over union of two [x1, y1, x2, y2] boxes."""
+    ax1, ay1, ax2, ay2 = a
+    bx1, by1, bx2, by2 = b
+    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
+    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
+    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
+    inter = iw * ih
+    if inter == 0:
+        return 0.0
+    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
+    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
+    union = area_a + area_b - inter
+    return inter / union if union > 0 else 0.0
+
+
+def centroid(box: list[int]) -> tuple[float, float]:
+    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
+
+
+@dataclass
+class Track:
+    """One vehicle followed across frames on a single camera."""
+    track_id: int
+    camera_id: str
+    vehicle_class: str
+    bbox: list[int]
+    first_pts_ms: float
+    last_pts_ms: float
+    embedding: list | None = None
+    #: centroid history, for direction and zone crossing
+    path: list = field(default_factory=list)
+    sightings: int = 1
+    counted: bool = False
+    #: zones this track has already triggered, so one entry alerts once
+    zones_triggered: set = field(default_factory=set)
+
+    @property
+    def dwell_s(self) -> float:
+        """How long this vehicle has been visible, in stream time."""
+        return (self.last_pts_ms - self.first_pts_ms) / 1000.0
+
+    def direction(self) -> str | None:
+        """Coarse compass-free direction of travel across the frame.
+
+        Reported only once the vehicle has moved far enough for the direction
+        to mean something; jitter on a stationary vehicle is not a direction.
+        """
+        if len(self.path) < 2:
+            return None
+        (x0, y0), (x1, y1) = self.path[0], self.path[-1]
+        dx, dy = x1 - x0, y1 - y0
+        if (dx * dx + dy * dy) ** 0.5 < 40:
+            return None
+        if abs(dx) > abs(dy):
+            return "right" if dx > 0 else "left"
+        return "down" if dy > 0 else "up"
+
+
+class CameraTracker:
+    """Tracks vehicles on one camera."""
+
+    def __init__(self, camera_id: str):
+        self.camera_id = camera_id
+        self.tracks: dict[int, Track] = {}
+        self._next_id = 1
+        #: cumulative count of distinct vehicles seen, by class
+        self.counts: dict[str, int] = {}
+        self.total_count = 0
+        #: vehicles counted since the last loop cut, so a headline figure can be
+        #: reported per playthrough as well as cumulatively
+        self.counted_this_loop = 0
+        #: how many times the recording has restarted under this tracker
+        self.loops_seen = 0
+        #: tracks discarded by the cap rather than by timeout
+        self.dropped_tracks = 0
+
+    def reset(self) -> None:
+        """Discard track state at a loop cut, where continuity is void.
+
+        `total_count` deliberately survives: those vehicles really were
+        observed, and zeroing the figure would throw away real observation.
+        But the recording replays, so the same vehicles are counted again on
+        every loop, and a cumulative total taken alone reads as far more
+        traffic than the footage contains. `counted_this_loop` is therefore
+        reset here and `loops_seen` incremented, so anyone reading a count of
+        "4,893 vehicles" can see whether that is one playthrough or six.
+        """
+        self.tracks.clear()
+        self.counted_this_loop = 0
+        self.loops_seen += 1
+
+    def _match(self, det, pts_ms: float) -> Track | None:
+        """Best existing track for this detection, or None."""
+        best, best_score = None, 0.0
+        for track in self.tracks.values():
+            if track.vehicle_class != det.vehicle_class:
+                continue
+
+            gap_s = max((pts_ms - track.last_pts_ms) / 1000.0, 0.0)
+            # A longer gap means the vehicle moved further, so overlap alone is
+            # a weaker signal and the threshold relaxes with elapsed time.
+            threshold = BASE_IOU_THRESHOLD / (1.0 + gap_s)
+            overlap = iou(track.bbox, det.bbox)
+
+            score = overlap if overlap >= threshold else 0.0
+
+            # Poor overlap can still be the same vehicle if it looks the same.
+            if score == 0.0 and track.embedding and det.embedding:
+                from netra.analytics.reid import similarity
+                sim = similarity(track.embedding, det.embedding)
+                if sim >= APPEARANCE_RESCUE:
+                    score = sim * 0.5   # ranked below a genuine overlap match
+
+            if score > best_score:
+                best, best_score = track, score
+        return best
+
+    def update(self, detections: list, pts_ms: float) -> list:
+        """Associate detections with tracks. Returns newly completed counts.
+
+        Each detection is given a `track_id`, and each track its dwell time and
+        direction, so downstream analytics need no tracking logic of their own.
+        """
+        self._expire(pts_ms)
+        claimed: set[int] = set()
+
+        for det in detections:
+            track = self._match(det, pts_ms)
+            if track is not None and track.track_id not in claimed:
+                track.bbox = det.bbox
+                track.last_pts_ms = pts_ms
+                track.sightings += 1
+                track.path.append(centroid(det.bbox))
+                if det.embedding:
+                    track.embedding = det.embedding
+                claimed.add(track.track_id)
+            else:
+                track = Track(
+                    track_id=self._next_id, camera_id=self.camera_id,
+                    vehicle_class=det.vehicle_class, bbox=det.bbox,
+                    first_pts_ms=pts_ms, last_pts_ms=pts_ms,
+                    embedding=det.embedding, path=[centroid(det.bbox)])
+                self.tracks[track.track_id] = track
+                self._next_id += 1
+                claimed.add(track.track_id)
+
+            det.track_id = track.track_id
+
+        # A vehicle is counted once it has been seen twice, which filters the
+        # single-frame false positives that a busy night scene produces.
+        newly_counted = []
+        for track in self.tracks.values():
+            if not track.counted and track.sightings >= 2:
+                track.counted = True
+                self.counts[track.vehicle_class] = \
+                    self.counts.get(track.vehicle_class, 0) + 1
+                self.total_count += 1
+                self.counted_this_loop += 1
+                newly_counted.append(track)
+        return newly_counted
+
+    def _expire(self, pts_ms: float) -> None:
+        stale = [tid for tid, t in self.tracks.items()
+                 if (pts_ms - t.last_pts_ms) / 1000.0 > TRACK_TIMEOUT_S]
+        for tid in stale:
+            del self.tracks[tid]
+
+        # Timeout is an age limit, not a bound. Under rapid turnover the
+        # dictionary can still grow without limit, so the least recently seen
+        # tracks are dropped once the cap is passed: a track unseen for longest
+        # is the one least likely to receive another detection, so it is the
+        # cheapest to lose. Drops are counted rather than hidden - a rising
+        # figure means the camera is busier than the tracker can follow.
+        excess = len(self.tracks) - MAX_TRACKS_PER_CAMERA
+        if excess > 0:
+            oldest = sorted(self.tracks.items(), key=lambda kv: kv[1].last_pts_ms)
+            for tid, _ in oldest[:excess]:
+                del self.tracks[tid]
+            self.dropped_tracks += excess
+
+    def stats(self) -> dict:
+        active = list(self.tracks.values())
+        directions: dict[str, int] = {}
+        for t in active:
+            d = t.direction()
+            if d:
+                directions[d] = directions.get(d, 0) + 1
+        return {
+            "camera_id": self.camera_id,
+            "active_tracks": len(active),
+            "total_counted": self.total_count,
+            "counted_this_loop": self.counted_this_loop,
+            "loops_seen": self.loops_seen,
+            "dropped_tracks": self.dropped_tracks,
+            "counts_by_class": dict(self.counts),
+            "directions": directions,
+            "mean_dwell_s": round(
+                sum(t.dwell_s for t in active) / len(active), 1) if active else 0.0,
+        }
+
+
+class TrackerRegistry:
+    """One tracker per camera."""
+
+    def __init__(self):
+        self.trackers: dict[str, CameraTracker] = {}
+
+    def get(self, camera_id: str) -> CameraTracker:
+        if camera_id not in self.trackers:
+            self.trackers[camera_id] = CameraTracker(camera_id)
+        return self.trackers[camera_id]
+
+    def reset(self, camera_id: str) -> None:
+        if camera_id in self.trackers:
+            self.trackers[camera_id].reset()
+
+    def stats(self) -> list[dict]:
+        return [t.stats() for t in self.trackers.values()]
+
+
+def _self_check() -> None:
+    """Association decides counting, direction and intrusion, so the cases that
+    would silently inflate or merge counts are pinned down here."""
+    class Det:
+        def __init__(self, bbox, cls="car", emb=None):
+            self.bbox, self.vehicle_class, self.embedding = bbox, cls, emb
+            self.track_id = None
+
+    assert abs(iou([0, 0, 10, 10], [0, 0, 10, 10]) - 1.0) < 1e-9
+    assert iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
+
+    t = CameraTracker("cam01")
+
+    # A vehicle moving slightly across three frames is one track, counted once.
+    t.update([Det([100, 100, 200, 200])], 0.0)
+    t.update([Det([110, 100, 210, 200])], 1000.0)
+    t.update([Det([120, 100, 220, 200])], 2000.0)
+    assert len(t.tracks) == 1, t.tracks
+    assert t.total_count == 1, t.total_count
+
+    # A single-frame detection is never counted: that is how false positives
+    # on a noisy night scene get filtered.
+    t2 = CameraTracker("cam02")
+    t2.update([Det([0, 0, 50, 50])], 0.0)
+    assert t2.total_count == 0, t2.total_count
+
+    # Two vehicles far apart are two tracks.
+    t3 = CameraTracker("cam03")
+    t3.update([Det([0, 0, 50, 50]), Det([500, 500, 550, 550])], 0.0)
+    assert len(t3.tracks) == 2, t3.tracks
+
+    # Different classes never merge, however well the boxes overlap.
+    t4 = CameraTracker("cam04")
+    t4.update([Det([100, 100, 200, 200], "car")], 0.0)
+    t4.update([Det([100, 100, 200, 200], "truck")], 1000.0)
+    assert len(t4.tracks) == 2, t4.tracks
+
+    # Appearance rescues a match the boxes miss - the low-frame-rate case.
+    emb = [1.0, 0.0, 0.0]
+    t5 = CameraTracker("cam05")
+    t5.update([Det([0, 0, 100, 100], "car", emb)], 0.0)
+    t5.update([Det([400, 0, 500, 100], "car", emb)], 1000.0)
+    assert len(t5.tracks) == 1, "identical appearance should associate"
+
+    # Direction is reported once the vehicle has moved far enough to mean
+    # something, while still overlapping enough to stay one track.
+    t6 = CameraTracker("cam06")
+    t6.update([Det([0, 0, 100, 100])], 0.0)
+    t6.update([Det([60, 0, 160, 100])], 1000.0)
+    assert len(t6.tracks) == 1, t6.tracks
+    track = list(t6.tracks.values())[0]
+    assert track.direction() == "right", track.path
+    assert track.dwell_s == 1.0, track.dwell_s
+
+    # Without overlap and without an embedding, a large jump is treated as a
+    # new vehicle rather than guessed at. This is why embeddings matter at low
+    # frame rates: appearance is what holds a track together across the gap.
+    t6b = CameraTracker("cam06b")
+    t6b.update([Det([0, 0, 100, 100])], 0.0)
+    t6b.update([Det([600, 0, 700, 100])], 1000.0)
+    assert len(t6b.tracks) == 2, t6b.tracks
+
+    # A stationary vehicle has no direction.
+    t7 = CameraTracker("cam07")
+    t7.update([Det([0, 0, 100, 100])], 0.0)
+    t7.update([Det([2, 1, 102, 101])], 1000.0)
+    assert list(t7.tracks.values())[0].direction() is None
+
+    # Stale tracks expire rather than accumulating forever.
+    t8 = CameraTracker("cam08")
+    t8.update([Det([0, 0, 100, 100])], 0.0)
+    t8.update([Det([900, 900, 950, 950])], 30_000.0)
+    assert len(t8.tracks) == 1, "the original track should have expired"
+
+    # The cap bounds live tracks, and it is the least recently seen that go.
+    t9 = CameraTracker("cam09")
+    for i in range(MAX_TRACKS_PER_CAMERA + 50):
+        # Boxes far enough apart never associate, so each detection opens a
+        # track; staggered PTS gives them a well-defined recency order.
+        t9.update([Det([i * 200, 0, i * 200 + 20, 20])], float(i))
+    # Trimming happens on expiry, so the cap may be exceeded by the current
+    # frame's own detections until the next update; one expiry settles it.
+    t9._expire(float(MAX_TRACKS_PER_CAMERA + 50))
+    assert len(t9.tracks) == MAX_TRACKS_PER_CAMERA, len(t9.tracks)
+    assert t9.dropped_tracks == 50, t9.dropped_tracks
+    assert min(tr.last_pts_ms for tr in t9.tracks.values()) == 50.0, (
+        "the oldest tracks should be the ones dropped")
+    assert t9.stats()["dropped_tracks"] == 50
+
+    # A loop cut keeps cumulative observation but restarts the per-loop figure,
+    # so a count inflated by replays is visible rather than silent.
+    t10 = CameraTracker("cam10")
+    t10.update([Det([100, 100, 200, 200])], 0.0)
+    t10.update([Det([110, 100, 210, 200])], 1000.0)
+    assert t10.total_count == 1 and t10.counted_this_loop == 1
+    t10.reset()
+    assert t10.total_count == 1, "cumulative observation is real and must survive"
+    assert t10.counted_this_loop == 0, t10.counted_this_loop
+    assert t10.loops_seen == 1, t10.loops_seen
+    t10.update([Det([100, 100, 200, 200])], 0.0)
+    t10.update([Det([110, 100, 210, 200])], 1000.0)
+    st = t10.stats()
+    assert st["total_counted"] == 2 and st["counted_this_loop"] == 1, st
+    assert st["loops_seen"] == 1, st
+
+    print("tracking self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/analytics/zones.py b/netra/analytics/zones.py
new file mode 100644
index 0000000..5bc5763
--- /dev/null
+++ b/netra/analytics/zones.py
@@ -0,0 +1,259 @@
+"""Zone rules: intrusion, line crossing, and loitering.
+
+A camera watching a godown gate, a toll lane or a restricted compound is not
+asking "what vehicles are here" but "did anything enter where it should not,
+and when". These are the analytics that make a camera useful when plate
+recognition is impossible, which on this grid is most of them.
+
+Three rule types, all evaluated against tracked objects rather than raw
+detections, so an object entering a zone raises one alert rather than one per
+frame:
+
+    intrusion   object appears inside a polygon
+    crossing    object's path crosses a line, with the direction it crossed
+    loitering   object remains inside a polygon beyond a dwell threshold
+
+Zones are stored per camera in normalised 0-1 coordinates, so a rule survives
+the camera being re-encoded at a different resolution - which matters on a grid
+carrying five different resolutions.
+"""
+from __future__ import annotations
+
+import logging
+from dataclasses import dataclass, field
+from datetime import datetime, timezone
+
+log = logging.getLogger(__name__)
+
+RULE_TYPES = ("intrusion", "crossing", "loitering")
+
+
+def point_in_polygon(x: float, y: float, polygon: list) -> bool:
+    """Ray-casting test. Polygon is [[x, y], ...] in the same units as x, y."""
+    inside = False
+    n = len(polygon)
+    if n < 3:
+        return False
+    j = n - 1
+    for i in range(n):
+        xi, yi = polygon[i]
+        xj, yj = polygon[j]
+        if (yi > y) != (yj > y):
+            x_cross = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
+            if x < x_cross:
+                inside = not inside
+        j = i
+    return inside
+
+
+def segments_intersect(p1, p2, p3, p4) -> bool:
+    """Do segment p1-p2 and segment p3-p4 cross?"""
+    def orient(a, b, c) -> float:
+        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
+
+    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
+    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
+    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
+        return True
+    return False
+
+
+def crossing_side(line: list, point) -> str:
+    """Which side of a directed line a point falls on."""
+    (x1, y1), (x2, y2) = line[0], line[1]
+    d = (x2 - x1) * (point[1] - y1) - (y2 - y1) * (point[0] - x1)
+    return "a" if d > 0 else "b"
+
+
+@dataclass
+class Zone:
+    """One rule on one camera."""
+    zone_id: str
+    camera_id: str
+    name: str
+    rule: str                       # intrusion | crossing | loitering
+    #: normalised 0-1 coordinates: polygon for intrusion/loitering, two points
+    #: for crossing
+    points: list
+    classes: list = field(default_factory=list)   # empty means any class
+    severity: str = "medium"
+    dwell_s: float = 30.0           # loitering only
+    active: bool = True
+
+    def applies_to(self, vehicle_class: str) -> bool:
+        return not self.classes or vehicle_class in self.classes
+
+    def to_pixels(self, width: int, height: int) -> list:
+        return [[p[0] * width, p[1] * height] for p in self.points]
+
+
+@dataclass
+class ZoneEvent:
+    zone: Zone
+    track_id: int
+    camera_id: str
+    vehicle_class: str
+    rule: str
+    detail: str
+    at: datetime
+    direction: str | None = None
+
+
+class ZoneEngine:
+    """Evaluates zone rules against tracked objects."""
+
+    def __init__(self):
+        self.zones: dict[str, list[Zone]] = {}   # camera_id -> zones
+        #: (zone_id, track_id) already reported, so one entry alerts once
+        self._fired: set = set()
+        #: (zone_id, track_id) -> which side of a crossing line it was last on
+        self._sides: dict = {}
+        self.events_raised = 0
+
+    def set_zones(self, camera_id: str, zones: list[Zone]) -> None:
+        self.zones[camera_id] = [z for z in zones if z.active]
+
+    def reset_camera(self, camera_id: str) -> None:
+        """Forget per-track state after a loop cut; track ids restart."""
+        for key in [k for k in self._fired if k[0].startswith(f"{camera_id}:")]:
+            self._fired.discard(key)
+        for key in [k for k in self._sides if k[0].startswith(f"{camera_id}:")]:
+            self._sides.pop(key, None)
+
+    def evaluate(self, camera_id: str, tracks: list, frame_size) -> list[ZoneEvent]:
+        """Check every active track on this camera against its zones."""
+        zones = self.zones.get(camera_id)
+        if not zones or not tracks:
+            return []
+
+        width, height = frame_size
+        events: list[ZoneEvent] = []
+        now = datetime.now(timezone.utc)
+
+        for zone in zones:
+            pts = zone.to_pixels(width, height)
+            for track in tracks:
+                if not zone.applies_to(track.vehicle_class):
+                    continue
+                key = (zone.zone_id, track.track_id)
+
+                if zone.rule == "intrusion":
+                    if key in self._fired or not track.path:
+                        continue
+                    if point_in_polygon(*track.path[-1], pts):
+                        self._fired.add(key)
+                        events.append(ZoneEvent(
+                            zone=zone, track_id=track.track_id,
+                            camera_id=camera_id,
+                            vehicle_class=track.vehicle_class, rule="intrusion",
+                            detail=f"{track.vehicle_class} entered {zone.name}",
+                            at=now, direction=track.direction()))
+
+                elif zone.rule == "loitering":
+                    if key in self._fired or not track.path:
+                        continue
+                    if (point_in_polygon(*track.path[-1], pts)
+                            and track.dwell_s >= zone.dwell_s):
+                        self._fired.add(key)
+                        events.append(ZoneEvent(
+                            zone=zone, track_id=track.track_id,
+                            camera_id=camera_id,
+                            vehicle_class=track.vehicle_class, rule="loitering",
+                            detail=(f"{track.vehicle_class} remained in "
+                                    f"{zone.name} for {track.dwell_s:.0f}s"),
+                            at=now, direction=track.direction()))
+
+                elif zone.rule == "crossing":
+                    if len(track.path) < 2 or key in self._fired:
+                        continue
+                    if segments_intersect(track.path[-2], track.path[-1],
+                                          pts[0], pts[1]):
+                        side = crossing_side(pts, track.path[-1])
+                        self._fired.add(key)
+                        events.append(ZoneEvent(
+                            zone=zone, track_id=track.track_id,
+                            camera_id=camera_id,
+                            vehicle_class=track.vehicle_class, rule="crossing",
+                            detail=(f"{track.vehicle_class} crossed {zone.name} "
+                                    f"towards side {side}"),
+                            at=now, direction=track.direction()))
+
+        self.events_raised += len(events)
+        return events
+
+
+def _self_check() -> None:
+    """Geometry decides whether an intrusion alert fires, so the boundary cases
+    matter more than the obvious ones."""
+    square = [[0, 0], [100, 0], [100, 100], [0, 100]]
+    assert point_in_polygon(50, 50, square)
+    assert not point_in_polygon(150, 50, square)
+    assert not point_in_polygon(-1, 50, square)
+    assert not point_in_polygon(50, 150, square)
+    assert not point_in_polygon(5, 5, [[0, 0], [10, 0]])   # not a polygon
+
+    # A line from left to right crosses a vertical line between them.
+    assert segments_intersect((0, 50), (100, 50), (50, 0), (50, 100))
+    assert not segments_intersect((0, 0), (10, 0), (0, 50), (10, 50))
+
+    class FakeTrack:
+        def __init__(self, tid, path, cls="car", dwell=0.0):
+            self.track_id, self.path, self.vehicle_class = tid, path, cls
+            self.dwell_s = dwell
+
+        def direction(self):
+            return None
+
+    engine = ZoneEngine()
+    zone = Zone(zone_id="cam01:z1", camera_id="cam01", name="Gate",
+                rule="intrusion", points=[[0, 0], [0.5, 0], [0.5, 0.5], [0, 0.5]])
+    engine.set_zones("cam01", [zone])
+
+    # Inside the polygon fires once, not on every subsequent frame.
+    inside = FakeTrack(1, [(100, 100)])
+    ev = engine.evaluate("cam01", [inside], (1000, 1000))
+    assert len(ev) == 1 and ev[0].rule == "intrusion", ev
+    assert engine.evaluate("cam01", [inside], (1000, 1000)) == []
+
+    # Outside the polygon never fires.
+    outside = FakeTrack(2, [(900, 900)])
+    assert engine.evaluate("cam01", [outside], (1000, 1000)) == []
+
+    # Class filtering is honoured.
+    truck_only = Zone(zone_id="cam02:z1", camera_id="cam02", name="Dock",
+                      rule="intrusion", points=[[0, 0], [1, 0], [1, 1], [0, 1]],
+                      classes=["truck"])
+    engine.set_zones("cam02", [truck_only])
+    assert engine.evaluate("cam02", [FakeTrack(3, [(50, 50)], "car")],
+                           (100, 100)) == []
+    assert len(engine.evaluate("cam02", [FakeTrack(4, [(50, 50)], "truck")],
+                               (100, 100))) == 1
+
+    # Loitering needs the dwell threshold, not merely presence.
+    loiter = Zone(zone_id="cam03:z1", camera_id="cam03", name="Yard",
+                  rule="loitering", points=[[0, 0], [1, 0], [1, 1], [0, 1]],
+                  dwell_s=30)
+    engine.set_zones("cam03", [loiter])
+    assert engine.evaluate("cam03", [FakeTrack(5, [(50, 50)], "car", 10)],
+                           (100, 100)) == []
+    assert len(engine.evaluate("cam03", [FakeTrack(6, [(50, 50)], "car", 45)],
+                               (100, 100))) == 1
+
+    # Crossing needs a path that actually intersects the line.
+    line = Zone(zone_id="cam04:z1", camera_id="cam04", name="Tripwire",
+                rule="crossing", points=[[0.5, 0.0], [0.5, 1.0]])
+    engine.set_zones("cam04", [line])
+    assert engine.evaluate("cam04", [FakeTrack(7, [(10, 50), (20, 50)])],
+                           (100, 100)) == []
+    assert len(engine.evaluate("cam04", [FakeTrack(8, [(40, 50), (60, 50)])],
+                               (100, 100))) == 1
+
+    # A loop cut clears per-track state, since track ids restart from 1.
+    engine.reset_camera("cam01")
+    assert len(engine.evaluate("cam01", [inside], (1000, 1000))) == 1
+
+    print("zones self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/api/app.py b/netra/api/app.py
index 501317e..a6340ed 100644
--- a/netra/api/app.py
+++ b/netra/api/app.py
@@ -3,28 +3,32 @@ from __future__ import annotations
 
 import asyncio
 import csv
 import io
 import json
 import logging
+import threading
 from datetime import datetime, timedelta, timezone
 
 from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
-from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
+from fastapi.responses import (HTMLResponse, JSONResponse, Response,
+                               StreamingResponse)
 from fastapi.staticfiles import StaticFiles
 from sqlalchemy import func, select
 from sqlalchemy.orm import joinedload
 
 from fastapi import Depends, Header
 
 from netra import config
+from netra.analytics.loop_index import has_embedding
 from netra.analytics.route import build_route
 from netra.core import auth
 from netra.core.db import SessionLocal, init_db
 from netra.core.geo import TIME_GROUPS, time_group
-from netra.core.models import Alert, AuditLog, Camera, Detection, WatchlistEntry
+from netra.core.models import (Alert, AuditLog, Camera, Detection,
+                               VehicleAttributeRow, WatchlistEntry)
 from netra.pipeline import PIPELINE
 
 logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s %(levelname)s %(name)s: %(message)s")
 log = logging.getLogger("netra.api")
 
@@ -154,12 +158,116 @@ def gap_analysis():
             f"Cross-camera route reconstruction is valid only within a shared "
             f"recording session; {len(TIME_GROUPS)} such groups exist in this grid.",
         ],
     }
 
 
+#: A snapshot opens an RTSP connection, so it is cached: an operator placing
+#: points in the zone editor clicks many times on one camera and must not cost
+#: one connection per click. Short enough that the still stays current.
+SNAPSHOT_TTL_S = 30.0
+SNAPSHOT_TIMEOUT_S = 25.0
+_snapshots: dict[str, tuple[float, bytes]] = {}
+
+#: One lock per camera, so concurrent callers for the same camera wait on the
+#: grab already running instead of each starting their own. Without it the
+#: cache only protects the warm path: five clicks on "Load still frame" before
+#: the first returns would be five ffmpeg processes holding five threadpool
+#: threads for seventeen seconds apiece, which is how a snapshot request ends
+#: up starving /api/pipeline/status. The registry of locks needs its own lock
+#: because it is filled lazily from several request threads.
+_snapshot_locks: dict[str, threading.Lock] = {}
+_snapshot_locks_guard = threading.Lock()
+
+
+def _snapshot_lock(camera_id: str) -> threading.Lock:
+    with _snapshot_locks_guard:
+        return _snapshot_locks.setdefault(camera_id, threading.Lock())
+
+
+def _cached_snapshot(camera_id: str) -> bytes | None:
+    import time as _time
+    now = _time.time()
+    # Evict on read. Nothing else ever removes an entry, so the dict is bounded
+    # by the camera set only while camera ids are stable - a churning id space
+    # (participant-supplied feeds are onboarded under generated ids) would grow
+    # it without limit, holding a full-resolution JPEG per id for the life of
+    # the process. Four TTLs is well past any possible reuse and leaves the
+    # warm path untouched.
+    cutoff = now - SNAPSHOT_TTL_S * 4
+    for stale in [k for k, (at, _) in _snapshots.items() if at < cutoff]:
+        _snapshots.pop(stale, None)
+        # The lock registry is dropped alongside, but only for a camera with
+        # no grab in flight: replacing a held lock would let the next caller
+        # start a second ffmpeg against the same camera, which is the exact
+        # thing the registry exists to prevent.
+        with _snapshot_locks_guard:
+            lock = _snapshot_locks.get(stale)
+            if lock is not None and not lock.locked():
+                _snapshot_locks.pop(stale, None)
+    hit = _snapshots.get(camera_id)
+    if hit and (now - hit[0]) < SNAPSHOT_TTL_S:
+        return hit[1]
+    return None
+
+
+def _grab_snapshot(camera_id: str) -> bytes:
+    """One JPEG off the camera, bounded in time. Caller holds the camera lock."""
+    import os
+    import subprocess
+    import tempfile
+    import time as _time
+
+    fd, path = tempfile.mkstemp(suffix=".jpg")
+    os.close(fd)
+    try:
+        subprocess.run(
+            ["ffmpeg", "-v", "error", "-rtsp_transport", "tcp",
+             "-i", config.rtsp_url(camera_id), "-frames:v", "1",
+             "-q:v", "4", path, "-y"],
+            capture_output=True, timeout=SNAPSHOT_TIMEOUT_S)
+        data = open(path, "rb").read() if os.path.exists(path) else b""
+    except Exception as exc:                          # timeout, no ffmpeg, ...
+        log.warning("snapshot failed for %s: %s", camera_id, exc)
+        data = b""
+    finally:
+        if os.path.exists(path):
+            os.unlink(path)
+
+    if len(data) < 1000:
+        raise HTTPException(
+            503, f"could not grab a frame from {camera_id} within "
+                 f"{SNAPSHOT_TIMEOUT_S:.0f}s")
+    _snapshots[camera_id] = (_time.time(), data)
+    return data
+
+
+@app.get("/api/cameras/{camera_id}/snapshot")
+def camera_snapshot(camera_id: str, refresh: bool = False,
+                    _p=Depends(require("read"))):
+    """One still frame from a camera, for placing zone rules on.
+
+    Points are stored normalised, so the still only has to show the operator
+    the scene; it does not have to match the resolution the pipeline decodes.
+    """
+    with SessionLocal() as db:
+        if not db.get(Camera, camera_id):
+            raise HTTPException(404, "camera not found")
+
+    data = None if refresh else _cached_snapshot(camera_id)
+    if data is None:
+        with _snapshot_lock(camera_id):
+            # Re-checked inside the lock: whoever we queued behind has just
+            # filled the cache, and using their frame is the whole point of
+            # having queued.
+            data = _cached_snapshot(camera_id) or _grab_snapshot(camera_id)
+
+    return Response(content=data, media_type="image/jpeg",
+                    headers={"Cache-Control": "no-store"})
+
+
 # -------------------------------------------------------------- detections --
 @app.get("/api/detections")
 def list_detections(camera_id: str | None = None, plate: str | None = None,
                     vehicle_class: str | None = None, colour: str | None = None,
                     since_minutes: int | None = None,
                     limit: int = Query(100, le=1000), offset: int = 0):
@@ -175,28 +283,119 @@ def list_detections(camera_id: str | None = None, plate: str | None = None,
             q = q.filter(Detection.colour == colour)
         if since_minutes:
             cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
             q = q.filter(Detection.wall_time >= cutoff)
         total = q.count()
         rows = q.order_by(Detection.wall_time.desc()).offset(offset).limit(limit).all()
+        # One extra query for the page rather than a join: attributes are
+        # sparse - most detections have none - and a left join would carry the
+        # caption text of every row through the main query for the few that do.
+        described = _attributes_for([d.id for d in rows], db)
 
         items = [{
             "id": d.id, "camera_id": d.camera_id,
             "camera_name": d.camera.name if d.camera else None,
             "lat": d.camera.lat if d.camera else None,
             "lon": d.camera.lon if d.camera else None,
             "at": d.wall_time.isoformat(),
             "pts_ms": d.pts_ms,
             "vehicle_class": d.vehicle_class, "confidence": round(d.confidence, 3),
             "colour": d.colour, "plate_text": d.plate_text,
             "plate_conf": round(d.plate_conf, 3) if d.plate_conf else None,
+            "plate_chars": d.plate_chars,
+            #: how many OCR reads agreed on plate_text. One is a lone guess,
+            #: and the console shows the count so the two are not read alike.
+            "plate_votes": d.plate_votes,
             "evidence": d.evidence_path, "bbox": d.bbox,
+            "track_id": d.track_id,
+            "scene_time": d.scene_time.isoformat() if d.scene_time else None,
+            #: false means the scene time was anchored on a single overlay
+            #: reading nothing ever confirmed; no elapsed-time claim is made
+            #: from those rows. See netra/core/timing.py.
+            "scene_time_corroborated": bool(d.scene_time_corroborated),
+            "attributes": described.get(d.id),
         } for d in rows]
     return {"total": total, "count": len(items), "items": items}
 
 
+def _attribute_dict(row) -> dict:
+    """Serialise one stored description. Provenance travels with it."""
+    return {"body_type": row.body_type, "colour": row.colour,
+            "tinted_windows": row.tinted_windows, "wheels": row.wheels,
+            "roof_rack": row.roof_rack, "markings": row.markings or [],
+            "damage": row.damage or [], "description": row.description,
+            "raw_caption": row.raw_caption, "model": row.model,
+            "confidence": row.confidence, "source": row.source,
+            "at": row.created_at.isoformat() if row.created_at else None,
+            "note": ATTRIBUTE_NOTE}
+
+
+#: Attached to every description the API returns. A caption is a description of
+#: a crop, and the difference between that and an identification is the whole
+#: honesty position of this platform.
+ATTRIBUTE_NOTE = ("A vision-language description of the evidence crop, for "
+                  "search and for an operator to read. It describes what the "
+                  "vehicle looks like; it does not identify the vehicle.")
+
+
+def _attributes_for(detection_ids: list[int], db) -> dict:
+    """detection_id -> serialised attributes, for the ids that have any."""
+    if not detection_ids:
+        return {}
+    rows = (db.query(VehicleAttributeRow)
+            .filter(VehicleAttributeRow.detection_id.in_(detection_ids)).all())
+    return {r.detection_id: _attribute_dict(r) for r in rows}
+
+
+@app.post("/api/detections/{detection_id}/describe")
+def describe_detection(detection_id: int, refresh: bool = False,
+                       _p=Depends(require("read"))):
+    """Describe one vehicle in words, on request.
+
+    The operator-request tier. Extraction is expensive enough that the pipeline
+    only runs it unprompted on alerts, zone events and the largest vehicle on
+    an escalated camera; this is how an officer gets a description of any other
+    detection - synchronously, because they are waiting for it.
+    """
+    from netra.analytics import attributes as attrs
+    from netra.pipeline import evidence_file, store_attributes
+
+    with SessionLocal() as db:
+        det = db.get(Detection, detection_id)
+        if det is None:
+            raise HTTPException(404, "detection not found")
+        existing = db.query(VehicleAttributeRow).filter(
+            VehicleAttributeRow.detection_id == detection_id).one_or_none()
+        if existing is not None and not refresh:
+            return {"detection_id": detection_id, "cached": True,
+                    "attributes": _attribute_dict(existing)}
+        evidence_path = det.evidence_path
+
+    path = evidence_file(evidence_path)
+    if path is None:
+        raise HTTPException(
+            404, "no evidence crop is stored for this detection, so there is "
+                 "nothing to describe")
+
+    result = attrs.describe_image_file(path)
+    if not result.raw_caption:
+        # The model is unavailable or failed. Saying so is the honest answer;
+        # storing an all-unknown row would look like a description that found
+        # nothing, which is a different thing entirely.
+        raise HTTPException(503, result.description)
+
+    store_attributes(detection_id, result, "operator")
+    _audit("detection.describe", target=str(detection_id),
+           detail={"confidence": result.confidence})
+    with SessionLocal() as db:
+        row = db.query(VehicleAttributeRow).filter(
+            VehicleAttributeRow.detection_id == detection_id).one()
+        return {"detection_id": detection_id, "cached": False,
+                "attributes": _attribute_dict(row)}
+
+
 @app.get("/api/detections/stats")
 def detection_stats():
     with SessionLocal() as db:
         total = db.query(func.count(Detection.id)).scalar() or 0
         with_plate = db.query(func.count(Detection.id)).filter(
             Detection.plate_text.isnot(None)).scalar() or 0
@@ -280,12 +479,13 @@ def delete_watchlist(entry_id: int, _p=Depends(require("watchlist"))):
 def list_alerts(limit: int = Query(50, le=500), acknowledged: bool | None = None):
     with SessionLocal() as db:
         q = db.query(Alert)
         if acknowledged is not None:
             q = q.filter(Alert.acknowledged.is_(acknowledged))
         rows = q.order_by(Alert.created_at.desc()).limit(limit).all()
+        described = _attributes_for([a.detection_id for a in rows], db)
         out = []
         for a in rows:
             det = db.get(Detection, a.detection_id)
             wl = db.get(WatchlistEntry, a.watchlist_id)
             cam = db.get(Camera, a.camera_id)
             out.append({
@@ -298,12 +498,16 @@ def list_alerts(limit: int = Query(50, le=500), acknowledged: bool | None = None
                 "acknowledged": a.acknowledged,
                 "plate_observed": det.plate_text if det else None,
                 "plate_watchlist": wl.plate if wl else None,
                 "category": wl.category if wl else None,
                 "case_ref": wl.case_ref if wl else None,
                 "evidence": det.evidence_path if det else None,
+                "detection_id": a.detection_id,
+                # Present where the alert path already produced one; the card
+                # offers a Describe button where it has not.
+                "attributes": described.get(a.detection_id),
             })
     return out
 
 
 @app.post("/api/alerts/{alert_id}/acknowledge")
 def acknowledge(alert_id: int, _p=Depends(require("acknowledge"))):
@@ -461,13 +665,14 @@ def similar_vehicles(detection_id: int, limit: int = Query(25, le=100),
 
     This is the answer to "trace this vehicle" when no plate is readable, which
     on this grid is the normal case. Results are ranked candidates carrying
     their similarity score, ordered in time, and filtered for space-time
     plausibility - not assertions of identity.
     """
-    from netra.analytics.reid import similarity
+    from netra.analytics.reid import (attribute_agreement, flag_ambiguity,
+                                      similarity)
     from netra.core.geo import haversine_km, time_group
     from netra.analytics.matching import spacetime_plausible
 
     with SessionLocal() as db:
         query = db.get(Detection, detection_id)
         if query is None:
@@ -476,13 +681,15 @@ def similar_vehicles(detection_id: int, limit: int = Query(25, le=100),
             raise HTTPException(
                 400, "this detection has no appearance embedding")
 
         qcam = db.get(Camera, query.camera_id)
         others = (db.query(Detection).options(joinedload(Detection.camera))
                   .filter(Detection.id != detection_id,
-                          Detection.embedding.isnot(None)).all())
+                          has_embedding()).all())
+
+        query_attrs = _attributes_for([detection_id], db).get(detection_id)
 
         scored = []
         for det in others:
             sim = similarity(query.embedding, det.embedding)
             if sim < min_similarity:
                 continue
@@ -507,38 +714,70 @@ def similar_vehicles(detection_id: int, limit: int = Query(25, le=100),
                 "at": det.wall_time.isoformat(),
                 "vehicle_class": det.vehicle_class,
                 "colour": det.colour,
                 "plate_text": det.plate_text,
                 "evidence": det.evidence_path,
                 "similarity": round(sim, 4),
+                "presented_similarity": round(sim, 4),
+                "attributes": None,
+                "attribute_adjustment": None,
                 "distance_km": round(km, 2),
                 "elapsed_s": round(secs, 1),
                 "plausible": ok,
                 "plausibility": why,
                 "same_time_group": time_group(det.camera_id) == time_group(query.camera_id),
             })
 
+        # Ordered on raw appearance, not on the adjusted figure: the ranking is
+        # what appearance evidence says, and a description is only allowed to
+        # qualify it. Ambiguity is judged on the same raw scores for the same
+        # reason.
         scored.sort(key=lambda x: x["similarity"], reverse=True)
-        matches = scored[:limit]
+        # Two vehicles that look alike score alike, so where the top results
+        # are separated by less than the appearance model can resolve, every
+        # one of them is flagged. The console needs this to avoid rendering a
+        # coin-toss as an identification.
+        matches = flag_ambiguity(scored[:limit])
+
+        # The third signal, applied last and only to the candidates appearance
+        # has already chosen: it can qualify a match but never create one, and
+        # looking attributes up only for the returned page keeps this to one
+        # bounded query rather than one over every embedded detection.
+        attr_rows = _attributes_for([m["detection_id"] for m in matches], db)
+        for m in matches:
+            m["attributes"] = attr_rows.get(m["detection_id"])
+            agreement = attribute_agreement(query_attrs, m["attributes"])
+            if not agreement:
+                continue
+            # Presented separately from `similarity`, which stays the raw
+            # cosine: an operator must be able to see what appearance alone
+            # said and what the description did to it.
+            m["attribute_adjustment"] = agreement
+            m["presented_similarity"] = round(
+                max(0.0, min(1.0, m["similarity"] + agreement["delta"])), 4)
 
         origin = {
             "detection_id": query.id, "camera_id": query.camera_id,
             "camera_name": qcam.name if qcam else None,
             "lat": qcam.lat if qcam else None, "lon": qcam.lon if qcam else None,
             "at": query.wall_time.isoformat(),
             "vehicle_class": query.vehicle_class, "colour": query.colour,
             "evidence": query.evidence_path,
+            "attributes": query_attrs,
         }
 
     _audit("vehicle.similar", target=str(detection_id),
            detail={"matches": len(matches)})
     return {
         "query": origin,
         "matches": matches,
         "plausible_matches": [m for m in matches if m["plausible"]],
-        "method": "appearance re-identification (ResNet-18 512-d, cosine)",
+        "method": ("appearance re-identification (ResNet-18 512-d, cosine), "
+                   "corroborated where both sightings carry a "
+                   "vision-language description"),
+        "ambiguous": any(m.get("ambiguous") for m in matches),
         "note": ("Ranked candidates for operator confirmation, not identification. "
                  "Appearance evidence alone does not establish that two sightings "
                  "are the same vehicle."),
     }
 
 
@@ -639,6 +878,380 @@ async def assistant(request: Request):
     if not isinstance(body, dict):
         raise HTTPException(400, "request body must be a JSON object")
     question = (body.get("question") or "").strip()
     result = ask(question)
     _audit("assistant.ask", target=question[:120])
     return result
+
+
+# ------------------------------------------------------------------ zones --
+@app.get("/api/zones")
+def list_zones(camera_id: str | None = None):
+    """Spatial rules configured on cameras."""
+    from netra.core.models import ZoneRule
+    with SessionLocal() as db:
+        q = db.query(ZoneRule)
+        if camera_id:
+            q = q.filter(ZoneRule.camera_id == camera_id)
+        return [{
+            "id": z.id, "camera_id": z.camera_id, "name": z.name,
+            "rule": z.rule, "points": z.points, "classes": z.classes or [],
+            "severity": z.severity, "dwell_s": z.dwell_s, "active": z.active,
+        } for z in q.order_by(ZoneRule.camera_id, ZoneRule.id).all()]
+
+
+@app.post("/api/zones")
+async def create_zone(request: Request, _p=Depends(require("onboard"))):
+    """Define a rule. Points are normalised 0-1 so the rule survives a
+    resolution change on the source camera."""
+    from netra.analytics.zones import RULE_TYPES
+    from netra.core.models import ZoneRule
+
+    body = await request.json()
+    rule = body.get("rule", "intrusion")
+    if rule not in RULE_TYPES:
+        raise HTTPException(400, f"rule must be one of {RULE_TYPES}")
+
+    points = body.get("points") or []
+    needed = 2 if rule == "crossing" else 3
+    if len(points) < needed:
+        raise HTTPException(
+            400, f"a {rule} rule needs at least {needed} points")
+    for p in points:
+        if len(p) != 2 or not all(0.0 <= float(v) <= 1.0 for v in p):
+            raise HTTPException(400, "points must be normalised [x, y] in 0..1")
+
+    with SessionLocal() as db:
+        if not db.get(Camera, body.get("camera_id")):
+            raise HTTPException(404, "camera not found")
+        z = ZoneRule(
+            camera_id=body["camera_id"], name=body.get("name", "Zone"),
+            rule=rule, points=points, classes=body.get("classes") or [],
+            severity=body.get("severity", "medium"),
+            dwell_s=float(body.get("dwell_s", 30.0)))
+        db.add(z)
+        db.commit()
+        db.refresh(z)
+        zone_id = z.id
+
+    PIPELINE.reload_zone_rules()
+    _audit("zone.create", target=f"{body['camera_id']}:{zone_id}")
+    return {"id": zone_id, "camera_id": body["camera_id"], "rule": rule}
+
+
+@app.delete("/api/zones/{zone_id}")
+def delete_zone(zone_id: int, _p=Depends(require("onboard"))):
+    from netra.core.models import ZoneRule
+    with SessionLocal() as db:
+        z = db.get(ZoneRule, zone_id)
+        if not z:
+            raise HTTPException(404, "not found")
+        db.delete(z)
+        db.commit()
+    PIPELINE.reload_zone_rules()
+    _audit("zone.delete", target=str(zone_id))
+    return {"deleted": zone_id}
+
+
+@app.get("/api/zones/events")
+def zone_events(limit: int = Query(100, le=500), camera_id: str | None = None):
+    from netra.core.models import ZoneEventRow, ZoneRule
+    with SessionLocal() as db:
+        q = db.query(ZoneEventRow)
+        if camera_id:
+            q = q.filter(ZoneEventRow.camera_id == camera_id)
+        rows = q.order_by(ZoneEventRow.at.desc()).limit(limit).all()
+        out = []
+        for e in rows:
+            rule = db.get(ZoneRule, e.zone_rule_id)
+            cam = db.get(Camera, e.camera_id)
+            out.append({
+                "id": e.id, "at": e.at.isoformat(), "camera_id": e.camera_id,
+                "camera_name": cam.name if cam else None,
+                "lat": cam.lat if cam else None, "lon": cam.lon if cam else None,
+                "zone": rule.name if rule else None, "rule": e.rule,
+                "object_class": e.object_class, "direction": e.direction,
+                "detail": e.detail, "severity": e.severity,
+                "evidence": e.evidence_path, "acknowledged": e.acknowledged,
+            })
+    return out
+
+
+# -------------------------------------------------------- traffic analytics --
+@app.get("/api/traffic/live")
+def traffic_live():
+    """Current per-camera counts, class mix, direction split and dwell."""
+    return {"cameras": PIPELINE.engine.trackers.stats(),
+            "zone_events": PIPELINE.stats.get("zone_events", 0)}
+
+
+@app.post("/api/traffic/snapshot")
+def traffic_snapshot(_p=Depends(require("pipeline"))):
+    """Write the current counters into a time bucket for trend reporting."""
+    written = PIPELINE.flush_traffic_stats()
+    _audit("traffic.snapshot", detail={"cameras": written})
+    return {"buckets_written": written}
+
+
+@app.get("/api/traffic/history")
+def traffic_history(camera_id: str | None = None, limit: int = Query(200, le=1000)):
+    from netra.core.models import TrafficStat
+    with SessionLocal() as db:
+        q = db.query(TrafficStat)
+        if camera_id:
+            q = q.filter(TrafficStat.camera_id == camera_id)
+        rows = q.order_by(TrafficStat.bucket_start.desc()).limit(limit).all()
+        return [{
+            "camera_id": r.camera_id, "at": r.bucket_start.isoformat(),
+            # `total` is the traffic during this bucket; `cumulative_total` is
+            # the camera's running figure, which spans every replay of a
+            # looping recording and is only honest read beside `loops_seen`.
+            "total": r.total, "cumulative_total": r.cumulative_total,
+            "loops_seen": r.loops_seen, "counts_by_class": r.counts_by_class,
+            "directions": r.directions, "mean_dwell_s": r.mean_dwell_s,
+        } for r in rows]
+
+
+# ------------------------------------------------------------- baselines --
+#: History read per baseline request. Learning is bounded rather than
+#: unbounded: an operator refreshing a dashboard must never pull the whole
+#: traffic table and starve the detection threads of the database.
+BASELINE_HISTORY_LIMIT = 5000
+
+
+def _load_baselines(camera_id: str | None, limit: int):
+    from netra.analytics import baseline
+    from netra.core.models import TrafficStat
+    with SessionLocal() as db:
+        q = db.query(TrafficStat)
+        if camera_id:
+            q = q.filter(TrafficStat.camera_id == camera_id)
+        rows = q.order_by(TrafficStat.bucket_start.desc()).limit(limit).all()
+    return baseline.learn(rows), len(rows)
+
+
+@app.get("/api/analytics/baselines")
+def analytics_baselines(camera_id: str | None = None,
+                        limit: int = Query(BASELINE_HISTORY_LIMIT, le=20000),
+                        _p=Depends(require("read"))):
+    """What each camera normally sees, per hour of the day, in UTC.
+
+    Baselines below the sample floor are returned too, marked insufficient:
+    knowing the platform cannot yet judge an hour is itself operational
+    information, and hiding those rows would imply coverage that does not exist.
+    """
+    from netra.analytics import baseline
+    learned, sampled = _load_baselines(camera_id, limit)
+    items = sorted((b.as_dict() for b in learned.values()),
+                   key=lambda b: (b["camera_id"], b["hour"]))
+    ready = sum(1 for b in items if b["sufficient"])
+    return {"buckets_read": sampled, "min_samples": baseline.MIN_SAMPLES,
+            "stdev_floor": baseline.STDEV_FLOOR, "hours_learned": len(items),
+            "hours_judgeable": ready, "baselines": items}
+
+
+@app.get("/api/analytics/anomalies")
+def analytics_anomalies(camera_id: str | None = None,
+                        limit: int = Query(BASELINE_HISTORY_LIMIT, le=20000),
+                        include_normal: bool = False,
+                        _p=Depends(require("read"))):
+    """Current per-camera readings judged against the learned norms.
+
+    The current reading is the most recent completed traffic bucket for each
+    camera, so it is measured the same way the baseline was - comparing a live
+    partial count against full-bucket norms would manufacture false quiets.
+
+    A camera whose newest bucket is older than
+    baseline.ANOMALY_MAX_BUCKET_AGE_S is reported as `stale` and not judged:
+    that reading describes when the feed stopped, not the road now.
+    """
+    from netra.analytics import baseline
+    from netra.core.models import TrafficStat
+    learned, sampled = _load_baselines(camera_id, limit)
+
+    with SessionLocal() as db:
+        q = db.query(TrafficStat)
+        if camera_id:
+            q = q.filter(TrafficStat.camera_id == camera_id)
+        recent = q.order_by(TrafficStat.bucket_start.desc()).limit(limit).all()
+
+    latest: dict[str, object] = {}
+    for r in recent:
+        latest.setdefault(r.camera_id, r)   # rows arrive newest first
+
+    found = baseline.detect_anomalies(learned, list(latest.values()),
+                                      include_normal=include_normal)
+    flagged = [a for a in found if a.anomalous]
+    stale = [a for a in found if a.status == "stale"]
+    return {"buckets_read": sampled, "cameras_assessed": len(latest),
+            "anomalies": len(flagged),
+            #: cameras whose newest bucket is too old to be a current reading
+            "stale": len(stale),
+            "max_bucket_age_s": baseline.ANOMALY_MAX_BUCKET_AGE_S,
+            "assessments": [a.as_dict() for a in found]}
+
+
+# ---------------------------------------------------------------- storage --
+@app.get("/api/storage")
+def storage(_p=Depends(require("read"))):
+    """What the evidence directory and detections table hold, against budget."""
+    from netra.core import retention
+    return retention.storage_report()
+
+
+@app.post("/api/storage/prune")
+def storage_prune(dry_run: bool = False, _p=Depends(require("manage"))):
+    """Bring evidence and detections back inside their configured budgets.
+
+    Guarded by `manage` and audited: this deletes evidence, and who asked for
+    that deletion is exactly the kind of thing an enquiry later asks about.
+    `dry_run` reports what would go without touching anything.
+    """
+    from netra.core import retention
+    evidence = retention.prune_evidence(dry_run=dry_run)
+    detections = retention.prune_detections(dry_run=dry_run)
+    _audit("storage.prune", detail={"dry_run": dry_run,
+                                    "files_deleted": evidence["deleted"],
+                                    "bytes_freed": evidence["bytes_freed"],
+                                    "rows_deleted": detections["deleted"],
+                                    "retained_protected":
+                                        evidence["retained_protected"]})
+    return {"evidence": evidence, "detections": detections,
+            "storage": retention.storage_report()}
+
+
+@app.get("/api/analytics/cloned-plates")
+def cloned_plates(min_confidence: float = Query(0.6, ge=0.0, le=0.99),
+                  limit: int = Query(50, ge=1, le=500)):
+    """Registration numbers seen in two places one vehicle could not have reached.
+
+    Read-only analysis over stored detections; every finding carries the
+    distance, elapsed time and implied speed behind it so an officer can check
+    the claim rather than take it on trust.
+    """
+    from netra.analytics.cloned_plate import find_clones
+    with SessionLocal() as db:
+        rows = (db.query(Detection).options(joinedload(Detection.camera))
+                .filter(Detection.plate_text.isnot(None)).all())
+        findings = find_clones(rows, min_confidence=min_confidence)
+    _audit("analytics.cloned_plates", detail={"findings": len(findings)})
+    return {
+        "findings": [f.to_dict() for f in findings[:limit]],
+        "count": len(findings),
+        "min_confidence": min_confidence,
+        "note": ("Findings are inferred from OCR reads on wide-area cameras and "
+                 "are never certain. Only sightings sharing a recording session "
+                 "are compared."),
+    }
+
+
+#: Mining is an appearance comparison across a whole indexed recording, so it
+#: runs at most once per group unprompted and otherwise only on request. What
+#: is recorded here is that a mine *happened*, not what it produced: a group
+#: that legitimately yields no journeys is indistinguishable from one never
+#: mined if only the output is remembered, and it would be re-mined on every
+#: poll for as long as it stayed empty — the starvation class the plan warns
+#: about. Per process, so a restart re-mines once and then settles.
+_journeys_mined_at: dict[str, float] = {}
+
+
+@app.get("/api/analytics/journeys")
+def mined_journeys(group: str = Query(..., min_length=3, max_length=64),
+                   min_similarity: float = Query(0.84, ge=0.5, le=0.99),
+                   min_hops: int = Query(2, ge=2, le=10),
+                   refresh: bool = False,
+                   limit: int = Query(50, ge=1, le=200),
+                   _p=Depends(require("read"))):
+    """Vehicles that genuinely appear on more than one camera of one recording.
+
+    The grid replays fixed recordings, and the cameras of a time group share
+    the clock burnt into their frames, so a chain built on scene time is a real
+    journey through the Government's own footage rather than a demonstration.
+
+    Mining always runs at the module's own thresholds and stores the full set;
+    `min_hops`, `min_similarity` and `limit` filter what this caller is shown.
+    They deliberately do not change what is mined, because the store is shared
+    and one narrow request must not shrink what every other reader sees.
+    """
+    import time as _time
+
+    from netra.analytics.loop_index import (DEFAULT_MIN_SIMILARITY,
+                                            MAX_JOURNEYS, exclusion_report,
+                                            find_journeys, persist_journeys,
+                                            stored_count, stored_journeys)
+    from netra.core.geo import TIME_GROUPS
+
+    if group not in TIME_GROUPS:
+        raise HTTPException(status_code=400,
+                            detail=f"unknown time group; known groups are "
+                                   f"{', '.join(sorted(TIME_GROUPS))}")
+
+    held = stored_count(group)
+    mined_before = _journeys_mined_at.get(group)
+    mined = skipped = False
+
+    if refresh or (not held and mined_before is None):
+        report: dict = {}
+        journeys = find_journeys(group, min_similarity=DEFAULT_MIN_SIMILARITY,
+                                 min_hops=2, limit=MAX_JOURNEYS, report=report)
+        persist_journeys(group, journeys,
+                         min_similarity=DEFAULT_MIN_SIMILARITY)
+        _journeys_mined_at[group] = _time.time()
+        held = len(journeys)
+        mined = True
+    elif not held:
+        # Mined already and found nothing, so this is a real answer rather than
+        # an empty one, and it is not re-derived on every poll.
+        skipped = True
+
+    # Always served from the store, so both paths return the identical shape.
+    rows = stored_journeys(group, limit=limit, min_hops=min_hops,
+                           min_similarity=min_similarity)
+    last_mined = _journeys_mined_at.get(group)
+
+    _audit("analytics.journeys", target=group,
+           detail={"journeys": len(rows), "mined": mined})
+    return {
+        "group": group,
+        "cameras": TIME_GROUPS[group],
+        "journeys": rows,
+        "count": len(rows),
+        "stored": held,
+        "mined_now": mined,
+        "mining_skipped": skipped,
+        "last_mined_at": (datetime.fromtimestamp(last_mined, tz=timezone.utc)
+                          .isoformat() if last_mined else None),
+        #: nothing re-mines on a timer, so there is no next time to report
+        "next_mine": "only on request, with refresh=true",
+        "mined_at_similarity": DEFAULT_MIN_SIMILARITY,
+        "filters_applied": {"min_hops": min_hops,
+                            "min_similarity": min_similarity,
+                            "applied_by": "filter"},
+        "index": exclusion_report(group),
+        "note": ("Appearance-based candidate journeys for operator "
+                 "confirmation, not identifications. Chained on the clock "
+                 "recorded in the video, never on capture time, and never "
+                 f"across recording sessions. Mined at similarity "
+                 f"{DEFAULT_MIN_SIMILARITY}; your thresholds filter these "
+                 "results rather than re-mining. A stricter threshold can "
+                 "also change which chains form, so pass refresh=true to "
+                 "re-mine. Nothing re-mines on its own once journeys are "
+                 "stored, so detections indexed since the mined_at timestamp "
+                 "are not represented until a refresh."
+                 + (" This group has been mined and produced no journeys: "
+                    "that is the answer, not a pending one. Index more "
+                    "cameras of this group, or re-mine with refresh=true."
+                    if skipped else "")),
+    }
+
+
+@app.get("/api/report", response_class=HTMLResponse)
+def output_report(hours: int = Query(24, ge=1, le=720)):
+    """Operational output report, printable to PDF from the browser.
+
+    This is the output report the submission asks for: detected vehicles and
+    plates with timestamps, watchlist matches with their reasoning, zone
+    events, per-camera activity, and the cameras measured as unable to deliver.
+    """
+    from netra.api.report import build_report
+    _audit("report.generate", detail={"hours": hours})
+    return HTMLResponse(build_report(hours=hours))
diff --git a/netra/api/assistant.py b/netra/api/assistant.py
index 15b74e2..08e3b90 100644
--- a/netra/api/assistant.py
+++ b/netra/api/assistant.py
@@ -6,12 +6,18 @@ plain language instead of navigating to the right screen and filtering it.
 
 Every answer is produced from a database query and carries the records it was
 derived from. Nothing is generated or inferred: in a policing context an
 assistant that invents a plausible-sounding number is worse than no assistant,
 so unrecognised questions say so and list what can be asked instead.
 
+Entity mentions are resolved lexically (`netra/api/retrieval.py`) before the
+query runs, so "the junagad bypass camera" reaches the right registry row. That
+resolution decides *which* row is queried and never what the row contains, and
+whenever it narrows an answer the answer says which entity was inferred - an
+operator who meant a different camera has to be able to see the substitution.
+
 ponytail: intent matching on keywords rather than a language model. It needs no
 API key, no network call and no per-query cost, and the question space in a
 control room is small and repetitive. `LLM_HINT` below marks where a model
 would slot in if free-form phrasing is ever needed.
 """
 from __future__ import annotations
@@ -19,15 +25,17 @@ from __future__ import annotations
 import re
 from datetime import datetime, timedelta, timezone
 
 from sqlalchemy import func
 from sqlalchemy.orm import joinedload
 
+from netra.api import retrieval
 from netra.core.db import SessionLocal
 from netra.core.geo import TIME_GROUPS
-from netra.core.models import Alert, Camera, Detection, WatchlistEntry
+from netra.core.models import (Alert, Camera, Detection, VehicleAttributeRow,
+                               WatchlistEntry, ZoneEventRow, ZoneRule)
 
 PLATE_RE = re.compile(r"\b([A-Z]{2}\s?\d{1,2}\s?[A-Z]{0,3}\s?\d{3,4})\b", re.I)
 
 
 def _answer(text: str, data=None, actions=None) -> dict:
     return {"answer": text, "data": data or {}, "actions": actions or []}
@@ -141,12 +149,40 @@ def _find_plate(q: str) -> dict:
                  f"not physically consistent or from a different recording "
                  f"session.")
     return _answer(text, route.to_dict(),
                    [{"label": f"Trace {plate}", "view": "route", "query": plate}])
 
 
+def _cloned_plates(_q: str) -> dict:
+    from netra.analytics.cloned_plate import find_clones
+    with SessionLocal() as db:
+        rows = (db.query(Detection).options(joinedload(Detection.camera))
+                .filter(Detection.plate_text.isnot(None)).all())
+        findings = find_clones(rows)
+
+    if not findings:
+        return _answer(
+            "No cloned plates detected. A plate is flagged only when the same "
+            "registration is read at two cameras in the same recording session, "
+            "too far apart for one vehicle to have covered in the time between - "
+            "sightings from different sessions are never compared.",
+            {"count": 0},
+            [{"label": "Open detections", "view": "detections"}])
+
+    top = findings[0]
+    return _answer(
+        f"{len(findings)} possible cloned plates. Strongest: {top.plate} at "
+        f"{top.sighting_a['camera_name']} and {top.sighting_b['camera_name']}, "
+        f"{top.distance_km} km apart in {top.elapsed_s:.0f}s "
+        f"(confidence {top.confidence}). This is inferred from OCR reads and is "
+        f"never certain - check the evidence images before acting.",
+        {"count": len(findings),
+         "findings": [f.to_dict() for f in findings[:10]]},
+        [{"label": f"Trace {top.plate}", "view": "route", "query": top.plate}])
+
+
 def _watchlist_summary(_q: str) -> dict:
     with SessionLocal() as db:
         rows = db.query(WatchlistEntry).filter(
             WatchlistEntry.active.is_(True)).all()
     by_cat: dict[str, int] = {}
     for e in rows:
@@ -194,59 +230,432 @@ def _coverage(_q: str) -> dict:
         f". Cross-camera tracing is valid within {len(TIME_GROUPS)} groups of "
         f"cameras that share a recording session.",
         {"by_city": by_city, "time_groups": TIME_GROUPS},
         [{"label": "Open map", "view": "map"}])
 
 
+def _unusual(_q: str) -> dict:
+    """Anything abnormal, judged against each camera's own learned norm.
+
+    A control room cannot read seventeen thousand detections. It can read
+    "camera 12 is four times its usual 03:00 traffic", which is what this
+    answers - and, just as importantly, says plainly where the platform has not
+    yet watched a camera long enough to have an opinion.
+    """
+    from netra.analytics import baseline
+    from netra.core.models import TrafficStat
+
+    with SessionLocal() as db:
+        rows = (db.query(TrafficStat)
+                .order_by(TrafficStat.bucket_start.desc()).limit(5000).all())
+
+    if not rows:
+        return _answer(
+            "No traffic history has been recorded yet, so there is no norm to "
+            "compare against. Run the pipeline for a while and ask again.",
+            {"buckets": 0}, [{"label": "Overview", "view": "overview"}])
+
+    learned = baseline.learn(rows)
+    latest: dict = {}
+    for r in rows:
+        latest.setdefault(r.camera_id, r)   # newest first
+    found = baseline.detect_anomalies(learned, list(latest.values()))
+    flagged = [a for a in found if a.anomalous]
+    thin = [a for a in found if a.status == "insufficient_data"]
+    # A camera that stopped reporting hours ago has a "most recent bucket"
+    # like any other, and judging it would present a dropped feed as a road
+    # observed empty. detect_anomalies marks those stale; they are counted and
+    # named here rather than folded in with the findings.
+    stale = [a for a in found if a.status == "stale"]
+
+    data = {"buckets": len(rows), "cameras_assessed": len(latest),
+            "anomalies": len(flagged), "stale": len(stale),
+            "assessments": [a.as_dict() for a in found]}
+    actions = [{"label": "Traffic", "view": "traffic"}]
+
+    if not flagged:
+        text = (f"Nothing unusual. All {len(latest)} cameras with a current "
+                f"reading are within their usual range for this hour.")
+        if thin:
+            text += (f" {len(thin)} camera(s) have fewer than "
+                     f"{baseline.MIN_SAMPLES} observations of this hour, so "
+                     f"they are not being judged at all yet.")
+        if stale:
+            text += (f" {len(stale)} camera(s) have not reported for over "
+                     f"{baseline.ANOMALY_MAX_BUCKET_AGE_S / 60:.0f} minutes; "
+                     f"their last readings are not current and are not "
+                     f"judged.")
+        return _answer(text, data, actions)
+
+    lead = "; ".join(a.explanation for a in flagged[:3])
+    text = (f"{len(flagged)} of {len(latest)} cameras are outside their normal "
+            f"range for this hour of the day. {lead}")
+    if stale:
+        text += (f" A further {len(stale)} camera(s) have not reported for "
+                 f"over {baseline.ANOMALY_MAX_BUCKET_AGE_S / 60:.0f} minutes "
+                 f"and are not judged at all.")
+    if thin:
+        text += (f" A further {len(thin)} camera(s) have too little history "
+                 f"({baseline.MIN_SAMPLES} observations required) for any "
+                 f"judgement to be honest.")
+    return _answer(text, data, actions)
+
+
+# -- entity resolution ------------------------------------------------------
+#
+# Everything below still reads its facts from SQL. Resolution only chooses
+# which id the SQL runs against; it never contributes a number.
+
+def _resolution_note(m: retrieval.EntityMatch, label: str | None = None) -> str:
+    """The sentence that keeps a scoped answer honest.
+
+    A narrowed answer is only trustworthy if the operator can see what it was
+    narrowed to. It is never phrased as a certainty: the match is a guess about
+    intent, and the wording has to invite correction.
+
+    `label` is the name just read from the database. The index is TTL-cached,
+    so its label can be up to a minute stale, and naming an entity by an old
+    name beside freshly read facts is exactly the kind of quiet inconsistency
+    that costs an operator their trust in the whole answer.
+    """
+    how = ("closest spelling in the registry" if m.via == "trigram"
+           else "closest name match")
+    return (f"I took that to mean {m.kind} {m.id} ({label or m.label}) - "
+            f"{how}, inferred from your wording and not confirmed. Name the id "
+            f"directly if you meant a different one.")
+
+
+def _camera_facts(camera_id: str):
+    """Everything the database knows about one camera. Nothing inferred."""
+    with SessionLocal() as db:
+        cam = db.get(Camera, camera_id)
+        if cam is None:
+            return None
+        total = db.query(func.count(Detection.id)).filter(
+            Detection.camera_id == camera_id).scalar() or 0
+        plates = db.query(func.count(Detection.id)).filter(
+            Detection.camera_id == camera_id,
+            Detection.plate_text.isnot(None)).scalar() or 0
+        alerts = db.query(func.count(Alert.id)).filter(
+            Alert.camera_id == camera_id).scalar() or 0
+        last = (db.query(func.max(Detection.wall_time))
+                .filter(Detection.camera_id == camera_id).scalar())
+        zones = db.query(ZoneRule).filter(
+            ZoneRule.camera_id == camera_id).all()
+        zone_events = db.query(func.count(ZoneEventRow.id)).filter(
+            ZoneEventRow.camera_id == camera_id).scalar() or 0
+        data = {"id": cam.id, "name": cam.name, "city": cam.city,
+                "district": cam.district, "capability": cam.capability,
+                "capability_note": cam.capability_note, "health": cam.health,
+                "enabled": cam.enabled, "detections": total,
+                "with_plate": plates, "alerts": alerts,
+                "zone_rules": [{"id": z.id, "name": z.name, "rule": z.rule}
+                               for z in zones],
+                "zone_events": zone_events,
+                "last_detection_at": last.isoformat() if last else None}
+        text = (f"Camera {cam.id} - {cam.name} "
+                f"({cam.city or 'location unknown'}). Capability "
+                f"{cam.capability}, health {cam.health}. {total} detections "
+                f"recorded, {plates} with a readable plate, {alerts} watchlist "
+                f"alerts.")
+        if zones:
+            text += (f" {len(zones)} zone rule(s) configured, {zone_events} "
+                     f"event(s) triggered.")
+        if cam.capability == "degraded":
+            text += (f" It cannot deliver analytics: "
+                     f"{cam.capability_note or 'reason unspecified'}.")
+        if not total:
+            text += " Nothing has been detected on it yet."
+    return text, data
+
+
+def _zone_facts(zone_id: str):
+    with SessionLocal() as db:
+        z = db.get(ZoneRule, int(zone_id))
+        if z is None:
+            return None
+        fired = db.query(func.count(ZoneEventRow.id)).filter(
+            ZoneEventRow.zone_rule_id == z.id).scalar() or 0
+        last = (db.query(func.max(ZoneEventRow.at))
+                .filter(ZoneEventRow.zone_rule_id == z.id).scalar())
+        data = {"id": z.id, "name": z.name, "rule": z.rule,
+                "camera_id": z.camera_id, "severity": z.severity,
+                "active": z.active, "events": fired,
+                "last_event_at": last.isoformat() if last else None}
+        text = (f"Zone {z.id} - {z.name}: a {z.rule} rule on {z.camera_id}, "
+                f"severity {z.severity}, "
+                f"{'active' if z.active else 'inactive'}. {fired} event(s) "
+                f"triggered.")
+    return text, data
+
+
+def _watchlist_facts(entry_id: str):
+    with SessionLocal() as db:
+        e = db.get(WatchlistEntry, int(entry_id))
+        if e is None:
+            return None
+        seen = db.query(func.count(Detection.id)).filter(
+            Detection.plate_text == e.plate).scalar() or 0
+        alerts = db.query(func.count(Alert.id)).filter(
+            Alert.watchlist_id == e.id).scalar() or 0
+        data = {"id": e.id, "plate": e.plate, "category": e.category,
+                "severity": e.severity, "case_ref": e.case_ref,
+                "active": e.active, "sightings": seen, "alerts": alerts}
+        case = f", case {e.case_ref}" if e.case_ref else ""
+        text = (f"Watchlist entry {e.id} - {e.plate}, {e.category} "
+                f"({e.severity}){case}. {seen} sighting(s) recorded, "
+                f"{alerts} alert(s) raised.")
+    return text, data
+
+
+def _vehicle_facts(detection_id: str):
+    """Facts about a vehicle the operator found by its description.
+
+    The description resolved the mention; it states none of this. Camera,
+    time, class and colour are read from the detections table, and the stored
+    caption is shown as what a model said about the crop rather than as a
+    property of the vehicle.
+    """
+    with SessionLocal() as db:
+        d = db.get(Detection, int(detection_id))
+        if d is None:
+            return None
+        row = db.query(VehicleAttributeRow).filter(
+            VehicleAttributeRow.detection_id == d.id).one_or_none()
+        cam = db.get(Camera, d.camera_id)
+        when = (d.scene_time or d.wall_time)
+        data = {"detection_id": d.id, "camera_id": d.camera_id,
+                "camera_name": cam.name if cam else None,
+                "at": when.isoformat() if when else None,
+                "scene_time": d.scene_time.isoformat() if d.scene_time else None,
+                "vehicle_class": d.vehicle_class, "colour": d.colour,
+                "plate_text": d.plate_text,
+                "description": row.description if row else None,
+                "confidence": row.confidence if row else None}
+        where = cam.name if cam and cam.name else d.camera_id
+        stamp = when.strftime("%Y-%m-%d %H:%M:%S") if when else "an unknown time"
+        described = (f' Described by {row.model or "the vision-language model"} '
+                     f'as "{row.description}" (confidence '
+                     f'{row.confidence:.2f}) - a description of the crop, not '
+                     f'an identification.') if row else ""
+        text = (f"Detection {d.id} - a {d.colour or 'colour-unknown'} "
+                f"{d.vehicle_class} on {where} at {stamp}."
+                f"{described}")
+    return text, data
+
+
+_FACTS = {"camera": _camera_facts, "zone": _zone_facts,
+          "watchlist": _watchlist_facts, "vehicle": _vehicle_facts}
+
+
+def _entity_action(m: retrieval.EntityMatch) -> dict:
+    if m.kind == "camera":
+        return {"label": f"Open {m.id}", "view": "registry", "query": m.id}
+    if m.kind == "zone":
+        return {"label": "Open zones", "view": "zones"}
+    if m.kind == "vehicle":
+        return {"label": f"Open detection {m.id}", "view": "detections",
+                "query": m.id}
+    return {"label": f"Trace {m.label}", "view": "route", "query": m.label}
+
+
+def _search(q: str) -> dict:
+    """Free-text lookup: resolve what was named, then state the SQL facts.
+
+    Two steps that never blur into one. Retrieval produces ids and a ranking;
+    every figure in the answer comes from a query against those ids.
+    """
+    query = re.sub(r"^\s*(search|look\s*(up|for)|lookup)\b(\s+for)?[:\s]*",
+                   "", q or "", flags=re.I).strip() or (q or "")
+    matches = retrieval.resolve(query, limit=5, ignore=INTENT_VOCAB)
+    if not matches:
+        return _answer(
+            f"Nothing in the camera registry, the zone rules, the watchlist "
+            f"or the described vehicles "
+            f"matches '{query}' closely enough for me to be sure what you "
+            f"meant, and guessing would be worse than saying so. A camera id, "
+            f"a place name or a registration number will find it.",
+            {"query": query, "matches": []}, _help("")["actions"])
+
+    found, lines = [], []
+    for m in matches:
+        got = _FACTS[m.kind](m.id)
+        if got is None:
+            # The index is TTL-cached, so a row can be deleted between build
+            # and query. Dropping it is right: an entity whose facts cannot be
+            # read is one that must not be described.
+            continue
+        text, data = got
+        lines.append(text)
+        found.append({**m.as_dict(), "facts": data})
+
+    if not found:
+        return _answer(
+            f"'{query}' matched entries that are no longer in the database. "
+            f"Ask again in a moment.",
+            {"query": query, "matches": []}, _help("")["actions"])
+
+    head = (f"{len(found)} match(es) for '{query}'. The ranking is inferred "
+            f"from how closely the wording matches; every figure below is read "
+            f"from the database. ")
+    return _answer(head + " ".join(lines), {"query": query, "matches": found},
+                   [_entity_action(matches[0])])
+
+
+#: How far ahead of the runner-up a match must be before an estate-wide answer
+#: is replaced by a single-entity one. Five Ahmedabad cameras score almost
+#: identically on "the Ahmedabad camera": narrowing to whichever won by a
+#: rounding error would silently hide four of them, so an ambiguous mention
+#: falls through to the broad answer instead.
+SCOPE_MARGIN = 0.9
+
+#: Intents whose SQL is worth narrowing when the question names one entity.
+#: Deliberately short: an intent is listed only where a per-entity answer is
+#: strictly more useful than the estate-wide one it would replace. The plate
+#: trace and the clone search are absent on purpose - both already scope
+#: themselves, from a registration number the operator typed exactly.
+_SCOPABLE = (_camera_health, _coverage)
+
+
+def _scoped(question: str, handler):
+    """A per-entity answer when the question confidently names an entity.
+
+    ponytail: one entity, chosen by margin over the runner-up. A question that
+    names two ("compare cam06 and cam08") is answered about neither, falling
+    through to the estate-wide handler - which is the safe direction to fail,
+    but its ceiling is comparative questions, which need the resolver to return
+    a set and the handlers to accept one.
+    """
+    if handler not in _SCOPABLE:
+        return None
+    matches = (retrieval.resolve(question, kind="camera", limit=2,
+                                 ignore=INTENT_VOCAB) or
+               retrieval.resolve(question, kind="zone", limit=2,
+                                 ignore=INTENT_VOCAB))
+    if not matches:
+        return None
+    if len(matches) > 1 and matches[1].score >= SCOPE_MARGIN * matches[0].score:
+        return None
+    m = matches[0]
+    got = _FACTS[m.kind](m.id)
+    if got is None:
+        return None
+    text, data = got
+    fresh = data.get("name") or data.get("plate")
+    return _answer(f"{_resolution_note(m, fresh)} {text}",
+                   {"resolved": m.as_dict(), "facts": data},
+                   [_entity_action(m)])
+
+
 def _help(_q: str) -> dict:
     return _answer(
         "I answer from live platform data. You can ask about camera health and "
         "which cameras are faulty, detection counts, current alerts, the "
-        "watchlist, pipeline status, coverage by location, or where a specific "
-        "registration number has been seen.",
+        "watchlist, pipeline status, coverage by location, whether any plates look "
+        "cloned, whether anything looks unusual against each camera's normal "
+        "traffic, or where a specific registration number has been seen. You "
+        "can also name a camera, a place or a case reference loosely - 'look "
+        "up the junagadh bypass' - and I will say which entry I took you to "
+        "mean before answering about it.",
         {}, [{"label": "Camera health", "query": "which cameras are down"},
              {"label": "Current alerts", "query": "show me the alerts"},
-             {"label": "Detections", "query": "how many detections"}])
+             {"label": "Detections", "query": "how many detections"},
+             {"label": "Anything unusual", "query": "anything unusual?"}])
 
 
 # Ordered: the first intent whose keywords appear wins, so specific
 # intents must precede general ones.
 INTENTS = [
+    # First: an explicit lookup is a request to resolve a name, and its
+    # phrasing ("search for the toll camera") contains words that the camera
+    # and coverage intents would otherwise claim.
+    (("search", "look up", "lookup", "look for"), _search),
+    # Ahead of everything else: an operator phrases this question with words
+    # that later intents already claim - "which camera is busier than normal"
+    # contains "camera", "where is it unusual" contains "where" - so placed
+    # lower it would be answered by camera health or the plate trace instead.
+    (("unusual", "abnormal", "anomaly", "anomalies", "out of the ordinary",
+      "baseline", "baselines", "spike", "quieter than", "busier than"), _unusual),
+    # Ahead of the trace intent because "find cloned plates" contains "find";
+    # a question naming an actual registration number never reaches here, as
+    # `ask` routes those to the trace handler before the keyword loop runs.
+    (("clone", "cloned", "cloning", "forged", "forgery", "duplicate plate",
+      "fake plate"), _cloned_plates),
     (("where", "seen", "trace", "track", "find", "locate"), _find_plate),
     (("camera", "cameras", "down", "faulty", "degraded", "health", "broken"), _camera_health),
     (("alert", "alerts", "hit", "match", "matches"), _alert_summary),
     (("watchlist", "stolen", "wanted", "suspect", "blacklist"), _watchlist_summary),
     (("detection", "detections", "vehicles", "cars", "count", "how many"), _detection_summary),
     (("pipeline", "running", "status", "system"), _pipeline_status),
     (("coverage", "map", "location", "city", "where are"), _coverage),
     (("help", "what can you", "commands", "hello", "hi"), _help),
 ]
 
+#: Every word that appears in an intent's keywords. These are the words that
+#: told the router *what* is being asked; they say nothing about *of what*, and
+#: an entity resolver that counts them as unexplained information will sink the
+#: mention standing next to them - "is cam11 down" would resolve nothing while
+#: "cam11" resolves cleanly. Derived from INTENTS rather than written out, so a
+#: keyword added to an intent cannot be forgotten here. The resolver drops one
+#: only when the corpus has never seen it, so a real camera name is safe.
+INTENT_VOCAB = frozenset(
+    w for keywords, _ in INTENTS for k in keywords
+    for w in retrieval.tokenise(k))
+
 # LLM_HINT: to support free-form phrasing, classify the question to one of the
 # intent names above with a model and dispatch here. The handlers must remain
 # the only source of facts - the model chooses the query, never the answer.
 
 
-def ask(question: str) -> dict:
-    """Route a question to a handler and return a grounded answer."""
-    if not question or not question.strip():
-        return _help("")
-    q = question.lower().strip()
+def route(question: str):
+    """Which handler a question resolves to, or None for "I cannot answer".
 
-    # A registration number anywhere in the question is unambiguous intent.
+    Split out from `ask` so routing can be checked without running a handler,
+    and therefore without a database: a wrong route is the failure mode that
+    produces a confidently wrong answer, and it is worth pinning down on its
+    own.
+    """
+    if not question or not question.strip():
+        return _help
     if PLATE_RE.search(question):
-        return _find_plate(question)
-
+        # A registration number anywhere in the question is unambiguous intent.
+        return _find_plate
+    q = question.lower().strip()
     for keywords, handler in INTENTS:
         if any(k in q for k in keywords):
-            return handler(question)
+            return handler
+    return None
+
+
+def ask(question: str) -> dict:
+    """Route a question to a handler and return a grounded answer.
+
+    Entity resolution happens here rather than in `route`, so routing stays a
+    pure function of the text and can be asserted without a database.
+    """
+    handler = route(question)
+    if handler is not None:
+        # A question that names an entity gets an answer about that entity.
+        # `_scoped` returns None whenever the mention is not confident enough,
+        # and the estate-wide handler then runs exactly as it always did.
+        scoped = _scoped(question, handler)
+        if scoped is not None:
+            return scoped
+        return handler(question)
+
+    # No intent keyword at all, but an operator who types a bare "cam11" or
+    # "majewadi" has named something precisely. Resolution is tried last, so
+    # it can never divert a question an intent already claimed, and it still
+    # declines when nothing resolves.
+    if retrieval.resolve(question, limit=1, ignore=INTENT_VOCAB):
+        return _search(question)
 
     return _answer(
         "I could not match that to anything I can answer from platform data. "
         "Ask about camera health, detections, alerts, the watchlist, pipeline "
-        "status, coverage, or a specific registration number.",
+        "status, coverage, cloned plates, or a specific registration number.",
         {}, _help("")["actions"])
 
 
 def _self_check() -> None:
     """Routing decides which query runs; a wrong route gives a confident wrong
     answer, so the mapping is worth pinning down."""
@@ -261,12 +670,87 @@ def _self_check() -> None:
 
     # Intent routing without a plate.
     assert "cameras" in ask("which cameras are down?")["answer"].lower()
     assert ask("show me the alerts")["data"] is not None
     assert "watchlist" in ask("what is on the watchlist")["answer"].lower()
 
+    # The clone intent must not swallow a plate trace, and must win over the
+    # trace keywords when a question is about clones generally.
+    r = ask("any cloned plates?")
+    assert "clone" in r["answer"].lower(), r
+    r = ask("find cloned plates")
+    assert "clone" in r["answer"].lower(), r
+    r = ask("where has GJ01AB1234 been seen?")
+    assert "GJ01AB1234" in r["answer"], r
+
+    # The unusual/baseline intent must win over the general handlers whose
+    # keywords a naturally phrased question also contains. Routing is asserted
+    # rather than answered, so this needs no database.
+    for q in ("anything unusual?", "is anything abnormal right now",
+              "show me the anomalies", "which camera is busier than normal",
+              "what does the baseline say"):
+        assert route(q) is _unusual, (q, route(q))
+
+    # ...and the reverse direction: the new intent must not steal questions
+    # belonging to the handlers that were already there.
+    assert route("which cameras are down?") is _camera_health
+    assert route("where has GJ01AB1234 been seen?") is _find_plate
+    assert route("find cloned plates") is _cloned_plates
+    assert route("show me the alerts") is _alert_summary
+    assert route("how many detections") is _detection_summary
+    assert route("is the pipeline running") is _pipeline_status
+    assert route("what is the weather in Ahmedabad tomorrow") is None
+
+    # The lookup intent must not be stolen by, nor steal from, the intents
+    # that were already there. Routing only, so no database is involved.
+    for q in ("search for the junagadh bypass", "look up cam06",
+              "lookup GJ-AHM-014"):
+        assert route(q) is _search, (q, route(q))
+    assert route("which cameras are down?") is _camera_health
+    assert route("how many detections") is _detection_summary
+
+    # Resolution decides which row is queried, never what it contains: every
+    # fact function reads from the database and none of them is reachable
+    # without an id that the index actually holds.
+    assert set(_FACTS) == {"camera", "zone", "watchlist", "vehicle"}
+
+    # The honesty constraint on a scoped answer: it must name the entity and
+    # mark it as inferred, so a wrong substitution is visible.
+    note = _resolution_note(
+        retrieval.EntityMatch("camera", "cam06", "Timbavadi gate", 3.1))
+    assert "cam06" in note and "Timbavadi gate" in note, note
+    assert "inferred" in note and "not confirmed" in note, note
+
+    # The words an intent routes on must never be counted against the mention
+    # standing beside them. Checked on a synthetic corpus, so no database.
+    assert {"down", "health", "coverage", "faulty", "status"} <= INTENT_VOCAB
+    idx = retrieval.build_index(retrieval._SYNTHETIC)
+
+    def _res(q):
+        return idx.resolve(q, ignore=INTENT_VOCAB)
+
+    # A bare id, and an id buried in intent words, are the least ambiguous
+    # things an operator can type and must be the most reliable path.
+    for q in ("GJ-JUN-004", "is GJ-JUN-004 down", "camera health GJ-JUN-004",
+              "what is the status of GJ-JUN-004"):
+        m = _res(q)
+        assert m and m[0].id == "GJ-JUN-004", (q, m)
+
+    # A place name mixed with intent words resolves just as well.
+    for q, want in (("is the junagadh bypass camera down", "GJ-JUN-004"),
+                    ("camera health for rajkot ring road", "GJ-RAJ-002"),
+                    ("coverage in surat", "GJ-SUR-009")):
+        m = _res(q)
+        assert m and m[0].id == want, (q, m)
+
+    # ...and none of that opens a route to a spurious scope.
+    for q in ("the weather tomorrow", "banana", "xyzzy", "please",
+              "the camera", "a zone", "show me", "which cameras are down?",
+              "how many detections", "is the pipeline running"):
+        assert _res(q) == [], (q, _res(q))
+
     # Unknown questions must decline rather than invent an answer.
     r = ask("what is the weather in Ahmedabad tomorrow")
     assert "could not match" in r["answer"], r
 
     print("assistant self-check passed")
 
diff --git a/netra/api/report.py b/netra/api/report.py
new file mode 100644
index 0000000..6800ee4
--- /dev/null
+++ b/netra/api/report.py
@@ -0,0 +1,258 @@
+"""Operational output report.
+
+Submission requires an output report showing detected vehicles and number
+plates with their timestamps. A CSV satisfies that literally but is not what an
+investigating officer or an evaluator actually reads, so this renders a
+self-contained HTML document - printable to PDF from the browser, no
+dependencies, evidence imagery inline - covering:
+
+    what the network saw          detections, counts, class mix
+    what matched a watchlist      alerts with the reasoning behind each
+    what rules were triggered     zone intrusions, crossings, loitering
+    what the network cannot do    cameras measured as unable to deliver
+
+That last section is deliberate. A report that lists only successes tells a
+State programme nothing about where its infrastructure needs attention.
+"""
+from __future__ import annotations
+
+import html
+from datetime import datetime, timedelta, timezone
+
+from sqlalchemy import func
+from sqlalchemy.orm import joinedload
+
+from netra.core.db import SessionLocal
+from netra.core.models import (Alert, Camera, Detection, WatchlistEntry,
+                               ZoneEventRow, ZoneRule)
+
+CSS = """
+* { box-sizing: border-box; }
+body { font: 13px/1.55 'Segoe UI', system-ui, sans-serif; color: #1a2332;
+       margin: 0; padding: 32px 40px; background: #fff; }
+h1 { font-size: 23px; margin: 0 0 4px; color: #0b2d4f; }
+h2 { font-size: 15px; margin: 28px 0 10px; color: #0b2d4f;
+     border-bottom: 2px solid #0b2d4f; padding-bottom: 5px; }
+h3 { font-size: 13px; margin: 18px 0 8px; color: #35506e; }
+.sub { color: #64748b; font-size: 12px; margin-bottom: 2px; }
+table { width: 100%; border-collapse: collapse; font-size: 11.5px;
+        margin-bottom: 10px; }
+th { background: #0b2d4f; color: #fff; text-align: left; padding: 6px 8px;
+     font-size: 10.5px; text-transform: uppercase; letter-spacing: .04em; }
+td { padding: 5px 8px; border-bottom: 1px solid #e2e8f0; vertical-align: middle; }
+tr:nth-child(even) td { background: #f8fafc; }
+.mono { font-family: 'Consolas', monospace; }
+.plate { font-family: 'Consolas', monospace; font-weight: 700; letter-spacing: 1px; }
+img.ev { height: 40px; border: 1px solid #cbd5e1; border-radius: 3px; }
+.cards { display: flex; gap: 12px; flex-wrap: wrap; margin: 12px 0 4px; }
+.card { border: 1px solid #d8e3ee; border-radius: 8px; padding: 10px 14px;
+        min-width: 130px; background: #f8fbfe; }
+.card .n { font-size: 21px; font-weight: 800; color: #0b5c8f; }
+.card .l { font-size: 10.5px; color: #64748b; text-transform: uppercase;
+           letter-spacing: .05em; }
+.sev { display: inline-block; padding: 1px 6px; border-radius: 3px;
+       font-size: 10px; font-weight: 700; text-transform: uppercase; }
+.sev-critical { background: #fee2e2; color: #991b1b; }
+.sev-high { background: #ffedd5; color: #9a3412; }
+.sev-medium { background: #fef3c7; color: #854d0e; }
+.sev-low { background: #f1f5f9; color: #475569; }
+.note { background: #f8fafc; border-left: 3px solid #0b5c8f; padding: 9px 13px;
+        font-size: 11.5px; color: #35506e; margin: 8px 0; line-height: 1.6; }
+.why { font-size: 10.5px; color: #64748b; line-height: 1.5; }
+footer { margin-top: 30px; padding-top: 10px; border-top: 1px solid #e2e8f0;
+         font-size: 10.5px; color: #94a3b8; }
+@media print { body { padding: 12px; } h2 { page-break-after: avoid; }
+               tr { page-break-inside: avoid; } }
+"""
+
+
+def _e(v) -> str:
+    return html.escape(str(v if v is not None else "—"))
+
+
+def _card(n, label) -> str:
+    return f'<div class="card"><div class="n">{_e(n)}</div><div class="l">{_e(label)}</div></div>'
+
+
+def build_report(hours: int = 24, base_url: str = "") -> str:
+    """Render the operational report as a standalone HTML document."""
+    since = datetime.now(timezone.utc) - timedelta(hours=hours)
+    generated = datetime.now(timezone.utc)
+
+    with SessionLocal() as db:
+        total = db.query(func.count(Detection.id)).filter(
+            Detection.wall_time >= since).scalar() or 0
+        with_plate = db.query(func.count(Detection.id)).filter(
+            Detection.wall_time >= since,
+            Detection.plate_text.isnot(None)).scalar() or 0
+        by_class = dict(db.query(Detection.vehicle_class, func.count(Detection.id))
+                        .filter(Detection.wall_time >= since)
+                        .group_by(Detection.vehicle_class).all())
+        by_camera = (db.query(Detection.camera_id, func.count(Detection.id))
+                     .filter(Detection.wall_time >= since)
+                     .group_by(Detection.camera_id)
+                     .order_by(func.count(Detection.id).desc()).all())
+
+        plate_rows = (db.query(Detection).options(joinedload(Detection.camera))
+                      .filter(Detection.wall_time >= since,
+                              Detection.plate_text.isnot(None))
+                      .order_by(Detection.wall_time.desc()).limit(200).all())
+
+        alerts = (db.query(Alert).filter(Alert.created_at >= since)
+                  .order_by(Alert.created_at.desc()).limit(100).all())
+        alert_rows = []
+        for a in alerts:
+            det = db.get(Detection, a.detection_id)
+            wl = db.get(WatchlistEntry, a.watchlist_id)
+            cam = db.get(Camera, a.camera_id)
+            alert_rows.append((a, det, wl, cam))
+
+        zevents = (db.query(ZoneEventRow).filter(ZoneEventRow.at >= since)
+                   .order_by(ZoneEventRow.at.desc()).limit(100).all())
+        zone_rows = [(z, db.get(ZoneRule, z.zone_rule_id),
+                      db.get(Camera, z.camera_id)) for z in zevents]
+
+        cameras = db.query(Camera).all()
+        cam_names = {c.id: c.name for c in cameras}
+        degraded = [c for c in cameras if c.capability == "degraded"]
+
+    parts: list[str] = [f"""<!doctype html><html><head><meta charset="utf-8">
+<title>NETRA Output Report</title><style>{CSS}</style></head><body>
+<h1>NETRA &mdash; Video Analytics Output Report</h1>
+<div class="sub">Networked Evidence, Tracking &amp; Recognition for Analytics</div>
+<div class="sub">Gujarat Police Innovation Challenge 2026 &middot; Sentinel CCTV Grid</div>
+<div class="sub">Reporting period: last {hours} hours &middot;
+ generated {generated:%Y-%m-%d %H:%M:%S} UTC</div>
+
+<h2>1. Summary</h2>
+<div class="cards">
+{_card(total, "Detections")}
+{_card(with_plate, "Plates read")}
+{_card(len(alert_rows), "Watchlist alerts")}
+{_card(len(zone_rows), "Zone events")}
+{_card(len(cameras), "Cameras")}
+{_card(len(degraded), "Cameras degraded")}
+</div>
+<table><tr><th>Object class</th><th>Count</th></tr>
+{''.join(f'<tr><td>{_e(k)}</td><td class="mono">{v}</td></tr>'
+         for k, v in sorted(by_class.items(), key=lambda x: -x[1]))}
+</table>"""]
+
+    # -- 2. plate reads -------------------------------------------------------
+    parts.append("<h2>2. Number plate detections with timestamps</h2>")
+    if plate_rows:
+        parts.append('<table><tr><th>Timestamp (UTC)</th><th>Scene time</th>'
+                     '<th>Camera</th><th>Location</th><th>Plate</th>'
+                     '<th>Confidence</th><th>Vehicle</th><th>Evidence</th></tr>')
+        for d in plate_rows:
+            cam = d.camera
+            ev = (f'<img class="ev" src="{base_url}{_e(d.evidence_path)}">'
+                  if d.evidence_path else "&mdash;")
+            parts.append(
+                f'<tr><td class="mono">{d.wall_time:%Y-%m-%d %H:%M:%S}</td>'
+                f'<td class="mono">'
+                f'{d.scene_time.strftime("%Y-%m-%d %H:%M:%S") if d.scene_time else "&mdash;"}</td>'
+                f'<td class="mono">{_e(d.camera_id)}</td>'
+                f'<td>{_e(cam.name if cam else "")}</td>'
+                f'<td class="plate">{_e(d.plate_text)}</td>'
+                f'<td class="mono">{round(d.plate_conf, 2) if d.plate_conf else "&mdash;"}</td>'
+                f'<td>{_e(d.colour)} {_e(d.vehicle_class)}</td>'
+                f'<td>{ev}</td></tr>')
+        parts.append("</table>")
+    else:
+        parts.append("""<div class="note"><b>No plate reads in this period.</b>
+        On the Government-provided grid this is the expected result and is a
+        property of the cameras, not of the recognition model: these are
+        wide-area junction overview cameras operating at night, on which a
+        number plate spans roughly 10&ndash;20 pixels under headlight glare.
+        Measured over 2,691 frames across the three best-positioned cameras,
+        200+ detected vehicles yielded no readable plate. Vehicle-level
+        analytics and appearance-based cross-camera tracing apply on these
+        cameras instead; plate recognition is demonstrated on footage where
+        plate geometry permits it.</div>""")
+
+    # -- 3. alerts ------------------------------------------------------------
+    parts.append("<h2>3. Watchlist matches</h2>")
+    if alert_rows:
+        parts.append('<table><tr><th>Time (UTC)</th><th>Watchlist plate</th>'
+                     '<th>Read as</th><th>Camera</th><th>Category</th>'
+                     '<th>Case</th><th>Severity</th><th>Score</th>'
+                     '<th>Reasoning</th></tr>')
+        for a, det, wl, cam in alert_rows:
+            why = "<br>".join(
+                f'<b>{_e(k)}</b> {v.get("score")}: {_e(v.get("detail"))}'
+                for k, v in (a.reasons or {}).items())
+            parts.append(
+                f'<tr><td class="mono">{a.created_at:%Y-%m-%d %H:%M:%S}</td>'
+                f'<td class="plate">{_e(wl.plate if wl else "")}</td>'
+                f'<td class="mono">{_e(det.plate_text if det else "")}</td>'
+                f'<td>{_e(cam.name if cam else a.camera_id)}</td>'
+                f'<td>{_e(wl.category if wl else "")}</td>'
+                f'<td class="mono">{_e(wl.case_ref if wl else "")}</td>'
+                f'<td><span class="sev sev-{_e(a.severity)}">{_e(a.severity)}</span></td>'
+                f'<td class="mono">{a.score}</td>'
+                f'<td class="why">{why}</td></tr>')
+        parts.append("</table>")
+        parts.append("""<div class="note">Every alert records the individual
+        signals that produced it and the confidence of each, so an operator can
+        review the reasoning and overrule it. Appearance evidence alone never
+        raises an alert.</div>""")
+    else:
+        parts.append('<div class="note">No watchlist matches in this period.</div>')
+
+    # -- 4. zone events -------------------------------------------------------
+    parts.append("<h2>4. Zone rule events</h2>")
+    if zone_rows:
+        parts.append('<table><tr><th>Time (UTC)</th><th>Camera</th><th>Zone</th>'
+                     '<th>Rule</th><th>Object</th><th>Direction</th>'
+                     '<th>Detail</th><th>Severity</th></tr>')
+        for z, rule, cam in zone_rows:
+            parts.append(
+                f'<tr><td class="mono">{z.at:%Y-%m-%d %H:%M:%S}</td>'
+                f'<td>{_e(cam.name if cam else z.camera_id)}</td>'
+                f'<td>{_e(rule.name if rule else "")}</td>'
+                f'<td>{_e(z.rule)}</td><td>{_e(z.object_class)}</td>'
+                f'<td>{_e(z.direction)}</td><td>{_e(z.detail)}</td>'
+                f'<td><span class="sev sev-{_e(z.severity)}">{_e(z.severity)}</span></td>'
+                f'</tr>')
+        parts.append("</table>")
+    else:
+        parts.append('<div class="note">No zone rules were triggered in this period.</div>')
+
+    # -- 5. per-camera activity ----------------------------------------------
+    parts.append("<h2>5. Activity by camera</h2>")
+    parts.append('<table><tr><th>Camera</th><th>Location</th>'
+                 '<th>Detections</th></tr>')
+    for cam_id, count in by_camera:
+        parts.append(f'<tr><td class="mono">{_e(cam_id)}</td>'
+                     f'<td>{_e(cam_names.get(cam_id))}</td>'
+                     f'<td class="mono">{count}</td></tr>')
+    parts.append("</table>")
+
+    # -- 6. infrastructure ----------------------------------------------------
+    parts.append("<h2>6. Cameras unable to deliver analytics</h2>")
+    if degraded:
+        parts.append('<table><tr><th>Camera</th><th>Location</th>'
+                     '<th>Measured condition</th></tr>')
+        for c in degraded:
+            parts.append(f'<tr><td class="mono">{_e(c.id)}</td>'
+                         f'<td>{_e(c.name)}</td>'
+                         f'<td>{_e(c.capability_note)}</td></tr>')
+        parts.append("</table>")
+        parts.append(f"""<div class="note"><b>{len(degraded)} of
+        {len(cameras)} cameras cannot currently support video analytics.</b>
+        This is determined automatically at onboarding by probing each stream
+        and measuring illumination, signal integrity and frame availability -
+        not by manual inspection. These cameras require maintenance attention
+        before any analytics deployment can rely on them.</div>""")
+    else:
+        parts.append('<div class="note">All cameras are delivering usable video.</div>')
+
+    parts.append(f"""<footer>
+Generated by NETRA on {generated:%Y-%m-%d %H:%M:%S} UTC.
+All timestamps are UTC. Confidence scores are advisory and intended to support
+an operator decision, not replace it. Detections are retained as structured
+metadata with evidence crops; no continuous video is recorded by this platform.
+</footer></body></html>""")
+
+    return "".join(parts)
diff --git a/netra/api/retrieval.py b/netra/api/retrieval.py
new file mode 100644
index 0000000..42e0ee4
--- /dev/null
+++ b/netra/api/retrieval.py
@@ -0,0 +1,591 @@
+"""Lexical entity resolution for the control-room assistant.
+
+The assistant answers only from SQL, which is what stops it inventing a count.
+That guarantee costs it flexibility: an operator who types "the Junagadh bypass
+camera", "junagad", or "that toll camera" is naming a real row in the registry,
+but no keyword rule in `assistant.py` knows which one.
+
+This module closes that gap and nothing else. The division is deliberate:
+
+    SQL     owns every fact - counts, timestamps, plates, statuses
+    BM25    owns resolving a fuzzy mention to an entity id, and never a fact
+    vector  owns appearance similarity between vehicles (`analytics/reid.py`)
+
+So BM25 decides *what the operator meant*; SQL still decides *what is true*. A
+match here produces an id and a label, never a number, so a wrong resolution can
+mislead an operator about which camera they are looking at but can never put a
+fabricated figure in front of them - and the assistant is required to say out
+loud which entity it inferred, so the substitution is visible and correctable.
+
+There is deliberately no embedding search over text. The vector half of the
+hybrid already exists as appearance re-identification; a second, semantic,
+text index would add a way for the assistant to be confidently wrong about
+which camera an operator meant, in exchange for phrasing tolerance that BM25
+plus a character fallback already covers on a corpus of a few hundred rows.
+
+ponytail: the index is rebuilt wholesale behind a short TTL rather than
+maintained incrementally. The registry, zone rules and watchlist together are
+hundreds of rows, so a rebuild is a few milliseconds of pure Python. The
+ceiling is roughly a five-figure corpus, at which point the rebuild starts to
+be felt on the request that triggers it and an incremental index earns its
+complexity.
+"""
+from __future__ import annotations
+
+import logging
+import math
+import re
+import time
+from dataclasses import dataclass, field
+
+#: BM25 term-frequency saturation and length normalisation. The standard
+#: values; nothing about this corpus argues for tuning them.
+K1 = 1.5
+B = 0.75
+
+#: How long a built index is trusted before a rebuild. Short enough that a
+#: camera renamed or a watchlist entry added is findable almost immediately,
+#: long enough that a burst of questions does not rebuild per question.
+TTL_S = 60.0
+
+#: Words that carry no identifying information in this corpus. Kept tiny on
+#: purpose: a long stop list silently removes the very token that distinguishes
+#: two entities. These are only the ones an operator uses as scaffolding.
+STOPWORDS = frozenset({
+    "the", "a", "an", "of", "at", "on", "in", "to", "for", "and", "or",
+    "is", "are", "was", "were", "be", "that", "this", "it", "its",
+    "me", "my", "show", "tell", "give", "please", "what", "which", "who",
+    "any", "all", "from", "with", "by", "about",
+    # Domain scaffolding: an operator says "the toll camera" to name what kind
+    # of thing they mean, never which one. Left in, these words are unmatched
+    # information that drags a perfectly good partial name below the coverage
+    # floor - and matched, they would match every row equally.
+    "camera", "cameras", "cctv", "feed", "feeds", "footage", "cam",
+})
+
+#: Share of the query's information (measured as idf mass, so a rare word
+#: counts for more than a common one) that a document must account for before
+#: the match is called a resolution. Below this the top hit is merely the least
+#: bad row in the corpus, and returning it would be the assistant guessing.
+#: Half is the defensible line: the match must explain more of what was typed
+#: than it leaves unexplained.
+MIN_COVERAGE = 0.5
+
+#: Absolute floor on the idf mass actually matched, in nats. Coverage alone is
+#: a ratio and so is satisfiable by a query made entirely of common words -
+#: "the camera" would match every camera at coverage 1.0. Requiring real
+#: information to have been matched means a hit on generic vocabulary is never
+#: a resolution. One nat is roughly a term appearing in a third of the corpus.
+MIN_EVIDENCE = 1.0
+
+#: Character-fallback threshold: the share of the query's trigrams that must
+#: appear in the label. Containment rather than a symmetric measure because the
+#: query is a fragment ("junagad") and the label is longer ("Junagadh Bypass
+#: ANPR"); a symmetric score would punish the label for its extra words.
+MIN_TRIGRAM_CONTAINMENT = 0.7
+
+#: Below this many characters a trigram comparison is noise - one or two
+#: trigrams will coincide with something in any corpus of a few hundred labels.
+#: Four is the shortest word an operator actually shortens a camera to ("toll"
+#: for "Tollnaka"), and at four the containment threshold demands every trigram
+#: match, which is strict enough to stay safe.
+MIN_TRIGRAM_QUERY_LEN = 4
+
+#: How many described vehicles the index carries, newest first. Cameras, zones
+#: and the watchlist are a few hundred rows between them and are indexed whole;
+#: descriptions accumulate one per described detection and would eventually
+#: dominate a rebuild the TTL performs on an operator's own request. Newest
+#: first because a search for a vehicle is nearly always a search for a recent
+#: one. ponytail: its ceiling is exactly that - an older described vehicle is
+#: unfindable by description, and stays findable only by camera, time or plate.
+VEHICLE_INDEX_LIMIT = 5000
+
+_TOKEN_RE = re.compile(r"[a-z0-9]+")
+
+log = logging.getLogger(__name__)
+
+
+def tokenise(text: str) -> list[str]:
+    """Lowercase, split on anything that is not a letter or digit.
+
+    Camera ids look like `GJ-AHM-014` and plates like `GJ01AB1234`, so the
+    split has to treat punctuation as a separator while keeping digits as part
+    of the token: "ahm" and "014" are both worth matching on.
+    """
+    return _TOKEN_RE.findall((text or "").lower())
+
+
+def _content_tokens(text: str) -> list[str]:
+    return [t for t in tokenise(text) if t not in STOPWORDS]
+
+
+def normalise(text: str) -> str:
+    """Strip to bare lowercase alphanumerics for character-level comparison.
+
+    Spacing and punctuation are exactly what differs between how an operator
+    types a name and how it is stored ("Junagadh Bypass" / "junagadh-bypass"),
+    so they must not be part of the comparison.
+    """
+    return "".join(_TOKEN_RE.findall((text or "").lower()))
+
+
+def _trigrams(text: str) -> set[str]:
+    n = normalise(text)
+    if len(n) < 3:
+        return {n} if n else set()
+    return {n[i:i + 3] for i in range(len(n) - 2)}
+
+
+@dataclass(frozen=True)
+class EntityMatch:
+    """One resolved entity. Carries no facts - an id and how sure we are."""
+    kind: str            # camera | zone | watchlist | vehicle
+    id: str
+    label: str
+    score: float
+    #: Which mechanism resolved it, so the assistant can be honest about a
+    #: match that came from a spelling repair rather than a real token hit.
+    via: str = "bm25"
+
+    def as_dict(self) -> dict:
+        return {"kind": self.kind, "id": self.id, "label": self.label,
+                "score": round(self.score, 3), "via": self.via}
+
+
+@dataclass
+class _Doc:
+    kind: str
+    id: str
+    label: str
+    text: str
+    tokens: list[str] = field(default_factory=list)
+    tf: dict[str, int] = field(default_factory=dict)
+
+
+class EntityIndex:
+    """A BM25 index over the searchable text of registry-level entities."""
+
+    def __init__(self, docs: list[_Doc] | None = None):
+        self.docs: list[_Doc] = []
+        self.df: dict[str, int] = {}
+        self.avg_len = 0.0
+        self.built_at = time.monotonic()
+        for d in docs or []:
+            self.add(d.kind, d.id, d.label, d.text)
+        self.finalise()
+
+    # -- construction -------------------------------------------------------
+
+    def add(self, kind: str, entity_id: str, label: str, text: str) -> None:
+        toks = _content_tokens(text)
+        doc = _Doc(kind=kind, id=str(entity_id), label=label, text=text,
+                   tokens=toks)
+        for t in toks:
+            doc.tf[t] = doc.tf.get(t, 0) + 1
+        self.docs.append(doc)
+
+    def finalise(self) -> None:
+        self.df = {}
+        for doc in self.docs:
+            for t in set(doc.tokens):
+                self.df[t] = self.df.get(t, 0) + 1
+        total = sum(len(d.tokens) for d in self.docs)
+        self.avg_len = (total / len(self.docs)) if self.docs else 0.0
+        self.built_at = time.monotonic()
+
+    def __len__(self) -> int:
+        return len(self.docs)
+
+    # -- scoring ------------------------------------------------------------
+
+    def _idf(self, term: str) -> float:
+        """Robertson-Sparck-Jones idf, smoothed so it can never go negative.
+
+        An unseen term gets the maximum idf of the corpus: it is information
+        the operator supplied that no document accounts for, and coverage must
+        be penalised for that rather than quietly ignoring it.
+        """
+        n = len(self.docs) or 1
+        df = self.df.get(term, 0)
+        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))
+
+    def score(self, query_tokens: list[str], doc: _Doc) -> float:
+        dl = len(doc.tokens) or 1
+        norm = K1 * (1 - B + B * dl / (self.avg_len or 1.0))
+        total = 0.0
+        for t in query_tokens:
+            f = doc.tf.get(t, 0)
+            if not f:
+                continue
+            total += self._idf(t) * (f * (K1 + 1)) / (f + norm)
+        return total
+
+    # -- resolution ---------------------------------------------------------
+
+    def resolve(self, query: str, kind: str | None = None, limit: int = 5,
+                ignore: frozenset[str] | set[str] | None = None
+                ) -> list[EntityMatch]:
+        """Entities the query plausibly names, best first, or an empty list.
+
+        Returning nothing is a valid and frequent answer. A top hit that
+        explains little of the query is not a resolution, it is the corpus's
+        least bad row, and handing it back would let the assistant answer about
+        an entity the operator never mentioned.
+
+        `ignore` is the caller's own intent vocabulary - the words that told it
+        *what* was being asked rather than *of what*. Unmatched, those words
+        are information the query supplied that no entity accounts for, so they
+        drive coverage down and sink the mention beside them: "is cam11 down"
+        would resolve nothing while "cam11" resolves cleanly. An ignored word
+        is dropped only when the corpus has never seen it, so a camera actually
+        called "Highway Junction" is never made unfindable by a word that also
+        happens to be an intent keyword.
+        """
+        q = _content_tokens(query)
+        if ignore:
+            q = [t for t in q if t not in ignore or t in self.df]
+        if not q or not self.docs:
+            return []
+        pool = [d for d in self.docs if kind is None or d.kind == kind]
+        if not pool:
+            return []
+
+        # Denominator includes tokens absent from the corpus, so a query that
+        # is mostly unknown words cannot reach coverage on the one word it
+        # happens to share with a label.
+        want = {t: self._idf(t) for t in set(q)}
+        total_idf = sum(want.values()) or 1.0
+
+        qset = set(q)
+        hits: list[tuple[int, EntityMatch]] = []
+        for doc in pool:
+            matched = sum(w for t, w in want.items() if t in doc.tf)
+            if matched < MIN_EVIDENCE or matched / total_idf < MIN_COVERAGE:
+                continue
+            # An operator who types an entity's own id means that entity, not
+            # a document that merely mentions it - a zone rule names the camera
+            # it sits on, and being short, outscores that camera on the
+            # camera's own id. Identity beats term statistics.
+            own = set(tokenise(doc.id))
+            named = 1 if own and own <= qset else 0
+            hits.append((named, EntityMatch(doc.kind, doc.id, doc.label,
+                                            self.score(q, doc), "bm25")))
+        if hits:
+            hits.sort(key=lambda h: (-h[0], -h[1].score, h[1].id))
+            return [m for _, m in hits[:limit]]
+
+        return self._trigram_fallback(" ".join(q), pool, limit)
+
+    def _trigram_fallback(self, query: str, pool: list[_Doc],
+                          limit: int) -> list[EntityMatch]:
+        """Character-level rescue for a misspelling that shares no token.
+
+        BM25 is exact-token: "junagad" and "junagadh" are different terms and
+        score zero against each other, so the single most common operator error
+        - a dropped or transposed letter in a place name - defeats it entirely.
+        Comparing normalised trigrams recovers that case without introducing a
+        semantic model, and is only consulted when token matching found nothing,
+        so it can never outrank a genuine lexical hit.
+        """
+        norm_q = normalise(query)
+        if len(norm_q) < MIN_TRIGRAM_QUERY_LEN:
+            return []
+        # The whole phrase, plus any individual word the index has never seen.
+        # Only an unknown word can be the misspelling this fallback exists for;
+        # trying known words individually would let a common one ("bypass")
+        # score a spurious 1.0 against every row that happens to contain it.
+        unknown = [normalise(t) for t in _content_tokens(query)
+                   if t not in self.df]
+        candidates = [norm_q] + unknown
+        candidates = [c for c in dict.fromkeys(candidates)
+                      if len(c) >= MIN_TRIGRAM_QUERY_LEN]
+        if not candidates:
+            return []
+
+        hits: list[EntityMatch] = []
+        for doc in pool:
+            doc_grams = _trigrams(doc.text)
+            best = 0.0
+            for cand in candidates:
+                grams = _trigrams(cand)
+                if not grams:
+                    continue
+                best = max(best, len(grams & doc_grams) / len(grams))
+            if best >= MIN_TRIGRAM_CONTAINMENT:
+                hits.append(EntityMatch(doc.kind, doc.id, doc.label, best,
+                                        "trigram"))
+        hits.sort(key=lambda m: (-m.score, m.id))
+        return hits[:limit]
+
+
+# -- the live index ----------------------------------------------------------
+
+_CACHE: dict[str, object] = {"index": None, "at": 0.0}
+
+
+def _rows_from_db() -> list[tuple[str, str, str, str]]:
+    """(kind, id, label, searchable text) for every resolvable entity."""
+    from netra.core.db import SessionLocal
+    from netra.core.models import (Camera, VehicleAttributeRow,
+                                   WatchlistEntry, ZoneRule)
+
+    out: list[tuple[str, str, str, str]] = []
+    with SessionLocal() as db:
+        for c in db.query(Camera).all():
+            # The id is part of the text because operators say "AHM 14" as
+            # often as they say the camera's name.
+            text = " ".join(x for x in (c.id, c.name, c.city, c.district,
+                                        c.capability) if x)
+            out.append(("camera", c.id, c.name or c.id, text))
+        for z in db.query(ZoneRule).all():
+            text = " ".join(x for x in (z.name, z.rule, z.camera_id) if x)
+            out.append(("zone", str(z.id), z.name or f"zone {z.id}", text))
+        for w in db.query(WatchlistEntry).all():
+            text = " ".join(x for x in (w.plate, w.case_ref, w.notes,
+                                        w.category, w.owner_name,
+                                        w.vehicle_make) if x)
+            out.append(("watchlist", str(w.id), w.plate, text))
+        # Vision-language descriptions, so "the black SUV with a roof rack"
+        # resolves to the detections that were described that way. This is the
+        # only kind whose text is model-written rather than operator-entered,
+        # which changes nothing about the division of labour: it resolves a
+        # phrase to a detection id, and every fact about that detection is then
+        # read from the detections table.
+        for v in (db.query(VehicleAttributeRow)
+                  .order_by(VehicleAttributeRow.id.desc())
+                  .limit(VEHICLE_INDEX_LIMIT).all()):
+            marks = " ".join(str(m) for m in (v.markings or []))
+            text = " ".join(x for x in (v.description, marks) if x)
+            if not _worth_indexing(v, text):
+                continue
+            out.append(("vehicle", str(v.detection_id), v.description, text))
+    return out
+
+
+#: Structured description fields that, populated, say something specific
+#: enough about one vehicle to be worth resolving a phrase to.
+_VEHICLE_FIELDS = ("body_type", "colour", "wheels", "roof_rack",
+                   "tinted_windows", "damage", "markings")
+
+
+def _worth_indexing(row, text: str) -> bool:
+    """Whether one described vehicle earns a place in the retrieval corpus.
+
+    A description of a single word - the captioner regularly produces just
+    "yellow" - is not a search key. Every yellow vehicle on the grid scores
+    identically for the query "yellow", so which detection comes back is
+    decided by the tie-break rather than by the evidence, and the assistant
+    then presents that arbitrary row as *the* answer. Either two populated
+    structured fields or a description of at least two tokens is the minimum
+    that distinguishes one vehicle from the next.
+
+    ponytail: a token count, not a measure of information. Its ceiling is that
+    "a vehicle" passes and "yellow" does not, though neither is much of a key.
+    """
+    if not text.strip():
+        return False
+    populated = sum(1 for f in _VEHICLE_FIELDS
+                    if getattr(row, f, None) not in (None, "", [], False))
+    if populated >= 2:
+        return True
+    return len((row.description or "").split()) >= 2
+
+
+def build_index(rows=None) -> EntityIndex:
+    """Build an index from the given rows, or from the database."""
+    idx = EntityIndex()
+    for kind, entity_id, label, text in (rows if rows is not None
+                                         else _rows_from_db()):
+        idx.add(kind, entity_id, label, text)
+    idx.finalise()
+    return idx
+
+
+def get_index(force: bool = False) -> EntityIndex:
+    """The shared index, rebuilt when the TTL has expired."""
+    now = time.monotonic()
+    idx = _CACHE.get("index")
+    if force or idx is None or now - float(_CACHE["at"]) > TTL_S:
+        idx = build_index()
+        _CACHE["index"] = idx
+        _CACHE["at"] = now
+    return idx  # type: ignore[return-value]
+
+
+def resolve(query: str, kind: str | None = None, limit: int = 5,
+            ignore: frozenset[str] | set[str] | None = None
+            ) -> list[EntityMatch]:
+    """Resolve a fuzzy mention against live platform entities."""
+    try:
+        return get_index().resolve(query, kind=kind, limit=limit,
+                                   ignore=ignore)
+    except Exception as exc:
+        # Resolution is an assist, never a fact. If the registry cannot be
+        # read the assistant must still answer from SQL rather than fail - but
+        # a schema or connection fault that silently costs every operator
+        # their fuzzy lookups has to be findable in the log, not inferred from
+        # the feature quietly never working.
+        log.warning("entity resolution unavailable, answering unscoped: %r",
+                    exc)
+        return []
+
+
+# -- self-check --------------------------------------------------------------
+
+_SYNTHETIC = [
+    ("camera", "GJ-JUN-004", "Junagadh Bypass ANPR",
+     "GJ-JUN-004 Junagadh Bypass ANPR Junagadh Junagadh anpr"),
+    ("camera", "GJ-AHM-014", "Ahmedabad SG Highway Tollnaka",
+     "GJ-AHM-014 Ahmedabad SG Highway Tollnaka Ahmedabad Ahmedabad anpr"),
+    ("camera", "GJ-RAJ-002", "Rajkot Ring Road North",
+     "GJ-RAJ-002 Rajkot Ring Road North Rajkot Rajkot vehicle"),
+    ("camera", "GJ-SUR-009", "Surat Ring Road South",
+     "GJ-SUR-009 Surat Ring Road South Surat Surat degraded"),
+    ("zone", "3", "Bypass hard shoulder",
+     "Bypass hard shoulder intrusion GJ-JUN-004"),
+    ("watchlist", "7", "GJ01AB1234",
+     "GJ01AB1234 FIR-2026-118 reported stolen from Vadodara stolen"),
+    ("vehicle", "9182", "black suv; tinted windows; alloy wheels; roof rack",
+     "black suv tinted windows alloy wheels roof rack"),
+    ("vehicle", "9200", "white van; markings: Om Travels",
+     "white van markings Om Travels"),
+]
+
+
+def _self_check() -> None:
+    """Resolution never touches the database here: the failure worth pinning
+    down is what the scorer does with a corpus, not what the registry holds."""
+    idx = build_index(_SYNTHETIC)
+    assert len(idx) == len(_SYNTHETIC)
+
+    # An exact name resolves, and resolves to the right row.
+    m = idx.resolve("Junagadh Bypass ANPR")
+    assert m and m[0].id == "GJ-JUN-004", m
+
+    # A partial name resolves.
+    m = idx.resolve("the Junagadh bypass camera")
+    assert m and m[0].id == "GJ-JUN-004", m
+
+    # A misspelling shares no token with the target, so BM25 alone scores it
+    # zero; the trigram fallback must recover it.
+    m = idx.resolve("junagad")
+    assert m and m[0].id == "GJ-JUN-004", m
+    assert m[0].via == "trigram", m
+
+    # An unrelated string must resolve to nothing rather than to whichever row
+    # happens to score least badly. This is the whole confidence floor.
+    for junk in ("what is the weather tomorrow", "quantum entanglement",
+                 "zzzzzzzz", "how many detections", ""):
+        assert idx.resolve(junk) == [], (junk, idx.resolve(junk))
+
+    # Generic corpus vocabulary is not evidence: every camera contains "camera"
+    # -ish words, so a query of nothing but those must resolve to nothing.
+    assert idx.resolve("the camera") == [], idx.resolve("the camera")
+
+    # BM25 ranks the better lexical match higher: both Ring Road cameras match
+    # "ring road", but only one is in Rajkot.
+    m = idx.resolve("rajkot ring road")
+    assert m and m[0].id == "GJ-RAJ-002", m
+    ids = [x.id for x in m]
+    assert "GJ-SUR-009" not in ids or ids.index("GJ-RAJ-002") < ids.index("GJ-SUR-009"), m
+
+    # Term saturation and idf together: a rare term must outweigh a repeated
+    # common one.
+    assert idx._idf("junagadh") > idx._idf("road")
+
+    # Kind filtering, and the promise that a resolution is always a real row.
+    known = {(k, i) for k, i, _, _ in _SYNTHETIC}
+    for q in ("junagadh bypass", "surat", "FIR-2026-118", "hard shoulder",
+              "GJ01AB1234", "toll", "ahmedabad sg highway"):
+        for hit in idx.resolve(q):
+            assert (hit.kind, hit.id) in known, (q, hit)
+
+    m = idx.resolve("bypass", kind="zone")
+    assert m and all(x.kind == "zone" for x in m), m
+    assert idx.resolve("junagadh bypass", kind="watchlist") == []
+
+    # A watchlist entry resolves by its case reference, not only its plate.
+    m = idx.resolve("FIR-2026-118")
+    assert m and m[0].kind == "watchlist" and m[0].id == "7", m
+
+    # A vision-language description resolves to its detection, and the id it
+    # returns is the detection id the facts are then read from.
+    m = idx.resolve("the black SUV with a roof rack")
+    assert m and m[0].kind == "vehicle" and m[0].id == "9182", m
+    m = idx.resolve("Om Travels")
+    assert m and m[0].kind == "vehicle" and m[0].id == "9200", m
+    # ...and the description index must not answer questions about cameras.
+    m = idx.resolve("rajkot ring road")
+    assert m and m[0].kind == "camera", m
+
+    # An empty corpus must be answerable, not an exception.
+    assert build_index([]).resolve("anything") == []
+
+    # A short fragment of a longer word resolves, because an operator says
+    # "toll" and the registry says "Tollnaka".
+    m = idx.resolve("toll")
+    assert m and m[0].id == "GJ-AHM-014" and m[0].via == "trigram", m
+
+    # The fallback is consulted only when token matching found nothing, so it
+    # can never displace a genuine lexical hit.
+    m = idx.resolve("surat ring road")
+    assert m and m[0].via == "bm25" and m[0].id == "GJ-SUR-009", m
+
+    # Intent vocabulary must not sink the mention beside it. "down", "health"
+    # and "coverage" appear in no camera name, so unfiltered they carry maximum
+    # idf into the denominator and defeat a perfectly clear reference.
+    intent = frozenset({"down", "health", "coverage", "faulty", "status",
+                        "detections", "how", "many", "is", "in", "for"})
+    m = idx.resolve("is GJ-JUN-004 down", ignore=intent)
+    assert m and m[0].id == "GJ-JUN-004", m
+    m = idx.resolve("camera health for junagadh bypass", ignore=intent)
+    assert m and m[0].id == "GJ-JUN-004", m
+    m = idx.resolve("coverage in rajkot", ignore=intent)
+    assert m and m[0].id == "GJ-RAJ-002", m
+    m = idx.resolve("how many detections on junagad", ignore=intent)
+    assert m and m[0].id == "GJ-JUN-004" and m[0].via == "trigram", m
+
+    # ...and dropping intent words must not open a route to a spurious match.
+    for junk in ("the weather tomorrow", "banana", "xyzzy", "please",
+                 "the camera", "a zone", "show me", "how many detections",
+                 "is it down", "status"):
+        assert idx.resolve(junk, ignore=intent) == [], (
+            junk, idx.resolve(junk, ignore=intent))
+
+    # An ignored word the corpus does know is still matchable, so a camera is
+    # never made unfindable by a word that is also an intent keyword.
+    assert "rajkot" not in intent
+    m = idx.resolve("north ring road", ignore=frozenset({"north"}))
+    assert m and m[0].id in ("GJ-RAJ-002", "GJ-SUR-009"), m
+
+    # Normalisation is what makes the character fallback work at all.
+    assert normalise("Junagadh-Bypass ANPR") == "junagadhbypassanpr"
+    assert tokenise("GJ-AHM-014") == ["gj", "ahm", "014"]
+
+    # A one-word description is not a search key. Every yellow vehicle scores
+    # identically for "yellow", so indexing one would let the tie-break decide
+    # which detection the assistant presents as the answer.
+    class _Row:
+        body_type = colour = wheels = damage = None
+        roof_rack = tinted_windows = None
+        markings: list = []
+
+        def __init__(self, description, **kw):
+            self.description = description
+            for k, v in kw.items():
+                setattr(self, k, v)
+
+    assert not _worth_indexing(_Row("yellow"), "yellow")
+    assert not _worth_indexing(_Row(""), "")
+    assert _worth_indexing(_Row("yellow van"), "yellow van")
+    # One structured field is not enough on its own either...
+    assert not _worth_indexing(_Row("yellow", colour="yellow"), "yellow")
+    # ...but two describe a vehicle specifically enough to resolve to.
+    assert _worth_indexing(_Row("yellow", colour="yellow", body_type="van"),
+                           "yellow")
+
+    print("retrieval self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/config.py b/netra/config.py
index 5224f5d..817613b 100644
--- a/netra/config.py
+++ b/netra/config.py
@@ -106,6 +106,40 @@ VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
 
 # --- stream handling ---------------------------------------------------------
 RECONNECT_BASE_S = 2.0
 RECONNECT_MAX_S = 30.0
 # A backwards PTS jump larger than this means the loop restarted, not jitter.
 LOOP_CUT_THRESHOLD_MS = 2000.0
+
+# --- retention ---------------------------------------------------------------
+# Evidence and detections both grow without limit while the pipeline runs: one
+# JPEG and one row per observed vehicle, forever. These are the ceilings the
+# pruner enforces so a long deployment cannot fill the disk or the database.
+# 5 GiB is roughly a fortnight of evidence at the measured crop size on this
+# grid, and leaves room on the smallest edge node we target.
+EVIDENCE_MAX_BYTES = int(os.getenv("NETRA_EVIDENCE_MAX_BYTES", str(5 * 1024**3)))
+# Beyond a week an evidence crop is no longer operationally useful; the
+# detection row and its metadata survive far longer for trend and route work.
+EVIDENCE_MAX_AGE_DAYS = int(os.getenv("NETRA_EVIDENCE_MAX_AGE_DAYS", "7"))
+# Row cap on the detections table. SQLite query plans on the indexed columns
+# stay comfortable to a few million rows; past that the console's time-window
+# queries start to be felt.
+DETECTION_MAX_ROWS = int(os.getenv("NETRA_DETECTION_MAX_ROWS", "2000000"))
+# Floor under the row cap: recent detections are never pruned however far over
+# the cap the table is, because they are what an operator is actively querying.
+DETECTION_KEEP_DAYS = int(os.getenv("NETRA_DETECTION_KEEP_DAYS", "1"))
+
+# --- vehicle attributes (vision-language descriptions) -----------------------
+# A description an officer can read, search and testify to, on a grid where no
+# plate is recoverable. Florence-2-base is 0.23B parameters under an MIT
+# licence and sits in ~450 MiB of VRAM beside YOLOv8m, ReID and OCR.
+ATTRIBUTE_MODEL = os.getenv("NETRA_ATTRIBUTE_MODEL", "microsoft/Florence-2-base")
+ATTRIBUTES_ENABLED = os.getenv("NETRA_ATTRIBUTES", "1") not in ("0", "false", "no")
+# Per escalated camera, how rarely the opportunistic "largest vehicle" pass may
+# run. A caption costs roughly a second of GPU; once every thirty seconds per
+# camera keeps the whole grid's opportunistic spend to a small fraction of one
+# camera's detection budget.
+ATTRIBUTE_ESCALATED_INTERVAL_S = float(
+    os.getenv("NETRA_ATTRIBUTE_INTERVAL", "30"))
+# Bounded and dropping: enrichment must never apply back-pressure to detection.
+# Measured precedent for why - unbounded overlay OCR cost 71% of frames.
+ATTRIBUTE_QUEUE_SIZE = int(os.getenv("NETRA_ATTRIBUTE_QUEUE", "32"))
diff --git a/netra/core/db.py b/netra/core/db.py
index 8caf2bb..77c129a 100644
--- a/netra/core/db.py
+++ b/netra/core/db.py
@@ -11,9 +11,46 @@ class Base(DeclarativeBase):
 
 _connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
 engine = create_engine(DB_URL, connect_args=_connect_args, pool_pre_ping=True)
 SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
 
 
+#: Columns added to tables that already exist in the field. `create_all` only
+#: creates missing *tables*, so a column added to a model would be absent from
+#: an operator's existing data/netra.db and every ORM read of that table would
+#: fail. These are applied additively at start-up rather than asking anyone to
+#: delete their evidence database.
+#: ponytail: a hand-kept list, not a migration tool. Its ceiling is additive,
+#: nullable/defaulted columns on SQLite; a type change or a drop needs Alembic.
+_ADDED_COLUMNS = [
+    ("traffic_stats", "cumulative_total", "INTEGER DEFAULT 0"),
+    ("traffic_stats", "loops_seen", "INTEGER DEFAULT 0"),
+    ("mined_journeys", "min_similarity", "REAL DEFAULT 0.84"),
+    ("mined_journeys", "truncated", "BOOLEAN DEFAULT 0"),
+    # Defaults false, which is the honest reading of every row already in an
+    # operator's store: those scene times were anchored on a single overlay
+    # reading and are not evidence of when anything happened.
+    ("detections", "scene_time_corroborated", "BOOLEAN DEFAULT 0"),
+    # Nullable rather than defaulted to 1: an existing row's plate was read an
+    # unknown number of times, and claiming one vote would be inventing a fact.
+    ("detections", "plate_votes", "INTEGER"),
+]
+
+
+def _apply_added_columns() -> None:
+    from sqlalchemy import inspect, text
+    inspector = inspect(engine)
+    existing = set(inspector.get_table_names())
+    with engine.begin() as conn:
+        for table, column, ddl in _ADDED_COLUMNS:
+            if table not in existing:
+                continue  # create_all just made it, with the column present
+            have = {c["name"] for c in inspector.get_columns(table)}
+            if column in have:
+                continue
+            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
+
+
 def init_db() -> None:
     from netra.core import models  # noqa: F401  (registers mappers)
     Base.metadata.create_all(engine)
+    _apply_added_columns()
diff --git a/netra/core/models.py b/netra/core/models.py
index 0149cfc..f1cd5b6 100644
--- a/netra/core/models.py
+++ b/netra/core/models.py
@@ -74,23 +74,34 @@ class Detection(Base):
     # Timing. pts_ms is the stream's own clock and is authoritative for any
     # elapsed-time maths; wall_time is only for display and correlation.
     pts_ms: Mapped[float] = mapped_column(Float)
     wall_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
     # timestamp burned into the video by the source camera, when parsed
     scene_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
+    # Whether that timestamp came from an anchor two independent overlay
+    # readings agreed on. False on every row written before corroborated
+    # anchoring existed, and those rows include provably wrong spans dated
+    # 2025-06-14, 2026-06-24 and 2028-06-13 - so anything reasoning over
+    # elapsed time must treat an uncorroborated scene_time as absent rather
+    # than as evidence. See netra/core/timing.py.
+    scene_time_corroborated: Mapped[bool] = mapped_column(
+        Boolean, default=False, server_default="0")
 
     vehicle_class: Mapped[str] = mapped_column(String(16))
     confidence: Mapped[float] = mapped_column(Float)
     bbox: Mapped[list] = mapped_column(JSON)  # [x1, y1, x2, y2]
 
     colour: Mapped[str | None] = mapped_column(String(16))
     embedding: Mapped[list | None] = mapped_column(JSON)  # appearance vector
 
     plate_text: Mapped[str | None] = mapped_column(String(32), index=True)
     plate_conf: Mapped[float | None] = mapped_column(Float)
     plate_chars: Mapped[int | None] = mapped_column(Integer)  # chars actually recovered
+    # How many per-frame OCR reads voted for plate_text. 1 is a lone guess;
+    # a higher count is the only thing distinguishing it from a consensus.
+    plate_votes: Mapped[int | None] = mapped_column(Integer)
     plate_bbox: Mapped[list | None] = mapped_column(JSON)
 
     evidence_path: Mapped[str | None] = mapped_column(String(256))
     track_id: Mapped[int | None] = mapped_column(Integer, index=True)
 
     camera: Mapped["Camera"] = relationship(back_populates="detections")
@@ -146,6 +157,145 @@ class AuditLog(Base):
     id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
     actor: Mapped[str] = mapped_column(String(64))
     action: Mapped[str] = mapped_column(String(64))
     target: Mapped[str | None] = mapped_column(String(256))
     detail: Mapped[dict | None] = mapped_column(JSON)
     at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
+
+
+class ZoneRule(Base):
+    """A spatial rule on one camera: intrusion, line crossing, or loitering.
+
+    Coordinates are normalised 0-1 so a rule survives the camera being
+    re-encoded at a different resolution, which matters on a grid carrying five
+    different resolutions.
+    """
+    __tablename__ = "zone_rules"
+
+    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
+    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
+    name: Mapped[str] = mapped_column(String(128))
+    rule: Mapped[str] = mapped_column(String(16))       # intrusion|crossing|loitering
+    points: Mapped[list] = mapped_column(JSON)          # [[x, y], ...] normalised
+    classes: Mapped[list | None] = mapped_column(JSON)  # empty/None means any
+    severity: Mapped[str] = mapped_column(String(16), default="medium")
+    dwell_s: Mapped[float] = mapped_column(Float, default=30.0)
+    active: Mapped[bool] = mapped_column(Boolean, default=True)
+    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
+
+
+class ZoneEventRow(Base):
+    """One triggered zone rule."""
+    __tablename__ = "zone_events"
+
+    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
+    zone_rule_id: Mapped[int] = mapped_column(ForeignKey("zone_rules.id"), index=True)
+    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
+    rule: Mapped[str] = mapped_column(String(16))
+    track_id: Mapped[int | None] = mapped_column(Integer)
+    object_class: Mapped[str | None] = mapped_column(String(16))
+    direction: Mapped[str | None] = mapped_column(String(8))
+    detail: Mapped[str] = mapped_column(Text)
+    severity: Mapped[str] = mapped_column(String(16), default="medium")
+    evidence_path: Mapped[str | None] = mapped_column(String(256))
+    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
+    at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
+                                         default=_utcnow, index=True)
+
+
+class TrafficStat(Base):
+    """Per-camera traffic counts over a time bucket.
+
+    Detections answer "what was seen"; these answer "how much traffic passed",
+    which is the question a planner or a control room actually asks.
+    """
+    __tablename__ = "traffic_stats"
+
+    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
+    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
+    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
+    bucket_seconds: Mapped[int] = mapped_column(Integer, default=60)
+    #: traffic counted *during this bucket*. Baselines learn from this, so it
+    #: must not be cumulative: a monotonically rising figure would make the
+    #: learned "norm" a function of uptime rather than of how busy the road is.
+    total: Mapped[int] = mapped_column(Integer, default=0)
+    #: the camera's running total since the tracker was created, kept because a
+    #: sandbox recording loops and an operator still wants the headline figure.
+    cumulative_total: Mapped[int] = mapped_column(Integer, default=0)
+    #: how many times the recording had replayed when this bucket was written,
+    #: so the cumulative figure above can be read honestly.
+    loops_seen: Mapped[int] = mapped_column(Integer, default=0)
+    counts_by_class: Mapped[dict] = mapped_column(JSON, default=dict)
+    directions: Mapped[dict] = mapped_column(JSON, default=dict)
+    mean_dwell_s: Mapped[float] = mapped_column(Float, default=0.0)
+
+
+class MinedJourney(Base):
+    """A cross-camera journey mined from an indexed loop.
+
+    Stored so the console can show journeys without re-running the mining,
+    which is an O(n²) appearance comparison over an entire indexed recording
+    and far too slow to sit inside a console refresh. Every row keeps the
+    evidence behind it — the per-hop similarity, distance and implied speed —
+    because a journey is an appearance-based candidate, never an
+    identification, and the operator confirming it needs the arithmetic.
+    """
+    __tablename__ = "mined_journeys"
+
+    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
+    #: only cameras sharing a recorded clock can be chained, so a journey
+    #: belongs to exactly one recording session
+    time_group: Mapped[str] = mapped_column(String(64), index=True)
+    hop_count: Mapped[int] = mapped_column(Integer, default=0)
+    cameras: Mapped[list] = mapped_column(JSON, default=list)
+    total_km: Mapped[float] = mapped_column(Float, default=0.0)
+    elapsed_s: Mapped[float] = mapped_column(Float, default=0.0)
+    mean_similarity: Mapped[float] = mapped_column(Float, default=0.0)
+    confidence: Mapped[float] = mapped_column(Float, default=0.0, index=True)
+    #: the appearance threshold this journey was mined at, so a later request
+    #: asking for a stricter one can tell whether these rows answer it
+    min_similarity: Mapped[float] = mapped_column(Float, default=0.84)
+    #: the chain was cut at a ceiling rather than ending naturally
+    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
+    #: scene time, not wall time: these bound the journey on the recorded clock
+    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
+    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
+    hops: Mapped[list] = mapped_column(JSON, default=list)
+    note: Mapped[str | None] = mapped_column(Text)
+    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
+                                                 default=_utcnow, index=True)
+
+
+class VehicleAttributeRow(Base):
+    """A vision-language description of one detection's evidence crop.
+
+    One row per detection at most: the caption describes that crop, and
+    re-describing it would only re-derive the same words at GPU cost. Kept
+    beside the detection rather than on it because most detections never get
+    one - extraction is tiered, so this table is sparse by design.
+
+    Every structured field is parsed from `raw_caption`, which is stored so an
+    operator (or a court) can see exactly what the model said before the
+    keyword parser reduced it. A description is evidence, not identification.
+    """
+    __tablename__ = "vehicle_attributes"
+
+    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
+    detection_id: Mapped[int] = mapped_column(
+        ForeignKey("detections.id"), unique=True, index=True)
+
+    body_type: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
+    colour: Mapped[str | None] = mapped_column(String(16), index=True)
+    tinted_windows: Mapped[bool | None] = mapped_column(Boolean)
+    wheels: Mapped[str] = mapped_column(String(16), default="unknown")
+    roof_rack: Mapped[bool | None] = mapped_column(Boolean)
+    markings: Mapped[list] = mapped_column(JSON, default=list)
+    damage: Mapped[list] = mapped_column(JSON, default=list)
+
+    description: Mapped[str] = mapped_column(Text, default="")
+    raw_caption: Mapped[str] = mapped_column(Text, default="")
+    model: Mapped[str] = mapped_column(String(64), default="")
+    confidence: Mapped[float] = mapped_column(Float, default=0.0)
+    #: how this row came to exist: alert | zone | operator | escalated
+    source: Mapped[str] = mapped_column(String(16), default="operator")
+    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
+                                                 default=_utcnow, index=True)
diff --git a/netra/core/retention.py b/netra/core/retention.py
new file mode 100644
index 0000000..469d0d5
--- /dev/null
+++ b/netra/core/retention.py
@@ -0,0 +1,439 @@
+"""Retention: keeping two unbounded stores inside a fixed budget.
+
+The pipeline writes one evidence JPEG and one detection row for every vehicle
+it sees. On this grid that is thousands per minute, forever, and nothing in the
+platform previously deleted any of it. A deployment left running therefore ends
+with a full disk or a detections table too large to query - both of which take
+the whole system down rather than degrading it.
+
+Two pruners, with one rule they share: evidence attached to an unacknowledged
+alert or zone event is never deleted, and a detection an alert points at is
+never deleted. That evidence is the reason the alert is actionable - pruning it
+would leave an operator an alert they cannot act on, which is worse than
+keeping the file. Both pruners therefore report what they *retained* by that
+rule alongside what they removed, so the ceiling being hit is visible rather
+than silent.
+
+ponytail: pruning is invoked on demand (an endpoint, or a scheduled call),
+not by a background thread. A thread deleting files while inference runs is one
+more thing competing for I/O with the primary duty, and the operator - or
+cron - knows better than we do when the quiet hour is. The ceiling is that a
+platform nobody ever calls this on still fills its disk.
+"""
+from __future__ import annotations
+
+import logging
+import os
+from datetime import datetime, timedelta, timezone
+from pathlib import Path
+
+from netra import config
+
+log = logging.getLogger(__name__)
+
+
+def _session_factory():
+    """Resolved lazily so a self-check can substitute a throwaway database."""
+    from netra.core.db import SessionLocal
+    return SessionLocal
+
+
+def _basename(url_path: str | None) -> str | None:
+    """Evidence is stored as the URL path `/evidence/<file>`; files are not."""
+    if not url_path:
+        return None
+    return url_path.rsplit("/", 1)[-1]
+
+
+def protected_evidence(session_factory=None) -> set[str]:
+    """Evidence filenames that must survive any prune.
+
+    An alert or zone event an operator has not yet acknowledged is still open
+    police work; its picture is the evidence.
+
+    ponytail: acknowledgement is the only signal of continuing interest, so an
+    *acknowledged* alert's crop is prunable the moment it ages out, even where
+    the case behind it is still live. Its ceiling is a case that outlasts the
+    retention window: nothing here knows about cases, and an operator who needs
+    a crop kept beyond it must export it. A case-linked hold - evidence pinned
+    for as long as its FIR is open - is the real fix, and it needs the case
+    reference to travel with the alert from eGujCop rather than being typed in.
+    """
+    from netra.core.models import Alert, Detection, ZoneEventRow
+
+    sf = session_factory or _session_factory()
+    keep: set[str] = set()
+    with sf() as db:
+        rows = (db.query(Detection.evidence_path)
+                .join(Alert, Alert.detection_id == Detection.id)
+                .filter(Alert.acknowledged.is_(False))
+                .filter(Detection.evidence_path.isnot(None)).all())
+        keep.update(n for n in (_basename(r[0]) for r in rows) if n)
+
+        rows = (db.query(ZoneEventRow.evidence_path)
+                .filter(ZoneEventRow.acknowledged.is_(False))
+                .filter(ZoneEventRow.evidence_path.isnot(None)).all())
+        keep.update(n for n in (_basename(r[0]) for r in rows) if n)
+    return keep
+
+
+def prune_evidence(max_bytes: int | None = None, max_age_days: int | None = None,
+                   evidence_dir: Path | None = None,
+                   session_factory=None, dry_run: bool = False) -> dict:
+    """Delete evidence files oldest-first until inside the age and size budget.
+
+    Age is applied first, then the size budget, because an expired file is
+    worthless regardless of how much room is left. Files referenced by an
+    unacknowledged alert or zone event are skipped by both rules and counted
+    separately.
+    """
+    max_bytes = config.EVIDENCE_MAX_BYTES if max_bytes is None else max_bytes
+    max_age_days = (config.EVIDENCE_MAX_AGE_DAYS if max_age_days is None
+                    else max_age_days)
+    directory = Path(evidence_dir) if evidence_dir else config.EVIDENCE
+
+    keep = protected_evidence(session_factory)
+
+    # (mtime, size, path) oldest first. Statting every file is the whole cost
+    # of this pass; at the 5 GiB ceiling that is a few tens of thousands of
+    # entries, which is a fraction of a second.
+    files: list[tuple[float, int, Path]] = []
+    total = 0
+    for entry in os.scandir(directory) if directory.exists() else []:
+        if not entry.is_file():
+            continue
+        try:
+            st = entry.stat()
+        except OSError:
+            continue
+        files.append((st.st_mtime, st.st_size, Path(entry.path)))
+        total += st.st_size
+    files.sort()
+
+    report = {
+        "scanned": len(files), "bytes_before": total,
+        "deleted_expired": 0, "deleted_over_budget": 0,
+        "bytes_freed": 0, "retained_protected": 0,
+        "retained_protected_bytes": 0, "failed": 0,
+        "max_bytes": max_bytes, "max_age_days": max_age_days,
+        "dry_run": dry_run,
+    }
+
+    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).timestamp()
+    remaining: list[tuple[float, int, Path]] = []
+    # A protected file can be reached by both rules in one call - expired
+    # *and* over budget - and it must be reported once, not twice. This figure
+    # is audited into storage.prune, so a doubled count is a doubled claim.
+    counted_protected: set[str] = set()
+
+    def _remove(item, reason: str) -> None:
+        _mtime, size, path = item
+        if path.name in keep:
+            if path.name not in counted_protected:
+                counted_protected.add(path.name)
+                report["retained_protected"] += 1
+                report["retained_protected_bytes"] += size
+            remaining.append(item)
+            return
+        if not dry_run:
+            try:
+                path.unlink()
+            except OSError:
+                report["failed"] += 1
+                remaining.append(item)
+                return
+        report[reason] += 1
+        report["bytes_freed"] += size
+
+    for item in files:
+        if item[0] < cutoff:
+            _remove(item, "deleted_expired")
+        else:
+            remaining.append(item)
+
+    # Size budget over what age did not already take, still oldest-first.
+    live = total - report["bytes_freed"]
+    if live > max_bytes:
+        over_budget, remaining = remaining, []
+        for item in over_budget:
+            if live <= max_bytes:
+                remaining.append(item)
+                continue
+            before = report["bytes_freed"]
+            _remove(item, "deleted_over_budget")
+            live -= report["bytes_freed"] - before
+
+    report["bytes_after"] = total - report["bytes_freed"]
+    report["deleted"] = report["deleted_expired"] + report["deleted_over_budget"]
+    if report["deleted"] or report["retained_protected"]:
+        log.info("evidence prune: removed %d files (%.1f MiB), retained %d "
+                 "attached to open alerts", report["deleted"],
+                 report["bytes_freed"] / 1024**2, report["retained_protected"])
+    return report
+
+
+def prune_detections(max_rows: int | None = None, keep_days: int | None = None,
+                     session_factory=None, dry_run: bool = False) -> dict:
+    """Delete the oldest detections beyond the row cap.
+
+    Two things are never deleted: a detection any alert points at (the alert's
+    foreign key would dangle, and the alert would lose the sighting it was
+    raised on), and anything inside `keep_days`, which is the window an
+    operator is actively querying.
+    """
+    from netra.core.models import Alert, Detection
+
+    max_rows = config.DETECTION_MAX_ROWS if max_rows is None else max_rows
+    keep_days = config.DETECTION_KEEP_DAYS if keep_days is None else keep_days
+    sf = session_factory or _session_factory()
+    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
+
+    report = {"rows_before": 0, "deleted": 0, "retained_alerted": 0,
+              "retained_recent": 0, "max_rows": max_rows,
+              "keep_days": keep_days, "dry_run": dry_run}
+
+    with sf() as db:
+        total = db.query(Detection.id).count()
+        report["rows_before"] = total
+        excess = total - max_rows
+        if excess <= 0:
+            report["rows_after"] = total
+            return report
+
+        # Oldest-first, take only as many as the cap demands. The two
+        # protections are applied as filters rather than after selection, so a
+        # table that is entirely alert-referenced simply deletes nothing
+        # instead of looping.
+        alerted = db.query(Alert.detection_id).distinct().subquery()
+        candidates = (db.query(Detection.id)
+                      .filter(Detection.wall_time < cutoff)
+                      .filter(Detection.id.notin_(db.query(alerted.c.detection_id)))
+                      .order_by(Detection.wall_time.asc())
+                      .limit(excess).all())
+        ids = [row[0] for row in candidates]
+        report["retained_alerted"] = db.query(Alert.detection_id).distinct().count()
+        report["retained_recent"] = (db.query(Detection.id)
+                                     .filter(Detection.wall_time >= cutoff).count())
+
+        if ids and not dry_run:
+            db.query(Detection).filter(Detection.id.in_(ids)).delete(
+                synchronize_session=False)
+            db.commit()
+        report["deleted"] = len(ids)
+        report["rows_after"] = total - report["deleted"]
+        report["still_over_cap"] = max(0, report["rows_after"] - max_rows)
+
+    if report["deleted"]:
+        log.info("detection prune: removed %d rows, %d retained by an alert",
+                 report["deleted"], report["retained_alerted"])
+    return report
+
+
+def storage_report(evidence_dir: Path | None = None, session_factory=None) -> dict:
+    """What the two stores currently hold, against what they are allowed to."""
+    from netra.core.models import Alert, Detection, ZoneEventRow
+
+    directory = Path(evidence_dir) if evidence_dir else config.EVIDENCE
+    count = 0
+    total = 0
+    for entry in os.scandir(directory) if directory.exists() else []:
+        if entry.is_file():
+            try:
+                total += entry.stat().st_size
+            except OSError:
+                continue
+            count += 1
+
+    sf = session_factory or _session_factory()
+    with sf() as db:
+        detections = db.query(Detection.id).count()
+        alerts = db.query(Alert.id).count()
+        open_alerts = db.query(Alert.id).filter(Alert.acknowledged.is_(False)).count()
+        open_zone = (db.query(ZoneEventRow.id)
+                     .filter(ZoneEventRow.acknowledged.is_(False)).count())
+
+    return {
+        "evidence": {
+            "files": count,
+            "bytes": total,
+            "mib": round(total / 1024**2, 1),
+            "max_bytes": config.EVIDENCE_MAX_BYTES,
+            "max_age_days": config.EVIDENCE_MAX_AGE_DAYS,
+            "percent_of_budget": round(
+                100.0 * total / config.EVIDENCE_MAX_BYTES, 1)
+            if config.EVIDENCE_MAX_BYTES else None,
+        },
+        "detections": {
+            "rows": detections,
+            "max_rows": config.DETECTION_MAX_ROWS,
+            "keep_days": config.DETECTION_KEEP_DAYS,
+            "percent_of_cap": round(
+                100.0 * detections / config.DETECTION_MAX_ROWS, 1)
+            if config.DETECTION_MAX_ROWS else None,
+        },
+        "alerts": {"rows": alerts, "unacknowledged": open_alerts},
+        "zone_events": {"unacknowledged": open_zone},
+    }
+
+
+def _self_check() -> None:
+    """Exercise both pruners against a temporary directory and database.
+
+    Deliberately never touches data/netra.db or data/evidence: this runs on a
+    developer's machine with a live pipeline's evidence sitting on disk, and a
+    self-check that deletes real evidence would be worse than no self-check.
+    """
+    import tempfile
+
+    from sqlalchemy import create_engine
+    from sqlalchemy.orm import sessionmaker
+
+    from netra.core.db import Base
+    from netra.core import models  # noqa: F401  (registers the mappers)
+
+    with tempfile.TemporaryDirectory() as tmp:
+        root = Path(tmp)
+        evidence = root / "evidence"
+        evidence.mkdir()
+        engine = create_engine(f"sqlite:///{root / 'check.db'}")
+        Base.metadata.create_all(engine)
+        sf = sessionmaker(bind=engine, expire_on_commit=False)
+        try:
+            _check_body(evidence, sf)
+        finally:
+            # Windows will not remove the temporary directory while SQLite
+            # still holds the file open, which would mask the real failure.
+            engine.dispose()
+
+    print("retention self-check passed")
+
+
+def _check_body(evidence, sf) -> None:
+        """The body of the self-check, over a temporary directory and database."""
+        from netra.core.models import Alert, Camera, Detection, ZoneEventRow
+
+        now = datetime.now(timezone.utc)
+        old = now - timedelta(days=30)
+
+        def write(name: str, size: int, age_days: float) -> Path:
+            path = evidence / name
+            path.write_bytes(b"\0" * size)
+            stamp = (now - timedelta(days=age_days)).timestamp()
+            os.utime(path, (stamp, stamp))
+            return path
+
+        with sf() as db:
+            db.add(Camera(id="CAM1", name="check"))
+            db.flush()
+            # d1 is old and attached to an OPEN alert - must survive both
+            # pruners. d2 is old and attached to an ACKNOWLEDGED alert - its
+            # file may go, but the row may not (the alert's key points at it).
+            # d3 is old and unreferenced - the only row eligible for deletion.
+            for i, (path, when) in enumerate(
+                    [("/evidence/open.jpg", old), ("/evidence/ack.jpg", old),
+                     ("/evidence/plain.jpg", old)], start=1):
+                db.add(Detection(id=i, camera_id="CAM1", pts_ms=0.0,
+                                 wall_time=when, vehicle_class="car",
+                                 confidence=0.9, bbox=[0, 0, 10, 10],
+                                 evidence_path=path))
+            db.add(Alert(detection_id=1, watchlist_id=1, camera_id="CAM1",
+                         score=0.9, match_type="exact", reasons={},
+                         acknowledged=False))
+            db.add(Alert(detection_id=2, watchlist_id=1, camera_id="CAM1",
+                         score=0.9, match_type="exact", reasons={},
+                         acknowledged=True))
+            db.add(ZoneEventRow(zone_rule_id=1, camera_id="CAM1",
+                                rule="intrusion", detail="check",
+                                evidence_path="/evidence/zone_open.jpg",
+                                acknowledged=False))
+            db.add(ZoneEventRow(zone_rule_id=1, camera_id="CAM1",
+                                rule="intrusion", detail="check",
+                                evidence_path="/evidence/zone_ack.jpg",
+                                acknowledged=True))
+            db.commit()
+
+        for name in ("open.jpg", "ack.jpg", "plain.jpg",
+                     "zone_open.jpg", "zone_ack.jpg"):
+            write(name, 1000, age_days=30)
+
+        keep = protected_evidence(sf)
+        assert keep == {"open.jpg", "zone_open.jpg"}, keep
+
+        # --- age rule, with the protection in force -------------------------
+        r = prune_evidence(max_bytes=10**9, max_age_days=7,
+                           evidence_dir=evidence, session_factory=sf)
+        assert r["deleted_expired"] == 3, r
+        assert r["retained_protected"] == 2, r
+        assert r["retained_protected_bytes"] == 2000, r
+        survivors = {p.name for p in evidence.iterdir()}
+        assert survivors == {"open.jpg", "zone_open.jpg"}, survivors
+
+        # --- size budget, oldest first --------------------------------------
+        for i in range(5):
+            write(f"recent{i}.jpg", 1000, age_days=i)  # recent0 newest
+        # Seven 1000-byte files against a 4500-byte budget. The two protected
+        # ones are the oldest, so they are visited first and free nothing;
+        # three unprotected files then go before the budget is met.
+        r = prune_evidence(max_bytes=4500, max_age_days=365,
+                           evidence_dir=evidence, session_factory=sf)
+        left = {p.name for p in evidence.iterdir()}
+        # Protected files count against the budget but cannot be removed, so
+        # the budget is honoured only as far as the protection allows.
+        assert "open.jpg" in left and "zone_open.jpg" in left, left
+        assert "recent4.jpg" not in left, left   # oldest unprotected went first
+        assert "recent0.jpg" in left, left       # newest survived
+        assert r["deleted_over_budget"] == 3, r
+        assert r["retained_protected"] == 2, r
+
+        # Both rules in one call. A protected file is expired *and* over
+        # budget, so it passes through the removal path twice; it must still
+        # be reported once. Nothing was covering this, which is how a doubled
+        # count reached an audit record.
+        r = prune_evidence(max_bytes=0, max_age_days=0, evidence_dir=evidence,
+                           session_factory=sf, dry_run=True)
+        assert r["retained_protected"] == 2, r
+        assert r["retained_protected_bytes"] == 2000, r
+        assert r["deleted_expired"] == r["scanned"] - 2, r
+        assert r["deleted_over_budget"] == 0, r   # age already took them all
+
+        # A dry run must report the same intent without touching the disk.
+        before = sorted(p.name for p in evidence.iterdir())
+        r = prune_evidence(max_bytes=0, max_age_days=365, evidence_dir=evidence,
+                           session_factory=sf, dry_run=True)
+        assert sorted(p.name for p in evidence.iterdir()) == before
+        assert r["deleted"] >= 1 and r["retained_protected"] == 2, r
+
+        # --- detection rows -------------------------------------------------
+        # Cap of 1 against 3 rows: two are alert-referenced and must survive,
+        # so exactly one row goes and the table stays above its cap. Reporting
+        # that honestly matters more than forcing the cap.
+        r = prune_detections(max_rows=1, keep_days=1, session_factory=sf)
+        assert r["deleted"] == 1, r
+        assert r["retained_alerted"] == 2, r
+        assert r["still_over_cap"] == 1, r
+        with sf() as db:
+            left_ids = sorted(i for (i,) in db.query(Detection.id).all())
+        assert left_ids == [1, 2], left_ids
+
+        # Nothing left to take: the remaining rows are all alert-referenced.
+        r = prune_detections(max_rows=0, keep_days=1, session_factory=sf)
+        assert r["deleted"] == 0, r
+
+        # keep_days protects recent rows even far over the cap.
+        with sf() as db:
+            db.add(Detection(id=99, camera_id="CAM1", pts_ms=0.0,
+                             wall_time=now, vehicle_class="car",
+                             confidence=0.5, bbox=[0, 0, 1, 1]))
+            db.commit()
+        r = prune_detections(max_rows=0, keep_days=7, session_factory=sf)
+        assert r["deleted"] == 0 and r["retained_recent"] == 1, r
+
+        rep = storage_report(evidence_dir=evidence, session_factory=sf)
+        assert rep["detections"]["rows"] == 3, rep
+        assert rep["alerts"]["unacknowledged"] == 1, rep
+        assert rep["zone_events"]["unacknowledged"] == 1, rep
+        assert rep["evidence"]["files"] == len(list(evidence.iterdir())), rep
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/core/timing.py b/netra/core/timing.py
new file mode 100644
index 0000000..2118ace
--- /dev/null
+++ b/netra/core/timing.py
@@ -0,0 +1,89 @@
+"""When a sighting actually happened.
+
+The sandbox replays recordings, so the wall clock records when we happened to
+connect to a feed, not when the scene occurred. Anything that orders or
+subtracts sighting times - route reconstruction, cloned-plate detection - must
+agree on the same preference, otherwise two modules reading the same rows can
+reach contradictory conclusions about the same vehicle.
+
+A scene time is only usable where the anchor behind it was corroborated by two
+independent overlay readings. A single misread digit anchors a whole stream and
+mis-times every sighting on it; this grid produced spans dated 2025-06-14,
+2026-06-24 and 2028-06-13 that way, each from one bad read that passed every
+syntactic check. Rows written before corroborated anchoring landed carry
+`scene_time_corroborated` false, and are treated here exactly as if they had no
+scene time at all.
+"""
+from __future__ import annotations
+
+from datetime import datetime
+
+
+def scene_time(det) -> datetime | None:
+    """The sighting's scene time, or None where it cannot be trusted.
+
+    The single place that decides what "has a scene clock" means, so that a
+    row excluded from journey mining is the same row excluded from route
+    elapsed-time maths.
+    """
+    at = getattr(det, "scene_time", None)
+    if at is None:
+        return None
+    if not getattr(det, "scene_time_corroborated", False):
+        return None
+    return at
+
+
+def sighting_time(det) -> datetime:
+    """Prefer the timestamp burned into the source video over our own clock.
+
+    Where the camera's own overlay has been parsed *and corroborated*, that is
+    the only meaningful ordering; wall time is the fallback for feeds with no
+    readable overlay. Wall time orders sightings within one connection but says
+    nothing about when the scene occurred, so callers making a cross-camera
+    claim must gate on `scene_time()` rather than reading this and hoping.
+    """
+    return scene_time(det) or det.wall_time
+
+
+def _self_check() -> None:
+    """Pin the preference, and that an uncorroborated overlay is not used."""
+    from datetime import timedelta, timezone
+
+    class Det:
+        def __init__(self, scene, corroborated, wall):
+            self.scene_time = scene
+            self.scene_time_corroborated = corroborated
+            self.wall_time = wall
+
+    overlay = datetime(2026, 6, 13, 23, 20, tzinfo=timezone.utc)
+    connected = overlay + timedelta(days=400)
+
+    # A corroborated overlay wins over our own clock. That is the whole point:
+    # wall time here is when we dialled the recording, not when the car passed.
+    good = Det(overlay, True, connected)
+    assert scene_time(good) == overlay
+    assert sighting_time(good) == overlay
+
+    # An uncorroborated one is not a scene time at all.
+    bad = Det(overlay, False, connected)
+    assert scene_time(bad) is None
+    assert sighting_time(bad) == connected
+
+    # A row from a store written before the column existed reads as absent
+    # rather than raising, because getattr defaults false.
+    class Legacy:
+        scene_time = overlay
+        wall_time = connected
+    assert scene_time(Legacy()) is None
+
+    # No overlay at all: wall time, and callers that need a real scene clock
+    # can tell the difference by asking scene_time() instead.
+    none = Det(None, False, connected)
+    assert scene_time(none) is None and sighting_time(none) == connected
+
+    print("timing self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/pipeline.py b/netra/pipeline.py
index 4d6ad3c..23c5b28 100644
--- a/netra/pipeline.py
+++ b/netra/pipeline.py
@@ -12,24 +12,32 @@ import time
 from datetime import datetime, timezone
 
 import cv2
 
 from netra import config
 from netra.analytics.inference import InferenceEngine
-from netra.analytics.matching import score_match
+from netra.analytics.matching import WatchlistIndex, score_match
 from netra.core.db import SessionLocal
-from netra.core.models import Alert, Camera, Detection, WatchlistEntry
+from netra.core.models import (Alert, Camera, Detection, TrafficStat,
+                               VehicleAttributeRow, WatchlistEntry,
+                               ZoneEventRow, ZoneRule)
 from netra.core.notify import NOTIFIER
 from netra.ingest.stream import IngestSupervisor
 
 log = logging.getLogger(__name__)
 
 #: Detections are persisted in batches rather than one transaction each.
 WRITE_BATCH_SIZE = 50
 WRITE_INTERVAL_S = 1.0
 
+#: How long after an alert a description may still be pushed to the console as
+#: a live update. Past this the operator has already read and acted on the
+#: alert card, so an arriving caption is noise on the wire; the row is still
+#: persisted and the console fetches it on demand.
+ATTRIBUTE_BROADCAST_BOUND_S = 3.0
+
 
 class Pipeline:
     def __init__(self):
         self.engine = InferenceEngine(
             on_detection=self._handle_detection,
             on_vehicles_present=self._handle_vehicles_present)
@@ -39,21 +47,50 @@ class Pipeline:
 
         # Alerts are pushed to connected consoles from here.
         self.alert_subscribers: list[queue.Queue] = []
         self._lock = threading.Lock()
 
         self._watchlist_cache: list[dict] = []
+        self._watchlist_index = WatchlistIndex([])
         self._watchlist_loaded_at = 0.0
         self.running = False
         self.started_at: datetime | None = None
 
         # Persistence runs off the inference thread.
         self._write_queue: queue.Queue = queue.Queue(maxsize=4000)
         self._stop_writer = threading.Event()
         self._writer: threading.Thread | None = None
-        self.stats = {"written": 0, "write_dropped": 0}
+        self.stats = {"written": 0, "write_dropped": 0, "zone_events": 0,
+                      "traffic_buckets": 0}
+
+        # Counters per camera at the last traffic flush, so each bucket can
+        # record the traffic during it rather than the running total.
+        self._traffic_last_total: dict[str, int] = {}
+        self._traffic_last_counts: dict[str, dict[str, int]] = {}
+
+        # Zone rules are evaluated inside the inference engine, where the
+        # tracks live; the pipeline supplies the engine and receives events.
+        from netra.analytics.zones import ZoneEngine
+        self.zone_engine = ZoneEngine()
+        self.engine.zone_engine = self.zone_engine
+        self.engine.on_zone_event = self._handle_zone_event
+        self._last_traffic_flush = 0.0
+
+        # Vision-language descriptions run on their own daemon thread behind a
+        # bounded queue that drops when full. Detection is the primary duty and
+        # a caption costs roughly a second of GPU, so this must never be able
+        # to apply back-pressure to inference or to the writer: the measured
+        # precedent is unbounded overlay OCR, which cost 71% of frames.
+        self._attr_queue: queue.Queue = queue.Queue(
+            maxsize=config.ATTRIBUTE_QUEUE_SIZE)
+        self._attr_stop = threading.Event()
+        self._attr_thread: threading.Thread | None = None
+        #: camera_id -> monotonic time of its last opportunistic description
+        self._attr_last: dict[str, float] = {}
+        self.attribute_stats = {"queued": 0, "processed": 0, "dropped": 0,
+                                "failed": 0, "broadcast": 0}
 
     # -- lifecycle -----------------------------------------------------------
     def start(self, camera_ids: list[str] | None = None,
               source_specs: dict | None = None) -> None:
         """Begin processing. `source_specs` overrides how a camera is reached,
         which is how participant-supplied video files are onboarded alongside
@@ -63,43 +100,199 @@ class Pipeline:
         with SessionLocal() as db:
             cams = db.query(Camera).filter(Camera.enabled.is_(True)).all()
             self.engine.camera_capability = {c.id: c.capability for c in cams}
             ids = camera_ids or [c.id for c in cams
                                  if c.capability != "degraded"]
 
+        self._load_zone_rules()
         log.info("starting pipeline over %d cameras", len(ids))
         self.engine.load()
         self.engine.start()
         NOTIFIER.start()
         self._stop_writer.clear()
         self._writer = threading.Thread(target=self._writer_loop,
                                         name="detection-writer", daemon=True)
         self._writer.start()
+        if config.ATTRIBUTES_ENABLED:
+            self._attr_stop.clear()
+            self._attr_thread = threading.Thread(
+                target=self._attribute_loop, name="attribute-worker",
+                daemon=True)
+            self._attr_thread.start()
         self.supervisor.start(ids, source_specs)
         self.running = True
         self.started_at = datetime.now(timezone.utc)
 
     def stop(self) -> None:
         self.supervisor.stop()
         self.engine.stop()
         # Drain whatever is still queued before shutting the writer down.
         self._stop_writer.set()
         if self._writer:
             self._writer.join(timeout=15)
+        # Enrichment is abandoned rather than drained: an operator stopping the
+        # pipeline is not waiting on a caption.
+        self._attr_stop.set()
+        if self._attr_thread:
+            self._attr_thread.join(timeout=5)
         self.running = False
 
+    def _load_zone_rules(self) -> None:
+        """Load zone rules from the database into the evaluation engine."""
+        from netra.analytics.zones import Zone
+        with SessionLocal() as db:
+            rules = db.query(ZoneRule).filter(ZoneRule.active.is_(True)).all()
+            by_camera: dict[str, list] = {}
+            for r in rules:
+                by_camera.setdefault(r.camera_id, []).append(Zone(
+                    zone_id=f"{r.camera_id}:{r.id}", camera_id=r.camera_id,
+                    name=r.name, rule=r.rule, points=r.points,
+                    classes=r.classes or [], severity=r.severity,
+                    dwell_s=r.dwell_s, active=r.active))
+        for camera_id, zones in by_camera.items():
+            self.zone_engine.set_zones(camera_id, zones)
+        if by_camera:
+            log.info("loaded zone rules for %d cameras", len(by_camera))
+
+    def reload_zone_rules(self) -> None:
+        """Called when rules change so a running pipeline picks them up."""
+        self._load_zone_rules()
+
     # -- callbacks -----------------------------------------------------------
     def _handle_vehicles_present(self, camera_id: str) -> None:
         """Escalate a camera to tier-2 sampling while traffic is present."""
         self.supervisor.escalate(camera_id)
 
     def _handle_discontinuity(self, camera_id: str) -> None:
         """The recording looped. Any cross-frame state for this camera is void."""
         log.info("%s discontinuity - resetting per-camera state", camera_id)
         self.engine.reset_camera_state(camera_id)
 
+    def _handle_zone_event(self, event, frame) -> None:
+        """Persist a zone trigger and push it to consoles as an alert.
+
+        Zone rules are how a camera earns its keep when plate recognition is
+        impossible, which on this grid is most of them.
+        """
+        evidence_path = None
+        try:
+            fname = (f"zone_{event.camera_id}_{int(frame.wall_time * 1000)}"
+                     f"_{event.track_id}.jpg")
+            cv2.imwrite(str(config.EVIDENCE / fname), frame.image)
+            evidence_path = f"/evidence/{fname}"
+        except Exception:
+            log.exception("could not write zone evidence frame")
+
+        rule_id = int(event.zone.zone_id.split(":")[-1])
+        with SessionLocal() as db:
+            row = ZoneEventRow(
+                zone_rule_id=rule_id, camera_id=event.camera_id,
+                rule=event.rule, track_id=event.track_id,
+                object_class=event.vehicle_class, direction=event.direction,
+                detail=event.detail, severity=event.zone.severity,
+                evidence_path=evidence_path)
+            db.add(row)
+            db.commit()
+            db.refresh(row)
+            row_id = row.id
+            payload = {
+                "kind": "zone",
+                "id": row_id,
+                "camera_id": event.camera_id,
+                "zone": event.zone.name,
+                "rule": event.rule,
+                "severity": event.zone.severity,
+                "object_class": event.vehicle_class,
+                "direction": event.direction,
+                "detail": event.detail,
+                "evidence": evidence_path,
+                "at": row.at.isoformat(),
+            }
+
+        self.stats["zone_events"] += 1
+        log.warning("ZONE %s on %s: %s", event.rule, event.camera_id, event.detail)
+        self._broadcast(payload)
+        # After the broadcast, for the same reason as on the alert path. A zone
+        # event has no detection row to key attributes to - the evidence is a
+        # whole frame rather than one vehicle - so the description is pushed to
+        # the console if it is ready in time, and not stored.
+        self._submit_attributes(None, evidence_path, "zone",
+                                alert={"zone_event_id": row_id,
+                                       "camera_id": event.camera_id})
+        NOTIFIER.submit({**payload, "plate_watchlist": event.zone.name,
+                         "plate_observed": event.detail,
+                         "match_type": event.rule, "score": 1.0,
+                         "reasons": {"zone": {"score": 1.0,
+                                              "detail": event.detail}}})
+
+    def _bucket_deltas(self, camera_id: str, cumulative: int,
+                       counts: dict) -> tuple[int, dict]:
+        """Traffic during this bucket, from the tracker's cumulative counters.
+
+        Both the total and the class breakdown are differenced against the
+        previous flush, and - this is the part that went wrong - against the
+        *same* previous flush. A tracker recreated mid-run restarts its
+        counters, so a restart shows up as a cumulative smaller than the one
+        last seen, and the whole of it is taken as this bucket's traffic rather
+        than persisting a negative count.
+
+        The class snapshot has to be reset on exactly that condition. Taking
+        the whole cumulative as the total while still differencing the classes
+        against the larger pre-restart snapshot left every class delta at or
+        below zero, so the bucket carried a total with an empty breakdown -
+        a row an analyst can only read as traffic of unknown composition.
+        """
+        previous = self._traffic_last_total.get(camera_id)
+        restarted = previous is not None and cumulative < previous
+        delta = (cumulative if previous is None or restarted
+                 else cumulative - previous)
+        self._traffic_last_total[camera_id] = cumulative
+
+        before = {} if restarted else self._traffic_last_counts.get(camera_id, {})
+        by_class = {k: v - before.get(k, 0) for k, v in counts.items()
+                    if v - before.get(k, 0) > 0}
+        self._traffic_last_counts[camera_id] = dict(counts)
+        return delta, by_class
+
+    def flush_traffic_stats(self, bucket_seconds: int = 60) -> int:
+        """Snapshot per-camera traffic counters into a time bucket.
+
+        `total` is the traffic counted *during this bucket*, obtained by
+        differencing the tracker's cumulative counter against the value at the
+        previous flush. Writing the cumulative figure here - as this once did -
+        made every row larger than the last, because the sandbox replays a
+        fixed recording and the counter spans every replay. Baselines learned
+        from that would describe uptime, not traffic.
+
+        A bucket with no traffic is still written: "this camera saw nothing"
+        is exactly the observation a quiet-road baseline needs, and dropping it
+        would teach the baseline that the road is never empty.
+        """
+        now = datetime.now(timezone.utc)
+        written = 0
+        with SessionLocal() as db:
+            for stats in self.engine.trackers.stats():
+                camera_id = stats["camera_id"]
+                cumulative = stats["total_counted"]
+                delta, by_class = self._bucket_deltas(
+                    camera_id, cumulative, stats["counts_by_class"])
+
+                db.add(TrafficStat(
+                    camera_id=camera_id, bucket_start=now,
+                    bucket_seconds=bucket_seconds,
+                    total=delta,
+                    cumulative_total=cumulative,
+                    loops_seen=stats["loops_seen"],
+                    counts_by_class=by_class,
+                    directions=stats["directions"],
+                    mean_dwell_s=stats["mean_dwell_s"]))
+                written += 1
+            db.commit()
+        self.stats["traffic_buckets"] += written
+        return written
+
     def _handle_detection(self, det) -> None:
         """Hand a detection to the writer. Must not touch disk or the database.
 
         This runs on the inference thread. A busy junction camera produces
         several detections per frame, and doing a JPEG write plus its own
         database transaction for each one starves inference: measured at 76% of
@@ -159,12 +352,15 @@ class Pipeline:
                 colour=det.colour,
                 plate_text=det.plate_text,
                 plate_conf=det.plate_conf,
                 plate_chars=det.plate_chars,
                 plate_bbox=det.plate_bbox,
                 scene_time=det.scene_time,
+                scene_time_corroborated=det.scene_time_corroborated,
+                plate_votes=det.plate_votes,
+                track_id=det.track_id,
                 embedding=det.embedding,
                 evidence_path=evidence_path,
             ))
             dets.append(det)
 
         with SessionLocal() as db:
@@ -175,12 +371,117 @@ class Pipeline:
 
         # Watchlist checking needs the persisted id, so it follows the flush.
         for detection_id, det in zip(ids, dets):
             if det.plate_text:
                 self._check_watchlist(detection_id, det)
 
+        self._queue_escalated_attributes(rows)
+
+    # -- vehicle attributes --------------------------------------------------
+    def _queue_escalated_attributes(self, rows: list) -> None:
+        """Describe the largest vehicle on each escalated camera, rarely.
+
+        Tiering, again: a camera is escalated because it has traffic worth
+        resolving, so it is the one place an unrequested description is worth
+        anything - and even there, at most once per
+        ATTRIBUTE_ESCALATED_INTERVAL_S, on the biggest crop, which is the only
+        one large enough for a captioner to say anything true about.
+
+        Runs on the writer thread but only ever enqueues, so its whole cost is
+        a dictionary lookup and a `put_nowait`.
+        """
+        if not config.ATTRIBUTES_ENABLED or not rows:
+            return
+        try:
+            escalated = set(self.supervisor.scheduling().get("escalated", []))
+        except Exception:
+            return
+        if not escalated:
+            return
+
+        now = time.monotonic()
+        biggest: dict[str, object] = {}
+        for row in rows:
+            if row.camera_id not in escalated or not row.evidence_path:
+                continue
+            if now - self._attr_last.get(row.camera_id, 0.0) < \
+                    config.ATTRIBUTE_ESCALATED_INTERVAL_S:
+                continue
+            best = biggest.get(row.camera_id)
+            if best is None or _bbox_area(row.bbox) > _bbox_area(best.bbox):
+                biggest[row.camera_id] = row
+
+        for camera_id, row in biggest.items():
+            # Stamped on the attempt, not on the completion: a camera whose
+            # crop is dropped by a full queue must still wait its interval, or
+            # a busy camera would retry on every single flush.
+            self._attr_last[camera_id] = now
+            self._submit_attributes(row.id, row.evidence_path, "escalated")
+
+    def _submit_attributes(self, detection_id, evidence_path,
+                           source: str, alert: dict | None = None) -> bool:
+        """Hand a crop to the attribute worker. Never blocks, may drop."""
+        if not config.ATTRIBUTES_ENABLED or not evidence_path:
+            return False
+        job = {"detection_id": detection_id, "evidence_path": evidence_path,
+               "source": source, "alert": alert, "at": time.monotonic()}
+        try:
+            self._attr_queue.put_nowait(job)
+        except queue.Full:
+            # Dropping is the designed behaviour, but silent dropping is not:
+            # an operator seeing no descriptions deserves to find the count.
+            self.attribute_stats["dropped"] += 1
+            return False
+        self.attribute_stats["queued"] += 1
+        return True
+
+    def _attribute_loop(self) -> None:
+        """Describe queued crops off every other thread, forever."""
+        while not self._attr_stop.is_set():
+            try:
+                job = self._attr_queue.get(timeout=0.5)
+            except queue.Empty:
+                continue
+            try:
+                self._describe_job(job)
+            except Exception:
+                self.attribute_stats["failed"] += 1
+                log.exception("attribute extraction failed for detection %s",
+                              job.get("detection_id"))
+
+    def _describe_job(self, job: dict) -> None:
+        from netra.analytics import attributes as attrs
+
+        path = evidence_file(job["evidence_path"])
+        if path is None:
+            self.attribute_stats["failed"] += 1
+            return
+        result = attrs.describe_image_file(path)
+        self.attribute_stats["processed"] += 1
+        if not result.raw_caption:
+            # The extractor degraded rather than described. Nothing is stored:
+            # a row saying "unknown" would be indistinguishable from a caption
+            # that genuinely found nothing to say.
+            self.attribute_stats["failed"] += 1
+            return
+
+        if job["detection_id"] is not None:
+            store_attributes(job["detection_id"], result, job["source"])
+
+        # A description that arrives while the operator is still looking at the
+        # alert is worth pushing; one that arrives later is not, and the
+        # console fetches it from the stored row instead.
+        if job.get("alert") is not None and \
+                time.monotonic() - job["at"] <= ATTRIBUTE_BROADCAST_BOUND_S:
+            self.attribute_stats["broadcast"] += 1
+            self._broadcast({"kind": "attributes", **job["alert"],
+                             "detection_id": job["detection_id"],
+                             "description": result.description,
+                             "confidence": result.confidence,
+                             "raw_caption": result.raw_caption})
+
     # -- watchlist -----------------------------------------------------------
     def _watchlist(self) -> list[dict]:
         """Cached watchlist. Reloaded periodically rather than per detection."""
         import time
         if time.time() - self._watchlist_loaded_at > 30:
             with SessionLocal() as db:
@@ -189,23 +490,34 @@ class Pipeline:
                 self._watchlist_cache = [{
                     "id": e.id, "plate": e.plate, "category": e.category,
                     "severity": e.severity, "vehicle_class": e.vehicle_class,
                     "vehicle_colour": e.vehicle_colour, "case_ref": e.case_ref,
                     "owner_name": e.owner_name, "source_db": e.source_db,
                 } for e in entries]
+            # Rebuilt with the cache, never separately: an index describing a
+            # watchlist that has already changed would silently stop
+            # considering entries that were just added.
+            self._watchlist_index = WatchlistIndex(self._watchlist_cache)
             self._watchlist_loaded_at = time.time()
         return self._watchlist_cache
 
     def _check_watchlist(self, detection_id: int, det) -> None:
         candidate = {
             "plate_text": det.plate_text,
             "plate_chars": det.plate_chars,
             "vehicle_class": det.vehicle_class,
             "colour": det.colour,
         }
-        for entry in self._watchlist():
+        # Refresh the cache, then score only the entries whose plate shares a
+        # character window with this read. Full scoring still decides each
+        # candidate, so partial and confusion-folded matching is unchanged;
+        # this only avoids scoring entries that could not match. At 10,000
+        # entries that is the difference between 10,000 comparisons per
+        # detection and a few dozen, on the thread that also persists rows.
+        self._watchlist()
+        for entry in self._watchlist_index.candidates(det.plate_text):
             result = score_match(candidate, entry)
             if not result.is_alert:
                 continue
             self._raise_alert(detection_id, det, entry, result)
 
     def _raise_alert(self, detection_id: int, det, entry: dict, result) -> None:
@@ -219,14 +531,15 @@ class Pipeline:
                 reasons=result.reasons,
                 severity=entry.get("severity", "medium"),
             )
             db.add(alert)
             db.commit()
             db.refresh(alert)
+            alert_id = alert.id
             payload = {
-                "id": alert.id,
+                "id": alert_id,
                 "detection_id": detection_id,
                 "camera_id": det.camera_id,
                 "plate_observed": det.plate_text,
                 "plate_watchlist": entry["plate"],
                 "category": entry["category"],
                 "severity": entry.get("severity"),
@@ -239,12 +552,19 @@ class Pipeline:
 
         log.warning("ALERT %s on %s (%s, score %.2f)",
                     entry["plate"], det.camera_id, result.match_type, result.score)
         self._broadcast(payload)
         NOTIFIER.submit(payload)
 
+        # Only now: the vehicle already matters, so a description of it is
+        # worth the GPU - but the alert has already reached the console and the
+        # notifier, so nothing about this can delay it.
+        self._submit_attributes(detection_id, _detection_evidence(detection_id),
+                                "alert", alert={"alert_id": alert_id,
+                                                "camera_id": det.camera_id})
+
     # -- push to consoles ----------------------------------------------------
     def subscribe(self) -> queue.Queue:
         q: queue.Queue = queue.Queue(maxsize=100)
         with self._lock:
             self.alert_subscribers.append(q)
         return q
@@ -269,12 +589,123 @@ class Pipeline:
             "running": self.running,
             "started_at": self.started_at.isoformat() if self.started_at else None,
             "inference": self.engine.stats,
             "queue_depth": self.engine.queue.qsize(),
             "write_queue_depth": self._write_queue.qsize(),
             "scheduling": self.supervisor.scheduling(),
+            "traffic": self.engine.trackers.stats(),
+            "zone_events": self.stats["zone_events"],
+            "watchlist_index": self._watchlist_index.stats(),
+            # Cameras the engine has stopped inferring on because their feed
+            # went black. Surfaced rather than silent: a control room must be
+            # able to see that a camera is no longer being looked at.
+            "dark_cameras": self.engine.dark_cameras(),
             "persistence": self.stats,
+            "attributes": {**self.attribute_stats,
+                           "enabled": config.ATTRIBUTES_ENABLED,
+                           "queue_depth": self._attr_queue.qsize()},
             "cameras": self.supervisor.health(),
         }
 
 
+def _bbox_area(bbox) -> int:
+    """Pixel area of an [x1, y1, x2, y2] box, or 0 if it is not one."""
+    try:
+        return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])
+    except Exception:
+        return 0
+
+
+def evidence_file(evidence_path):
+    """Resolve a stored `/evidence/x.jpg` reference to a path on disk.
+
+    Crops are read back from disk rather than carried through the queue: a
+    decoded frame is megabytes, and holding a queue of them behind a feature
+    that is allowed to be dropped is exactly the memory pressure the bounded
+    queue exists to avoid.
+    """
+    if not evidence_path:
+        return None
+    name = str(evidence_path).replace("\\", "/").rsplit("/", 1)[-1]
+    # Confined to the evidence directory: the reference comes from the
+    # database, but the path it becomes must not be able to leave.
+    if not name or name in (".", ".."):
+        return None
+    path = config.EVIDENCE / name
+    return path if path.exists() else None
+
+
+def _detection_evidence(detection_id: int):
+    with SessionLocal() as db:
+        row = db.get(Detection, detection_id)
+        return row.evidence_path if row else None
+
+
+def store_attributes(detection_id: int, result, source: str) -> dict:
+    """Persist one description against its detection, one row per detection."""
+    with SessionLocal() as db:
+        row = db.query(VehicleAttributeRow).filter(
+            VehicleAttributeRow.detection_id == detection_id).one_or_none()
+        if row is None:
+            row = VehicleAttributeRow(detection_id=detection_id)
+            db.add(row)
+        row.body_type = result.body_type
+        row.colour = result.colour
+        row.tinted_windows = result.tinted_windows
+        row.wheels = result.wheels
+        row.roof_rack = result.roof_rack
+        row.markings = result.markings
+        row.damage = result.damage
+        row.description = result.description
+        row.raw_caption = result.raw_caption
+        row.model = result.model
+        row.confidence = result.confidence
+        row.source = source
+        db.commit()
+    return result.as_dict()
+
+
 PIPELINE = Pipeline()
+
+
+def _self_check() -> None:
+    """Pin the traffic-bucket differencing, restart included.
+
+    Nothing here touches the database, the GPU or a thread: the arithmetic is
+    the part that has been wrong twice, and it is checkable on its own.
+    """
+    p = Pipeline.__new__(Pipeline)
+    p._traffic_last_total, p._traffic_last_counts = {}, {}
+
+    # First bucket: nothing to difference against, so the whole cumulative is
+    # this bucket's traffic and the breakdown is the whole breakdown.
+    delta, by_class = p._bucket_deltas("cam01", 10, {"car": 7, "truck": 3})
+    assert delta == 10 and by_class == {"car": 7, "truck": 3}, (delta, by_class)
+
+    # Steady state: only the increment since the last flush.
+    delta, by_class = p._bucket_deltas("cam01", 16, {"car": 11, "truck": 5})
+    assert delta == 6 and by_class == {"car": 4, "truck": 2}, (delta, by_class)
+
+    # A bucket in which nothing passed is still coherent, not negative.
+    delta, by_class = p._bucket_deltas("cam01", 16, {"car": 11, "truck": 5})
+    assert delta == 0 and by_class == {}, (delta, by_class)
+
+    # Tracker restart: the counter drops. The whole of the new cumulative is
+    # this bucket's traffic, and the breakdown must be reset with it - a total
+    # of 4 against an empty breakdown was the bug.
+    delta, by_class = p._bucket_deltas("cam01", 4, {"car": 3, "bus": 1})
+    assert delta == 4, delta
+    assert by_class == {"car": 3, "bus": 1}, by_class
+    assert by_class, "a restart bucket with detections must carry a breakdown"
+    assert sum(by_class.values()) <= delta, (by_class, delta)
+
+    # And the flush after a restart differences against the post-restart
+    # snapshot, not the pre-restart one.
+    delta, by_class = p._bucket_deltas("cam01", 9, {"car": 6, "bus": 3})
+    assert delta == 5 and by_class == {"car": 3, "bus": 2}, (delta, by_class)
+    assert sum(by_class.values()) <= delta, (by_class, delta)
+
+    print("pipeline self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/web/app.js b/netra/web/app.js
index c60974b..9195b34 100644
--- a/netra/web/app.js
+++ b/netra/web/app.js
@@ -1,10 +1,33 @@
 /* NETRA operator console. */
 const $ = (s) => document.querySelector(s);
 const $$ = (s) => Array.from(document.querySelectorAll(s));
-const api = async (p, o) => (await fetch(p, o)).json();
+/* API key. Empty by default and empty in the demo, where no data/api_keys.json
+   exists and the server runs open; the header is simply sent blank and every
+   endpoint behaves as it always has. Where an operator has configured keys,
+   this is what lets the console reach the protected endpoints — including the
+   zone editor's still, which an <img src> alone cannot fetch because it has no
+   way to carry a header. */
+const API_KEY_STORE = "NETRA_API_KEY";
+const apiKey = () => { try { return localStorage.getItem(API_KEY_STORE) || ""; }
+                       catch (e) { return ""; } };
+const authHeaders = (extra) => Object.assign({ "X-API-Key": apiKey() }, extra || {});
+const api = async (p, o) => {
+  const opts = Object.assign({}, o);
+  opts.headers = authHeaders(opts.headers);
+  return (await fetch(p, opts)).json();
+};
+document.addEventListener("DOMContentLoaded", () => {
+  const box = document.getElementById("apiKey");
+  if (!box) return;
+  box.value = apiKey();
+  box.onchange = () => {
+    try { localStorage.setItem(API_KEY_STORE, box.value.trim()); }
+    catch (e) { toast("This browser will not persist the key for this session."); }
+  };
+});
 const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c =>
   ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
 
 let CAMERAS = [], MAP = null, ROUTE_MAP = null, MARKERS = {}, ROUTE_LAYER = null;
 const PC = {};                       // active WebRTC peer connections
 
@@ -17,12 +40,15 @@ $$("nav a").forEach(a => a.onclick = () => {
   if (a.dataset.view === "map") setTimeout(initMap, 60);
   if (a.dataset.view === "route") setTimeout(initRouteMap, 60);
   if (a.dataset.view === "registry") loadRegistry();
   if (a.dataset.view === "watchlist") loadWatchlist();
   if (a.dataset.view === "alerts") loadAlerts();
   if (a.dataset.view === "detections") loadDetections();
+  if (a.dataset.view === "zones") loadZones();
+  if (a.dataset.view === "traffic") loadTraffic();
+  if (a.dataset.view === "intel") loadIntel();
 });
 
 function toast(html) {
   const d = document.createElement("div");
   d.className = "t";
   d.innerHTML = html;
@@ -69,12 +95,17 @@ async function refresh() {
     card(stats.with_plate ?? 0, "Plates read", "ok", (stats.plate_rate_pct ?? 0) + "% of detections"),
     card(stats.total_alerts ?? 0, "Watchlist alerts", stats.total_alerts ? "bad" : "", "matches raised"),
     card(esc_, "Escalated to tier-2", "warn", "cameras with active traffic"),
     card((inf.infer_ms ?? 0) + "ms", "Inference latency", "", "last batch"),
     card(st.queue_depth ?? 0, "Queue depth", drop > 20 ? "warn" : "", drop.toFixed(1) + "% frames dropped"),
     card(cams.reduce((a, c) => a + (c.loop_cuts || 0), 0), "Loop cuts handled", "", "state resets"),
+    // Counts detection-frames whose plate was replaced by their track's voted
+    // consensus - not the same quantity as the per-detection read count shown
+    // beside each plate in the Detections table, which is why it is named for
+    // what it is.
+    card(inf.plate_consensus_applied ?? 0, "Plate consensus applied", "", "frames given a voted plate"),
   ].join("");
 }
 const card = (n, l, cls = "", s = "") =>
   `<div class="stat ${cls}"><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div><div class="s">${esc(s)}</div></div>`;
 
 async function loadRecent() {
@@ -164,30 +195,101 @@ $("#btnWall").onclick = async () => {
 $("#btnWallStop").onclick = () => {
   Object.values(PC).forEach(p => p.close());
   for (const k in PC) delete PC[k];
   $("#wall").innerHTML = "";
 };
 
+/* ------------------------------------------------------- descriptions --- */
+/* A vision-language description is the only account of a vehicle an officer
+   can read, search and testify to on this grid, where no plate is recoverable.
+   It is always rendered as a description of the crop, never as an identity. */
+function attrHtml(at) {
+  if (!at) return "";
+  const bits = [];
+  if (at.body_type && at.body_type !== "unknown") bits.push(at.body_type.replace(/_/g, " "));
+  if (at.colour) bits.unshift(at.colour);
+  const tags = [];
+  if (at.tinted_windows === true) tags.push("tinted");
+  if (at.wheels && at.wheels !== "unknown") tags.push(at.wheels + " wheels");
+  if (at.roof_rack === true) tags.push("roof rack");
+  (at.markings || []).forEach(m => tags.push("marking: " + m));
+  (at.damage || []).forEach(d => tags.push(d));
+  return `<div class="vdesc" title="${esc(at.raw_caption || "")}">
+    <b>${esc(at.description || bits.join(" ") || "—")}</b>
+    ${tags.length ? `<span class="faint"> · ${esc(tags.join(" · "))}</span>` : ""}
+    <span class="faint mono" style="font-size:10px"> · ${esc(at.model || "model")}
+      conf ${at.confidence ?? 0}</span>
+    <div class="faint" style="font-size:10px">Describes the crop; not an identification.</div>
+  </div>`;
+}
+
+function describeBtn(detectionId, has) {
+  if (detectionId == null) return "";
+  return `<button class="mini" data-describe="${esc(detectionId)}">${
+    has ? "Re-describe" : "Describe"}</button>`;
+}
+
+async function describeDetection(btn) {
+  const id = btn.dataset.describe;
+  const label = btn.textContent;
+  btn.disabled = true;
+  btn.textContent = "describing…";
+  try {
+    const r = await api(`/api/detections/${encodeURIComponent(id)}/describe`, { method: "POST" });
+    if (r.attributes) {
+      const holder = btn.closest("[data-desc-holder]") || btn.parentElement;
+      const existing = holder.querySelector(".vdesc");
+      if (existing) existing.remove();
+      btn.insertAdjacentHTML("beforebegin", attrHtml(r.attributes));
+      btn.textContent = "Re-describe";
+    } else {
+      toast("No description could be produced: " + esc(r.detail || "unavailable"));
+      btn.textContent = label;
+    }
+  } catch (e) {
+    toast("Describe failed: " + esc(e));
+    btn.textContent = label;
+  }
+  btn.disabled = false;
+}
+
+/* Delegated, because rows and alert cards are both re-rendered wholesale. */
+document.addEventListener("click", (e) => {
+  const btn = e.target.closest("[data-describe]");
+  if (btn) describeDetection(btn);
+});
+
 /* ---------------------------------------------------------- detections --- */
 async function loadDetections() {
   const q = new URLSearchParams({ limit: "200" });
   if ($("#dPlate").value) q.set("plate", $("#dPlate").value);
   if ($("#dCam").value) q.set("camera_id", $("#dCam").value);
   if ($("#dClass").value) q.set("vehicle_class", $("#dClass").value);
   const d = await api("/api/detections?" + q);
   $("#dCount").textContent = `${d.count} of ${d.total}`;
   const tb = $("#tblDet tbody");
-  if (!d.items.length) { tb.innerHTML = `<tr><td colspan="8" class="empty">No matching detections.</td></tr>`; return; }
+  if (!d.items.length) { tb.innerHTML = `<tr><td colspan="10" class="empty">No matching detections.</td></tr>`; return; }
   tb.innerHTML = d.items.map(x => `<tr>
     <td class="mono">${esc(x.at.replace("T", " ").slice(0, 19))}</td>
     <td class="mono faint">${Math.round(x.pts_ms)}</td>
     <td>${esc(x.camera_name || x.camera_id)}</td>
     <td>${esc(x.vehicle_class)}</td><td class="dim">${esc(x.colour || "—")}</td>
-    <td class="mono">${x.plate_text ? esc(x.plate_text) : '<span class="faint">—</span>'}</td>
+    <td class="mono">${x.plate_text ? esc(x.plate_text) : '<span class="faint">—</span>'}
+      ${x.plate_chars ? `<span class="faint" style="font-size:10.5px">· ${esc(x.plate_chars)} chars</span>` : ""}
+      ${x.plate_votes ? `<span class="faint" style="font-size:10.5px"
+        title="OCR reads of this tracked vehicle that agreed on this plate. One read is a single guess, not a consensus."
+        >· ${esc(x.plate_votes)} read${x.plate_votes === 1 ? "" : "s"}</span>` : ""}</td>
     <td class="dim">${x.plate_conf ?? "—"}</td>
-    <td>${x.evidence ? `<img src="${esc(x.evidence)}" style="height:34px;border-radius:4px">` : ""}</td></tr>`).join("");
+    <td class="mono faint">${x.track_id != null ? esc(x.track_id) : "—"}</td>
+    <td class="mono faint">${x.scene_time && x.scene_time_corroborated
+      ? esc(x.scene_time.replace("T", " ").slice(0, 19))
+      : (x.scene_time
+        ? `<span title="read once from the overlay and never confirmed by a second reading, so it is not used for any timing claim">${esc(x.scene_time.replace("T", " ").slice(0, 19))} <b style="color:var(--warn)">?</b></span>`
+        : '<span title="no clock recovered from the overlay">—</span>')}</td>
+    <td data-desc-holder>${x.evidence ? `<img src="${esc(x.evidence)}" style="height:34px;border-radius:4px">` : ""}
+      ${attrHtml(x.attributes)}${describeBtn(x.id, !!x.attributes)}</td></tr>`).join("");
 }
 $("#dSearch").onclick = loadDetections;
 $("#dCsv").onclick = () => {
   const p = $("#dPlate").value;
   location.href = "/api/export/detections.csv" + (p ? "?plate=" + encodeURIComponent(p) : "");
 };
@@ -257,32 +359,32 @@ async function loadWatchlist() {
     <td class="dim">${esc([e.vehicle_colour, e.vehicle_class].filter(Boolean).join(" ") || "—")}</td>
     <td class="mono faint">${esc(e.case_ref || "—")}</td>
     <td class="dim">${esc(e.source_db)}</td>
     <td><button onclick="delWl(${e.id})">Remove</button></td></tr>`).join("");
 }
 window.delWl = async (id) => {
-  await fetch("/api/watchlist/" + id, { method: "DELETE" });
+  await fetch("/api/watchlist/" + id, { method: "DELETE", headers: authHeaders() });
   loadWatchlist();
 };
 $("#wAdd").onclick = async () => {
   const plate = $("#wPlate").value.trim().toUpperCase();
   if (!plate) return;
   await fetch("/api/watchlist", {
-    method: "POST", headers: { "Content-Type": "application/json" },
+    method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
     body: JSON.stringify({
       plate, category: $("#wCat").value, severity: $("#wSev").value,
       vehicle_colour: $("#wColour").value || null,
       vehicle_class: $("#wClass").value || null,
       case_ref: $("#wCase").value || null, source_db: "MANUAL",
     }),
   });
   $("#wPlate").value = ""; $("#wCase").value = "";
   loadWatchlist();
 };
 $("#wSeed").onclick = async () => {
-  await fetch("/api/watchlist/seed", { method: "POST" });
+  await fetch("/api/watchlist/seed", { method: "POST", headers: authHeaders() });
   loadWatchlist();
   toast("Sample watchlist loaded.");
 };
 
 /* -------------------------------------------------------------- alerts --- */
 function alertHtml(a) {
@@ -301,13 +403,16 @@ function alertHtml(a) {
       <span class="dim">read as</span> <span class="mono">${esc(a.plate_observed || "—")}</span>
       <span class="dim">on</span> <b>${esc(a.camera_name || a.camera_id)}</b>
       ${a.category ? `<span class="dim"> · ${esc(a.category)}</span>` : ""}
       ${a.case_ref ? `<span class="faint mono"> · ${esc(a.case_ref)}</span>` : ""}
     </div>
     <div class="why">${reasons}</div>
-    ${a.evidence ? `<img src="${esc(a.evidence)}">` : ""}
+    <div data-desc-holder>
+      ${a.evidence ? `<img src="${esc(a.evidence)}">` : ""}
+      ${attrHtml(a.attributes)}${describeBtn(a.detection_id, !!a.attributes)}
+    </div>
   </div>`;
 }
 async function loadAlerts() {
   const rows = await api("/api/alerts?limit=100");
   $("#alertList").innerHTML = rows.length
     ? rows.map(alertHtml).join("") : `<div class="empty">No alerts raised.</div>`;
@@ -366,14 +471,42 @@ function connectWs() {
     $("#wsDot").className = "dot off"; $("#wsTxt").textContent = "reconnecting";
     setTimeout(connectWs, 3000);
   };
   ws.onmessage = (e) => {
     const a = JSON.parse(e.data);
     if (a.type === "ping") return;
+    if (a.kind === "attributes") {
+      // The description is extracted after the alert has already been sent, so
+      // it arrives separately and is folded into the card that is already up.
+      if (a.detection_id != null) {
+        const btn = document.querySelector(`[data-describe="${a.detection_id}"]`);
+        if (btn) btn.insertAdjacentHTML("beforebegin", attrHtml(a));
+      } else if (a.zone_event_id != null) {
+        // A zone event describes a whole frame, so it has no detection row to
+        // key a description to; the card it belongs to is addressed directly.
+        $$(`[data-zone-event="${a.zone_event_id}"]`)
+          .forEach(card => card.insertAdjacentHTML("beforeend", attrHtml(a)));
+      }
+      return;
+    }
     const feed = $("#alertFeed");
     if (feed.querySelector(".empty")) feed.innerHTML = "";
+    if (a.kind === "zone") {
+      // A rule breach is not a watchlist hit, and rendering one as the other
+      // would put an identity claim on an event that carries none.
+      feed.insertAdjacentHTML("afterbegin", zoneEventHtml(a));
+      const ze = $("#zEvents");
+      if (ze) {
+        if (ze.querySelector(".empty")) ze.innerHTML = "";
+        ze.insertAdjacentHTML("afterbegin", zoneEventHtml(a));
+      }
+      toast(`<b style="color:#ffb066">ZONE ${esc(a.rule)}</b><br>
+        <span style="font-size:12px;color:#8b9bb4">${esc(a.zone || "")} ·
+        ${esc(a.camera_id)} · ${esc(a.detail || "")}</span>`);
+      return;
+    }
     feed.insertAdjacentHTML("afterbegin", alertHtml(a));
     toast(`<b style="color:#ff8080">WATCHLIST HIT</b><br>
       <span class="mono" style="font-size:15px">${esc(a.plate_watchlist)}</span><br>
       <span style="font-size:12px;color:#8b9bb4">${esc(a.camera_id)} · ${esc(a.match_type)} · score ${a.score}</span>`);
   };
 }
@@ -384,12 +517,20 @@ function connectWs() {
   await loadRecent();
   connectWs();
   setInterval(refresh, 3000);
   setInterval(() => {
     if ($("#v-overview").classList.contains("active")) loadRecent();
   }, 4000);
+  // The Traffic tab is the one an operator leaves open while watching a
+  // junction, and until now it only ever showed what was there when the tab
+  // was opened. Gated on visibility, like the overview poll above: an unseen
+  // tab must not spend a database round trip every five seconds, and the
+  // history query it makes is the heaviest of the console's reads.
+  setInterval(() => {
+    if ($("#v-traffic").classList.contains("active")) loadTraffic();
+  }, 5000);
 })();
 
 /* ----------------------------------------------------------- assistant --- */
 const ASST_SUGGESTIONS = [
   "Which cameras are down?",
   "How many detections so far?",
@@ -483,15 +624,314 @@ $("#rAppearance").onclick = async () => {
         <div><b>${esc(h.camera_name || h.camera_id)}</b>
           <span class="faint mono">${esc(h.camera_id)}</span></div>
         <div class="mono dim" style="font-size:11.5px">${esc(h.at)}</div>
         <div style="font-size:11.5px;margin-top:3px">
           ${esc(h.colour || "")} ${esc(h.vehicle_class || "")}
           ${h.similarity ? `· <b style="color:#c99bff">similarity ${h.similarity}</b>` : "· query vehicle"}
-          ${h.leg_km != null ? `· ${h.leg_km} km from previous` : ""}</div>
+          ${h.leg_km != null ? `· ${h.leg_km} km from previous` : ""}
+          ${h.ambiguous ? `<div class="tag t-degraded" style="margin-top:4px">ambiguous</div>
+            <div class="faint" style="font-size:11px;margin-top:3px">${esc(h.ambiguity_note || "")}</div>` : ""}</div>
         ${h.evidence || h.evidence_path ? `<img src="${esc(h.evidence || h.evidence_path)}">` : ""}
       </div></div>`).join("");
 
   $("#routeRejected").innerHTML = r.rejected.length
     ? r.rejected.map(x => `<div class="finding" style="border-color:var(--bad);background:rgba(239,68,68,.06)">
         <b class="mono">${esc(x.camera_id)}</b> — ${esc(x.plausibility || "excluded")}</div>`).join("")
     : `<div class="faint" style="font-size:12px">None excluded.</div>`;
 };
+
+/* ---------------------------------------------------------------- zones --- */
+let ZPOINTS = [];                      // normalised [x, y] pairs, in click order
+
+function zoneEventHtml(e) {
+  return `<div class="zev" data-zone-event="${esc(e.id ?? "")}">
+    <div class="row" style="margin:0 0 5px 0;gap:8px">
+      <span class="tag sev-${esc(e.severity || "medium")}">${esc(e.severity || "")}</span>
+      <span class="tag t-vehicle">${esc(e.rule)}</span>
+      <b>${esc(e.zone || "zone")}</b>
+      <span class="faint mono" style="font-size:11px">${esc(e.camera_name || e.camera_id)}</span>
+      <span style="margin-left:auto" class="faint mono">${esc((e.at || "").slice(11, 19))}</span>
+    </div>
+    <div class="dim">${esc(e.detail || "")}
+      ${e.object_class ? `· ${esc(e.object_class)}` : ""}
+      ${e.direction ? `· heading ${esc(e.direction)}` : ""}</div>
+    ${e.evidence ? `<img src="${esc(e.evidence)}">` : ""}
+  </div>`;
+}
+
+function drawZone() {
+  const cv = $("#zCanvas"), img = $("#zImg");
+  if (!cv || !img || !img.naturalWidth) return;
+  cv.width = img.clientWidth; cv.height = img.clientHeight;
+  const g = cv.getContext("2d");
+  g.clearRect(0, 0, cv.width, cv.height);
+  const pts = ZPOINTS.map(([x, y]) => [x * cv.width, y * cv.height]);
+  if (!pts.length) return;
+  g.strokeStyle = "#ff6b00"; g.lineWidth = 2;
+  g.fillStyle = "rgba(255,107,0,.18)";
+  g.beginPath();
+  pts.forEach(([x, y], i) => i ? g.lineTo(x, y) : g.moveTo(x, y));
+  if ($("#zRule").value !== "crossing" && pts.length > 2) { g.closePath(); g.fill(); }
+  g.stroke();
+  pts.forEach(([x, y], i) => {
+    g.beginPath(); g.arc(x, y, 6, 0, 6.283); g.fillStyle = "#ff6b00"; g.fill();
+    g.fillStyle = "#111"; g.font = "bold 10px monospace";
+    g.fillText(String(i + 1), x - 3, y + 3);
+  });
+}
+
+$("#zLoad").onclick = async () => {
+  const cam = $("#zCam").value;
+  if (!cam) return;
+  const btn = $("#zLoad");
+  btn.disabled = true; btn.textContent = "Grabbing frame…";
+  const img = $("#zImg");
+  // Fetched rather than set as an <img src>: the snapshot endpoint is behind
+  // `require`, and an <img> has no way to carry X-API-Key, so with keys
+  // configured the still 401'd while the rest of the console worked. The blob
+  // is handed to the <img> as an object URL instead. The previous one is
+  // revoked because the zone editor is reloaded repeatedly while an operator
+  // draws, and each blob would otherwise be held for the life of the page.
+  try {
+    const r = await fetch(`/api/cameras/${encodeURIComponent(cam)}/snapshot?t=${Date.now()}`,
+                          { headers: authHeaders() });
+    if (!r.ok) throw new Error(r.status === 401 || r.status === 403
+      ? "not authorised — set an API key in the header"
+      : "HTTP " + r.status);
+    const url = URL.createObjectURL(await r.blob());
+    if (img.dataset.blobUrl) URL.revokeObjectURL(img.dataset.blobUrl);
+    img.dataset.blobUrl = url;
+    img.src = url;
+    await img.decode();
+    $("#zWrap").style.display = "inline-block";
+    drawZone();
+  } catch (e) {
+    toast("Could not grab a still from " + esc(cam) + " — " + esc(e.message) +
+      ". The camera may be down or the feed unreachable.");
+  }
+  btn.disabled = false; btn.textContent = "Load still frame";
+};
+
+$("#zCanvas").onclick = (e) => {
+  const r = e.currentTarget.getBoundingClientRect();
+  ZPOINTS.push([(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]);
+  drawZone();
+  $("#zHint").textContent = `${ZPOINTS.length} point(s) placed.`;
+};
+$("#zClear").onclick = () => {
+  ZPOINTS = []; drawZone(); $("#zHint").textContent = "Points cleared.";
+};
+$("#zRule").onchange = drawZone;
+window.addEventListener("resize", drawZone);
+
+$("#zSave").onclick = async () => {
+  const rule = $("#zRule").value;
+  const needed = rule === "crossing" ? 2 : 3;
+  if (ZPOINTS.length < needed) {
+    toast(`A ${esc(rule)} rule needs at least ${needed} points.`); return;
+  }
+  const body = {
+    camera_id: $("#zCam").value, name: $("#zName").value || "Zone",
+    rule, points: ZPOINTS.map(([x, y]) => [+x.toFixed(4), +y.toFixed(4)]),
+    classes: $("#zClasses").value ? [$("#zClasses").value] : [],
+    severity: $("#zSev").value, dwell_s: parseFloat($("#zDwell").value) || 30,
+  };
+  const r = await fetch("/api/zones", {
+    method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
+    body: JSON.stringify(body),
+  });
+  const out = await r.json().catch(() => ({}));
+  if (!r.ok) { toast("Rule rejected: " + esc(out.detail || r.status)); return; }
+  ZPOINTS = []; drawZone();
+  toast("Rule saved on " + esc(body.camera_id) + ".");
+  loadZones();
+};
+
+window.delZone = async (id) => {
+  await fetch("/api/zones/" + id, { method: "DELETE", headers: authHeaders() });
+  loadZones();
+};
+
+async function loadZones() {
+  if (!$("#zCam").options.length) {
+    $("#zCam").innerHTML = CAMERAS.map(c =>
+      `<option value="${esc(c.id)}">${esc(c.id)} — ${esc(c.name)}</option>`).join("");
+  }
+  const zones = await api("/api/zones");
+  $("#zList").innerHTML = zones.length ? zones.map(z => `<div class="zone-item">
+    <div style="flex:1">
+      <div><b>${esc(z.name)}</b>
+        <span class="tag t-vehicle" style="margin-left:6px">${esc(z.rule)}</span>
+        <span class="tag sev-${esc(z.severity)}" style="margin-left:4px">${esc(z.severity)}</span>
+        ${z.active ? "" : `<span class="tag t-degraded" style="margin-left:4px">inactive</span>`}</div>
+      <div class="faint mono" style="font-size:11.5px;margin-top:3px">${esc(z.camera_id)} ·
+        ${esc((z.points || []).length)} points ·
+        ${(z.classes || []).length ? esc((z.classes || []).join(", ")) : "any class"}
+        ${z.rule === "loitering" ? `· dwell ${esc(z.dwell_s)}s` : ""}</div>
+    </div>
+    <button onclick="delZone(${esc(z.id)})">Delete</button></div>`).join("")
+    : `<div class="empty">No rules configured.</div>`;
+
+  const events = await api("/api/zones/events?limit=50");
+  $("#zEvents").innerHTML = events.length
+    ? events.map(zoneEventHtml).join("") : `<div class="empty">No zone events yet.</div>`;
+}
+
+/* -------------------------------------------------------------- traffic --- */
+function sparks(values) {
+  const max = Math.max(1, ...values);
+  return `<div class="sparks">${values.map(v =>
+    `<i style="height:${Math.round(100 * v / max)}%" title="${esc(v)}"></i>`).join("")}</div>`;
+}
+const kv = (obj) => Object.entries(obj || {})
+  .map(([k, v]) => `${esc(k)} ${esc(v)}`).join(" · ") || "—";
+
+async function loadTraffic() {
+  const live = await api("/api/traffic/live");
+  const history = await api("/api/traffic/history?limit=1000");
+  const byCam = {};
+  history.forEach(h => (byCam[h.camera_id] = byCam[h.camera_id] || []).push(h.total));
+
+  const cams = live.cameras || [];
+  const totals = cams.reduce((a, c) => a + (c.total_counted || 0), 0);
+  const loop = cams.reduce((a, c) => a + (c.counted_this_loop || 0), 0);
+  const active = cams.reduce((a, c) => a + (c.active_tracks || 0), 0);
+  const loops = cams.length ? Math.max(...cams.map(c => c.loops_seen || 0)) : 0;
+  $("#tStats").innerHTML = [
+    card(cams.length, "Cameras counting", cams.length ? "ok" : ""),
+    card(active, "Active tracks", "", "being followed right now"),
+    card(loop, "Counted this loop", "ok", "current pass of the recording"),
+    card(totals, "Total counted", "", "cumulative across every replay"),
+    card(loops, "Loops seen", loops > 1 ? "warn" : "", "recording replays observed"),
+    card(live.zone_events ?? 0, "Zone events", live.zone_events ? "warn" : "", "rule breaches raised"),
+  ].join("");
+
+  const tb = $("#tblTraffic tbody");
+  tb.innerHTML = cams.length ? cams.map(c => {
+    const cam = CAMERAS.find(x => x.id === c.camera_id);
+    const hist = (byCam[c.camera_id] || []).slice(0, 30).reverse();
+    return `<tr>
+      <td><b>${esc(cam ? cam.name : c.camera_id)}</b>
+        <div class="faint mono" style="font-size:11px">${esc(c.camera_id)}</div></td>
+      <td class="mono">${esc(c.active_tracks)}</td>
+      <td class="mono" style="color:#4ade80;font-weight:700">${esc(c.counted_this_loop)}</td>
+      <td class="mono">${esc(c.total_counted)}</td>
+      <td class="mono faint">${esc(c.loops_seen)}</td>
+      <td class="mono faint">${esc(c.dropped_tracks)}</td>
+      <td class="dim">${kv(c.counts_by_class)}</td>
+      <td class="dim">${kv(c.directions)}</td>
+      <td class="mono">${esc(c.mean_dwell_s)}s</td>
+      <td style="min-width:110px">${hist.length ? sparks(hist)
+        : `<span class="faint">no snapshots</span>`}</td></tr>`;
+  }).join("") : `<tr><td colspan="10" class="empty">No cameras counting — start the pipeline.</td></tr>`;
+}
+$("#tRefresh").onclick = loadTraffic;
+$("#tSnap").onclick = async () => {
+  const r = await api("/api/traffic/snapshot", { method: "POST" });
+  toast(`Traffic snapshot written for ${esc(r.buckets_written)} camera(s).`);
+  loadTraffic();
+};
+
+/* --------------------------------------------------------- intelligence --- */
+async function loadIntel() {
+  if (!$("#iGroup").options.length) {
+    const groups = [...new Set(CAMERAS.map(c => c.time_group).filter(Boolean))].sort();
+    $("#iGroup").innerHTML = groups.map(g =>
+      `<option value="${esc(g)}">${esc(g)}</option>`).join("");
+  }
+  loadClones(); loadAnomalies(); loadJourneys();
+}
+$("#iRefresh").onclick = loadIntel;
+$("#iGroup").onchange = loadJourneys;
+
+async function loadClones() {
+  const r = await api("/api/analytics/cloned-plates");
+  const f = r.findings || [];
+  $("#iClones").innerHTML =
+    `<div class="faint" style="font-size:12px;margin-bottom:9px">${esc(r.note)}</div>` +
+    (f.length ? f.map(x => `<div class="finding" style="border-color:var(--bad);background:rgba(239,68,68,.06)">
+      <b class="mono" style="font-size:14px;color:#fff">${esc(x.plate)}</b>
+      <span class="faint mono" style="font-size:11px">· confidence ${esc(x.confidence)}</span>
+      <div style="margin-top:5px">${esc(x.sighting_a.camera_name)}
+        <span class="faint mono">${esc(x.sighting_a.at)}</span> &rarr;
+        ${esc(x.sighting_b.camera_name)}
+        <span class="faint mono">${esc(x.sighting_b.at)}</span></div>
+      <div class="dim" style="margin-top:4px">${esc(x.distance_km)} km ·
+        ${esc(x.elapsed_s)} s ·
+        ${x.implied_kmh == null ? "speed not computable" : esc(x.implied_kmh) + " km/h implied"}</div>
+      <div style="margin-top:5px">${esc(x.reason)}</div></div>`).join("")
+      : `<div class="empty">No cloned-plate findings in the stored detections.</div>`);
+}
+
+async function loadAnomalies() {
+  const r = await api("/api/analytics/anomalies");
+  const a = r.assessments || [];
+  const head = `<div class="faint" style="font-size:12px;margin-bottom:9px">
+    ${esc(r.cameras_assessed)} camera(s) assessed against ${esc(r.buckets_read)} stored buckets ·
+    ${esc(r.anomalies)} flagged${r.stale ? `, ${esc(r.stale)} not reporting` : ""}. Cameras with too
+    little history to judge, or whose last bucket is too old to be a current reading, are shown muted
+    rather than hidden: hiding them would imply coverage that does not exist.</div>`;
+  $("#iAnoms").innerHTML = head + (a.length ? a.map(x => {
+    // A stale camera is muted alongside an unjudged one: neither is a
+    // statement about the road, and colouring stale green would present a
+    // dropped feed as a road confirmed clear.
+    const thin = x.status === "insufficient_data" || x.status === "stale";
+    const colour = thin ? "var(--faint)" : (x.anomalous ? "var(--warn)" : "var(--ok)");
+    return `<div class="finding ${thin ? "muted" : ""}"
+      style="border-color:${colour};background:rgba(59,130,246,.05)">
+      <b class="mono">${esc(x.camera_id)}</b>
+      <span class="tag ${thin ? "t-unknown" : (x.anomalous ? "sev-high" : "t-anpr")}"
+        style="margin-left:6px">${esc(x.status)}</span>
+      <span class="faint mono" style="font-size:11px;margin-left:6px">hour ${esc(x.hour)} UTC ·
+        observed ${esc(x.observed)}${x.z_score == null ? "" : " · z " + esc(x.z_score)}</span>
+      <div style="margin-top:4px">${esc(x.explanation)}</div>
+      ${x.baseline ? `<div class="faint" style="font-size:11px;margin-top:3px">
+        baseline mean ${esc(x.baseline.mean)} · stdev ${esc(x.baseline.stdev)} ·
+        ${esc(x.baseline.samples)} samples</div>` : ""}</div>`;
+  }).join("") : `<div class="empty">No traffic snapshots stored yet — write one from the Traffic tab.</div>`);
+}
+
+async function loadJourneys() {
+  const g = $("#iGroup").value;
+  if (!g) { $("#iJourneys").innerHTML = `<div class="empty">No time group available.</div>`; return; }
+  const r = await api("/api/analytics/journeys?group=" + encodeURIComponent(g));
+  if (r.detail) { $("#iJourneys").innerHTML = `<div class="empty">${esc(r.detail)}</div>`; return; }
+  const idx = r.index || {};
+  let head = `<div class="faint" style="font-size:12px;margin-bottom:9px">
+    ${esc(r.note)}<br>Index: ${esc(idx.detections_in_group ?? 0)} detections ·
+    ${esc(idx.comparable ?? 0)} comparable ·
+    ${esc(idx.excluded_no_scene_time ?? 0)} without a scene clock ·
+    ${esc(idx.excluded_no_embedding ?? 0)} without an appearance vector.</div>`;
+  if (r.mining_skipped) {
+    head += `<div class="finding">Mining was skipped: this group has been mined already and held no
+      journeys, so it is not re-derived on every poll. Nothing re-mines on a timer —
+      next mine ${esc(r.next_mine)}.</div>`;
+  }
+  const js = r.journeys || [];
+  if (!js.length) {
+    $("#iJourneys").innerHTML = head +
+      `<div class="empty">No cross-camera journey found in the indexed recordings.</div>`;
+    return;
+  }
+  $("#iJourneys").innerHTML = head + js.map((j, n) => `<div class="panel" style="margin-bottom:12px">
+    <h3>Journey ${n + 1} · ${esc(j.hop_count)} hops · ${esc(j.total_km)} km ·
+      confidence ${esc(j.confidence)}
+      ${j.truncated ? `<span class="tag t-degraded">truncated</span>` : ""}</h3>
+    <div class="body">
+      ${j.note ? `<div class="faint" style="font-size:11.5px;margin-bottom:8px">${esc(j.note)}</div>` : ""}
+      ${(j.hops || []).map((h, i) => `<div class="hop">
+        <div class="num">${i + 1}</div>
+        <div style="flex:1">
+          <div><b>${esc(h.camera_name || h.camera_id)}</b>
+            <span class="faint mono">${esc(h.camera_id)}</span></div>
+          <div class="mono dim" style="font-size:11.5px">${esc(h.at)}</div>
+          <div style="font-size:11.5px;margin-top:3px">
+            ${esc(h.colour || "")} ${esc(h.vehicle_class || "")}
+            ${h.plate_text ? `· plate <span class="mono">${esc(h.plate_text)}</span>` : ""}
+            ${h.similarity != null ? `· <b style="color:#c99bff">similarity ${esc(h.similarity)}</b>`
+              : "· first sighting"}
+            ${h.leg_km != null ? `· ${esc(h.leg_km)} km · ${esc(h.implied_kmh ?? "?")} km/h` : ""}</div>
+          ${h.reason ? `<div class="faint" style="font-size:11px;margin-top:3px">${esc(h.reason)}</div>` : ""}
+          ${h.evidence_path ? `<img src="${esc(h.evidence_path)}">` : ""}
+        </div></div>`).join("")}
+    </div></div>`).join("");
+}
diff --git a/netra/web/index.html b/netra/web/index.html
index 86d055f..d14cbd2 100644
--- a/netra/web/index.html
+++ b/netra/web/index.html
@@ -102,37 +102,61 @@ input.mono{font-family:var(--mono);letter-spacing:1px;text-transform:uppercase}
 .bar{height:7px;background:#0a0e14;border-radius:4px;overflow:hidden;margin-top:6px}
 .bar span{display:block;height:100%;background:var(--blue)}
 .hop{display:flex;gap:12px;padding:11px 0;border-bottom:1px solid var(--line);align-items:flex-start}
 .hop:last-child{border-bottom:none}
 .hop .num{width:26px;height:26px;border-radius:50%;background:var(--accent);color:#111;display:grid;place-items:center;font-weight:800;font-size:12px;flex-shrink:0}
 .hop img{height:52px;border-radius:5px;border:1px solid var(--line)}
+.muted{opacity:.55}
+.zwrap{position:relative;display:inline-block;background:#000;border:1px solid var(--line);border-radius:9px;overflow:hidden;max-width:100%}
+.zwrap img{display:block;max-width:100%}
+.zwrap canvas{position:absolute;inset:0;width:100%;height:100%;cursor:crosshair}
+.zone-item{display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px solid var(--line)}
+.zone-item:last-child{border-bottom:none}
+.sparks{display:flex;align-items:flex-end;gap:2px;height:38px;margin-top:8px}
+.sparks i{flex:1;background:var(--blue);border-radius:2px 2px 0 0;min-height:2px;display:block}
+.zev{border-left:3px solid var(--warn);background:var(--panel2);border-radius:0 9px 9px 0;padding:10px 13px;margin-bottom:8px;font-size:12.5px}
+.zev img{max-height:56px;border-radius:5px;border:1px solid var(--line);margin-top:6px}
 .toast{position:fixed;right:18px;bottom:18px;z-index:9999;display:flex;flex-direction:column;gap:9px;max-width:390px}
 .toast .t{background:var(--panel2);border:1px solid var(--bad);border-left:4px solid var(--bad);border-radius:9px;padding:12px 15px;box-shadow:0 12px 32px rgba(0,0,0,.6);animation:slideIn .25s}
+/* vision-language vehicle descriptions, under evidence crops */
+.vdesc{font-size:11.5px;line-height:1.45;margin:4px 0;max-width:340px}
+button.mini{padding:2px 7px;font-size:10.5px;font-weight:500;border-radius:4px}
 </style>
 </head>
 <body>
 
 <header>
   <div class="logo">NET<b>RA</b></div>
   <div class="tagline">Networked Evidence, Tracking &amp; Recognition for Analytics<br>
     <span style="color:var(--faint)">Gujarat Police Innovation Challenge 2026 · Model 1 + Model 2</span></div>
   <div class="spacer"></div>
   <div class="pill"><span class="dot" id="wsDot"></span><span id="wsTxt">connecting</span></div>
   <div class="pill" id="pipePill"><span class="dot" id="pipeDot"></span><span id="pipeTxt">pipeline idle</span></div>
+  <!-- Blank in the demo, where no data/api_keys.json exists and the server
+       runs open. It is here so an operator who has configured keys can reach
+       the protected endpoints — the zone editor's still in particular, which
+       is fetched with this header rather than loaded as a plain image src. -->
+  <input id="apiKey" type="password" placeholder="API key (optional)"
+         autocomplete="off" title="Sent as X-API-Key. Leave blank when the server runs open."
+         style="width:150px;padding:5px 8px;background:var(--bg);color:var(--ink);
+                border:1px solid var(--line);border-radius:6px;font-size:12px">
   <button id="btnStart" class="primary">Start pipeline</button>
   <button id="btnStop">Stop</button>
 </header>
 
 <nav>
   <a data-view="overview" class="active">Overview</a>
   <a data-view="map">GIS Map</a>
   <a data-view="wall">Video Wall</a>
   <a data-view="detections">Detections</a>
   <a data-view="route">Vehicle Trace</a>
   <a data-view="watchlist">Watchlist</a>
   <a data-view="alerts">Alerts</a>
+  <a data-view="zones">Zones</a>
+  <a data-view="traffic">Traffic</a>
+  <a data-view="intel">Intelligence</a>
   <a data-view="registry">Registry &amp; Gaps</a>
   <a data-view="assistant">Assistant</a>
 </nav>
 
 <main>
   <!-- OVERVIEW -->
@@ -191,14 +215,14 @@ input.mono{font-family:var(--mono);letter-spacing:1px;text-transform:uppercase}
         <option>bus</option><option>motorcycle</option></select>
       <button id="dSearch" class="primary">Search</button>
       <button id="dCsv">Export CSV</button>
       <span class="faint" id="dCount" style="font-size:12px"></span>
     </div>
     <div class="panel"><div style="max-height:calc(100vh - 250px);overflow:auto"><table id="tblDet">
-      <thead><tr><th>Time (UTC)</th><th>PTS</th><th>Camera</th><th>Class</th><th>Colour</th><th>Plate</th><th>Conf</th><th>Evidence</th></tr></thead>
-      <tbody><tr><td colspan="8" class="empty">No detections.</td></tr></tbody>
+      <thead><tr><th>Time (UTC)</th><th>PTS</th><th>Camera</th><th>Class</th><th>Colour</th><th>Plate</th><th>Conf</th><th>Track</th><th>Scene time</th><th>Evidence</th></tr></thead>
+      <tbody><tr><td colspan="10" class="empty">No detections.</td></tr></tbody>
     </table></div></div>
   </section>
 
   <!-- ROUTE -->
   <section class="view" id="v-route">
     <div class="row">
@@ -268,12 +292,86 @@ input.mono{font-family:var(--mono);letter-spacing:1px;text-transform:uppercase}
         </div>
         <div class="row" id="asstChips" style="margin:0"></div>
       </div>
     </div>
   </section>
 
+  <!-- ZONES -->
+  <section class="view" id="v-zones">
+    <div class="two">
+      <div>
+        <div class="panel" style="margin-bottom:14px">
+          <h3>Define a rule on a live still</h3>
+          <div class="body">
+            <div class="row">
+              <select id="zCam"></select>
+              <button id="zLoad" class="primary">Load still frame</button>
+              <select id="zRule">
+                <option value="intrusion">Intrusion (area)</option>
+                <option value="crossing">Line crossing (2 points)</option>
+                <option value="loitering">Loitering (area)</option>
+              </select>
+              <input id="zName" placeholder="Rule name" style="width:150px">
+              <select id="zClasses"><option value="">Any class</option><option>car</option>
+                <option>truck</option><option>bus</option><option>motorcycle</option><option>person</option></select>
+              <select id="zSev"><option value="critical">Critical</option><option value="high">High</option>
+                <option value="medium" selected>Medium</option><option value="low">Low</option></select>
+              <input id="zDwell" type="number" value="30" style="width:90px" title="Dwell seconds (loitering)">
+              <button id="zClear">Clear points</button>
+              <button id="zSave" class="primary">Save rule</button>
+            </div>
+            <div class="zwrap" id="zWrap" style="display:none">
+              <img id="zImg" alt="camera still">
+              <canvas id="zCanvas"></canvas>
+            </div>
+            <div class="faint" id="zHint" style="font-size:12px;margin-top:8px">
+              Load a still, then click to place points. Points are stored normalised 0&ndash;1,
+              so a rule survives a resolution change on the source camera.</div>
+          </div>
+        </div>
+        <div class="panel"><h3>Live zone events</h3>
+          <div class="body" id="zEvents" style="max-height:340px;overflow:auto">
+            <div class="empty">No zone events yet.</div></div></div>
+      </div>
+      <div class="panel"><h3>Configured rules</h3>
+        <div class="body" id="zList"><div class="empty">No rules configured.</div></div></div>
+    </div>
+  </section>
+
+  <!-- TRAFFIC -->
+  <section class="view" id="v-traffic">
+    <div class="row">
+      <button id="tSnap" class="primary">Write traffic snapshot</button>
+      <button id="tRefresh">Refresh</button>
+      <span class="faint" style="font-size:12px">Counts are cumulative across every replay of a looping
+        recording. <b>Counted this loop</b> beside <b>loops seen</b> is the honest reading.</span>
+    </div>
+    <div class="grid stats" id="tStats"></div>
+    <div class="panel"><h3>Per-camera counters</h3>
+      <div style="max-height:calc(100vh - 380px);overflow:auto"><table id="tblTraffic">
+        <thead><tr><th>Camera</th><th>Active tracks</th><th>Counted this loop</th><th>Total counted</th>
+          <th>Loops seen</th><th>Dropped</th><th>Class mix</th><th>Directions</th><th>Mean dwell</th>
+          <th>History</th></tr></thead>
+        <tbody><tr><td colspan="10" class="empty">Start the pipeline to begin counting.</td></tr></tbody>
+      </table></div></div>
+  </section>
+
+  <!-- INTELLIGENCE -->
+  <section class="view" id="v-intel">
+    <div class="row"><button id="iRefresh" class="primary">Refresh analysis</button>
+      <select id="iGroup"></select>
+      <span class="faint" style="font-size:12px">Every finding here is inference from wide-area footage,
+        carrying the arithmetic behind it. None of it is an identification.</span></div>
+    <div class="panel" style="margin-bottom:14px"><h3>Cloned-plate findings</h3>
+      <div class="body" id="iClones"><div class="empty">Not analysed yet.</div></div></div>
+    <div class="panel" style="margin-bottom:14px"><h3>Behavioural anomalies</h3>
+      <div class="body" id="iAnoms"><div class="empty">Not analysed yet.</div></div></div>
+    <div class="panel"><h3>Mined cross-camera journeys</h3>
+      <div class="body" id="iJourneys"><div class="empty">Not analysed yet.</div></div></div>
+  </section>
+
   <!-- REGISTRY -->
   <section class="view" id="v-registry">
     <div class="row">
       <button id="regOnboard" class="primary">Re-run onboarding &amp; profiling</button>
       <span class="faint" style="font-size:12px">Probes every camera, measures signal quality, and classifies what each can deliver.</span>
     </div>
diff --git a/tools/index_loops.py b/tools/index_loops.py
new file mode 100644
index 0000000..999f0db
--- /dev/null
+++ b/tools/index_loops.py
@@ -0,0 +1,146 @@
+"""Index camera loops, then mine them for real cross-camera journeys.
+
+The grid replays a fixed recording on each camera, and the cameras of one time
+group replay recordings that share a clock. So a loop can be processed once,
+completely, and the resulting index mined for vehicles that genuinely appear on
+more than one camera — a discovered fact from the Government's footage rather
+than a demonstrated capability.
+
+    python tools/index_loops.py --cameras cam01,cam04 --group ahmedabad-13jun
+    python tools/index_loops.py --group ahmedabad-13jun --mine-only
+
+Indexing needs the network. Mining does not: `--mine-only` works entirely from
+detections already stored.
+"""
+from __future__ import annotations
+
+import argparse
+import os
+import sys
+import time
+
+sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
+
+from netra.analytics.loop_index import (estimate_loop_length,  # noqa: E402
+                                        exclusion_report, find_journeys,
+                                        index_camera, persist_journeys)
+from netra.core.db import SessionLocal, init_db  # noqa: E402
+from netra.core.geo import TIME_GROUPS  # noqa: E402
+from netra.core.models import Camera  # noqa: E402
+
+
+def _print_journeys(journeys: list) -> None:
+    if not journeys:
+        print("No journeys found. A journey needs the same vehicle on two "
+              "cameras of one group, with a readable scene clock on both.")
+        return
+    for n, j in enumerate(journeys, 1):
+        print(f"\nJourney {n}: {j.hop_count} sightings across "
+              f"{len(j.cameras)} cameras  "
+              f"confidence {j.confidence:.2f}  "
+              f"mean similarity {j.mean_similarity:.3f}")
+        print(f"  {j.total_km:.2f} km over {j.elapsed_s:.0f}s of recorded time")
+        for hop in j.hops:
+            lead = f"  {hop.camera_id:<7} {hop.at}"
+            if hop.similarity is None:
+                print(f"{lead}  (first sighting)")
+            else:
+                print(f"{lead}  sim {hop.similarity:.3f}  "
+                      f"{hop.leg_km:.2f} km in {hop.leg_seconds:.0f}s "
+                      f"({hop.implied_kmh:.0f} km/h)")
+    print(f"\n{journeys[0].note}")
+
+
+def main() -> int:
+    ap = argparse.ArgumentParser()
+    ap.add_argument("--cameras", default="",
+                    help="comma-separated camera ids to index before mining")
+    ap.add_argument("--group", default="ahmedabad-13jun",
+                    help=f"time group to mine; one of {', '.join(TIME_GROUPS)}")
+    ap.add_argument("--max-seconds", type=float, default=900.0,
+                    help="wall-clock ceiling on each camera's indexing pass")
+    ap.add_argument("--min-similarity", type=float, default=0.84)
+    ap.add_argument("--min-hops", type=int, default=2)
+    ap.add_argument("--probe-loops", action="store_true",
+                    help="measure each camera's loop length first")
+    ap.add_argument("--mine-only", action="store_true",
+                    help="skip indexing and mine what is already stored")
+    ap.add_argument("--no-persist", action="store_true",
+                    help="print journeys without storing them")
+    args = ap.parse_args()
+
+    if args.group not in TIME_GROUPS:
+        print(f"Unknown time group '{args.group}'. "
+              f"Known: {', '.join(sorted(TIME_GROUPS))}")
+        return 1
+
+    init_db()
+    cams = [c.strip() for c in args.cameras.split(",") if c.strip()]
+
+    if cams and not args.mine_only:
+        # One engine for every camera: loading YOLO, the plate model and the
+        # ReID backbone once is most of the start-up cost.
+        from netra.analytics.inference import InferenceEngine
+
+        engine = InferenceEngine(on_detection=lambda det: None)
+        print(f"Loading models on {len(cams)} camera(s)...")
+        engine.load()
+        with SessionLocal() as db:
+            engine.camera_capability = {
+                c.id: c.capability for c in db.query(Camera).all()}
+        engine.start()
+        try:
+            for cam in cams:
+                if args.probe_loops:
+                    length = estimate_loop_length(cam)
+                    print(f"{cam}: loop length "
+                          f"{f'{length:.1f}s (restart to restart)' if length
+                             else 'not measured within the probe timeout'}")
+                t0 = time.time()
+                result = index_camera(cam, engine, max_seconds=args.max_seconds)
+                if result.get("error"):
+                    print(f"{cam}: {result['error']}")
+                    continue
+                print(f"{cam}: {result['frames']} frames, "
+                      f"{result['detections']} vehicles, "
+                      f"{result['written']} stored, "
+                      f"{result['video_seconds']:.0f}s of video, "
+                      f"scene clock on {result['scene_time_coverage']*100:.0f}% "
+                      f"of detections, "
+                      f"loop {'completed' if result['loop_complete'] else 'truncated'} "
+                      f"in {time.time() - t0:.0f}s")
+        finally:
+            engine.stop()
+
+    print(f"\nMining '{args.group}' ({', '.join(TIME_GROUPS[args.group])})")
+    report: dict = {}
+    journeys = find_journeys(args.group, min_similarity=args.min_similarity,
+                             min_hops=args.min_hops, report=report)
+    # Two populations, printed so a reader can tell them apart. The index
+    # figures describe every detection stored for these cameras; the mining
+    # figures describe only the rows that reached the miner, which the database
+    # query has already filtered - printing the miner's "0 excluded for no
+    # scene clock" beside an index where most rows have no clock would read as
+    # a contradiction rather than as two different questions.
+    index = exclusion_report(args.group)
+    if index:
+        print(f"  index: {index['detections_in_group']} detections on these "
+              f"cameras; {index['excluded_no_scene_time']} have no scene "
+              f"clock; of the {index['with_scene_time']} that do, "
+              f"{index['excluded_no_embedding']} have no appearance vector, "
+              f"leaving {index['comparable']} comparable")
+    excluded = report.get("excluded", {})
+    print(f"  mining: {report.get('considered', 0)} sightings chained over, "
+          f"from {report.get('supplied', 0)} rows "
+          f"({report.get('population', 'unknown population')}); dropped here: "
+          f"{excluded.get('no_scene_time', 0)} no clock, "
+          f"{excluded.get('no_embedding', 0)} no appearance vector, "
+          f"{excluded.get('wrong_group', 0)} outside the group")
+    if not args.no_persist:
+        persist_journeys(args.group, journeys)
+    _print_journeys(journeys)
+    return 0
+
+
+if __name__ == "__main__":
+    raise SystemExit(main())
diff --git a/tools/measure_bandwidth.py b/tools/measure_bandwidth.py
new file mode 100644
index 0000000..5452565
--- /dev/null
+++ b/tools/measure_bandwidth.py
@@ -0,0 +1,135 @@
+"""Measure the bandwidth case for edge processing.
+
+The scaling argument for 80,000 cameras rests on regional nodes transmitting
+structured metadata instead of video. That is easy to assert and worth
+measuring, so this does both halves on the live grid:
+
+    video      bytes actually received over RTSP for a camera, over a window
+    metadata   bytes the same camera's detections occupy, over the same window
+
+The ratio between them is the bandwidth argument, stated as a measurement
+rather than an estimate.
+
+    python tools/measure_bandwidth.py --cameras cam04,cam14 --seconds 60
+"""
+from __future__ import annotations
+
+import argparse
+import json
+import os
+import subprocess
+import sys
+import time
+
+sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
+
+from netra import config  # noqa: E402
+
+
+def measure_video_bytes(camera_id: str, seconds: int) -> tuple[int, float]:
+    """Bytes received from one camera over a window, by copying the stream to
+    a null muxer - no decoding, so this is the true network payload."""
+    out = config.DATA / f"_bw_{camera_id}.ts"
+    t0 = time.time()
+    try:
+        subprocess.run(
+            ["ffmpeg", "-v", "error", "-rtsp_transport", "tcp",
+             "-t", str(seconds), "-i", config.rtsp_url(camera_id),
+             "-c", "copy", "-y", str(out)],
+            capture_output=True, timeout=seconds + 90)
+    except subprocess.TimeoutExpired:
+        pass
+    elapsed = time.time() - t0
+    size = out.stat().st_size if out.exists() else 0
+    if out.exists():
+        out.unlink()
+    return size, elapsed
+
+
+def metadata_bytes_per_detection() -> dict:
+    """Size of one detection as stored and as transmitted.
+
+    Two figures matter and differ by an order of magnitude. The appearance
+    embedding is 512 floats and dominates the stored row, but it is only needed
+    where cross-camera matching happens; an edge node forwarding to the centre
+    can send the compact record and retain embeddings locally.
+    """
+    full = {
+        "camera_id": "cam04", "pts_ms": 123456.7,
+        "wall_time": "2026-09-05T01:23:45.678901+00:00",
+        "scene_time": "2026-06-13T23:22:47+00:00",
+        "vehicle_class": "car", "confidence": 0.873,
+        "bbox": [1024, 512, 1180, 640], "colour": "silver",
+        "plate_text": "GJ01AB1234", "plate_conf": 0.812, "plate_chars": 10,
+        "track_id": 417,
+        "embedding": [0.0123456] * 512,
+    }
+    compact = {k: v for k, v in full.items() if k != "embedding"}
+    return {
+        "with_embedding": len(json.dumps(full).encode()),
+        "compact": len(json.dumps(compact).encode()),
+    }
+
+
+def main() -> int:
+    ap = argparse.ArgumentParser()
+    ap.add_argument("--cameras", default="cam04,cam14,cam15")
+    ap.add_argument("--seconds", type=int, default=60)
+    #: vehicles per camera per hour, from the measured live runs
+    ap.add_argument("--detections-per-hour", type=int, default=3000)
+    args = ap.parse_args()
+
+    sizes = metadata_bytes_per_detection()
+    cams = [c.strip() for c in args.cameras.split(",") if c.strip()]
+
+    print(f"Measuring {len(cams)} cameras for {args.seconds}s each\n")
+    print(f"{'camera':<10} {'MB received':>12} {'Mbit/s':>9} {'MB/hour':>10}")
+    print("-" * 45)
+
+    total_mbps = 0.0
+    rows = []
+    for cam in cams:
+        size, elapsed = measure_video_bytes(cam, args.seconds)
+        if not size or elapsed <= 0:
+            print(f"{cam:<10} {'unavailable':>12}")
+            continue
+        mbps = (size * 8) / elapsed / 1e6
+        mb_hour = size / elapsed * 3600 / 1e6
+        total_mbps += mbps
+        rows.append((cam, mbps, mb_hour))
+        print(f"{cam:<10} {size/1e6:>12.1f} {mbps:>9.2f} {mb_hour:>10.0f}")
+
+    if not rows:
+        print("\nNo cameras measured.")
+        return 1
+
+    mean_mbps = total_mbps / len(rows)
+    mean_mb_hour = sum(r[2] for r in rows) / len(rows)
+
+    # Metadata side, using the compact record an edge node forwards.
+    meta_hour_mb = args.detections_per_hour * sizes["compact"] / 1e6
+    meta_hour_mb_emb = args.detections_per_hour * sizes["with_embedding"] / 1e6
+
+    print(f"\nVideo, mean per camera      : {mean_mbps:.2f} Mbit/s "
+          f"({mean_mb_hour:.0f} MB/hour)")
+    print(f"Metadata record, compact    : {sizes['compact']} bytes")
+    print(f"Metadata record, +embedding : {sizes['with_embedding']} bytes")
+    print(f"Metadata per camera-hour    : {meta_hour_mb:.2f} MB "
+          f"at {args.detections_per_hour} detections/hour")
+    print(f"                              {meta_hour_mb_emb:.2f} MB with embeddings")
+    print(f"\nReduction, compact metadata : {mean_mb_hour / meta_hour_mb:.0f}x")
+    print(f"Reduction, with embeddings  : {mean_mb_hour / meta_hour_mb_emb:.0f}x")
+
+    print(f"\nStatewide, 80,000 cameras:")
+    print(f"  continuous video          : "
+          f"{80000 * mean_mbps / 1000:,.0f} Gbit/s sustained")
+    print(f"  compact metadata          : "
+          f"{80000 * meta_hour_mb / 1000:,.1f} GB/hour "
+          f"({80000 * meta_hour_mb * 8 / 3600 / 1000:,.2f} Gbit/s)")
+    print("\nEvidence crops are additional and are transmitted on demand or "
+          "for alerts only, not continuously.")
+    return 0
+
+
+if __name__ == "__main__":
+    raise SystemExit(main())
diff --git a/tools/purge_scene_times.py b/tools/purge_scene_times.py
new file mode 100644
index 0000000..26184d3
--- /dev/null
+++ b/tools/purge_scene_times.py
@@ -0,0 +1,82 @@
+"""Null the scene times that were never corroborated.
+
+Corroborated anchoring - two independent overlay readings that agree once
+projected forward by PTS - landed after a large part of this store had already
+been indexed. Those earlier rows carry a scene time derived from a single OCR
+reading, and a single misread digit anchors an entire stream: this grid
+produced spans dated 2025-06-14, 2026-06-24 and 2028-06-13 that way, each from
+one bad read that passed every syntactic check.
+
+The analytics already refuse to reason over an uncorroborated scene time (see
+netra/core/timing.py), so nothing is *concluded* from those values any more.
+This tool is for the operator who would rather the wrong number were not
+sitting in the column at all, where a direct SQL query or an export could still
+pick it up.
+
+    python tools/purge_scene_times.py              # dry run, counts only
+    python tools/purge_scene_times.py --apply      # actually nulls them
+
+Dry run is the default deliberately: this destroys data, and the count alone
+answers the question most people are asking.
+"""
+from __future__ import annotations
+
+import argparse
+import os
+import sys
+
+sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
+
+from netra.core.db import SessionLocal, init_db  # noqa: E402
+from netra.core.models import Detection  # noqa: E402
+
+
+def affected_count(db) -> int:
+    """Rows carrying a scene time no second reading ever confirmed."""
+    return (db.query(Detection)
+            .filter(Detection.scene_time.isnot(None),
+                    Detection.scene_time_corroborated.is_(False))
+            .count())
+
+
+def main() -> int:
+    ap = argparse.ArgumentParser(description=__doc__,
+                                 formatter_class=argparse.RawDescriptionHelpFormatter)
+    ap.add_argument("--apply", action="store_true",
+                    help="actually null the values; without it nothing is written")
+    args = ap.parse_args()
+
+    # Additive columns are applied here as everywhere else; a store predating
+    # scene_time_corroborated would otherwise fail on the filter above.
+    init_db()
+
+    with SessionLocal() as db:
+        total = db.query(Detection).count()
+        clocked = db.query(Detection).filter(Detection.scene_time.isnot(None)).count()
+        affected = affected_count(db)
+
+        print(f"detections stored:            {total}")
+        print(f"  carrying a scene time:      {clocked}")
+        print(f"  of those, uncorroborated:   {affected}")
+
+        if not args.apply:
+            print("\ndry run: nothing written. Re-run with --apply to null "
+                  f"scene_time on those {affected} rows.")
+            return 0
+
+        if not affected:
+            print("\nnothing to do.")
+            return 0
+
+        updated = (db.query(Detection)
+                   .filter(Detection.scene_time.isnot(None),
+                           Detection.scene_time_corroborated.is_(False))
+                   .update({Detection.scene_time: None},
+                           synchronize_session=False))
+        db.commit()
+        print(f"\nnulled scene_time on {updated} rows.")
+    return 0
+
+
+if __name__ == "__main__":
+    raise SystemExit(main())
