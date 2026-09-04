"""Watchlist matching.

Plate text alone is not a viable matching strategy on this grid: most cameras
are wide-area night overviews where a plate occupies a few dozen pixels. So a
detection is scored against a watchlist entry on four independent signals and
the results are fused:

  exact plate      - decisive when OCR actually resolved the whole plate
  partial plate    - a 4-of-10 character recovery is a strong constraint,
                     not a failure, and is scored on how much was recovered
  appearance       - vehicle class and colour, which survive poor plate optics
  space-time       - two sightings too far apart for the elapsed time cannot
                     be the same vehicle, whatever the other signals say

Space-time acts as a veto rather than a contributor: it can only reduce
confidence, never manufacture it.

Every alert carries the per-signal breakdown so an operator can see why the
system believes what it believes, and overrule it.
"""
from __future__ import annotations

from dataclasses import dataclass

# Characters that OCR routinely confuses on Indian plates, especially at night.
CONFUSIONS = {
    "O": "0", "Q": "0", "D": "0",
    "I": "1", "L": "1",
    "S": "5",
    "B": "8",
    "Z": "2",
    "G": "6",
    "T": "7",
}

# Weight of each signal in the fused score. Plate evidence dominates when it
# exists; appearance carries the cameras where it does not.
WEIGHTS = {"plate": 0.60, "appearance": 0.40}

ALERT_THRESHOLD = 0.55


def normalise_plate(text: str | None) -> str:
    """Uppercase, strip separators, and fold known OCR confusions."""
    if not text:
        return ""
    cleaned = "".join(ch for ch in text.upper() if ch.isalnum())
    return "".join(CONFUSIONS.get(ch, ch) for ch in cleaned)


def plate_similarity(observed: str, target: str) -> tuple[float, str]:
    """Score an observed plate against a watchlist plate.

    Returns (score in 0..1, explanation). Comparison happens after confusion
    folding, so "GJ01AB1Z34" read for "GJ01AB1234" is treated as a match rather
    than a miss.
    """
    obs, tgt = normalise_plate(observed), normalise_plate(target)
    if not obs or not tgt:
        return 0.0, "no plate text recovered"

    if obs == tgt:
        # Report the watchlist plate as written, not the confusion-folded form:
        # an operator reading "exact match (6J01A81234)" for GJ01AB1234 would
        # reasonably think the system had matched the wrong vehicle.
        return 1.0, f"exact plate match ({target})"

    # Substring: a partial read that is wholly contained in the target.
    if len(obs) >= 4 and obs in tgt:
        frac = len(obs) / len(tgt)
        return 0.55 + 0.35 * frac, f"partial plate '{obs}' within '{tgt}'"

    # Positional agreement over the overlapping length.
    span = min(len(obs), len(tgt))
    if span < 4:
        return 0.0, "too few characters recovered to constrain"
    agree = sum(1 for a, b in zip(obs, tgt) if a == b)
    frac = agree / span
    if frac < 0.6:
        return 0.0, f"plate mismatch ({agree}/{span} characters)"
    # 60% agreement scores 0; full agreement over a short read approaches 0.9.
    score = 0.9 * (frac - 0.6) / 0.4
    return score, f"{agree}/{span} characters agree ({obs} vs {tgt})"


# --- watchlist prefilter -----------------------------------------------------
# `score_match` is cheap, but a watchlist of 10,000 entries scored against
# every detection at thousands of detections per minute is tens of millions of
# comparisons per minute, on the thread that also has to persist detections.
# The prefilter's only job is to drop entries that cannot possibly alert, so
# full scoring still decides every candidate.
#
# The hard constraint is that it must never drop a pair `score_match` would
# alert on: a watchlist hit lost inside an optimisation is the worst failure
# this platform can have, and unlike a slow scan nothing would ever reveal it.
# So the window size is derived rather than chosen. `plate_similarity` has
# three branches, and the awkward one is positional agreement: two plates can
# agree on 8 of 10 characters with the two mismatches placed so that they share
# no long common run at all. Indexing on four-character windows loses those -
# measured at 6.1% of alerting two-error reads.
#
# Derivation. An alert needs `fused >= ALERT_THRESHOLD`; appearance can
# contribute at most `WEIGHTS["appearance"]`, so the plate score must reach
# `p_min`, which fixes the minimum positional agreement `_MIN_ALERTING_FRAC`.
# Over a span of n characters that allows at most m mismatches, which cut the
# agreeing characters into at most m+1 runs, so the longest shared run is at
# least ceil((n-m)/(m+1)). A shared q-character window therefore exists
# whenever n > q*m + q - 1. Evaluated over every span, q=2 is safe everywhere
# and q=3 is safe except for spans of 4, 5, 7, 8, 11 and 14 - both facts are
# asserted in the self-check rather than trusted.
INDEX_WINDOW = 3
#: Provably complete for every span, but far coarser: on a watchlist of
#: Gujarat plates every entry shares the "GJ" bucket, so this degenerates to a
#: full scan. Used only for the spans where INDEX_WINDOW is not safe.
FALLBACK_WINDOW = 2
#: Below this a read cannot score at all except against an equally short entry
#: (`plate_similarity` refuses spans under 4), so those entries are held apart.
MIN_SCORABLE_CHARS = 4


