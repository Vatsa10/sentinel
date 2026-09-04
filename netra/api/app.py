"""NETRA REST + WebSocket API and operator console."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from netra import config
from netra.analytics.route import build_route
from netra.core.db import SessionLocal, init_db
from netra.core.geo import TIME_GROUPS, time_group
from netra.core.models import Alert, AuditLog, Camera, Detection, WatchlistEntry
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


def _audit(action: str, target: str | None = None, detail: dict | None = None,
           actor: str = "operator") -> None:
    with SessionLocal() as db:
        db.add(AuditLog(actor=actor, action=action, target=target, detail=detail))
        db.commit()


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
def onboard(probe: bool = True):
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
            "evidence": d.evidence_path, "bbox": d.bbox,
        } for d in rows]
    return {"total": total, "count": len(items), "items": items}


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
async def add_watchlist(request: Request):
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
def delete_watchlist(entry_id: int):
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
            })
    return out


@app.post("/api/alerts/{alert_id}/acknowledge")
def acknowledge(alert_id: int):
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
def pipeline_start(cameras: str | None = None):
    ids = cameras.split(",") if cameras else None
    PIPELINE.start(ids)
    _audit("pipeline.start", detail={"cameras": ids})
    return PIPELINE.status()


@app.post("/api/pipeline/stop")
def pipeline_stop():
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
def seed_watchlist():
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
    from netra.analytics.reid import similarity
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
                          Detection.embedding.isnot(None)).all())

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
                "distance_km": round(km, 2),
                "elapsed_s": round(secs, 1),
                "plausible": ok,
                "plausibility": why,
                "same_time_group": time_group(det.camera_id) == time_group(query.camera_id),
            })

        scored.sort(key=lambda x: x["similarity"], reverse=True)
        matches = scored[:limit]

        origin = {
            "detection_id": query.id, "camera_id": query.camera_id,
            "camera_name": qcam.name if qcam else None,
            "lat": qcam.lat if qcam else None, "lon": qcam.lon if qcam else None,
            "at": query.wall_time.isoformat(),
            "vehicle_class": query.vehicle_class, "colour": query.colour,
            "evidence": query.evidence_path,
        }

    _audit("vehicle.similar", target=str(detection_id),
           detail={"matches": len(matches)})
    return {
        "query": origin,
        "matches": matches,
        "plausible_matches": [m for m in matches if m["plausible"]],
        "method": "appearance re-identification (ResNet-18 512-d, cosine)",
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
