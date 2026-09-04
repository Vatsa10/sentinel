"""Two-tier GPU inference.

One process owns the GPU. Thirty ingest threads feed it through a bounded
queue; it batches what it can and drops what it cannot keep up with. Thirty
processes each holding their own CUDA context would exhaust 8 GB immediately,
so this is deliberately the only component that touches the device.

Tier 1 runs on every camera at a low rate and answers one question: are there
vehicles here? Cameras that say yes are escalated by the ingest supervisor to a
higher frame rate, and tier 2 then does the expensive work - plate localisation,
OCR, and colour extraction - only on those.

The effect is that GPU budget follows traffic instead of being spread evenly
over cameras that are looking at an empty road at 3am. The same tiering is what
makes the design scale to regional edge nodes.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from netra import config

log = logging.getLogger(__name__)

#: How many frames to spend looking for a timestamp overlay before accepting
#: that a camera has none. Roughly half the grid has no legible overlay.
CLOCK_ATTEMPT_LIMIT = 4

#: Stream seconds after which an anchor is re-read from the overlay. One
#: reading extrapolated indefinitely drifts with the decoder's timing, so
#: timestamps on a long-lived connection grow silently wrong. Fifteen minutes
#: is far more often than drift becomes material, and far rarer than the OCR
#: cost would justify doing it any more eagerly.
CLOCK_REANCHOR_AFTER_S = 900.0

#: Anchoring budget for an offline exhaustive pass over a finite recording.
#: Far larger than the live limit because the two situations are opposites: a
#: live camera competes with detection for the same second, an indexing pass
#: competes with nothing and exists precisely to get this right. Attempts are
#: spaced INDEX_CLOCK_RETRY_MS apart in *stream* time so the budget is spent
#: across the recording rather than burnt on its first ten seconds - an overlay
#: obscured by a passing lorry at the join may be perfectly legible a minute in.
INDEX_CLOCK_ATTEMPT_LIMIT = 30
INDEX_CLOCK_RETRY_MS = 20000.0

#: How the engine spends its scene-clock budget.
#:   opportunistic  live: skip the read whenever frames are backing up, because
#:                  detection is the primary duty. Measured: without this,
#:                  83% of frames dropped.
#:   exhaustive     offline indexing: never skip. An indexing pass feeds frames
#:                  blocking, so the queue is always full and the opportunistic
#:                  rule would skip every single attempt - which is exactly
#:                  what it did, anchoring 0% of 27,000 indexed detections.
CLOCK_OPPORTUNISTIC = "opportunistic"
CLOCK_EXHAUSTIVE = "exhaustive"

#: Minimum crop height worth embedding. Below this an appearance vector cannot
#: distinguish one vehicle from another, so it is cost without information.
REID_MIN_CROP_PX = 64
#: Cap on embeddings per frame. Busy junction cameras routinely show 20+
#: vehicles; embedding every one saturates the queue and starves detection,
#: which matters more. Largest vehicles are embedded first.
REID_MAX_PER_FRAME = 8

#: Minimum vehicle height before a plate read is even attempted. A plate on a
#: vehicle smaller than this spans a handful of pixels and cannot be resolved,
#: so the OCR call is pure cost. Measured on this grid, no plate was readable
#: on any vehicle at all - see docs/feed-recon-findings.md.
PLATE_MIN_VEHICLE_PX = 110
#: Cap on plate reads per frame. OCR is the most expensive operation in the
#: pipeline at roughly 50ms per vehicle.
PLATE_MAX_PER_FRAME = 4

#: Mean luma below which a frame carries no scene at all. The registry
#: classifies dead cameras at onboarding, but a camera can go dark afterwards -
#: nightfall, a failed IR illuminator, a lens cover - and a black feed still
#: costs a full YOLO pass per frame, forever, on a GPU that is the scarcest
#: resource here. Measured across this grid, usable night frames sit well above
#: 18 while dead feeds sit near zero.
DARK_LUMA_THRESHOLD = 18
#: Consecutive dark frames with nothing detected before a camera is skipped.
#: Sixty at tier-1 rate is about a minute, which is long enough that a lorry
#: parked against the lens or a passing cloud cannot trip it.
DARK_FRAME_LIMIT = 60
#: A dark camera is re-tested every this many frames so it recovers on its own
#: at dawn. At tier-1 rate that is a probe roughly every five minutes: cheap
#: against the ~99.7% of inference passes it saves, and quick enough that no
#: real traffic is missed for long.
DARK_RECHECK_FRAMES = 300
#: Stride used to downscale a frame before measuring luma. Sampling every 8th
#: pixel is a numpy view rather than a copy, so the measurement costs
#: microseconds - it must not become a cost of its own.
LUMA_SAMPLE_STRIDE = 8


def mean_luma(image) -> float:
    """Mean brightness of a frame, measured on a strided sample.

    BT.601 weights on BGR. Deliberately not cv2.cvtColor over the full frame:
    that allocates a greyscale copy of every frame on every camera, which is
    real cost to answer a question about darkness.
    """
    if image is None or getattr(image, "size", 0) == 0:
        return 0.0
    sample = image[::LUMA_SAMPLE_STRIDE, ::LUMA_SAMPLE_STRIDE]
    if sample.size == 0:
        return 0.0
    if sample.ndim == 3 and sample.shape[2] >= 3:
        b, g, r = (sample[:, :, 0].mean(), sample[:, :, 1].mean(),
                   sample[:, :, 2].mean())
        return float(0.114 * b + 0.587 * g + 0.299 * r)
    return float(sample.mean())


@dataclass
class VehicleDetection:
    camera_id: str
    pts_ms: float
    wall_time: float
    vehicle_class: str
    confidence: float
    bbox: list[int]
    colour: str | None = None
    plate_text: str | None = None
    plate_conf: float | None = None
    plate_chars: int | None = None
    plate_bbox: list[int] | None = None
    #: real time the scene occurred, from the camera's burned-in overlay
    scene_time: object | None = None
    #: assigned by the per-camera tracker; identifies one vehicle journey
    track_id: int | None = None
    embedding: list | None = field(default=None, repr=False)
    evidence: object | None = field(default=None, repr=False)


# Coarse colour vocabulary. Street lighting makes anything finer dishonest.
_COLOUR_REFS = {
    "white": (200, 200, 200),
    "silver": (150, 150, 150),
    "black": (45, 45, 45),
    "red": (30, 30, 160),
    "blue": (150, 60, 30),
    "yellow": (40, 180, 200),
    "green": (60, 130, 60),
}


def estimate_colour(crop: np.ndarray) -> str | None:
    """Nearest reference colour of the vehicle body.

    Sampled from the middle band of the crop, avoiding windows above and
    shadow/road below.

    ponytail: nearest-neighbour in BGR. Colour under sodium vapour lighting is
    not reliable enough to justify anything cleverer, and the matcher weights
    it accordingly.
    """
    if crop is None or crop.size == 0:
        return None
    h, w = crop.shape[:2]
    if h < 8 or w < 8:
        return None
    band = crop[int(h * 0.35):int(h * 0.75), int(w * 0.2):int(w * 0.8)]
    if band.size == 0:
        return None
    mean = band.reshape(-1, 3).mean(axis=0)
    best, best_dist = None, 1e9
    for name, ref in _COLOUR_REFS.items():
        d = float(np.linalg.norm(mean - np.array(ref)))
        if d < best_dist:
            best, best_dist = name, d
    return best


class InferenceEngine:
    """Owns the GPU. Consumes frames, produces detections."""

    def __init__(self, on_detection, on_vehicles_present=None, queue_size: int = 64):
        self.on_detection = on_detection
        self.on_vehicles_present = on_vehicles_present
        self.queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self.camera_capability: dict[str, str] = {}

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._vehicle_model = None
        self._plate_model = None
        self._ocr = None
        self._reid = None
        #: camera_id -> ClockAnchor, tying each stream to real scene time
        self._clocks: dict = {}
        #: how many overlay reads have been attempted per camera
        self._clock_attempts: dict = {}
        #: stream time of the last overlay attempt per camera, used only by the
        #: exhaustive policy to space its attempts across the recording
        self._clock_last_try: dict = {}
        #: live behaviour is the default and is unchanged; only an offline
        #: indexing pass sets this to CLOCK_EXHAUSTIVE
        self.clock_policy: str = CLOCK_OPPORTUNISTIC
        #: per-camera trackers; tracking is what counting, direction, dwell
        #: and zone rules are all built on
        from netra.analytics.tracking import TrackerRegistry
        self.trackers = TrackerRegistry()
        #: camera_id -> PlateVoter; plate reads from one tracked vehicle vote
        #: together, because a single frame's read is a guess
        self._plate_voters: dict = {}
        #: camera_id -> consecutive dark, empty frames seen
        self._dark_streak: dict = {}
        #: camera_id -> when it was marked dark; presence means "skip"
        self._dark_cameras: dict = {}
        #: camera_id -> frames skipped since the last probe
        self._dark_skipped: dict = {}
        #: set by the pipeline so zone rules can be evaluated here, where the
        #: tracks live
        self.zone_engine = None
        self.on_zone_event = None

        self.stats = {"submitted": 0, "dropped": 0, "processed": 0,
                      "vehicles": 0, "plates": 0, "embedded": 0,
                      "clocks_anchored": 0, "plate_votes": 0,
                      "dark_cameras": 0, "dark_frames_skipped": 0,
                      "infer_ms": 0.0}

    # -- model loading -------------------------------------------------------
    def load(self) -> None:
        from ultralytics import YOLO
        log.info("loading vehicle model on %s", config.DEVICE)
        self._vehicle_model = YOLO(config.VEHICLE_MODEL)
        self._vehicle_model.to(config.DEVICE)

        import os
        if os.path.exists(config.PLATE_MODEL):
            log.info("loading plate model")
            self._plate_model = YOLO(config.PLATE_MODEL)
            self._plate_model.to(config.DEVICE)
        else:
            log.warning("no plate model at %s - plate localisation disabled",
                        config.PLATE_MODEL)

        try:
            self._ocr = _load_ocr()
            log.info("OCR ready")
        except Exception as exc:
            log.warning("OCR unavailable (%s) - plates will not be read", exc)

        # Appearance embeddings are what allow a vehicle to be followed across
        # cameras on this grid, where plates are not recoverable.
        try:
            from netra.analytics.reid import ReIdEncoder
            self._reid = ReIdEncoder()
            self._reid.load()
        except Exception as exc:
            log.warning("re-identification unavailable (%s)", exc)
            self._reid = None

    # -- frame intake --------------------------------------------------------
    def submit(self, frame) -> None:
        """Non-blocking. A full queue drops the frame rather than stalling ingest.

        Dropping is correct here: the streams are live and unpauseable, so a
        frame we cannot process now is worth less than the one arriving next.
        """
        self.stats["submitted"] += 1
        try:
            self.queue.put_nowait(frame)
        except queue.Full:
            self.stats["dropped"] += 1

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="inference", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process(frame)
            except Exception:
                log.exception("inference failed for %s", frame.camera_id)

    # -- the actual work -----------------------------------------------------
    def reset_camera_state(self, camera_id: str) -> None:
        """Discard per-camera state after a loop cut.

        The recording restarted, so the previous scene-time anchor no longer
        describes this stream and must be read again.
        """
        self._clocks.pop(camera_id, None)
        self._clock_attempts.pop(camera_id, None)
        self._clock_last_try.pop(camera_id, None)
        self.trackers.reset(camera_id)
        self._plate_voters.pop(camera_id, None)
        self._dark_streak.pop(camera_id, None)
        self._dark_cameras.pop(camera_id, None)
        self._dark_skipped.pop(camera_id, None)
        self.stats["dark_cameras"] = len(self._dark_cameras)
        if self.zone_engine is not None:
            self.zone_engine.reset_camera(camera_id)

    def _anchor_clock(self, frame) -> None:
        """Read the burned-in timestamp, then extrapolate until it goes stale.

        Attempts are capped. Reading an overlay costs several OCR passes over
        upscaled crops, and about half the cameras on this grid have no legible
        overlay at all - retrying every frame on those saturates the queue and
        starves detection, which matters far more than scene time. Measured
        without the cap: 83% of frames dropped.

        The anchor is re-read once it has been extrapolated for
        CLOCK_REANCHOR_AFTER_S of stream time, because decoder timing drift
        accumulates and an hours-old anchor times sightings wrongly without
        ever saying so.
        """
        cam = frame.camera_id
        if self._ocr is None:
            return
        existing = self._clocks.get(cam)
        if (existing is not None
                and existing.age_s(frame.pts_ms) < CLOCK_REANCHOR_AFTER_S):
            return
        exhaustive = self.clock_policy == CLOCK_EXHAUSTIVE
        limit = INDEX_CLOCK_ATTEMPT_LIMIT if exhaustive else CLOCK_ATTEMPT_LIMIT
        attempts = self._clock_attempts.get(cam, 0)
        if attempts >= limit:
            return

        if exhaustive:
            # An offline pass over a finite recording has nothing to starve and
            # every reason to succeed, so it never skips - but it spaces its
            # attempts through the recording rather than spending the whole
            # budget on the first frames, where the overlay may be obscured.
            last_try = self._clock_last_try.get(cam)
            if last_try is not None and frame.pts_ms - last_try < INDEX_CLOCK_RETRY_MS:
                return
            self._clock_last_try[cam] = frame.pts_ms
        # Anchoring costs roughly a second of OCR per attempt. Detection is the
        # primary duty and must not queue behind it, so on the live path scene
        # time is enriched opportunistically: attempted only while the pipeline
        # has slack, and skipped whenever frames are backing up. A camera
        # simply anchors a little later instead of the whole pipeline stalling.
        elif self.queue.qsize() > self.queue.maxsize // 4:
            return

        self._clock_attempts[cam] = attempts + 1
        from netra.analytics.scene_clock import read_scene_time
        try:
            anchor = read_scene_time(self._ocr, frame.image, frame.pts_ms, cam)
        except Exception:
            log.debug("scene clock read failed for %s", cam, exc_info=True)
            return

        if anchor:
            self._clocks[cam] = anchor
            # The budget is per anchoring window, not per connection, so a
            # camera that anchors successfully may re-anchor again when this
            # reading in turn goes stale.
            self._clock_attempts[cam] = 0
            self.stats["clocks_anchored"] = len(self._clocks)
        elif self._clock_attempts[cam] >= limit:
            # A failed re-anchor leaves the existing anchor alone: an anchor
            # carrying some drift still times sightings far better than none.
            # The attempt still counts, so a camera whose overlay has become
            # unreadable (night, rain, a moved caption) stops retrying instead
            # of burning OCR on every frame for the rest of the connection.
            log.info("%s has no legible timestamp overlay after %d attempts; "
                     "%s", cam, limit,
                     "keeping the existing anchor despite its age" if existing
                     else "sightings on this camera carry no scene time")

    def _process(self, frame) -> None:
        t0 = time.time()
        img = frame.image
        capability = self.camera_capability.get(frame.camera_id, "vehicle")

        if capability == "degraded":
            return  # corrupt or unusable feed; health monitoring only

        if not self._dark_gate(frame.camera_id):
            return  # feed has gone dark; skipping until the next probe frame

        self._anchor_clock(frame)
        anchor = self._clocks.get(frame.camera_id)
        scene_time = anchor.at(frame.pts_ms) if anchor else None

        classes = None if capability == "person" else list(config.VEHICLE_CLASSES)
        if capability == "person":
            classes = [0]  # COCO person

        # Escalated cameras get the larger input size: they have traffic worth
        # resolving properly, and small distant vehicles are what a 640px pass
        # loses first.
        imgsz = config.TIER2_IMGSZ if frame.dt_s and frame.dt_s < 0.5 \
            else config.TIER1_IMGSZ

        results = self._vehicle_model.predict(
            img, device=config.DEVICE, verbose=False,
            conf=config.CONF_THRESHOLD, imgsz=imgsz, classes=classes)

        if not results:
            return
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            self._note_luma(frame.camera_id, img, found=False)
            self.stats["processed"] += 1
            return
        self._note_luma(frame.camera_id, img, found=True)

        detections: list[VehicleDetection] = []
        for box in boxes:
            cls_id = int(box.cls.item())
            name = config.VEHICLE_CLASSES.get(cls_id, "person" if cls_id == 0 else str(cls_id))
            conf = float(box.conf.item())
            x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
            crop = img[max(y1, 0):y2, max(x1, 0):x2]

            det = VehicleDetection(
                camera_id=frame.camera_id, pts_ms=frame.pts_ms,
                wall_time=frame.wall_time, vehicle_class=name,
                confidence=conf, bbox=[x1, y1, x2, y2],
                colour=estimate_colour(crop) if cls_id != 0 else None,
                scene_time=scene_time,
                track_id=None,
                evidence=crop)
            detections.append(det)

        self.stats["vehicles"] += len(detections)

        # Embed in one batch - far cheaper than one call each - but only the
        # vehicles worth embedding. A crop a few dozen pixels tall produces an
        # appearance vector that cannot distinguish one car from another, so
        # embedding it both wastes GPU time and pollutes the gallery with
        # noise that weakens genuine matches. Largest first, capped per frame.
        if self._reid is not None and self._reid.ready and detections:
            worth = [d for d in detections
                     if d.evidence is not None
                     and d.evidence.shape[0] >= REID_MIN_CROP_PX]
            worth.sort(key=lambda d: -(d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1]))
            worth = worth[:REID_MAX_PER_FRAME]
            if worth:
                try:
                    vectors = self._reid.encode([d.evidence for d in worth])
                    for det, vec in zip(worth, vectors):
                        det.embedding = vec.tolist()
                    self.stats["embedded"] += len(worth)
                except Exception:
                    log.exception("embedding failed")

        # Tier 2 only where plate geometry can actually support a read, and
        # only on vehicles large enough to carry a legible plate. OCR is by far
        # the most expensive operation here - roughly 50ms per vehicle - so
        # running it on every detection on a busy junction costs about a second
        # per frame and starves the whole pipeline. Measured before this limit:
        # ~2 frames/second processed against ~25 available, 71% dropped.
        if capability == "anpr" and detections:
            candidates = [d for d in detections
                          if (d.bbox[3] - d.bbox[1]) >= PLATE_MIN_VEHICLE_PX]
            candidates.sort(key=lambda d: -(d.bbox[3] - d.bbox[1]))
            for det in candidates[:PLATE_MAX_PER_FRAME]:
                self._read_plate(img, det)

        # Tracking turns independent detections into vehicle journeys, which
        # is what counting, direction, dwell and zone rules all require.
        tracker = self.trackers.get(frame.camera_id)
        tracker.update(detections, frame.pts_ms)

        # Tracking has now assigned track ids, so the per-frame plate reads
        # taken above can be pooled per vehicle and voted on. A read from one
        # frame is a guess; ten reads of the same track are evidence.
        if capability == "anpr":
            self._vote_plates(frame, tracker, detections)

        if self.zone_engine is not None:
            try:
                h, w = img.shape[:2]
                events = self.zone_engine.evaluate(
                    frame.camera_id, list(tracker.tracks.values()), (w, h))
                if events and self.on_zone_event:
                    for event in events:
                        self.on_zone_event(event, frame)
            except Exception:
                log.exception("zone evaluation failed for %s", frame.camera_id)

        if detections and self.on_vehicles_present:
            self.on_vehicles_present(frame.camera_id)

        for det in detections:
            self.on_detection(det)

        self.stats["processed"] += 1
        self.stats["infer_ms"] = round((time.time() - t0) * 1000, 1)

    # -- dark feeds ----------------------------------------------------------
    def _dark_gate(self, camera_id: str) -> bool:
        """False while this camera is dark and not due for its probe frame.

        A camera marked dark is never abandoned: one frame in every
        DARK_RECHECK_FRAMES goes through the full pass, so dawn, a restored
        illuminator or an uncovered lens brings it back with no operator
        action. Recovery is decided by that frame's own result, in _note_luma.
        """
        if camera_id not in self._dark_cameras:
            return True
        seen = self._dark_skipped.get(camera_id, 0) + 1
        if seen < DARK_RECHECK_FRAMES:
            self._dark_skipped[camera_id] = seen
            self.stats["dark_frames_skipped"] += 1
            return False
        self._dark_skipped[camera_id] = 0
        return True

    def _note_luma(self, camera_id: str, img, found: bool) -> None:
        """Track the dark-frame streak for one camera.

        Darkness alone is not enough to stop looking: a genuinely dark scene
        that still yields detections is a camera doing its job. Only frames
        that are both dark *and* empty count towards the streak, and either
        condition failing clears it and restores the camera.
        """
        if not found and mean_luma(img) < DARK_LUMA_THRESHOLD:
            streak = self._dark_streak.get(camera_id, 0) + 1
            self._dark_streak[camera_id] = streak
            if streak >= DARK_FRAME_LIMIT and camera_id not in self._dark_cameras:
                self._dark_cameras[camera_id] = time.time()
                self._dark_skipped[camera_id] = 0
                log.warning("%s has produced %d dark, empty frames - skipping "
                            "inference, re-testing every %d frames",
                            camera_id, streak, DARK_RECHECK_FRAMES)
        else:
            self._dark_streak.pop(camera_id, None)
            if self._dark_cameras.pop(camera_id, None) is not None:
                self._dark_skipped.pop(camera_id, None)
                log.info("%s is no longer dark - resuming inference", camera_id)
        self.stats["dark_cameras"] = len(self._dark_cameras)

    def dark_cameras(self) -> list[str]:
        """Cameras currently being skipped, for pipeline status."""
        return sorted(self._dark_cameras)

    def _vote_plates(self, frame, tracker, detections: list) -> None:
        """Fold this frame's plate reads into each track's running vote."""
        voter = self._plate_voters.get(frame.camera_id)
        if voter is None:
            from netra.analytics.plate_vote import PlateVoter
            voter = self._plate_voters[frame.camera_id] = PlateVoter()

        for det in detections:
            if det.track_id is None:
                continue
            if det.plate_text:
                voter.add(det.track_id, det.plate_text,
                          det.plate_conf or 0.0, frame.pts_ms)
            result = voter.consensus(det.track_id)
            if result is None:
                continue
            text, conf, voters = result
            if voters < 2:
                # One voter is not a vote. Either the track has a single read,
                # or the reads disagreed on length and one was passed through
                # unvoted. Leave this frame's own read alone rather than
                # presenting a lone OCR guess as a consensus.
                continue
            det.plate_text = text
            det.plate_conf = conf
            det.plate_chars = len(text)
            self.stats["plate_votes"] += 1

        # The tracker expires stale tracks internally; without this the voter
        # would hold reads for vehicles that left the frame long ago.
        voter.retain(tracker.tracks.keys())

    def _read_plate(self, img, det: VehicleDetection) -> None:
        """Localise and read the plate on one vehicle."""
        x1, y1, x2, y2 = det.bbox
        crop = img[max(y1, 0):y2, max(x1, 0):x2]
        if crop.size == 0:
            return

        plate_crop, plate_box = None, None
        if self._plate_model is not None:
            res = self._plate_model.predict(crop, device=config.DEVICE,
                                            verbose=False, conf=0.25, imgsz=320)
            if res and res[0].boxes is not None and len(res[0].boxes) > 0:
                best = max(res[0].boxes, key=lambda b: float(b.conf.item()))
                px1, py1, px2, py2 = (int(v) for v in best.xyxy[0].tolist())
                plate_crop = crop[max(py1, 0):py2, max(px1, 0):px2]
                plate_box = [x1 + px1, y1 + py1, x1 + px2, y1 + py2]
        else:
            # Without a dedicated plate detector, search the lower third of the
            # vehicle, where a rear plate sits.
            h = crop.shape[0]
            plate_crop = crop[int(h * 0.6):, :]

        if plate_crop is None or plate_crop.size == 0 or self._ocr is None:
            return

        text, conf = _run_ocr(self._ocr, plate_crop)
        if not text:
            return
        det.plate_text = text
        det.plate_conf = conf
        det.plate_chars = len(text)
        det.plate_bbox = plate_box
        self.stats["plates"] += 1


# -- OCR backend -------------------------------------------------------------
# Kept behind two small functions so the backend can be swapped without the
# engine caring which library is installed.

def _load_ocr():
    import easyocr
    return easyocr.Reader(["en"], gpu=(config.DEVICE == "cuda"), verbose=False)


def _run_ocr(reader, crop) -> tuple[str | None, float | None]:
    import cv2
    if crop.shape[0] < 12 or crop.shape[1] < 30:
        return None, None
    # Upscale small crops; plates on this grid are frequently under 30px tall.
    if crop.shape[0] < 48:
        scale = 48 / crop.shape[0]
        crop = cv2.resize(crop, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC)
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    grey = cv2.bilateralFilter(grey, 7, 55, 55)

    results = reader.readtext(grey, allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                              detail=1, paragraph=False)
    if not results:
        return None, None
    best = max(results, key=lambda r: r[2])
    text = "".join(ch for ch in best[1].upper() if ch.isalnum())
    if len(text) < 4:
        return None, None
    return text, float(best[2])


def _self_check() -> None:
    """Check the dark-feed short-circuit without loading a model or a GPU."""
    engine = InferenceEngine.__new__(InferenceEngine)  # no models, no threads
    engine._dark_streak, engine._dark_cameras, engine._dark_skipped = {}, {}, {}
    engine.stats = {"dark_cameras": 0, "dark_frames_skipped": 0}

    black = np.zeros((240, 320, 3), dtype=np.uint8)
    lit = np.full((240, 320, 3), 90, dtype=np.uint8)
    assert mean_luma(black) == 0.0
    assert mean_luma(lit) > DARK_LUMA_THRESHOLD
    assert mean_luma(None) == 0.0

    # Dark but not yet decided: one frame short of the limit still runs.
    for _ in range(DARK_FRAME_LIMIT - 1):
        engine._note_luma("CAM1", black, found=False)
    assert engine._dark_cameras == {}, engine._dark_streak
    assert engine._dark_gate("CAM1") is True

    engine._note_luma("CAM1", black, found=False)
    assert "CAM1" in engine._dark_cameras
    assert engine.dark_cameras() == ["CAM1"]
    assert engine.stats["dark_cameras"] == 1

    # Skipped until the probe frame, then one frame goes through.
    for _ in range(DARK_RECHECK_FRAMES - 1):
        assert engine._dark_gate("CAM1") is False
    assert engine.stats["dark_frames_skipped"] == DARK_RECHECK_FRAMES - 1
    assert engine._dark_gate("CAM1") is True          # the probe
    assert engine._dark_gate("CAM1") is False         # back to skipping

    # A probe that finds light must restore the camera by itself.
    engine._note_luma("CAM1", lit, found=False)
    assert engine._dark_cameras == {} and engine._dark_streak == {}
    assert engine.stats["dark_cameras"] == 0

    # A dark scene that still yields detections is a camera doing its job and
    # must never be short-circuited, however long it stays dark.
    for _ in range(DARK_FRAME_LIMIT * 2):
        engine._note_luma("CAM2", black, found=True)
    assert "CAM2" not in engine._dark_cameras
    assert engine._dark_gate("CAM2") is True

    # A streak broken before the limit starts again from zero.
    for _ in range(DARK_FRAME_LIMIT - 1):
        engine._note_luma("CAM3", black, found=False)
    engine._note_luma("CAM3", black, found=True)
    engine._note_luma("CAM3", black, found=False)
    assert engine._dark_streak["CAM3"] == 1 and "CAM3" not in engine._dark_cameras

    print("inference self-check passed")


if __name__ == "__main__":
    _self_check()
