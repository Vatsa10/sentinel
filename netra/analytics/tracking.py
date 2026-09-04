"""Per-camera multi-object tracking.

Tracking turns a stream of independent detections into vehicle journeys, which
is what counting, direction of travel, dwell time and zone intrusion all need.

Two constraints rule out an off-the-shelf tracker here:

  * One inference engine serves every camera, so tracker state must be kept per
    camera. A single shared tracker would associate a vehicle on one camera
    with a vehicle on another.
  * Frames arrive at 1-5 fps, not 25. A vehicle can cross half the frame
    between samples, so the constant-velocity Kalman assumption that ByteTrack
    and similar trackers rely on does not hold.

So association is by spatial overlap, widened by how much time actually passed
(from PTS, never frame count), and broken ties are settled by the appearance
embedding that the inference engine already computes. At low frame rates
appearance is the stronger signal, and it costs nothing extra here.

ponytail: no Kalman filter, no motion model. At 1-5 fps a motion model predicts
badly and adds state to get wrong. Revisit only if sampling rises above ~10 fps.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

#: Minimum overlap to associate a detection with an existing track at 1s apart.
BASE_IOU_THRESHOLD = 0.25
#: Appearance similarity that can rescue an association with poor overlap.
APPEARANCE_RESCUE = 0.86
#: A track with no sighting for this long in stream time is closed.
TRACK_TIMEOUT_S = 6.0


def iou(a: list[int], b: list[int]) -> float:
    """Intersection over union of two [x1, y1, x2, y2] boxes."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def centroid(box: list[int]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


@dataclass
class Track:
    """One vehicle followed across frames on a single camera."""
    track_id: int
    camera_id: str
    vehicle_class: str
    bbox: list[int]
    first_pts_ms: float
    last_pts_ms: float
    embedding: list | None = None
    #: centroid history, for direction and zone crossing
    path: list = field(default_factory=list)
    sightings: int = 1
    counted: bool = False
    #: zones this track has already triggered, so one entry alerts once
    zones_triggered: set = field(default_factory=set)

    @property
    def dwell_s(self) -> float:
        """How long this vehicle has been visible, in stream time."""
        return (self.last_pts_ms - self.first_pts_ms) / 1000.0

    def direction(self) -> str | None:
        """Coarse compass-free direction of travel across the frame.

        Reported only once the vehicle has moved far enough for the direction
        to mean something; jitter on a stationary vehicle is not a direction.
        """
        if len(self.path) < 2:
            return None
        (x0, y0), (x1, y1) = self.path[0], self.path[-1]
        dx, dy = x1 - x0, y1 - y0
        if (dx * dx + dy * dy) ** 0.5 < 40:
            return None
        if abs(dx) > abs(dy):
            return "right" if dx > 0 else "left"
        return "down" if dy > 0 else "up"


class CameraTracker:
    """Tracks vehicles on one camera."""

    def __init__(self, camera_id: str):
        self.camera_id = camera_id
        self.tracks: dict[int, Track] = {}
        self._next_id = 1
        #: cumulative count of distinct vehicles seen, by class
        self.counts: dict[str, int] = {}
        self.total_count = 0

    def reset(self) -> None:
        """Discard all state. Called at a loop cut, where continuity is void."""
        self.tracks.clear()

    def _match(self, det, pts_ms: float) -> Track | None:
        """Best existing track for this detection, or None."""
        best, best_score = None, 0.0
        for track in self.tracks.values():
            if track.vehicle_class != det.vehicle_class:
                continue

            gap_s = max((pts_ms - track.last_pts_ms) / 1000.0, 0.0)
            # A longer gap means the vehicle moved further, so overlap alone is
            # a weaker signal and the threshold relaxes with elapsed time.
            threshold = BASE_IOU_THRESHOLD / (1.0 + gap_s)
            overlap = iou(track.bbox, det.bbox)

            score = overlap if overlap >= threshold else 0.0

            # Poor overlap can still be the same vehicle if it looks the same.
            if score == 0.0 and track.embedding and det.embedding:
                from netra.analytics.reid import similarity
                sim = similarity(track.embedding, det.embedding)
                if sim >= APPEARANCE_RESCUE:
                    score = sim * 0.5   # ranked below a genuine overlap match

            if score > best_score:
                best, best_score = track, score
        return best

    def update(self, detections: list, pts_ms: float) -> list:
        """Associate detections with tracks. Returns newly completed counts.

        Each detection is given a `track_id`, and each track its dwell time and
        direction, so downstream analytics need no tracking logic of their own.
        """
        self._expire(pts_ms)
        claimed: set[int] = set()

        for det in detections:
            track = self._match(det, pts_ms)
            if track is not None and track.track_id not in claimed:
                track.bbox = det.bbox
                track.last_pts_ms = pts_ms
                track.sightings += 1
                track.path.append(centroid(det.bbox))
                if det.embedding:
                    track.embedding = det.embedding
                claimed.add(track.track_id)
            else:
                track = Track(
                    track_id=self._next_id, camera_id=self.camera_id,
                    vehicle_class=det.vehicle_class, bbox=det.bbox,
                    first_pts_ms=pts_ms, last_pts_ms=pts_ms,
                    embedding=det.embedding, path=[centroid(det.bbox)])
                self.tracks[track.track_id] = track
                self._next_id += 1
                claimed.add(track.track_id)

            det.track_id = track.track_id

        # A vehicle is counted once it has been seen twice, which filters the
        # single-frame false positives that a busy night scene produces.
        newly_counted = []
        for track in self.tracks.values():
            if not track.counted and track.sightings >= 2:
                track.counted = True
                self.counts[track.vehicle_class] = \
                    self.counts.get(track.vehicle_class, 0) + 1
                self.total_count += 1
                newly_counted.append(track)
        return newly_counted

    def _expire(self, pts_ms: float) -> None:
        stale = [tid for tid, t in self.tracks.items()
                 if (pts_ms - t.last_pts_ms) / 1000.0 > TRACK_TIMEOUT_S]
        for tid in stale:
            del self.tracks[tid]

    def stats(self) -> dict:
        active = list(self.tracks.values())
        directions: dict[str, int] = {}
        for t in active:
            d = t.direction()
            if d:
                directions[d] = directions.get(d, 0) + 1
        return {
            "camera_id": self.camera_id,
            "active_tracks": len(active),
            "total_counted": self.total_count,
            "counts_by_class": dict(self.counts),
            "directions": directions,
            "mean_dwell_s": round(
                sum(t.dwell_s for t in active) / len(active), 1) if active else 0.0,
        }


