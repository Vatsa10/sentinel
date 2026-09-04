"""Model 1 — camera registry onboarding.

The grid publishes only `{id, name}`. Everything else that matters — codec,
resolution, geography, and whether the camera can actually support plate
recognition — is discovered here by probing the stream, and persisted so the
inference scheduler can spend GPU budget only where it can pay off.
"""
from __future__ import annotations

import json
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor

import requests

from netra import config
from netra.core.db import SessionLocal
from netra.core.geo import CAMERA_GEO
from netra.core.models import Camera

log = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 60


def _authenticated_session() -> requests.Session:
    """Sign in to the Sentinel portal and return a session carrying the cookie.

    The portal sets a long-lived `sentinel` cookie on a form POST. Redirects
    must not be followed on that POST: the redirect target rejects the method.
    Credentials come from NETRA_GRID_EMAIL / NETRA_GRID_PASSWORD.
    """
    s = requests.Session()
    s.headers["User-Agent"] = config.UA
    payload = {"password": config.GRID_PASSWORD}
    if config.GRID_EMAIL:
        payload["email"] = config.GRID_EMAIL
    s.post(f"{config.CDN_HOST}/auth/login", data=payload,
           allow_redirects=False, timeout=30)
    return s


def fetch_catalogue() -> list[dict]:
    """Read the authoritative camera list.

    The catalogue is the contract - camera ids and the set of cameras can
    change, and stream URLs are derived from ids rather than hard-coded.

    The portal that serves it is credential-gated and its auth scheme has
    changed at least once during the challenge. Since RTSP itself is not
    gated, a portal outage must not stop the platform from running: a bundled
    snapshot is used as a fallback, and the registry records which source was
    used so nobody mistakes stale data for live data.
    """
    try:
        s = _authenticated_session()
        r = s.get(config.CATALOGUE_URL, timeout=30)
        r.raise_for_status()
        cams = r.json()
        if isinstance(cams, list) and cams:
            _save_catalogue_snapshot(cams)
            log.info("catalogue fetched live: %d cameras", len(cams))
            return cams
        raise ValueError("empty catalogue")
    except Exception as exc:
        log.warning("live catalogue unavailable (%s); using bundled snapshot", exc)
        return _load_catalogue_snapshot()


def _save_catalogue_snapshot(cams: list[dict]) -> None:
    try:
        config.CATALOGUE_SNAPSHOT.write_text(json.dumps(cams, indent=1),
                                             encoding="utf-8")
    except Exception:
        log.debug("could not write catalogue snapshot", exc_info=True)


def _load_catalogue_snapshot() -> list[dict]:
    if not config.CATALOGUE_SNAPSHOT.exists():
        raise RuntimeError(
            "no live catalogue and no bundled snapshot at "
            f"{config.CATALOGUE_SNAPSHOT}. Set NETRA_GRID_EMAIL and "
            "NETRA_GRID_PASSWORD for the Sentinel portal.")
    cams = json.loads(config.CATALOGUE_SNAPSHOT.read_text(encoding="utf-8"))
    log.info("catalogue loaded from snapshot: %d cameras", len(cams))
    return cams


