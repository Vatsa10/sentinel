# Review package — Task 9

c89d4d1 Vatsa Joshi | Look attributes up only for the candidates that are actually returned
200d315 Vatsa Joshi | Show the vehicle description in the console, and stop reading colour off a monochrome caption
c189737 Vatsa10 | feat: add vehicle attribute extraction and description capabilities

 netra/analytics/attributes.py | 595 ++++++++++++++++++++++++++++++++++++++++++
 netra/analytics/reid.py       |  79 ++++++
 netra/api/app.py              | 123 ++++++++-
 netra/api/assistant.py        |  50 +++-
 netra/api/retrieval.py        |  42 ++-
 netra/config.py               |  16 ++
 netra/core/models.py          |  36 +++
 netra/pipeline.py             | 239 ++++++++++++++++-
 netra/web/app.js              |  84 +++++-
 netra/web/index.html          |   3 +
 10 files changed, 1251 insertions(+), 16 deletions(-)

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
diff --git a/netra/analytics/reid.py b/netra/analytics/reid.py
index 5b99bad..6a16ead 100644
--- a/netra/analytics/reid.py
+++ b/netra/analytics/reid.py
@@ -39,20 +39,75 @@ SIMILARITY_THRESHOLD = 0.80
 #: When the runner-up scores within this of the top match, the two cannot be
 #: told apart on appearance and neither may be presented as the answer.
 AMBIGUITY_MARGIN = 0.02
 
 _AMBIGUITY_NOTE = (
     "Near-identical appearance scores: other candidates are within "
     f"{AMBIGUITY_MARGIN:.2f} of the top match, so appearance alone cannot "
     "separate them. Confirm against another signal before acting.")
 
 
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
 def flag_ambiguity(scored: list[dict]) -> list[dict]:
     """Mark results the appearance evidence cannot actually separate.
 
     Two silver hatchbacks embed almost identically, so a ranked list whose top
     scores are nearly equal has picked a winner the evidence does not support.
     The ambiguous candidates are kept rather than dropped - an operator shown
     "three near-identical candidates" is better served than one shown a single
     confident wrong answer - but every result carries the flag so the console
     can never render the top hit as if it stood alone.
 
@@ -195,15 +250,39 @@ def _self_check() -> None:
     clear = [{"similarity": 0.95}, {"similarity": 0.84}]
     flag_ambiguity(clear)
     assert not any(r["ambiguous"] for r in clear), clear
 
     # Exactly on the margin counts as ambiguous: the boundary should not be
     # resolved in favour of false confidence.
     edge = [{"similarity": 0.90}, {"similarity": 0.88}]
     flag_ambiguity(edge)
     assert edge[0]["ambiguous"] and edge[1]["ambiguous"], edge
 
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
+
     print("reid self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/api/app.py b/netra/api/app.py
index 0542703..669409b 100644
--- a/netra/api/app.py
+++ b/netra/api/app.py
@@ -17,21 +17,22 @@ from sqlalchemy import func, select
 from sqlalchemy.orm import joinedload
 
 from fastapi import Depends, Header
 
 from netra import config
 from netra.analytics.loop_index import has_embedding
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
 
 app = FastAPI(title="NETRA", version="1.0",
               description="Networked Evidence, Tracking & Recognition for Analytics")
 
 WEB_DIR = config.ROOT / "netra" / "web"
@@ -260,39 +261,120 @@ def list_detections(camera_id: str | None = None, plate: str | None = None,
             q = q.filter(Detection.plate_text.ilike(f"%{plate}%"))
         if vehicle_class:
             q = q.filter(Detection.vehicle_class == vehicle_class)
         if colour:
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
             "plate_chars": d.plate_chars,
             "evidence": d.evidence_path, "bbox": d.bbox,
             "track_id": d.track_id,
             "scene_time": d.scene_time.isoformat() if d.scene_time else None,
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
         by_class = dict(db.query(Detection.vehicle_class,
                                  func.count(Detection.id))
                         .group_by(Detection.vehicle_class).all())
         by_camera = dict(db.query(Detection.camera_id, func.count(Detection.id))
@@ -368,38 +450,43 @@ def delete_watchlist(entry_id: int, _p=Depends(require("watchlist"))):
 
 
 # ----------------------------------------------------------------- alerts --
 @app.get("/api/alerts")
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
                 "id": a.id, "at": a.created_at.isoformat(),
                 "camera_id": a.camera_id,
                 "camera_name": cam.name if cam else None,
                 "lat": cam.lat if cam else None, "lon": cam.lon if cam else None,
                 "score": a.score, "match_type": a.match_type,
                 "reasons": a.reasons, "severity": a.severity,
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
     with SessionLocal() as db:
         a = db.get(Alert, alert_id)
         if not a:
             raise HTTPException(404, "not found")
@@ -549,37 +636,40 @@ def seed_watchlist(_p=Depends(require("watchlist"))):
 @app.get("/api/vehicles/{detection_id}/similar")
 def similar_vehicles(detection_id: int, limit: int = Query(25, le=100),
                      min_similarity: float = 0.80):
     """Find the same vehicle on other cameras by appearance.
 
     This is the answer to "trace this vehicle" when no plate is readable, which
     on this grid is the normal case. Results are ranked candidates carrying
     their similarity score, ordered in time, and filtered for space-time
     plausibility - not assertions of identity.
     """
-    from netra.analytics.reid import flag_ambiguity, similarity
+    from netra.analytics.reid import (attribute_agreement, flag_ambiguity,
+                                      similarity)
     from netra.core.geo import haversine_km, time_group
     from netra.analytics.matching import spacetime_plausible
 
     with SessionLocal() as db:
         query = db.get(Detection, detection_id)
         if query is None:
             raise HTTPException(404, "detection not found")
         if not query.embedding:
             raise HTTPException(
                 400, "this detection has no appearance embedding")
 
         qcam = db.get(Camera, query.camera_id)
         others = (db.query(Detection).options(joinedload(Detection.camera))
                   .filter(Detection.id != detection_id,
                           has_embedding()).all())
 
+        query_attrs = _attributes_for([detection_id], db).get(detection_id)
+
         scored = []
         for det in others:
             sim = similarity(query.embedding, det.embedding)
             if sim < min_similarity:
                 continue
             cam = det.camera
             km = 0.0
             if qcam and cam and None not in (qcam.lat, qcam.lon, cam.lat, cam.lon):
                 km = haversine_km(qcam.lat, qcam.lon, cam.lat, cam.lon)
             secs = abs((det.wall_time - query.wall_time).total_seconds())
@@ -595,50 +685,77 @@ def similar_vehicles(detection_id: int, limit: int = Query(25, le=100),
                 "camera_id": det.camera_id,
                 "camera_name": cam.name if cam else None,
                 "lat": cam.lat if cam else None,
                 "lon": cam.lon if cam else None,
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
         # Two vehicles that look alike score alike, so where the top results
         # are separated by less than the appearance model can resolve, every
         # one of them is flagged. The console needs this to avoid rendering a
         # coin-toss as an identification.
         matches = flag_ambiguity(scored[:limit])
 
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
+
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
         "ambiguous": any(m.get("ambiguous") for m in matches),
         "note": ("Ranked candidates for operator confirmation, not identification. "
                  "Appearance evidence alone does not establish that two sightings "
                  "are the same vehicle."),
     }
 
 
 @app.get("/api/vehicles/{detection_id}/track")
 def appearance_track(detection_id: int, min_similarity: float = 0.82):
     """Build a movement path for a vehicle using appearance alone.
