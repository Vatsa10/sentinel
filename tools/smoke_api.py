"""Hit every console endpoint as viewer and operator; assert expected status.

    python tools/smoke_api.py [--base http://localhost:8080]
"""
import argparse, json, sys, urllib.request, urllib.error
from pathlib import Path

CHECKS = [  # (method, path, role, expected)
    ("GET", "/api/auth/whoami", None, 200),
    ("GET", "/api/cameras", None, 200),
    ("GET", "/api/cameras/health", None, 200),
    ("GET", "/api/cameras/gap-analysis", None, 200),
    ("GET", "/api/detections?limit=5", None, 200),
    ("GET", "/api/detections/stats", None, 200),
    ("GET", "/api/watchlist", None, 200),
    ("GET", "/api/alerts", None, 200),
    ("GET", "/api/pipeline/status", None, 200),
    ("GET", "/api/zones", None, 200),
    ("GET", "/api/zones/events", None, 200),
    ("GET", "/api/traffic/live", None, 200),
    ("GET", "/api/traffic/history", None, 200),
    ("GET", "/api/analytics/baselines", None, 200),
    ("GET", "/api/analytics/anomalies", None, 200),
    ("GET", "/api/analytics/cloned-plates", None, 200),
    ("GET", "/api/analytics/journeys?group=ahmedabad-13jun", None, 200),
    ("GET", "/api/storage", None, 200),
    ("GET", "/api/notify/config", None, 200),
    ("GET", "/api/report?hours=1", None, 200),
    ("GET", "/api/cameras/cam13/hls/status", None, 200),
    ("POST", "/api/watchlist/seed", None, 403),
    ("POST", "/api/watchlist/seed", "operator", 200),
    ("POST", "/api/pipeline/stop", "operator", 403),
    ("POST", "/api/notify/test", "operator", 200),
    ("POST", "/api/storage/prune?dry_run=true", "admin", 200),
    ("GET", "/api/audit", "admin", 200),
    ("MJPEG", "/api/cameras/cam13/live.mjpg", None, 200),
    ("POST", "/api/cameras", None, {"id": "smoke-cam", "name": "Smoke Cam",
                                     "rtsp_url": "rtsp://example.com/smoke"}, 403),
    ("POST", "/api/cameras", "admin", {"id": "smoke-cam", "name": "Smoke Cam",
                                        "rtsp_url": "rtsp://example.com/smoke"}, 200),
    ("DELETE", "/api/cameras/smoke-cam", None, 403),
    # In enforced mode the anon delete above is rejected and smoke-cam still
    # exists; in open mode (anon == admin) it already deleted smoke-cam, so
    # this re-creates it before the final admin-delete assertion below —
    # keeping that check meaningful (200, not a stale 404) in both modes.
    ("POST", "/api/cameras", "admin", {"id": "smoke-cam", "name": "Smoke Cam",
                                        "rtsp_url": "rtsp://example.com/smoke"}, 200),
    ("DELETE", "/api/cameras/smoke-cam", "admin", None, 200),
]

# Checks whose expected status is not simply "200 everywhere in open mode":
# they depend on data that may or may not exist (an alert id) or need
# opposite expectations in open vs. enforced mode for the same anon call.
# Run separately from CHECKS, after it, so CHECKS keeps its simple shape.