class TrackerRegistry:
    """One tracker per camera."""

    def __init__(self):
        self.trackers: dict[str, CameraTracker] = {}

    def get(self, camera_id: str) -> CameraTracker:
        if camera_id not in self.trackers:
            self.trackers[camera_id] = CameraTracker(camera_id)
        return self.trackers[camera_id]

    def reset(self, camera_id: str) -> None:
        if camera_id in self.trackers:
            self.trackers[camera_id].reset()

    def stats(self) -> list[dict]:
        return [t.stats() for t in self.trackers.values()]


def _self_check() -> None:
    """Association decides counting, direction and intrusion, so the cases that
    would silently inflate or merge counts are pinned down here."""
    class Det:
        def __init__(self, bbox, cls="car", emb=None):
            self.bbox, self.vehicle_class, self.embedding = bbox, cls, emb
            self.track_id = None

    assert abs(iou([0, 0, 10, 10], [0, 0, 10, 10]) - 1.0) < 1e-9
    assert iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0

    t = CameraTracker("cam01")

    # A vehicle moving slightly across three frames is one track, counted once.
    t.update([Det([100, 100, 200, 200])], 0.0)
    t.update([Det([110, 100, 210, 200])], 1000.0)
    t.update([Det([120, 100, 220, 200])], 2000.0)
    assert len(t.tracks) == 1, t.tracks
    assert t.total_count == 1, t.total_count

    # A single-frame detection is never counted: that is how false positives
    # on a noisy night scene get filtered.
    t2 = CameraTracker("cam02")
    t2.update([Det([0, 0, 50, 50])], 0.0)
    assert t2.total_count == 0, t2.total_count

    # Two vehicles far apart are two tracks.
    t3 = CameraTracker("cam03")
    t3.update([Det([0, 0, 50, 50]), Det([500, 500, 550, 550])], 0.0)
    assert len(t3.tracks) == 2, t3.tracks

    # Different classes never merge, however well the boxes overlap.
    t4 = CameraTracker("cam04")
    t4.update([Det([100, 100, 200, 200], "car")], 0.0)
    t4.update([Det([100, 100, 200, 200], "truck")], 1000.0)
    assert len(t4.tracks) == 2, t4.tracks

    # Appearance rescues a match the boxes miss - the low-frame-rate case.
    emb = [1.0, 0.0, 0.0]
    t5 = CameraTracker("cam05")
    t5.update([Det([0, 0, 100, 100], "car", emb)], 0.0)
    t5.update([Det([400, 0, 500, 100], "car", emb)], 1000.0)
    assert len(t5.tracks) == 1, "identical appearance should associate"

    # Direction is reported once the vehicle has moved far enough to mean
    # something, while still overlapping enough to stay one track.
    t6 = CameraTracker("cam06")
    t6.update([Det([0, 0, 100, 100])], 0.0)
    t6.update([Det([60, 0, 160, 100])], 1000.0)
    assert len(t6.tracks) == 1, t6.tracks
    track = list(t6.tracks.values())[0]
    assert track.direction() == "right", track.path
    assert track.dwell_s == 1.0, track.dwell_s

    # Without overlap and without an embedding, a large jump is treated as a
    # new vehicle rather than guessed at. This is why embeddings matter at low
    # frame rates: appearance is what holds a track together across the gap.
    t6b = CameraTracker("cam06b")
    t6b.update([Det([0, 0, 100, 100])], 0.0)
    t6b.update([Det([600, 0, 700, 100])], 1000.0)
    assert len(t6b.tracks) == 2, t6b.tracks

    # A stationary vehicle has no direction.
    t7 = CameraTracker("cam07")
    t7.update([Det([0, 0, 100, 100])], 0.0)
    t7.update([Det([2, 1, 102, 101])], 1000.0)
    assert list(t7.tracks.values())[0].direction() is None

    # Stale tracks expire rather than accumulating forever.
    t8 = CameraTracker("cam08")
    t8.update([Det([0, 0, 100, 100])], 0.0)
    t8.update([Det([900, 900, 950, 950])], 30_000.0)
    assert len(t8.tracks) == 1, "the original track should have expired"

    print("tracking self-check passed")


if __name__ == "__main__":
    _self_check()
