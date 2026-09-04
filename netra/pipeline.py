"""The running platform: ingest -> inference -> matching -> storage -> alerts.

This is the only place the pieces know about each other. Each component is
independently testable; this module is the wiring.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
from datetime import datetime, timezone

import cv2

from netra import config
from netra.analytics.inference import InferenceEngine
from netra.analytics.matching import score_match
from netra.core.db import SessionLocal
from netra.core.models import Alert, Camera, Detection, WatchlistEntry
from netra.core.notify import NOTIFIER
from netra.ingest.stream import IngestSupervisor

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self):
        self.engine = InferenceEngine(
            on_detection=self._handle_detection,
            on_vehicles_present=self._handle_vehicles_present)
        self.supervisor = IngestSupervisor(
            sink=self.engine.submit,
            on_discontinuity=self._handle_discontinuity)

        # Alerts are pushed to connected consoles from here.
        self.alert_subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

        self._watchlist_cache: list[dict] = []
        self._watchlist_loaded_at = 0.0
        self.running = False
        self.started_at: datetime | None = None

    # -- lifecycle -----------------------------------------------------------
    def start(self, camera_ids: list[str] | None = None,
              source_specs: dict | None = None) -> None:
        """Begin processing. `source_specs` overrides how a camera is reached,
        which is how participant-supplied video files are onboarded alongside
        live grid cameras."""
        if self.running:
            return
        with SessionLocal() as db:
            cams = db.query(Camera).filter(Camera.enabled.is_(True)).all()
            self.engine.camera_capability = {c.id: c.capability for c in cams}
            ids = camera_ids or [c.id for c in cams
                                 if c.capability != "degraded"]

        log.info("starting pipeline over %d cameras", len(ids))
        self.engine.load()
        self.engine.start()
        NOTIFIER.start()
        self.supervisor.start(ids, source_specs)
        self.running = True
        self.started_at = datetime.now(timezone.utc)

    def stop(self) -> None:
        self.supervisor.stop()
        self.engine.stop()
        self.running = False

    # -- callbacks -----------------------------------------------------------
    def _handle_vehicles_present(self, camera_id: str) -> None:
        """Escalate a camera to tier-2 sampling while traffic is present."""
        self.supervisor.escalate(camera_id)

    def _handle_discontinuity(self, camera_id: str) -> None:
        """The recording looped. Any cross-frame state for this camera is void."""
        log.info("%s discontinuity - resetting per-camera state", camera_id)
        self.engine.reset_camera_state(camera_id)

    def _handle_detection(self, det) -> None:
        """Persist a detection, then test it against the watchlist."""
        evidence_path = None
        if det.evidence is not None and det.evidence.size > 0:
            fname = f"{det.camera_id}_{int(det.wall_time * 1000)}_{det.bbox[0]}.jpg"
            path = config.EVIDENCE / fname
            try:
                cv2.imwrite(str(path), det.evidence)
                evidence_path = f"/evidence/{fname}"
            except Exception:
                log.exception("could not write evidence crop")

        row = Detection(
            camera_id=det.camera_id,
            pts_ms=det.pts_ms,
            wall_time=datetime.fromtimestamp(det.wall_time, tz=timezone.utc),
            vehicle_class=det.vehicle_class,
            confidence=det.confidence,
            bbox=det.bbox,
            colour=det.colour,
            plate_text=det.plate_text,
            plate_conf=det.plate_conf,
            plate_chars=det.plate_chars,
            plate_bbox=det.plate_bbox,
            scene_time=det.scene_time,
            embedding=det.embedding,
            evidence_path=evidence_path,
        )

        with SessionLocal() as db:
            db.add(row)
            db.commit()
            db.refresh(row)
            detection_id = row.id

        if det.plate_text:
            self._check_watchlist(detection_id, det)

    # -- watchlist -----------------------------------------------------------
    def _watchlist(self) -> list[dict]:
        """Cached watchlist. Reloaded periodically rather than per detection."""
        import time
        if time.time() - self._watchlist_loaded_at > 30:
            with SessionLocal() as db:
                entries = db.query(WatchlistEntry).filter(
                    WatchlistEntry.active.is_(True)).all()
                self._watchlist_cache = [{
                    "id": e.id, "plate": e.plate, "category": e.category,
                    "severity": e.severity, "vehicle_class": e.vehicle_class,
                    "vehicle_colour": e.vehicle_colour, "case_ref": e.case_ref,
                    "owner_name": e.owner_name, "source_db": e.source_db,
                } for e in entries]
            self._watchlist_loaded_at = time.time()
        return self._watchlist_cache

    def _check_watchlist(self, detection_id: int, det) -> None:
        candidate = {
            "plate_text": det.plate_text,
            "plate_chars": det.plate_chars,
            "vehicle_class": det.vehicle_class,
            "colour": det.colour,
        }
        for entry in self._watchlist():
            result = score_match(candidate, entry)
            if not result.is_alert:
                continue
            self._raise_alert(detection_id, det, entry, result)

    def _raise_alert(self, detection_id: int, det, entry: dict, result) -> None:
        with SessionLocal() as db:
            alert = Alert(
                detection_id=detection_id,
                watchlist_id=entry["id"],
                camera_id=det.camera_id,
                score=result.score,
                match_type=result.match_type,
                reasons=result.reasons,
                severity=entry.get("severity", "medium"),
            )
            db.add(alert)
            db.commit()
            db.refresh(alert)
            payload = {
                "id": alert.id,
                "detection_id": detection_id,
                "camera_id": det.camera_id,
                "plate_observed": det.plate_text,
                "plate_watchlist": entry["plate"],
                "category": entry["category"],
                "severity": entry.get("severity"),
                "case_ref": entry.get("case_ref"),
                "score": result.score,
                "match_type": result.match_type,
                "reasons": result.reasons,
                "at": alert.created_at.isoformat(),
            }

        log.warning("ALERT %s on %s (%s, score %.2f)",
                    entry["plate"], det.camera_id, result.match_type, result.score)
        self._broadcast(payload)
        NOTIFIER.submit(payload)

    # -- push to consoles ----------------------------------------------------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self.alert_subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self.alert_subscribers:
                self.alert_subscribers.remove(q)

    def _broadcast(self, payload: dict) -> None:
        with self._lock:
            subscribers = list(self.alert_subscribers)
        for q in subscribers:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass  # a console that cannot keep up misses alerts, nothing more

    # -- introspection -------------------------------------------------------
    def status(self) -> dict:
        return {
            "running": self.running,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "inference": self.engine.stats,
            "queue_depth": self.engine.queue.qsize(),
            "cameras": self.supervisor.health(),
        }


PIPELINE = Pipeline()
