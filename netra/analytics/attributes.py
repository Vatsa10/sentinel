"""Vision-language vehicle attributes: a description an officer can read.

Cross-camera re-identification on this grid runs on a 512-dimension appearance
vector (`reid.py`), because plates are not recoverable here - measured over
2,691 frames, more than two hundred vehicles, zero readable plates. That vector
works, but an operator cannot read it, cannot search it, and cannot testify to
it. "Candidate 4, cosine 0.87" is not something anyone can act on alone.

A vision-language model turns the same crop into words: *"black SUV, heavily
tinted windows, aftermarket alloy wheels, roof rack, dent on the rear left"*.
That is searchable in plain language, explainable in a courtroom, and
comparable across cameras without a plate.

Two halves, deliberately separated:

    Florence-2   produces free text about the crop. Probabilistic, and the only
                 part that needs a GPU or a download.
    the parser   turns that text into structured fields by exact keyword
                 matching. Deterministic, testable, and model-free - which is
                 why `python -m netra.analytics.attributes` needs neither
                 network nor GPU.

**A description is evidence for an operator, never an identification.** The
model describes what a crop looks like; it does not know what vehicle it is.
Where the caption gives no signal the field stays `unknown`/`None` rather than
being guessed, `confidence` never exceeds 0.9, and attributes may only nudge a
re-identification score that appearance already produced - never create one.

ponytail: one prompt (`<MORE_DETAILED_CAPTION>`) and a hand-written keyword
vocabulary. Florence-2 also offers `<OD>` and `<DENSE_REGION_CAPTION>`, which
would localise a roof rack or a dent rather than merely noting it, at roughly
another full generation pass per crop. The ceiling of the present approach is
whatever the caption happens to mention: an attribute the model does not say
out loud is simply absent, and the parser reports that honestly as unknown.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field, asdict

from netra import config

log = logging.getLogger(__name__)

#: The prompt Florence-2 answers with a paragraph of free text. The plain
#: `<CAPTION>` task returns a single clause ("a car on a road") that carries
#: none of the detail this module exists to extract.
CAPTION_TASK = "<MORE_DETAILED_CAPTION>"

#: Generation settings. Both are load-bearing on transformers 4.57.6 with the
#: Florence-2 remote code: beam search without `use_cache=False` raises inside
#: the model's own cache handling, and the default sdpa attention selection
#: fails because the remote module declares no `_supports_sdpa`. See
#: `AttributeExtractor.load`.
MAX_NEW_TOKENS = 96
NUM_BEAMS = 3

#: Ceiling on reported confidence. Every field here is inferred from a sentence
#: a model wrote about a night-time CCTV crop; there is no reading of this
#: evidence that deserves to be presented as near-certain.
MAX_CONFIDENCE = 0.9

BODY_TYPES = ("hatchback", "sedan", "suv", "van", "pickup", "truck", "bus",
              "auto_rickshaw", "motorcycle")

#: Caption phrase -> body type. Longest phrases are matched first (see
#: `_match_vocabulary`), so "pick-up truck" cannot be claimed by "truck" and
#: "sport utility vehicle" cannot be claimed by "vehicle".
_BODY_VOCAB = {
    "auto rickshaw": "auto_rickshaw", "auto-rickshaw": "auto_rickshaw",
    "autorickshaw": "auto_rickshaw", "tuk tuk": "auto_rickshaw",
    "tuk-tuk": "auto_rickshaw", "three wheeler": "auto_rickshaw",
    "three-wheeler": "auto_rickshaw", "rickshaw": "auto_rickshaw",
    "sport utility vehicle": "suv", "suv": "suv", "jeep": "suv",
    "pick-up truck": "pickup", "pickup truck": "pickup", "pick up truck": "pickup",
    "pickup": "pickup", "pick-up": "pickup", "ute": "pickup",
    "hatchback": "hatchback", "estate car": "hatchback",
    "sedan": "sedan", "saloon": "sedan",
    "minivan": "van", "mini-van": "van", "van": "van",
    "lorry": "truck", "tanker": "truck", "tipper": "truck",
    "semi truck": "truck", "semi-truck": "truck", "truck": "truck",
    "coach": "bus", "minibus": "bus", "bus": "bus",
    "motorcycle": "motorcycle", "motorbike": "motorcycle",
    "scooter": "motorcycle", "moped": "motorcycle",
}

#: Colour vocabulary, matching `inference.estimate_colour`'s coarse palette
#: plus the handful of words a captioner reaches for that map onto it. Street
#: lighting makes anything finer dishonest, and the two colour signals have to
#: be comparable for `reid.attribute_agreement` to compare them at all.
_COLOUR_VOCAB = {
    "white": "white", "off-white": "white", "cream": "white",
    "silver": "silver", "grey": "silver", "gray": "silver",
    "black": "black", "dark grey": "black", "dark gray": "black",
    "red": "red", "maroon": "red", "crimson": "red",
    "blue": "blue", "navy": "blue", "teal": "blue",
    "yellow": "yellow", "golden": "yellow", "gold": "yellow", "orange": "yellow",
    "green": "green", "olive": "green",
    "brown": "brown", "beige": "brown", "tan": "brown",
}

#: Phrases in which a colour word is not the vehicle's colour. Florence-2
#: routinely opens with "a black and white photograph of a street", and a
#: parser that read "black" out of that would put a black vehicle in front of
#: an operator on the strength of the image being monochrome.
_COLOUR_MASK = ("black and white", "white and black", "black-and-white")

_TINT_PHRASES = ("tinted", "tint", "blacked out window", "blacked-out window",
                 "smoked glass", "dark windows", "darkened windows")
_ALLOY_PHRASES = ("alloy", "alloys", "chrome wheel", "chrome rim",
                  "aftermarket wheel", "spoked wheel", "mag wheel")
_STOCK_WHEEL_PHRASES = ("steel wheel", "hubcap", "hub cap", "stock wheel",
                        "wheel cover", "plain wheel")
_ROOF_RACK_PHRASES = ("roof rack", "roof-rack", "luggage rack", "roof rails",
                      "roof bars", "carrier on the roof", "roof carrier")

_MARKING_PHRASES = ("sticker", "decal", "livery", "logo", "sign", "lettering",
                    "writing", "advertisement", "banner", "emblem", "graphic",
                    "text")
_DAMAGE_PHRASES = ("dent", "dented", "scratch", "scratched", "broken",
                   "cracked", "smashed", "damaged", "rust", "rusted",
                   "rusty", "missing bumper", "crumpled")

#: Words that, standing close in front of a phrase, invert it. A caption
#: saying "the windows are not tinted" must never set `tinted_windows` true -
#: an operator filtering for tinted windows would be shown the one vehicle the
#: model explicitly ruled out.
_NEGATIONS = ("no", "not", "non", "without", "lacks", "lacking", "never",
              "isn't", "aren't", "doesn't", "does not", "is not", "are not",
              "free of", "devoid of")
#: How many characters before a phrase are searched for a negation. Long enough
#: for "the windows do not appear to be tinted", short enough that a negation
#: about a different clause in the same sentence does not reach across.
_NEGATION_WINDOW = 40

#: Fields the confidence fraction is measured over. Seven, so one populated
#: field is worth ~0.13 - a caption that mentions only a colour is reported as
#: weak evidence, which is what it is.
_SCORED_FIELDS = ("body_type", "colour", "tinted_windows", "wheels",
                  "roof_rack", "markings", "damage")


@dataclass
class VehicleAttributes:
    """A readable description of one vehicle crop, with its provenance."""
    body_type: str = "unknown"
    colour: str | None = None
    tinted_windows: bool | None = None
    wheels: str = "unknown"          # stock | alloy | unknown
    roof_rack: bool | None = None
    markings: list[str] = field(default_factory=list)
    damage: list[str] = field(default_factory=list)
    description: str = ""
    raw_caption: str = ""
    model: str = ""
    confidence: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def _normalise(caption: str) -> str:
    """Lowercase with runs of whitespace collapsed, for phrase matching."""
    return re.sub(r"\s+", " ", (caption or "").lower()).strip()


def _negated(text: str, at: int) -> bool:
    """True when a negation sits in the window just before position `at`."""
    window = text[max(0, at - _NEGATION_WINDOW):at]
    # Word-boundary anchored: "nowhere" must not negate, "no" must.
    return any(re.search(rf"\b{re.escape(n)}\b", window) for n in _NEGATIONS)


def _find_phrase(text: str, phrases) -> bool | None:
    """Tri-state phrase presence: True, False if negated, None if unmentioned.

    None and False are different answers and are kept apart everywhere: "the
    caption said nothing about a roof rack" is not "the model saw no roof
    rack", and an operator ruling vehicles in or out needs to know which.
    """
    seen = False
    for phrase in phrases:
        for m in re.finditer(rf"\b{re.escape(phrase)}", text):
            seen = True
            if not _negated(text, m.start()):
                return True
    return False if seen else None


def _mask(text: str, phrases) -> str:
    """Blank out phrases, keeping length so positions elsewhere still hold."""
    for phrase in phrases:
        text = re.sub(re.escape(phrase), " " * len(phrase), text)
    return text


def _match_vocabulary(text: str, vocab: dict) -> str | None:
    """First vocabulary hit in the caption, longest phrase first.

    Earliest position wins because a captioner leads with its subject: "a black
    SUV parked behind a bus" is about the SUV. Longest-first within that stops
    a specific phrase being swallowed by a substring of itself.
    """
    best_at, best_value = None, None
    for phrase in sorted(vocab, key=len, reverse=True):
        m = re.search(rf"\b{re.escape(phrase)}\b", text)
        if m is None or _negated(text, m.start()):
            continue
        if best_at is None or m.start() < best_at:
            best_at, best_value = m.start(), vocab[phrase]
    return best_value


def _quoted_text(caption: str) -> list[str]:
    """Any lettering the model transcribed, e.g. reads 'Cafe'.

    Text on a vehicle is the single most identifying attribute available when
    the plate is not - a fleet name or a phone number narrows a search far
    harder than "white van" does.
    """
    out = []
    for m in re.finditer(r"['\"‘“]([^'\"’”]{2,40})"
                         r"['\"’”]", caption or ""):
        value = m.group(1).strip()
        if value and value.lower() not in out:
            out.append(value)
    return out


def _collect(text: str, phrases) -> list[str]:
    """Every non-negated phrase from a vocabulary that the caption mentions."""
    found = []
    for phrase in phrases:
        for m in re.finditer(rf"\b{re.escape(phrase)}", text):
            if not _negated(text, m.start()) and phrase not in found:
                found.append(phrase)
            break
    return found


def _compose(attrs: VehicleAttributes, caption: str) -> str:
    """One sentence an operator can read, built only from parsed fields."""
    parts = []
    head = " ".join(x for x in (attrs.colour,
                                attrs.body_type.replace("_", " ")
                                if attrs.body_type != "unknown" else None)
                    if x)
    if head:
        parts.append(head)
    if attrs.tinted_windows:
        parts.append("tinted windows")
    if attrs.wheels == "alloy":
        parts.append("alloy wheels")
    elif attrs.wheels == "stock":
        parts.append("stock wheels")
    if attrs.roof_rack:
        parts.append("roof rack")
    if attrs.markings:
        parts.append("markings: " + ", ".join(attrs.markings))
    if attrs.damage:
        parts.append("damage: " + ", ".join(attrs.damage))
    if parts:
        return "; ".join(parts)
    # Nothing structured survived the parse. The model's own first sentence is
    # still the most useful thing to show, and showing it verbatim keeps the
    # distinction between what was extracted and what was merely said.
    first = re.split(r"(?<=[.!?])\s", (caption or "").strip())[0].strip()
    return first


def parse_caption(caption: str, model: str = "") -> VehicleAttributes:
    """Turn a free-text caption into structured attributes. Deterministic.

    Nothing here guesses. A field the caption does not speak to comes back
    `unknown` or `None`, and `confidence` is the share of fields that the
    caption actually populated - so a bare "a car on a road" reports itself as
    almost worthless, which is the correct thing for it to do.
    """
    text = _normalise(caption)
    attrs = VehicleAttributes(raw_caption=(caption or "").strip(), model=model)
    if not text:
        return attrs

    attrs.body_type = _match_vocabulary(text, _BODY_VOCAB) or "unknown"
    attrs.colour = _match_vocabulary(_mask(text, _COLOUR_MASK), _COLOUR_VOCAB)
    attrs.tinted_windows = _find_phrase(text, _TINT_PHRASES)

    if _find_phrase(text, _ALLOY_PHRASES) is True:
        attrs.wheels = "alloy"
    elif _find_phrase(text, _STOCK_WHEEL_PHRASES) is True:
        attrs.wheels = "stock"

    attrs.roof_rack = _find_phrase(text, _ROOF_RACK_PHRASES)

    # Transcribed lettering first: it is the specific evidence, and the generic
    # word ("a sign") is only worth recording when nothing was actually read.
    attrs.markings = _quoted_text(caption) or _collect(text, _MARKING_PHRASES)
    attrs.damage = _collect(text, _DAMAGE_PHRASES)

    populated = sum(1 for f in _SCORED_FIELDS
                    if _is_populated(getattr(attrs, f)))
    attrs.confidence = round(
        min(MAX_CONFIDENCE, populated / len(_SCORED_FIELDS)), 3)
    attrs.description = _compose(attrs, caption)
    return attrs


def _is_populated(value) -> bool:
    """A field counts towards confidence only when the caption spoke to it.

    `False` counts: "the windows are not tinted" is a real observation. An
    empty list and `None` and "unknown" do not.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value not in ("", "unknown")
    if isinstance(value, list):
        return bool(value)
    return True


