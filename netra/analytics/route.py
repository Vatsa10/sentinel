"""Cross-camera route reconstruction.

Given a registration number, assemble the path a vehicle took across the
integrated network: every sighting, in order, with the elapsed time and
distance between consecutive hops.

Two constraints shape this:

  * Sightings are only chainable within a group of cameras that share a
    recorded clock. The Sentinel sandbox holds several independently recorded
    sessions, so comparing a timestamp from one against another is meaningless.
    See docs/feed-recon-findings.md.
  * A hop that would require an impossible speed is rejected rather than drawn.
    A route the operator cannot trust is worse than a short one.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime

from netra.analytics.matching import normalise_plate, spacetime_plausible
from netra.core.geo import haversine_km, time_group


@dataclass
class Hop:
    camera_id: str
    camera_name: str
    lat: float | None
    lon: float | None
    at: datetime
    plate_text: str | None
    plate_conf: float | None
    vehicle_class: str | None
    colour: str | None
    evidence_path: str | None
    detection_id: int
    #: distance and time from the previous hop; None for the first sighting
    leg_km: float | None = None
    leg_seconds: float | None = None
    implied_kmh: float | None = None


@dataclass
class Route:
    query: str
    hops: list[Hop]
    rejected: list[dict]
    total_km: float
    duration_s: float
    time_groups: list[str]

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "hops": [asdict(h) for h in self.hops],
            "rejected": self.rejected,
            "total_km": round(self.total_km, 2),
            "duration_s": round(self.duration_s, 1),
            "time_groups": self.time_groups,
            "hop_count": len(self.hops),
        }


def _sighting_time(det) -> datetime:
    """Prefer the timestamp burned into the source video over our own clock.

    The sandbox replays recordings, so wall time reflects when we happened to
    connect, not when the scene occurred. Where the camera's own overlay has
    been parsed, that is the only meaningful ordering.
    """
    return det.scene_time or det.wall_time


def build_route(detections: list, query: str, min_plate_score: float = 0.6) -> Route:
    """Chain detections of one vehicle into an ordered, validated route.

    `detections` are ORM Detection rows with `.camera` loaded.
    """
    target = normalise_plate(query)
    candidates = []
    for det in detections:
        obs = normalise_plate(det.plate_text)
        if not obs:
            continue
        # Accept an exact match or a partial read contained in the target.
        if obs == target or (len(obs) >= 4 and obs in target):
            candidates.append(det)

    candidates.sort(key=_sighting_time)

    hops: list[Hop] = []
    rejected: list[dict] = []
    total_km = 0.0

    for det in candidates:
        cam = det.camera
        hop = Hop(
            camera_id=det.camera_id,
            camera_name=cam.name if cam else det.camera_id,
            lat=cam.lat if cam else None,
            lon=cam.lon if cam else None,
            at=_sighting_time(det),
            plate_text=det.plate_text,
            plate_conf=det.plate_conf,
            vehicle_class=det.vehicle_class,
            colour=det.colour,
            evidence_path=det.evidence_path,
            detection_id=det.id,
        )

        if hops:
            prev = hops[-1]
            # Only chain sightings that share a recorded clock.
            if time_group(prev.camera_id) != time_group(hop.camera_id):
                rejected.append({
                    "camera_id": hop.camera_id,
                    "reason": "sighting belongs to a different recording session; "
                              "its timestamp is not comparable",
                })
                continue

            if None in (prev.lat, prev.lon, hop.lat, hop.lon):
                km = 0.0
            else:
                km = haversine_km(prev.lat, prev.lon, hop.lat, hop.lon)
            secs = (hop.at - prev.at).total_seconds()

            ok, why = spacetime_plausible(km, secs) if secs > 0 else (False, "out of order")
            if not ok:
                rejected.append({"camera_id": hop.camera_id, "reason": why})
                continue

            hop.leg_km = round(km, 2)
            hop.leg_seconds = round(secs, 1)
            hop.implied_kmh = round(km / (secs / 3600.0), 1) if secs > 0 else None
            total_km += km

        hops.append(hop)

    duration = (hops[-1].at - hops[0].at).total_seconds() if len(hops) > 1 else 0.0
    groups = sorted({g for h in hops if (g := time_group(h.camera_id))})

    return Route(query=query, hops=hops, rejected=rejected,
                 total_km=total_km, duration_s=duration, time_groups=groups)


def _self_check() -> None:
    """Verify the route builder rejects what it should and keeps what it should."""
    from datetime import timedelta, timezone

    class FakeCam:
        def __init__(self, cid, name, lat, lon):
            self.id, self.name, self.lat, self.lon = cid, name, lat, lon

    class FakeDet:
        _next = [1]

        def __init__(self, cam, plate, at):
            self.camera, self.camera_id = cam, cam.id
            self.plate_text, self.plate_conf = plate, 0.9
            self.vehicle_class, self.colour = "car", "white"
            self.evidence_path = None
            self.scene_time, self.wall_time = at, at
            self.id = FakeDet._next[0]
            FakeDet._next[0] += 1

    t0 = datetime(2026, 6, 13, 23, 20, tzinfo=timezone.utc)
    # Two Ahmedabad cameras ~1.3 km apart, three minutes apart: plausible.
    c04 = FakeCam("cam04", "Paldi Circle", 23.0130, 72.5620)
    c14 = FakeCam("cam14", "Delight RLVD", 23.0290, 72.5700)
    # A Junagadh camera: different recording session entirely.
    c10 = FakeCam("cam10", "Char Chowk", 21.5220, 70.4570)

    dets = [
        FakeDet(c04, "GJ01AB1234", t0),
        FakeDet(c14, "GJ01AB1234", t0 + timedelta(minutes=3)),
        FakeDet(c10, "GJ01AB1234", t0 + timedelta(minutes=6)),
    ]
    route = build_route(dets, "GJ01AB1234")

    assert len(route.hops) == 2, route.hops
    assert route.hops[1].leg_km and route.hops[1].leg_km < 5, route.hops[1]
    assert len(route.rejected) == 1, route.rejected
    assert "recording session" in route.rejected[0]["reason"], route.rejected

    # Same cluster but impossibly fast: must be rejected.
    dets_fast = [
        FakeDet(c04, "GJ01AB1234", t0),
        FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=2)),
    ]
    r2 = build_route(dets_fast, "GJ01AB1234")
    assert len(r2.hops) == 1 and len(r2.rejected) == 1, (r2.hops, r2.rejected)

    # A partial plate read still places the vehicle on the route.
    dets_partial = [
        FakeDet(c04, "AB1234", t0),
        FakeDet(c14, "GJ01AB1234", t0 + timedelta(minutes=3)),
    ]
    r3 = build_route(dets_partial, "GJ01AB1234")
    assert len(r3.hops) == 2, r3.hops

    print("route self-check passed")


if __name__ == "__main__":
    _self_check()
