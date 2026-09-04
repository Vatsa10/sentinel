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
        #: per-camera trackers; tracking is what counting, direction, dwell
        #: and zone rules are all built on
        from netra.analytics.tracking import TrackerRegistry
        self.trackers = TrackerRegistry()
        #: set by the pipeline so zone rules can be evaluated here, where the
        #: tracks live
        self.zone_engine = None
        self.on_zone_event = None

        self.stats = {"submitted": 0, "dropped": 0, "processed": 0,
                      "vehicles": 0, "plates": 0, "embedded": 0,
                      "clocks_anchored": 0, "infer_ms": 0.0}

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
        self.trackers.reset(camera_id)
        if self.zone_engine is not None:
            self.zone_engine.reset_camera(camera_id)

    def _anchor_clock(self, frame) -> None:
        """Read the burned-in timestamp once per connection, then extrapolate.

        Attempts are capped. Reading an overlay costs several OCR passes over
        upscaled crops, and about half the cameras on this grid have no legible
        overlay at all - retrying every frame on those saturates the queue and
        starves detection, which matters far more than scene time. Measured
        without the cap: 83% of frames dropped.
        """
        cam = frame.camera_id
        if self._ocr is None or cam in self._clocks:
            return
        attempts = self._clock_attempts.get(cam, 0)
        if attempts >= CLOCK_ATTEMPT_LIMIT:
            return

        # Anchoring costs roughly a second of OCR per attempt. Detection is the
        # primary duty and must not queue behind it, so scene time is enriched
        # opportunistically: attempted only while the pipeline has slack, and
        # skipped whenever frames are backing up. A camera simply anchors a
        # little later instead of the whole pipeline stalling.
        if self.queue.qsize() > self.queue.maxsize // 4:
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
            self.stats["clocks_anchored"] = len(self._clocks)
        elif self._clock_attempts[cam] >= CLOCK_ATTEMPT_LIMIT:
            log.info("%s has no legible timestamp overlay after %d attempts; "
                     "sightings on this camera carry no scene time",
                     cam, CLOCK_ATTEMPT_LIMIT)

    def _process(self, frame) -> None:
        t0 = time.time()
        img = frame.image
        capability = self.camera_capability.get(frame.camera_id, "vehicle")

        if capability == "degraded":
            return  # corrupt or unusable feed; health monitoring only

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
            self.stats["processed"] += 1
            return

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