def _min_alerting_fraction() -> float:
    """Least positional agreement that could still clear ALERT_THRESHOLD.

    Derived from the scoring constants rather than written down, so retuning
    the weights or the threshold cannot silently invalidate the index. The 0.6
    and 0.9/0.4 are `plate_similarity`'s own positional curve.
    """
    p_min = max(0.0, (ALERT_THRESHOLD - WEIGHTS["appearance"]) / WEIGHTS["plate"])
    if p_min >= 0.9:
        return 1.0  # no positional score could reach the threshold at all
    return 0.6 + 0.4 * p_min / 0.9


_MIN_ALERTING_FRAC = _min_alerting_fraction()
_PRIMARY_SAFE: dict[int, bool] = {}


def window_guaranteed(span: int, window: int) -> bool:
    """Must two plates alerting positionally over `span` share a window?"""
    import math
    if span < window:
        return False
    agree = math.ceil(_MIN_ALERTING_FRAC * span - 1e-9)
    mismatches = span - agree
    return span > window * mismatches + window - 1


def _primary_safe(length: int) -> bool:
    """Cached: consulted once per entry at build and once per lookup."""
    cached = _PRIMARY_SAFE.get(length)
    if cached is None:
        cached = _PRIMARY_SAFE[length] = window_guaranteed(length, INDEX_WINDOW)
    return cached


def plate_windows(plate: str | None, window: int = INDEX_WINDOW) -> set[str]:
    """Every `window`-character window of the confusion-folded plate.

    Windows, not a prefix: `plate_similarity` matches an observed read that is
    a *substring* of the watchlist plate, so "AB1234" must find "GJ01AB1234"
    even though their first characters have nothing in common. Folding first is
    equally load-bearing - comparison happens on folded text, so an index built
    on raw text would miss every OCR confusion the matcher exists to absorb.
    """
    folded = normalise_plate(plate)
    if len(folded) < window:
        return set()
    return {folded[i:i + window] for i in range(len(folded) - window + 1)}


class WatchlistIndex:
    """Entries bucketed by the windows of their plate, for candidate lookup.

    Built once per watchlist reload and thrown away with it; nothing here is
    incremental, because a rebuild over 10,000 entries is a few milliseconds
    every thirty seconds.

    Both window sizes are indexed up front. The fallback is needed only for
    short reads, but building it lazily would mean building it on the inference
    thread at the moment a short read arrives, which is exactly when there is
    no time for it.

    ponytail: the fallback index is a full scan in disguise on a watchlist of
    same-region plates, because every Gujarat plate shares the "GJ" bucket. A
    read of 4, 5, 7 or 8 characters therefore costs what the prefilter was
    written to avoid. That is deliberate - those are the spans where a
    3-character window is not provably complete, and a slow correct answer
    beats a fast one that loses a watchlist hit. The ceiling is that a grid
    producing mostly short partial reads gets little benefit from any of this.
    """

    def __init__(self, entries: list[dict] | None = None):
        self.entries: list[dict] = list(entries or [])
        self._buckets: dict[str, list[dict]] = {}
        self._fallback: dict[str, list[dict]] = {}
        #: entries too short for any window to constrain
        self._short: list[dict] = []
        #: entries whose own length makes the primary window unsafe whatever
        #: the read is, because the span is capped by the shorter of the two
        self._forced: list[dict] = []
        for entry in self.entries:
            folded = normalise_plate(entry.get("plate"))
            if len(folded) < MIN_SCORABLE_CHARS:
                self._short.append(entry)
                continue
            if not _primary_safe(len(folded)):
                self._forced.append(entry)
            for key in plate_windows(folded, INDEX_WINDOW):
                self._buckets.setdefault(key, []).append(entry)
            for key in plate_windows(folded, FALLBACK_WINDOW):
                self._fallback.setdefault(key, []).append(entry)

    @staticmethod
    def _gather(buckets: dict, windows: set[str], extra: list) -> list[dict]:
        seen: set[int] = set()
        out: list[dict] = []
        for source in [buckets.get(k, ()) for k in windows] + [extra]:
            for entry in source:
                marker = id(entry)
                if marker not in seen:
                    seen.add(marker)
                    out.append(entry)
        return out

    def candidates(self, plate_text: str | None) -> list[dict]:
        """Entries worth scoring against this observed plate.

        A superset of everything `score_match` could alert on. Order is stable
        so alert ordering does not change with the prefilter's internals.
        """
        folded = normalise_plate(plate_text)
        if len(folded) < MIN_SCORABLE_CHARS:
            # Nothing this short can score against a longer plate; only an
            # equally short entry could match it, and only exactly.
            return list(self._short)

        if _primary_safe(len(folded)):
            return self._gather(self._buckets,
                                plate_windows(folded, INDEX_WINDOW),
                                self._short + self._forced)
        # This read's span is one the primary window cannot prove. Fall back to
        # the window that is complete for every span, and pay for it.
        return self._gather(self._fallback,
                            plate_windows(folded, FALLBACK_WINDOW),
                            self._short)

    def stats(self) -> dict:
        return {"entries": len(self.entries),
                "buckets": len(self._buckets),
                "window": INDEX_WINDOW,
                "unindexed": len(self._short) + len(self._forced),
                "largest_bucket": max((len(v) for v in self._buckets.values()),
                                      default=0)}


