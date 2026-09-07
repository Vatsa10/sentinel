"""Latest annotated frame per camera, for the console's live tiles.

The pipeline already decodes every sampled frame; this keeps the newest one
per camera as a JPEG with detection boxes drawn, so the console can show what
the AI is looking at without a second RTSP connection per viewer.

Encoding happens only while at least one MJPEG client is subscribed to that
camera, so an unwatched grid costs nothing. ponytail: the encode runs on the
inference thread (about 2 ms at 960 px); move it to a worker if infer_ms
rises measurably with many tiles open.
"""
from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from netra import config

_LABEL_FONT = cv2.FONT_HERSHEY_SIMPLEX
_BOX = (0, 107, 255)       # BGR of saffron #FF6B00
_TXT = (255, 255, 255)


def annotate(image, detections: list) -> "np.ndarray":
    """Draw boxes + labels. Returns a resized copy, never touches the input."""
    h, w = image.shape[:2]
    scale = min(1.0, config.LIVE_MAX_EDGE / max(h, w))
    out = cv2.resize(image, (int(w * scale), int(h * scale))) if scale < 1 else image.copy()
    for d in detections:
        x1, y1, x2, y2 = [int(v * scale) for v in d.bbox]
        cv2.rectangle(out, (x1, y1), (x2, y2), _BOX, 2)
        label = d.vehicle_class
        if d.plate_text:
            label += f" {d.plate_text}"
        (tw, th), _ = cv2.getTextSize(label, _LABEL_FONT, 0.5, 1)
        cv2.rectangle(out, (x1, max(0, y1 - th - 6)), (x1 + tw + 6, y1), _BOX, -1)
        cv2.putText(out, label, (x1 + 3, y1 - 4), _LABEL_FONT, 0.5, _TXT, 1, cv2.LINE_AA)
    return out


class LiveFrameStore:
    def __init__(self) -> None:
        self._frames: dict[str, tuple[float, bytes]] = {}
        self._subs: dict[str, int] = {}
        self._lock = threading.Lock()

    def subscribe(self, camera_id: str) -> None:
        with self._lock:
            self._subs[camera_id] = self._subs.get(camera_id, 0) + 1

    def unsubscribe(self, camera_id: str) -> None:
        with self._lock:
            n = self._subs.get(camera_id, 0) - 1
            if n <= 0:
                self._subs.pop(camera_id, None)
                self._frames.pop(camera_id, None)
            else:
                self._subs[camera_id] = n

    def wanted(self, camera_id: str) -> bool:
        return self._subs.get(camera_id, 0) > 0

    def put(self, camera_id: str, image, detections: list, pts_ms: float) -> None:
        if not self.wanted(camera_id):
            return
        ok, buf = cv2.imencode(".jpg", annotate(image, detections),
                               [cv2.IMWRITE_JPEG_QUALITY, config.LIVE_JPEG_QUALITY])
        if ok:
            with self._lock:
                self._frames[camera_id] = (pts_ms, buf.tobytes())

    def get(self, camera_id: str) -> tuple[float, bytes] | None:
        with self._lock:
            return self._frames.get(camera_id)


LIVE_FRAMES = LiveFrameStore()


def _self_check() -> None:
    from types import SimpleNamespace
    store = LiveFrameStore()
    img = np.zeros((1080, 1920, 3), dtype=np.uint8)
    det = SimpleNamespace(bbox=[100, 100, 400, 300], vehicle_class="car",
                          plate_text="GJ01AB1234", confidence=0.9)
    store.put("c1", img, [det], 1000.0)
    assert store.get("c1") is None, "must not encode without a subscriber"
    store.subscribe("c1")
    store.put("c1", img, [det], 1500.0)  # warm up cv2's JPEG codec before timing
    t0 = time.perf_counter()
    store.put("c1", img, [det], 2000.0)
    ms = (time.perf_counter() - t0) * 1000
    pts, jpeg = store.get("c1")
    assert pts == 2000.0 and jpeg[:2] == b"\xff\xd8", "expected a JPEG"
    decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert max(decoded.shape[:2]) <= config.LIVE_MAX_EDGE
    store.unsubscribe("c1")
    assert store.get("c1") is None, "frame dropped when last subscriber leaves"
    print(f"live_frames self-check ok (encode {ms:.1f} ms)")
    assert ms < 25, "encode too slow for the inference thread"


if __name__ == "__main__":
    _self_check()