def unavailable(reason: str) -> VehicleAttributes:
    """What every caller gets when the model cannot run.

    Attribute extraction is enrichment. It failing must degrade to "we do not
    know" and must never be mistaken for "there is nothing to say" - hence a
    description that states the outage rather than an empty one.
    """
    return VehicleAttributes(description=f"attributes unavailable ({reason})")


class AttributeExtractor:
    """Florence-2, loaded lazily and shared through one device lock.

    Mirrors `ReIdEncoder`: the model is never loaded at import, and every
    forward pass is serialised behind `self._lock` so this cannot contend with
    itself for the 8 GB the detector, ReID and OCR already share.
    """

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or config.ATTRIBUTE_MODEL
        self._model = None
        self._processor = None
        self._lock = threading.Lock()
        #: set once a load has failed, so a broken install is not retried on
        #: every alert - the first warning is the useful one
        self._failed: str | None = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> bool:
        """Load the weights. Returns False rather than raising.

        A missing download, a torch/transformers mismatch or an out-of-memory
        GPU must cost the platform its descriptions and nothing else: detection
        is the primary duty and it does not depend on this.
        """
        if self._model is not None:
            return True
        if self._failed is not None:
            return False
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoProcessor

            cuda = str(config.DEVICE).startswith("cuda") and torch.cuda.is_available()
            dtype = torch.float16 if cuda else torch.float32
            # attn_implementation="eager" is required: Florence-2 ships as
            # remote code that predates the `_supports_sdpa` flag, and the
            # default attention selection in transformers 4.57 raises on it.
            model = AutoModelForCausalLM.from_pretrained(
                self.model_name, trust_remote_code=True,
                torch_dtype=dtype, attn_implementation="eager")
            model.eval().to(config.DEVICE if cuda else "cpu")
            self._processor = AutoProcessor.from_pretrained(
                self.model_name, trust_remote_code=True)
            self._model = model
            self._dtype = dtype
            self._device = config.DEVICE if cuda else "cpu"
            log.info("attribute extractor ready (%s, %s)",
                     self.model_name, self._device)
            return True
        except Exception as exc:
            self._failed = str(exc)
            log.warning("attribute extraction unavailable (%s) - the pipeline "
                        "runs without descriptions", exc)
            return False

    # -- inference -----------------------------------------------------------
    def describe(self, crop) -> VehicleAttributes:
        """Describe one BGR crop."""
        out = self.describe_batch([crop])
        return out[0] if out else unavailable("no crop")

    def describe_batch(self, crops: list) -> list[VehicleAttributes]:
        """Describe several crops in one generation pass.

        Batched because beam search dominates the cost: three beams over 96
        tokens is the same decode whether it runs on one crop or four.
        """
        usable = [c for c in crops
                  if c is not None and getattr(c, "size", 0) > 0]
        if not crops:
            return []
        if not usable or not self.load():
            reason = self._failed or "empty crop"
            return [unavailable(reason) for _ in crops]

        import cv2
        import torch
        from PIL import Image

        images = [Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB))
                  for c in usable]
        prompts = [CAPTION_TASK] * len(images)
        try:
            with self._lock:
                inputs = self._processor(text=prompts, images=images,
                                         return_tensors="pt")
                pixel_values = inputs["pixel_values"].to(self._device,
                                                         self._dtype)
                with torch.no_grad():
                    generated = self._model.generate(
                        input_ids=inputs["input_ids"].to(self._device),
                        pixel_values=pixel_values,
                        max_new_tokens=MAX_NEW_TOKENS, num_beams=NUM_BEAMS,
                        do_sample=False,
                        # Required: the remote code's cache handling raises on
                        # a None past-key-value during beam search.
                        use_cache=False)
                texts = self._processor.batch_decode(generated,
                                                     skip_special_tokens=False)
        except Exception:
            log.exception("attribute generation failed")
            return [unavailable("generation failed") for _ in crops]

        parsed = []
        for text, image in zip(texts, images):
            try:
                answer = self._processor.post_process_generation(
                    text, task=CAPTION_TASK,
                    image_size=(image.width, image.height))
                caption = answer.get(CAPTION_TASK, "") if isinstance(answer, dict) \
                    else str(answer)
            except Exception:
                caption = text
            parsed.append(parse_caption(caption, model=self.model_name))

        # Re-align with the caller's list so an unusable crop still gets an
        # answer in its own position rather than shifting everything after it.
        out, it = [], iter(parsed)
        for c in crops:
            out.append(next(it) if (c is not None and getattr(c, "size", 0) > 0)
                       else unavailable("empty crop"))
        return out


