# Re-review — Task 6 fix round 1

04e5517 Stop a greedy chain welding dozens of vehicles into one journey

diff --git a/netra/analytics/loop_index.py b/netra/analytics/loop_index.py
index 21c45b3..777aa85 100644
--- a/netra/analytics/loop_index.py
+++ b/netra/analytics/loop_index.py
@@ -62,91 +62,119 @@ READ_FAILURE_LIMIT = 60
 #: Ceiling on the mining itself. Appearance comparison is O(n²) in the worst
 #: case, and a fully indexed Ahmedabad group runs to tens of thousands of rows.
 #: ponytail: mining considers at most MAX_MINED_DETECTIONS of the most recent
 #: detections, examines at most MAX_CANDIDATES_PER_DETECTION forward matches
 #: for each one, extends each chain greedily by the single best next hop rather
 #: than searching alternatives, and returns at most MAX_JOURNEYS. So this finds
 #: strong journeys, not every journey: a vehicle whose true next sighting
 #: scored second is followed down the wrong branch and no backtracking recovers
 #: it. An exhaustive search over an indexed loop is not affordable inside an
 #: API request, and a bounded search that says so is the honest trade.
+#:
+#: A chain is capped at MAX_CHAIN_HOPS sightings and MAX_JOURNEY_SECONDS of
+#: recorded time, and confidence decays with every additional hop. Without
+#: those two ceilings a greedy chain welds itself onward indefinitely — 20,000
+#: synthetic detections produced one 1,500-hop "journey" spanning twelve hours
+#: at maximum confidence, because every individual leg is feasible. On real
+#: footage, where a hundred silver hatchbacks look alike, that is not one
+#: vehicle; it is dozens, presented as overwhelming evidence. The ceiling is
+#: therefore a correctness property, not a performance one, and it is
+#: deliberately tight: beyond a dozen transitive appearance links there is no
+#: honest reading of the chain as one vehicle.
 MAX_MINED_DETECTIONS = 4000
 MAX_CANDIDATES_PER_DETECTION = 8
 #: Rows looked at ahead of a hop before the search gives up on extending it.
 #: Bounds the inner loop even where nothing scores above the threshold.
 MAX_SCAN_AHEAD = 200
 MAX_JOURNEYS = 50
 
 #: How far ahead in scene time a hop may reach. Beyond this the appearance
 #: evidence is doing all the work and the space-time check none of it.
 MAX_HOP_SECONDS = 1800.0
 
+#: Sightings one journey may contain, and the recorded time it may span. A
+#: vehicle followed continuously for hours across a dozen transitive
+#: appearance links is not a claim this evidence supports.
+MAX_CHAIN_HOPS = 12
+MAX_JOURNEY_SECONDS = 3600.0
+
 #: A journey can never be certain — see the module docstring.
 MAX_CONFIDENCE = 0.95
 
+#: Confidence lost per hop beyond the first pair. Every extra hop is another
+#: transitive appearance match, and the chance that one of them jumped to a
+#: different vehicle of the same colour compounds — so a long chain is weaker
+#: evidence than a short one, not stronger, and the arithmetic must say so.
+CHAIN_DECAY_PER_HOP = 0.08
+
 JOURNEY_NOTE = (
     "Appearance-based candidate journey, not an identification. Each hop is a "
     "cosine match between vehicle crops that also passes the space-time "
     "feasibility check on the recorded clock. Confirm against plate, evidence "
     "crops or another signal before acting on it.")
 
 
 # --------------------------------------------------------------- indexing --
 def estimate_loop_length(camera_id: str, timeout_s: float = LOOP_PROBE_TIMEOUT_S,
                          spec=None) -> float | None:
