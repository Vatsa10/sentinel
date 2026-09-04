# Review package — Task 5

52f518e Learn what normal looks like per camera, per hour
b564f86 Refactor code structure for improved readability and maintainability

 netra/analytics/baseline.py | 342 ++++++++++++++++++++++++++++++++++++++++++++
 netra/api/app.py            |  77 +++++++++-
 netra/api/assistant.py      | 113 +++++++++++++--
 netra/core/db.py            |  28 ++++
 netra/core/models.py        |   9 ++
 netra/pipeline.py           |  48 ++++++-
 6 files changed, 599 insertions(+), 18 deletions(-)

diff --git a/netra/analytics/baseline.py b/netra/analytics/baseline.py
new file mode 100644
index 0000000..8a2853a
--- /dev/null
+++ b/netra/analytics/baseline.py
@@ -0,0 +1,342 @@
+"""Per-camera behavioural baselines: what is normal here, at this hour.
+
+Tens of thousands of detections are data, not information. A control room does
+not need to be told that camera 12 saw 41 vehicles; it needs to be told that 41
+is four times what that camera normally sees at 03:00, because that is what a
+blocked road, a diverted convoy or a forming crowd looks like from the outside.
+
+The model is deliberately the simplest one that can be defended in an enquiry:
+for each (camera, hour of day) the mean and standard deviation of the per-bucket
+vehicle count, and a z-score of the current reading against it. Two honesty
+constraints shape it:
+
+  * Below `MIN_SAMPLES` observations no judgement is offered at all. A "norm"
+    built from three buckets is noise, and calling a reading anomalous against
+    noise is fabrication dressed as analysis.
+  * Dispersion is floored, so a camera that has happened to see the same count
+    every time cannot generate an infinite z-score from a single extra vehicle.
+
+ponytail: a per-hour Gaussian ignores the difference between a Tuesday and a
+Sunday, and treats an hour boundary as a hard edge. Its ceiling is a camera
+whose traffic is strongly weekly rather than daily - a market street, a stadium
+approach - where a busy Saturday would read as an anomaly against a weekday
+norm. Adding day-of-week to the key is the next step, and needs roughly seven
+times the observation history before it earns its place.
+"""
+from __future__ import annotations
+
+import statistics
+from dataclasses import dataclass, field
+
+#: Observations required before any deviation judgement is offered. A hard
+#: floor, not a preference: below it the answer is "I do not know yet".
+MIN_SAMPLES = 5
+
+#: Standard deviation is floored here before any z-score is taken. A camera
+#: whose counts are identical every bucket has zero variance, and one extra
+#: vehicle against zero variance is an infinite deviation - arithmetically
+#: true, operationally absurd. One vehicle is the smallest difference the
+#: counter can even express, so it is the smallest dispersion worth believing.
+STDEV_FLOOR = 1.0
+
+#: z-score bands. Deliberately wide: an alert an operator learns to ignore is
+#: worse than no alert, and traffic counts are not normally distributed.
+Z_HIGH = 3.0
+Z_ELEVATED = 2.0
+Z_LOW = -2.0
+
+
+@dataclass
+class Baseline:
+    """What one camera normally sees in one hour of the day."""
+    camera_id: str
+    hour: int
+    mean: float
+    stdev: float
+    samples: int
+
+    @property
+    def sufficient(self) -> bool:
+        return self.samples >= MIN_SAMPLES
+
+    @property
+    def effective_stdev(self) -> float:
+        return max(self.stdev, STDEV_FLOOR)
+
+    def as_dict(self) -> dict:
+        return {"camera_id": self.camera_id, "hour": self.hour,
+                "mean": round(self.mean, 2), "stdev": round(self.stdev, 2),
+                "effective_stdev": round(self.effective_stdev, 2),
+                "samples": self.samples, "sufficient": self.sufficient}
+
+
+@dataclass
+class Assessment:
+    """A reading judged against a baseline, with the reasoning attached."""
+    camera_id: str
+    hour: int
+    observed: int
+    status: str            # insufficient_data|quiet|low|normal|elevated|high
+    z_score: float | None
+    explanation: str
+    baseline: Baseline | None = None
+    detail: dict = field(default_factory=dict)
+
+    @property
+    def anomalous(self) -> bool:
+        return self.status in ("quiet", "low", "elevated", "high")
+
+    def as_dict(self) -> dict:
+        return {"camera_id": self.camera_id, "hour": self.hour,
+                "observed": self.observed, "status": self.status,
+                "z_score": self.z_score, "anomalous": self.anomalous,
+                "explanation": self.explanation,
+                "baseline": self.baseline.as_dict() if self.baseline else None,
+                **self.detail}
+
+
+def _field(row, name: str, default=None):
+    """Read a field from either an ORM row or a plain dict.
+
+    The learner is fed `TrafficStat` rows in the running platform and synthetic
+    dicts in the self-check, and keeping it indifferent to which means the
+    self-check needs no database.
+    """
+    if isinstance(row, dict):
+        return row.get(name, default)
+    return getattr(row, name, default)
+
+
+def _hour_of(row) -> int | None:
+    """Hour of day from `bucket_start`, in UTC throughout.
+
+    Every stored timestamp on this platform is UTC, so the baseline is learned
+    and assessed in UTC. Mixing in a local hour would silently shift a norm by
+    the offset and make the 03:00 night baseline the 08:30 rush-hour one.
+    """
+    ts = _field(row, "bucket_start")
+    if ts is None:
+        return None
+    try:
+        from datetime import timezone
+        if ts.tzinfo is not None:
+            ts = ts.astimezone(timezone.utc)
+        return ts.hour
+    except AttributeError:
+        return None
+
+
+def learn(rows) -> dict[tuple[str, int], Baseline]:
+    """Learn per-(camera, hour) norms from `TrafficStat` rows.
+
+    `total` must be the traffic *during* that bucket. A cumulative counter would
+    make the learned mean a function of how long the platform has been running
+    rather than of how busy the road is, and every judgement drawn from it
+    meaningless.
+    """
+    grouped: dict[tuple[str, int], list[float]] = {}
+    for row in rows:
+        camera_id = _field(row, "camera_id")
+        hour = _hour_of(row)
+        total = _field(row, "total")
+        if camera_id is None or hour is None or total is None:
+            continue
+        grouped.setdefault((camera_id, int(hour)), []).append(float(total))
+
+    baselines: dict[tuple[str, int], Baseline] = {}
+    for (camera_id, hour), values in grouped.items():
+        mean = statistics.fmean(values)
+        # Sample standard deviation needs two points; one observation has no
+        # dispersion to speak of, and is below MIN_SAMPLES anyway.
+        stdev = statistics.stdev(values) if len(values) > 1 else 0.0
+        baselines[(camera_id, hour)] = Baseline(
+            camera_id=camera_id, hour=hour, mean=mean, stdev=stdev,
+            samples=len(values))
+    return baselines
+
+
+def assess(baseline: Baseline | None, observed: int) -> Assessment:
+    """Judge one reading against a baseline, or decline to."""
+    observed = int(observed)
+
+    if baseline is None:
+        return Assessment(
+            camera_id="", hour=-1, observed=observed,
+            status="insufficient_data", z_score=None,
+            explanation=("No baseline has been learned for this camera and "
+                         "hour yet, so this reading cannot be judged."))
+
+    if not baseline.sufficient:
+        return Assessment(
+            camera_id=baseline.camera_id, hour=baseline.hour, observed=observed,
+            status="insufficient_data", z_score=None, baseline=baseline,
+            explanation=(
+                f"Only {baseline.samples} observation"
+                f"{'' if baseline.samples == 1 else 's'} of {baseline.camera_id} "
+                f"at hour {baseline.hour:02d}:00 UTC; "
+                f"{MIN_SAMPLES} are required before this platform will call a "
+                f"reading normal or abnormal. Observed {observed}."))
+
+    z = (observed - baseline.mean) / baseline.effective_stdev
+    z = round(z, 2)
+    norm = (f"the norm for {baseline.camera_id} at hour {baseline.hour:02d}:00 "
+            f"UTC is {baseline.mean:.1f} "
+            f"(sd {baseline.effective_stdev:.1f}, {baseline.samples} samples)")
+
+    if observed == 0 and baseline.mean >= 1.0:
+        status = "quiet"
+        text = (f"No traffic counted, where {norm}. A road that normally carries "
+                f"vehicles and now carries none may be blocked, closed, or the "
+                f"camera's view obstructed.")
+    elif z >= Z_HIGH:
+        status = "high"
+        text = (f"{observed} vehicles, {z:+.1f} standard deviations above "
+                f"normal: {norm}.")
+    elif z >= Z_ELEVATED:
+        status = "elevated"
+        text = (f"{observed} vehicles, {z:+.1f} standard deviations above "
+                f"normal: {norm}.")
+    elif z <= Z_LOW:
+        status = "low"
+        text = (f"{observed} vehicles, {z:+.1f} standard deviations below "
+                f"normal: {norm}.")
+    else:
+        status = "normal"
+        text = (f"{observed} vehicles is within the usual range: {norm}.")
+
+    return Assessment(camera_id=baseline.camera_id, hour=baseline.hour,
+                      observed=observed, status=status, z_score=z,
+                      explanation=text, baseline=baseline)
+
+
+def detect_anomalies(baselines: dict[tuple[str, int], Baseline],
+                     current_stats, include_normal: bool = False
+                     ) -> list[Assessment]:
+    """Assess a set of current readings, most deviant first.
+
+    `current_stats` entries carry `camera_id`, `total`, and either an `hour` or
+    a `bucket_start` from which the UTC hour is taken.
+    """
+    out: list[Assessment] = []
+    for row in current_stats:
+        camera_id = _field(row, "camera_id")
+        if camera_id is None:
+            continue
+        hour = _field(row, "hour")
+        if hour is None:
+            hour = _hour_of(row)
+        if hour is None:
+            continue
+        hour = int(hour)
+        observed = int(_field(row, "total") or 0)
+
+        result = assess(baselines.get((camera_id, hour)), observed)
+        # `assess` cannot know the camera when there is no baseline at all.
+        result.camera_id = camera_id
+        result.hour = hour
+        if include_normal or result.status != "normal":
+            out.append(result)
+
+    # Insufficient-data entries sort last: they are information about the
+    # platform's own coverage, not about the road.
+    out.sort(key=lambda a: (a.z_score is None, -abs(a.z_score or 0.0)))
+    return out
+
+
+def _self_check() -> None:
+    """A baseline that flags the wrong thing costs an operator's trust, and one
+    that flags nothing is decoration, so both directions are pinned here. All
+    rows are synthetic: no database, no network."""
+    from datetime import datetime, timezone
+
+    def row(cam, hour, total):
+        return {"camera_id": cam, "total": total,
+                "bucket_start": datetime(2026, 9, 1, hour, 0,
+                                         tzinfo=timezone.utc)}
+
+    # A busy camera with a settled norm, plus a thin one with three samples.
+    rows = ([row("cam01", 9, n) for n in (40, 44, 38, 42, 46, 41)] +
+            [row("cam02", 9, n) for n in (10, 12, 11)] +
+            [row("cam01", 3, n) for n in (2, 3, 1, 2, 4, 2)])
+    b = learn(rows)
+
+    assert b[("cam01", 9)].samples == 6
+    assert 40 < b[("cam01", 9)].mean < 43, b[("cam01", 9)].mean
+    assert b[("cam02", 9)].samples == 3
+
+    # Hours are keyed separately: the night norm must not absorb the day norm.
+    assert b[("cam01", 3)].mean < 5, b[("cam01", 3)].mean
+
+    # Below MIN_SAMPLES no verdict is offered, however extreme the reading.
+    a = assess(b[("cam02", 9)], 500)
+    assert a.status == "insufficient_data", a
+    assert a.z_score is None and not a.anomalous, a
+    assert "5 are required" in a.explanation, a.explanation
+
+    # No baseline at all behaves the same way.
+    assert assess(None, 99).status == "insufficient_data"
+
+    # A normal reading is not flagged.
+    assert assess(b[("cam01", 9)], 42).status == "normal"
+
+    # A clear spike is flagged.
+    spike = assess(b[("cam01", 9)], 200)
+    assert spike.status == "high", spike
+    assert spike.z_score > Z_HIGH and spike.anomalous
+
+    moderate = assess(b[("cam01", 9)], 49)
+    assert moderate.status in ("elevated", "high"), moderate
+
+    # Zero traffic against a busy baseline is quiet, not merely low.
+    dead = assess(b[("cam01", 9)], 0)
+    assert dead.status == "quiet", dead
+    assert "blocked" in dead.explanation
+
+    # A genuinely low but non-zero reading is low.
+    assert assess(b[("cam01", 9)], 30).status == "low"
+
+    # Zero variance must not produce an infinite or absurd z-score.
+    flat = learn([row("cam03", 5, 20) for _ in range(8)])[("cam03", 5)]
+    assert flat.stdev == 0.0 and flat.effective_stdev == STDEV_FLOOR
+    one_more = assess(flat, 21)
+    assert one_more.z_score == 1.0, one_more
+    assert one_more.status == "normal", one_more
+    far = assess(flat, 25)
+    assert far.z_score == 5.0 and far.status == "high", far
+
+    # A quiet night camera is not swamped by the floor either: 2 vehicles
+    # against a norm of ~2 stays normal.
+    assert assess(b[("cam01", 3)], 2).status == "normal"
+
+    # detect_anomalies ranks the most deviant first and suppresses the normal.
+    found = detect_anomalies(b, [
+        {"camera_id": "cam01", "hour": 9, "total": 42},    # normal, dropped
+        {"camera_id": "cam01", "hour": 3, "total": 30},    # wild spike
+        {"camera_id": "cam02", "hour": 9, "total": 500},   # no verdict
+        {"camera_id": "cam01", "hour": 9, "total": 55},    # elevated
+    ])
+    assert [f.status for f in found] == ["high", "high", "insufficient_data"], \
+        [(f.camera_id, f.hour, f.status, f.z_score) for f in found]
+    assert found[0].camera_id == "cam01" and found[0].hour == 3, found[0]
+    assert found[-1].status == "insufficient_data", found[-1]
+    assert all(f.camera_id for f in found)
+
+    # An unknown camera is declined, not guessed at.
+    unknown = detect_anomalies(b, [{"camera_id": "cam99", "hour": 9, "total": 900}])
+    assert unknown[0].status == "insufficient_data"
+    assert unknown[0].camera_id == "cam99"
+
+    # bucket_start is accepted in place of an explicit hour.
+    via_ts = detect_anomalies(b, [row("cam01", 9, 200)])
+    assert via_ts[0].status == "high", via_ts
+
+    # Naive timestamps are tolerated and read as UTC.
+    naive = learn([{"camera_id": "cam04", "total": 7,
+                    "bucket_start": datetime(2026, 9, 1, 14, 0)}])
+    assert ("cam04", 14) in naive
+
+    print("baseline self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
diff --git a/netra/api/app.py b/netra/api/app.py
index c2f1862..d08d937 100644
--- a/netra/api/app.py
+++ b/netra/api/app.py
@@ -761,25 +761,100 @@ def traffic_snapshot(_p=Depends(require("pipeline"))):
 @app.get("/api/traffic/history")
 def traffic_history(camera_id: str | None = None, limit: int = Query(200, le=1000)):
     from netra.core.models import TrafficStat
     with SessionLocal() as db:
         q = db.query(TrafficStat)
         if camera_id:
             q = q.filter(TrafficStat.camera_id == camera_id)
         rows = q.order_by(TrafficStat.bucket_start.desc()).limit(limit).all()
         return [{
             "camera_id": r.camera_id, "at": r.bucket_start.isoformat(),
-            "total": r.total, "counts_by_class": r.counts_by_class,
+            # `total` is the traffic during this bucket; `cumulative_total` is
+            # the camera's running figure, which spans every replay of a
+            # looping recording and is only honest read beside `loops_seen`.
+            "total": r.total, "cumulative_total": r.cumulative_total,
+            "loops_seen": r.loops_seen, "counts_by_class": r.counts_by_class,
             "directions": r.directions, "mean_dwell_s": r.mean_dwell_s,
         } for r in rows]
 
 
+# ------------------------------------------------------------- baselines --
+#: History read per baseline request. Learning is bounded rather than
+#: unbounded: an operator refreshing a dashboard must never pull the whole
+#: traffic table and starve the detection threads of the database.
+BASELINE_HISTORY_LIMIT = 5000
+
+
+def _load_baselines(camera_id: str | None, limit: int):
+    from netra.analytics import baseline
+    from netra.core.models import TrafficStat
+    with SessionLocal() as db:
+        q = db.query(TrafficStat)
+        if camera_id:
+            q = q.filter(TrafficStat.camera_id == camera_id)
+        rows = q.order_by(TrafficStat.bucket_start.desc()).limit(limit).all()
+    return baseline.learn(rows), len(rows)
+
+
+@app.get("/api/analytics/baselines")
+def analytics_baselines(camera_id: str | None = None,
+                        limit: int = Query(BASELINE_HISTORY_LIMIT, le=20000),
+                        _p=Depends(require("read"))):
+    """What each camera normally sees, per hour of the day, in UTC.
+
+    Baselines below the sample floor are returned too, marked insufficient:
+    knowing the platform cannot yet judge an hour is itself operational
+    information, and hiding those rows would imply coverage that does not exist.
+    """
+    from netra.analytics import baseline
+    learned, sampled = _load_baselines(camera_id, limit)
+    items = sorted((b.as_dict() for b in learned.values()),
+                   key=lambda b: (b["camera_id"], b["hour"]))
+    ready = sum(1 for b in items if b["sufficient"])
+    return {"buckets_read": sampled, "min_samples": baseline.MIN_SAMPLES,
+            "stdev_floor": baseline.STDEV_FLOOR, "hours_learned": len(items),
+            "hours_judgeable": ready, "baselines": items}
+
+
+@app.get("/api/analytics/anomalies")
+def analytics_anomalies(camera_id: str | None = None,
+                        limit: int = Query(BASELINE_HISTORY_LIMIT, le=20000),
+                        include_normal: bool = False,
+                        _p=Depends(require("read"))):
+    """Current per-camera readings judged against the learned norms.
+
+    The current reading is the most recent completed traffic bucket for each
+    camera, so it is measured the same way the baseline was - comparing a live
+    partial count against full-bucket norms would manufacture false quiets.
+    """
+    from netra.analytics import baseline
+    from netra.core.models import TrafficStat
+    learned, sampled = _load_baselines(camera_id, limit)
+
+    with SessionLocal() as db:
+        q = db.query(TrafficStat)
+        if camera_id:
+            q = q.filter(TrafficStat.camera_id == camera_id)
+        recent = q.order_by(TrafficStat.bucket_start.desc()).limit(limit).all()
+
+    latest: dict[str, object] = {}
+    for r in recent:
+        latest.setdefault(r.camera_id, r)   # rows arrive newest first
+
+    found = baseline.detect_anomalies(learned, list(latest.values()),
+                                      include_normal=include_normal)
+    flagged = [a for a in found if a.anomalous]
+    return {"buckets_read": sampled, "cameras_assessed": len(latest),
+            "anomalies": len(flagged),
+            "assessments": [a.as_dict() for a in found]}
+
+
 # ---------------------------------------------------------------- storage --
 @app.get("/api/storage")
 def storage(_p=Depends(require("read"))):
     """What the evidence directory and detections table hold, against budget."""
     from netra.core import retention
     return retention.storage_report()
 
 
 @app.post("/api/storage/prune")
 def storage_prune(dry_run: bool = False, _p=Depends(require("manage"))):
diff --git a/netra/api/assistant.py b/netra/api/assistant.py
index 49d3530..e2ea67e 100644
--- a/netra/api/assistant.py
+++ b/netra/api/assistant.py
@@ -218,67 +218,140 @@ def _coverage(_q: str) -> dict:
     top = sorted(by_city.items(), key=lambda x: -x[1])
     return _answer(
         "Camera coverage by location: " +
         ", ".join(f"{city} {n}" for city, n in top[:8]) +
         f". Cross-camera tracing is valid within {len(TIME_GROUPS)} groups of "
         f"cameras that share a recording session.",
         {"by_city": by_city, "time_groups": TIME_GROUPS},
         [{"label": "Open map", "view": "map"}])
 
 
+def _unusual(_q: str) -> dict:
+    """Anything abnormal, judged against each camera's own learned norm.
+
+    A control room cannot read seventeen thousand detections. It can read
+    "camera 12 is four times its usual 03:00 traffic", which is what this
+    answers - and, just as importantly, says plainly where the platform has not
+    yet watched a camera long enough to have an opinion.
+    """
+    from netra.analytics import baseline
+    from netra.core.models import TrafficStat
+
+    with SessionLocal() as db:
+        rows = (db.query(TrafficStat)
+                .order_by(TrafficStat.bucket_start.desc()).limit(5000).all())
+
+    if not rows:
+        return _answer(
+            "No traffic history has been recorded yet, so there is no norm to "
+            "compare against. Run the pipeline for a while and ask again.",
+            {"buckets": 0}, [{"label": "Overview", "view": "overview"}])
+
+    learned = baseline.learn(rows)
+    latest: dict = {}
+    for r in rows:
+        latest.setdefault(r.camera_id, r)   # newest first
+    found = baseline.detect_anomalies(learned, list(latest.values()))
+    flagged = [a for a in found if a.anomalous]
+    thin = [a for a in found if a.status == "insufficient_data"]
+
+    data = {"buckets": len(rows), "cameras_assessed": len(latest),
+            "anomalies": len(flagged),
+            "assessments": [a.as_dict() for a in found]}
+    actions = [{"label": "Traffic", "view": "traffic"}]
+
+    if not flagged:
+        text = (f"Nothing unusual. All {len(latest)} cameras with a current "
+                f"reading are within their usual range for this hour.")
+        if thin:
+            text += (f" {len(thin)} camera(s) have fewer than "
+                     f"{baseline.MIN_SAMPLES} observations of this hour, so "
+                     f"they are not being judged at all yet.")
+        return _answer(text, data, actions)
+
+    lead = "; ".join(a.explanation for a in flagged[:3])
+    text = (f"{len(flagged)} of {len(latest)} cameras are outside their normal "
+            f"range for this hour of the day. {lead}")
+    if thin:
+        text += (f" A further {len(thin)} camera(s) have too little history "
+                 f"({baseline.MIN_SAMPLES} observations required) for any "
+                 f"judgement to be honest.")
+    return _answer(text, data, actions)
+
+
 def _help(_q: str) -> dict:
     return _answer(
         "I answer from live platform data. You can ask about camera health and "
         "which cameras are faulty, detection counts, current alerts, the "
         "watchlist, pipeline status, coverage by location, whether any plates look "
-        "cloned, or where a specific registration number has been seen.",
+        "cloned, whether anything looks unusual against each camera's normal "
+        "traffic, or where a specific registration number has been seen.",
         {}, [{"label": "Camera health", "query": "which cameras are down"},
              {"label": "Current alerts", "query": "show me the alerts"},
-             {"label": "Detections", "query": "how many detections"}])
+             {"label": "Detections", "query": "how many detections"},
+             {"label": "Anything unusual", "query": "anything unusual?"}])
 
 
 # Ordered: the first intent whose keywords appear wins, so specific
 # intents must precede general ones.
 INTENTS = [
+    # Ahead of everything else: an operator phrases this question with words
+    # that later intents already claim - "which camera is busier than normal"
+    # contains "camera", "where is it unusual" contains "where" - so placed
+    # lower it would be answered by camera health or the plate trace instead.
+    (("unusual", "abnormal", "anomaly", "anomalies", "out of the ordinary",
+      "baseline", "baselines", "spike", "quieter than", "busier than"), _unusual),
     # Ahead of the trace intent because "find cloned plates" contains "find";
     # a question naming an actual registration number never reaches here, as
     # `ask` routes those to the trace handler before the keyword loop runs.
     (("clone", "cloned", "cloning", "forged", "forgery", "duplicate plate",
       "fake plate"), _cloned_plates),
     (("where", "seen", "trace", "track", "find", "locate"), _find_plate),
     (("camera", "cameras", "down", "faulty", "degraded", "health", "broken"), _camera_health),
     (("alert", "alerts", "hit", "match", "matches"), _alert_summary),
     (("watchlist", "stolen", "wanted", "suspect", "blacklist"), _watchlist_summary),
     (("detection", "detections", "vehicles", "cars", "count", "how many"), _detection_summary),
     (("pipeline", "running", "status", "system"), _pipeline_status),
     (("coverage", "map", "location", "city", "where are"), _coverage),
     (("help", "what can you", "commands", "hello", "hi"), _help),
 ]
 
 # LLM_HINT: to support free-form phrasing, classify the question to one of the
 # intent names above with a model and dispatch here. The handlers must remain
 # the only source of facts - the model chooses the query, never the answer.
 
 
