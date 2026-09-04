# Review package — Task 2

71c0e99 Detect cloned plates from space-time impossibility

 .../task-2-report.md                               |  69 +++++
 netra/analytics/cloned_plate.py                    | 299 +++++++++++++++++++++
 netra/analytics/route.py                           |  12 +-
 netra/api/app.py                                   |  25 ++
 netra/api/assistant.py                             |  48 +++-
 netra/core/timing.py                               |  20 ++
 6 files changed, 460 insertions(+), 13 deletions(-)

diff --git a/netra/analytics/cloned_plate.py b/netra/analytics/cloned_plate.py
new file mode 100644
index 0000000..6ddb59e
--- /dev/null
+++ b/netra/analytics/cloned_plate.py
@@ -0,0 +1,299 @@
+"""Cloned- and forged-plate detection.
+
+The same registration number appearing in two places too far apart for one
+vehicle to have travelled is not a tracking failure - it is evidence that two
+vehicles are wearing the same plate. Plate cloning is a named offence and the
+detection falls straight out of the space-time feasibility check already used
+to veto impossible hops in `route.py`; here the same arithmetic is inverted and
+reported as a finding in its own right.
+
+Three constraints keep the finding honest:
+
+  * Only sightings within one recording session are ever compared. The Sentinel
+    sandbox holds several independently recorded feeds, so a timestamp from one
+    session says nothing about a timestamp from another. Comparing across them
+    would manufacture an accusation out of a clock offset - the single most
+    dangerous failure mode here.
+  * Two sightings on the same camera are never a clone. That is one vehicle
+    seen twice, whatever the interval.
+  * Confidence is capped below certainty. Every finding rests on OCR output
+    from wide-area night cameras, and OCR misreads plates into each other. The
+    `reason` field carries the arithmetic so an officer can check the claim
+    rather than take it.
+"""
+from __future__ import annotations
+
+from dataclasses import dataclass, asdict
+from datetime import datetime
+
+from netra.analytics.matching import (MAX_PLAUSIBLE_KMH, normalise_plate,
+                                      spacetime_plausible)
+from netra.core.geo import haversine_km, time_group
+from netra.core.timing import sighting_time
+
+# Plate confidence assumed when a detection carries none. Deliberately middling:
+# an unscored read must neither inflate nor destroy a finding.
+DEFAULT_PLATE_CONF = 0.5
+
+# A finding can never be certain - see the module docstring.
+MAX_CONFIDENCE = 0.99
+
+
+@dataclass
+class CloneFinding:
+    plate: str
+    sighting_a: dict
+    sighting_b: dict
+    distance_km: float
+    elapsed_s: float
+    implied_kmh: float | None
+    confidence: float
+    reason: str
+
+    def to_dict(self) -> dict:
+        return asdict(self)
+
+
+def _sighting_dict(det) -> dict:
+    cam = getattr(det, "camera", None)
+    at = sighting_time(det)
+    return {
+        "detection_id": det.id,
+        "camera_id": det.camera_id,
+        "camera_name": cam.name if cam else det.camera_id,
+        "lat": cam.lat if cam else None,
+        "lon": cam.lon if cam else None,
+        "at": at.isoformat() if isinstance(at, datetime) else at,
+    }
+
+
+def _confidence(implied_kmh: float | None, conf_a: float, conf_b: float) -> float:
+    """How strongly this pair argues that two vehicles share one plate.
+
+    Two independent factors:
+
+      * How badly the pair violates plausibility. A pair implying 200 km/h is
+        weak - a motorway run, a slightly wrong timestamp or an approximate
+        camera coordinate could all produce it. One implying 5,000 km/h has no
+        innocent explanation. Scored as 1 - (limit / implied), which approaches
+        1 as the implied speed runs away and is near 0 just over the limit.
+      * How well the plate was read at both ends. The weaker read governs: a
+        confident read paired with a guess is still a guess.
+    """
+    if implied_kmh is None or implied_kmh <= MAX_PLAUSIBLE_KMH:
+        # Simultaneous sightings at separated cameras imply infinite speed.
+        violation = 1.0 if implied_kmh is None else 0.0
+    else:
+        violation = 1.0 - (MAX_PLAUSIBLE_KMH / implied_kmh)
+
+    weakest = min(conf_a, conf_b)
+    # Plate quality can halve the score but never zero it: even a poor read of
+    # the same string in two impossible places is worth an officer's attention.
+    return round(min(MAX_CONFIDENCE, violation * (0.5 + 0.5 * weakest)), 3)
+
+
+def find_clones(detections: list, min_confidence: float = 0.6) -> list[CloneFinding]:
+    """Report registration numbers seen in physically incompatible places.
+
+    `detections` are ORM Detection rows with `.camera` loaded.
+
+    ponytail: consecutive pairs only, after ordering by time. A clone active
+    across three cameras is reported as its adjacent impossible hops rather
+    than as one multi-camera cluster; the ceiling is that the officer reads two
+    findings instead of one, not that anything is missed.
+    """
+    groups: dict[str, list] = {}
+    for det in detections:
+        plate = normalise_plate(det.plate_text)
+        # A partial read cannot identify a vehicle, so it cannot evidence a
+        # clone either: "AB12" is shared by thousands of legitimate plates.
+        if len(plate) < 6:
+            continue
+        if sighting_time(det) is None:
+            continue
+        groups.setdefault(plate, []).append(det)
+
+    findings: list[CloneFinding] = []
+    for plate, dets in groups.items():
+        if len(dets) < 2:
+            continue
+        dets.sort(key=sighting_time)
+
+        for prev, cur in zip(dets, dets[1:]):
+            # One vehicle passing the same camera twice is not a clone.
+            if prev.camera_id == cur.camera_id:
+                continue
+
+            # Different recording sessions are not simultaneous in reality.
+            # Unknown group (None) is also not comparable - we cannot show the
+            # two clocks agree, so we must not claim the speed between them.
+            group = time_group(prev.camera_id)
+            if group is None or group != time_group(cur.camera_id):
+                continue
+
+            cam_a, cam_b = getattr(prev, "camera", None), getattr(cur, "camera", None)
+            coords = (getattr(cam_a, "lat", None), getattr(cam_a, "lon", None),
+                      getattr(cam_b, "lat", None), getattr(cam_b, "lon", None))
+            if None in coords:
+                # Without both positions there is no distance and therefore no
+                # impossibility to assert.
+                continue
+
+            km = haversine_km(*coords)
+            secs = (sighting_time(cur) - sighting_time(prev)).total_seconds()
+            ok, why = spacetime_plausible(km, secs)
+            if ok:
+                continue
+            if km <= 0.0:
+                # Co-located cameras: no distance was covered, so no speed is
+                # implied however close together the sightings fall.
+                continue
+
+            # Report the plate as OCR actually read it, not the confusion-folded
+            # key: an officer shown "6J01A81234" would reasonably think the
+            # system had flagged a different vehicle entirely.
+            read_a = (prev.plate_text or plate).upper()
+            read_b = (cur.plate_text or plate).upper()
+            shown = read_a if read_a == read_b else f"{read_a} / {read_b}"
+
+            implied = km / (secs / 3600.0) if secs > 0 else None
+            conf = _confidence(implied,
+                               prev.plate_conf if prev.plate_conf is not None else DEFAULT_PLATE_CONF,
+                               cur.plate_conf if cur.plate_conf is not None else DEFAULT_PLATE_CONF)
+            if conf < min_confidence:
+                continue
+
+            a, b = _sighting_dict(prev), _sighting_dict(cur)
+            if implied is None:
+                arithmetic = (f"{shown} was recorded at {a['camera_name']} and "
+                              f"{b['camera_name']}, {km:.1f} km apart, with no "
+                              f"time between the two sightings")
+            else:
+                arithmetic = (f"{shown} was recorded at {a['camera_name']} and "
+                              f"{b['camera_name']}, {km:.1f} km apart, "
+                              f"{secs:.0f}s apart - implying {implied:.0f} km/h "
+                              f"against a {MAX_PLAUSIBLE_KMH:.0f} km/h ceiling")
+            reason = (f"{arithmetic}. Both cameras share the {group} recording "
+                      f"session, so the timestamps are comparable. One vehicle "
+                      f"cannot have made this journey, so the plate is likely "
+                      f"cloned or forged. Plate reads scored "
+                      f"{prev.plate_conf if prev.plate_conf is not None else 'unscored'} "
+                      f"and {cur.plate_conf if cur.plate_conf is not None else 'unscored'}; "
+                      f"verify against the evidence images before acting.")
+            if read_a != read_b:
+                reason += (f" The two reads differ by characters OCR is known to "
+                           f"confuse and were treated as the same plate.")
+
+            findings.append(CloneFinding(
+                plate=shown, sighting_a=a, sighting_b=b,
+                distance_km=round(km, 2), elapsed_s=round(secs, 1),
+                implied_kmh=round(implied, 1) if implied is not None else None,
+                confidence=conf, reason=reason))
+
+    findings.sort(key=lambda f: -f.confidence)
+    return findings
+
+
+def _self_check() -> None:
+    """A clone finding is an accusation, so every guard here protects someone."""
+    from datetime import timedelta, timezone
+
+    class FakeCam:
+        def __init__(self, cid, name, lat, lon):
+            self.id, self.name, self.lat, self.lon = cid, name, lat, lon
+
+    class FakeDet:
+        _next = [1]
+
+        def __init__(self, cam, plate, at, conf=0.9):
+            self.camera, self.camera_id = cam, cam.id
+            self.plate_text, self.plate_conf = plate, conf
+            self.evidence_path = None
+            self.scene_time, self.wall_time = at, at
+            self.id = FakeDet._next[0]
+            FakeDet._next[0] += 1
+
+    t0 = datetime(2026, 6, 13, 23, 20, tzinfo=timezone.utc)
+    c04 = FakeCam("cam04", "Paldi Circle", 23.0130, 72.5620)
+    c14 = FakeCam("cam14", "Delight RLVD", 23.0290, 72.5700)
+    c15 = FakeCam("cam15", "Vasna", 23.0180, 72.5300)
+    c10 = FakeCam("cam10", "Char Chowk", 21.5220, 70.4570)   # other session
+    c99 = FakeCam("cam99", "Unlisted", 23.0000, 72.5000)     # no time group
+
+    # Impossible pair: ~1.9 km in two seconds is flagged.
+    out = find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                       FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=2))])
+    assert len(out) == 1, out
+    assert out[0].plate == "GJ01AB1234" and out[0].confidence <= MAX_CONFIDENCE, out[0]
+    assert "km/h" in out[0].reason and "2.0 km" in out[0].reason, out[0].reason
+
+    # A plausible pair is not a clone.
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                        FakeDet(c14, "GJ01AB1234", t0 + timedelta(minutes=3))]) == []
+
+    # Same camera seconds apart: one vehicle seen twice, never a clone.
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                        FakeDet(c04, "GJ01AB1234", t0 + timedelta(seconds=1))]) == []
+
+    # Different recording sessions must never be compared, however impossible
+    # the arithmetic would look. This is the constraint that stops the platform
+    # accusing an innocent vehicle on the strength of a clock offset.
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                        FakeDet(c10, "GJ01AB1234", t0 + timedelta(seconds=5))]) == []
+    # A camera in no known session is equally incomparable.
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                        FakeDet(c99, "GJ01AB1234", t0 + timedelta(seconds=5))]) == []
+
+    # A single sighting yields nothing.
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0)]) == []
+
+    # Confidence ordering: the worse violation must score higher.
+    mild = find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                        FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=40))],
+                       min_confidence=0.0)
+    severe = find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                          FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=1))],
+                         min_confidence=0.0)
+    assert mild and severe, (mild, severe)
+    assert severe[0].confidence > mild[0].confidence, (severe[0], mild[0])
+    # ...and the mild one is weak enough that the default threshold hides it.
+    assert mild[0].confidence < 0.6, mild[0]
+
+    # A weaker plate read must not score as highly as a confident one.
+    weak = find_clones([FakeDet(c04, "GJ01AB1234", t0, conf=0.3),
+                        FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=1), conf=0.3)],
+                       min_confidence=0.0)
+    assert weak[0].confidence < severe[0].confidence, (weak[0], severe[0])
+
+    # Missing coordinates must not crash and must not produce a finding.
+    blind = FakeCam("cam15", "Vasna", None, None)
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                        FakeDet(blind, "GJ01AB1234", t0 + timedelta(seconds=2))]) == []
+
+    # Partial reads cannot identify a vehicle and must not accuse one.
+    assert find_clones([FakeDet(c04, "AB12", t0),
+                        FakeDet(c14, "AB12", t0 + timedelta(seconds=2))]) == []
+
+    # Three cameras, two impossible hops: both are reported.
+    chain = find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                         FakeDet(c14, "GJ01AB1234", t0 + timedelta(seconds=2)),
+                         FakeDet(c15, "GJ01AB1234", t0 + timedelta(seconds=4))])
+    assert len(chain) == 2, chain
+    assert chain[0].confidence >= chain[1].confidence, chain
+
+    # Two reads that differ only by a known OCR confusion are the same plate,
+    # but the finding must show both as read rather than the folded key.
+    folded = find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                          FakeDet(c14, "GJ0IAB1234", t0 + timedelta(seconds=2))])
+    assert len(folded) == 1 and "6J01A81234" not in folded[0].plate, folded
+    assert "GJ0IAB1234" in folded[0].plate, folded[0].plate
+
+    # Distinct plates are never cross-compared.
+    assert find_clones([FakeDet(c04, "GJ01AB1234", t0),
+                        FakeDet(c14, "GJ09ZZ8888", t0 + timedelta(seconds=2))]) == []
+
+    print("cloned_plate self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/analytics/route.py b/netra/analytics/route.py
index 496b900..565b3e8 100644
--- a/netra/analytics/route.py
+++ b/netra/analytics/route.py
@@ -13,20 +13,22 @@ Two constraints shape this:
   * A hop that would require an impossible speed is rejected rather than drawn.
     A route the operator cannot trust is worse than a short one.
 """
 from __future__ import annotations
 
 from dataclasses import dataclass, asdict
 from datetime import datetime
 
 from netra.analytics.matching import normalise_plate, spacetime_plausible
 from netra.core.geo import haversine_km, time_group
+# Shared with cloned-plate detection so both modules order sightings identically.
+from netra.core.timing import sighting_time as _sighting_time
 
 
 @dataclass
 class Hop:
     camera_id: str
     camera_name: str
     lat: float | None
     lon: float | None
     at: datetime
     plate_text: str | None
@@ -55,30 +57,20 @@ class Route:
             "query": self.query,
             "hops": [asdict(h) for h in self.hops],
             "rejected": self.rejected,
             "total_km": round(self.total_km, 2),
             "duration_s": round(self.duration_s, 1),
             "time_groups": self.time_groups,
             "hop_count": len(self.hops),
         }
 
 
-def _sighting_time(det) -> datetime:
-    """Prefer the timestamp burned into the source video over our own clock.
-
-    The sandbox replays recordings, so wall time reflects when we happened to
-    connect, not when the scene occurred. Where the camera's own overlay has
-    been parsed, that is the only meaningful ordering.
-    """
-    return det.scene_time or det.wall_time
-
-
 def build_route(detections: list, query: str, min_plate_score: float = 0.6) -> Route:
     """Chain detections of one vehicle into an ordered, validated route.
 
     `detections` are ORM Detection rows with `.camera` loaded.
     """
     target = normalise_plate(query)
     candidates = []
     for det in detections:
         obs = normalise_plate(det.plate_text)
         if not obs:
diff --git a/netra/api/app.py b/netra/api/app.py
index d2fecaa..e8bddf2 100644
--- a/netra/api/app.py
+++ b/netra/api/app.py
@@ -766,20 +766,45 @@ def traffic_history(camera_id: str | None = None, limit: int = Query(200, le=100
         if camera_id:
             q = q.filter(TrafficStat.camera_id == camera_id)
         rows = q.order_by(TrafficStat.bucket_start.desc()).limit(limit).all()
         return [{
             "camera_id": r.camera_id, "at": r.bucket_start.isoformat(),
             "total": r.total, "counts_by_class": r.counts_by_class,
             "directions": r.directions, "mean_dwell_s": r.mean_dwell_s,
         } for r in rows]
 
 
+@app.get("/api/analytics/cloned-plates")
+def cloned_plates(min_confidence: float = Query(0.6, ge=0.0, le=0.99),
+                  limit: int = Query(50, ge=1, le=500)):
+    """Registration numbers seen in two places one vehicle could not have reached.
+
+    Read-only analysis over stored detections; every finding carries the
+    distance, elapsed time and implied speed behind it so an officer can check
+    the claim rather than take it on trust.
+    """
+    from netra.analytics.cloned_plate import find_clones
+    with SessionLocal() as db:
+        rows = (db.query(Detection).options(joinedload(Detection.camera))
+                .filter(Detection.plate_text.isnot(None)).all())
+        findings = find_clones(rows, min_confidence=min_confidence)
+    _audit("analytics.cloned_plates", detail={"findings": len(findings)})
+    return {
+        "findings": [f.to_dict() for f in findings[:limit]],
+        "count": len(findings),
+        "min_confidence": min_confidence,
+        "note": ("Findings are inferred from OCR reads on wide-area cameras and "
+                 "are never certain. Only sightings sharing a recording session "
+                 "are compared."),
+    }
+
+
 @app.get("/api/report", response_class=HTMLResponse)
 def output_report(hours: int = Query(24, ge=1, le=720)):
     """Operational output report, printable to PDF from the browser.
 
     This is the output report the submission asks for: detected vehicles and
     plates with timestamps, watchlist matches with their reasoning, zone
     events, per-camera activity, and the cameras measured as unable to deliver.
     """
     from netra.api.report import build_report
     _audit("report.generate", detail={"hours": hours})
diff --git a/netra/api/assistant.py b/netra/api/assistant.py
index 15b74e2..49d3530 100644
--- a/netra/api/assistant.py
+++ b/netra/api/assistant.py
@@ -137,20 +137,48 @@ def _find_plate(q: str) -> dict:
             f"{last.camera_name} ({last.at:%H:%M:%S}), covering "
             f"{route.total_km} km.")
     if route.rejected:
         text += (f" {len(route.rejected)} further sightings were excluded as "
                  f"not physically consistent or from a different recording "
                  f"session.")
     return _answer(text, route.to_dict(),
                    [{"label": f"Trace {plate}", "view": "route", "query": plate}])
 
 
+def _cloned_plates(_q: str) -> dict:
+    from netra.analytics.cloned_plate import find_clones
+    with SessionLocal() as db:
+        rows = (db.query(Detection).options(joinedload(Detection.camera))
+                .filter(Detection.plate_text.isnot(None)).all())
+        findings = find_clones(rows)
+
+    if not findings:
+        return _answer(
+            "No cloned plates detected. A plate is flagged only when the same "
+            "registration is read at two cameras in the same recording session, "
+            "too far apart for one vehicle to have covered in the time between - "
+            "sightings from different sessions are never compared.",
+            {"count": 0},
+            [{"label": "Open detections", "view": "detections"}])
+
+    top = findings[0]
+    return _answer(
+        f"{len(findings)} possible cloned plates. Strongest: {top.plate} at "
+        f"{top.sighting_a['camera_name']} and {top.sighting_b['camera_name']}, "
+        f"{top.distance_km} km apart in {top.elapsed_s:.0f}s "
+        f"(confidence {top.confidence}). This is inferred from OCR reads and is "
+        f"never certain - check the evidence images before acting.",
+        {"count": len(findings),
+         "findings": [f.to_dict() for f in findings[:10]]},
+        [{"label": f"Trace {top.plate}", "view": "route", "query": top.plate}])
+
+
 def _watchlist_summary(_q: str) -> dict:
     with SessionLocal() as db:
         rows = db.query(WatchlistEntry).filter(
             WatchlistEntry.active.is_(True)).all()
     by_cat: dict[str, int] = {}
     for e in rows:
         by_cat[e.category] = by_cat.get(e.category, 0) + 1
     if not rows:
         return _answer("The watchlist is empty. Load the sample dataset or add "
                        "entries from the Watchlist screen.", {"total": 0},
@@ -194,30 +222,35 @@ def _coverage(_q: str) -> dict:
         f". Cross-camera tracing is valid within {len(TIME_GROUPS)} groups of "
         f"cameras that share a recording session.",
         {"by_city": by_city, "time_groups": TIME_GROUPS},
         [{"label": "Open map", "view": "map"}])
 
 
 def _help(_q: str) -> dict:
     return _answer(
         "I answer from live platform data. You can ask about camera health and "
         "which cameras are faulty, detection counts, current alerts, the "
-        "watchlist, pipeline status, coverage by location, or where a specific "
-        "registration number has been seen.",
+        "watchlist, pipeline status, coverage by location, whether any plates look "
+        "cloned, or where a specific registration number has been seen.",
         {}, [{"label": "Camera health", "query": "which cameras are down"},
              {"label": "Current alerts", "query": "show me the alerts"},
              {"label": "Detections", "query": "how many detections"}])
 
 
 # Ordered: the first intent whose keywords appear wins, so specific
 # intents must precede general ones.
 INTENTS = [
+    # Ahead of the trace intent because "find cloned plates" contains "find";
+    # a question naming an actual registration number never reaches here, as
+    # `ask` routes those to the trace handler before the keyword loop runs.
+    (("clone", "cloned", "cloning", "forged", "forgery", "duplicate plate",
+      "fake plate"), _cloned_plates),
     (("where", "seen", "trace", "track", "find", "locate"), _find_plate),
     (("camera", "cameras", "down", "faulty", "degraded", "health", "broken"), _camera_health),
     (("alert", "alerts", "hit", "match", "matches"), _alert_summary),
     (("watchlist", "stolen", "wanted", "suspect", "blacklist"), _watchlist_summary),
     (("detection", "detections", "vehicles", "cars", "count", "how many"), _detection_summary),
     (("pipeline", "running", "status", "system"), _pipeline_status),
     (("coverage", "map", "location", "city", "where are"), _coverage),
     (("help", "what can you", "commands", "hello", "hi"), _help),
 ]
 
@@ -236,40 +269,49 @@ def ask(question: str) -> dict:
     if PLATE_RE.search(question):
         return _find_plate(question)
 
     for keywords, handler in INTENTS:
         if any(k in q for k in keywords):
             return handler(question)
 
     return _answer(
         "I could not match that to anything I can answer from platform data. "
         "Ask about camera health, detections, alerts, the watchlist, pipeline "
-        "status, coverage, or a specific registration number.",
+        "status, coverage, cloned plates, or a specific registration number.",
         {}, _help("")["actions"])
 
 
 def _self_check() -> None:
     """Routing decides which query runs; a wrong route gives a confident wrong
     answer, so the mapping is worth pinning down."""
     assert ask("")["answer"].startswith("I answer from live platform data")
 
     # A plate anywhere in the question must route to the trace handler.
     r = ask("where has GJ01AB1234 been seen?")
     assert "GJ01AB1234" in r["answer"], r
 
     r = ask("Any sign of GJ 18 XY 7788 today")
     assert "GJ18XY7788" in r["answer"], r
 
     # Intent routing without a plate.
     assert "cameras" in ask("which cameras are down?")["answer"].lower()
     assert ask("show me the alerts")["data"] is not None
     assert "watchlist" in ask("what is on the watchlist")["answer"].lower()
 
+    # The clone intent must not swallow a plate trace, and must win over the
+    # trace keywords when a question is about clones generally.
+    r = ask("any cloned plates?")
+    assert "clone" in r["answer"].lower(), r
+    r = ask("find cloned plates")
+    assert "clone" in r["answer"].lower(), r
+    r = ask("where has GJ01AB1234 been seen?")
+    assert "GJ01AB1234" in r["answer"], r
+
     # Unknown questions must decline rather than invent an answer.
     r = ask("what is the weather in Ahmedabad tomorrow")
     assert "could not match" in r["answer"], r
 
     print("assistant self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/core/timing.py b/netra/core/timing.py
new file mode 100644
index 0000000..4cab9a8
--- /dev/null
+++ b/netra/core/timing.py
@@ -0,0 +1,20 @@
+"""When a sighting actually happened.
+
+The sandbox replays recordings, so the wall clock records when we happened to
+connect to a feed, not when the scene occurred. Anything that orders or
+subtracts sighting times - route reconstruction, cloned-plate detection - must
+agree on the same preference, otherwise two modules reading the same rows can
+reach contradictory conclusions about the same vehicle.
+"""
+from __future__ import annotations
+
+from datetime import datetime
+
+
+def sighting_time(det) -> datetime:
+    """Prefer the timestamp burned into the source video over our own clock.
+
+    Where the camera's own overlay has been parsed, that is the only meaningful
+    ordering; wall time is the fallback for feeds with no readable overlay.
+    """
+    return det.scene_time or det.wall_time
