"""Index camera loops, then mine them for real cross-camera journeys.

The grid replays a fixed recording on each camera, and the cameras of one time
group replay recordings that share a clock. So a loop can be processed once,
completely, and the resulting index mined for vehicles that genuinely appear on
more than one camera — a discovered fact from the Government's footage rather
than a demonstrated capability.

    python tools/index_loops.py --cameras cam01,cam04 --group ahmedabad-13jun
    python tools/index_loops.py --group ahmedabad-13jun --mine-only

Indexing needs the network. Mining does not: `--mine-only` works entirely from
detections already stored.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netra.analytics.loop_index import (estimate_loop_length,  # noqa: E402
                                        exclusion_report, find_journeys,
                                        index_camera, persist_journeys)
from netra.core.db import SessionLocal, init_db  # noqa: E402
from netra.core.geo import TIME_GROUPS  # noqa: E402
from netra.core.models import Camera  # noqa: E402


def _print_journeys(journeys: list) -> None:
    if not journeys:
        print("No journeys found. A journey needs the same vehicle on two "
              "cameras of one group, with a readable scene clock on both.")
        return
    for n, j in enumerate(journeys, 1):
        print(f"\nJourney {n}: {j.hop_count} sightings across "
              f"{len(j.cameras)} cameras  "
              f"confidence {j.confidence:.2f}  "
              f"mean similarity {j.mean_similarity:.3f}")
        print(f"  {j.total_km:.2f} km over {j.elapsed_s:.0f}s of recorded time")
        for hop in j.hops:
            lead = f"  {hop.camera_id:<7} {hop.at}"
            if hop.similarity is None:
                print(f"{lead}  (first sighting)")
            else:
                print(f"{lead}  sim {hop.similarity:.3f}  "
                      f"{hop.leg_km:.2f} km in {hop.leg_seconds:.0f}s "
                      f"({hop.implied_kmh:.0f} km/h)")
    print(f"\n{journeys[0].note}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cameras", default="",
                    help="comma-separated camera ids to index before mining")
    ap.add_argument("--group", default="ahmedabad-13jun",
                    help=f"time group to mine; one of {', '.join(TIME_GROUPS)}")
    ap.add_argument("--max-seconds", type=float, default=900.0,
                    help="wall-clock ceiling on each camera's indexing pass")
    ap.add_argument("--min-similarity", type=float, default=0.84)
    ap.add_argument("--min-hops", type=int, default=2)
    ap.add_argument("--probe-loops", action="store_true",
                    help="measure each camera's loop length first")
    ap.add_argument("--mine-only", action="store_true",
                    help="skip indexing and mine what is already stored")
    ap.add_argument("--no-persist", action="store_true",
                    help="print journeys without storing them")
    args = ap.parse_args()

    if args.group not in TIME_GROUPS:
        print(f"Unknown time group '{args.group}'. "
              f"Known: {', '.join(sorted(TIME_GROUPS))}")
        return 1

    init_db()
    cams = [c.strip() for c in args.cameras.split(",") if c.strip()]

    if cams and not args.mine_only:
        # One engine for every camera: loading YOLO, the plate model and the
        # ReID backbone once is most of the start-up cost.
        from netra.analytics.inference import InferenceEngine

        engine = InferenceEngine(on_detection=lambda det: None)
        print(f"Loading models on {len(cams)} camera(s)...")
        engine.load()
        with SessionLocal() as db:
            engine.camera_capability = {
                c.id: c.capability for c in db.query(Camera).all()}
        engine.start()
        try:
            for cam in cams:
                if args.probe_loops:
                    length = estimate_loop_length(cam)
                    print(f"{cam}: loop length "
                          f"{f'{length:.1f}s (restart to restart)' if length
                             else 'not measured within the probe timeout'}")
                t0 = time.time()
                result = index_camera(cam, engine, max_seconds=args.max_seconds)
                if result.get("error"):
                    print(f"{cam}: {result['error']}")
                    continue
                print(f"{cam}: {result['frames']} frames, "
                      f"{result['detections']} vehicles, "
                      f"{result['written']} stored, "
                      f"{result['video_seconds']:.0f}s of video, "
                      f"scene clock on {result['scene_time_coverage']*100:.0f}% "
                      f"of detections, "
                      f"loop {'completed' if result['loop_complete'] else 'truncated'} "
                      f"in {time.time() - t0:.0f}s")
        finally:
            engine.stop()

    print(f"\nMining '{args.group}' ({', '.join(TIME_GROUPS[args.group])})")
    report: dict = {}
    journeys = find_journeys(args.group, min_similarity=args.min_similarity,
                             min_hops=args.min_hops, report=report)
    # Two populations, printed so a reader can tell them apart. The index
    # figures describe every detection stored for these cameras; the mining
    # figures describe only the rows that reached the miner, which the database
    # query has already filtered - printing the miner's "0 excluded for no
    # scene clock" beside an index where most rows have no clock would read as
    # a contradiction rather than as two different questions.
    index = exclusion_report(args.group)
    if index:
        print(f"  index: {index['detections_in_group']} detections on these "
              f"cameras; {index['excluded_no_scene_time']} have no scene "
              f"clock; of the {index['with_scene_time']} that do, "
              f"{index['excluded_no_embedding']} have no appearance vector, "
              f"leaving {index['comparable']} comparable")
    excluded = report.get("excluded", {})
    print(f"  mining: {report.get('considered', 0)} sightings chained over, "
          f"from {report.get('supplied', 0)} rows "
          f"({report.get('population', 'unknown population')}); dropped here: "
          f"{excluded.get('no_scene_time', 0)} no clock, "
          f"{excluded.get('no_embedding', 0)} no appearance vector, "
          f"{excluded.get('wrong_group', 0)} outside the group")
    if not args.no_persist:
        persist_journeys(args.group, journeys)
    _print_journeys(journeys)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