-def ask(question: str) -> dict:
-    """Route a question to a handler and return a grounded answer."""
-    if not question or not question.strip():
-        return _help("")
-    q = question.lower().strip()
+def route(question: str):
+    """Which handler a question resolves to, or None for "I cannot answer".
 
-    # A registration number anywhere in the question is unambiguous intent.
+    Split out from `ask` so routing can be checked without running a handler,
+    and therefore without a database: a wrong route is the failure mode that
+    produces a confidently wrong answer, and it is worth pinning down on its
+    own.
+    """
+    if not question or not question.strip():
+        return _help
     if PLATE_RE.search(question):
-        return _find_plate(question)
-
+        # A registration number anywhere in the question is unambiguous intent.
+        return _find_plate
+    q = question.lower().strip()
     for keywords, handler in INTENTS:
         if any(k in q for k in keywords):
-            return handler(question)
+            return handler
+    return None
+
+
+def ask(question: str) -> dict:
+    """Route a question to a handler and return a grounded answer."""
+    handler = route(question)
+    if handler is not None:
+        return handler(question)
 
     return _answer(
         "I could not match that to anything I can answer from platform data. "
         "Ask about camera health, detections, alerts, the watchlist, pipeline "
         "status, coverage, cloned plates, or a specific registration number.",
         {}, _help("")["actions"])
 
 
 def _self_check() -> None:
     """Routing decides which query runs; a wrong route gives a confident wrong
@@ -299,19 +372,37 @@ def _self_check() -> None:
 
     # The clone intent must not swallow a plate trace, and must win over the
     # trace keywords when a question is about clones generally.
     r = ask("any cloned plates?")
     assert "clone" in r["answer"].lower(), r
     r = ask("find cloned plates")
     assert "clone" in r["answer"].lower(), r
     r = ask("where has GJ01AB1234 been seen?")
     assert "GJ01AB1234" in r["answer"], r
 
+    # The unusual/baseline intent must win over the general handlers whose
+    # keywords a naturally phrased question also contains. Routing is asserted
+    # rather than answered, so this needs no database.
+    for q in ("anything unusual?", "is anything abnormal right now",
+              "show me the anomalies", "which camera is busier than normal",
+              "what does the baseline say"):
+        assert route(q) is _unusual, (q, route(q))
+
+    # ...and the reverse direction: the new intent must not steal questions
+    # belonging to the handlers that were already there.
+    assert route("which cameras are down?") is _camera_health
+    assert route("where has GJ01AB1234 been seen?") is _find_plate
+    assert route("find cloned plates") is _cloned_plates
+    assert route("show me the alerts") is _alert_summary
+    assert route("how many detections") is _detection_summary
+    assert route("is the pipeline running") is _pipeline_status
+    assert route("what is the weather in Ahmedabad tomorrow") is None
+
     # Unknown questions must decline rather than invent an answer.
     r = ask("what is the weather in Ahmedabad tomorrow")
     assert "could not match" in r["answer"], r
 
     print("assistant self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/core/db.py b/netra/core/db.py
index 8caf2bb..4da81c1 100644
--- a/netra/core/db.py
+++ b/netra/core/db.py
@@ -7,13 +7,41 @@ from netra.config import DB_URL
 
 class Base(DeclarativeBase):
     pass
 
 
 _connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
 engine = create_engine(DB_URL, connect_args=_connect_args, pool_pre_ping=True)
 SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
 
 
+#: Columns added to tables that already exist in the field. `create_all` only
+#: creates missing *tables*, so a column added to a model would be absent from
+#: an operator's existing data/netra.db and every ORM read of that table would
+#: fail. These are applied additively at start-up rather than asking anyone to
+#: delete their evidence database.
+#: ponytail: a hand-kept list, not a migration tool. Its ceiling is additive,
+#: nullable/defaulted columns on SQLite; a type change or a drop needs Alembic.
+_ADDED_COLUMNS = [
+    ("traffic_stats", "cumulative_total", "INTEGER DEFAULT 0"),
+    ("traffic_stats", "loops_seen", "INTEGER DEFAULT 0"),
+]
+
+
+def _apply_added_columns() -> None:
+    from sqlalchemy import inspect, text
+    inspector = inspect(engine)
+    existing = set(inspector.get_table_names())
+    with engine.begin() as conn:
+        for table, column, ddl in _ADDED_COLUMNS:
+            if table not in existing:
+                continue  # create_all just made it, with the column present
+            have = {c["name"] for c in inspector.get_columns(table)}
+            if column in have:
+                continue
+            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
+
+
 def init_db() -> None:
     from netra.core import models  # noqa: F401  (registers mappers)
     Base.metadata.create_all(engine)
+    _apply_added_columns()
diff --git a/netra/core/models.py b/netra/core/models.py
index 60a05a4..9c8c75b 100644
--- a/netra/core/models.py
+++ b/netra/core/models.py
@@ -196,14 +196,23 @@ class TrafficStat(Base):
 
     Detections answer "what was seen"; these answer "how much traffic passed",
     which is the question a planner or a control room actually asks.
     """
     __tablename__ = "traffic_stats"
 
     id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
     camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
     bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
     bucket_seconds: Mapped[int] = mapped_column(Integer, default=60)
