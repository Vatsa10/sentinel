"""End-to-end verification against the live grid.

Runs the real pipeline on real cameras for a fixed window and reports what it
actually did. This is the evidence that the system works, not a mock.

    python verify.py                 # 60s over the ANPR-capable cameras
    python verify.py --seconds 120 --cameras cam12,cam17,cam18
"""
from __future__ import annotations

import argparse
import logging
import time

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("verify")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=int, default=60)
    p.add_argument("--cameras", default=None,
                   help="comma-separated camera ids (default: ANPR-capable)")
    args = p.parse_args()

    from netra.core.db import SessionLocal, init_db
    from netra.core.models import Camera, Detection, Alert
    from netra.pipeline import PIPELINE

    init_db()

    with SessionLocal() as db:
        if db.query(Camera).count() == 0:
            log.info("registry empty - onboarding first")
            from netra.core.registry import onboard_all
            onboard_all(probe=True)

        if args.cameras:
            ids = args.cameras.split(",")
        else:
            ids = [c.id for c in db.query(Camera)
                   .filter(Camera.capability == "anpr").all()]
            if not ids:
                ids = [c.id for c in db.query(Camera)
                       .filter(Camera.capability != "degraded").limit(8).all()]

        before_det = db.query(Detection).count()
        before_alert = db.query(Alert).count()

    print("=" * 62)
    print(f"NETRA verification run: {len(ids)} cameras, {args.seconds}s")
    print(f"cameras: {', '.join(ids)}")
    print("=" * 62)

    PIPELINE.start(ids)

    t0 = time.time()
    try:
        while time.time() - t0 < args.seconds:
            time.sleep(5)
            st = PIPELINE.status()
            up = sum(1 for c in st["cameras"] if c["connected"])
            inf = st["inference"]
            print(f"  t+{int(time.time()-t0):>3}s  "
                  f"connected {up}/{len(ids)}  "
                  f"frames {inf['submitted']} (dropped {inf['dropped']})  "
                  f"vehicles {inf['vehicles']}  plates {inf['plates']}  "
                  f"queue {st['queue_depth']}")
    except KeyboardInterrupt:
        pass
    finally:
        PIPELINE.stop()

    with SessionLocal() as db:
        after_det = db.query(Detection).count()
        after_alert = db.query(Alert).count()
        with_plate = (db.query(Detection)
                      .filter(Detection.plate_text.isnot(None)).count())
        plates = [d.plate_text for d in db.query(Detection)
                  .filter(Detection.plate_text.isnot(None))
                  .order_by(Detection.id.desc()).limit(25).all()]

    st = PIPELINE.status()
    print("=" * 62)
    print("RESULT")
    print(f"  detections created : {after_det - before_det}")
    print(f"  alerts raised      : {after_alert - before_alert}")
    print(f"  detections w/ plate: {with_plate} (cumulative)")
    print(f"  frames submitted   : {st['inference']['submitted']}")
    print(f"  frames dropped     : {st['inference']['dropped']}")
    print(f"  loop cuts handled  : {sum(c['loop_cuts'] for c in st['cameras'])}")
    print(f"  reconnects         : {sum(c['reconnects'] for c in st['cameras'])}")
    if plates:
        print(f"  recent plate reads : {', '.join(plates[:15])}")
    print("=" * 62)

    for c in st["cameras"]:
        print(f"  {c['camera_id']:<8} connected={c['connected']!s:<5} "
              f"seen={c['frames_seen']:<6} emitted={c['frames_emitted']:<5} "
              f"fps={c['measured_fps']:<6} loops={c['loop_cuts']} "
              f"{c['last_error'] or ''}")

    return 0 if (after_det - before_det) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
