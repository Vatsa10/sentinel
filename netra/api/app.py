"""NETRA REST + WebSocket API and operator console."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import threading
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (HTMLResponse, JSONResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from fastapi import Depends, Header

from netra import config
from netra.analytics.loop_index import has_embedding
from netra.analytics.route import build_route
from netra.core import auth
from netra.core.db import SessionLocal, init_db
from netra.core.geo import TIME_GROUPS, time_group
from netra.core.models import (Alert, AuditLog, Camera, Detection,
                               VehicleAttributeRow, WatchlistEntry)
from netra.pipeline import PIPELINE

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("netra.api")

app = FastAPI(title="NETRA", version="1.0",
              description="Networked Evidence, Tracking & Recognition for Analytics")

WEB_DIR = config.ROOT / "netra" / "web"


@app.on_event("startup")
def _startup() -> None:
    init_db()
    log.info("database ready at %s", config.DB_URL)
    if auth.enabled():
        log.info("access control ENABLED (%d keys configured)",
                 len(auth.load_keys()))
    else:
        log.warning("ACCESS CONTROL DISABLED - every caller is treated as admin. "
                    "Run 'python run.py --make-keys' before any shared deployment.")


def _audit(action: str, target: str | None = None, detail: dict | None = None,
           actor: str = "operator") -> None:
    with SessionLocal() as db:
        db.add(AuditLog(actor=actor, action=action, target=target, detail=detail))
        db.commit()


def require(permission: str):
    """Dependency enforcing one permission on an endpoint.

    In open mode (no API keys configured) every caller is admin, so a
    demonstration needs no credential setup. Configuring any key switches the
    whole surface to enforced access.
    """
    def _check(x_api_key: str | None = Header(default=None)) -> auth.Principal:
        principal = auth.resolve(x_api_key)
        if principal is None:
            raise HTTPException(401, "valid X-API-Key header required")
        if not principal.may(permission):
            raise HTTPException(
                403, f"role '{principal.role}' may not {permission}")
        return principal
    return _check


# ---------------------------------------------------------------- registry --
@app.get("/api/cameras")
def list_cameras(capability: str | None = None, city: str | None = None):
    """Model 1 registry: every camera with its geography and capability profile."""
    with SessionLocal() as db:
        q = db.query(Camera)
        if capability:
            q = q.filter(Camera.capability == capability)
        if city:
            q = q.filter(Camera.city == city)
        cams = q.order_by(Camera.id).all()

    health = {h["camera_id"]: h for h in PIPELINE.supervisor.health()}
    return [{
        "id": c.id, "name": c.name,
        "lat": c.lat, "lon": c.lon, "city": c.city, "district": c.district,
        "department": c.department,
        "codec": c.codec, "width": c.width, "height": c.height,
        "declared_fps": c.declared_fps,
        "capability": c.capability, "health": c.health,
        "capability_note": c.capability_note,
        "mean_luma": round(c.mean_luma, 1) if c.mean_luma else None,
        "time_group": time_group(c.id),
        "whep_url": c.whep_url, "hls_url": c.hls_url, "rtsp_url": c.rtsp_url,
        "live": health.get(c.id, {}),
    } for c in cams]


@app.post("/api/cameras/onboard")
def onboard(probe: bool = True, _p=Depends(require("onboard"))):
    """Re-run registry onboarding: fetch catalogue, probe, profile, persist."""
    from netra.core.registry import onboard_all
    cams = onboard_all(probe=probe)
    _audit("registry.onboard", detail={"count": len(cams), "probe": probe})
    return {"onboarded": len(cams)}


@app.get("/api/cameras/gap-analysis")
def gap_analysis():
    """Coverage and infrastructure report derived from measured camera state.

    This is the Model 1 gap analysis. Every figure below is measured from the
    live grid rather than declared, which is the point: a State programme needs
    to know which of its cameras cannot actually deliver what is assumed of them.
    """
    with SessionLocal() as db:
        cams = db.query(Camera).all()

    by_capability: dict[str, list[str]] = {}
    by_city: dict[str, int] = {}
    for c in cams:
        by_capability.setdefault(c.capability, []).append(c.id)
        by_city[c.city or "unknown"] = by_city.get(c.city or "unknown", 0) + 1

    degraded = [{
        "id": c.id, "name": c.name, "city": c.city,
        "reason": c.capability_note or "unspecified",
        "mean_luma": round(c.mean_luma, 1) if c.mean_luma else None,
    } for c in cams if c.capability == "degraded"]

    anpr_capable = [c.id for c in cams if c.capability == "anpr"]
    total = len(cams) or 1

    return {
        "total_cameras": len(cams),
        "by_capability": {k: len(v) for k, v in by_capability.items()},
        "capability_members": by_capability,
        "by_city": by_city,
        "degraded_cameras": degraded,
        "anpr_capable": anpr_capable,
        "anpr_coverage_pct": round(100 * len(anpr_capable) / total, 1),
        "usable_pct": round(100 * sum(
            1 for c in cams if c.capability != "degraded") / total, 1),
        "time_groups": {k: v for k, v in TIME_GROUPS.items()},
        "findings": [
            f"{len(degraded)} of {len(cams)} cameras cannot support video analytics "
            f"in their current state.",
            f"{len(anpr_capable)} cameras have plate geometry adequate for ANPR; "
            f"the remainder are wide-area overview cameras where plate recognition "
            f"is unreliable and vehicle-level analytics apply instead.",
            f"Cross-camera route reconstruction is valid only within a shared "
            f"recording session; {len(TIME_GROUPS)} such groups exist in this grid.",
        ],
    }


#: A snapshot opens an RTSP connection, so it is cached: an operator placing
#: points in the zone editor clicks many times on one camera and must not cost
#: one connection per click. Short enough that the still stays current.
SNAPSHOT_TTL_S = 30.0
SNAPSHOT_TIMEOUT_S = 25.0
_snapshots: dict[str, tuple[float, bytes]] = {}

#: One lock per camera, so concurrent callers for the same camera wait on the
#: grab already running instead of each starting their own. Without it the
#: cache only protects the warm path: five clicks on "Load still frame" before
#: the first returns would be five ffmpeg processes holding five threadpool
#: threads for seventeen seconds apiece, which is how a snapshot request ends
#: up starving /api/pipeline/status. The registry of locks needs its own lock
#: because it is filled lazily from several request threads.
_snapshot_locks: dict[str, threading.Lock] = {}
_snapshot_locks_guard = threading.Lock()


def _snapshot_lock(camera_id: str) -> threading.Lock:
    with _snapshot_locks_guard:
        return _snapshot_locks.setdefault(camera_id, threading.Lock())


def _cached_snapshot(camera_id: str) -> bytes | None:
    import time as _time
    now = _time.time()
    # Evict on read. Nothing else ever removes an entry, so the dict is bounded
    # by the camera set only while camera ids are stable - a churning id space
    # (participant-supplied feeds are onboarded under generated ids) would grow
    # it without limit, holding a full-resolution JPEG per id for the life of
    # the process. Four TTLs is well past any possible reuse and leaves the
    # warm path untouched.
    cutoff = now - SNAPSHOT_TTL_S * 4
    for stale in [k for k, (at, _) in _snapshots.items() if at < cutoff]:
        _snapshots.pop(stale, None)
        # The lock registry is dropped alongside, but only for a camera with
        # no grab in flight: replacing a held lock would let the next caller
        # start a second ffmpeg against the same camera, which is the exact
        # thing the registry exists to prevent.
        with _snapshot_locks_guard:
            lock = _snapshot_locks.get(stale)
            if lock is not None and not lock.locked():
                _snapshot_locks.pop(stale, None)
    hit = _snapshots.get(camera_id)
    if hit and (now - hit[0]) < SNAPSHOT_TTL_S:
        return hit[1]
    return None


def _grab_snapshot(camera_id: str) -> bytes:
    """One JPEG off the camera, bounded in time. Caller holds the camera lock."""
    import os
    import subprocess
    import tempfile
    import time as _time

    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-rtsp_transport", "tcp",
             "-i", config.rtsp_url(camera_id), "-frames:v", "1",
             "-q:v", "4", path, "-y"],
            capture_output=True, timeout=SNAPSHOT_TIMEOUT_S)
        data = open(path, "rb").read() if os.path.exists(path) else b""
    except Exception as exc:                          # timeout, no ffmpeg, ...
        log.warning("snapshot failed for %s: %s", camera_id, exc)
        data = b""
    finally:
        if os.path.exists(path):
            os.unlink(path)

    if len(data) < 1000:
        raise HTTPException(
            503, f"could not grab a frame from {camera_id} within "
                 f"{SNAPSHOT_TIMEOUT_S:.0f}s")
    _snapshots[camera_id] = (_time.time(), data)
    return data


@app.get("/api/cameras/{camera_id}/snapshot")
def camera_snapshot(camera_id: str, refresh: bool = False,
                    _p=Depends(require("read"))):
    """One still frame from a camera, for placing zone rules on.

    Points are stored normalised, so the still only has to show the operator
    the scene; it does not have to match the resolution the pipeline decodes.
    """
    with SessionLocal() as db:
        if not db.get(Camera, camera_id):
            raise HTTPException(404, "camera not found")

    data = None if refresh else _cached_snapshot(camera_id)
    if data is None:
        with _snapshot_lock(camera_id):
            # Re-checked inside the lock: whoever we queued behind has just
            # filled the cache, and using their frame is the whole point of
            # having queued.
            data = _cached_snapshot(camera_id) or _grab_snapshot(camera_id)

    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


# -------------------------------------------------------------- detections --
@app.get("/api/detections")
def list_detections(camera_id: str | None = None, plate: str | None = None,
                    vehicle_class: str | None = None, colour: str | None = None,
                    since_minutes: int | None = None,
                    limit: int = Query(100, le=1000), offset: int = 0):
    with SessionLocal() as db:
        q = db.query(Detection).options(joinedload(Detection.camera))
        if camera_id:
            q = q.filter(Detection.camera_id == camera_id)
        if plate:
            q = q.filter(Detection.plate_text.ilike(f"%{plate}%"))
        if vehicle_class:
            q = q.filter(Detection.vehicle_class == vehicle_class)
        if colour:
            q = q.filter(Detection.colour == colour)
        if since_minutes:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
            q = q.filter(Detection.wall_time >= cutoff)
        total = q.count()
        rows = q.order_by(Detection.wall_time.desc()).offset(offset).limit(limit).all()
        # One extra query for the page rather than a join: attributes are
        # sparse - most detections have none - and a left join would carry the
        # caption text of every row through the main query for the few that do.
        described = _attributes_for([d.id for d in rows], db)

        items = [{
            "id": d.id, "camera_id": d.camera_id,
            "camera_name": d.camera.name if d.camera else None,
            "lat": d.camera.lat if d.camera else None,
            "lon": d.camera.lon if d.camera else None,
            "at": d.wall_time.isoformat(),
            "pts_ms": d.pts_ms,
            "vehicle_class": d.vehicle_class, "confidence": round(d.confidence, 3),
            "colour": d.colour, "plate_text": d.plate_text,
            "plate_conf": round(d.plate_conf, 3) if d.plate_conf else None,
            "plate_chars": d.plate_chars,
            #: how many OCR reads agreed on plate_text. One is a lone guess,
            #: and the console shows the count so the two are not read alike.
            "plate_votes": d.plate_votes,
            "evidence": d.evidence_path, "bbox": d.bbox,
            "track_id": d.track_id,
            "scene_time": d.scene_time.isoformat() if d.scene_time else None,
            #: false means the scene time was anchored on a single overlay
            #: reading nothing ever confirmed; no elapsed-time claim is made
            #: from those rows. See netra/core/timing.py.
            "scene_time_corroborated": bool(d.scene_time_corroborated),
            "attributes": described.get(d.id),
        } for d in rows]
    return {"total": total, "count": len(items), "items": items}


def _attribute_dict(row) -> dict:
    """Serialise one stored description. Provenance travels with it."""
    return {"body_type": row.body_type, "colour": row.colour,
            "tinted_windows": row.tinted_windows, "wheels": row.wheels,
            "roof_rack": row.roof_rack, "markings": row.markings or [],
            "damage": row.damage or [], "description": row.description,
            "raw_caption": row.raw_caption, "model": row.model,
            "confidence": row.confidence, "source": row.source,
            "at": row.created_at.isoformat() if row.created_at else None,
            "note": ATTRIBUTE_NOTE}


#: Attached to every description the API returns. A caption is a description of
#: a crop, and the difference between that and an identification is the whole
#: honesty position of this platform.
ATTRIBUTE_NOTE = ("A vision-language description of the evidence crop, for "
                  "search and for an operator to read. It describes what the "
                  "vehicle looks like; it does not identify the vehicle.")


def _attributes_for(detection_ids: list[int], db) -> dict:
    """detection_id -> serialised attributes, for the ids that have any."""
    if not detection_ids:
        return {}
    rows = (db.query(VehicleAttributeRow)
            .filter(VehicleAttributeRow.detection_id.in_(detection_ids)).all())
    return {r.detection_id: _attribute_dict(r) for r in rows}


@app.post("/api/detections/{detection_id}/describe")
def describe_detection(detection_id: int, refresh: bool = False,
                       _p=Depends(require("read"))):
    """Describe one vehicle in words, on request.

    The operator-request tier. Extraction is expensive enough that the pipeline
    only runs it unprompted on alerts, zone events and the largest vehicle on
    an escalated camera; this is how an officer gets a description of any other
    detection - synchronously, because they are waiting for it.
    """
    from netra.analytics import attributes as attrs
    from netra.pipeline import evidence_file, store_attributes

    with SessionLocal() as db:
        det = db.get(Detection, detection_id)
        if det is None:
            raise HTTPException(404, "detection not found")
        existing = db.query(VehicleAttributeRow).filter(
            VehicleAttributeRow.detection_id == detection_id).one_or_none()
        if existing is not None and not refresh:
            return {"detection_id": detection_id, "cached": True,
                    "attributes": _attribute_dict(existing)}
        evidence_path = det.evidence_path

    path = evidence_file(evidence_path)
    if path is None:
        raise HTTPException(
            404, "no evidence crop is stored for this detection, so there is "
                 "nothing to describe")

    result = attrs.describe_image_file(path)
    if not result.raw_caption:
        # The model is unavailable or failed. Saying so is the honest answer;
        # storing an all-unknown row would look like a description that found
        # nothing, which is a different thing entirely.
        raise HTTPException(503, result.description)

    store_attributes(detection_id, result, "operator")
    _audit("detection.describe", target=str(detection_id),
           detail={"confidence": result.confidence})
    with SessionLocal() as db:
        row = db.query(VehicleAttributeRow).filter(
            VehicleAttributeRow.detection_id == detection_id).one()
        return {"detection_id": detection_id, "cached": False,
                "attributes": _attribute_dict(row)}


@app.get("/api/detections/stats")
def detection_stats():
    with SessionLocal() as db:
        total = db.query(func.count(Detection.id)).scalar() or 0
        with_plate = db.query(func.count(Detection.id)).filter(
            Detection.plate_text.isnot(None)).scalar() or 0
        by_class = dict(db.query(Detection.vehicle_class,
                                 func.count(Detection.id))
                        .group_by(Detection.vehicle_class).all())
        by_camera = dict(db.query(Detection.camera_id, func.count(Detection.id))
                         .group_by(Detection.camera_id)
                         .order_by(func.count(Detection.id).desc()).limit(15).all())
        alerts = db.query(func.count(Alert.id)).scalar() or 0
    return {"total_detections": total, "with_plate": with_plate,
            "plate_rate_pct": round(100 * with_plate / total, 1) if total else 0.0,
            "by_class": by_class, "top_cameras": by_camera,
            "total_alerts": alerts}


# ------------------------------------------------------------------ route --
@app.get("/api/route")
def vehicle_route(plate: str = Query(..., min_length=3)):
    """Trace one vehicle across the integrated network."""
    with SessionLocal() as db:
        rows = (db.query(Detection).options(joinedload(Detection.camera))
                .filter(Detection.plate_text.isnot(None)).all())
        route = build_route(rows, plate)
    _audit("route.query", target=plate, detail={"hops": len(route.hops)})
    return route.to_dict()


# -------------------------------------------------------------- watchlist --
@app.get("/api/watchlist")
def list_watchlist():
    with SessionLocal() as db:
        rows = db.query(WatchlistEntry).order_by(WatchlistEntry.id.desc()).all()
        return [{
            "id": e.id, "plate": e.plate, "category": e.category,
            "severity": e.severity, "owner_name": e.owner_name,
            "vehicle_make": e.vehicle_make, "vehicle_colour": e.vehicle_colour,
            "vehicle_class": e.vehicle_class, "case_ref": e.case_ref,
            "source_db": e.source_db, "notes": e.notes, "active": e.active,
        } for e in rows]


@app.post("/api/watchlist")
async def add_watchlist(request: Request, _p=Depends(require("watchlist"))):
    body = await request.json()
    if not body.get("plate"):
        raise HTTPException(400, "plate is required")
    with SessionLocal() as db:
        entry = WatchlistEntry(
            plate=body["plate"].upper().replace(" ", ""),
            category=body.get("category", "suspect"),
            severity=body.get("severity", "medium"),
            owner_name=body.get("owner_name"),
            vehicle_make=body.get("vehicle_make"),
            vehicle_colour=body.get("vehicle_colour"),
            vehicle_class=body.get("vehicle_class"),
            case_ref=body.get("case_ref"),
            source_db=body.get("source_db", "MANUAL"),
            notes=body.get("notes"))
        db.add(entry)
        db.commit()
        db.refresh(entry)
        _audit("watchlist.add", target=entry.plate)
        return {"id": entry.id, "plate": entry.plate}


@app.delete("/api/watchlist/{entry_id}")
def delete_watchlist(entry_id: int, _p=Depends(require("watchlist"))):
    with SessionLocal() as db:
        e = db.get(WatchlistEntry, entry_id)
        if not e:
            raise HTTPException(404, "not found")
        db.delete(e)
        db.commit()
    _audit("watchlist.delete", target=str(entry_id))
    return {"deleted": entry_id}


# ----------------------------------------------------------------- alerts --
@app.get("/api/alerts")
def list_alerts(limit: int = Query(50, le=500), acknowledged: bool | None = None):
    with SessionLocal() as db:
        q = db.query(Alert)
        if acknowledged is not None:
            q = q.filter(Alert.acknowledged.is_(acknowledged))
        rows = q.order_by(Alert.created_at.desc()).limit(limit).all()
        described = _attributes_for([a.detection_id for a in rows], db)
        out = []
        for a in rows:
            det = db.get(Detection, a.detection_id)
            wl = db.get(WatchlistEntry, a.watchlist_id)
            cam = db.get(Camera, a.camera_id)
            out.append({
                "id": a.id, "at": a.created_at.isoformat(),
                "camera_id": a.camera_id,
                "camera_name": cam.name if cam else None,
                "lat": cam.lat if cam else None, "lon": cam.lon if cam else None,
                "score": a.score, "match_type": a.match_type,
                "reasons": a.reasons, "severity": a.severity,
                "acknowledged": a.acknowledged,
                "plate_observed": det.plate_text if det else None,
                "plate_watchlist": wl.plate if wl else None,
                "category": wl.category if wl else None,
                "case_ref": wl.case_ref if wl else None,
                "evidence": det.evidence_path if det else None,
                "detection_id": a.detection_id,
                # Present where the alert path already produced one; the card
                # offers a Describe button where it has not.
                "attributes": described.get(a.detection_id),
            })
    return out


@app.post("/api/alerts/{alert_id}/acknowledge")
def acknowledge(alert_id: int, _p=Depends(require("acknowledge"))):
    with SessionLocal() as db:
        a = db.get(Alert, alert_id)
        if not a:
            raise HTTPException(404, "not found")
        a.acknowledged = True
        db.commit()
    _audit("alert.acknowledge", target=str(alert_id))
    return {"acknowledged": alert_id}


@app.websocket("/ws/alerts")
async def alert_socket(ws: WebSocket):
    await ws.accept()
    q = PIPELINE.subscribe()
    try:
        while True:
            try:
                payload = q.get_nowait()
                await ws.send_json(payload)
            except Exception:
                await asyncio.sleep(0.25)
                # keepalive so proxies do not drop an idle console
                await ws.send_json({"type": "ping"})
                await asyncio.sleep(4.75)
    except WebSocketDisconnect:
        pass
    finally:
        PIPELINE.unsubscribe(q)


# --------------------------------------------------------------- pipeline --
@app.post("/api/pipeline/start")
def pipeline_start(cameras: str | None = None, _p=Depends(require("pipeline"))):
    ids = cameras.split(",") if cameras else None
    PIPELINE.start(ids)
    _audit("pipeline.start", detail={"cameras": ids})
    return PIPELINE.status()


@app.post("/api/pipeline/stop")
def pipeline_stop(_p=Depends(require("pipeline"))):
    PIPELINE.stop()
    _audit("pipeline.stop")
    return {"running": False}


@app.get("/api/pipeline/status")
def pipeline_status():
    return PIPELINE.status()


# ----------------------------------------------------------------- export --
@app.get("/api/export/detections.csv")
def export_detections(plate: str | None = None):
    """Output report: detections with timestamps, as the brief requires."""
    with SessionLocal() as db:
        q = db.query(Detection).options(joinedload(Detection.camera))
        if plate:
            q = q.filter(Detection.plate_text.ilike(f"%{plate}%"))
        rows = q.order_by(Detection.wall_time).all()

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["detection_id", "camera_id", "camera_name", "city",
                    "latitude", "longitude", "timestamp_utc", "pts_ms",
                    "vehicle_class", "colour", "plate_text", "plate_confidence",
                    "detection_confidence", "evidence"])
        for d in rows:
            c = d.camera
            w.writerow([d.id, d.camera_id, c.name if c else "", c.city if c else "",
                        c.lat if c else "", c.lon if c else "",
                        d.wall_time.isoformat(), round(d.pts_ms, 1),
                        d.vehicle_class, d.colour or "", d.plate_text or "",
                        round(d.plate_conf, 3) if d.plate_conf else "",
                        round(d.confidence, 3), d.evidence_path or ""])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=netra_detections.csv"})


@app.get("/api/audit")
def audit_log(limit: int = Query(100, le=500)):
    with SessionLocal() as db:
        rows = db.query(AuditLog).order_by(AuditLog.at.desc()).limit(limit).all()
        return [{"at": r.at.isoformat(), "actor": r.actor, "action": r.action,
                 "target": r.target, "detail": r.detail} for r in rows]


# -------------------------------------------------------------------- web --
app.mount("/evidence", StaticFiles(directory=str(config.EVIDENCE)), name="evidence")


@app.get("/", response_class=HTMLResponse)
def console():
    index = WEB_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>NETRA</h1><p>Console not built.</p>")
    return HTMLResponse(index.read_text(encoding="utf-8"))


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# -------------------------------------------------------- watchlist seed --
SAMPLE_WATCHLIST = [
    ("GJ01AB1234", "stolen", "critical", "Silver Swift reported stolen",
     "car", "silver", "FIR/2026/AHM/0417", "VAHAN"),
    ("GJ18XY7788", "wanted", "critical", "Vehicle linked to wanted accused",
     "car", "white", "CR/2026/GNR/1129", "eGujCop"),
    ("GJ03KL4521", "blacklist", "high", "Blacklisted commercial vehicle",
     "truck", "blue", "RTO/BL/2026/338", "VAHAN"),
    ("GJ11MN9090", "suspect", "high", "Suspect vehicle, surveillance request",
     "car", "black", "SW/2026/JND/072", "eGujCop"),
    ("GJ05CD3311", "stolen", "medium", "Two-wheeler theft",
     "motorcycle", "red", "FIR/2026/RJT/2210", "VAHAN"),
    ("GJ27EF8123", "missing", "high", "Vehicle of missing person",
     "car", "white", "MP/2026/AHM/0088", "eGujCop"),
]


@app.post("/api/watchlist/seed")
def seed_watchlist(_p=Depends(require("watchlist"))):
    """Load a representative watchlist for demonstration.

    Participants are expected to supply their own watchlist data; these records
    are synthetic and shaped to match VAHAN and eGujCop field structure.
    """
    added = 0
    with SessionLocal() as db:
        for plate, cat, sev, note, vclass, colour, case, src in SAMPLE_WATCHLIST:
            exists = db.query(WatchlistEntry).filter(
                WatchlistEntry.plate == plate).first()
            if exists:
                continue
            db.add(WatchlistEntry(
                plate=plate, category=cat, severity=sev, notes=note,
                vehicle_class=vclass, vehicle_colour=colour,
                case_ref=case, source_db=src))
            added += 1
        db.commit()
    _audit("watchlist.seed", detail={"added": added})
    return {"added": added}


# ------------------------------------------------- cross-camera appearance --
@app.get("/api/vehicles/{detection_id}/similar")
def similar_vehicles(detection_id: int, limit: int = Query(25, le=100),
                     min_similarity: float = 0.80):
    """Find the same vehicle on other cameras by appearance.

    This is the answer to "trace this vehicle" when no plate is readable, which
    on this grid is the normal case. Results are ranked candidates carrying
    their similarity score, ordered in time, and filtered for space-time
    plausibility - not assertions of identity.
    """
    from netra.analytics.reid import (attribute_agreement, flag_ambiguity,
                                      similarity)
    from netra.core.geo import haversine_km, time_group
    from netra.analytics.matching import spacetime_plausible

    with SessionLocal() as db:
        query = db.get(Detection, detection_id)
        if query is None:
            raise HTTPException(404, "detection not found")
        if not query.embedding:
            raise HTTPException(
                400, "this detection has no appearance embedding")

        qcam = db.get(Camera, query.camera_id)
        others = (db.query(Detection).options(joinedload(Detection.camera))
                  .filter(Detection.id != detection_id,
                          has_embedding()).all())

        query_attrs = _attributes_for([detection_id], db).get(detection_id)

        scored = []
        for det in others:
            sim = similarity(query.embedding, det.embedding)
            if sim < min_similarity:
                continue
            cam = det.camera
            km = 0.0
            if qcam and cam and None not in (qcam.lat, qcam.lon, cam.lat, cam.lon):
                km = haversine_km(qcam.lat, qcam.lon, cam.lat, cam.lon)
            secs = abs((det.wall_time - query.wall_time).total_seconds())

            # Same-camera sightings need no travel check; different cameras do.
            if det.camera_id != query.camera_id and secs > 0:
                ok, why = spacetime_plausible(km, secs)
            else:
                ok, why = True, "same camera"

            scored.append({
                "detection_id": det.id,
                "camera_id": det.camera_id,
                "camera_name": cam.name if cam else None,
                "lat": cam.lat if cam else None,
                "lon": cam.lon if cam else None,
                "at": det.wall_time.isoformat(),
                "vehicle_class": det.vehicle_class,
                "colour": det.colour,
                "plate_text": det.plate_text,
                "evidence": det.evidence_path,
                "similarity": round(sim, 4),
                "presented_similarity": round(sim, 4),
                "attributes": None,
                "attribute_adjustment": None,
                "distance_km": round(km, 2),
                "elapsed_s": round(secs, 1),
                "plausible": ok,
                "plausibility": why,
                "same_time_group": time_group(det.camera_id) == time_group(query.camera_id),
            })

        # Ordered on raw appearance, not on the adjusted figure: the ranking is
        # what appearance evidence says, and a description is only allowed to
        # qualify it. Ambiguity is judged on the same raw scores for the same
        # reason.
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        # Two vehicles that look alike score alike, so where the top results
        # are separated by less than the appearance model can resolve, every
        # one of them is flagged. The console needs this to avoid rendering a
        # coin-toss as an identification.
        matches = flag_ambiguity(scored[:limit])

        # The third signal, applied last and only to the candidates appearance
        # has already chosen: it can qualify a match but never create one, and
        # looking attributes up only for the returned page keeps this to one
        # bounded query rather than one over every embedded detection.
        attr_rows = _attributes_for([m["detection_id"] for m in matches], db)
        for m in matches:
            m["attributes"] = attr_rows.get(m["detection_id"])
            agreement = attribute_agreement(query_attrs, m["attributes"])
            if not agreement:
                continue
            # Presented separately from `similarity`, which stays the raw
            # cosine: an operator must be able to see what appearance alone
            # said and what the description did to it.
            m["attribute_adjustment"] = agreement
            m["presented_similarity"] = round(
                max(0.0, min(1.0, m["similarity"] + agreement["delta"])), 4)

        origin = {
            "detection_id": query.id, "camera_id": query.camera_id,
            "camera_name": qcam.name if qcam else None,
            "lat": qcam.lat if qcam else None, "lon": qcam.lon if qcam else None,
            "at": query.wall_time.isoformat(),
            "vehicle_class": query.vehicle_class, "colour": query.colour,
            "evidence": query.evidence_path,
            "attributes": query_attrs,
        }

    _audit("vehicle.similar", target=str(detection_id),
           detail={"matches": len(matches)})
    return {
        "query": origin,
        "matches": matches,
        "plausible_matches": [m for m in matches if m["plausible"]],
        "method": ("appearance re-identification (ResNet-18 512-d, cosine), "
                   "corroborated where both sightings carry a "
                   "vision-language description"),
        "ambiguous": any(m.get("ambiguous") for m in matches),
        "note": ("Ranked candidates for operator confirmation, not identification. "
                 "Appearance evidence alone does not establish that two sightings "
                 "are the same vehicle."),
    }


@app.get("/api/vehicles/{detection_id}/track")
def appearance_track(detection_id: int, min_similarity: float = 0.82):
    """Build a movement path for a vehicle using appearance alone.

    Same output shape as /api/route so the console renders either identically,
    whether the vehicle was followed by plate or by appearance.
    """
    data = similar_vehicles(detection_id, limit=100, min_similarity=min_similarity)
    hops = [data["query"]] + [m for m in data["matches"]
                              if m["plausible"] and m["same_time_group"]]
    hops.sort(key=lambda h: h["at"])

    from netra.core.geo import haversine_km
    total_km = 0.0
    for prev, cur in zip(hops, hops[1:]):
        if None in (prev.get("lat"), prev.get("lon"), cur.get("lat"), cur.get("lon")):
            continue
        leg = haversine_km(prev["lat"], prev["lon"], cur["lat"], cur["lon"])
        cur["leg_km"] = round(leg, 2)
        total_km += leg

    return {"query": f"detection #{detection_id}", "hops": hops,
            "hop_count": len(hops), "total_km": round(total_km, 2),
            "rejected": [m for m in data["matches"] if not m["plausible"]],
            "method": data["method"], "note": data["note"]}


# --------------------------------------------------------------- own feed --
@app.post("/api/cameras/own-feed")
async def register_own_feed(request: Request, _p=Depends(require("onboard"))):
    """Onboard a local video file as a camera.

    Submission requires a demonstration on the participant's own footage as
    well as on the Government feed. That is not merely a formality here: the
    Government grid's cameras are wide-area night overviews on which plate
    recognition is not achievable (see docs/feed-recon-findings.md), so
    end-to-end ANPR has to be shown on video where plates are resolvable.

    The file is onboarded through the same adapter interface as a live camera,
    so it runs the identical detection, matching and alerting path.
    """
    import os
    body = await request.json()
    path = body.get("path")
    if not path or not os.path.exists(path):
        raise HTTPException(400, f"video file not found: {path}")

    cam_id = body.get("camera_id") or f"own{abs(hash(path)) % 1000:03d}"
    with SessionLocal() as db:
        cam = db.get(Camera, cam_id) or Camera(id=cam_id)
        cam.name = body.get("name") or f"Own feed - {os.path.basename(path)}"
        cam.city = body.get("city") or "Participant footage"
        cam.district = body.get("district") or "Own feed"
        cam.department = "Participant"
        cam.lat = body.get("lat")
        cam.lon = body.get("lon")
        # Own footage is supplied precisely because plates are resolvable in it.
        cam.capability = body.get("capability", "anpr")
        cam.health = "ok"
        cam.rtsp_url = path
        db.merge(cam)
        db.commit()

    _audit("camera.own_feed", target=cam_id, detail={"path": path})
    return {"camera_id": cam_id, "name": cam.name, "path": path,
            "capability": cam.capability}


@app.post("/api/pipeline/start-own-feed")
def start_own_feed(camera_id: str, loop: bool = True, _p=Depends(require("pipeline"))):
    """Run the pipeline against a registered own-feed camera."""
    from netra.ingest.sources import SourceSpec

    with SessionLocal() as db:
        cam = db.get(Camera, camera_id)
        if cam is None:
            raise HTTPException(404, "camera not registered")
        path = cam.rtsp_url

    spec = SourceSpec(camera_id=camera_id, kind="file", uri=path, loop=loop)
    PIPELINE.start([camera_id], {camera_id: spec})
    _audit("pipeline.start_own_feed", target=camera_id)
    return PIPELINE.status()


# ------------------------------------------------------------- assistant --
@app.post("/api/assistant")
async def assistant(request: Request):
    """Answer an operational question from live platform state."""
    from netra.api.assistant import ask
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "request body must be JSON: {\"question\": \"...\"}")
    if not isinstance(body, dict):
        raise HTTPException(400, "request body must be a JSON object")
    question = (body.get("question") or "").strip()
    result = ask(question)
    _audit("assistant.ask", target=question[:120])
    return result


# ------------------------------------------------------------------ zones --
@app.get("/api/zones")
def list_zones(camera_id: str | None = None):
    """Spatial rules configured on cameras."""
    from netra.core.models import ZoneRule
    with SessionLocal() as db:
        q = db.query(ZoneRule)
        if camera_id:
            q = q.filter(ZoneRule.camera_id == camera_id)
        return [{
            "id": z.id, "camera_id": z.camera_id, "name": z.name,
            "rule": z.rule, "points": z.points, "classes": z.classes or [],
            "severity": z.severity, "dwell_s": z.dwell_s, "active": z.active,
        } for z in q.order_by(ZoneRule.camera_id, ZoneRule.id).all()]


@app.post("/api/zones")
async def create_zone(request: Request, _p=Depends(require("onboard"))):
    """Define a rule. Points are normalised 0-1 so the rule survives a
    resolution change on the source camera."""
    from netra.analytics.zones import RULE_TYPES
    from netra.core.models import ZoneRule

    body = await request.json()
    rule = body.get("rule", "intrusion")
    if rule not in RULE_TYPES:
        raise HTTPException(400, f"rule must be one of {RULE_TYPES}")

    points = body.get("points") or []
    needed = 2 if rule == "crossing" else 3
    if len(points) < needed:
        raise HTTPException(
            400, f"a {rule} rule needs at least {needed} points")
    for p in points:
        if len(p) != 2 or not all(0.0 <= float(v) <= 1.0 for v in p):
            raise HTTPException(400, "points must be normalised [x, y] in 0..1")

    with SessionLocal() as db:
        if not db.get(Camera, body.get("camera_id")):
            raise HTTPException(404, "camera not found")
        z = ZoneRule(
            camera_id=body["camera_id"], name=body.get("name", "Zone"),
            rule=rule, points=points, classes=body.get("classes") or [],
            severity=body.get("severity", "medium"),
            dwell_s=float(body.get("dwell_s", 30.0)))
        db.add(z)
        db.commit()
        db.refresh(z)
        zone_id = z.id

    PIPELINE.reload_zone_rules()
    _audit("zone.create", target=f"{body['camera_id']}:{zone_id}")
    return {"id": zone_id, "camera_id": body["camera_id"], "rule": rule}


@app.delete("/api/zones/{zone_id}")
def delete_zone(zone_id: int, _p=Depends(require("onboard"))):
    from netra.core.models import ZoneRule
    with SessionLocal() as db:
        z = db.get(ZoneRule, zone_id)
        if not z:
            raise HTTPException(404, "not found")
        db.delete(z)
        db.commit()
    PIPELINE.reload_zone_rules()
    _audit("zone.delete", target=str(zone_id))
    return {"deleted": zone_id}


@app.get("/api/zones/events")
def zone_events(limit: int = Query(100, le=500), camera_id: str | None = None):
    from netra.core.models import ZoneEventRow, ZoneRule
    with SessionLocal() as db:
        q = db.query(ZoneEventRow)
        if camera_id:
            q = q.filter(ZoneEventRow.camera_id == camera_id)
        rows = q.order_by(ZoneEventRow.at.desc()).limit(limit).all()
        out = []
        for e in rows:
            rule = db.get(ZoneRule, e.zone_rule_id)
            cam = db.get(Camera, e.camera_id)
            out.append({
                "id": e.id, "at": e.at.isoformat(), "camera_id": e.camera_id,
                "camera_name": cam.name if cam else None,
                "lat": cam.lat if cam else None, "lon": cam.lon if cam else None,
                "zone": rule.name if rule else None, "rule": e.rule,
                "object_class": e.object_class, "direction": e.direction,
                "detail": e.detail, "severity": e.severity,
                "evidence": e.evidence_path, "acknowledged": e.acknowledged,
            })
    return out


# -------------------------------------------------------- traffic analytics --
@app.get("/api/traffic/live")
def traffic_live():
    """Current per-camera counts, class mix, direction split and dwell."""
    return {"cameras": PIPELINE.engine.trackers.stats(),
            "zone_events": PIPELINE.stats.get("zone_events", 0)}


@app.post("/api/traffic/snapshot")
def traffic_snapshot(_p=Depends(require("pipeline"))):
    """Write the current counters into a time bucket for trend reporting."""
    written = PIPELINE.flush_traffic_stats()
    _audit("traffic.snapshot", detail={"cameras": written})
    return {"buckets_written": written}


@app.get("/api/traffic/history")
def traffic_history(camera_id: str | None = None, limit: int = Query(200, le=1000)):
    from netra.core.models import TrafficStat
    with SessionLocal() as db:
        q = db.query(TrafficStat)
        if camera_id:
            q = q.filter(TrafficStat.camera_id == camera_id)
        rows = q.order_by(TrafficStat.bucket_start.desc()).limit(limit).all()
        return [{
            "camera_id": r.camera_id, "at": r.bucket_start.isoformat(),
            # `total` is the traffic during this bucket; `cumulative_total` is
            # the camera's running figure, which spans every replay of a
            # looping recording and is only honest read beside `loops_seen`.
            "total": r.total, "cumulative_total": r.cumulative_total,
            "loops_seen": r.loops_seen, "counts_by_class": r.counts_by_class,
            "directions": r.directions, "mean_dwell_s": r.mean_dwell_s,
        } for r in rows]


# ------------------------------------------------------------- baselines --
#: History read per baseline request. Learning is bounded rather than
#: unbounded: an operator refreshing a dashboard must never pull the whole
#: traffic table and starve the detection threads of the database.
BASELINE_HISTORY_LIMIT = 5000


def _load_baselines(camera_id: str | None, limit: int):
    from netra.analytics import baseline
    from netra.core.models import TrafficStat
    with SessionLocal() as db:
        q = db.query(TrafficStat)
        if camera_id:
            q = q.filter(TrafficStat.camera_id == camera_id)
        rows = q.order_by(TrafficStat.bucket_start.desc()).limit(limit).all()
    return baseline.learn(rows), len(rows)


@app.get("/api/analytics/baselines")
def analytics_baselines(camera_id: str | None = None,
                        limit: int = Query(BASELINE_HISTORY_LIMIT, le=20000),
                        _p=Depends(require("read"))):
    """What each camera normally sees, per hour of the day, in UTC.

    Baselines below the sample floor are returned too, marked insufficient:
    knowing the platform cannot yet judge an hour is itself operational
    information, and hiding those rows would imply coverage that does not exist.
    """
    from netra.analytics import baseline
    learned, sampled = _load_baselines(camera_id, limit)
    items = sorted((b.as_dict() for b in learned.values()),
                   key=lambda b: (b["camera_id"], b["hour"]))
    ready = sum(1 for b in items if b["sufficient"])
    return {"buckets_read": sampled, "min_samples": baseline.MIN_SAMPLES,
            "stdev_floor": baseline.STDEV_FLOOR, "hours_learned": len(items),
            "hours_judgeable": ready, "baselines": items}


@app.get("/api/analytics/anomalies")
def analytics_anomalies(camera_id: str | None = None,
                        limit: int = Query(BASELINE_HISTORY_LIMIT, le=20000),
                        include_normal: bool = False,
                        _p=Depends(require("read"))):
    """Current per-camera readings judged against the learned norms.

    The current reading is the most recent completed traffic bucket for each
    camera, so it is measured the same way the baseline was - comparing a live
    partial count against full-bucket norms would manufacture false quiets.

    A camera whose newest bucket is older than
    baseline.ANOMALY_MAX_BUCKET_AGE_S is reported as `stale` and not judged:
    that reading describes when the feed stopped, not the road now.
    """
    from netra.analytics import baseline
    from netra.core.models import TrafficStat
    learned, sampled = _load_baselines(camera_id, limit)

    with SessionLocal() as db:
        q = db.query(TrafficStat)
        if camera_id:
            q = q.filter(TrafficStat.camera_id == camera_id)
        recent = q.order_by(TrafficStat.bucket_start.desc()).limit(limit).all()

    latest: dict[str, object] = {}
    for r in recent:
        latest.setdefault(r.camera_id, r)   # rows arrive newest first

    found = baseline.detect_anomalies(learned, list(latest.values()),
                                      include_normal=include_normal)
    flagged = [a for a in found if a.anomalous]
    stale = [a for a in found if a.status == "stale"]
    return {"buckets_read": sampled, "cameras_assessed": len(latest),
            "anomalies": len(flagged),
            #: cameras whose newest bucket is too old to be a current reading
            "stale": len(stale),
            "max_bucket_age_s": baseline.ANOMALY_MAX_BUCKET_AGE_S,
            "assessments": [a.as_dict() for a in found]}


# ---------------------------------------------------------------- storage --
@app.get("/api/storage")
def storage(_p=Depends(require("read"))):
    """What the evidence directory and detections table hold, against budget."""
    from netra.core import retention
    return retention.storage_report()


@app.post("/api/storage/prune")
def storage_prune(dry_run: bool = False, _p=Depends(require("manage"))):
    """Bring evidence and detections back inside their configured budgets.

    Guarded by `manage` and audited: this deletes evidence, and who asked for
    that deletion is exactly the kind of thing an enquiry later asks about.
    `dry_run` reports what would go without touching anything.
    """
    from netra.core import retention
    evidence = retention.prune_evidence(dry_run=dry_run)
    detections = retention.prune_detections(dry_run=dry_run)
    _audit("storage.prune", detail={"dry_run": dry_run,
                                    "files_deleted": evidence["deleted"],
                                    "bytes_freed": evidence["bytes_freed"],
                                    "rows_deleted": detections["deleted"],
                                    "retained_protected":
                                        evidence["retained_protected"]})
    return {"evidence": evidence, "detections": detections,
            "storage": retention.storage_report()}


@app.get("/api/analytics/cloned-plates")
def cloned_plates(min_confidence: float = Query(0.6, ge=0.0, le=0.99),
                  limit: int = Query(50, ge=1, le=500)):
    """Registration numbers seen in two places one vehicle could not have reached.

    Read-only analysis over stored detections; every finding carries the
    distance, elapsed time and implied speed behind it so an officer can check
    the claim rather than take it on trust.
    """
    from netra.analytics.cloned_plate import find_clones
    with SessionLocal() as db:
        rows = (db.query(Detection).options(joinedload(Detection.camera))
                .filter(Detection.plate_text.isnot(None)).all())
        findings = find_clones(rows, min_confidence=min_confidence)
    _audit("analytics.cloned_plates", detail={"findings": len(findings)})
    return {
        "findings": [f.to_dict() for f in findings[:limit]],
        "count": len(findings),
        "min_confidence": min_confidence,
        "note": ("Findings are inferred from OCR reads on wide-area cameras and "
                 "are never certain. Only sightings sharing a recording session "
                 "are compared."),
    }


#: Mining is an appearance comparison across a whole indexed recording, so it
#: runs at most once per group unprompted and otherwise only on request. What
#: is recorded here is that a mine *happened*, not what it produced: a group
#: that legitimately yields no journeys is indistinguishable from one never
#: mined if only the output is remembered, and it would be re-mined on every
#: poll for as long as it stayed empty — the starvation class the plan warns
#: about. Per process, so a restart re-mines once and then settles.
_journeys_mined_at: dict[str, float] = {}


@app.get("/api/analytics/journeys")
def mined_journeys(group: str = Query(..., min_length=3, max_length=64),
                   min_similarity: float = Query(0.84, ge=0.5, le=0.99),
                   min_hops: int = Query(2, ge=2, le=10),
                   refresh: bool = False,
                   limit: int = Query(50, ge=1, le=200),
                   _p=Depends(require("read"))):
    """Vehicles that genuinely appear on more than one camera of one recording.

    The grid replays fixed recordings, and the cameras of a time group share
    the clock burnt into their frames, so a chain built on scene time is a real
    journey through the Government's own footage rather than a demonstration.

    Mining always runs at the module's own thresholds and stores the full set;
    `min_hops`, `min_similarity` and `limit` filter what this caller is shown.
    They deliberately do not change what is mined, because the store is shared
    and one narrow request must not shrink what every other reader sees.
    """
    import time as _time

    from netra.analytics.loop_index import (DEFAULT_MIN_SIMILARITY,
                                            MAX_JOURNEYS, exclusion_report,
                                            find_journeys, persist_journeys,
                                            stored_count, stored_journeys)
    from netra.core.geo import TIME_GROUPS

    if group not in TIME_GROUPS:
        raise HTTPException(status_code=400,
                            detail=f"unknown time group; known groups are "
                                   f"{', '.join(sorted(TIME_GROUPS))}")

    held = stored_count(group)
    mined_before = _journeys_mined_at.get(group)
    mined = skipped = False

    if refresh or (not held and mined_before is None):
        report: dict = {}
        journeys = find_journeys(group, min_similarity=DEFAULT_MIN_SIMILARITY,
                                 min_hops=2, limit=MAX_JOURNEYS, report=report)
        persist_journeys(group, journeys,
                         min_similarity=DEFAULT_MIN_SIMILARITY)
        _journeys_mined_at[group] = _time.time()
        held = len(journeys)
        mined = True
    elif not held:
        # Mined already and found nothing, so this is a real answer rather than
        # an empty one, and it is not re-derived on every poll.
        skipped = True

    # Always served from the store, so both paths return the identical shape.
    rows = stored_journeys(group, limit=limit, min_hops=min_hops,
                           min_similarity=min_similarity)
    last_mined = _journeys_mined_at.get(group)

    _audit("analytics.journeys", target=group,
           detail={"journeys": len(rows), "mined": mined})
    return {
        "group": group,
        "cameras": TIME_GROUPS[group],
        "journeys": rows,
        "count": len(rows),
        "stored": held,
        "mined_now": mined,
        "mining_skipped": skipped,
        "last_mined_at": (datetime.fromtimestamp(last_mined, tz=timezone.utc)
                          .isoformat() if last_mined else None),
        #: nothing re-mines on a timer, so there is no next time to report
        "next_mine": "only on request, with refresh=true",
        "mined_at_similarity": DEFAULT_MIN_SIMILARITY,
        "filters_applied": {"min_hops": min_hops,
                            "min_similarity": min_similarity,
                            "applied_by": "filter"},
        "index": exclusion_report(group),
        "note": ("Appearance-based candidate journeys for operator "
                 "confirmation, not identifications. Chained on the clock "
                 "recorded in the video, never on capture time, and never "
                 f"across recording sessions. Mined at similarity "
                 f"{DEFAULT_MIN_SIMILARITY}; your thresholds filter these "
                 "results rather than re-mining. A stricter threshold can "
                 "also change which chains form, so pass refresh=true to "
                 "re-mine. Nothing re-mines on its own once journeys are "
                 "stored, so detections indexed since the mined_at timestamp "
                 "are not represented until a refresh."
                 + (" This group has been mined and produced no journeys: "
                    "that is the answer, not a pending one. Index more "
                    "cameras of this group, or re-mine with refresh=true."
                    if skipped else "")),
    }


@app.get("/api/report", response_class=HTMLResponse)
def output_report(hours: int = Query(24, ge=1, le=720)):
    """Operational output report, printable to PDF from the browser.

    This is the output report the submission asks for: detected vehicles and
    plates with timestamps, watchlist matches with their reasoning, zone
    events, per-camera activity, and the cameras measured as unable to deliver.
    """
    from netra.api.report import build_report
    _audit("report.generate", detail={"hours": hours})
    return HTMLResponse(build_report(hours=hours))