+    #: traffic counted *during this bucket*. Baselines learn from this, so it
+    #: must not be cumulative: a monotonically rising figure would make the
+    #: learned "norm" a function of uptime rather than of how busy the road is.
     total: Mapped[int] = mapped_column(Integer, default=0)
+    #: the camera's running total since the tracker was created, kept because a
+    #: sandbox recording loops and an operator still wants the headline figure.
+    cumulative_total: Mapped[int] = mapped_column(Integer, default=0)
+    #: how many times the recording had replayed when this bucket was written,
+    #: so the cumulative figure above can be read honestly.
+    loops_seen: Mapped[int] = mapped_column(Integer, default=0)
     counts_by_class: Mapped[dict] = mapped_column(JSON, default=dict)
     directions: Mapped[dict] = mapped_column(JSON, default=dict)
     mean_dwell_s: Mapped[float] = mapped_column(Float, default=0.0)
diff --git a/netra/pipeline.py b/netra/pipeline.py
index b25af66..5ab89da 100644
--- a/netra/pipeline.py
+++ b/netra/pipeline.py
@@ -48,20 +48,25 @@ class Pipeline:
         self.running = False
         self.started_at: datetime | None = None
 
         # Persistence runs off the inference thread.
         self._write_queue: queue.Queue = queue.Queue(maxsize=4000)
         self._stop_writer = threading.Event()
         self._writer: threading.Thread | None = None
         self.stats = {"written": 0, "write_dropped": 0, "zone_events": 0,
                       "traffic_buckets": 0}
 
