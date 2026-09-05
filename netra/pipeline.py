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
                               VehicleAttributeRow, WatchlistEntry,
                               ZoneEventRow, ZoneRule)
from netra.core.notify import NOTIFIER
from netra.ingest.stream import IngestSupervisor

log = logging.getLogger(__name__)

#: Detections are persisted in batches rather than one transaction each.
WRITE_BATCH_SIZE = 50
WRITE_INTERVAL_S = 1.0

#: How long after an alert a description may still be pushed to the console as
#: a live update. Past this the operator has already read and acted on the
#: alert card, so an arriving caption is noise on the wire; the row is still
#: persisted and the console fetches it on demand.
ATTRIBUTE_BROADCAST_BOUND_S = 3.0

#: How long after an alert a description may still be pushed to the console as
#: a live update. Past this the operator has already read and acted on the
#: alert card, so an arriving caption is noise on the wire; the row is still
#: persisted and the console fetches it on demand.
ATTRIBUTE_BROADCAST_BOUND_S = 3.0


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

        # Counters per camera at the last traffic flush, so each bucket can
        # record the traffic during it rather than the running total.
        self._traffic_last_total: dict[str, int] = {}
        self._traffic_last_counts: dict[str, dict[str, int]] = {}

        # Zone rules are evaluated inside the inference engine, where the
        # tracks live; the pipeline supplies the engine and receives events.
        from netra.analytics.zones import ZoneEngine
        self.zone_engine = ZoneEngine()
        self.engine.zone_engine = self.zone_engine
        self.engine.on_zone_event = self._handle_zone_event
        self._last_traffic_flush = 0.0

        # Vision-language descriptions run on their own daemon thread behind a
        # bounded queue that drops when full. Detection is the primary duty and
        # a caption costs roughly a second of GPU, so this must never be able
        # to apply back-pressure to inference or to the writer: the measured
        # precedent is unbounded overlay OCR, which cost 71% of frames.
        self._attr_queue: queue.Queue = queue.Queue(
            maxsize=config.ATTRIBUTE_QUEUE_SIZE)
        self._attr_stop = threading.Event()
        self._attr_thread: threading.Thread | None = None
        #: camera_id -> monotonic time of its last opportunistic description
        self._attr_last: dict[str, float] = {}
        self.attribute_stats = {"queued": 0, "processed": 0, "dropped": 0,
                                "failed": 0, "broadcast": 0}

        # Vision-language descriptions run on their own daemon thread behind a
        # bounded queue that drops when full. Detection is the primary duty and
        # a caption costs roughly a second of GPU, so this must never be able
        # to apply back-pressure to inference or to the writer: the measured
        # precedent is unbounded overlay OCR, which cost 71% of frames.
        self._attr_queue: queue.Queue = queue.Queue(
            maxsize=config.ATTRIBUTE_QUEUE_SIZE)
        self._attr_stop = threading.Event()
        self._attr_thread: threading.Thread | None = None
        #: camera_id -> monotonic time of its last opportunistic description
        self._attr_last: dict[str, float] = {}
        self.attribute_stats = {"queued": 0, "processed": 0, "dropped": 0,
                                "failed": 0, "broadcast": 0}

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
        if config.ATTRIBUTES_ENABLED:
            self._attr_stop.clear()
            self._attr_thread = threading.Thread(
                target=self._attribute_loop, name="attribute-worker",
                daemon=True)
            self._attr_thread.start()
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
        # Enrichment is abandoned rather than drained: an operator stopping the
        # pipeline is not waiting on a caption.
        self._attr_stop.set()
        if self._attr_thread:
            self._attr_thread.join(timeout=5)
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
            row_id = row.id
            payload = {
                "kind": "zone",
                "id": row_id,
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
        # After the broadcast, for the same reason as on the alert path. A zone
        # event has no detection row to key attributes to - the evidence is a
        # whole frame rather than one vehicle - so the description is pushed to
        # the console if it is ready in time, and not stored.
        self._submit_attributes(None, evidence_path, "zone",
                                alert={"zone_event_id": row_id,
                                       "camera_id": event.camera_id})
        NOTIFIER.submit({**payload, "plate_watchlist": event.zone.name,
                         "plate_observed": event.detail,
                         "match_type": event.rule, "score": 1.0,
                         "reasons": {"zone": {"score": 1.0,
                                              "detail": event.detail}}})

    def _bucket_deltas(self, camera_id: str, cumulative: int,
                       counts: dict) -> tuple[int, dict]:
        """Traffic during this bucket, from the tracker's cumulative counters.

        Both the total and the class breakdown are differenced against the
        previous flush, and - this is the part that went wrong - against the
        *same* previous flush. A tracker recreated mid-run restarts its
        counters, so a restart shows up as a cumulative smaller than the one
        last seen, and the whole of it is taken as this bucket's traffic rather
        than persisting a negative count.

        The class snapshot has to be reset on exactly that condition. Taking
        the whole cumulative as the total while still differencing the classes
        against the larger pre-restart snapshot left every class delta at or
        below zero, so the bucket carried a total with an empty breakdown -
        a row an analyst can only read as traffic of unknown composition.
        """
        previous = self._traffic_last_total.get(camera_id)
        restarted = previous is not None and cumulative < previous
        delta = (cumulative if previous is None or restarted
                 else cumulative - previous)
        self._traffic_last_total[camera_id] = cumulative

        before = {} if restarted else self._traffic_last_counts.get(camera_id, {})
        by_class = {k: v - before.get(k, 0) for k, v in counts.items()
                    if v - before.get(k, 0) > 0}
        self._traffic_last_counts[camera_id] = dict(counts)
        return delta, by_class

    def flush_traffic_stats(self, bucket_seconds: int = 60) -> int:
        """Snapshot per-camera traffic counters into a time bucket.

        `total` is the traffic counted *during this bucket*, obtained by
        differencing the tracker's cumulative counter against the value at the
        previous flush. Writing the cumulative figure here - as this once did -
        made every row larger than the last, because the sandbox replays a
        fixed recording and the counter spans every replay. Baselines learned
        from that would describe uptime, not traffic.

        A bucket with no traffic is still written: "this camera saw nothing"
        is exactly the observation a quiet-road baseline needs, and dropping it
        would teach the baseline that the road is never empty.
        """
        now = datetime.now(timezone.utc)
        written = 0
        with SessionLocal() as db:
            for stats in self.engine.trackers.stats():
                camera_id = stats["camera_id"]
                cumulative = stats["total_counted"]
                delta, by_class = self._bucket_deltas(
                    camera_id, cumulative, stats["counts_by_class"])

                db.add(TrafficStat(
                    camera_id=camera_id, bucket_start=now,
                    bucket_seconds=bucket_seconds,
                    total=delta,
                    cumulative_total=cumulative,
                    loops_seen=stats["loops_seen"],
                    counts_by_class=by_class,
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
                scene_time_corroborated=det.scene_time_corroborated,
                plate_votes=det.plate_votes,
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

        self._queue_escalated_attributes(rows)

    # -- vehicle attributes --------------------------------------------------
    def _queue_escalated_attributes(self, rows: list) -> None:
        """Describe the largest vehicle on each escalated camera, rarely.

        Tiering, again: a camera is escalated because it has traffic worth
        resolving, so it is the one place an unrequested description is worth
        anything - and even there, at most once per
        ATTRIBUTE_ESCALATED_INTERVAL_S, on the biggest crop, which is the only
        one large enough for a captioner to say anything true about.

        Runs on the writer thread but only ever enqueues, so its whole cost is
        a dictionary lookup and a `put_nowait`.
        """
        if not config.ATTRIBUTES_ENABLED or not rows:
            return
        try:
            escalated = set(self.supervisor.scheduling().get("escalated", []))
        except Exception:
            return
        if not escalated:
            return

        now = time.monotonic()
        biggest: dict[str, object] = {}
        for row in rows:
            if row.camera_id not in escalated or not row.evidence_path:
                continue
            if now - self._attr_last.get(row.camera_id, 0.0) < \
                    config.ATTRIBUTE_ESCALATED_INTERVAL_S:
                continue
            best = biggest.get(row.camera_id)
            if best is None or _bbox_area(row.bbox) > _bbox_area(best.bbox):
                biggest[row.camera_id] = row

        for camera_id, row in biggest.items():
            # Stamped on the attempt, not on the completion: a camera whose
            # crop is dropped by a full queue must still wait its interval, or
            # a busy camera would retry on every single flush.
            self._attr_last[camera_id] = now
            self._submit_attributes(row.id, row.evidence_path, "escalated")

    def _submit_attributes(self, detection_id, evidence_path,
                           source: str, alert: dict | None = None) -> bool:
        """Hand a crop to the attribute worker. Never blocks, may drop."""
        if not config.ATTRIBUTES_ENABLED or not evidence_path:
            return False
        job = {"detection_id": detection_id, "evidence_path": evidence_path,
               "source": source, "alert": alert, "at": time.monotonic()}
        try:
            self._attr_queue.put_nowait(job)
        except queue.Full:
            # Dropping is the designed behaviour, but silent dropping is not:
            # an operator seeing no descriptions deserves to find the count.
            self.attribute_stats["dropped"] += 1
            return False
        self.attribute_stats["queued"] += 1
        return True

    def _attribute_loop(self) -> None:
        """Describe queued crops off every other thread, forever."""
        while not self._attr_stop.is_set():
            try:
                job = self._attr_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._describe_job(job)
            except Exception:
                self.attribute_stats["failed"] += 1
                log.exception("attribute extraction failed for detection %s",
                              job.get("detection_id"))

    def _describe_job(self, job: dict) -> None:
        from netra.analytics import attributes as attrs

        path = evidence_file(job["evidence_path"])
        if path is None:
            self.attribute_stats["failed"] += 1
            return
        result = attrs.describe_image_file(path)
        self.attribute_stats["processed"] += 1
        if not result.raw_caption:
            # The extractor degraded rather than described. Nothing is stored:
            # a row saying "unknown" would be indistinguishable from a caption
            # that genuinely found nothing to say.
            self.attribute_stats["failed"] += 1
            return

        if job["detection_id"] is not None:
            store_attributes(job["detection_id"], result, job["source"])

        # A description that arrives while the operator is still looking at the
        # alert is worth pushing; one that arrives later is not, and the
        # console fetches it from the stored row instead.
        if job.get("alert") is not None and \
                time.monotonic() - job["at"] <= ATTRIBUTE_BROADCAST_BOUND_S:
            self.attribute_stats["broadcast"] += 1
            self._broadcast({"kind": "attributes", **job["alert"],
                             "detection_id": job["detection_id"],
                             "description": result.description,
                             "confidence": result.confidence,
                             "raw_caption": result.raw_caption})

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
            alert_id = alert.id
            payload = {
                "id": alert_id,
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

        # Only now: the vehicle already matters, so a description of it is
        # worth the GPU - but the alert has already reached the console and the
        # notifier, so nothing about this can delay it.
        self._submit_attributes(detection_id, _detection_evidence(detection_id),
                                "alert", alert={"alert_id": alert_id,
                                                "camera_id": det.camera_id})

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
            "attributes": {**self.attribute_stats,
                           "enabled": config.ATTRIBUTES_ENABLED,
                           "queue_depth": self._attr_queue.qsize()},
            "cameras": self.supervisor.health(),
        }


def _bbox_area(bbox) -> int:
    """Pixel area of an [x1, y1, x2, y2] box, or 0 if it is not one."""
    try:
        return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])
    except Exception:
        return 0


def evidence_file(evidence_path):
    """Resolve a stored `/evidence/x.jpg` reference to a path on disk.

    Crops are read back from disk rather than carried through the queue: a
    decoded frame is megabytes, and holding a queue of them behind a feature
    that is allowed to be dropped is exactly the memory pressure the bounded
    queue exists to avoid.
    """
    if not evidence_path:
        return None
    name = str(evidence_path).replace("\\", "/").rsplit("/", 1)[-1]
    # Confined to the evidence directory: the reference comes from the
    # database, but the path it becomes must not be able to leave.
    if not name or name in (".", ".."):
        return None
    path = config.EVIDENCE / name
    return path if path.exists() else None


def _detection_evidence(detection_id: int):
    with SessionLocal() as db:
        row = db.get(Detection, detection_id)
        return row.evidence_path if row else None


def store_attributes(detection_id: int, result, source: str) -> dict:
    """Persist one description against its detection, one row per detection."""
    with SessionLocal() as db:
        row = db.query(VehicleAttributeRow).filter(
            VehicleAttributeRow.detection_id == detection_id).one_or_none()
        if row is None:
            row = VehicleAttributeRow(detection_id=detection_id)
            db.add(row)
        row.body_type = result.body_type
        row.colour = result.colour
        row.tinted_windows = result.tinted_windows
        row.wheels = result.wheels
        row.roof_rack = result.roof_rack
        row.markings = result.markings
        row.damage = result.damage
        row.description = result.description
        row.raw_caption = result.raw_caption
        row.model = result.model
        row.confidence = result.confidence
        row.source = source
        db.commit()
    return result.as_dict()


PIPELINE = Pipeline()


def _self_check() -> None:
    """Pin the traffic-bucket differencing, restart included.

    Nothing here touches the database, the GPU or a thread: the arithmetic is
    the part that has been wrong twice, and it is checkable on its own.
    """
    p = Pipeline.__new__(Pipeline)
    p._traffic_last_total, p._traffic_last_counts = {}, {}

    # First bucket: nothing to difference against, so the whole cumulative is
    # this bucket's traffic and the breakdown is the whole breakdown.
    delta, by_class = p._bucket_deltas("cam01", 10, {"car": 7, "truck": 3})
    assert delta == 10 and by_class == {"car": 7, "truck": 3}, (delta, by_class)

    # Steady state: only the increment since the last flush.
    delta, by_class = p._bucket_deltas("cam01", 16, {"car": 11, "truck": 5})
    assert delta == 6 and by_class == {"car": 4, "truck": 2}, (delta, by_class)

    # A bucket in which nothing passed is still coherent, not negative.
    delta, by_class = p._bucket_deltas("cam01", 16, {"car": 11, "truck": 5})
    assert delta == 0 and by_class == {}, (delta, by_class)

    # Tracker restart: the counter drops. The whole of the new cumulative is
    # this bucket's traffic, and the breakdown must be reset with it - a total
    # of 4 against an empty breakdown was the bug.
    delta, by_class = p._bucket_deltas("cam01", 4, {"car": 3, "bus": 1})
    assert delta == 4, delta
    assert by_class == {"car": 3, "bus": 1}, by_class
    assert by_class, "a restart bucket with detections must carry a breakdown"
    assert sum(by_class.values()) <= delta, (by_class, delta)

    # And the flush after a restart differences against the post-restart
    # snapshot, not the pre-restart one.
    delta, by_class = p._bucket_deltas("cam01", 9, {"car": 6, "bus": 3})
    assert delta == 5 and by_class == {"car": 3, "bus": 2}, (delta, by_class)
    assert sum(by_class.values()) <= delta, (by_class, delta)

    print("pipeline self-check passed")


if __name__ == "__main__":
    _self_check()