def call(base, method, path, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data,
                                  method="GET" if method == "MJPEG" else method)
    if key:
        req.add_header("X-API-Key", key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if method == "MJPEG":
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                ctype = r.headers.get("Content-Type", "")
                body = r.read(4096)
                if r.status == 200 and ctype.startswith("multipart/x-mixed-replace") and b"--netraframe" in body:
                    return 200
                return 599
        except urllib.error.HTTPError:
            return 599
        except (TimeoutError, OSError):
            # No live frame and the fallback snapshot grab (up to
            # SNAPSHOT_TIMEOUT_S) also didn't complete inside our client
            # timeout - e.g. no pipeline running against this camera, or the
            # grid is unreachable from here. Distinct from a 403/401/500: the
            # gate itself was never reached.
            return 598
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (TimeoutError, OSError):
        return 598


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default="http://localhost:8080")
    a = ap.parse_args()
    keys = {}
    kp = Path("data/api_keys.json")
    if kp.exists():
        for k, v in json.load(open(kp)).items():
            keys[v["role"]] = k
    else:
        print("no data/api_keys.json: open mode, role checks expect 200 everywhere")
    bad = 0
    for check in CHECKS:
        if len(check) == 5:
            method, path, role, body, expected = check
        else:
            method, path, role, expected = check
            body = None
        if role and role not in keys and not kp.exists():
            expected = 200
        if not kp.exists() and expected == 403:
            expected = 200
        got = call(a.base, method, path, keys.get(role) if role else None, body)
        mark = "ok " if got == expected else "BAD"
        bad += got != expected
        print(f"{mark} {method:4} {path:45} as {role or 'anon':8} -> {got} (want {expected})")

    extra_total = extra_bad = 0
    open_mode = not kp.exists()

    def report(method, path, role, got, expected):
        nonlocal extra_total, extra_bad
        extra_total += 1
        extra_bad += got != expected
        mark = "ok " if got == expected else "BAD"
        print(f"{mark} {method:4} {path:45} as {role or 'anon':8} -> {got} (want {expected})")

    # /api/audit requires "manage": anon is admin in open mode (200), a
    # read-only viewer in enforced mode (403, distinct from the 200 already
    # asserted for admin above in CHECKS).
    got = call(a.base, "GET", "/api/audit", None)
    report("GET", "/api/audit", None, got, 200 if open_mode else 403)

    # Zone lifecycle: anon may not create, admin may, and the admin-created
    # zone is cleaned up with an admin delete so the smoke run leaves no
    # zone behind on a repeat run.
    zone_body = {"camera_id": "cam13", "name": "Smoke Zone", "rule": "intrusion",
                "points": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]}
    got = call(a.base, "POST", "/api/zones", None, zone_body)
    report("POST", "/api/zones", None, got, 200 if open_mode else 403)
    if keys.get("admin") or open_mode:
        req = urllib.request.Request(
            a.base + "/api/zones", data=json.dumps(zone_body).encode(),
            method="POST")
        if keys.get("admin"):
            req.add_header("X-API-Key", keys["admin"])
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                created = json.loads(r.read())
                got = r.status
        except urllib.error.HTTPError as e:
            created, got = None, e.code
        report("POST", "/api/zones", "admin", got, 200)
        if created and created.get("id") is not None:
            del_got = call(a.base, "DELETE", f"/api/zones/{created['id']}",
                          keys.get("admin"))
            report("DELETE", f"/api/zones/{created['id']}", "admin", del_got, 200)

    # Alert acknowledge: anon may not, regardless of whether an alert exists
    # yet - if none does the endpoint 404s for admin too, and the anon call
    # is still expected to be refused before it gets that far. Reuse the
    # first alert id found via GET /api/alerts, or skip with a message.
    alerts_got = call(a.base, "GET", "/api/alerts?limit=1", keys.get("viewer"))
    alert_id = None
    if alerts_got == 200:
        try:
            req = urllib.request.Request(a.base + "/api/alerts?limit=1")
            if keys.get("viewer"):
                req.add_header("X-API-Key", keys["viewer"])
            with urllib.request.urlopen(req, timeout=20) as r:
                rows = json.loads(r.read())
            if rows:
                alert_id = rows[0]["id"]
        except Exception:
            alert_id = None
    if alert_id is not None:
        got = call(a.base, "POST", f"/api/alerts/{alert_id}/acknowledge", None)
        report("POST", f"/api/alerts/{alert_id}/acknowledge", None, got,
              200 if open_mode else 403)
    else:
        print("skip POST /api/alerts/{id}/acknowledge anon->403: no alert on record")

    total = len(CHECKS) + extra_total
    bad += extra_bad
    print(f"{total-bad}/{total} passed"); sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
