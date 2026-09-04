"""The running platform: ingest -> inference -> matching -> storage -> alerts.

This is the only place the pieces know about each other. Each component is
independently testable; this module is the wiring.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timezone

import cv2

from netra import config
from netra.analytics.inference import InferenceEngine
from netra.analytics.matching import WatchlistIndex, score_match
from netra.core.db import SessionLocal
from netra.core.models import (Alert, Camera, Detection, TrafficStat,
                               WatchlistEntry, ZoneEventRow, ZoneRule)
from netra.core.notify import NOTIFIER
from netra.ingest.stream import IngestSupervisor

log = logging.getLogger(__name__)

#: Detections are persisted in batches rather than one transaction each.
WRITE_BATCH_SIZE = 50
WRITE_INTERVAL_S = 1.0


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
        self._watchlist_index = WatchlistIndex([])
        self._watchlist_loaded_at = 0.0
        self.running = False
        self.started_at: datetime | None = None

        # Persistence runs off the inference thread.
        self._write_queue: queue.Queue = queue.Queue(maxsize=4000)
        self._stop_writer = threading.Event()
        self._writer: threading.Thread | None = None
        self.stats = {"written": 0, "write_dropped": 0, "zone_events": 0,
                      "traffic_buckets": 0}

        # Zone rules are evaluated inside the inference engine, where the
        # tracks live; the pipeline supplies the engine and receives events.
        from netra.analytics.zones import ZoneEngine
        self.zone_engine = ZoneEngine()
        self.engine.zone_engine = self.zone_engine
        self.engine.on_zone_event = self._handle_zone_event
        self._last_traffic_flush = 0.0

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

        self._load_zone_rules()
        log.info("starting pipeline over %d cameras", len(ids))
        self.engine.load()
        self.engine.start()
        NOTIFIER.start()
        self._stop_writer.clear()
        self._writer = threading.Thread(target=self._writer_loop,
                                        name="detection-writer", daemon=True)
        self._writer.start()
        self.supervisor.start(ids, source_specs)
        self.running = True
        self.started_at = datetime.now(timezone.utc)

    def stop(self) -> None:
        self.supervisor.stop()
        self.engine.stop()
        # Drain whatever is still queued before shutting the writer down.
        self._stop_writer.set()
        if self._writer:
            self._writer.join(timeout=15)
        self.running = False

    def _load_zone_rules(self) -> None:
        """Load zone rules from the database into the evaluation engine."""
        from netra.analytics.zones import Zone
        with SessionLocal() as db:
            rules = db.query(ZoneRule).filter(ZoneRule.active.is_(True)).all()
            by_camera: dict[str, list] = {}
            for r in rules:
                by_camera.setdefault(r.camera_id, []).append(Zone(
                    zone_id=f"{r.camera_id}:{r.id}", camera_id=r.camera_id,
                    name=r.name, rule=r.rule, points=r.points,
                    classes=r.classes or [], severity=r.severity,
                    dwell_s=r.dwell_s, active=r.active))
        for camera_id, zones in by_camera.items():
            self.zone_engine.set_zones(camera_id, zones)
        if by_camera:
            log.info("loaded zone rules for %d cameras", len(by_camera))

    def reload_zone_rules(self) -> None:
        """Called when rules change so a running pipeline picks them up."""
        self._load_zone_rules()

    # -- callbacks -----------------------------------------------------------
    def _handle_vehicles_present(self, camera_id: str) -> None:
        """Escalate a camera to tier-2 sampling while traffic is present."""
        self.supervisor.escalate(camera_id)

    def _handle_discontinuity(self, camera_id: str) -> None:
        """The recording looped. Any cross-frame state for this camera is void."""
        log.info("%s discontinuity - resetting per-camera state", camera_id)
        self.engine.reset_camera_state(camera_id)

    def _handle_zone_event(self, event, frame) -> None:
        """Persist a zone trigger and push it to consoles as an alert.

        Zone rules are how a camera earns its keep when plate recognition is
        impossible, which on this grid is most of them.
        """
        evidence_path = None
        try:
            fname = (f"zone_{event.camera_id}_{int(frame.wall_time * 1000)}"
                     f"_{event.track_id}.jpg")
            cv2.imwrite(str(config.EVIDENCE / fname), frame.image)
            evidence_path = f"/evidence/{fname}"
        except Exception:
            log.exception("could not write zone evidence frame")

        rule_id = int(event.zone.zone_id.split(":")[-1])
        with SessionLocal() as db:
            row = ZoneEventRow(
                zone_rule_id=rule_id, camera_id=event.camera_id,
                rule=event.rule, track_id=event.track_id,
                object_class=event.vehicle_class, direction=event.direction,
                detail=event.detail, severity=event.zone.severity,
                evidence_path=evidence_path)
            db.add(row)
            db.commit()
            db.refresh(row)
            payload = {
                "kind": "zone",
                "id": row.id,
                "camera_id": event.camera_id,
                "zone": event.zone.name,
                "rule": event.rule,
                "severity": event.zone.severity,
                "object_class": event.vehicle_class,
                "direction": event.direction,
                "detail": event.detail,
                "evidence": evidence_path,
                "at": row.at.isoformat(),
            }

        self.stats["zone_events"] += 1
        log.warning("ZONE %s on %s: %s", event.rule, event.camera_id, event.detail)
        self._broadcast(payload)
        NOTIFIER.submit({**payload, "plate_watchlist": event.zone.name,
                         "plate_observed": event.detail,
                         "match_type": event.rule, "score": 1.0,
                         "reasons": {"zone": {"score": 1.0,
                                              "detail": event.detail}}})

    def flush_traffic_stats(self, bucket_seconds: int = 60) -> int:
        """Snapshot per-camera traffic counters into a time bucket."""
        now = datetime.now(timezone.utc)
        written = 0
        with SessionLocal() as db:
            for stats in self.engine.trackers.stats():
                if not stats["total_counted"]:
                    continue
                db.add(TrafficStat(
                    camera_id=stats["camera_id"], bucket_start=now,
                    bucket_seconds=bucket_seconds,
                    total=stats["total_counted"],
                    counts_by_class=stats["counts_by_class"],
                    directions=stats["directions"],
                    mean_dwell_s=stats["mean_dwell_s"]))
                written += 1
            db.commit()
        self.stats["traffic_buckets"] += written
        return written

    def _handle_detection(self, det) -> None:
        """Hand a detection to the writer. Must not touch disk or the database.

        This runs on the inference thread. A busy junction camera produces
        several detections per frame, and doing a JPEG write plus its own
        database transaction for each one starves inference: measured at 76% of
        frames dropped. Persistence happens in batches on a separate thread.
        """
        try:
            self._write_queue.put_nowait(det)
        except queue.Full:
            self.stats["write_dropped"] += 1

    def _writer_loop(self) -> None:
        """Persist detections in batches, off the inference thread."""
        batch: list = []
        last_flush = time.time()
        while not self._stop_writer.is_set() or batch:
            try:
                det = self._write_queue.get(timeout=0.2)
                batch.append(det)
            except queue.Empty:
                pass

            due = (len(batch) >= WRITE_BATCH_SIZE or
                   (batch and time.time() - last_flush >= WRITE_INTERVAL_S))
            if not due:
                if self._stop_writer.is_set() and not batch:
                    break
                continue

            try:
                self._flush(batch)
            except Exception:
                log.exception("failed to persist a batch of %d detections",
                              len(batch))
            batch = []
            last_flush = time.time()

    def _flush(self, batch: list) -> None:
        rows, dets = [], []
        for det in batch:
            evidence_path = None
            if det.evidence is not None and det.evidence.size > 0:
                fname = (f"{det.camera_id}_{int(det.wall_time * 1000)}"
                         f"_{det.bbox[0]}.jpg")
                try:
                    cv2.imwrite(str(config.EVIDENCE / fname), det.evidence)
                    evidence_path = f"/evidence/{fname}"
                except Exception:
                    log.exception("could not write evidence crop")

            rows.append(Detection(
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
                track_id=det.track_id,
                embedding=det.embedding,
                evidence_path=evidence_path,
            ))
            dets.append(det)

        with SessionLocal() as db:
            db.add_all(rows)
            db.commit()
            ids = [r.id for r in rows]
        self.stats["written"] += len(rows)

        # Watchlist checking needs the persisted id, so it follows the flush.
        for detection_id, det in zip(ids, dets):
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
            # Rebuilt with the cache, never separately: an index describing a
            # watchlist that has already changed would silently stop
            # considering entries that were just added.
            self._watchlist_index = WatchlistIndex(self._watchlist_cache)
            self._watchlist_loaded_at = time.time()
        return self._watchlist_cache

    def _check_watchlist(self, detection_id: int, det) -> None:
        candidate = {
            "plate_text": det.plate_text,
            "plate_chars": det.plate_chars,
            "vehicle_class": det.vehicle_class,
            "colour": det.colour,
        }
        # Refresh the cache, then score only the entries whose plate shares a
        # character window with this read. Full scoring still decides each
        # candidate, so partial and confusion-folded matching is unchanged;
        # this only avoids scoring entries that could not match. At 10,000
        # entries that is the difference between 10,000 comparisons per
        # detection and a few dozen, on the thread that also persists rows.
        self._watchlist()
        for entry in self._watchlist_index.candidates(det.plate_text):
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
            "write_queue_depth": self._write_queue.qsize(),
            "scheduling": self.supervisor.scheduling(),
            "traffic": self.engine.trackers.stats(),
            "zone_events": self.stats["zone_events"],
            "watchlist_index": self._watchlist_index.stats(),
            # Cameras the engine has stopped inferring on because their feed
            # went black. Surfaced rather than silent: a control room must be
            # able to see that a camera is no longer being looked at.
            "dark_cameras": self.engine.dark_cameras(),
            "persistence": self.stats,
            "cameras": self.supervisor.health(),
        }


PIPELINE = Pipeline()