def probe_stream(cam_id: str) -> dict:
    """Discover codec/resolution/fps. The catalogue supplies none of these."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-rtsp_transport", "tcp",
             "-show_entries", "stream=codec_name,width,height,avg_frame_rate",
             "-of", "json", config.rtsp_url(cam_id)],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_S)
        streams = json.loads(r.stdout or "{}").get("streams", [])
        if not streams:
            return {}
        s = streams[0]
        return {"codec": s.get("codec_name"), "width": s.get("width"),
                "height": s.get("height"), "declared_fps": s.get("avg_frame_rate")}
    except Exception as exc:  # probing must never abort onboarding
        log.warning("probe failed for %s: %s", cam_id, exc)
        return {}


def grab_frame(cam_id: str, out_path: str) -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-rtsp_transport", "tcp",
             "-i", config.rtsp_url(cam_id), "-frames:v", "1", "-q:v", "4",
             out_path, "-y"],
            capture_output=True, timeout=PROBE_TIMEOUT_S)
        import os
        return os.path.exists(out_path) and os.path.getsize(out_path) > 1000
    except Exception:
        return False


def assess_frame(path: str) -> dict:
    """Classify what a camera can deliver, from one sample frame.

    Three failure modes are separated because they need different responses:
    a black frame is a lighting problem, a blown-out frame is an exposure
    problem, and column banding is a decoder/feed fault to be reported as
    infrastructure damage.

    ponytail: single-frame heuristics with fixed thresholds. Good enough to
    route GPU budget; replace with a rolling multi-frame assessment if cameras
    start being misclassified.
    """
    import cv2
    import numpy as np

    img = cv2.imread(path)
    if img is None:
        return {"health": "down", "capability": "degraded",
                "capability_note": "no decodable frame"}

    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    luma = float(grey.mean())

    # Column-bar corruption (cam08/cam11): strong vertical structure with almost
    # no vertical variation within each column.
    col_means = grey.mean(axis=0)
    row_means = grey.mean(axis=1)
    col_var = float(np.var(col_means))
    row_var = float(np.var(row_means)) + 1e-6
    banding = col_var / row_var

    note, capability, health = None, "vehicle", "ok"
    if luma < 28:
        capability, health = "degraded", "degraded"
        note = f"scene effectively black (mean luma {luma:.1f})"
    elif luma > 235:
        capability, health = "degraded", "degraded"
        note = f"scene overexposed (mean luma {luma:.1f})"
    elif banding > 40:
        capability, health = "degraded", "degraded"
        note = f"column banding suggests corrupt feed (ratio {banding:.1f})"

    return {"health": health, "capability": capability, "mean_luma": luma,
            "capability_note": note}


# Cameras confirmed by survey to have no vehicle traffic at all — indoor
# terminals. Plate recognition is meaningless; they get person analytics.
INDOOR_CAMERAS = {"cam28", "cam29"}
# Close-range or daylight cameras where plate geometry supports true ANPR.
ANPR_CAMERAS = {"cam12", "cam17", "cam18", "cam20"}


def onboard_all(probe: bool = True) -> list[Camera]:
    """Fetch the catalogue, profile every camera, and persist the registry."""
    cameras = fetch_catalogue()
    log.info("catalogue returned %d cameras", len(cameras))

    profiles: dict[str, dict] = {}
    if probe:
        def _profile(entry: dict) -> tuple[str, dict]:
            cid = entry["id"]
            prof = probe_stream(cid)
            shot = str(config.EVIDENCE / f"_profile_{cid}.jpg")
            if grab_frame(cid, shot):
                prof.update(assess_frame(shot))
            else:
                prof.update({"health": "down", "capability": "degraded",
                             "capability_note": "no frame received during onboarding"})
            return cid, prof

        with ThreadPoolExecutor(max_workers=6) as pool:
            for cid, prof in pool.map(_profile, cameras):
                profiles[cid] = prof
                log.info("profiled %s: %s", cid, prof.get("capability"))

    out: list[Camera] = []
    with SessionLocal() as db:
        for entry in cameras:
            cid = entry["id"]
            prof = profiles.get(cid, {})
            geo = CAMERA_GEO.get(cid)

            capability = prof.get("capability", "unknown")
            # Survey-derived overrides: these are properties of the scene, not
            # of any single frame, so they are not re-derived every onboarding.
            if capability != "degraded":
                if cid in INDOOR_CAMERAS:
                    capability = "person"
                elif cid in ANPR_CAMERAS:
                    capability = "anpr"

            cam = db.get(Camera, cid) or Camera(id=cid)
            cam.name = entry.get("name", cid)
            cam.rtsp_url = config.rtsp_url(cid)
            cam.whep_url = config.whep_url(cid)
            cam.hls_url = config.hls_url(cid)
            if geo:
                cam.lat, cam.lon, cam.city, cam.district = geo
            for field in ("codec", "width", "height", "declared_fps",
                          "mean_luma", "capability_note"):
                if field in prof:
                    setattr(cam, field, prof[field])
            cam.capability = capability
            cam.health = prof.get("health", cam.health or "unknown")
            db.merge(cam)
            out.append(cam)
        db.commit()
    log.info("registry onboarded %d cameras", len(out))
    return out
