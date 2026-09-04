"""Measure the bandwidth case for edge processing.

The scaling argument for 80,000 cameras rests on regional nodes transmitting
structured metadata instead of video. That is easy to assert and worth
measuring, so this does both halves on the live grid:

    video      bytes actually received over RTSP for a camera, over a window
    metadata   bytes the same camera's detections occupy, over the same window

The ratio between them is the bandwidth argument, stated as a measurement
rather than an estimate.

    python tools/measure_bandwidth.py --cameras cam04,cam14 --seconds 60
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netra import config  # noqa: E402


def measure_video_bytes(camera_id: str, seconds: int) -> tuple[int, float]:
    """Bytes received from one camera over a window, by copying the stream to
    a null muxer - no decoding, so this is the true network payload."""
    out = config.DATA / f"_bw_{camera_id}.ts"
    t0 = time.time()
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-rtsp_transport", "tcp",
             "-t", str(seconds), "-i", config.rtsp_url(camera_id),
             "-c", "copy", "-y", str(out)],
            capture_output=True, timeout=seconds + 90)
    except subprocess.TimeoutExpired:
        pass
    elapsed = time.time() - t0
    size = out.stat().st_size if out.exists() else 0
    if out.exists():
        out.unlink()
    return size, elapsed


def metadata_bytes_per_detection() -> dict:
    """Size of one detection as stored and as transmitted.

    Two figures matter and differ by an order of magnitude. The appearance
    embedding is 512 floats and dominates the stored row, but it is only needed
    where cross-camera matching happens; an edge node forwarding to the centre
    can send the compact record and retain embeddings locally.
    """
    full = {
        "camera_id": "cam04", "pts_ms": 123456.7,
        "wall_time": "2026-09-05T01:23:45.678901+00:00",
        "scene_time": "2026-06-13T23:22:47+00:00",
        "vehicle_class": "car", "confidence": 0.873,
        "bbox": [1024, 512, 1180, 640], "colour": "silver",
        "plate_text": "GJ01AB1234", "plate_conf": 0.812, "plate_chars": 10,
        "track_id": 417,
        "embedding": [0.0123456] * 512,
    }
    compact = {k: v for k, v in full.items() if k != "embedding"}
    return {
        "with_embedding": len(json.dumps(full).encode()),
        "compact": len(json.dumps(compact).encode()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cameras", default="cam04,cam14,cam15")
    ap.add_argument("--seconds", type=int, default=60)
    #: vehicles per camera per hour, from the measured live runs
    ap.add_argument("--detections-per-hour", type=int, default=3000)
    args = ap.parse_args()

    sizes = metadata_bytes_per_detection()
    cams = [c.strip() for c in args.cameras.split(",") if c.strip()]

    print(f"Measuring {len(cams)} cameras for {args.seconds}s each\n")
    print(f"{'camera':<10} {'MB received':>12} {'Mbit/s':>9} {'MB/hour':>10}")
    print("-" * 45)

    total_mbps = 0.0
    rows = []
    for cam in cams:
        size, elapsed = measure_video_bytes(cam, args.seconds)
        if not size or elapsed <= 0:
            print(f"{cam:<10} {'unavailable':>12}")
            continue
        mbps = (size * 8) / elapsed / 1e6
        mb_hour = size / elapsed * 3600 / 1e6
        total_mbps += mbps
        rows.append((cam, mbps, mb_hour))
        print(f"{cam:<10} {size/1e6:>12.1f} {mbps:>9.2f} {mb_hour:>10.0f}")

    if not rows:
        print("\nNo cameras measured.")
        return 1

    mean_mbps = total_mbps / len(rows)
    mean_mb_hour = sum(r[2] for r in rows) / len(rows)

    # Metadata side, using the compact record an edge node forwards.
    meta_hour_mb = args.detections_per_hour * sizes["compact"] / 1e6
    meta_hour_mb_emb = args.detections_per_hour * sizes["with_embedding"] / 1e6

    print(f"\nVideo, mean per camera      : {mean_mbps:.2f} Mbit/s "
          f"({mean_mb_hour:.0f} MB/hour)")
    print(f"Metadata record, compact    : {sizes['compact']} bytes")
    print(f"Metadata record, +embedding : {sizes['with_embedding']} bytes")
    print(f"Metadata per camera-hour    : {meta_hour_mb:.2f} MB "
          f"at {args.detections_per_hour} detections/hour")
    print(f"                              {meta_hour_mb_emb:.2f} MB with embeddings")
    print(f"\nReduction, compact metadata : {mean_mb_hour / meta_hour_mb:.0f}x")
    print(f"Reduction, with embeddings  : {mean_mb_hour / meta_hour_mb_emb:.0f}x")

    print(f"\nStatewide, 80,000 cameras:")
    print(f"  continuous video          : "
          f"{80000 * mean_mbps / 1000:,.0f} Gbit/s sustained")
    print(f"  compact metadata          : "
          f"{80000 * meta_hour_mb / 1000:,.1f} GB/hour "
          f"({80000 * meta_hour_mb * 8 / 3600 / 1000:,.2f} Gbit/s)")
    print("\nEvidence crops are additional and are transmitted on demand or "
          "for alerts only, not continuously.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