diff --git a/netra/api/assistant.py b/netra/api/assistant.py
index 38d3b90..90dde54 100644
--- a/netra/api/assistant.py
+++ b/netra/api/assistant.py
@@ -24,22 +24,22 @@ from __future__ import annotations
 
 import re
 from datetime import datetime, timedelta, timezone
 
 from sqlalchemy import func
 from sqlalchemy.orm import joinedload
 
 from netra.api import retrieval
 from netra.core.db import SessionLocal
 from netra.core.geo import TIME_GROUPS
-from netra.core.models import (Alert, Camera, Detection, WatchlistEntry,
-                               ZoneEventRow, ZoneRule)
+from netra.core.models import (Alert, Camera, Detection, VehicleAttributeRow,
+                               WatchlistEntry, ZoneEventRow, ZoneRule)
 
 PLATE_RE = re.compile(r"\b([A-Z]{2}\s?\d{1,2}\s?[A-Z]{0,3}\s?\d{3,4})\b", re.I)
 
 
 def _answer(text: str, data=None, actions=None) -> dict:
     return {"answer": text, "data": data or {}, "actions": actions or []}
 
 
 # -- intents ----------------------------------------------------------------
 
@@ -386,44 +386,84 @@ def _watchlist_facts(entry_id: str):
         data = {"id": e.id, "plate": e.plate, "category": e.category,
                 "severity": e.severity, "case_ref": e.case_ref,
                 "active": e.active, "sightings": seen, "alerts": alerts}
         case = f", case {e.case_ref}" if e.case_ref else ""
         text = (f"Watchlist entry {e.id} - {e.plate}, {e.category} "
                 f"({e.severity}){case}. {seen} sighting(s) recorded, "
                 f"{alerts} alert(s) raised.")
     return text, data
 
 
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
 _FACTS = {"camera": _camera_facts, "zone": _zone_facts,
-          "watchlist": _watchlist_facts}
+          "watchlist": _watchlist_facts, "vehicle": _vehicle_facts}
 
 
 def _entity_action(m: retrieval.EntityMatch) -> dict:
     if m.kind == "camera":
         return {"label": f"Open {m.id}", "view": "registry", "query": m.id}
     if m.kind == "zone":
         return {"label": "Open zones", "view": "zones"}