+        # Counters per camera at the last traffic flush, so each bucket can
+        # record the traffic during it rather than the running total.
+        self._traffic_last_total: dict[str, int] = {}
+        self._traffic_last_counts: dict[str, dict[str, int]] = {}
+
         # Zone rules are evaluated inside the inference engine, where the
         # tracks live; the pipeline supplies the engine and receives events.
         from netra.analytics.zones import ZoneEngine
         self.zone_engine = ZoneEngine()
         self.engine.zone_engine = self.zone_engine
         self.engine.on_zone_event = self._handle_zone_event
         self._last_traffic_flush = 0.0
 
     # -- lifecycle -----------------------------------------------------------
     def start(self, camera_ids: list[str] | None = None,
@@ -173,32 +178,63 @@ class Pipeline:
         self.stats["zone_events"] += 1
         log.warning("ZONE %s on %s: %s", event.rule, event.camera_id, event.detail)
         self._broadcast(payload)
         NOTIFIER.submit({**payload, "plate_watchlist": event.zone.name,
                          "plate_observed": event.detail,
                          "match_type": event.rule, "score": 1.0,
                          "reasons": {"zone": {"score": 1.0,
                                               "detail": event.detail}}})
 
     def flush_traffic_stats(self, bucket_seconds: int = 60) -> int:
-        """Snapshot per-camera traffic counters into a time bucket."""
+        """Snapshot per-camera traffic counters into a time bucket.
+
+        `total` is the traffic counted *during this bucket*, obtained by
+        differencing the tracker's cumulative counter against the value at the
+        previous flush. Writing the cumulative figure here - as this once did -
+        made every row larger than the last, because the sandbox replays a
+        fixed recording and the counter spans every replay. Baselines learned
+        from that would describe uptime, not traffic.
+
+        A bucket with no traffic is still written: "this camera saw nothing"
+        is exactly the observation a quiet-road baseline needs, and dropping it
+        would teach the baseline that the road is never empty.
+        """
         now = datetime.now(timezone.utc)
         written = 0
         with SessionLocal() as db:
             for stats in self.engine.trackers.stats():
-                if not stats["total_counted"]:
-                    continue
+                camera_id = stats["camera_id"]
+                cumulative = stats["total_counted"]
+                previous = self._traffic_last_total.get(camera_id)
+                # A tracker recreated mid-run restarts its counter; treat the
+                # whole of a smaller cumulative as this bucket's traffic rather
+                # than persisting a negative count.
+                delta = cumulative if previous is None or cumulative < previous \
+                    else cumulative - previous
+                self._traffic_last_total[camera_id] = cumulative
+
+                # The class breakdown is cumulative for the same reason, and
+                # is differenced the same way: a bucket whose classes summed to
+                # more than its total would be visibly incoherent to an analyst.
+                counts = stats["counts_by_class"]
+                before = self._traffic_last_counts.get(camera_id, {})
+                by_class = {k: v - before.get(k, 0) for k, v in counts.items()
+                            if v - before.get(k, 0) > 0}
+                self._traffic_last_counts[camera_id] = dict(counts)
+
                 db.add(TrafficStat(
-                    camera_id=stats["camera_id"], bucket_start=now,
+                    camera_id=camera_id, bucket_start=now,
                     bucket_seconds=bucket_seconds,
-                    total=stats["total_counted"],
-                    counts_by_class=stats["counts_by_class"],
+                    total=delta,
+                    cumulative_total=cumulative,
+                    loops_seen=stats["loops_seen"],
+                    counts_by_class=by_class,
                     directions=stats["directions"],
                     mean_dwell_s=stats["mean_dwell_s"]))
                 written += 1
             db.commit()
         self.stats["traffic_buckets"] += written
         return written
 
     def _handle_detection(self, det) -> None:
         """Hand a detection to the writer. Must not touch disk or the database.
 
