"""Retention: keeping two unbounded stores inside a fixed budget.

The pipeline writes one evidence JPEG and one detection row for every vehicle
it sees. On this grid that is thousands per minute, forever, and nothing in the
platform previously deleted any of it. A deployment left running therefore ends
with a full disk or a detections table too large to query - both of which take
the whole system down rather than degrading it.

Two pruners, with one rule they share: evidence attached to an unacknowledged
alert or zone event is never deleted, and a detection an alert points at is
never deleted. That evidence is the reason the alert is actionable - pruning it
would leave an operator an alert they cannot act on, which is worse than
keeping the file. Both pruners therefore report what they *retained* by that
rule alongside what they removed, so the ceiling being hit is visible rather
than silent.

ponytail: pruning is invoked on demand (an endpoint, or a scheduled call),
not by a background thread. A thread deleting files while inference runs is one
more thing competing for I/O with the primary duty, and the operator - or
cron - knows better than we do when the quiet hour is. The ceiling is that a
platform nobody ever calls this on still fills its disk.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from netra import config

log = logging.getLogger(__name__)


def _session_factory():
    """Resolved lazily so a self-check can substitute a throwaway database."""
    from netra.core.db import SessionLocal
    return SessionLocal


def _basename(url_path: str | None) -> str | None:
    """Evidence is stored as the URL path `/evidence/<file>`; files are not."""
    if not url_path:
        return None
    return url_path.rsplit("/", 1)[-1]


def protected_evidence(session_factory=None) -> set[str]:
    """Evidence filenames that must survive any prune.

    An alert or zone event an operator has not yet acknowledged is still open
    police work; its picture is the evidence.
    """
    from netra.core.models import Alert, Detection, ZoneEventRow

    sf = session_factory or _session_factory()
    keep: set[str] = set()
    with sf() as db:
        rows = (db.query(Detection.evidence_path)
                .join(Alert, Alert.detection_id == Detection.id)
                .filter(Alert.acknowledged.is_(False))
                .filter(Detection.evidence_path.isnot(None)).all())
        keep.update(n for n in (_basename(r[0]) for r in rows) if n)

        rows = (db.query(ZoneEventRow.evidence_path)
                .filter(ZoneEventRow.acknowledged.is_(False))
                .filter(ZoneEventRow.evidence_path.isnot(None)).all())
        keep.update(n for n in (_basename(r[0]) for r in rows) if n)
    return keep


def prune_evidence(max_bytes: int | None = None, max_age_days: int | None = None,
                   evidence_dir: Path | None = None,
                   session_factory=None, dry_run: bool = False) -> dict:
    """Delete evidence files oldest-first until inside the age and size budget.

    Age is applied first, then the size budget, because an expired file is
    worthless regardless of how much room is left. Files referenced by an
    unacknowledged alert or zone event are skipped by both rules and counted
    separately.
    """
    max_bytes = config.EVIDENCE_MAX_BYTES if max_bytes is None else max_bytes
    max_age_days = (config.EVIDENCE_MAX_AGE_DAYS if max_age_days is None
                    else max_age_days)
    directory = Path(evidence_dir) if evidence_dir else config.EVIDENCE

    keep = protected_evidence(session_factory)

    # (mtime, size, path) oldest first. Statting every file is the whole cost
    # of this pass; at the 5 GiB ceiling that is a few tens of thousands of
    # entries, which is a fraction of a second.
    files: list[tuple[float, int, Path]] = []
    total = 0
    for entry in os.scandir(directory) if directory.exists() else []:
        if not entry.is_file():
            continue
        try:
            st = entry.stat()
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, Path(entry.path)))
        total += st.st_size
    files.sort()

    report = {
        "scanned": len(files), "bytes_before": total,
        "deleted_expired": 0, "deleted_over_budget": 0,
        "bytes_freed": 0, "retained_protected": 0,
        "retained_protected_bytes": 0, "failed": 0,
        "max_bytes": max_bytes, "max_age_days": max_age_days,
        "dry_run": dry_run,
    }

    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).timestamp()
    remaining: list[tuple[float, int, Path]] = []
    # A protected file can be reached by both rules in one call - expired
    # *and* over budget - and it must be reported once, not twice. This figure
    # is audited into storage.prune, so a doubled count is a doubled claim.
    counted_protected: set[str] = set()

    def _remove(item, reason: str) -> None:
        _mtime, size, path = item
        if path.name in keep:
            if path.name not in counted_protected:
                counted_protected.add(path.name)
                report["retained_protected"] += 1
                report["retained_protected_bytes"] += size
            remaining.append(item)
            return
        if not dry_run:
            try:
                path.unlink()
            except OSError:
                report["failed"] += 1
                remaining.append(item)
                return
        report[reason] += 1
        report["bytes_freed"] += size

    for item in files:
        if item[0] < cutoff:
            _remove(item, "deleted_expired")
        else:
            remaining.append(item)

    # Size budget over what age did not already take, still oldest-first.
    live = total - report["bytes_freed"]
    if live > max_bytes:
        over_budget, remaining = remaining, []
        for item in over_budget:
            if live <= max_bytes:
                remaining.append(item)
                continue
            before = report["bytes_freed"]
            _remove(item, "deleted_over_budget")
            live -= report["bytes_freed"] - before

    report["bytes_after"] = total - report["bytes_freed"]
    report["deleted"] = report["deleted_expired"] + report["deleted_over_budget"]
    if report["deleted"] or report["retained_protected"]:
        log.info("evidence prune: removed %d files (%.1f MiB), retained %d "
                 "attached to open alerts", report["deleted"],
                 report["bytes_freed"] / 1024**2, report["retained_protected"])
    return report


def prune_detections(max_rows: int | None = None, keep_days: int | None = None,
                     session_factory=None, dry_run: bool = False) -> dict:
    """Delete the oldest detections beyond the row cap.

    Two things are never deleted: a detection any alert points at (the alert's
    foreign key would dangle, and the alert would lose the sighting it was
    raised on), and anything inside `keep_days`, which is the window an
    operator is actively querying.
    """
    from netra.core.models import Alert, Detection

    max_rows = config.DETECTION_MAX_ROWS if max_rows is None else max_rows
    keep_days = config.DETECTION_KEEP_DAYS if keep_days is None else keep_days
    sf = session_factory or _session_factory()
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)

    report = {"rows_before": 0, "deleted": 0, "retained_alerted": 0,
              "retained_recent": 0, "max_rows": max_rows,
              "keep_days": keep_days, "dry_run": dry_run}

    with sf() as db:
        total = db.query(Detection.id).count()
        report["rows_before"] = total
        excess = total - max_rows
        if excess <= 0:
            report["rows_after"] = total
            return report

        # Oldest-first, take only as many as the cap demands. The two
        # protections are applied as filters rather than after selection, so a
        # table that is entirely alert-referenced simply deletes nothing
        # instead of looping.
        alerted = db.query(Alert.detection_id).distinct().subquery()
        candidates = (db.query(Detection.id)
                      .filter(Detection.wall_time < cutoff)
                      .filter(Detection.id.notin_(db.query(alerted.c.detection_id)))
                      .order_by(Detection.wall_time.asc())
                      .limit(excess).all())
        ids = [row[0] for row in candidates]
        report["retained_alerted"] = db.query(Alert.detection_id).distinct().count()
        report["retained_recent"] = (db.query(Detection.id)
                                     .filter(Detection.wall_time >= cutoff).count())

        if ids and not dry_run:
            db.query(Detection).filter(Detection.id.in_(ids)).delete(
                synchronize_session=False)
            db.commit()
        report["deleted"] = len(ids)
        report["rows_after"] = total - report["deleted"]
        report["still_over_cap"] = max(0, report["rows_after"] - max_rows)

    if report["deleted"]:
        log.info("detection prune: removed %d rows, %d retained by an alert",
                 report["deleted"], report["retained_alerted"])
    return report


def storage_report(evidence_dir: Path | None = None, session_factory=None) -> dict:
    """What the two stores currently hold, against what they are allowed to."""
    from netra.core.models import Alert, Detection, ZoneEventRow

    directory = Path(evidence_dir) if evidence_dir else config.EVIDENCE
    count = 0
    total = 0
    for entry in os.scandir(directory) if directory.exists() else []:
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                continue
            count += 1

    sf = session_factory or _session_factory()
    with sf() as db:
        detections = db.query(Detection.id).count()
        alerts = db.query(Alert.id).count()
        open_alerts = db.query(Alert.id).filter(Alert.acknowledged.is_(False)).count()
        open_zone = (db.query(ZoneEventRow.id)
                     .filter(ZoneEventRow.acknowledged.is_(False)).count())

    return {
        "evidence": {
            "files": count,
            "bytes": total,
            "mib": round(total / 1024**2, 1),
            "max_bytes": config.EVIDENCE_MAX_BYTES,
            "max_age_days": config.EVIDENCE_MAX_AGE_DAYS,
            "percent_of_budget": round(
                100.0 * total / config.EVIDENCE_MAX_BYTES, 1)
            if config.EVIDENCE_MAX_BYTES else None,
        },
        "detections": {
            "rows": detections,
            "max_rows": config.DETECTION_MAX_ROWS,
            "keep_days": config.DETECTION_KEEP_DAYS,
            "percent_of_cap": round(
                100.0 * detections / config.DETECTION_MAX_ROWS, 1)
            if config.DETECTION_MAX_ROWS else None,
        },
        "alerts": {"rows": alerts, "unacknowledged": open_alerts},
        "zone_events": {"unacknowledged": open_zone},
    }


def _self_check() -> None:
    """Exercise both pruners against a temporary directory and database.

    Deliberately never touches data/netra.db or data/evidence: this runs on a
    developer's machine with a live pipeline's evidence sitting on disk, and a
    self-check that deletes real evidence would be worse than no self-check.
    """
    import tempfile

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from netra.core.db import Base
    from netra.core import models  # noqa: F401  (registers the mappers)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        evidence = root / "evidence"
        evidence.mkdir()
        engine = create_engine(f"sqlite:///{root / 'check.db'}")
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine, expire_on_commit=False)
        try:
            _check_body(evidence, sf)
        finally:
            # Windows will not remove the temporary directory while SQLite
            # still holds the file open, which would mask the real failure.
            engine.dispose()

    print("retention self-check passed")


def _check_body(evidence, sf) -> None:
        """The body of the self-check, over a temporary directory and database."""
        from netra.core.models import Alert, Camera, Detection, ZoneEventRow

        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)

        def write(name: str, size: int, age_days: float) -> Path:
            path = evidence / name
            path.write_bytes(b"\0" * size)
            stamp = (now - timedelta(days=age_days)).timestamp()
            os.utime(path, (stamp, stamp))
            return path

        with sf() as db:
            db.add(Camera(id="CAM1", name="check"))
            db.flush()
            # d1 is old and attached to an OPEN alert - must survive both
            # pruners. d2 is old and attached to an ACKNOWLEDGED alert - its
            # file may go, but the row may not (the alert's key points at it).
            # d3 is old and unreferenced - the only row eligible for deletion.
            for i, (path, when) in enumerate(
                    [("/evidence/open.jpg", old), ("/evidence/ack.jpg", old),
                     ("/evidence/plain.jpg", old)], start=1):
                db.add(Detection(id=i, camera_id="CAM1", pts_ms=0.0,
                                 wall_time=when, vehicle_class="car",
                                 confidence=0.9, bbox=[0, 0, 10, 10],
                                 evidence_path=path))
            db.add(Alert(detection_id=1, watchlist_id=1, camera_id="CAM1",
                         score=0.9, match_type="exact", reasons={},
                         acknowledged=False))
            db.add(Alert(detection_id=2, watchlist_id=1, camera_id="CAM1",
                         score=0.9, match_type="exact", reasons={},
                         acknowledged=True))
            db.add(ZoneEventRow(zone_rule_id=1, camera_id="CAM1",
                                rule="intrusion", detail="check",
                                evidence_path="/evidence/zone_open.jpg",
                                acknowledged=False))
            db.add(ZoneEventRow(zone_rule_id=1, camera_id="CAM1",
                                rule="intrusion", detail="check",
                                evidence_path="/evidence/zone_ack.jpg",
                                acknowledged=True))
            db.commit()

        for name in ("open.jpg", "ack.jpg", "plain.jpg",
                     "zone_open.jpg", "zone_ack.jpg"):
            write(name, 1000, age_days=30)

        keep = protected_evidence(sf)
        assert keep == {"open.jpg", "zone_open.jpg"}, keep

        # --- age rule, with the protection in force -------------------------
        r = prune_evidence(max_bytes=10**9, max_age_days=7,
                           evidence_dir=evidence, session_factory=sf)
        assert r["deleted_expired"] == 3, r
        assert r["retained_protected"] == 2, r
        assert r["retained_protected_bytes"] == 2000, r
        survivors = {p.name for p in evidence.iterdir()}
        assert survivors == {"open.jpg", "zone_open.jpg"}, survivors

        # --- size budget, oldest first --------------------------------------
        for i in range(5):
            write(f"recent{i}.jpg", 1000, age_days=i)  # recent0 newest
        # Seven 1000-byte files against a 4500-byte budget. The two protected
        # ones are the oldest, so they are visited first and free nothing;
        # three unprotected files then go before the budget is met.
        r = prune_evidence(max_bytes=4500, max_age_days=365,
                           evidence_dir=evidence, session_factory=sf)
        left = {p.name for p in evidence.iterdir()}
        # Protected files count against the budget but cannot be removed, so
        # the budget is honoured only as far as the protection allows.
        assert "open.jpg" in left and "zone_open.jpg" in left, left
        assert "recent4.jpg" not in left, left   # oldest unprotected went first
        assert "recent0.jpg" in left, left       # newest survived
        assert r["deleted_over_budget"] == 3, r
        assert r["retained_protected"] == 2, r

        # Both rules in one call. A protected file is expired *and* over
        # budget, so it passes through the removal path twice; it must still
        # be reported once. Nothing was covering this, which is how a doubled
        # count reached an audit record.
        r = prune_evidence(max_bytes=0, max_age_days=0, evidence_dir=evidence,
                           session_factory=sf, dry_run=True)
        assert r["retained_protected"] == 2, r
        assert r["retained_protected_bytes"] == 2000, r
        assert r["deleted_expired"] == r["scanned"] - 2, r
        assert r["deleted_over_budget"] == 0, r   # age already took them all

        # A dry run must report the same intent without touching the disk.
        before = sorted(p.name for p in evidence.iterdir())
        r = prune_evidence(max_bytes=0, max_age_days=365, evidence_dir=evidence,
                           session_factory=sf, dry_run=True)
        assert sorted(p.name for p in evidence.iterdir()) == before
        assert r["deleted"] >= 1 and r["retained_protected"] == 2, r

        # --- detection rows -------------------------------------------------
        # Cap of 1 against 3 rows: two are alert-referenced and must survive,
        # so exactly one row goes and the table stays above its cap. Reporting
        # that honestly matters more than forcing the cap.
        r = prune_detections(max_rows=1, keep_days=1, session_factory=sf)
        assert r["deleted"] == 1, r
        assert r["retained_alerted"] == 2, r
        assert r["still_over_cap"] == 1, r
        with sf() as db:
            left_ids = sorted(i for (i,) in db.query(Detection.id).all())
        assert left_ids == [1, 2], left_ids

        # Nothing left to take: the remaining rows are all alert-referenced.
        r = prune_detections(max_rows=0, keep_days=1, session_factory=sf)
        assert r["deleted"] == 0, r

        # keep_days protects recent rows even far over the cap.
        with sf() as db:
            db.add(Detection(id=99, camera_id="CAM1", pts_ms=0.0,
                             wall_time=now, vehicle_class="car",
                             confidence=0.5, bbox=[0, 0, 1, 1]))
            db.commit()
        r = prune_detections(max_rows=0, keep_days=7, session_factory=sf)
        assert r["deleted"] == 0 and r["retained_recent"] == 1, r

        rep = storage_report(evidence_dir=evidence, session_factory=sf)
        assert rep["detections"]["rows"] == 3, rep
        assert rep["alerts"]["unacknowledged"] == 1, rep
        assert rep["zone_events"]["unacknowledged"] == 1, rep
        assert rep["evidence"]["files"] == len(list(evidence.iterdir())), rep


if __name__ == "__main__":
    _self_check()
