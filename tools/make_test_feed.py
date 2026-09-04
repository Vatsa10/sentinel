"""Generate a validation video with resolvable number plates.

The Government grid cannot exercise the plate-recognition path: its cameras are
wide-area night overviews where a plate spans 10-20 pixels and no plate is
readable (docs/feed-recon-findings.md). Without a feed containing legible
plates there is no way to verify that detection -> ANPR -> watchlist -> alert
actually works end to end.

This builds one. Real vehicles detected in real frames from the grid are
composited with rendered Indian-format plates, so YOLO sees genuine vehicles in
a genuine scene and OCR sees a plate at a realistic size and angle.

This is a TEST FIXTURE, not a demonstration asset. It exists to prove the
pipeline is correct. The submitted own-feed demonstration must use real
footage of real vehicles.

    python tools/make_test_feed.py --plates GJ01AB1234,GJ18XY7788
"""
from __future__ import annotations

import argparse
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netra import config  # noqa: E402


def render_plate(text: str, width: int, height: int) -> np.ndarray:
    """Draw an Indian-style plate: black characters on white, dark border."""
    height = max(height, 18)
    width = max(width, int(height * 4.5))
    plate = np.full((height, width, 3), 235, dtype=np.uint8)
    cv2.rectangle(plate, (0, 0), (width - 1, height - 1), (30, 30, 30), 2)

    scale = 1.0
    thickness = max(1, height // 12)
    for candidate in np.arange(2.5, 0.25, -0.05):
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX,
                                      candidate, thickness)
        if tw <= width * 0.90 and th <= height * 0.68:
            scale = candidate
            break

    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    cv2.putText(plate, text, ((width - tw) // 2, (height + th) // 2),
                cv2.FONT_HERSHEY_SIMPLEX, scale, (20, 20, 20), thickness,
                cv2.LINE_AA)
    return plate


def paste_plate(frame: np.ndarray, box: tuple[int, int, int, int],
                text: str) -> bool:
    """Composite a plate onto the lower-centre of one vehicle box."""
    x1, y1, x2, y2 = box
    vw, vh = x2 - x1, y2 - y1
    if vw < 90 or vh < 70:
        return False  # too small for a plate to be legible anyway

    pw = int(vw * 0.42)
    ph = max(int(pw / 4.5), 16)
    plate = render_plate(text, pw, ph)

    px = x1 + (vw - pw) // 2
    py = y1 + int(vh * 0.74)
    if py + ph >= frame.shape[0] or px + pw >= frame.shape[1] or px < 0 or py < 0:
        return False

    # Blend rather than paste flat, so the plate carries some of the scene's
    # lighting instead of looking pasted on.
    region = frame[py:py + ph, px:px + pw]
    if region.shape[:2] != plate.shape[:2]:
        return False
    frame[py:py + ph, px:px + pw] = cv2.addWeighted(plate, 0.92, region, 0.08, 0)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", default="cam04",
                    help="grid camera to source real frames from")
    ap.add_argument("--plates", default="GJ01AB1234,GJ18XY7788",
                    help="comma-separated plates to composite")
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--out", default="data/own_feed_test.mp4")
    args = ap.parse_args()

    from ultralytics import YOLO

    plates = [p.strip().upper() for p in args.plates.split(",") if p.strip()]
    model = YOLO(config.VEHICLE_MODEL)
    model.to(config.DEVICE)

    cap = cv2.VideoCapture(config.rtsp_url(args.camera), cv2.CAP_FFMPEG)
    if not cap.isOpened():
        print(f"could not open {args.camera}")
        return 1

    writer = None
    written = stamped = 0
    target = args.seconds * args.fps
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    print(f"sourcing real frames from {args.camera}; "
          f"compositing plates {plates}")
    try:
        while written < target:
            ok, frame = cap.read()
            if not ok:
                break

            result = model.predict(frame, device=config.DEVICE, verbose=False,
                                   conf=0.25, imgsz=960,
                                   classes=list(config.VEHICLE_CLASSES))[0]
            boxes = list(result.boxes or [])
            # Largest vehicles first: they are the only ones big enough to
            # carry a plate a reader could resolve.
            boxes.sort(key=lambda b: -(float(b.xyxy[0][2] - b.xyxy[0][0]) *
                                       float(b.xyxy[0][3] - b.xyxy[0][1])))
            for i, box in enumerate(boxes[:len(plates)]):
                xy = tuple(int(v) for v in box.xyxy[0].tolist())
                if paste_plate(frame, xy, plates[i % len(plates)]):
                    stamped += 1

            if writer is None:
                h, w = frame.shape[:2]
                writer = cv2.VideoWriter(args.out,
                                         cv2.VideoWriter_fourcc(*"mp4v"),
                                         args.fps, (w, h))
            writer.write(frame)
            written += 1
            if written % 50 == 0:
                print(f"  {written}/{target} frames, {stamped} plates composited")
    finally:
        cap.release()
        if writer:
            writer.release()

    size_mb = os.path.getsize(args.out) / 1e6 if os.path.exists(args.out) else 0
    print(f"\nwrote {args.out}  ({written} frames, {stamped} plates, {size_mb:.1f} MB)")
    print("This is a validation fixture. The submitted own-feed demonstration "
          "must use real footage.")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