-    """Observed length of one camera's recording, in seconds, or None.
-
-    Read PTS until it jumps backwards; the highest PTS reached before that jump
-    is the recording's length. This is measured rather than asked for, because
-    the grid publishes no duration and two of its cameras declare 0/0 fps, so
-    nothing in the catalogue can be trusted to describe timing.
+    """Length of one camera's recording in seconds, measured, or None.
+
+    Measured rather than asked for: the grid publishes no duration and two of
+    its cameras declare 0/0 fps, so nothing in the catalogue can be trusted to
+    describe timing.
+
+    The measurement is restart-to-restart. Joining mid-loop and timing to the
+    first restart would only ever see the tail of the recording, and reporting
+    that as *the* loop length would understate it by however long we happened
+    to arrive late — so the first restart starts the clock and the second stops
+    it. That costs up to two loops of patience, hence the timeout, and returns
+    None rather than a lower bound dressed up as a measurement.
     """
     from netra.ingest.sources import build, spec_for_camera
 
     spec = spec or spec_for_camera(camera_id)
     source = build(spec)
     try:
         source.open()
     except Exception as exc:  # a probe must never take the caller down with it
         log.warning("%s loop probe could not open source: %s", camera_id, exc)
         return None
 
     deadline = time.time() + timeout_s
-    first_pts: float | None = None
+    restarts = 0
     highest = 0.0
     last = 0.0
     failures = 0
     try:
         while time.time() < deadline:
             ok, _img, pts = source.read()
             if not ok:
                 failures += 1
                 if failures >= READ_FAILURE_LIMIT:
                     log.warning("%s loop probe: stream stopped delivering", camera_id)
                     return None
                 continue
             failures = 0
-            if first_pts is None:
-                first_pts = pts
             if pts + LOOP_JUMP_TOLERANCE_MS < last:
-                # The recording restarted. What we saw between join and restart
-                # is a lower bound only if we joined mid-loop, so report the
-                # span actually observed and let the caller judge it.
-                length = (highest - first_pts) / 1000.0
-                return round(length, 2) if length > 0 else None
+                restarts += 1
+                if restarts >= 2:
+                    # A complete pass, start to start.
+                    return round(highest / 1000.0, 2) if highest > 0 else None
+                highest = 0.0  # discard the partial loop we joined
             last = pts
-            highest = max(highest, pts)
+            if restarts >= 1:
+                highest = max(highest, pts)
     finally:
         source.release()
 
-    log.warning("%s loop probe timed out after %.0fs without a restart",
-                camera_id, timeout_s)
+    log.warning("%s loop probe timed out after %.0fs having seen %d restart(s)",
+                camera_id, timeout_s, restarts)
     return None
 
 
 def _submit_blocking(engine, frame, deadline: float) -> bool:
     """Hand a frame to inference, waiting for room rather than dropping it.
 
     The live path drops frames under load deliberately: a control room needs
     the newest frame, not every frame. Indexing wants the opposite. The whole
     value of a finite loop is that it can be processed *completely*, so here we
     slow the reader down to the model's pace instead of losing vehicles.
