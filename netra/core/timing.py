"""When a sighting actually happened.

The sandbox replays recordings, so the wall clock records when we happened to
connect to a feed, not when the scene occurred. Anything that orders or
subtracts sighting times - route reconstruction, cloned-plate detection - must
agree on the same preference, otherwise two modules reading the same rows can
reach contradictory conclusions about the same vehicle.

A scene time is only usable where the anchor behind it was corroborated by two
independent overlay readings. A single misread digit anchors a whole stream and
mis-times every sighting on it; this grid produced spans dated 2025-06-14,
2026-06-24 and 2028-06-13 that way, each from one bad read that passed every
syntactic check. Rows written before corroborated anchoring landed carry
`scene_time_corroborated` false, and are treated here exactly as if they had no
scene time at all.
"""
from __future__ import annotations

from datetime import datetime


def scene_time(det) -> datetime | None:
    """The sighting's scene time, or None where it cannot be trusted.

    The single place that decides what "has a scene clock" means, so that a
    row excluded from journey mining is the same row excluded from route
    elapsed-time maths.
    """
    at = getattr(det, "scene_time", None)
    if at is None:
        return None
    if not getattr(det, "scene_time_corroborated", False):
        return None
    return at


def sighting_time(det) -> datetime:
    """Prefer the timestamp burned into the source video over our own clock.

    Where the camera's own overlay has been parsed *and corroborated*, that is
    the only meaningful ordering; wall time is the fallback for feeds with no
    readable overlay. Wall time orders sightings within one connection but says
    nothing about when the scene occurred, so callers making a cross-camera
    claim must gate on `scene_time()` rather than reading this and hoping.
    """
    return scene_time(det) or det.wall_time


def _self_check() -> None:
    """Pin the preference, and that an uncorroborated overlay is not used."""
    from datetime import timedelta, timezone

    class Det:
        def __init__(self, scene, corroborated, wall):
            self.scene_time = scene
            self.scene_time_corroborated = corroborated
            self.wall_time = wall

    overlay = datetime(2026, 6, 13, 23, 20, tzinfo=timezone.utc)
    connected = overlay + timedelta(days=400)

    # A corroborated overlay wins over our own clock. That is the whole point:
    # wall time here is when we dialled the recording, not when the car passed.
    good = Det(overlay, True, connected)
    assert scene_time(good) == overlay
    assert sighting_time(good) == overlay

    # An uncorroborated one is not a scene time at all.
    bad = Det(overlay, False, connected)
    assert scene_time(bad) is None
    assert sighting_time(bad) == connected

    # A row from a store written before the column existed reads as absent
    # rather than raising, because getattr defaults false.
    class Legacy:
        scene_time = overlay
        wall_time = connected
    assert scene_time(Legacy()) is None

    # No overlay at all: wall time, and callers that need a real scene clock
    # can tell the difference by asking scene_time() instead.
    none = Det(None, False, connected)
    assert scene_time(none) is None and sighting_time(none) == connected

    print("timing self-check passed")


if __name__ == "__main__":
    _self_check()
