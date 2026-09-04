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

    print("matching self-check passed")


if __name__ == "__main__":
    _self_check()
