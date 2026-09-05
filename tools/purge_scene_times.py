"""Null the scene times that were never corroborated.

Corroborated anchoring - two independent overlay readings that agree once
projected forward by PTS - landed after a large part of this store had already
been indexed. Those earlier rows carry a scene time derived from a single OCR
reading, and a single misread digit anchors an entire stream: this grid
produced spans dated 2025-06-14, 2026-06-24 and 2028-06-13 that way, each from
one bad read that passed every syntactic check.

The analytics already refuse to reason over an uncorroborated scene time (see
netra/core/timing.py), so nothing is *concluded* from those values any more.
This tool is for the operator who would rather the wrong number were not
sitting in the column at all, where a direct SQL query or an export could still
pick it up.

    python tools/purge_scene_times.py              # dry run, counts only
    python tools/purge_scene_times.py --apply      # actually nulls them

Dry run is the default deliberately: this destroys data, and the count alone
answers the question most people are asking.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netra.core.db import SessionLocal, init_db  # noqa: E402
from netra.core.models import Detection  # noqa: E402


def affected_count(db) -> int:
    """Rows carrying a scene time no second reading ever confirmed."""
    return (db.query(Detection)
            .filter(Detection.scene_time.isnot(None),
                    Detection.scene_time_corroborated.is_(False))
            .count())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually null the values; without it nothing is written")
    args = ap.parse_args()

    # Additive columns are applied here as everywhere else; a store predating
    # scene_time_corroborated would otherwise fail on the filter above.
    init_db()

    with SessionLocal() as db:
        total = db.query(Detection).count()
        clocked = db.query(Detection).filter(Detection.scene_time.isnot(None)).count()
        affected = affected_count(db)

        print(f"detections stored:            {total}")
        print(f"  carrying a scene time:      {clocked}")
        print(f"  of those, uncorroborated:   {affected}")

        if not args.apply:
            print("\ndry run: nothing written. Re-run with --apply to null "
                  f"scene_time on those {affected} rows.")
            return 0

        if not affected:
            print("\nnothing to do.")
            return 0

        updated = (db.query(Detection)
                   .filter(Detection.scene_time.isnot(None),
                           Detection.scene_time_corroborated.is_(False))
                   .update({Detection.scene_time: None},
                           synchronize_session=False))
        db.commit()
        print(f"\nnulled scene_time on {updated} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
