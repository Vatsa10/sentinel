"""Zone rules: intrusion, line crossing, and loitering.

A camera watching a godown gate, a toll lane or a restricted compound is not
asking "what vehicles are here" but "did anything enter where it should not,
and when". These are the analytics that make a camera useful when plate
recognition is impossible, which on this grid is most of them.

Three rule types, all evaluated against tracked objects rather than raw
detections, so an object entering a zone raises one alert rather than one per
frame:

    intrusion   object appears inside a polygon
    crossing    object's path crosses a line, with the direction it crossed
    loitering   object remains inside a polygon beyond a dwell threshold

Zones are stored per camera in normalised 0-1 coordinates, so a rule survives
the camera being re-encoded at a different resolution - which matters on a grid
carrying five different resolutions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger(__name__)

RULE_TYPES = ("intrusion", "crossing", "loitering")


def point_in_polygon(x: float, y: float, polygon: list) -> bool:
    """Ray-casting test. Polygon is [[x, y], ...] in the same units as x, y."""
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def segments_intersect(p1, p2, p3, p4) -> bool:
    """Do segment p1-p2 and segment p3-p4 cross?"""
    def orient(a, b, c) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    return False


def crossing_side(line: list, point) -> str:
    """Which side of a directed line a point falls on."""
    (x1, y1), (x2, y2) = line[0], line[1]
    d = (x2 - x1) * (point[1] - y1) - (y2 - y1) * (point[0] - x1)
    return "a" if d > 0 else "b"


@dataclass
class Zone:
    """One rule on one camera."""
    zone_id: str
    camera_id: str
    name: str
    rule: str                       # intrusion | crossing | loitering
    #: normalised 0-1 coordinates: polygon for intrusion/loitering, two points
    #: for crossing
    points: list
    classes: list = field(default_factory=list)   # empty means any class
    severity: str = "medium"
    dwell_s: float = 30.0           # loitering only
    active: bool = True

    def applies_to(self, vehicle_class: str) -> bool:
        return not self.classes or vehicle_class in self.classes

    def to_pixels(self, width: int, height: int) -> list:
        return [[p[0] * width, p[1] * height] for p in self.points]


@dataclass
class ZoneEvent:
    zone: Zone
    track_id: int
    camera_id: str
    vehicle_class: str
    rule: str
    detail: str
    at: datetime
    direction: str | None = None


class ZoneEngine:
    """Evaluates zone rules against tracked objects."""

    def __init__(self):
        self.zones: dict[str, list[Zone]] = {}   # camera_id -> zones
        #: (zone_id, track_id) already reported, so one entry alerts once
        self._fired: set = set()
        #: (zone_id, track_id) -> which side of a crossing line it was last on
        self._sides: dict = {}
        self.events_raised = 0

    def set_zones(self, camera_id: str, zones: list[Zone]) -> None:
        self.zones[camera_id] = [z for z in zones if z.active]

    def reset_camera(self, camera_id: str) -> None:
        """Forget per-track state after a loop cut; track ids restart."""
        for key in [k for k in self._fired if k[0].startswith(f"{camera_id}:")]:
            self._fired.discard(key)
        for key in [k for k in self._sides if k[0].startswith(f"{camera_id}:")]:
            self._sides.pop(key, None)

    def evaluate(self, camera_id: str, tracks: list, frame_size) -> list[ZoneEvent]:
        """Check every active track on this camera against its zones."""
        zones = self.zones.get(camera_id)
        if not zones or not tracks:
            return []

        width, height = frame_size
        events: list[ZoneEvent] = []
        now = datetime.now(timezone.utc)

        for zone in zones:
            pts = zone.to_pixels(width, height)
            for track in tracks:
                if not zone.applies_to(track.vehicle_class):
                    continue
                key = (zone.zone_id, track.track_id)

                if zone.rule == "intrusion":
                    if key in self._fired or not track.path:
                        continue
                    if point_in_polygon(*track.path[-1], pts):
                        self._fired.add(key)
                        events.append(ZoneEvent(
                            zone=zone, track_id=track.track_id,
                            camera_id=camera_id,
                            vehicle_class=track.vehicle_class, rule="intrusion",
                            detail=f"{track.vehicle_class} entered {zone.name}",
                            at=now, direction=track.direction()))

                elif zone.rule == "loitering":
                    if key in self._fired or not track.path:
                        continue
                    if (point_in_polygon(*track.path[-1], pts)
                            and track.dwell_s >= zone.dwell_s):
                        self._fired.add(key)
                        events.append(ZoneEvent(
                            zone=zone, track_id=track.track_id,
                            camera_id=camera_id,
                            vehicle_class=track.vehicle_class, rule="loitering",
                            detail=(f"{track.vehicle_class} remained in "
                                    f"{zone.name} for {track.dwell_s:.0f}s"),
                            at=now, direction=track.direction()))

                elif zone.rule == "crossing":
                    if len(track.path) < 2 or key in self._fired:
                        continue
                    if segments_intersect(track.path[-2], track.path[-1],
                                          pts[0], pts[1]):
                        side = crossing_side(pts, track.path[-1])
                        self._fired.add(key)
                        events.append(ZoneEvent(
                            zone=zone, track_id=track.track_id,
                            camera_id=camera_id,
                            vehicle_class=track.vehicle_class, rule="crossing",
                            detail=(f"{track.vehicle_class} crossed {zone.name} "
                                    f"towards side {side}"),
                            at=now, direction=track.direction()))

        self.events_raised += len(events)
        return events


def _self_check() -> None:
    """Geometry decides whether an intrusion alert fires, so the boundary cases
    matter more than the obvious ones."""
    square = [[0, 0], [100, 0], [100, 100], [0, 100]]
    assert point_in_polygon(50, 50, square)
    assert not point_in_polygon(150, 50, square)
    assert not point_in_polygon(-1, 50, square)
    assert not point_in_polygon(50, 150, square)
    assert not point_in_polygon(5, 5, [[0, 0], [10, 0]])   # not a polygon

    # A line from left to right crosses a vertical line between them.
    assert segments_intersect((0, 50), (100, 50), (50, 0), (50, 100))
    assert not segments_intersect((0, 0), (10, 0), (0, 50), (10, 50))

    class FakeTrack:
        def __init__(self, tid, path, cls="car", dwell=0.0):
            self.track_id, self.path, self.vehicle_class = tid, path, cls
            self.dwell_s = dwell

        def direction(self):
            return None

    engine = ZoneEngine()
    zone = Zone(zone_id="cam01:z1", camera_id="cam01", name="Gate",
                rule="intrusion", points=[[0, 0], [0.5, 0], [0.5, 0.5], [0, 0.5]])
    engine.set_zones("cam01", [zone])

    # Inside the polygon fires once, not on every subsequent frame.
    inside = FakeTrack(1, [(100, 100)])
    ev = engine.evaluate("cam01", [inside], (1000, 1000))
    assert len(ev) == 1 and ev[0].rule == "intrusion", ev
    assert engine.evaluate("cam01", [inside], (1000, 1000)) == []

    # Outside the polygon never fires.
    outside = FakeTrack(2, [(900, 900)])
    assert engine.evaluate("cam01", [outside], (1000, 1000)) == []

    # Class filtering is honoured.
    truck_only = Zone(zone_id="cam02:z1", camera_id="cam02", name="Dock",
                      rule="intrusion", points=[[0, 0], [1, 0], [1, 1], [0, 1]],
                      classes=["truck"])
    engine.set_zones("cam02", [truck_only])
    assert engine.evaluate("cam02", [FakeTrack(3, [(50, 50)], "car")],
                           (100, 100)) == []
    assert len(engine.evaluate("cam02", [FakeTrack(4, [(50, 50)], "truck")],
                               (100, 100))) == 1

    # Loitering needs the dwell threshold, not merely presence.
    loiter = Zone(zone_id="cam03:z1", camera_id="cam03", name="Yard",
                  rule="loitering", points=[[0, 0], [1, 0], [1, 1], [0, 1]],
                  dwell_s=30)
    engine.set_zones("cam03", [loiter])
    assert engine.evaluate("cam03", [FakeTrack(5, [(50, 50)], "car", 10)],
                           (100, 100)) == []
    assert len(engine.evaluate("cam03", [FakeTrack(6, [(50, 50)], "car", 45)],
                               (100, 100))) == 1

    # Crossing needs a path that actually intersects the line.
    line = Zone(zone_id="cam04:z1", camera_id="cam04", name="Tripwire",
                rule="crossing", points=[[0.5, 0.0], [0.5, 1.0]])
    engine.set_zones("cam04", [line])
    assert engine.evaluate("cam04", [FakeTrack(7, [(10, 50), (20, 50)])],
                           (100, 100)) == []
    assert len(engine.evaluate("cam04", [FakeTrack(8, [(40, 50), (60, 50)])],
                               (100, 100))) == 1

    # A loop cut clears per-track state, since track ids restart from 1.
    engine.reset_camera("cam01")
    assert len(engine.evaluate("cam01", [inside], (1000, 1000))) == 1

    print("zones self-check passed")


if __name__ == "__main__":
    _self_check()
