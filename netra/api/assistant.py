"""Control-room assistant.

Answers operational questions against live platform state - camera health,
detections, alerts, watchlist, vehicle traces - so an operator can ask in
plain language instead of navigating to the right screen and filtering it.

Every answer is produced from a database query and carries the records it was
derived from. Nothing is generated or inferred: in a policing context an
assistant that invents a plausible-sounding number is worse than no assistant,
so unrecognised questions say so and list what can be asked instead.

ponytail: intent matching on keywords rather than a language model. It needs no
API key, no network call and no per-query cost, and the question space in a
control room is small and repetitive. `LLM_HINT` below marks where a model
would slot in if free-form phrasing is ever needed.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from netra.core.db import SessionLocal
from netra.core.geo import TIME_GROUPS
from netra.core.models import Alert, Camera, Detection, WatchlistEntry

PLATE_RE = re.compile(r"\b([A-Z]{2}\s?\d{1,2}\s?[A-Z]{0,3}\s?\d{3,4})\b", re.I)


def _answer(text: str, data=None, actions=None) -> dict:
    return {"answer": text, "data": data or {}, "actions": actions or []}


# -- intents ----------------------------------------------------------------

def _camera_health(_q: str) -> dict:
    with SessionLocal() as db:
        cams = db.query(Camera).all()
    by_cap: dict[str, list[str]] = {}
    for c in cams:
        by_cap.setdefault(c.capability, []).append(c.id)
    degraded = [c for c in cams if c.capability == "degraded"]

    lines = [f"{len(cams)} cameras are registered."]
    for cap in ("anpr", "vehicle", "person", "degraded"):
        if by_cap.get(cap):
            lines.append(f"{len(by_cap[cap])} classified {cap}.")
    if degraded:
        lines.append("Cameras that cannot deliver analytics: " +
                     ", ".join(f"{c.id} ({c.capability_note or 'unspecified'})"
                               for c in degraded[:8]) + ".")
    return _answer(" ".join(lines),
                   {"by_capability": {k: len(v) for k, v in by_cap.items()},
                    "degraded": [{"id": c.id, "name": c.name,
                                  "reason": c.capability_note} for c in degraded]},
                   [{"label": "Open registry", "view": "registry"}])


def _detection_summary(_q: str) -> dict:
    with SessionLocal() as db:
        total = db.query(func.count(Detection.id)).scalar() or 0
        plates = db.query(func.count(Detection.id)).filter(
            Detection.plate_text.isnot(None)).scalar() or 0
        by_class = dict(db.query(Detection.vehicle_class, func.count(Detection.id))
                        .group_by(Detection.vehicle_class).all())
        recent = db.query(func.count(Detection.id)).filter(
            Detection.wall_time >= datetime.now(timezone.utc) - timedelta(minutes=5)
        ).scalar() or 0

    breakdown = ", ".join(f"{v} {k}" for k, v in
                          sorted(by_class.items(), key=lambda x: -x[1]))
    text = (f"{total} detections recorded ({recent} in the last five minutes): "
            f"{breakdown or 'none yet'}. {plates} carry a readable plate.")
    if total and plates / total < 0.05:
        text += (" Plate yield is low because most grid cameras are wide-area "
                 "night overviews where plates are not resolvable; vehicle-level "
                 "analytics apply there instead.")
    return _answer(text, {"total": total, "with_plate": plates,
                          "by_class": by_class, "last_5_min": recent},
                   [{"label": "Open detections", "view": "detections"}])


def _alert_summary(_q: str) -> dict:
    with SessionLocal() as db:
        rows = db.query(Alert).order_by(Alert.created_at.desc()).limit(10).all()
        total = db.query(func.count(Alert.id)).scalar() or 0
        unack = db.query(func.count(Alert.id)).filter(
            Alert.acknowledged.is_(False)).scalar() or 0
        items = []
        for a in rows:
            wl = db.get(WatchlistEntry, a.watchlist_id)
            items.append({"id": a.id, "plate": wl.plate if wl else None,
                          "category": wl.category if wl else None,
                          "camera_id": a.camera_id, "score": a.score,
                          "severity": a.severity, "at": a.created_at.isoformat()})

    if not total:
        return _answer("No watchlist alerts have been raised yet.", {"total": 0},
                       [{"label": "Open watchlist", "view": "watchlist"}])
    newest = items[0]
    return _answer(
        f"{total} alerts raised, {unack} not yet acknowledged. Most recent: "
        f"{newest['plate']} on {newest['camera_id']} "
        f"({newest['severity']}, confidence {newest['score']}).",
        {"total": total, "unacknowledged": unack, "recent": items},
        [{"label": "Open alerts", "view": "alerts"}])


def _find_plate(q: str) -> dict:
    m = PLATE_RE.search(q)
    if not m:
        return _answer("Give me a registration number and I will trace it, "
                       "for example: where has GJ01AB1234 been seen?")
    plate = m.group(1).upper().replace(" ", "")

    from netra.analytics.route import build_route
    with SessionLocal() as db:
        rows = (db.query(Detection).options(joinedload(Detection.camera))
                .filter(Detection.plate_text.isnot(None)).all())
        route = build_route(rows, plate)
        listed = db.query(WatchlistEntry).filter(
            WatchlistEntry.plate == plate).first()

    if not route.hops:
        text = f"No sightings of {plate}."
        if listed:
            text += (f" It is on the watchlist as {listed.category} "
                     f"({listed.severity}), so an alert will be raised if it "
                     f"appears.")
        return _answer(text, {"plate": plate, "hops": 0})

    first, last = route.hops[0], route.hops[-1]
    text = (f"{plate} was seen {len(route.hops)} times, first at "
            f"{first.camera_name} ({first.at:%H:%M:%S}) and last at "
            f"{last.camera_name} ({last.at:%H:%M:%S}), covering "
            f"{route.total_km} km.")
    if route.rejected:
        text += (f" {len(route.rejected)} further sightings were excluded as "
                 f"not physically consistent or from a different recording "
                 f"session.")
    return _answer(text, route.to_dict(),
                   [{"label": f"Trace {plate}", "view": "route", "query": plate}])


def _cloned_plates(_q: str) -> dict:
    from netra.analytics.cloned_plate import find_clones
    with SessionLocal() as db:
        rows = (db.query(Detection).options(joinedload(Detection.camera))
                .filter(Detection.plate_text.isnot(None)).all())
        findings = find_clones(rows)

    if not findings:
        return _answer(
            "No cloned plates detected. A plate is flagged only when the same "
            "registration is read at two cameras in the same recording session, "
            "too far apart for one vehicle to have covered in the time between - "
            "sightings from different sessions are never compared.",
            {"count": 0},
            [{"label": "Open detections", "view": "detections"}])

    top = findings[0]
    return _answer(
        f"{len(findings)} possible cloned plates. Strongest: {top.plate} at "
        f"{top.sighting_a['camera_name']} and {top.sighting_b['camera_name']}, "
        f"{top.distance_km} km apart in {top.elapsed_s:.0f}s "
        f"(confidence {top.confidence}). This is inferred from OCR reads and is "
        f"never certain - check the evidence images before acting.",
        {"count": len(findings),
         "findings": [f.to_dict() for f in findings[:10]]},
        [{"label": f"Trace {top.plate}", "view": "route", "query": top.plate}])


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
                       [{"label": "Open watchlist", "view": "watchlist"}])
    return _answer(
        f"{len(rows)} active watchlist entries: " +
        ", ".join(f"{v} {k}" for k, v in sorted(by_cat.items())) + ".",
        {"total": len(rows), "by_category": by_cat,
         "plates": [e.plate for e in rows[:20]]},
        [{"label": "Open watchlist", "view": "watchlist"}])


def _pipeline_status(_q: str) -> dict:
    from netra.pipeline import PIPELINE
    st = PIPELINE.status()
    cams = st.get("cameras", [])
    up = sum(1 for c in cams if c.get("connected"))
    inf = st.get("inference", {})
    if not st.get("running"):
        return _answer("The pipeline is not running. Start it to begin "
                       "processing camera feeds.", st,
                       [{"label": "Overview", "view": "overview"}])
    return _answer(
        f"Pipeline running: {up} of {len(cams)} cameras connected, "
        f"{inf.get('vehicles', 0)} vehicles detected, "
        f"{inf.get('plates', 0)} plates read, "
        f"{inf.get('dropped', 0)} frames dropped.", st,
        [{"label": "Overview", "view": "overview"}])


def _coverage(_q: str) -> dict:
    with SessionLocal() as db:
        cams = db.query(Camera).all()
    by_city: dict[str, int] = {}
    for c in cams:
        by_city[c.city or "unknown"] = by_city.get(c.city or "unknown", 0) + 1
    top = sorted(by_city.items(), key=lambda x: -x[1])
    return _answer(
        "Camera coverage by location: " +
        ", ".join(f"{city} {n}" for city, n in top[:8]) +
        f". Cross-camera tracing is valid within {len(TIME_GROUPS)} groups of "
        f"cameras that share a recording session.",
        {"by_city": by_city, "time_groups": TIME_GROUPS},
        [{"label": "Open map", "view": "map"}])


def _unusual(_q: str) -> dict:
    """Anything abnormal, judged against each camera's own learned norm.

    A control room cannot read seventeen thousand detections. It can read
    "camera 12 is four times its usual 03:00 traffic", which is what this
    answers - and, just as importantly, says plainly where the platform has not
    yet watched a camera long enough to have an opinion.
    """
    from netra.analytics import baseline
    from netra.core.models import TrafficStat

    with SessionLocal() as db:
        rows = (db.query(TrafficStat)
                .order_by(TrafficStat.bucket_start.desc()).limit(5000).all())

    if not rows:
        return _answer(
            "No traffic history has been recorded yet, so there is no norm to "
            "compare against. Run the pipeline for a while and ask again.",
            {"buckets": 0}, [{"label": "Overview", "view": "overview"}])

    learned = baseline.learn(rows)
    latest: dict = {}
    for r in rows:
        latest.setdefault(r.camera_id, r)   # newest first
    found = baseline.detect_anomalies(learned, list(latest.values()))
    flagged = [a for a in found if a.anomalous]
    thin = [a for a in found if a.status == "insufficient_data"]

    data = {"buckets": len(rows), "cameras_assessed": len(latest),
            "anomalies": len(flagged),
            "assessments": [a.as_dict() for a in found]}
    actions = [{"label": "Traffic", "view": "traffic"}]

    if not flagged:
        text = (f"Nothing unusual. All {len(latest)} cameras with a current "
                f"reading are within their usual range for this hour.")
        if thin:
            text += (f" {len(thin)} camera(s) have fewer than "
                     f"{baseline.MIN_SAMPLES} observations of this hour, so "
                     f"they are not being judged at all yet.")
        return _answer(text, data, actions)

    lead = "; ".join(a.explanation for a in flagged[:3])
    text = (f"{len(flagged)} of {len(latest)} cameras are outside their normal "
            f"range for this hour of the day. {lead}")
    if thin:
        text += (f" A further {len(thin)} camera(s) have too little history "
                 f"({baseline.MIN_SAMPLES} observations required) for any "
                 f"judgement to be honest.")
    return _answer(text, data, actions)


def _help(_q: str) -> dict:
    return _answer(
        "I answer from live platform data. You can ask about camera health and "
        "which cameras are faulty, detection counts, current alerts, the "
        "watchlist, pipeline status, coverage by location, whether any plates look "
        "cloned, whether anything looks unusual against each camera's normal "
        "traffic, or where a specific registration number has been seen.",
        {}, [{"label": "Camera health", "query": "which cameras are down"},
             {"label": "Current alerts", "query": "show me the alerts"},
             {"label": "Detections", "query": "how many detections"},
             {"label": "Anything unusual", "query": "anything unusual?"}])


# Ordered: the first intent whose keywords appear wins, so specific
# intents must precede general ones.
INTENTS = [
    # Ahead of everything else: an operator phrases this question with words
    # that later intents already claim - "which camera is busier than normal"
    # contains "camera", "where is it unusual" contains "where" - so placed
    # lower it would be answered by camera health or the plate trace instead.
    (("unusual", "abnormal", "anomaly", "anomalies", "out of the ordinary",
      "baseline", "baselines", "spike", "quieter than", "busier than"), _unusual),
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


def route(question: str):
    """Which handler a question resolves to, or None for "I cannot answer".

    Split out from `ask` so routing can be checked without running a handler,
    and therefore without a database: a wrong route is the failure mode that
    produces a confidently wrong answer, and it is worth pinning down on its
    own.
    """
    if not question or not question.strip():
        return _help
    if PLATE_RE.search(question):
        # A registration number anywhere in the question is unambiguous intent.
        return _find_plate
    q = question.lower().strip()
    for keywords, handler in INTENTS:
        if any(k in q for k in keywords):
            return handler
    return None


def ask(question: str) -> dict:
    """Route a question to a handler and return a grounded answer."""
    handler = route(question)
    if handler is not None:
        return handler(question)

    return _answer(
        "I could not match that to anything I can answer from platform data. "
        "Ask about camera health, detections, alerts, the watchlist, pipeline "
        "status, coverage, cloned plates, or a specific registration number.",
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

    # The clone intent must not swallow a plate trace, and must win over the
    # trace keywords when a question is about clones generally.
    r = ask("any cloned plates?")
    assert "clone" in r["answer"].lower(), r
    r = ask("find cloned plates")
    assert "clone" in r["answer"].lower(), r
    r = ask("where has GJ01AB1234 been seen?")
    assert "GJ01AB1234" in r["answer"], r

    # The unusual/baseline intent must win over the general handlers whose
    # keywords a naturally phrased question also contains. Routing is asserted
    # rather than answered, so this needs no database.
    for q in ("anything unusual?", "is anything abnormal right now",
              "show me the anomalies", "which camera is busier than normal",
              "what does the baseline say"):
        assert route(q) is _unusual, (q, route(q))

    # ...and the reverse direction: the new intent must not steal questions
    # belonging to the handlers that were already there.
    assert route("which cameras are down?") is _camera_health
    assert route("where has GJ01AB1234 been seen?") is _find_plate
    assert route("find cloned plates") is _cloned_plates
    assert route("show me the alerts") is _alert_summary
    assert route("how many detections") is _detection_summary
    assert route("is the pipeline running") is _pipeline_status
    assert route("what is the weather in Ahmedabad tomorrow") is None

    # Unknown questions must decline rather than invent an answer.
    r = ask("what is the weather in Ahmedabad tomorrow")
    assert "could not match" in r["answer"], r

    print("assistant self-check passed")


if __name__ == "__main__":
    _self_check()
