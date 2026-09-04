# Re-review — Task 6 fix round 2

e96c2e9 Implement fuzzy entity resolution for the assistant
7695b0c Count embeddings honestly, and stop one caller shrinking the shared store

diff --git a/netra/analytics/loop_index.py b/netra/analytics/loop_index.py
index 777aa85..0344a50 100644
--- a/netra/analytics/loop_index.py
+++ b/netra/analytics/loop_index.py
@@ -90,20 +90,26 @@ MAX_JOURNEYS = 50
 #: How far ahead in scene time a hop may reach. Beyond this the appearance
 #: evidence is doing all the work and the space-time check none of it.
 MAX_HOP_SECONDS = 1800.0
 
 #: Sightings one journey may contain, and the recorded time it may span. A
 #: vehicle followed continuously for hours across a dozen transitive
 #: appearance links is not a claim this evidence supports.
 MAX_CHAIN_HOPS = 12
 MAX_JOURNEY_SECONDS = 3600.0
 
+#: The threshold journeys are always *mined* at. Callers may ask for a stricter
+#: one, but that filters what they are shown; it never re-mines the shared
+#: store at their setting, because one narrow request must not shrink what
+#: every other reader sees.
+DEFAULT_MIN_SIMILARITY = 0.84
+
 #: A journey can never be certain — see the module docstring.
 MAX_CONFIDENCE = 0.95
 
 #: Confidence lost per hop beyond the first pair. Every extra hop is another
 #: transitive appearance match, and the chance that one of them jumped to a
 #: different vehicle of the same colour compounds — so a long chain is weaker
 #: evidence than a short one, not stronger, and the arithmetic must say so.
 CHAIN_DECAY_PER_HOP = 0.08
 
 JOURNEY_NOTE = (
@@ -459,46 +465,57 @@ def find_journeys(time_group: str, min_similarity: float = 0.84,
 
     `report`, when supplied, is filled with how many sightings were considered
     and how many were excluded and why. A reader cannot judge what the journeys
     mean without knowing how much of the index could not take part.
 
     Chaining is greedy and bounded — see MAX_MINED_DETECTIONS above for the
     ceiling and what it costs.
     """
     if time_group not in TIME_GROUPS:
         return []
-    if detections is None:
+    from_db = detections is None
+    if from_db:
         detections = _load_group_detections(time_group)
 
     usable, excluded = _minable(detections, time_group)
     if report is not None:
-        report.update({"considered": len(usable), "excluded": excluded,
-                       "supplied": len(detections)})
+        report.update({
+            "considered": len(usable), "excluded": excluded,
+            "supplied": len(detections),
+            # Rows read from the database were already filtered in SQL, so the
+            # exclusion counts above describe only what survived that filter -
+            # they are not the whole index. exclusion_report() is. Saying which
+            # population a number describes is the difference between an
+            # honest figure and a misleading one.
+            "population": ("rows already filtered in SQL for scene clock and "
+                           "embedding" if from_db else "the supplied list"),
+            "prefiltered_in_sql": from_db,
+        })
     min_similarity = max(min_similarity, SIMILARITY_THRESHOLD)
 
     used: set[int] = set()
     journeys: list[Journey] = []
 
     for i, seed in enumerate(usable):
         if len(journeys) >= limit:
             break
         if seed.id in used:
             continue
 
         chain = [seed]
         chain_times = [sighting_time(seed)]
         sims: list[float] = []
         legs: list[dict] = []
 
         cursor = i
         truncated = False
-        while len(chain) < MAX_CHAIN_HOPS:
+        while True:
             current = chain[-1]
             current_at = chain_times[-1]
             best = None
             considered = 0
             scanned = 0
             for j in range(cursor + 1, len(usable)):
                 nxt = usable[j]
                 if considered >= MAX_CANDIDATES_PER_DETECTION or scanned >= MAX_SCAN_AHEAD:
                     break
                 scanned += 1
@@ -516,32 +533,37 @@ def find_journeys(time_group: str, min_similarity: float = 0.84,
                 nxt_at = sighting_time(nxt)
                 ok, leg = _leg(current, nxt, current_at, nxt_at)
                 if not ok:
                     continue
                 if best is None or score > best[0]:
                     best = (score, j, nxt, nxt_at, leg)
 
             if best is None:
                 break
             score, j, nxt, nxt_at, leg = best
+            if len(chain) >= MAX_CHAIN_HOPS:
+                # A further hop was available and is being refused, which is
+                # what "truncated" should mean. A chain that simply runs out of
+                # candidates at exactly the ceiling is complete, not cut.
+                truncated = True
+                break
             if (nxt_at - chain_times[0]).total_seconds() > MAX_JOURNEY_SECONDS:
                 # Beyond this the chain is no longer one journey; whatever
                 # follows is a separate claim and must be mined as one.
                 truncated = True
                 break
             chain.append(nxt)
             chain_times.append(nxt_at)
             sims.append(score)
             legs.append(leg)
             cursor = j
 
-        truncated = truncated or len(chain) >= MAX_CHAIN_HOPS
         if len(chain) < max(2, min_hops):
             continue
         if len({d.camera_id for d in chain}) < 2:
             continue
 
         hops = [_hop_from(chain[0], chain_times[0])]
         for k in range(1, len(chain)):
             hop = _hop_from(chain[k], chain_times[k])
             leg = legs[k - 1]
             hop.similarity = round(sims[k - 1], 3)
@@ -563,70 +585,97 @@ def find_journeys(time_group: str, min_similarity: float = 0.84,
             confidence=_confidence(sims, len(chain)),
             truncated=truncated,
             cameras=sorted({d.camera_id for d in chain}),
         ))
 
     # Strongest evidence first: an operator reads from the top.
     journeys.sort(key=lambda j: (j.confidence, j.hop_count), reverse=True)
     return journeys[:limit]
 
 
+def has_embedding():
+    """SQL for "this detection actually carries an appearance vector".
+
+    `embedding.isnot(None)` is a trap on a JSON column: SQLAlchemy stores a
+    Python None as the JSON literal `null`, which is not SQL NULL, so the
+    obvious filter matches every row. On the live database that is 15,710 rows
+    of `null` counted as usable. Anything comparing embeddings, or counting how
+    many can be compared, must use this instead.
+    """
+    from sqlalchemy import JSON, String, and_, cast
+
+    from netra.core.models import Detection
+    return and_(Detection.embedding.isnot(None),
+                Detection.embedding != JSON.NULL,
+                # An empty vector is stored as `[]` and is equally unusable.
+                cast(Detection.embedding, String) != "[]")
+
+
 def _load_group_detections(group: str) -> list:
     from sqlalchemy.orm import joinedload
 
     from netra.core.db import SessionLocal
     from netra.core.models import Detection
 
     members = TIME_GROUPS.get(group, [])
     if not members:
         return []
     with SessionLocal() as db:
         return (db.query(Detection).options(joinedload(Detection.camera))
                 .filter(Detection.camera_id.in_(members),
                         Detection.scene_time.isnot(None),
-                        Detection.embedding.isnot(None))
+                        has_embedding())
                 # Newest first for the cap, matching _minable's tail slice, so
                 # both layers keep the same end of a long index.
                 .order_by(Detection.scene_time.desc())
                 .limit(MAX_MINED_DETECTIONS).all())
 
 
 # ----------------------------------------------------------- persistence --
 def exclusion_report(group: str) -> dict:
-    """How much of a group's index could not take part in mining, and why.
+    """How much of a group's index cannot take part in mining, and why.
 
     Published rather than kept internal: a reader shown three journeys needs to
     know whether they were drawn from thirty comparable sightings or from three
     thousand of which most had no readable clock. Without that, the journeys
     look like the whole picture when they are a corner of it.
+
+    Every figure below describes the same population — all detections stored
+    for this group's cameras — and the three exclusion counts plus `comparable`
+    sum to it, so the breakdown can be checked rather than trusted.
     """
     from netra.core.db import SessionLocal
     from netra.core.models import Detection
 
     members = TIME_GROUPS.get(group, [])
     if not members:
         return {}
+    embedded = has_embedding()
     with SessionLocal() as db:
         base = db.query(Detection).filter(Detection.camera_id.in_(members))
         total = base.count()
         no_clock = base.filter(Detection.scene_time.is_(None)).count()
-        no_embedding = base.filter(Detection.embedding.is_(None)).count()
-        comparable = base.filter(Detection.scene_time.isnot(None),
-                                 Detection.embedding.isnot(None)).count()
+        clocked = base.filter(Detection.scene_time.isnot(None))
+        with_clock = clocked.count()
+        comparable = clocked.filter(embedded).count()
     return {
         "detections_in_group": total,
+        "with_scene_time": with_clock,
         "comparable": comparable,
         "excluded_no_scene_time": no_clock,
-        "excluded_no_embedding": no_embedding,
+        #: counted among the clocked rows only, so the figures reconcile:
+        #: comparable + no_embedding + no_scene_time == detections_in_group
+        "excluded_no_embedding": with_clock - comparable,
         "note": ("A sighting with no scene clock cannot be placed on a "
                  "journey: wall time records when we connected to the loop, "
-                 "not when the vehicle passed."),
+                 "not when the vehicle passed. Counts describe every "
+                 "detection stored for these cameras."),
     }
 
 
 def persist_journeys(group: str, journeys: list[Journey],
                      min_similarity: float = 0.84) -> int:
     """Replace the stored journeys for one group.
 
     Replaced rather than appended: mining is deterministic over the index, so a
     second run of the same group produces the same journeys and appending would
     show an operator each one several times.
@@ -643,20 +692,34 @@ def persist_journeys(group: str, journeys: list[Journey],
                 time_group=group, hop_count=len(j.hops), cameras=j.cameras,
                 total_km=round(j.total_km, 2), elapsed_s=round(j.elapsed_s, 1),
                 mean_similarity=round(j.mean_similarity, 3),
                 confidence=j.confidence, first_seen=first, last_seen=last,
                 min_similarity=min_similarity, truncated=j.truncated,
                 hops=[asdict(h) for h in j.hops], note=j.note))
         db.commit()
     return len(journeys)
 
 
+def stored_count(group: str) -> int:
+    """How many journeys are stored for a group, before any filtering.
+
+    Lets a caller tell "nothing has been mined yet" from "the filters removed
+    everything", which are different answers to different questions.
+    """
+    from netra.core.db import SessionLocal
+    from netra.core.models import MinedJourney
+
+    with SessionLocal() as db:
+        return (db.query(MinedJourney)
+                .filter(MinedJourney.time_group == group).count())
+
+
 def stored_journeys(group: str, limit: int = MAX_JOURNEYS,
                     min_hops: int = 2, min_similarity: float = 0.0) -> list[dict]:
     """Journeys mined earlier, so the console need not re-run the mining.
 
     `min_hops` and `min_similarity` filter the stored rows rather than
     re-mining. Filtering is exact for hop count; for similarity it keeps
     journeys whose weakest hop clears the bar, which is a subset of what
     re-mining at that threshold would produce — a stricter threshold can also
     change which chains form, so a caller who needs that must ask for a
     refresh. The endpoint says which of the two it did.
@@ -801,15 +864,40 @@ def _self_check() -> None:
     assert longest.truncated, "a chain cut at a ceiling must say so"
     # Length costs confidence rather than earning it: the longest chain scores
     # below a two-hop journey built from the identical embedding, and no
     # journey of more than two hops can reach the cap.
     two_hop = find_journeys("ahmedabad-13jun", detections=two)[0]
     assert longest.confidence < two_hop.confidence, (longest.confidence,
                                                      two_hop.confidence)
     assert all(j.confidence < MAX_CONFIDENCE
                for j in long_j if j.hop_count > 2),         [(j.hop_count, j.confidence) for j in long_j]
 
+    # The JSON-null trap: a Python None in a JSON column is stored as the JSON
+    # literal `null`, not SQL NULL, so `isnot(None)` matches it and every
+    # count built on that filter is wrong. Pinned against an in-memory SQLite
+    # so the honesty figures cannot silently regress. No network, no model.
+    from sqlalchemy import create_engine
+    from sqlalchemy.orm import sessionmaker
+
+    from netra.core.db import Base
+    from netra.core.models import Camera, Detection
+
+    mem = create_engine("sqlite://")
+    Base.metadata.create_all(mem)
+    with sessionmaker(bind=mem)() as db:
+        db.add(Camera(id="cam04", name="Paldi Circle"))
+        for emb in (None, [], [0.1, 0.2]):
+            db.add(Detection(camera_id="cam04", pts_ms=1.0, wall_time=t0,
+                             vehicle_class="car", confidence=0.5,
+                             bbox=[1, 2, 3, 4], embedding=emb))
+        db.commit()
+        rows = db.query(Detection)
+        assert rows.count() == 3
+        # The naive filter is the bug: it matches all three.
+        assert rows.filter(Detection.embedding.isnot(None)).count() == 3
+        assert rows.filter(has_embedding()).count() == 1,             rows.filter(has_embedding()).count()
+
     print("loop_index self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/api/app.py b/netra/api/app.py
index b5fb855..c6bd1ee 100644
--- a/netra/api/app.py
+++ b/netra/api/app.py
@@ -10,20 +10,21 @@ from datetime import datetime, timedelta, timezone
 
 from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
 from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
 from fastapi.staticfiles import StaticFiles
 from sqlalchemy import func, select
 from sqlalchemy.orm import joinedload
 
 from fastapi import Depends, Header
 
 from netra import config
+from netra.analytics.loop_index import has_embedding
 from netra.analytics.route import build_route
 from netra.core import auth
 from netra.core.db import SessionLocal, init_db
 from netra.core.geo import TIME_GROUPS, time_group
 from netra.core.models import Alert, AuditLog, Camera, Detection, WatchlistEntry
 from netra.pipeline import PIPELINE
 
 logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s %(levelname)s %(name)s: %(message)s")
 log = logging.getLogger("netra.api")
@@ -472,21 +473,21 @@ def similar_vehicles(detection_id: int, limit: int = Query(25, le=100),
         query = db.get(Detection, detection_id)
         if query is None:
             raise HTTPException(404, "detection not found")
         if not query.embedding:
             raise HTTPException(
                 400, "this detection has no appearance embedding")
 
         qcam = db.get(Camera, query.camera_id)
         others = (db.query(Detection).options(joinedload(Detection.camera))
                   .filter(Detection.id != detection_id,
-                          Detection.embedding.isnot(None)).all())
+                          has_embedding()).all())
 
         scored = []
         for det in others:
             sim = similarity(query.embedding, det.embedding)
             if sim < min_similarity:
                 continue
             cam = det.camera
             km = 0.0
             if qcam and cam and None not in (qcam.lat, qcam.lon, cam.lat, cam.lon):
                 km = haversine_km(qcam.lat, qcam.lon, cam.lat, cam.lon)
@@ -918,69 +919,89 @@ def mined_journeys(group: str = Query(..., min_length=3, max_length=64),
                    min_hops: int = Query(2, ge=2, le=10),
                    refresh: bool = False,
                    limit: int = Query(50, ge=1, le=200),
                    _p=Depends(require("read"))):
     """Vehicles that genuinely appear on more than one camera of one recording.
 
     The grid replays fixed recordings, and the cameras of a time group share
     the clock burnt into their frames, so a chain built on scene time is a real
     journey through the Government's own footage rather than a demonstration.
 
-    Served from the mined store, filtered by `min_hops` and `min_similarity`.
-    Those parameters only *re-mine* under `refresh`, because a stricter
-    threshold can change which chains form and not merely which survive; the
-    response says which of the two happened.
+    Mining always runs at the module's own thresholds and stores the full set;
+    `min_hops`, `min_similarity` and `limit` filter what this caller is shown.
+    They deliberately do not change what is mined, because the store is shared
+    and one narrow request must not shrink what every other reader sees.
     """
     import time as _time
 
-    from netra.analytics.loop_index import (exclusion_report, find_journeys,
-                                            persist_journeys, stored_journeys)
+    from netra.analytics.loop_index import (DEFAULT_MIN_SIMILARITY,
+                                            MAX_JOURNEYS, exclusion_report,
+                                            find_journeys, persist_journeys,
+                                            stored_count, stored_journeys)
     from netra.core.geo import TIME_GROUPS
 
     if group not in TIME_GROUPS:
         raise HTTPException(status_code=400,
                             detail=f"unknown time group; known groups are "
                                    f"{', '.join(sorted(TIME_GROUPS))}")
 
-    rows = [] if refresh else stored_journeys(group, limit=limit,
-                                              min_hops=min_hops,
-                                              min_similarity=min_similarity)
+    held = stored_count(group)
     last = _journeys_mined_at.get(group, 0.0)
-    cooling = (_time.time() - last) < JOURNEY_REMINE_COOLDOWN_S
-    mined = False
-    if refresh or (not rows and not cooling):
+    waiting = JOURNEY_REMINE_COOLDOWN_S - (_time.time() - last)
+    mined = skipped = False
+
+    if refresh or (not held and waiting <= 0):
         report: dict = {}
-        journeys = find_journeys(group, min_similarity=min_similarity,
-                                 min_hops=min_hops, limit=limit, report=report)
-        persist_journeys(group, journeys, min_similarity=min_similarity)
+        journeys = find_journeys(group, min_similarity=DEFAULT_MIN_SIMILARITY,
+                                 min_hops=2, limit=MAX_JOURNEYS, report=report)
+        persist_journeys(group, journeys,
+                         min_similarity=DEFAULT_MIN_SIMILARITY)
         _journeys_mined_at[group] = _time.time()
-        rows = [j.to_dict() for j in journeys]
+        held = len(journeys)
         mined = True
+    elif not held:
+        skipped = True
+
+    # Always served from the store, so both paths return the identical shape.
+    rows = stored_journeys(group, limit=limit, min_hops=min_hops,
+                           min_similarity=min_similarity)
 
     _audit("analytics.journeys", target=group,
            detail={"journeys": len(rows), "mined": mined})
     return {
         "group": group,
         "cameras": TIME_GROUPS[group],
         "journeys": rows,
         "count": len(rows),
+        "stored": held,
         "mined_now": mined,
+        "mining_skipped": skipped,
+        "next_mine_in_s": (round(max(0.0, waiting), 1)
+                           if skipped else 0.0),
+        "mined_at_similarity": DEFAULT_MIN_SIMILARITY,
         "filters_applied": {"min_hops": min_hops,
                             "min_similarity": min_similarity,
-                            "applied_by": "mining" if mined else "filter"},
+                            "applied_by": "filter"},
         "index": exclusion_report(group),
         "note": ("Appearance-based candidate journeys for operator "
                  "confirmation, not identifications. Chained on the clock "
                  "recorded in the video, never on capture time, and never "
-                 "across recording sessions. Journeys are mined periodically "
-                 "and served from store; pass refresh=true to re-mine at "
-                 "these thresholds."),
+                 f"across recording sessions. Mined at similarity "
+                 f"{DEFAULT_MIN_SIMILARITY}; your thresholds filter these "
+                 "results rather than re-mining. A stricter threshold can "
+                 "also change which chains form, so pass refresh=true to "
+                 "re-mine. Nothing re-mines on its own once journeys are "
+                 "stored, so detections indexed since the mined_at timestamp "
+                 "are not represented until a refresh."
+                 + (" Mining was skipped: it last ran under the cooldown, so "
+                    "this empty result means not-yet-mined rather than "
+                    "nothing-found." if skipped else "")),
     }
 
 
 @app.get("/api/report", response_class=HTMLResponse)
 def output_report(hours: int = Query(24, ge=1, le=720)):
     """Operational output report, printable to PDF from the browser.
 
     This is the output report the submission asks for: detected vehicles and
     plates with timestamps, watchlist matches with their reasoning, zone
     events, per-camera activity, and the cameras measured as unable to deliver.
diff --git a/tools/index_loops.py b/tools/index_loops.py
index 6dc4c97..999f0db 100644
--- a/tools/index_loops.py
+++ b/tools/index_loops.py
@@ -109,27 +109,38 @@ def main() -> int:
                       f"of detections, "
                       f"loop {'completed' if result['loop_complete'] else 'truncated'} "
                       f"in {time.time() - t0:.0f}s")
         finally:
             engine.stop()
 
     print(f"\nMining '{args.group}' ({', '.join(TIME_GROUPS[args.group])})")
     report: dict = {}
     journeys = find_journeys(args.group, min_similarity=args.min_similarity,
                              min_hops=args.min_hops, report=report)
-    excluded = report.get("excluded", {})
-    print(f"  {report.get('considered', 0)} sightings comparable, "
-          f"{excluded.get('no_scene_time', 0)} excluded for no scene clock, "
-          f"{excluded.get('no_embedding', 0)} for no appearance vector, "
-          f"{excluded.get('wrong_group', 0)} outside the group")
+    # Two populations, printed so a reader can tell them apart. The index
+    # figures describe every detection stored for these cameras; the mining
+    # figures describe only the rows that reached the miner, which the database
+    # query has already filtered - printing the miner's "0 excluded for no
+    # scene clock" beside an index where most rows have no clock would read as
+    # a contradiction rather than as two different questions.
     index = exclusion_report(args.group)
     if index:
-        print(f"  index holds {index['detections_in_group']} detections on "
-              f"these cameras, {index['comparable']} of them comparable")
+        print(f"  index: {index['detections_in_group']} detections on these "
+              f"cameras; {index['excluded_no_scene_time']} have no scene "
+              f"clock; of the {index['with_scene_time']} that do, "
+              f"{index['excluded_no_embedding']} have no appearance vector, "
+              f"leaving {index['comparable']} comparable")
+    excluded = report.get("excluded", {})
+    print(f"  mining: {report.get('considered', 0)} sightings chained over, "
+          f"from {report.get('supplied', 0)} rows "
+          f"({report.get('population', 'unknown population')}); dropped here: "
+          f"{excluded.get('no_scene_time', 0)} no clock, "
+          f"{excluded.get('no_embedding', 0)} no appearance vector, "
+          f"{excluded.get('wrong_group', 0)} outside the group")
     if not args.no_persist:
         persist_journeys(args.group, journeys)
     _print_journeys(journeys)
     return 0
 
 
 if __name__ == "__main__":
     raise SystemExit(main())