_EXTRACTOR: AttributeExtractor | None = None
_EXTRACTOR_LOCK = threading.Lock()


def get_extractor() -> AttributeExtractor:
    """The one shared extractor. Constructed here, loaded on first describe."""
    global _EXTRACTOR
    with _EXTRACTOR_LOCK:
        if _EXTRACTOR is None:
            _EXTRACTOR = AttributeExtractor()
        return _EXTRACTOR


def describe_image_file(path) -> VehicleAttributes:
    """Describe a crop already written to disk.

    The background worker reads evidence from disk rather than holding frames:
    a queue of decoded frames is megabytes each and would put memory pressure
    behind a feature that is allowed to be dropped.
    """
    import cv2
    img = cv2.imread(str(path))
    if img is None:
        return unavailable("evidence crop unreadable")
    return get_extractor().describe(img)


# -- self-check --------------------------------------------------------------

def _self_check() -> None:
    """Parser only: no model, no GPU, no network.

    The generation half is verified separately against real grid crops; what
    is worth pinning down here is that the caption is never over-read, because
    an invented attribute is how a description stops being evidence.
    """
    rich = ("The image shows a black SUV parked on the side of a street. The "
            "vehicle has heavily tinted windows and aftermarket alloy wheels, "
            "with a roof rack on top. There is a large dent on the rear left "
            "door and a sticker on the rear window that reads 'Om Travels'.")
    a = parse_caption(rich, model="test")
    assert a.body_type == "suv", a
    assert a.colour == "black", a
    assert a.tinted_windows is True, a
    assert a.wheels == "alloy", a
    assert a.roof_rack is True, a
    assert "dent" in a.damage, a
    assert "Om Travels" in a.markings, a
    assert a.model == "test" and a.raw_caption.startswith("The image shows")
    assert 0.0 < a.confidence <= MAX_CONFIDENCE, a.confidence
    assert "black suv" in a.description.lower(), a.description

    # A bare caption must yield unknowns, not plausible-sounding defaults.
    bare = parse_caption("a car on a road at night")
    assert bare.body_type == "unknown", bare
    assert bare.colour is None and bare.tinted_windows is None, bare
    assert bare.wheels == "unknown" and bare.roof_rack is None, bare
    assert bare.markings == [] and bare.damage == [], bare
    assert bare.confidence == 0.0, bare.confidence
    # ...and with nothing structured to say, it shows the model's own words.
    assert bare.description == "a car on a road at night", bare.description

    # An empty caption is not an observation.
    empty = parse_caption("")
    assert empty.confidence == 0.0 and empty.description == "", empty
    assert parse_caption(None).body_type == "unknown"

    # Negation must never set the positive - this is the failure that would
    # put the one explicitly-excluded vehicle in front of an operator.
    for phrase, attr in (("the windows are not tinted", "tinted_windows"),
                         ("there is no roof rack on the vehicle", "roof_rack")):
        got = parse_caption(f"a white van; {phrase}")
        assert getattr(got, attr) is False, (phrase, got)
    assert parse_caption("a van with no alloy wheels").wheels == "unknown"
    # False is still an observation and still counts as evidence.
    assert parse_caption("a van with no roof rack").confidence > 0

    # Confidence is capped even when every field is populated.
    everything = parse_caption(
        "a red sedan with tinted windows, alloy wheels, a roof rack, a "
        "sticker on the boot and a deep scratch along the door")
    assert everything.confidence <= MAX_CONFIDENCE, everything.confidence

    # Longest-phrase-first: the specific body type must win over its substring.
    assert parse_caption("a white pick-up truck").body_type == "pickup"
    assert parse_caption("a yellow auto rickshaw").body_type == "auto_rickshaw"
    assert parse_caption("a sport utility vehicle").body_type == "suv"
    # Earliest mention wins: a captioner leads with its subject.
    assert parse_caption("a black sedan parked behind a bus").body_type == "sedan"
    # Colour synonyms fold onto the coarse palette the pipeline already uses.
    assert parse_caption("a grey hatchback").colour == "silver"
    assert parse_caption("a navy motorbike").colour == "blue"
    assert parse_caption("a navy motorbike").body_type == "motorcycle"

    # Every body type the dataclass documents must be reachable from the
    # vocabulary, or the field promises a value nothing can ever produce.
    reachable = set(_BODY_VOCAB.values())
    assert reachable == set(BODY_TYPES), reachable ^ set(BODY_TYPES)

    # Florence-2 opens a great many night captions with "a black and white
    # photograph of...". That is the image, not the vehicle.
    mono = parse_caption("The image is a black and white photograph of a "
                         "street at night with a sedan on it.")
    assert mono.colour is None, mono
    assert mono.body_type == "sedan", mono
    # ...but a real black vehicle in the same caption is still read.
    both = parse_caption("a black and white photograph showing a red truck")
    assert both.colour == "red", both

    # A real Florence-2 caption from cam13, verbatim.
    real = parse_caption(
        "The image shows a truck parked on the side of a street. The truck is "
        "orange and white in color and has a sign on the front that reads "
        "'Cafe'.")
    assert real.body_type == "truck", real
    assert real.colour in ("yellow", "white"), real
    assert real.markings == ["Cafe"], real
    assert real.confidence > 0, real

    # The outage path is a real answer, not an exception.
    down = unavailable("model not installed")
    assert down.body_type == "unknown" and down.confidence == 0.0
    assert "unavailable" in down.description

    # Serialisation, because the API and the database both round-trip this.
    d = a.as_dict()
    assert d["body_type"] == "suv" and isinstance(d["markings"], list), d

    print("attributes self-check passed")


if __name__ == "__main__":
    _self_check()