+    if m.kind == "vehicle":
+        return {"label": f"Open detection {m.id}", "view": "detections",
+                "query": m.id}
     return {"label": f"Trace {m.label}", "view": "route", "query": m.label}
 
 
 def _search(q: str) -> dict:
     """Free-text lookup: resolve what was named, then state the SQL facts.
 
     Two steps that never blur into one. Retrieval produces ids and a ranking;
     every figure in the answer comes from a query against those ids.
     """
     query = re.sub(r"^\s*(search|look\s*(up|for)|lookup)\b(\s+for)?[:\s]*",
                    "", q or "", flags=re.I).strip() or (q or "")
     matches = retrieval.resolve(query, limit=5, ignore=INTENT_VOCAB)
     if not matches:
         return _answer(
-            f"Nothing in the camera registry, the zone rules or the watchlist "
+            f"Nothing in the camera registry, the zone rules, the watchlist "
+            f"or the described vehicles "
             f"matches '{query}' closely enough for me to be sure what you "
             f"meant, and guessing would be worse than saying so. A camera id, "
             f"a place name or a registration number will find it.",
             {"query": query, "matches": []}, _help("")["actions"])
 
     found, lines = [], []
     for m in matches:
         got = _FACTS[m.kind](m.id)
         if got is None:
             # The index is TTL-cached, so a row can be deleted between build
@@ -650,21 +690,21 @@ def _self_check() -> None:
     # that were already there. Routing only, so no database is involved.
     for q in ("search for the junagadh bypass", "look up cam06",
               "lookup GJ-AHM-014"):
         assert route(q) is _search, (q, route(q))
     assert route("which cameras are down?") is _camera_health
     assert route("how many detections") is _detection_summary
 
     # Resolution decides which row is queried, never what it contains: every
     # fact function reads from the database and none of them is reachable
     # without an id that the index actually holds.
-    assert set(_FACTS) == {"camera", "zone", "watchlist"}
+    assert set(_FACTS) == {"camera", "zone", "watchlist", "vehicle"}
 
     # The honesty constraint on a scoped answer: it must name the entity and
     # mark it as inferred, so a wrong substitution is visible.
     note = _resolution_note(
         retrieval.EntityMatch("camera", "cam06", "Timbavadi gate", 3.1))
     assert "cam06" in note and "Timbavadi gate" in note, note
     assert "inferred" in note and "not confirmed" in note, note
 
     # The words an intent routes on must never be counted against the mention
     # standing beside them. Checked on a synthetic corpus, so no database.