@@ -168,20 +196,28 @@ def index_camera(camera_id: str, engine, max_seconds: float = 900.0,
     identical detection, embedding and scene-time-anchoring code the live
     pipeline runs — an index built by a second implementation would not be
     comparable with the detections already in the database.
 
     Stops at the loop point, at `max_seconds` of wall time, or when the stream
     stops delivering, whichever comes first.
     """
     from netra.ingest.sources import build, spec_for_camera
     from netra.ingest.stream import Frame
 
+    # Fail here rather than silently indexing nothing: an unloaded engine
+    # accepts frames and produces no detections at all, which looks exactly
+    # like a camera with no traffic.
+    if getattr(engine, "_vehicle_model", None) is None:
+        raise RuntimeError("engine must be load()ed before indexing")
+    if getattr(engine, "_thread", None) is None or not engine._thread.is_alive():
+        raise RuntimeError("engine must be start()ed before indexing")
+
     spec = spec or spec_for_camera(camera_id)
     source = build(spec)
     try:
         source.open()
     except Exception as exc:
         log.warning("%s index could not open source: %s", camera_id, exc)
         return {"camera_id": camera_id, "error": str(exc), "frames": 0,
                 "detections": 0, "written": 0}
 
     collected: list = []
@@ -297,37 +333,41 @@ class JourneyHop:
 
 
 @dataclass
 class Journey:
     time_group: str
     hops: list[JourneyHop]
     total_km: float
     elapsed_s: float
     mean_similarity: float
     confidence: float
+    #: the chain hit MAX_CHAIN_HOPS or MAX_JOURNEY_SECONDS and was cut, so
+    #: what is shown is a bounded slice rather than the whole of what matched
+    truncated: bool = False
     note: str = JOURNEY_NOTE
     cameras: list[str] = field(default_factory=list)
 
     @property
     def hop_count(self) -> int:
         return len(self.hops)
 
     def to_dict(self) -> dict:
         return {
             "time_group": self.time_group,
             "hops": [asdict(h) for h in self.hops],
             "hop_count": len(self.hops),
             "cameras": self.cameras,
             "total_km": round(self.total_km, 2),
             "elapsed_s": round(self.elapsed_s, 1),
             "mean_similarity": round(self.mean_similarity, 3),
             "confidence": self.confidence,
+            "truncated": self.truncated,
             "note": self.note,
         }
 
 
 def _hop_from(det, scene_at: datetime) -> JourneyHop:
     cam = getattr(det, "camera", None)
     return JourneyHop(
         camera_id=det.camera_id,
         camera_name=cam.name if cam else det.camera_id,
         lat=cam.lat if cam else None,
@@ -360,90 +400,105 @@ def _leg(prev_det, det, prev_at: datetime, at: datetime) -> tuple[bool, dict]:
         km = haversine_km(pcam.lat, pcam.lon, ncam.lat, ncam.lon)
 
     ok, why = spacetime_plausible(km, seconds)
     return ok, {"km": km, "seconds": seconds, "reason": why,
                 "implied_kmh": km / (seconds / 3600.0)}
 
 
 def _confidence(similarities: list[float], hop_count: int) -> float:
     """How strongly the appearance evidence supports this chain.
 
-    Mean similarity leads, because that is what the evidence actually is. A
-    longer chain earns a small addition — three cameras agreeing is a stronger
-    argument than two — but only a small one, since a greedy chain can extend
-    itself through a wrong link as easily as a right one. Capped below
-    certainty: this is never an identification.
+    Mean similarity leads, because that is what the evidence actually is, and
+    it is then attenuated by chain length. A chain of many hops is a chain of
+    many chances to have stepped onto a different vehicle that merely looks the
+    same, and a greedy search takes the best-scoring step whether or not it is
+    the right one — so length must cost confidence rather than earn it. Only a
+    two-hop journey, the shortest thing that is a journey at all, can approach
+    the cap, and even that is capped below certainty: this is never an
+    identification.
     """
-    mean = sum(similarities) / len(similarities) if similarities else 0.0
-    length_bonus = min(0.06, 0.02 * max(0, hop_count - 2))
-    return round(min(MAX_CONFIDENCE, mean * 0.95 + length_bonus), 3)
+    if not similarities:
+        return 0.0
+    mean = sum(similarities) / len(similarities)
+    decay = 1.0 / (1.0 + CHAIN_DECAY_PER_HOP * max(0, hop_count - 2))
+    return round(min(MAX_CONFIDENCE, mean * 0.95 * decay), 3)
 
 
 def _minable(detections: list, group: str) -> tuple[list, dict]:
     """Detections of one group that can legitimately take part in a journey."""
     members = set(TIME_GROUPS.get(group, ()))
     usable, excluded = [], {"wrong_group": 0, "no_scene_time": 0, "no_embedding": 0}
     for det in detections:
         if det.camera_id not in members:
             excluded["wrong_group"] += 1
             continue
         if not getattr(det, "scene_time", None):
             # Wall time is our connection time, not the vehicle's. A sighting
             # with no recorded clock simply cannot be placed on a journey.
             excluded["no_scene_time"] += 1
             continue
         if not getattr(det, "embedding", None):
             excluded["no_embedding"] += 1
             continue
         usable.append(det)
-    # Newest first for the cap, so a long-running index keeps its recent pass.
+    # Ordered oldest first, then tail-sliced: where the cap bites, the most
+    # recent pass over the recording is the one kept.
     usable.sort(key=sighting_time)
     if len(usable) > MAX_MINED_DETECTIONS:
         usable = usable[-MAX_MINED_DETECTIONS:]
     return usable, excluded
 
 
 def find_journeys(time_group: str, min_similarity: float = 0.84,
                   min_hops: int = 2, detections: list | None = None,
-                  limit: int = MAX_JOURNEYS) -> list[Journey]:
+                  limit: int = MAX_JOURNEYS,
+                  report: dict | None = None) -> list[Journey]:
     """Mine one time group's indexed detections for real cross-camera journeys.
 
     `detections` are ORM Detection rows with `.camera` loaded; when omitted they
     are read from the database for the group's cameras.
 
+    `report`, when supplied, is filled with how many sightings were considered
+    and how many were excluded and why. A reader cannot judge what the journeys
+    mean without knowing how much of the index could not take part.
+
     Chaining is greedy and bounded — see MAX_MINED_DETECTIONS above for the
     ceiling and what it costs.
     """
     if time_group not in TIME_GROUPS:
         return []
     if detections is None:
         detections = _load_group_detections(time_group)
 
-    usable, _excluded = _minable(detections, time_group)
+    usable, excluded = _minable(detections, time_group)
+    if report is not None:
+        report.update({"considered": len(usable), "excluded": excluded,
+                       "supplied": len(detections)})
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
-        while True:
+        truncated = False
+        while len(chain) < MAX_CHAIN_HOPS:
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
@@ -461,26 +516,32 @@ def find_journeys(time_group: str, min_similarity: float = 0.84,
                 nxt_at = sighting_time(nxt)
                 ok, leg = _leg(current, nxt, current_at, nxt_at)
                 if not ok:
                     continue
                 if best is None or score > best[0]:
                     best = (score, j, nxt, nxt_at, leg)
 
             if best is None:
                 break
             score, j, nxt, nxt_at, leg = best
+            if (nxt_at - chain_times[0]).total_seconds() > MAX_JOURNEY_SECONDS:
+                # Beyond this the chain is no longer one journey; whatever
+                # follows is a separate claim and must be mined as one.
+                truncated = True
+                break
             chain.append(nxt)
             chain_times.append(nxt_at)
             sims.append(score)
             legs.append(leg)
             cursor = j
 
+        truncated = truncated or len(chain) >= MAX_CHAIN_HOPS
         if len(chain) < max(2, min_hops):
             continue
         if len({d.camera_id for d in chain}) < 2:
             continue
 
         hops = [_hop_from(chain[0], chain_times[0])]
         for k in range(1, len(chain)):
             hop = _hop_from(chain[k], chain_times[k])
             leg = legs[k - 1]
             hop.similarity = round(sims[k - 1], 3)
@@ -493,20 +554,21 @@ def find_journeys(time_group: str, min_similarity: float = 0.84,
         for det in chain:
             used.add(det.id)
 
         journeys.append(Journey(
             time_group=time_group,
             hops=hops,
             total_km=sum(leg["km"] for leg in legs),
             elapsed_s=(chain_times[-1] - chain_times[0]).total_seconds(),
             mean_similarity=sum(sims) / len(sims),
             confidence=_confidence(sims, len(chain)),
+            truncated=truncated,
             cameras=sorted({d.camera_id for d in chain}),
         ))
 
     # Strongest evidence first: an operator reads from the top.
     journeys.sort(key=lambda j: (j.confidence, j.hop_count), reverse=True)
     return journeys[:limit]
 
 
 def _load_group_detections(group: str) -> list:
     from sqlalchemy.orm import joinedload
@@ -515,79 +577,132 @@ def _load_group_detections(group: str) -> list:
     from netra.core.models import Detection
 
     members = TIME_GROUPS.get(group, [])
     if not members:
         return []
     with SessionLocal() as db:
         return (db.query(Detection).options(joinedload(Detection.camera))
                 .filter(Detection.camera_id.in_(members),
                         Detection.scene_time.isnot(None),
                         Detection.embedding.isnot(None))
-                .order_by(Detection.scene_time)
+                # Newest first for the cap, matching _minable's tail slice, so
+                # both layers keep the same end of a long index.
+                .order_by(Detection.scene_time.desc())
                 .limit(MAX_MINED_DETECTIONS).all())
 
 
 # ----------------------------------------------------------- persistence --
-def persist_journeys(group: str, journeys: list[Journey]) -> int:
+def exclusion_report(group: str) -> dict:
+    """How much of a group's index could not take part in mining, and why.
+
+    Published rather than kept internal: a reader shown three journeys needs to
+    know whether they were drawn from thirty comparable sightings or from three
+    thousand of which most had no readable clock. Without that, the journeys
+    look like the whole picture when they are a corner of it.
+    """
+    from netra.core.db import SessionLocal
+    from netra.core.models import Detection
+
+    members = TIME_GROUPS.get(group, [])
+    if not members:
+        return {}
+    with SessionLocal() as db:
+        base = db.query(Detection).filter(Detection.camera_id.in_(members))
+        total = base.count()
+        no_clock = base.filter(Detection.scene_time.is_(None)).count()
+        no_embedding = base.filter(Detection.embedding.is_(None)).count()
+        comparable = base.filter(Detection.scene_time.isnot(None),
+                                 Detection.embedding.isnot(None)).count()
+    return {
+        "detections_in_group": total,
+        "comparable": comparable,
+        "excluded_no_scene_time": no_clock,
+        "excluded_no_embedding": no_embedding,
+        "note": ("A sighting with no scene clock cannot be placed on a "
+                 "journey: wall time records when we connected to the loop, "
+                 "not when the vehicle passed."),
+    }
+
+
+def persist_journeys(group: str, journeys: list[Journey],
+                     min_similarity: float = 0.84) -> int:
     """Replace the stored journeys for one group.
 
     Replaced rather than appended: mining is deterministic over the index, so a
     second run of the same group produces the same journeys and appending would
     show an operator each one several times.
     """
     from netra.core.db import SessionLocal
     from netra.core.models import MinedJourney
 
     with SessionLocal() as db:
         db.query(MinedJourney).filter(MinedJourney.time_group == group).delete()
         for j in journeys:
             first = datetime.fromisoformat(j.hops[0].at)
             last = datetime.fromisoformat(j.hops[-1].at)
             db.add(MinedJourney(
                 time_group=group, hop_count=len(j.hops), cameras=j.cameras,
                 total_km=round(j.total_km, 2), elapsed_s=round(j.elapsed_s, 1),
                 mean_similarity=round(j.mean_similarity, 3),
                 confidence=j.confidence, first_seen=first, last_seen=last,
+                min_similarity=min_similarity, truncated=j.truncated,
                 hops=[asdict(h) for h in j.hops], note=j.note))
         db.commit()
     return len(journeys)
 
 
-def stored_journeys(group: str, limit: int = MAX_JOURNEYS) -> list[dict]:
-    """Journeys mined earlier, so the console need not re-run the mining."""
+def stored_journeys(group: str, limit: int = MAX_JOURNEYS,
+                    min_hops: int = 2, min_similarity: float = 0.0) -> list[dict]:
+    """Journeys mined earlier, so the console need not re-run the mining.
+
+    `min_hops` and `min_similarity` filter the stored rows rather than
+    re-mining. Filtering is exact for hop count; for similarity it keeps
+    journeys whose weakest hop clears the bar, which is a subset of what
+    re-mining at that threshold would produce — a stricter threshold can also
+    change which chains form, so a caller who needs that must ask for a
+    refresh. The endpoint says which of the two it did.
+    """
     from netra.core.db import SessionLocal
     from netra.core.models import MinedJourney
 
     with SessionLocal() as db:
         rows = (db.query(MinedJourney)
-                .filter(MinedJourney.time_group == group)
-                .order_by(MinedJourney.confidence.desc()).limit(limit).all())
+                .filter(MinedJourney.time_group == group,
+                        MinedJourney.hop_count >= min_hops)
+                .order_by(MinedJourney.confidence.desc()).all())
+    if min_similarity > 0:
+        rows = [r for r in rows
+                if min(([h.get("similarity") or 1.0 for h in r.hops] or [0.0]))
+                >= min_similarity]
+    rows = rows[:limit]
     return [{
         "time_group": r.time_group, "hops": r.hops, "hop_count": r.hop_count,
         "cameras": r.cameras, "total_km": r.total_km, "elapsed_s": r.elapsed_s,
         "mean_similarity": r.mean_similarity, "confidence": r.confidence,
+        "truncated": bool(r.truncated), "mined_at_similarity": r.min_similarity,
         "note": r.note, "mined_at": r.created_at.isoformat() if r.created_at else None,
     } for r in rows]
 
 
 # --------------------------------------------------------------- self-check --
 def _self_check() -> None:
     """Mining is checked on synthetic detections: no network, no GPU, no model."""
     from datetime import timedelta, timezone
 
     import numpy as np
 
     def vec(seed: int):
         rng = np.random.default_rng(seed)
         v = rng.normal(size=32).astype(np.float32)
         return (v / np.linalg.norm(v)).tolist()
 
+    L_MAX_HOPS = MAX_CHAIN_HOPS
     silver, red = vec(1), vec(2)
     assert similarity(silver, red) < 0.5, "test vectors must be distinguishable"
 
     class FakeCam:
         def __init__(self, cid, name, lat, lon):
             self.id, self.name, self.lat, self.lon = cid, name, lat, lon
 
     class FakeDet:
         _next = [1]
 
@@ -656,15 +771,45 @@ def _self_check() -> None:
                 FakeDet(c14, t0 + timedelta(minutes=3), silver, scene=False)]
     assert find_journeys("ahmedabad-13jun", detections=no_clock) == []
 
     # Dissimilar vehicles are not chained together.
     unlike = [FakeDet(c04, t0, silver), FakeDet(c14, t0 + timedelta(minutes=3), red)]
     assert find_journeys("ahmedabad-13jun", detections=unlike) == []
 
     # An unknown group mines nothing rather than raising.
     assert find_journeys("no-such-group", detections=dets) == []
 
+    # Exclusions are reported to the caller, not computed and discarded.
+    report: dict = {}
+    find_journeys("ahmedabad-13jun", detections=no_clock + [FakeDet(c10, t0, silver)],
+                  report=report)
+    assert report["excluded"]["no_scene_time"] == 1, report
+    assert report["excluded"]["wrong_group"] == 1, report
+    assert report["considered"] == 1, report
+
+    # A long chain must not become a maximum-confidence mega-journey. Every
+    # individual leg here is feasible, so nothing but the chain ceilings stops
+    # it running to a thousand hops.
+    long_run = []
+    for k in range(400):
+        cam = (c04, c14, c01)[k % 3]
+        long_run.append(FakeDet(cam, t0 + timedelta(minutes=3 * k), silver))
+    long_j = find_journeys("ahmedabad-13jun", detections=long_run)
+    assert long_j, "a long chain should still produce journeys"
+    longest = max(long_j, key=lambda j: j.hop_count)
+    assert longest.hop_count <= L_MAX_HOPS, longest.hop_count
+    assert all(j.elapsed_s <= MAX_JOURNEY_SECONDS for j in long_j),         [j.elapsed_s for j in long_j]
+    assert longest.truncated, "a chain cut at a ceiling must say so"
+    # Length costs confidence rather than earning it: the longest chain scores
+    # below a two-hop journey built from the identical embedding, and no
+    # journey of more than two hops can reach the cap.
+    two_hop = find_journeys("ahmedabad-13jun", detections=two)[0]
+    assert longest.confidence < two_hop.confidence, (longest.confidence,
+                                                     two_hop.confidence)
+    assert all(j.confidence < MAX_CONFIDENCE
+               for j in long_j if j.hop_count > 2),         [(j.hop_count, j.confidence) for j in long_j]
+
     print("loop_index self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/api/app.py b/netra/api/app.py
index 0779f44..b5fb855 100644
--- a/netra/api/app.py
+++ b/netra/api/app.py
@@ -895,66 +895,92 @@ def cloned_plates(min_confidence: float = Query(0.6, ge=0.0, le=0.99),
     return {
         "findings": [f.to_dict() for f in findings[:limit]],
         "count": len(findings),
         "min_confidence": min_confidence,
         "note": ("Findings are inferred from OCR reads on wide-area cameras and "
                  "are never certain. Only sightings sharing a recording session "
                  "are compared."),
     }
 
 
+#: Mining is an appearance comparison across a whole indexed recording, so a
+#: console polling this endpoint must not trigger one on every poll. A result
+#: is remembered for this long — including an *empty* result, which is the
+#: expensive case: with nothing stored, every poll would otherwise re-mine and
+#: re-write while detection is running, which is exactly the starvation class
+#: the plan warns about.
+JOURNEY_REMINE_COOLDOWN_S = 300.0
+_journeys_mined_at: dict[str, float] = {}
+
+
 @app.get("/api/analytics/journeys")
 def mined_journeys(group: str = Query(..., min_length=3, max_length=64),
                    min_similarity: float = Query(0.84, ge=0.5, le=0.99),
                    min_hops: int = Query(2, ge=2, le=10),
                    refresh: bool = False,
                    limit: int = Query(50, ge=1, le=200),
                    _p=Depends(require("read"))):
     """Vehicles that genuinely appear on more than one camera of one recording.
 
     The grid replays fixed recordings, and the cameras of a time group share
     the clock burnt into their frames, so a chain built on scene time is a real
     journey through the Government's own footage rather than a demonstration.
 
-    Served from the mined store; `refresh` re-runs the mining, which is an
-    appearance comparison across the whole index and is not cheap.
+    Served from the mined store, filtered by `min_hops` and `min_similarity`.
+    Those parameters only *re-mine* under `refresh`, because a stricter
+    threshold can change which chains form and not merely which survive; the
+    response says which of the two happened.
     """
-    from netra.analytics.loop_index import (find_journeys, persist_journeys,
-                                            stored_journeys)
+    import time as _time
+
+    from netra.analytics.loop_index import (exclusion_report, find_journeys,
+                                            persist_journeys, stored_journeys)
     from netra.core.geo import TIME_GROUPS
 
     if group not in TIME_GROUPS:
         raise HTTPException(status_code=400,
                             detail=f"unknown time group; known groups are "
                                    f"{', '.join(sorted(TIME_GROUPS))}")
 
-    rows = [] if refresh else stored_journeys(group, limit=limit)
+    rows = [] if refresh else stored_journeys(group, limit=limit,
+                                              min_hops=min_hops,
+                                              min_similarity=min_similarity)
+    last = _journeys_mined_at.get(group, 0.0)
+    cooling = (_time.time() - last) < JOURNEY_REMINE_COOLDOWN_S
     mined = False
-    if not rows:
+    if refresh or (not rows and not cooling):
+        report: dict = {}
         journeys = find_journeys(group, min_similarity=min_similarity,
-                                 min_hops=min_hops, limit=limit)
-        persist_journeys(group, journeys)
+                                 min_hops=min_hops, limit=limit, report=report)
+        persist_journeys(group, journeys, min_similarity=min_similarity)
+        _journeys_mined_at[group] = _time.time()
         rows = [j.to_dict() for j in journeys]
         mined = True
 
     _audit("analytics.journeys", target=group,
            detail={"journeys": len(rows), "mined": mined})
     return {
         "group": group,
         "cameras": TIME_GROUPS[group],
         "journeys": rows,
         "count": len(rows),
         "mined_now": mined,
+        "filters_applied": {"min_hops": min_hops,
+                            "min_similarity": min_similarity,
+                            "applied_by": "mining" if mined else "filter"},
+        "index": exclusion_report(group),
         "note": ("Appearance-based candidate journeys for operator "
                  "confirmation, not identifications. Chained on the clock "
                  "recorded in the video, never on capture time, and never "
-                 "across recording sessions."),
+                 "across recording sessions. Journeys are mined periodically "
+                 "and served from store; pass refresh=true to re-mine at "
+                 "these thresholds."),
     }
 
 
 @app.get("/api/report", response_class=HTMLResponse)
 def output_report(hours: int = Query(24, ge=1, le=720)):
     """Operational output report, printable to PDF from the browser.
 
     This is the output report the submission asks for: detected vehicles and
     plates with timestamps, watchlist matches with their reasoning, zone
     events, per-camera activity, and the cameras measured as unable to deliver.
diff --git a/netra/core/db.py b/netra/core/db.py
index 4da81c1..adcb46c 100644
--- a/netra/core/db.py
+++ b/netra/core/db.py
@@ -17,20 +17,22 @@ SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
 #: Columns added to tables that already exist in the field. `create_all` only
 #: creates missing *tables*, so a column added to a model would be absent from
 #: an operator's existing data/netra.db and every ORM read of that table would
 #: fail. These are applied additively at start-up rather than asking anyone to
 #: delete their evidence database.
 #: ponytail: a hand-kept list, not a migration tool. Its ceiling is additive,
 #: nullable/defaulted columns on SQLite; a type change or a drop needs Alembic.
 _ADDED_COLUMNS = [
     ("traffic_stats", "cumulative_total", "INTEGER DEFAULT 0"),
     ("traffic_stats", "loops_seen", "INTEGER DEFAULT 0"),
+    ("mined_journeys", "min_similarity", "REAL DEFAULT 0.84"),
+    ("mined_journeys", "truncated", "BOOLEAN DEFAULT 0"),
 ]
 
 
 def _apply_added_columns() -> None:
     from sqlalchemy import inspect, text
     inspector = inspect(engine)
     existing = set(inspector.get_table_names())
     with engine.begin() as conn:
         for table, column, ddl in _ADDED_COLUMNS:
             if table not in existing:
diff --git a/netra/core/models.py b/netra/core/models.py
index 79081f4..9a0e838 100644
--- a/netra/core/models.py
+++ b/netra/core/models.py
@@ -233,17 +233,22 @@ class MinedJourney(Base):
     id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
     #: only cameras sharing a recorded clock can be chained, so a journey
     #: belongs to exactly one recording session
     time_group: Mapped[str] = mapped_column(String(64), index=True)
     hop_count: Mapped[int] = mapped_column(Integer, default=0)
     cameras: Mapped[list] = mapped_column(JSON, default=list)
     total_km: Mapped[float] = mapped_column(Float, default=0.0)
     elapsed_s: Mapped[float] = mapped_column(Float, default=0.0)
     mean_similarity: Mapped[float] = mapped_column(Float, default=0.0)
     confidence: Mapped[float] = mapped_column(Float, default=0.0, index=True)
+    #: the appearance threshold this journey was mined at, so a later request
+    #: asking for a stricter one can tell whether these rows answer it
+    min_similarity: Mapped[float] = mapped_column(Float, default=0.84)
+    #: the chain was cut at a ceiling rather than ending naturally
+    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
     #: scene time, not wall time: these bound the journey on the recorded clock
     first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
     last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
     hops: Mapped[list] = mapped_column(JSON, default=list)
     note: Mapped[str | None] = mapped_column(Text)
     created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=_utcnow, index=True)