def appearance_similarity(det_class: str | None, det_colour: str | None,
                          wl_class: str | None, wl_colour: str | None) -> tuple[float, str]:
    """Score vehicle class and colour agreement.

    Deliberately coarse. Colour under sodium and LED street lighting is not
    reliable enough to carry more weight than this, and pretending otherwise
    would manufacture false confidence.
    """
    parts, score, possible = [], 0.0, 0.0

    if wl_class:
        possible += 1.0
        if det_class and det_class == wl_class:
            score += 1.0
            parts.append(f"class matches ({det_class})")
        else:
            parts.append(f"class differs ({det_class or 'unknown'} vs {wl_class})")

    if wl_colour:
        possible += 1.0
        if det_colour and det_colour == wl_colour:
            score += 1.0
            parts.append(f"colour matches ({det_colour})")
        else:
            parts.append(f"colour differs ({det_colour or 'unknown'} vs {wl_colour})")

    if possible == 0:
        return 0.0, "watchlist entry carries no appearance attributes"
    return score / possible, "; ".join(parts)


# Fastest speed we will credit a vehicle with, in km/h. Two sightings that
# would require exceeding this are physically impossible and get vetoed.
MAX_PLAUSIBLE_KMH = 120.0


def spacetime_plausible(distance_km: float, elapsed_s: float) -> tuple[bool, str]:
    """Could one vehicle have covered this distance in this time?"""
    if elapsed_s <= 0:
        return False, "sightings are simultaneous or out of order"
    implied_kmh = distance_km / (elapsed_s / 3600.0)
    if implied_kmh > MAX_PLAUSIBLE_KMH:
        return False, (f"implies {implied_kmh:.0f} km/h over {distance_km:.1f} km "
                       f"in {elapsed_s:.0f}s - not physically possible")
    return True, f"implies {implied_kmh:.0f} km/h over {distance_km:.1f} km"


@dataclass
class MatchResult:
    score: float
    match_type: str
    reasons: dict
    is_alert: bool