diff --git a/netra/api/retrieval.py b/netra/api/retrieval.py
index 8409d6a..70a2f71 100644
--- a/netra/api/retrieval.py
+++ b/netra/api/retrieval.py
@@ -84,20 +84,29 @@ MIN_EVIDENCE = 1.0
 #: ANPR"); a symmetric score would punish the label for its extra words.
 MIN_TRIGRAM_CONTAINMENT = 0.7
 
 #: Below this many characters a trigram comparison is noise - one or two
 #: trigrams will coincide with something in any corpus of a few hundred labels.
 #: Four is the shortest word an operator actually shortens a camera to ("toll"
 #: for "Tollnaka"), and at four the containment threshold demands every trigram
 #: match, which is strict enough to stay safe.
 MIN_TRIGRAM_QUERY_LEN = 4
 
+#: How many described vehicles the index carries, newest first. Cameras, zones
+#: and the watchlist are a few hundred rows between them and are indexed whole;
+#: descriptions accumulate one per described detection and would eventually
+#: dominate a rebuild the TTL performs on an operator's own request. Newest
+#: first because a search for a vehicle is nearly always a search for a recent
+#: one. ponytail: its ceiling is exactly that - an older described vehicle is
+#: unfindable by description, and stays findable only by camera, time or plate.
+VEHICLE_INDEX_LIMIT = 5000
+
 _TOKEN_RE = re.compile(r"[a-z0-9]+")
 
 log = logging.getLogger(__name__)
 
 
 def tokenise(text: str) -> list[str]:
     """Lowercase, split on anything that is not a letter or digit.
 
     Camera ids look like `GJ-AHM-014` and plates like `GJ01AB1234`, so the
     split has to treat punctuation as a separator while keeping digits as part
@@ -123,21 +132,21 @@ def normalise(text: str) -> str:
 def _trigrams(text: str) -> set[str]:
     n = normalise(text)
     if len(n) < 3:
         return {n} if n else set()
     return {n[i:i + 3] for i in range(len(n) - 2)}
 
 
 @dataclass(frozen=True)
 class EntityMatch:
     """One resolved entity. Carries no facts - an id and how sure we are."""
-    kind: str            # camera | zone | watchlist
+    kind: str            # camera | zone | watchlist | vehicle
     id: str
     label: str
     score: float
     #: Which mechanism resolved it, so the assistant can be honest about a
     #: match that came from a spelling repair rather than a real token hit.
     via: str = "bm25"
 
     def as_dict(self) -> dict:
         return {"kind": self.kind, "id": self.id, "label": self.label,
                 "score": round(self.score, 3), "via": self.via}
@@ -310,38 +319,53 @@ class EntityIndex:
 
 
 # -- the live index ----------------------------------------------------------
 
 _CACHE: dict[str, object] = {"index": None, "at": 0.0}
 
 
 def _rows_from_db() -> list[tuple[str, str, str, str]]:
     """(kind, id, label, searchable text) for every resolvable entity."""
     from netra.core.db import SessionLocal
-    from netra.core.models import Camera, WatchlistEntry, ZoneRule
+    from netra.core.models import (Camera, VehicleAttributeRow,
+                                   WatchlistEntry, ZoneRule)
 
     out: list[tuple[str, str, str, str]] = []
     with SessionLocal() as db:
         for c in db.query(Camera).all():
             # The id is part of the text because operators say "AHM 14" as
             # often as they say the camera's name.
             text = " ".join(x for x in (c.id, c.name, c.city, c.district,
                                         c.capability) if x)
             out.append(("camera", c.id, c.name or c.id, text))
         for z in db.query(ZoneRule).all():
             text = " ".join(x for x in (z.name, z.rule, z.camera_id) if x)
             out.append(("zone", str(z.id), z.name or f"zone {z.id}", text))
         for w in db.query(WatchlistEntry).all():
             text = " ".join(x for x in (w.plate, w.case_ref, w.notes,
                                         w.category, w.owner_name,
                                         w.vehicle_make) if x)
             out.append(("watchlist", str(w.id), w.plate, text))
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
+            if not text.strip():
+                continue
+            out.append(("vehicle", str(v.detection_id), v.description, text))
     return out
 
 
 def build_index(rows=None) -> EntityIndex:
     """Build an index from the given rows, or from the database."""
     idx = EntityIndex()
     for kind, entity_id, label, text in (rows if rows is not None
                                          else _rows_from_db()):
         idx.add(kind, entity_id, label, text)
     idx.finalise()
@@ -385,20 +409,24 @@ _SYNTHETIC = [
     ("camera", "GJ-AHM-014", "Ahmedabad SG Highway Tollnaka",
      "GJ-AHM-014 Ahmedabad SG Highway Tollnaka Ahmedabad Ahmedabad anpr"),
     ("camera", "GJ-RAJ-002", "Rajkot Ring Road North",
      "GJ-RAJ-002 Rajkot Ring Road North Rajkot Rajkot vehicle"),
     ("camera", "GJ-SUR-009", "Surat Ring Road South",
      "GJ-SUR-009 Surat Ring Road South Surat Surat degraded"),
     ("zone", "3", "Bypass hard shoulder",
      "Bypass hard shoulder intrusion GJ-JUN-004"),
     ("watchlist", "7", "GJ01AB1234",
      "GJ01AB1234 FIR-2026-118 reported stolen from Vadodara stolen"),
+    ("vehicle", "9182", "black suv; tinted windows; alloy wheels; roof rack",
+     "black suv tinted windows alloy wheels roof rack"),
+    ("vehicle", "9200", "white van; markings: Om Travels",
+     "white van markings Om Travels"),
 ]
 
 
 def _self_check() -> None:
     """Resolution never touches the database here: the failure worth pinning
     down is what the scorer does with a corpus, not what the registry holds."""
     idx = build_index(_SYNTHETIC)
     assert len(idx) == len(_SYNTHETIC)
 
     # An exact name resolves, and resolves to the right row.
@@ -444,20 +472,30 @@ def _self_check() -> None:
             assert (hit.kind, hit.id) in known, (q, hit)
 
     m = idx.resolve("bypass", kind="zone")
     assert m and all(x.kind == "zone" for x in m), m
     assert idx.resolve("junagadh bypass", kind="watchlist") == []
 
     # A watchlist entry resolves by its case reference, not only its plate.
     m = idx.resolve("FIR-2026-118")
     assert m and m[0].kind == "watchlist" and m[0].id == "7", m
 
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
     # An empty corpus must be answerable, not an exception.
     assert build_index([]).resolve("anything") == []
 
     # A short fragment of a longer word resolves, because an operator says
     # "toll" and the registry says "Tollnaka".
     m = idx.resolve("toll")
     assert m and m[0].id == "GJ-AHM-014" and m[0].via == "trigram", m
 
     # The fallback is consulted only when token matching found nothing, so it
     # can never displace a genuine lexical hit.
diff --git a/netra/config.py b/netra/config.py
index e8e0b1c..817613b 100644
--- a/netra/config.py
+++ b/netra/config.py
@@ -120,10 +120,26 @@ EVIDENCE_MAX_BYTES = int(os.getenv("NETRA_EVIDENCE_MAX_BYTES", str(5 * 1024**3))
 # Beyond a week an evidence crop is no longer operationally useful; the
 # detection row and its metadata survive far longer for trend and route work.
 EVIDENCE_MAX_AGE_DAYS = int(os.getenv("NETRA_EVIDENCE_MAX_AGE_DAYS", "7"))
 # Row cap on the detections table. SQLite query plans on the indexed columns
 # stay comfortable to a few million rows; past that the console's time-window
 # queries start to be felt.
 DETECTION_MAX_ROWS = int(os.getenv("NETRA_DETECTION_MAX_ROWS", "2000000"))
 # Floor under the row cap: recent detections are never pruned however far over
 # the cap the table is, because they are what an operator is actively querying.
 DETECTION_KEEP_DAYS = int(os.getenv("NETRA_DETECTION_KEEP_DAYS", "1"))
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
diff --git a/netra/core/models.py b/netra/core/models.py
index 9a0e838..f4e6a15 100644
--- a/netra/core/models.py
+++ b/netra/core/models.py
@@ -245,10 +245,46 @@ class MinedJourney(Base):
     min_similarity: Mapped[float] = mapped_column(Float, default=0.84)
     #: the chain was cut at a ceiling rather than ending naturally
     truncated: Mapped[bool] = mapped_column(Boolean, default=False)
     #: scene time, not wall time: these bound the journey on the recorded clock
     first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
     last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
     hops: Mapped[list] = mapped_column(JSON, default=list)
     note: Mapped[str | None] = mapped_column(Text)
     created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=_utcnow, index=True)
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
diff --git a/netra/pipeline.py b/netra/pipeline.py
index 5ab89da..e5f4359 100644
--- a/netra/pipeline.py
+++ b/netra/pipeline.py
@@ -11,30 +11,43 @@ import threading
 import time
 from datetime import datetime, timezone
 
 import cv2
 
 from netra import config
 from netra.analytics.inference import InferenceEngine
 from netra.analytics.matching import WatchlistIndex, score_match
 from netra.core.db import SessionLocal
 from netra.core.models import (Alert, Camera, Detection, TrafficStat,
-                               WatchlistEntry, ZoneEventRow, ZoneRule)
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
         self.supervisor = IngestSupervisor(
             sink=self.engine.submit,
             on_discontinuity=self._handle_discontinuity)
 
@@ -61,20 +74,48 @@ class Pipeline:
         self._traffic_last_counts: dict[str, dict[str, int]] = {}
 
         # Zone rules are evaluated inside the inference engine, where the
         # tracks live; the pipeline supplies the engine and receives events.
         from netra.analytics.zones import ZoneEngine
         self.zone_engine = ZoneEngine()
         self.engine.zone_engine = self.zone_engine
         self.engine.on_zone_event = self._handle_zone_event
         self._last_traffic_flush = 0.0
 
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
+
     # -- lifecycle -----------------------------------------------------------
     def start(self, camera_ids: list[str] | None = None,
               source_specs: dict | None = None) -> None:
         """Begin processing. `source_specs` overrides how a camera is reached,
         which is how participant-supplied video files are onboarded alongside
         live grid cameras."""
         if self.running:
             return
         with SessionLocal() as db:
             cams = db.query(Camera).filter(Camera.enabled.is_(True)).all()
@@ -84,31 +125,42 @@ class Pipeline:
 
         self._load_zone_rules()
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
 
     def _load_zone_rules(self) -> None:
         """Load zone rules from the database into the evaluation engine."""
         from netra.analytics.zones import Zone
         with SessionLocal() as db:
             rules = db.query(ZoneRule).filter(ZoneRule.active.is_(True)).all()
             by_camera: dict[str, list] = {}
             for r in rules:
                 by_camera.setdefault(r.camera_id, []).append(Zone(
@@ -154,37 +206,45 @@ class Pipeline:
         with SessionLocal() as db:
             row = ZoneEventRow(
                 zone_rule_id=rule_id, camera_id=event.camera_id,
                 rule=event.rule, track_id=event.track_id,
                 object_class=event.vehicle_class, direction=event.direction,
                 detail=event.detail, severity=event.zone.severity,
                 evidence_path=evidence_path)
             db.add(row)
             db.commit()
             db.refresh(row)
+            row_id = row.id
             payload = {
                 "kind": "zone",
-                "id": row.id,
+                "id": row_id,
                 "camera_id": event.camera_id,
                 "zone": event.zone.name,
                 "rule": event.rule,
                 "severity": event.zone.severity,
                 "object_class": event.vehicle_class,
                 "direction": event.direction,
                 "detail": event.detail,
                 "evidence": evidence_path,
                 "at": row.at.isoformat(),
             }
 
         self.stats["zone_events"] += 1
         log.warning("ZONE %s on %s: %s", event.rule, event.camera_id, event.detail)
         self._broadcast(payload)
+        # After the broadcast, for the same reason as on the alert path. A zone
+        # event has no detection row to key attributes to - the evidence is a
+        # whole frame rather than one vehicle - so the description is pushed to
+        # the console if it is ready in time, and not stored.
+        self._submit_attributes(None, evidence_path, "zone",
+                                alert={"zone_event_id": row_id,
+                                       "camera_id": event.camera_id})
         NOTIFIER.submit({**payload, "plate_watchlist": event.zone.name,
                          "plate_observed": event.detail,
                          "match_type": event.rule, "score": 1.0,
                          "reasons": {"zone": {"score": 1.0,
                                               "detail": event.detail}}})
 
     def flush_traffic_stats(self, bucket_seconds: int = 60) -> int:
         """Snapshot per-camera traffic counters into a time bucket.
 
         `total` is the traffic counted *during this bucket*, obtained by
