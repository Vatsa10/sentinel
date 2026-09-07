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
    ("GET", "/api/audit", None, 200),
    ("GET", "/api/notify/config", None, 200),
    ("GET", "/api/report?hours=1", None, 200),
    ("GET", "/api/cameras/cam13/hls/status", None, 200),
    ("POST", "/api/watchlist/seed", None, 403),
    ("POST", "/api/watchlist/seed", "operator", 200),
    ("POST", "/api/pipeline/stop", "operator", 403),
    ("POST", "/api/notify/test", "operator", 200),
    ("POST", "/api/storage/prune?dry_run=true", "admin", 200),
]


def call(base, method, path, key):
    req = urllib.request.Request(base + path, method=method)
    if key:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


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
    for method, path, role, expected in CHECKS:
        if role and role not in keys and not kp.exists():
            expected = 200
        if not kp.exists() and expected == 403:
            expected = 200
        got = call(a.base, method, path, keys.get(role) if role else None)
        mark = "ok " if got == expected else "BAD"
        bad += got != expected
        print(f"{mark} {method:4} {path:45} as {role or 'anon':8} -> {got} (want {expected})")
    print(f"{len(CHECKS)-bad}/{len(CHECKS)} passed"); sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