def score_match(detection: dict, entry: dict) -> MatchResult:
    """Fuse all available signals into one decision, preserving the reasoning.

    `detection` needs plate_text, plate_chars, vehicle_class, colour.
    `entry` needs plate, and optionally vehicle_class, vehicle_colour.
    """
    reasons: dict = {}

    p_score, p_why = plate_similarity(detection.get("plate_text"), entry.get("plate"))
    reasons["plate"] = {"score": round(p_score, 3), "detail": p_why}

    a_score, a_why = appearance_similarity(
        detection.get("vehicle_class"), detection.get("colour"),
        entry.get("vehicle_class"), entry.get("vehicle_colour"))
    reasons["appearance"] = {"score": round(a_score, 3), "detail": a_why}

    fused = WEIGHTS["plate"] * p_score + WEIGHTS["appearance"] * a_score

    # A registration number identifies a vehicle; colour and class do not.
    # Without this floor, a fully matched plate scores only 0.60 whenever the
    # appearance attributes disagree - and on wide night cameras they routinely
    # do, because colour estimation there is unreliable. Weak appearance
    # evidence must not be allowed to argue down decisive plate evidence.
    if p_score >= 0.999:
        fused = max(fused, 0.95)
        if a_score < 0.5:
            reasons["policy"] = {
                "score": 1.0,
                "detail": ("plate matched exactly; appearance attributes "
                           "disagree but do not override a registration number"),
            }

    # The converse: appearance alone must never raise an alert. Two silver
    # hatchbacks are not evidence; without plate support this is a lead.
    if p_score == 0.0:
        fused = min(fused, 0.35)
        reasons["policy"] = {
            "score": 0.0,
            "detail": "no plate evidence - appearance alone cannot raise an alert",
        }

    if p_score >= 0.999:
        match_type = "exact"
    elif p_score > 0:
        match_type = "partial" if a_score == 0 else "fused"
    else:
        match_type = "appearance"

    return MatchResult(score=round(fused, 3), match_type=match_type,
                       reasons=reasons, is_alert=fused >= ALERT_THRESHOLD)