@@ -310,20 +370,125 @@ class Pipeline:
             db.add_all(rows)
             db.commit()
             ids = [r.id for r in rows]
         self.stats["written"] += len(rows)
 
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
                 entries = db.query(WatchlistEntry).filter(
                     WatchlistEntry.active.is_(True)).all()
                 self._watchlist_cache = [{
                     "id": e.id, "plate": e.plate, "category": e.category,
@@ -365,40 +530,48 @@ class Pipeline:
                 watchlist_id=entry["id"],
                 camera_id=det.camera_id,
                 score=result.score,
                 match_type=result.match_type,
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
                 "case_ref": entry.get("case_ref"),
                 "score": result.score,
                 "match_type": result.match_type,
                 "reasons": result.reasons,
                 "at": alert.created_at.isoformat(),
             }
 
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
 
     def unsubscribe(self, q: queue.Queue) -> None:
         with self._lock:
             if q in self.alert_subscribers:
@@ -423,15 +596,75 @@ class Pipeline:
             "write_queue_depth": self._write_queue.qsize(),
             "scheduling": self.supervisor.scheduling(),
             "traffic": self.engine.trackers.stats(),
             "zone_events": self.stats["zone_events"],
             "watchlist_index": self._watchlist_index.stats(),
             # Cameras the engine has stopped inferring on because their feed
             # went black. Surfaced rather than silent: a control room must be
             # able to see that a camera is no longer being looked at.
             "dark_cameras": self.engine.dark_cameras(),
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
diff --git a/netra/web/app.js b/netra/web/app.js
index 5bc0d19..c399e0f 100644
--- a/netra/web/app.js
+++ b/netra/web/app.js
@@ -163,20 +163,80 @@ $("#btnWall").onclick = async () => {
         <span style="font-size:10.5px">${esc(e.message)}</span>`;
     }
   }
 };
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
   if (!d.items.length) { tb.innerHTML = `<tr><td colspan="10" class="empty">No matching detections.</td></tr>`; return; }
@@ -185,21 +245,22 @@ async function loadDetections() {
     <td class="mono faint">${Math.round(x.pts_ms)}</td>
     <td>${esc(x.camera_name || x.camera_id)}</td>
     <td>${esc(x.vehicle_class)}</td><td class="dim">${esc(x.colour || "—")}</td>
     <td class="mono">${x.plate_text ? esc(x.plate_text) : '<span class="faint">—</span>'}
       ${x.plate_chars ? `<span class="faint" style="font-size:10.5px">· ${esc(x.plate_chars)} chars</span>` : ""}</td>
     <td class="dim">${x.plate_conf ?? "—"}</td>
     <td class="mono faint">${x.track_id != null ? esc(x.track_id) : "—"}</td>
     <td class="mono faint">${x.scene_time
       ? esc(x.scene_time.replace("T", " ").slice(0, 19))
       : '<span title="no clock recovered from the overlay">—</span>'}</td>
-    <td>${x.evidence ? `<img src="${esc(x.evidence)}" style="height:34px;border-radius:4px">` : ""}</td></tr>`).join("");
+    <td data-desc-holder>${x.evidence ? `<img src="${esc(x.evidence)}" style="height:34px;border-radius:4px">` : ""}
+      ${attrHtml(x.attributes)}${describeBtn(x.id, !!x.attributes)}</td></tr>`).join("");
 }
 $("#dSearch").onclick = loadDetections;
 $("#dCsv").onclick = () => {
   const p = $("#dPlate").value;
   location.href = "/api/export/detections.csv" + (p ? "?plate=" + encodeURIComponent(p) : "");
 };
 
 /* --------------------------------------------------------------- route --- */
 function initRouteMap() {
   if (!ROUTE_MAP) {
@@ -305,21 +366,24 @@ function alertHtml(a) {
       <span class="spacer" style="margin-left:auto"></span>
       <span class="faint mono" style="font-size:11px">${esc((a.at || "").slice(11, 19))}</span>
     </div>
     <div style="font-size:12.5px">
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
 }
 $("#aRefresh").onclick = loadAlerts;
 
 /* ------------------------------------------------------------ registry --- */
@@ -370,20 +434,34 @@ function connectWs() {
   const proto = location.protocol === "https:" ? "wss" : "ws";
   const ws = new WebSocket(`${proto}://${location.host}/ws/alerts`);
   ws.onopen = () => { $("#wsDot").className = "dot on"; $("#wsTxt").textContent = "alerts live"; };
   ws.onclose = () => {
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
     if (a.kind === "zone") {
       // A rule breach is not a watchlist hit, and rendering one as the other
       // would put an identity claim on an event that carries none.
       feed.insertAdjacentHTML("afterbegin", zoneEventHtml(a));
       const ze = $("#zEvents");
       if (ze) {
         if (ze.querySelector(".empty")) ze.innerHTML = "";
         ze.insertAdjacentHTML("afterbegin", zoneEventHtml(a));
@@ -517,21 +595,21 @@ $("#rAppearance").onclick = async () => {
   $("#routeRejected").innerHTML = r.rejected.length
     ? r.rejected.map(x => `<div class="finding" style="border-color:var(--bad);background:rgba(239,68,68,.06)">
         <b class="mono">${esc(x.camera_id)}</b> — ${esc(x.plausibility || "excluded")}</div>`).join("")
     : `<div class="faint" style="font-size:12px">None excluded.</div>`;
 };
 
 /* ---------------------------------------------------------------- zones --- */
 let ZPOINTS = [];                      // normalised [x, y] pairs, in click order
 
 function zoneEventHtml(e) {
-  return `<div class="zev">
+  return `<div class="zev" data-zone-event="${esc(e.id ?? "")}">
     <div class="row" style="margin:0 0 5px 0;gap:8px">
       <span class="tag sev-${esc(e.severity || "medium")}">${esc(e.severity || "")}</span>
       <span class="tag t-vehicle">${esc(e.rule)}</span>
       <b>${esc(e.zone || "zone")}</b>
       <span class="faint mono" style="font-size:11px">${esc(e.camera_name || e.camera_id)}</span>
       <span style="margin-left:auto" class="faint mono">${esc((e.at || "").slice(11, 19))}</span>
     </div>
     <div class="dim">${esc(e.detail || "")}
       ${e.object_class ? `· ${esc(e.object_class)}` : ""}
       ${e.direction ? `· heading ${esc(e.direction)}` : ""}</div>
diff --git a/netra/web/index.html b/netra/web/index.html
index 7aec271..0d49e92 100644
--- a/netra/web/index.html
+++ b/netra/web/index.html
@@ -110,20 +110,23 @@ input.mono{font-family:var(--mono);letter-spacing:1px;text-transform:uppercase}
 .zwrap img{display:block;max-width:100%}
 .zwrap canvas{position:absolute;inset:0;width:100%;height:100%;cursor:crosshair}
 .zone-item{display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px solid var(--line)}
 .zone-item:last-child{border-bottom:none}
 .sparks{display:flex;align-items:flex-end;gap:2px;height:38px;margin-top:8px}
 .sparks i{flex:1;background:var(--blue);border-radius:2px 2px 0 0;min-height:2px;display:block}
 .zev{border-left:3px solid var(--warn);background:var(--panel2);border-radius:0 9px 9px 0;padding:10px 13px;margin-bottom:8px;font-size:12.5px}
 .zev img{max-height:56px;border-radius:5px;border:1px solid var(--line);margin-top:6px}
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
