"""Launch NETRA.

    python run.py              start the console on :8080
    python run.py --onboard    profile every camera into the registry, then exit
    python run.py --check      verify environment and grid connectivity
"""
import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")


def check() -> int:
    ok = True
    print("NETRA environment check")
    print("-" * 52)

    try:
        import torch
        cuda = torch.cuda.is_available()
        name = torch.cuda.get_device_name(0) if cuda else "none"
        print(f"  torch      {torch.__version__}  cuda={cuda}  {name}")
        if not cuda:
            print("             WARNING: no GPU, inference will be slow")
    except Exception as e:
        print(f"  torch      FAILED: {e}")
        ok = False

    for mod in ("cv2", "ultralytics", "easyocr", "fastapi", "sqlalchemy"):
        try:
            m = __import__(mod)
            print(f"  {mod:<10} {getattr(m, '__version__', 'ok')}")
        except Exception as e:
            print(f"  {mod:<10} FAILED: {e}")
            ok = False

    try:
        from netra.core.registry import fetch_catalogue
        cams = fetch_catalogue()
        print(f"  catalogue  {len(cams)} cameras reachable")
    except Exception as e:
        print(f"  catalogue  FAILED: {e}")
        ok = False

    import socket
    from netra import config
    for port in (8554, 8889):
        s = socket.socket()
        s.settimeout(6)
        try:
            s.connect((config.GRID_HOST, port))
            print(f"  port {port}  open")
        except Exception as e:
            print(f"  port {port}  BLOCKED: {e}")
            ok = False
        finally:
            s.close()

    print("-" * 52)
    print("READY" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--onboard", action="store_true",
                   help="profile all cameras into the registry and exit")
    p.add_argument("--check", action="store_true", help="verify environment")
    p.add_argument("--no-probe", action="store_true",
                   help="onboard without probing streams (fast)")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args()

    if args.check:
        return check()

    from netra.core.db import init_db
    init_db()

    if args.onboard:
        from netra.core.registry import onboard_all
        cams = onboard_all(probe=not args.no_probe)
        print(f"onboarded {len(cams)} cameras")
        return 0

    import uvicorn
    uvicorn.run("netra.api.app:app", host=args.host, port=args.port,
                log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