diff --git a/tools/index_loops.py b/tools/index_loops.py
index f92de72..6dc4c97 100644
--- a/tools/index_loops.py
+++ b/tools/index_loops.py
@@ -15,22 +15,22 @@ detections already stored.
 from __future__ import annotations
 
 import argparse
 import os
 import sys
 import time
 
 sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
 
 from netra.analytics.loop_index import (estimate_loop_length,  # noqa: E402
-                                        find_journeys, index_camera,
-                                        persist_journeys)
+                                        exclusion_report, find_journeys,
+                                        index_camera, persist_journeys)
 from netra.core.db import SessionLocal, init_db  # noqa: E402
 from netra.core.geo import TIME_GROUPS  # noqa: E402
 from netra.core.models import Camera  # noqa: E402
 
 
 def _print_journeys(journeys: list) -> None:
     if not journeys:
         print("No journeys found. A journey needs the same vehicle on two "
               "cameras of one group, with a readable scene clock on both.")
         return
@@ -87,38 +87,49 @@ def main() -> int:
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
-                          f"{f'{length:.1f}s' if length else 'not observed'}")
+                          f"{f'{length:.1f}s (restart to restart)' if length
+                             else 'not measured within the probe timeout'}")
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
+    report: dict = {}
     journeys = find_journeys(args.group, min_similarity=args.min_similarity,
-                             min_hops=args.min_hops)
+                             min_hops=args.min_hops, report=report)
+    excluded = report.get("excluded", {})
+    print(f"  {report.get('considered', 0)} sightings comparable, "
+          f"{excluded.get('no_scene_time', 0)} excluded for no scene clock, "
+          f"{excluded.get('no_embedding', 0)} for no appearance vector, "
+          f"{excluded.get('wrong_group', 0)} outside the group")
+    index = exclusion_report(args.group)
+    if index:
+        print(f"  index holds {index['detections_in_group']} detections on "
+              f"these cameras, {index['comparable']} of them comparable")
     if not args.no_persist:
         persist_journeys(args.group, journeys)
     _print_journeys(journeys)
     return 0
 
 
 if __name__ == "__main__":
     raise SystemExit(main())