def _self_check() -> None:
    """Runnable check on the logic that decides whether police get an alert."""
    # Exact match, allowing for OCR confusion folding.
    r = score_match({"plate_text": "GJ01AB1234", "vehicle_class": "car"},
                    {"plate": "GJ01AB1234"})
    assert r.match_type == "exact" and r.is_alert, r
    # The reason must name the plate as written, not the folded form.
    assert "GJ01AB1234" in r.reasons["plate"]["detail"], r.reasons

    # An exact plate match must stay decisive even when appearance disagrees.
    # Observed live: OCR read the plate correctly while colour and class were
    # misjudged on a night camera, scoring the match down to 0.60.
    r = score_match({"plate_text": "GJ01AB1234", "vehicle_class": "truck",
                     "colour": "green"},
                    {"plate": "GJ01AB1234", "vehicle_class": "car",
                     "vehicle_colour": "silver"})
    assert r.score >= 0.95, r
    assert r.is_alert, r

    r = score_match({"plate_text": "GJ0IAB12E4".replace("E", "3"), "vehicle_class": "car"},
                    {"plate": "GJ01AB1234"})
    assert r.score > 0.5, r  # I->1 folding must not break the match

    # Partial read: fewer characters, still a real constraint.
    r = score_match({"plate_text": "AB1234", "vehicle_class": "car"},
                    {"plate": "GJ01AB1234"})
    assert r.reasons["plate"]["score"] > 0.5, r

    # Too little recovered to mean anything.
    r = score_match({"plate_text": "G1", "vehicle_class": "car"},
                    {"plate": "GJ01AB1234"})
    assert r.reasons["plate"]["score"] == 0.0 and not r.is_alert, r

    # Appearance alone must never alert, however well it agrees.
    r = score_match({"plate_text": None, "vehicle_class": "car", "colour": "white"},
                    {"plate": "GJ01AB1234", "vehicle_class": "car",
                     "vehicle_colour": "white"})
    assert not r.is_alert, r
    assert "policy" in r.reasons, r

    # A wrong plate is not rescued by matching appearance.
    r = score_match({"plate_text": "MH12XY9999", "vehicle_class": "car",
                     "colour": "white"},
                    {"plate": "GJ01AB1234", "vehicle_class": "car",
                     "vehicle_colour": "white"})
    assert not r.is_alert, r

    # Space-time veto.
    ok, why = spacetime_plausible(distance_km=300.0, elapsed_s=60)
    assert not ok, why
    ok, why = spacetime_plausible(distance_km=2.0, elapsed_s=180)
    assert ok, why

    # --- watchlist prefilter ------------------------------------------------
    # The window size is derived from the scoring constants, so check the
    # derivation itself before checking anything built on it.
    assert abs(_MIN_ALERTING_FRAC - 0.7111111) < 1e-6, _MIN_ALERTING_FRAC
    unsafe = [n for n in range(4, 200) if not window_guaranteed(n, INDEX_WINDOW)]
    assert unsafe == [4, 5, 7, 8, 11, 14], unsafe
    assert all(window_guaranteed(n, FALLBACK_WINDOW) for n in range(4, 200))
    # And the four-character window the first cut of this used is unsafe at
    # every span, which is why it lost alerts.
    assert not any(window_guaranteed(n, 4) for n in range(4, 200))

    entries = [{"id": 1, "plate": "GJ01AB1234"},
               {"id": 2, "plate": "MH12XY9999"},
               {"id": 3, "plate": "GJ18CD5678"},
               {"id": 4, "plate": "XY9"}]          # too short to index
    index = WatchlistIndex(entries)

    # A partial read that full scoring matches must survive the prefilter. A
    # naive first-characters index would bucket entry 1 under "GJ0" and never
    # consider it for "AB1234", silently losing an alert.
    got = {e["id"] for e in index.candidates("AB1234")}
    assert 1 in got, got
    assert score_match({"plate_text": "AB1234"}, entries[0]).reasons["plate"]["score"] > 0.5

    # Confusion folding happens before bucketing, so an OCR read of "AB1Z34"
    # still finds the entry written "AB1234".
    assert 1 in {e["id"] for e in index.candidates("GJ0IAB1Z34")}

    # The two-error positional reads that the four-character window dropped.
    for observed in ("GJW1ABW234", "GJ0WABW234", "GJ0WAB1W34"):
        assert 1 in {e["id"] for e in index.candidates(observed)}, observed

    # A read too short to score at all sees only the short bucket.
    assert {e["id"] for e in index.candidates("G1")} == {4}

    # It must still actually filter, or it is a scan with extra steps.
    got = {e["id"] for e in index.candidates("GJ01AB1234")}
    assert 2 not in got, got

    # --- brute force: every alerting two-error read must be returned ---------
    # Not a hand-picked example. Every position pair, every substitution, over
    # a watchlist large enough that the buckets are doing real work. Appearance
    # attributes agree, which is the case that lets a weak positional plate
    # score reach the threshold - and the case the first cut got wrong.
    import itertools
    import random

    rng = random.Random(11)
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    plates = set()
    while len(plates) < 300:
        plates.add(f"GJ{rng.randint(1, 38):02d}{rng.choice(letters)}"
                   f"{rng.choice(letters)}{rng.randint(0, 9999):04d}")
    corpus = [{"id": i, "plate": p, "vehicle_class": "car",
               "vehicle_colour": "white"} for i, p in enumerate(sorted(plates))]
    # A short entry and an awkward-length one, so the short and forced buckets
    # are exercised by the sweep rather than only by the cases above.
    corpus.append({"id": -1, "plate": "GJ1", "vehicle_class": "car",
                   "vehicle_colour": "white"})
    corpus.append({"id": -2, "plate": "GJ01AB12", "vehicle_class": "car",
                   "vehicle_colour": "white"})
    big = WatchlistIndex(corpus)
    observed_base = {"vehicle_class": "car", "colour": "white"}

    def _variants(plate: str, errors: int):
        folded = normalise_plate(plate)
        for positions in itertools.combinations(range(len(folded)), errors):
            for subs in itertools.product("WV5X", repeat=errors):
                candidate = list(folded)
                if any(candidate[i] == c for i, c in zip(positions, subs)):
                    continue
                for i, c in zip(positions, subs):
                    candidate[i] = c
                yield "".join(candidate)

    # Bounded so the check stays a few seconds: the full one- and two-error
    # spaces of one plate, plus a sample of the three-error space, each scored
    # against every entry. That is the space the four-character window lost
    # alerts in.
    sweep = list(_variants(corpus[0]["plate"], 1))
    sweep += list(_variants(corpus[0]["plate"], 2))
    three = list(_variants(corpus[0]["plate"], 3))
    sweep += rng.sample(three, 150)
    sweep += list(_variants(corpus[-1]["plate"], 2))

    alerting = lookups = 0
    for observed in sweep:
        keep = {id(e) for e in big.candidates(observed)}
        lookups += 1
        for entry in corpus:
            if score_match({**observed_base, "plate_text": observed},
                           entry).is_alert:
                alerting += 1
                # The whole point of the prefilter, asserted directly: zero
                # losses, not "few".
                assert id(entry) in keep, (observed, entry["plate"])
    assert alerting > 300, alerting          # the sweep must reach real alerts
    assert lookups > 900, lookups
    # Partial reads longer than the truncated variants also matter: a
    # contiguous substring is the other way OCR degrades on this grid.
    for target in corpus[:40]:
        folded = normalise_plate(target["plate"])
        for begin in range(0, len(folded) - 4):
            observed = folded[begin:begin + rng.randint(4, len(folded) - begin)]
            keep = {id(e) for e in big.candidates(observed)}
            for entry in corpus:
                if score_match({**observed_base, "plate_text": observed},
                               entry).is_alert:
                    assert id(entry) in keep, (observed, entry["plate"])

    print("matching self-check passed")


if __name__ == "__main__":
    _self_check()
