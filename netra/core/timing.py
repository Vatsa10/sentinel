"""When a sighting actually happened.

The sandbox replays recordings, so the wall clock records when we happened to
connect to a feed, not when the scene occurred. Anything that orders or
subtracts sighting times - route reconstruction, cloned-plate detection - must
agree on the same preference, otherwise two modules reading the same rows can
reach contradictory conclusions about the same vehicle.
"""
from __future__ import annotations

from datetime import datetime


def sighting_time(det) -> datetime:
    """Prefer the timestamp burned into the source video over our own clock.

    Where the camera's own overlay has been parsed, that is the only meaningful
    ordering; wall time is the fallback for feeds with no readable overlay.
    """
    return det.scene_time or det.wall_time
