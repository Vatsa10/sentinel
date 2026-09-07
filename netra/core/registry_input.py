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

# Defaults applied only when the caller (netra/api/app.py) is creating a new
# row and the field was not supplied. validate_camera_payload never applies
# these itself, so an update payload that omits a field never resets it.
CREATE_DEFAULTS = {
    "capability": "vehicle",
    "department": "Home Department",
    "enabled": True,
}

_STRING_FIELDS = ("id", "name", "city", "district", "department",
                   "rtsp_url", "hls_url")


def validate_camera_payload(payload: dict) -> dict:
    """Validate one camera row for manual/bulk registration.

    Returns a dict containing ONLY the keys that were actually present (and
    non-empty-string) in `payload`, normalised/validated. It never fills in
    defaults for missing optional fields (capability/department/enabled
    included) and never emits fields such as `codec` or `health` unless the
    caller supplied them — an update payload that omits `codec` must leave
    a camera's detected codec untouched, and the same goes for any other
    field a probe/profile step (not a human) is the real source of truth
    for. Callers decide field defaults for a brand-new row themselves (see
    CREATE_DEFAULTS).

    Raises ValueError with a human-readable message on any invalid field.
    """
    if not isinstance(payload, dict):
        raise ValueError("camera payload must be an object")

    payload = dict(payload)
    for key in _STRING_FIELDS:
        v = payload.get(key)
        if isinstance(v, str):
            payload[key] = v.strip()

    cam_id = payload.get("id")
    if not cam_id or not isinstance(cam_id, str) or not ID_RE.match(cam_id):
        raise ValueError(
            "id is required and must match ^[A-Za-z0-9_-]{2,32}$")

    name = payload.get("name")
    if not name or not isinstance(name, str):
        raise ValueError("name is required")

    out: dict = {"id": cam_id, "name": name}

    def _float_opt(key, lo, hi):
        if key not in payload or payload[key] is None:
            return
        v = payload[key]
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number")
        if not (lo <= v <= hi):
            raise ValueError(f"{key} must be between {lo} and {hi}")
        out[key] = v

    _float_opt("lat", -90, 90)
    _float_opt("lon", -180, 180)

    rtsp_url = payload.get("rtsp_url") or None
    hls_url = payload.get("hls_url") or None
    if not rtsp_url and not hls_url:
        raise ValueError("at least one of rtsp_url/hls_url is required")
    for key, url in (("rtsp_url", rtsp_url), ("hls_url", hls_url)):
        if url:
            if not url.startswith(URL_SCHEMES):
                raise ValueError(
                    f"{key} must start with one of {URL_SCHEMES}")
            out[key] = url

    if "capability" in payload and payload["capability"] is not None:
        capability = payload["capability"]
        if capability not in VALID_CAPABILITIES:
            raise ValueError(
                f"capability must be one of {sorted(VALID_CAPABILITIES)}")
        out["capability"] = capability

    for key in ("width", "height"):
        if key in payload and payload[key] is not None:
            try:
                out[key] = int(payload[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be an integer")

    for key in ("city", "district", "department", "codec", "declared_fps",
                "whep_url", "health"):
        if key in payload and payload[key] not in (None, ""):
            out[key] = payload[key]

    if "enabled" in payload and payload["enabled"] is not None:
        out["enabled"] = bool(payload["enabled"])

    return out


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
    assert "capability" not in out, "no default should be injected by validate"
    assert "department" not in out
    assert "enabled" not in out
    assert "codec" not in out
    assert "health" not in out

    # An update payload that omits codec must not mention codec at all.
    update = {"id": "cam-test1", "name": "Test Camera",
              "rtsp_url": "rtsp://example.com/stream"}
    out2 = validate_camera_payload(update)
    assert "codec" not in out2, "update without codec must not clear it"

    with_codec = dict(good, codec="h264")
    out3 = validate_camera_payload(with_codec)
    assert out3["codec"] == "h264"

    # whitespace stripping
    padded = dict(good, id=" cam-test1 ".strip(), name="  Test Camera  ")
    out4 = validate_camera_payload(padded)
    assert out4["name"] == "Test Camera"

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
