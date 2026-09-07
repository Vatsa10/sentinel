"""Per-camera health for the console: registry facts + live ingest state."""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func

from netra.core.db import SessionLocal
from netra.core.models import Camera, Detection

STALE_DEGRADED_S = 5.0
STALE_OFFLINE_S = 30.0


def redact_url(url: str) -> str:
    """Strip embedded userinfo (user:pass@) from a URL; pass through anything
    that isn't a URL with a network location (e.g. a local file path used by
    own-feed cameras)."""
    if not url:
        return url
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc or "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def classify(cam: Camera, live: dict | None, dark: set[str]) -> str:
    if cam.capability == "degraded":
        return "degraded"
    if live is None:
        return "not-started"
    stale = live.get("stale_s")
    if not live.get("connected") or stale is None or stale > STALE_OFFLINE_S:
        return "offline"
    if stale > STALE_DEGRADED_S or cam.id in dark:
        return "degraded"
    return "online"


def camera_health(pipeline) -> list[dict]:
    live = {h["camera_id"]: h for h in pipeline.supervisor.health()}
    dark = set(pipeline.engine.dark_cameras() or [])
    with SessionLocal() as db:
        cams = db.query(Camera).order_by(Camera.id).all()
        last = dict(db.query(Detection.camera_id, func.max(Detection.wall_time))
                    .group_by(Detection.camera_id).all())
    out = []
    for cam in cams:
        h = live.get(cam.id)
        seen, emitted = (h or {}).get("frames_seen", 0), (h or {}).get("frames_emitted", 0)
        out.append({
            "camera_id": cam.id, "name": cam.name, "city": cam.city,
            "capability": cam.capability,
            "state": classify(cam, h, dark),
            "fps": (h or {}).get("measured_fps"),
            "stale_s": (h or {}).get("stale_s"),
            # frames_emitted/frames_seen is the ingest sampling ratio (frames
            # kept after rate-limiting to the target tier), not an inference
            # or network drop rate - see stream.py:120-150 (_read_until_failure)
            # where frames outside the sampling interval are simply skipped,
            # never lost. Naming it "dropped_pct" would misrepresent that as
            # loss. Engine-level inference drops belong in pipeline.status()
            # ["inference"]["dropped"] instead.
            "sampled_pct": round(100 * emitted / seen, 1) if seen else None,
            "reconnects": (h or {}).get("reconnects", 0),
            "loop_cuts": (h or {}).get("loop_cuts", 0),
            "escalated": (h or {}).get("escalated", False),
            "codec": getattr(cam, "codec", None),
            "resolution": f"{cam.width}x{cam.height}" if getattr(cam, "width", None) else None,
            "last_detection_at": last[cam.id].isoformat() if last.get(cam.id) else None,
            "last_error": (h or {}).get("last_error"),
            "dark": cam.id in dark,
        })
    return out


def _self_check() -> None:
    from types import SimpleNamespace as NS
    cam = NS(id="c", capability="vehicle")
    assert classify(cam, None, set()) == "not-started"
    assert classify(cam, {"connected": True, "stale_s": 1.0}, set()) == "online"
    assert classify(cam, {"connected": True, "stale_s": 9.0}, set()) == "degraded"
    assert classify(cam, {"connected": True, "stale_s": 1.0}, {"c"}) == "degraded"
    assert classify(cam, {"connected": False, "stale_s": 1.0}, set()) == "offline"
    assert classify(NS(id="d", capability="degraded"), None, set()) == "degraded"

    assert redact_url("rtsp://user:pass@1.2.3.4:8554/stream/c1") == "rtsp://1.2.3.4:8554/stream/c1"
    assert redact_url("http://1.2.3.4:8889/stream/c1/whep") == "http://1.2.3.4:8889/stream/c1/whep"
    assert redact_url("D:/videos/own_feed.mp4") == "D:/videos/own_feed.mp4"
    assert redact_url(None) is None
    assert redact_url("") == ""
    assert "@" not in redact_url("rtsp://a%40b:p%40ss@host:554/x")

    print("health self-check ok")


if __name__ == "__main__":
    _self_check()
