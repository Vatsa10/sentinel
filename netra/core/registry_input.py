"""Pure validation for manual / bulk camera registration (Task A8).

Kept separate from netra/api/app.py so it is testable without HTTP:

    python -m netra.core.registry_input
"""
from __future__ import annotations

import re

ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,32}$")
URL_SCHEMES = ("rtsp://", "rtsps://", "http://", "https://")
# capabilities produced by netra/core/registry.py's onboarding/profiling.
VALID_CAPABILITIES = {"anpr", "vehicle", "person", "degraded", "unknown"}


def validate_camera_payload(payload: dict) -> dict:
    """Validate and normalise one camera row for manual/bulk registration.

    Returns a dict of column values ready to assign onto a `Camera` row.
    Raises ValueError with a human-readable message on any invalid field.
    """
    if not isinstance(payload, dict):
        raise ValueError("camera payload must be an object")

    cam_id = payload.get("id")
    if not cam_id or not isinstance(cam_id, str) or not ID_RE.match(cam_id):
        raise ValueError(
            "id is required and must match ^[A-Za-z0-9_-]{2,32}$")

    name = payload.get("name")
    if not name or not isinstance(name, str):
        raise ValueError("name is required")

    def _float_opt(key, lo, hi):
        v = payload.get(key)
        if v is None:
            return None
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number")
        if not (lo <= v <= hi):
            raise ValueError(f"{key} must be between {lo} and {hi}")
        return v

    lat = _float_opt("lat", -90, 90)
    lon = _float_opt("lon", -180, 180)

    rtsp_url = payload.get("rtsp_url") or None
    hls_url = payload.get("hls_url") or None
    if not rtsp_url and not hls_url:
        raise ValueError("at least one of rtsp_url/hls_url is required")
    for key, url in (("rtsp_url", rtsp_url), ("hls_url", hls_url)):
        if url and not url.startswith(URL_SCHEMES):
            raise ValueError(
                f"{key} must start with one of {URL_SCHEMES}")

    capability = payload.get("capability") or "vehicle"
    if capability not in VALID_CAPABILITIES:
        raise ValueError(
            f"capability must be one of {sorted(VALID_CAPABILITIES)}")

    width = payload.get("width")
    height = payload.get("height")
    for key, v in (("width", width), ("height", height)):
        if v is not None:
            try:
                int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be an integer")

    return {
        "id": cam_id,
        "name": name,
        "lat": lat,
        "lon": lon,
        "city": payload.get("city") or None,
        "district": payload.get("district") or None,
        "department": payload.get("department") or "Home Department",
        "codec": payload.get("codec") or None,
        "width": int(width) if width is not None else None,
        "height": int(height) if height is not None else None,
        "declared_fps": payload.get("declared_fps") or None,
        "rtsp_url": rtsp_url,
        "whep_url": payload.get("whep_url") or None,
        "hls_url": hls_url,
        "capability": capability,
        "health": payload.get("health") or "unknown",
        "enabled": bool(payload.get("enabled", True)),
    }


def _self_check() -> None:
    good = {
        "id": "cam-test1",
        "name": "Test Camera",
        "lat": 23.02,
        "lon": 72.57,
        "rtsp_url": "rtsp://example.com/stream",
    }
    out = validate_camera_payload(good)
    assert out["id"] == "cam-test1"
    assert out["capability"] == "vehicle"
    assert out["department"] == "Home Department"
    assert out["enabled"] is True

    bad_id = dict(good, id="a")
    try:
        validate_camera_payload(bad_id)
        raise AssertionError("bad id should have raised")
    except ValueError:
        pass

    bad_lat = dict(good, lat=999)
    try:
        validate_camera_payload(bad_lat)
        raise AssertionError("bad lat should have raised")
    except ValueError:
        pass

    missing_url = {"id": "cam-test2", "name": "No URL"}
    try:
        validate_camera_payload(missing_url)
        raise AssertionError("missing url should have raised")
    except ValueError:
        pass

    bad_scheme = dict(good, rtsp_url="ftp://example.com/stream")
    try:
        validate_camera_payload(bad_scheme)
        raise AssertionError("bad scheme should have raised")
    except ValueError:
        pass

    print("registry_input self-check: OK")


if __name__ == "__main__":
    _self_check()
