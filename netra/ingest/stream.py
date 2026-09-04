"""Per-camera stream workers.

Every rule in the Sentinel integrator checklist is enforced here:

  * RTSP is forced over TCP.
  * All timing derives from PTS (CAP_PROP_POS_MSEC). Arrival time is never
    used, and the declared frame rate is never trusted - two cameras on this
    grid report 0/0.
  * Inter-frame gaps are tolerated; they are not treated as a disconnect.
  * Reconnection uses exponential backoff, 2s rising to a 30s cap.
  * Decoder warnings at join are non-fatal.
  * The loop point is detected as a backwards PTS jump and announced so that
    trackers and re-identification galleries can reset instead of inventing
    impossible motion across the cut.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

# Must be set before cv2 is imported for the option to take effect.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
import cv2  # noqa: E402

from netra import config  # noqa: E402

log = logging.getLogger(__name__)


@dataclass
class Frame:
    """One sampled frame plus the timing context needed downstream."""
    camera_id: str
    image: object
    pts_ms: float
    wall_time: float
    #: seconds of stream time since the previous delivered frame, from PTS.
    #: None immediately after a loop cut, where no delta is meaningful.
    dt_s: float | None
    sequence: int


@dataclass
class CameraState:
    camera_id: str
    connected: bool = False
    last_pts_ms: float = 0.0
    last_frame_wall: float = 0.0
    frames_seen: int = 0
    frames_emitted: int = 0
    reconnects: int = 0
    loop_cuts: int = 0
    last_error: str | None = None
    measured_fps: float = 0.0
    escalated_until: float = 0.0
    fps_window: list = field(default_factory=list)

    @property
    def escalated(self) -> bool:
        return time.time() < self.escalated_until


class CameraWorker(threading.Thread):
    """Reads one camera forever, emitting sampled frames to a sink."""

    def __init__(self, camera_id: str, sink: Callable[[Frame], None],
                 on_discontinuity: Callable[[str], None] | None = None,
                 source_spec=None):
        super().__init__(daemon=True, name=f"ingest-{camera_id}")
        self.camera_id = camera_id
        self.sink = sink
        self.on_discontinuity = on_discontinuity
        # Which adapter reaches this camera. Defaults to RTSP on the grid;
        # a file or HLS spec is accepted unchanged.
        from netra.ingest.sources import spec_for_camera
        self.source_spec = source_spec or spec_for_camera(camera_id)
        self.state = CameraState(camera_id)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    # -- sampling ------------------------------------------------------------
    def _target_interval_s(self) -> float:
        """Tier-1 baseline, tier-2 while the camera is escalated."""
        fps = config.TIER2_FPS if self.state.escalated else config.TIER1_FPS
        return 1.0 / max(fps, 0.01)

    def escalate(self) -> None:
        """Called when something worth a closer look was seen on this camera."""
        self.state.escalated_until = time.time() + config.ESCALATION_HOLD_S

    # -- main loop -----------------------------------------------------------
    def run(self) -> None:
        from netra.ingest.sources import build

        backoff = config.RECONNECT_BASE_S
        while not self._stop.is_set():
            source = None
            try:
                source = build(self.source_spec)
                source.open()

                self.state.connected = True
                self.state.last_error = None
                backoff = config.RECONNECT_BASE_S  # reset only on a real connection
                log.info("%s connected (%s)", self.camera_id, self.source_spec.kind)
                self._read_until_failure(source)

            except Exception as exc:
                self.state.last_error = str(exc)
                log.warning("%s stream error: %s", self.camera_id, exc)
            finally:
                self.state.connected = False
                if source is not None:
                    source.release()

            if self._stop.is_set():
                break
            self.state.reconnects += 1
            log.info("%s reconnecting in %.1fs", self.camera_id, backoff)
            self._stop.wait(backoff)
            backoff = min(backoff * 2, config.RECONNECT_MAX_S)

    def _read_until_failure(self, source) -> None:
        st = self.state
        # PTS of the last frame we actually forwarded, so sampling is measured
        # in stream time rather than wall time.
        last_emit_pts: float | None = None
        st.last_pts_ms = 0.0

        while not self._stop.is_set():
            ok, frame, pts = source.read()
            if not ok:
                # End of stream or a decoder giving up: reconnect. A momentary
                # gap does not reach here - read() blocks through those.
                raise ConnectionError("read failed")

            now = time.time()
            st.frames_seen += 1
            st.last_frame_wall = now

            # Loop cut: the recording restarted. Downstream state must reset.
            if pts + config.LOOP_CUT_THRESHOLD_MS < st.last_pts_ms:
                st.loop_cuts += 1
                log.info("%s loop discontinuity (pts %.0f -> %.0f)",
                         self.camera_id, st.last_pts_ms, pts)
                last_emit_pts = None
                if self.on_discontinuity:
                    self.on_discontinuity(self.camera_id)

            self._measure_fps(pts)
            st.last_pts_ms = pts

            # Sample in stream time, not wall time, so a burst of buffered GOP
            # frames on connect does not flood the pipeline.
            interval_ms = self._target_interval_s() * 1000.0
            if last_emit_pts is not None and (pts - last_emit_pts) < interval_ms:
                continue

            dt_s = None if last_emit_pts is None else (pts - last_emit_pts) / 1000.0
            last_emit_pts = pts
            st.frames_emitted += 1

            try:
                self.sink(Frame(camera_id=self.camera_id, image=frame, pts_ms=pts,
                                wall_time=now, dt_s=dt_s, sequence=st.frames_emitted))
            except Exception:
                # A slow or broken consumer must never kill ingestion.
                log.exception("%s sink raised", self.camera_id)

    def _measure_fps(self, pts: float) -> None:
        """Measure the real delivery rate; the declared value is unreliable."""
        w = self.state.fps_window
        w.append(pts)
        if len(w) > 60:
            w.pop(0)
        span_s = (w[-1] - w[0]) / 1000.0
        if len(w) >= 10 and span_s > 0:
            self.state.measured_fps = (len(w) - 1) / span_s


class IngestSupervisor:
    """Owns one worker per camera and exposes their health."""

    def __init__(self, sink: Callable[[Frame], None],
                 on_discontinuity: Callable[[str], None] | None = None):
        self.sink = sink
        self.on_discontinuity = on_discontinuity
        self.workers: dict[str, CameraWorker] = {}

    def start(self, camera_ids: list[str], source_specs: dict | None = None) -> None:
        source_specs = source_specs or {}
        for cid in camera_ids:
            if cid in self.workers:
                continue
            w = CameraWorker(cid, self.sink, self.on_discontinuity,
                             source_spec=source_specs.get(cid))
            self.workers[cid] = w
            w.start()
            # Stagger connections: 30 simultaneous RTSP handshakes is a burst
            # the gateway has no reason to absorb, and each client gets its own
            # copy of the stream.
            time.sleep(0.35)
        log.info("supervisor started %d workers", len(self.workers))

    def escalate(self, camera_id: str) -> None:
        w = self.workers.get(camera_id)
        if w:
            w.escalate()

    def stop(self) -> None:
        for w in self.workers.values():
            w.stop()
        for w in self.workers.values():
            w.join(timeout=5)

    def health(self) -> list[dict]:
        out = []
        for cid, w in self.workers.items():
            s = w.state
            out.append({
                "camera_id": cid,
                "connected": s.connected,
                "frames_seen": s.frames_seen,
                "frames_emitted": s.frames_emitted,
                "measured_fps": round(s.measured_fps, 2),
                "reconnects": s.reconnects,
                "loop_cuts": s.loop_cuts,
                "escalated": s.escalated,
                "last_error": s.last_error,
                "stale_s": round(time.time() - s.last_frame_wall, 1) if s.last_frame_wall else None,
            })
        return out
