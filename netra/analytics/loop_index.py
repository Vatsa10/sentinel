"""Loop indexing and real journey mining.

The Sentinel grid does not carry live cameras. Each endpoint replays one finite
recording on an endless loop, and — this is the part that matters — the cameras
inside a time group replay recordings made at the same time, so the clock burnt
into their frames is a *shared* clock. See docs/feed-recon-findings.md.

Two consequences follow, and this module is both of them:

  * A camera's loop is finite, so it can be processed once, exhaustively, into
    a complete index of every vehicle it ever shows. Live processing samples;
    indexing does not have to. `index_camera` therefore runs slower than
    real time on purpose rather than dropping frames.
  * Once two cameras of one group are indexed, vehicles that genuinely appear
    on both can be *found* rather than demonstrated. `find_journeys` mines the
    index for them.

The honesty rule that governs `reid.py` governs this too. A mined journey is a
chain of appearance matches, not an identification. Every journey carries its
mean similarity, the arithmetic of each hop, and a note saying plainly that it
is a candidate for an operator to confirm.

Three constraints keep the mining defensible:

  * Scene time only. A journey validated on wall time would be fiction: each
    loop starts whenever a client happened to connect, so wall time measures
    our connection, not the vehicle. A detection with no parsed scene time
    cannot take part in a journey, and is reported as excluded rather than
    quietly falling back to the capture clock.
  * Never across time groups, for the same reason route reconstruction and
    clone detection refuse to: two recording sessions share no clock.
  * Never two sightings of one camera in a row. That is one vehicle seen
    twice, not a journey between places.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime

from netra.analytics.matching import spacetime_plausible
from netra.analytics.reid import SIMILARITY_THRESHOLD, similarity
from netra.core.geo import TIME_GROUPS, haversine_km
from netra.core.geo import time_group as camera_time_group
from netra.core.timing import sighting_time

log = logging.getLogger(__name__)

#: Longest a loop-length probe may run before giving up. A recording that has
#: not restarted inside this is either longer than we care to wait for or the
#: connection is wedged; either way the caller gets None rather than a hang.
LOOP_PROBE_TIMEOUT_S = 180.0

#: PTS moving backwards by more than this is the loop point rather than the
#: ordinary out-of-order delivery of B-frames.
LOOP_JUMP_TOLERANCE_MS = 250.0

#: Consecutive failed reads tolerated before a probe or index gives up.
READ_FAILURE_LIMIT = 60

#: Ceiling on the mining itself. Appearance comparison is O(n²) in the worst
#: case, and a fully indexed Ahmedabad group runs to tens of thousands of rows.
#: ponytail: mining considers at most MAX_MINED_DETECTIONS of the most recent
#: detections, examines at most MAX_CANDIDATES_PER_DETECTION forward matches
#: for each one, extends each chain greedily by the single best next hop rather
#: than searching alternatives, and returns at most MAX_JOURNEYS. So this finds
#: strong journeys, not every journey: a vehicle whose true next sighting
#: scored second is followed down the wrong branch and no backtracking recovers
#: it. An exhaustive search over an indexed loop is not affordable inside an
#: API request, and a bounded search that says so is the honest trade.
MAX_MINED_DETECTIONS = 4000
MAX_CANDIDATES_PER_DETECTION = 8
#: Rows looked at ahead of a hop before the search gives up on extending it.
#: Bounds the inner loop even where nothing scores above the threshold.
MAX_SCAN_AHEAD = 200
MAX_JOURNEYS = 50

#: How far ahead in scene time a hop may reach. Beyond this the appearance
#: evidence is doing all the work and the space-time check none of it.
MAX_HOP_SECONDS = 1800.0

#: A journey can never be certain — see the module docstring.
MAX_CONFIDENCE = 0.95

JOURNEY_NOTE = (
    "Appearance-based candidate journey, not an identification. Each hop is a "
    "cosine match between vehicle crops that also passes the space-time "
    "feasibility check on the recorded clock. Confirm against plate, evidence "
    "crops or another signal before acting on it.")


# --------------------------------------------------------------- indexing --
def estimate_loop_length(camera_id: str, timeout_s: float = LOOP_PROBE_TIMEOUT_S,
                         spec=None) -> float | None:
    """Observed length of one camera's recording, in seconds, or None.

    Read PTS until it jumps backwards; the highest PTS reached before that jump
    is the recording's length. This is measured rather than asked for, because
    the grid publishes no duration and two of its cameras declare 0/0 fps, so
    nothing in the catalogue can be trusted to describe timing.
    """
    from netra.ingest.sources import build, spec_for_camera

    spec = spec or spec_for_camera(camera_id)
    source = build(spec)
    try:
        source.open()
    except Exception as exc:  # a probe must never take the caller down with it
        log.warning("%s loop probe could not open source: %s", camera_id, exc)
        return None

    deadline = time.time() + timeout_s
    first_pts: float | None = None
    highest = 0.0
    last = 0.0
    failures = 0
    try:
        while time.time() < deadline:
            ok, _img, pts = source.read()
            if not ok:
                failures += 1
                if failures >= READ_FAILURE_LIMIT:
                    log.warning("%s loop probe: stream stopped delivering", camera_id)
                    return None
                continue
            failures = 0
            if first_pts is None:
                first_pts = pts
            if pts + LOOP_JUMP_TOLERANCE_MS < last:
                # The recording restarted. What we saw between join and restart
                # is a lower bound only if we joined mid-loop, so report the
                # span actually observed and let the caller judge it.
                length = (highest - first_pts) / 1000.0
                return round(length, 2) if length > 0 else None
            last = pts
            highest = max(highest, pts)
    finally:
        source.release()

    log.warning("%s loop probe timed out after %.0fs without a restart",
                camera_id, timeout_s)
    return None


def _submit_blocking(engine, frame, deadline: float) -> bool:
    """Hand a frame to inference, waiting for room rather than dropping it.

    The live path drops frames under load deliberately: a control room needs
    the newest frame, not every frame. Indexing wants the opposite. The whole
    value of a finite loop is that it can be processed *completely*, so here we
    slow the reader down to the model's pace instead of losing vehicles.
    """
    while time.time() < deadline:
        if engine.queue.qsize() < engine.queue.maxsize:
            engine.submit(frame)
            return True
        time.sleep(0.01)
    return False


def index_camera(camera_id: str, engine, max_seconds: float = 900.0,
                 spec=None, persist: bool = True) -> dict:
    """Run one complete pass of a camera's loop through the live inference path.

    `engine` is a loaded, started `InferenceEngine`. Its detection callback is
    borrowed for the duration and restored afterwards, so indexing reuses the
    identical detection, embedding and scene-time-anchoring code the live
    pipeline runs — an index built by a second implementation would not be
    comparable with the detections already in the database.

    Stops at the loop point, at `max_seconds` of wall time, or when the stream
    stops delivering, whichever comes first.
    """
    from netra.ingest.sources import build, spec_for_camera
    from netra.ingest.stream import Frame

    spec = spec or spec_for_camera(camera_id)
    source = build(spec)
    try:
        source.open()
    except Exception as exc:
        log.warning("%s index could not open source: %s", camera_id, exc)
        return {"camera_id": camera_id, "error": str(exc), "frames": 0,
                "detections": 0, "written": 0}

    collected: list = []
    previous_callback = engine.on_detection
    engine.on_detection = collected.append
    # Trackers and clock anchors from any earlier run describe a different pass
    # over the same recording; carrying them in would invent motion across the
    # join point.
    engine.reset_camera_state(camera_id)

    deadline = time.time() + max_seconds
    first_pts: float | None = None
    last_pts = 0.0
    highest = 0.0
    frames = 0
    submitted = 0
    failures = 0
    looped = False

    try:
        while time.time() < deadline:
            ok, img, pts = source.read()
            if not ok:
                failures += 1
                if failures >= READ_FAILURE_LIMIT:
                    break
                continue
            failures = 0
            frames += 1
            if first_pts is None:
                first_pts = pts
            if pts + LOOP_JUMP_TOLERANCE_MS < last_pts:
                looped = True  # a full pass is done
                break

            dt_s = (pts - last_pts) / 1000.0 if frames > 1 else None
            last_pts = pts
            highest = max(highest, pts)
            frame = Frame(camera_id=camera_id, image=img, pts_ms=pts,
                          wall_time=time.time(), dt_s=dt_s, sequence=frames)
            if _submit_blocking(engine, frame, deadline):
                submitted += 1

        # Let the queue drain so detections from the last frames are collected
        # rather than discarded at the moment the pass is declared complete.
        drain_until = min(deadline + 30.0, time.time() + 30.0)
        while engine.queue.qsize() and time.time() < drain_until:
            time.sleep(0.05)
        time.sleep(0.5)
    finally:
        source.release()
        engine.on_detection = previous_callback

    written = _persist(collected) if persist else 0
    with_scene_time = sum(1 for d in collected if getattr(d, "scene_time", None))

    return {
        "camera_id": camera_id,
        "frames": frames,
        "submitted": submitted,
        "detections": len(collected),
        "written": written,
        "video_seconds": round((highest - (first_pts or 0.0)) / 1000.0, 1),
        "loop_complete": looped,
        "scene_time_coverage": (round(with_scene_time / len(collected), 3)
                                if collected else 0.0),
    }


def _persist(detections: list) -> int:
    """Store indexed detections exactly as the live path stores them.

    Deliberately routed through the pipeline's own batched flush rather than a
    second writer: it is the code that names evidence crops, converts the wall
    clock and runs the watchlist check, and an index whose rows differed from
    live rows in any of those would be a second, subtly incompatible dataset.
    """
    if not detections:
        return 0
    from netra.pipeline import PIPELINE, WRITE_BATCH_SIZE

    written = 0
    for start in range(0, len(detections), WRITE_BATCH_SIZE):
        batch = detections[start:start + WRITE_BATCH_SIZE]
        try:
            PIPELINE._flush(batch)
            written += len(batch)
        except Exception:
            log.exception("could not persist a batch of %d indexed detections",
                          len(batch))
    return written


# ---------------------------------------------------------------- mining --
@dataclass
class JourneyHop:
    camera_id: str
    camera_name: str
    lat: float | None
    lon: float | None
    at: str
    detection_id: int
    vehicle_class: str | None
    colour: str | None
    plate_text: str | None
    evidence_path: str | None
    #: appearance agreement with the previous hop; None for the first sighting
    similarity: float | None = None
    leg_km: float | None = None
    leg_seconds: float | None = None
    implied_kmh: float | None = None
    reason: str | None = None


@dataclass
class Journey:
    time_group: str
    hops: list[JourneyHop]
    total_km: float
    elapsed_s: float
    mean_similarity: float
    confidence: float
    note: str = JOURNEY_NOTE
    cameras: list[str] = field(default_factory=list)

    @property
    def hop_count(self) -> int:
        return len(self.hops)

    def to_dict(self) -> dict:
        return {
            "time_group": self.time_group,
            "hops": [asdict(h) for h in self.hops],
            "hop_count": len(self.hops),
            "cameras": self.cameras,
            "total_km": round(self.total_km, 2),
            "elapsed_s": round(self.elapsed_s, 1),
            "mean_similarity": round(self.mean_similarity, 3),
            "confidence": self.confidence,
            "note": self.note,
        }


def _hop_from(det, scene_at: datetime) -> JourneyHop:
    cam = getattr(det, "camera", None)
    return JourneyHop(
        camera_id=det.camera_id,
        camera_name=cam.name if cam else det.camera_id,
        lat=cam.lat if cam else None,
        lon=cam.lon if cam else None,
        at=scene_at.isoformat(),
        detection_id=det.id,
        vehicle_class=det.vehicle_class,
        colour=det.colour,
        plate_text=det.plate_text,
        evidence_path=det.evidence_path,
    )


def _leg(prev_det, det, prev_at: datetime, at: datetime) -> tuple[bool, dict]:
    """Is this hop physically possible on the recorded clock?"""
    seconds = (at - prev_at).total_seconds()
    if seconds <= 0:
        return False, {"reason": "sightings are simultaneous or out of order"}
    if seconds > MAX_HOP_SECONDS:
        return False, {"reason": f"gap of {seconds:.0f}s exceeds the "
                                 f"{MAX_HOP_SECONDS:.0f}s hop limit"}

    pcam, ncam = getattr(prev_det, "camera", None), getattr(det, "camera", None)
    if None in (pcam, ncam) or None in (getattr(pcam, "lat", None),
                                        getattr(pcam, "lon", None),
                                        getattr(ncam, "lat", None),
                                        getattr(ncam, "lon", None)):
        km = 0.0
    else:
        km = haversine_km(pcam.lat, pcam.lon, ncam.lat, ncam.lon)

    ok, why = spacetime_plausible(km, seconds)
    return ok, {"km": km, "seconds": seconds, "reason": why,
                "implied_kmh": km / (seconds / 3600.0)}


def _confidence(similarities: list[float], hop_count: int) -> float:
    """How strongly the appearance evidence supports this chain.

    Mean similarity leads, because that is what the evidence actually is. A
    longer chain earns a small addition — three cameras agreeing is a stronger
    argument than two — but only a small one, since a greedy chain can extend
    itself through a wrong link as easily as a right one. Capped below
    certainty: this is never an identification.
    """
    mean = sum(similarities) / len(similarities) if similarities else 0.0
    length_bonus = min(0.06, 0.02 * max(0, hop_count - 2))
    return round(min(MAX_CONFIDENCE, mean * 0.95 + length_bonus), 3)


def _minable(detections: list, group: str) -> tuple[list, dict]:
    """Detections of one group that can legitimately take part in a journey."""
    members = set(TIME_GROUPS.get(group, ()))
    usable, excluded = [], {"wrong_group": 0, "no_scene_time": 0, "no_embedding": 0}
    for det in detections:
        if det.camera_id not in members:
            excluded["wrong_group"] += 1
            continue
        if not getattr(det, "scene_time", None):
            # Wall time is our connection time, not the vehicle's. A sighting
            # with no recorded clock simply cannot be placed on a journey.
            excluded["no_scene_time"] += 1
            continue
        if not getattr(det, "embedding", None):
            excluded["no_embedding"] += 1
            continue
        usable.append(det)
    # Newest first for the cap, so a long-running index keeps its recent pass.
    usable.sort(key=sighting_time)
    if len(usable) > MAX_MINED_DETECTIONS:
        usable = usable[-MAX_MINED_DETECTIONS:]
    return usable, excluded


def find_journeys(time_group: str, min_similarity: float = 0.84,
                  min_hops: int = 2, detections: list | None = None,
                  limit: int = MAX_JOURNEYS) -> list[Journey]:
    """Mine one time group's indexed detections for real cross-camera journeys.

    `detections` are ORM Detection rows with `.camera` loaded; when omitted they
    are read from the database for the group's cameras.

    Chaining is greedy and bounded — see MAX_MINED_DETECTIONS above for the
    ceiling and what it costs.
    """
    if time_group not in TIME_GROUPS:
        return []
    if detections is None:
        detections = _load_group_detections(time_group)

    usable, _excluded = _minable(detections, time_group)
    min_similarity = max(min_similarity, SIMILARITY_THRESHOLD)

    used: set[int] = set()
    journeys: list[Journey] = []

    for i, seed in enumerate(usable):
        if len(journeys) >= limit:
            break
        if seed.id in used:
            continue

        chain = [seed]
        chain_times = [sighting_time(seed)]
        sims: list[float] = []
        legs: list[dict] = []

        cursor = i
        while True:
            current = chain[-1]
            current_at = chain_times[-1]
            best = None
            considered = 0
            scanned = 0
            for j in range(cursor + 1, len(usable)):
                nxt = usable[j]
                if considered >= MAX_CANDIDATES_PER_DETECTION or scanned >= MAX_SCAN_AHEAD:
                    break
                scanned += 1
                if nxt.id in used or nxt.camera_id == current.camera_id:
                    # Two sightings on one camera are one vehicle seen twice.
                    continue
                # Both cameras are group members by construction; asserted
                # because chaining across recording sessions is the one error
                # that would make every figure below meaningless.
                assert camera_time_group(nxt.camera_id) == time_group, nxt.camera_id
                score = similarity(current.embedding, nxt.embedding)
                if score < min_similarity:
                    continue
                considered += 1
                nxt_at = sighting_time(nxt)
                ok, leg = _leg(current, nxt, current_at, nxt_at)
                if not ok:
                    continue
                if best is None or score > best[0]:
                    best = (score, j, nxt, nxt_at, leg)

            if best is None:
                break
            score, j, nxt, nxt_at, leg = best
            chain.append(nxt)
            chain_times.append(nxt_at)
            sims.append(score)
            legs.append(leg)
            cursor = j

        if len(chain) < max(2, min_hops):
            continue
        if len({d.camera_id for d in chain}) < 2:
            continue

        hops = [_hop_from(chain[0], chain_times[0])]
        for k in range(1, len(chain)):
            hop = _hop_from(chain[k], chain_times[k])
            leg = legs[k - 1]
            hop.similarity = round(sims[k - 1], 3)
            hop.leg_km = round(leg["km"], 2)
            hop.leg_seconds = round(leg["seconds"], 1)
            hop.implied_kmh = round(leg["implied_kmh"], 1)
            hop.reason = leg["reason"]
            hops.append(hop)

        for det in chain:
            used.add(det.id)

        journeys.append(Journey(
            time_group=time_group,
            hops=hops,
            total_km=sum(leg["km"] for leg in legs),
            elapsed_s=(chain_times[-1] - chain_times[0]).total_seconds(),
            mean_similarity=sum(sims) / len(sims),
            confidence=_confidence(sims, len(chain)),
            cameras=sorted({d.camera_id for d in chain}),
        ))

    # Strongest evidence first: an operator reads from the top.
    journeys.sort(key=lambda j: (j.confidence, j.hop_count), reverse=True)
    return journeys[:limit]


def _load_group_detections(group: str) -> list:
    from sqlalchemy.orm import joinedload

    from netra.core.db import SessionLocal
    from netra.core.models import Detection

    members = TIME_GROUPS.get(group, [])
    if not members:
        return []
    with SessionLocal() as db:
        return (db.query(Detection).options(joinedload(Detection.camera))
                .filter(Detection.camera_id.in_(members),
                        Detection.scene_time.isnot(None),
                        Detection.embedding.isnot(None))
                .order_by(Detection.scene_time)
                .limit(MAX_MINED_DETECTIONS).all())


# ----------------------------------------------------------- persistence --
def persist_journeys(group: str, journeys: list[Journey]) -> int:
    """Replace the stored journeys for one group.

    Replaced rather than appended: mining is deterministic over the index, so a
    second run of the same group produces the same journeys and appending would
    show an operator each one several times.
    """
    from netra.core.db import SessionLocal
    from netra.core.models import MinedJourney

    with SessionLocal() as db:
        db.query(MinedJourney).filter(MinedJourney.time_group == group).delete()
        for j in journeys:
            first = datetime.fromisoformat(j.hops[0].at)
            last = datetime.fromisoformat(j.hops[-1].at)
            db.add(MinedJourney(
                time_group=group, hop_count=len(j.hops), cameras=j.cameras,
                total_km=round(j.total_km, 2), elapsed_s=round(j.elapsed_s, 1),
                mean_similarity=round(j.mean_similarity, 3),
                confidence=j.confidence, first_seen=first, last_seen=last,
                hops=[asdict(h) for h in j.hops], note=j.note))
        db.commit()
    return len(journeys)


def stored_journeys(group: str, limit: int = MAX_JOURNEYS) -> list[dict]:
    """Journeys mined earlier, so the console need not re-run the mining."""
    from netra.core.db import SessionLocal
    from netra.core.models import MinedJourney

    with SessionLocal() as db:
        rows = (db.query(MinedJourney)
                .filter(MinedJourney.time_group == group)
                .order_by(MinedJourney.confidence.desc()).limit(limit).all())
    return [{
        "time_group": r.time_group, "hops": r.hops, "hop_count": r.hop_count,
        "cameras": r.cameras, "total_km": r.total_km, "elapsed_s": r.elapsed_s,
        "mean_similarity": r.mean_similarity, "confidence": r.confidence,
        "note": r.note, "mined_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


# --------------------------------------------------------------- self-check --
def _self_check() -> None:
    """Mining is checked on synthetic detections: no network, no GPU, no model."""
    from datetime import timedelta, timezone

    import numpy as np

    def vec(seed: int):
        rng = np.random.default_rng(seed)
        v = rng.normal(size=32).astype(np.float32)
        return (v / np.linalg.norm(v)).tolist()

    silver, red = vec(1), vec(2)
    assert similarity(silver, red) < 0.5, "test vectors must be distinguishable"

    class FakeCam:
        def __init__(self, cid, name, lat, lon):
            self.id, self.name, self.lat, self.lon = cid, name, lat, lon

    class FakeDet:
        _next = [1]

        def __init__(self, cam, at, emb, scene=True):
            self.camera, self.camera_id = cam, cam.id
            self.scene_time = at if scene else None
            self.wall_time = at
            self.embedding = emb
            self.vehicle_class, self.colour = "car", "silver"
            self.plate_text = self.evidence_path = None
            self.id = FakeDet._next[0]
            FakeDet._next[0] += 1

    c01 = FakeCam("cam01", "Vastrapur", 23.0290, 72.5580)
    c04 = FakeCam("cam04", "Paldi Circle", 23.0130, 72.5620)
    c14 = FakeCam("cam14", "Delight RLVD", 23.0290, 72.5700)
    c10 = FakeCam("cam10", "Char Chowk", 21.5220, 70.4570)  # other group

    t0 = datetime(2026, 6, 13, 23, 20, tzinfo=timezone.utc)

    # A genuine three-camera journey, plus unrelated traffic.
    dets = [
        FakeDet(c04, t0, silver),
        FakeDet(c14, t0 + timedelta(minutes=3), silver),
        FakeDet(c01, t0 + timedelta(minutes=7), silver),
        FakeDet(c04, t0 + timedelta(minutes=1), red),
    ]
    journeys = find_journeys("ahmedabad-13jun", detections=dets)
    assert len(journeys) == 1, journeys
    j = journeys[0]
    assert j.hop_count == 3, j.hop_count
    assert [h.camera_id for h in j.hops] == ["cam04", "cam14", "cam01"], j.hops
    # Ordered by scene time, and each hop carries its arithmetic.
    ats = [datetime.fromisoformat(h.at) for h in j.hops]
    assert ats == sorted(ats), ats
    assert all(h.leg_km is not None and h.implied_kmh is not None
               for h in j.hops[1:]), j.hops
    assert 0 < j.confidence < 1.0, j.confidence
    assert "not an identification" in j.note

    # Shuffled input must produce the same scene-time ordering.
    shuffled = [dets[2], dets[0], dets[3], dets[1]]
    j2 = find_journeys("ahmedabad-13jun", detections=shuffled)[0]
    assert [h.camera_id for h in j2.hops] == ["cam04", "cam14", "cam01"], j2.hops

    # Never chains across time groups: the Junagadh sighting shares no clock.
    cross = [FakeDet(c04, t0, silver), FakeDet(c10, t0 + timedelta(minutes=4), silver)]
    assert find_journeys("ahmedabad-13jun", detections=cross) == []
    assert find_journeys("junagadh-13jun", detections=cross) == []

    # An implausible hop is rejected: 1.3 km in two seconds.
    fast = [FakeDet(c04, t0, silver), FakeDet(c14, t0 + timedelta(seconds=2), silver)]
    assert find_journeys("ahmedabad-13jun", detections=fast) == []

    # Two sightings on the same camera are not a journey.
    same = [FakeDet(c04, t0, silver), FakeDet(c04, t0 + timedelta(minutes=3), silver)]
    assert find_journeys("ahmedabad-13jun", detections=same) == []

    # Below min_hops, nothing is returned.
    two = [FakeDet(c04, t0, silver), FakeDet(c14, t0 + timedelta(minutes=3), silver)]
    assert len(find_journeys("ahmedabad-13jun", detections=two, min_hops=2)) == 1
    assert find_journeys("ahmedabad-13jun", detections=two, min_hops=3) == []

    # A sighting with no scene time cannot take part.
    no_clock = [FakeDet(c04, t0, silver),
                FakeDet(c14, t0 + timedelta(minutes=3), silver, scene=False)]
    assert find_journeys("ahmedabad-13jun", detections=no_clock) == []

    # Dissimilar vehicles are not chained together.
    unlike = [FakeDet(c04, t0, silver), FakeDet(c14, t0 + timedelta(minutes=3), red)]
    assert find_journeys("ahmedabad-13jun", detections=unlike) == []

    # An unknown group mines nothing rather than raising.
    assert find_journeys("no-such-group", detections=dets) == []

    print("loop_index self-check passed")


if __name__ == "__main__":
    _self_check()
