"""Cloned- and forged-plate detection.

The same registration number appearing in two places too far apart for one
vehicle to have travelled is not a tracking failure - it is evidence that two
vehicles are wearing the same plate. Plate cloning is a named offence and the
detection falls straight out of the space-time feasibility check already used
to veto impossible hops in `route.py`; here the same arithmetic is inverted and
reported as a finding in its own right.

Three constraints keep the finding honest:

  * Only sightings within one recording session are ever compared. The Sentinel
    sandbox holds several independently recorded feeds, so a timestamp from one
    session says nothing about a timestamp from another. Comparing across them
    would manufacture an accusation out of a clock offset - the single most
    dangerous failure mode here.
  * Two sightings on the same camera are never a clone. That is one vehicle
    seen twice, whatever the interval.
  * Confidence is capped below certainty. Every finding rests on OCR output
    from wide-area night cameras, and OCR misreads plates into each other. The
    `reason` field carries the arithmetic so an officer can check the claim
    rather than take it.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime

from netra.analytics.matching import (MAX_PLAUSIBLE_KMH, normalise_plate,
                                      spacetime_plausible)
from netra.core.geo import haversine_km, time_group
from netra.core.timing import sighting_time

# Plate confidence assumed when a detection carries none. Deliberately middling:
# an unscored read must neither inflate nor destroy a finding.
DEFAULT_PLATE_CONF = 0.5

# A finding can never be certain - see the module docstring.
MAX_CONFIDENCE = 0.99


@dataclass
class CloneFinding:
    plate: str
    sighting_a: dict
    sighting_b: dict
    distance_km: float
    elapsed_s: float
    implied_kmh: float | None
    confidence: float
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _sighting_dict(det) -> dict:
    cam = getattr(det, "camera", None)
    at = sighting_time(det)
    return {
        "detection_id": det.id,
        "camera_id": det.camera_id,
        "camera_name": cam.name if cam else det.camera_id,
        "lat": cam.lat if cam else None,
        "lon": cam.lon if cam else None,
        "at": at.isoformat() if isinstance(at, datetime) else at,
    }


def _confidence(implied_kmh: float | None, conf_a: float, conf_b: float) -> float:
    """How strongly this pair argues that two vehicles share one plate.

    Two independent factors:

      * How badly the pair violates plausibility. A pair implying 200 km/h is
        weak - a motorway run, a slightly wrong timestamp or an approximate
        camera coordinate could all produce it. One implying 5,000 km/h has no
        innocent explanation. Scored as 1 - (limit / implied), which approaches
        1 as the implied speed runs away and is near 0 just over the limit.
      * How well the plate was read at both ends. The weaker read governs: a
        confident read paired with a guess is still a guess.
    """
    if implied_kmh is None or implied_kmh <= MAX_PLAUSIBLE_KMH:
        # Simultaneous sightings at separated cameras imply infinite speed.
        violation = 1.0 if implied_kmh is None else 0.0
    else:
        violation = 1.0 - (MAX_PLAUSIBLE_KMH / implied_kmh)

    weakest = min(conf_a, conf_b)
    # Plate quality can halve the score but never zero it: even a poor read of
    # the same string in two impossible places is worth an officer's attention.
    return round(min(MAX_CONFIDENCE, violation * (0.5 + 0.5 * weakest)), 3)


def find_clones(detections: list, min_confidence: float = 0.6) -> list[CloneFinding]:
    """Report registration numbers seen in physically incompatible places.

    `detections` are ORM Detection rows with `.camera` loaded.

    ponytail: consecutive pairs only, after ordering by time. A clone active
    across three cameras is reported as its adjacent impossible hops rather
    than as one multi-camera cluster; the ceiling is that the officer reads two
    findings instead of one, not that anything is missed.
    """
    groups: dict[str, list] = {}
    for det in detections:
        plate = normalise_plate(det.plate_text)
        # A partial read cannot identify a vehicle, so it cannot evidence a
        # clone either: "AB12" is shared by thousands of legitimate plates.
        if len(plate) < 6:
            continue
        if sighting_time(det) is None:
            continue
        groups.setdefault(plate, []).append(det)

    findings: list[CloneFinding] = []
    for plate, dets in groups.items():
        if len(dets) < 2:
            continue
        dets.sort(key=sighting_time)

        for prev, cur in zip(dets, dets[1:]):
            # One vehicle passing the same camera twice is not a clone.
            if prev.camera_id == cur.camera_id:
                continue

            # Different recording sessions are not simultaneous in reality.
            # Unknown group (None) is also not comparable - we cannot show the
            # two clocks agree, so we must not claim the speed between them.
            group = time_group(prev.camera_id)
            if group is None or group != time_group(cur.camera_id):
                continue

            cam_a, cam_b = getattr(prev, "camera", None), getattr(cur, "camera", None)
            coords = (getattr(cam_a, "lat", None), getattr(cam_a, "lon", None),
                      getattr(cam_b, "lat", None), getattr(cam_b, "lon", None))
            if None in coords:
                # Without both positions there is no distance and therefore no
                # impossibility to assert.
                continue

            km = haversine_km(*coords)
            secs = (sighting_time(cur) - sighting_time(prev)).total_seconds()
            ok, why = spacetime_plausible(km, secs)
            if ok:
                continue
            if km <= 0.0:
                # Co-located cameras: no distance was covered, so no speed is
                # implied however close together the sightings fall.
                continue

            # Report the plate as OCR actually read it, not the confusion-folded
            # key: an officer shown "6J01A81234" would reasonably think the
            # system had flagged a different vehicle entirely.
            read_a = (prev.plate_text or plate).upper()
            read_b = (cur.plate_text or plate).upper()
            shown = read_a if read_a == read_b else f"{read_a} / {read_b}"

            implied = km / (secs / 3600.0) if secs > 0 else None
            conf = _confidence(implied,
                               prev.plate_conf if prev.plate_conf is not None else DEFAULT_PLATE_CONF,
                               cur.plate_conf if cur.plate_conf is not None else DEFAULT_PLATE_CONF)
            if conf < min_confidence:
                continue

            a, b = _sighting_dict(prev), _sighting_dict(cur)
            if implied is None:
                arithmetic = (f"{shown} was recorded at {a['camera_name']} and "
                              f"{b['camera_name']}, {km:.1f} km apart, with no "
                              f"time between the two sightings")
            else:
                arithmetic = (f"{shown} was recorded at {a['camera_name']} and "
                              f"{b['camera_name']}, {km:.1f} km apart, "
                              f"{secs:.0f}s apart - implying {implied:.0f} km/h "
                              f"against a {MAX_PLAUSIBLE_KMH:.0f} km/h ceiling")
            reason = (f"{arithmetic}. Both cameras share the {group} recording "
                      f"session, so the timestamps are comparable. One vehicle "
                      f"cannot have made this journey, so the plate is likely "
                      f"cloned or forged. Plate reads scored "
                      f"{prev.plate_conf if prev.plate_conf is not None else 'unscored'} "
                      f"and {cur.plate_conf if cur.plate_conf is not None else 'unscored'}; "
                      f"verify against the evidence images before acting.")
            if read_a != read_b:
                reason += (f" The two reads differ by characters OCR is known to "
                           f"confuse and were treated as the same plate.")

            findings.append(CloneFinding(
                plate=shown, sighting_a=a, sighting_b=b,
                distance_km=round(km, 2), elapsed_s=round(secs, 1),
                implied_kmh=round(implied, 1) if implied is not None else None,
                confidence=conf, reason=reason))

    findings.sort(key=lambda f: -f.confidence)
    return findings


def _self_check() -> None:
    """A clone finding is an accusation, so every guard here protects someone."""
    from datetime import timedelta, timezone

    class FakeCam:
        def __init__(self, cid, name, lat, lon):
            self.id, self.name, self.lat, self.lon = cid, name, lat, lon

    class FakeDet:
        _next = [1]

        def __init__(self, cam, plate, at, conf=0.9):
            self.camera, self.camera_id = cam, cam.id
            self.plate_text, self.plate_conf = plate, conf
            self.evidence_path = None
            self.scene_time, self.wall_time = at, at
            self.id = FakeDet._next[0]
            FakeDet._next[0] += 1

    t0 = datetime(2026, 6, 13, 23, 20, tzinfo=timezone.utc)
    c04 = FakeCam("cam04", "Paldi Circle", 23.0130, 72.5620)
    c14 = FakeCam("cam14", "Delight RLVD", 23.0290, 72.5700)
    c15 = FakeCam("cam15", "Vasna", 23.0180, 72.5300)
    c10 = FakeCam("cam10", "Char Chowk", 21.5220, 70.4570)   # other session
    c99 = FakeCam("cam99", "Unlisted", 23.0000, 72.5000)     # no time group

    # Impossible pair: ~1.9 km in two seconds is flagged.
    out = find_clones([FakeDet(c04, "GJ01AB1234", t0),
                       FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=2))])
    assert len(out) == 1, out
    assert out[0].plate == "GJ01AB1234" and out[0].confidence <= MAX_CONFIDENCE, out[0]
    assert "km/h" in out[0].reason and "2.0 km" in out[0].reason, out[0].reason

    # A plausible pair is not a clone.
    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
                        FakeDet(c14, "GJ01AB1234", t0 + timedelta(minutes=3))]) == []

    # Same camera seconds apart: one vehicle seen twice, never a clone.
    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
                        FakeDet(c04, "GJ01AB1234", t0 + timedelta(seconds=1))]) == []

    # Different recording sessions must never be compared, however impossible
    # the arithmetic would look. This is the constraint that stops the platform
    # accusing an innocent vehicle on the strength of a clock offset.
    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
                        FakeDet(c10, "GJ01AB1234", t0 + timedelta(seconds=5))]) == []
    # A camera in no known session is equally incomparable.
    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
                        FakeDet(c99, "GJ01AB1234", t0 + timedelta(seconds=5))]) == []

    # A single sighting yields nothing.
    assert find_clones([FakeDet(c04, "GJ01AB1234", t0)]) == []

    # Confidence ordering: the worse violation must score higher.
    mild = find_clones([FakeDet(c04, "GJ01AB1234", t0),
                        FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=40))],
                       min_confidence=0.0)
    severe = find_clones([FakeDet(c04, "GJ01AB1234", t0),
                          FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=1))],
                         min_confidence=0.0)
    assert mild and severe, (mild, severe)
    assert severe[0].confidence > mild[0].confidence, (severe[0], mild[0])
    # ...and the mild one is weak enough that the default threshold hides it.
    assert mild[0].confidence < 0.6, mild[0]

    # A weaker plate read must not score as highly as a confident one.
    weak = find_clones([FakeDet(c04, "GJ01AB1234", t0, conf=0.3),
                        FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=1), conf=0.3)],
                       min_confidence=0.0)
    assert weak[0].confidence < severe[0].confidence, (weak[0], severe[0])

    # Missing coordinates must not crash and must not produce a finding.
    blind = FakeCam("cam15", "Vasna", None, None)
    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
                        FakeDet(blind, "GJ01AB1234", t0 + timedelta(seconds=2))]) == []

    # Partial reads cannot identify a vehicle and must not accuse one.
    assert find_clones([FakeDet(c04, "AB12", t0),
                        FakeDet(c14, "AB12", t0 + timedelta(seconds=2))]) == []

    # Three cameras, two impossible hops: both are reported.
    chain = find_clones([FakeDet(c04, "GJ01AB1234", t0),
                         FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=2)),
                         FakeDet(c15, "GJ01AB1234", t0 + timedelta(seconds=4))])
    assert len(chain) == 2, chain
    assert chain[0].confidence >= chain[1].confidence, chain

    # Two reads that differ only by a known OCR confusion are the same plate,
    # but the finding must show both as read rather than the folded key.
    folded = find_clones([FakeDet(c04, "GJ01AB1234", t0),
                          FakeDet(c14, "GJ0IAB1234", t0 + timedelta(seconds=2))])
    assert len(folded) == 1 and "6J01A81234" not in folded[0].plate, folded
    assert "GJ0IAB1234" in folded[0].plate, folded[0].plate

    # Distinct plates are never cross-compared.
    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
                        FakeDet(c14, "GJ09ZZ8888", t0 + timedelta(seconds=2))]) == []

    print("cloned_plate self-check passed")


if __name__ == "__main__":
    _self_check()
