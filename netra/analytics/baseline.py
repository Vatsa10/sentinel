"""Per-camera behavioural baselines: what is normal here, at this hour.

Tens of thousands of detections are data, not information. A control room does
not need to be told that camera 12 saw 41 vehicles; it needs to be told that 41
is four times what that camera normally sees at 03:00, because that is what a
blocked road, a diverted convoy or a forming crowd looks like from the outside.

The model is deliberately the simplest one that can be defended in an enquiry:
for each (camera, hour of day) the mean and standard deviation of the per-bucket
vehicle count, and a z-score of the current reading against it. Two honesty
constraints shape it:

  * Below `MIN_SAMPLES` observations no judgement is offered at all. A "norm"
    built from three buckets is noise, and calling a reading anomalous against
    noise is fabrication dressed as analysis.
  * Dispersion is floored, so a camera that has happened to see the same count
    every time cannot generate an infinite z-score from a single extra vehicle.

ponytail: a per-hour Gaussian ignores the difference between a Tuesday and a
Sunday, and treats an hour boundary as a hard edge. Its ceiling is a camera
whose traffic is strongly weekly rather than daily - a market street, a stadium
approach - where a busy Saturday would read as an anomaly against a weekday
norm. Adding day-of-week to the key is the next step, and needs roughly seven
times the observation history before it earns its place.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

#: Observations required before any deviation judgement is offered. A hard
#: floor, not a preference: below it the answer is "I do not know yet".
MIN_SAMPLES = 5

#: Standard deviation is floored here before any z-score is taken. A camera
#: whose counts are identical every bucket has zero variance, and one extra
#: vehicle against zero variance is an infinite deviation - arithmetically
#: true, operationally absurd. One vehicle is the smallest difference the
#: counter can even express, so it is the smallest dispersion worth believing.
STDEV_FLOOR = 1.0

#: z-score bands. Deliberately wide: an alert an operator learns to ignore is
#: worse than no alert, and traffic counts are not normally distributed.
Z_HIGH = 3.0
Z_ELEVATED = 2.0
Z_LOW = -2.0


@dataclass
class Baseline:
    """What one camera normally sees in one hour of the day."""
    camera_id: str
    hour: int
    mean: float
    stdev: float
    samples: int

    @property
    def sufficient(self) -> bool:
        return self.samples >= MIN_SAMPLES

    @property
    def effective_stdev(self) -> float:
        return max(self.stdev, STDEV_FLOOR)

    def as_dict(self) -> dict:
        return {"camera_id": self.camera_id, "hour": self.hour,
                "mean": round(self.mean, 2), "stdev": round(self.stdev, 2),
                "effective_stdev": round(self.effective_stdev, 2),
                "samples": self.samples, "sufficient": self.sufficient}


@dataclass
class Assessment:
    """A reading judged against a baseline, with the reasoning attached."""
    camera_id: str
    hour: int
    observed: int
    status: str            # insufficient_data|quiet|low|normal|elevated|high
    z_score: float | None
    explanation: str
    baseline: Baseline | None = None
    detail: dict = field(default_factory=dict)

    @property
    def anomalous(self) -> bool:
        return self.status in ("quiet", "low", "elevated", "high")

    def as_dict(self) -> dict:
        return {"camera_id": self.camera_id, "hour": self.hour,
                "observed": self.observed, "status": self.status,
                "z_score": self.z_score, "anomalous": self.anomalous,
                "explanation": self.explanation,
                "baseline": self.baseline.as_dict() if self.baseline else None,
                **self.detail}


def _field(row, name: str, default=None):
    """Read a field from either an ORM row or a plain dict.

    The learner is fed `TrafficStat` rows in the running platform and synthetic
    dicts in the self-check, and keeping it indifferent to which means the
    self-check needs no database.
    """
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _hour_of(row) -> int | None:
    """Hour of day from `bucket_start`, in UTC throughout.

    Every stored timestamp on this platform is UTC, so the baseline is learned
    and assessed in UTC. Mixing in a local hour would silently shift a norm by
    the offset and make the 03:00 night baseline the 08:30 rush-hour one.
    """
    ts = _field(row, "bucket_start")
    if ts is None:
        return None
    try:
        from datetime import timezone
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc)
        return ts.hour
    except AttributeError:
        return None


def _is_legacy_cumulative(row, total) -> bool:
    """True for a row written before `total` became a per-bucket figure.

    Those rows carry a running count spanning every replay of the recording,
    and a single one poisons the hour it lands in: measured on cam15 at hour
    18, one legacy row moved the mean from 3.4 to 14.0 and the standard
    deviation from 1.1 to 26.0, so a genuine ten-fold spike read as normal.
    They cannot be aged out - nothing prunes traffic_stats and the learner has
    no time window - so they are excluded here instead.

    They are identifiable because the migration that added `cumulative_total`
    defaults it to 0, while any row written since carries the real cumulative,
    which is at least as large as the bucket's own delta. A genuine empty
    bucket has `total = 0` and is deliberately kept: a road that is normally
    quiet is exactly what the baseline needs to learn. A source with no
    `cumulative_total` field at all (a synthetic dict) is trusted as given.
    """
    cumulative = _field(row, "cumulative_total")
    return cumulative is not None and cumulative == 0 and total > 0


def learn(rows) -> dict[tuple[str, int], Baseline]:
    """Learn per-(camera, hour) norms from `TrafficStat` rows.

    `total` must be the traffic *during* that bucket. A cumulative counter would
    make the learned mean a function of how long the platform has been running
    rather than of how busy the road is, and every judgement drawn from it
    meaningless.
    """
    grouped: dict[tuple[str, int], list[float]] = {}
    for row in rows:
        camera_id = _field(row, "camera_id")
        hour = _hour_of(row)
        total = _field(row, "total")
        if camera_id is None or hour is None or total is None:
            continue
        if _is_legacy_cumulative(row, total):
            continue
        grouped.setdefault((camera_id, int(hour)), []).append(float(total))

    baselines: dict[tuple[str, int], Baseline] = {}
    for (camera_id, hour), values in grouped.items():
        mean = statistics.fmean(values)
        # Sample standard deviation needs two points; one observation has no
        # dispersion to speak of, and is below MIN_SAMPLES anyway.
        stdev = statistics.stdev(values) if len(values) > 1 else 0.0
        baselines[(camera_id, hour)] = Baseline(
            camera_id=camera_id, hour=hour, mean=mean, stdev=stdev,
            samples=len(values))
    return baselines


def assess(baseline: Baseline | None, observed: int) -> Assessment:
    """Judge one reading against a baseline, or decline to."""
    observed = int(observed)

    if baseline is None:
        return Assessment(
            camera_id="", hour=-1, observed=observed,
            status="insufficient_data", z_score=None,
            explanation=("No baseline has been learned for this camera and "
                         "hour yet, so this reading cannot be judged."))

    if not baseline.sufficient:
        return Assessment(
            camera_id=baseline.camera_id, hour=baseline.hour, observed=observed,
            status="insufficient_data", z_score=None, baseline=baseline,
            explanation=(
                f"Only {baseline.samples} observation"
                f"{'' if baseline.samples == 1 else 's'} of {baseline.camera_id} "
                f"at hour {baseline.hour:02d}:00 UTC; "
                f"{MIN_SAMPLES} are required before this platform will call a "
                f"reading normal or abnormal. Observed {observed}."))

    z = (observed - baseline.mean) / baseline.effective_stdev
    z = round(z, 2)
    norm = (f"the norm for {baseline.camera_id} at hour {baseline.hour:02d}:00 "
            f"UTC is {baseline.mean:.1f} "
            f"(sd {baseline.effective_stdev:.1f}, {baseline.samples} samples)")

    # `quiet` is a more strongly worded `low`, never an override of the bands.
    # Gating it on the z-score as well as on zero matters because a genuinely
    # quiet road counts zero routinely: a camera whose history is
    # (0, 2, 1, 0, 3, 1, 0, 2) has a mean of 1.1, and reporting its next empty
    # minute as a possible blockage would assert an abnormality on evidence
    # that says the reading is ordinary - and would do so every few minutes.
    if observed == 0 and z <= Z_LOW:
        status = "quiet"
        text = (f"No traffic counted, {z:+.1f} standard deviations below "
                f"normal, where {norm}. A road that normally carries vehicles "
                f"and now carries none may be blocked, closed, or the camera's "
                f"view obstructed.")
    elif z >= Z_HIGH:
        status = "high"
        text = (f"{observed} vehicles, {z:+.1f} standard deviations above "
                f"normal: {norm}.")
    elif z >= Z_ELEVATED:
        status = "elevated"
        text = (f"{observed} vehicles, {z:+.1f} standard deviations above "
                f"normal: {norm}.")
    elif z <= Z_LOW:
        status = "low"
        text = (f"{observed} vehicles, {z:+.1f} standard deviations below "
                f"normal: {norm}.")
    else:
        status = "normal"
        text = (f"{observed} vehicles is within the usual range: {norm}.")

    return Assessment(camera_id=baseline.camera_id, hour=baseline.hour,
                      observed=observed, status=status, z_score=z,
                      explanation=text, baseline=baseline)


def detect_anomalies(baselines: dict[tuple[str, int], Baseline],
                     current_stats, include_normal: bool = False
                     ) -> list[Assessment]:
    """Assess a set of current readings, most deviant first.

    `current_stats` entries carry `camera_id`, `total`, and either an `hour` or
    a `bucket_start` from which the UTC hour is taken.
    """
    out: list[Assessment] = []
    for row in current_stats:
        camera_id = _field(row, "camera_id")
        if camera_id is None:
            continue
        hour = _field(row, "hour")
        if hour is None:
            hour = _hour_of(row)
        if hour is None:
            continue
        hour = int(hour)
        observed = int(_field(row, "total") or 0)

        result = assess(baselines.get((camera_id, hour)), observed)
        # `assess` cannot know the camera when there is no baseline at all.
        result.camera_id = camera_id
        result.hour = hour
        if include_normal or result.status != "normal":
            out.append(result)

    # Insufficient-data entries sort last: they are information about the
    # platform's own coverage, not about the road.
    out.sort(key=lambda a: (a.z_score is None, -abs(a.z_score or 0.0)))
    return out


def _self_check() -> None:
    """A baseline that flags the wrong thing costs an operator's trust, and one
    that flags nothing is decoration, so both directions are pinned here. All
    rows are synthetic: no database, no network."""
    from datetime import datetime, timezone

    def row(cam, hour, total):
        return {"camera_id": cam, "total": total,
                "bucket_start": datetime(2026, 9, 1, hour, 0,
                                         tzinfo=timezone.utc)}

    # A busy camera with a settled norm, plus a thin one with three samples.
    rows = ([row("cam01", 9, n) for n in (40, 44, 38, 42, 46, 41)] +
            [row("cam02", 9, n) for n in (10, 12, 11)] +
            [row("cam01", 3, n) for n in (2, 3, 1, 2, 4, 2)])
    b = learn(rows)

    assert b[("cam01", 9)].samples == 6
    assert 40 < b[("cam01", 9)].mean < 43, b[("cam01", 9)].mean
    assert b[("cam02", 9)].samples == 3

    # Hours are keyed separately: the night norm must not absorb the day norm.
    assert b[("cam01", 3)].mean < 5, b[("cam01", 3)].mean

    # Below MIN_SAMPLES no verdict is offered, however extreme the reading.
    a = assess(b[("cam02", 9)], 500)
    assert a.status == "insufficient_data", a
    assert a.z_score is None and not a.anomalous, a
    assert "5 are required" in a.explanation, a.explanation

    # No baseline at all behaves the same way.
    assert assess(None, 99).status == "insufficient_data"

    # A normal reading is not flagged.
    assert assess(b[("cam01", 9)], 42).status == "normal"

    # A clear spike is flagged.
    spike = assess(b[("cam01", 9)], 200)
    assert spike.status == "high", spike
    assert spike.z_score > Z_HIGH and spike.anomalous

    moderate = assess(b[("cam01", 9)], 49)
    assert moderate.status in ("elevated", "high"), moderate

    # Zero traffic against a busy baseline is quiet, not merely low.
    dead = assess(b[("cam01", 9)], 0)
    assert dead.status == "quiet", dead
    assert "blocked" in dead.explanation

    # A genuinely low but non-zero reading is low.
    assert assess(b[("cam01", 9)], 30).status == "low"

    # ...but a road that is *normally* quiet must not have its ordinary empty
    # minute reported as a blockage. Three of these eight samples are
    # themselves zero, so an observed zero sits inside the normal band and the
    # `quiet` wording must not override that. An alert here would fire on most
    # quiet cameras every few minutes and cost an officer's attention each time.
    quiet_road = learn([row("cam05", 2, n)
                        for n in (0, 2, 1, 0, 3, 1, 0, 2)])[("cam05", 2)]
    calm = assess(quiet_road, 0)
    assert calm.status == "normal", calm
    assert not calm.anomalous, calm
    assert abs(calm.z_score) <= abs(Z_LOW), calm
    # The same camera stopping dead is still not abnormal; a genuine surge is.
    assert assess(quiet_road, 10).status in ("elevated", "high")

    # Rows written before `total` became a per-bucket figure carry a cumulative
    # count and must never be learned from: one alone inflates the mean and
    # standard deviation enough to hide a ten-fold spike.
    legacy = {"camera_id": "cam06", "total": 67, "cumulative_total": 0,
              "bucket_start": datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)}
    genuine_empty = {"camera_id": "cam06", "total": 0, "cumulative_total": 0,
                     "bucket_start": datetime(2026, 9, 1, 18, 0,
                                              tzinfo=timezone.utc)}
    modern = [{"camera_id": "cam06", "total": t, "cumulative_total": 100 + t,
               "bucket_start": datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)}
              for t in (3, 4, 2, 5, 3)]

    # The legacy row is excluded...
    with_legacy = learn(modern + [legacy])[("cam06", 18)]
    assert with_legacy.samples == 5, with_legacy
    assert with_legacy.mean < 4, with_legacy
    # ...and the spike it would otherwise have hidden is still seen.
    assert assess(with_legacy, 30).status == "high"

    # ...while a genuine empty bucket, which looks similar, is kept: a quiet
    # road is exactly what the baseline needs to learn.
    with_empty = learn(modern + [genuine_empty])[("cam06", 18)]
    assert with_empty.samples == 6, with_empty
    assert with_empty.mean < with_legacy.mean, with_empty

    # A source that carries no cumulative_total at all is trusted as given.
    assert learn([{"camera_id": "cam07", "total": 9,
                   "bucket_start": datetime(2026, 9, 1, 18, 0,
                                            tzinfo=timezone.utc)}])

    # Zero variance must not produce an infinite or absurd z-score.
    flat = learn([row("cam03", 5, 20) for _ in range(8)])[("cam03", 5)]
    assert flat.stdev == 0.0 and flat.effective_stdev == STDEV_FLOOR
    one_more = assess(flat, 21)
    assert one_more.z_score == 1.0, one_more
    assert one_more.status == "normal", one_more
    far = assess(flat, 25)
    assert far.z_score == 5.0 and far.status == "high", far

    # A quiet night camera is not swamped by the floor either: 2 vehicles
    # against a norm of ~2 stays normal.
    assert assess(b[("cam01", 3)], 2).status == "normal"

    # detect_anomalies ranks the most deviant first and suppresses the normal.
    found = detect_anomalies(b, [
        {"camera_id": "cam01", "hour": 9, "total": 42},    # normal, dropped
        {"camera_id": "cam01", "hour": 3, "total": 30},    # wild spike
        {"camera_id": "cam02", "hour": 9, "total": 500},   # no verdict
        {"camera_id": "cam01", "hour": 9, "total": 55},    # elevated
    ])
    assert [f.status for f in found] == ["high", "high", "insufficient_data"], \
        [(f.camera_id, f.hour, f.status, f.z_score) for f in found]
    assert found[0].camera_id == "cam01" and found[0].hour == 3, found[0]
    assert found[-1].status == "insufficient_data", found[-1]
    assert all(f.camera_id for f in found)

    # An unknown camera is declined, not guessed at.
    unknown = detect_anomalies(b, [{"camera_id": "cam99", "hour": 9, "total": 900}])
    assert unknown[0].status == "insufficient_data"
    assert unknown[0].camera_id == "cam99"

    # bucket_start is accepted in place of an explicit hour.
    via_ts = detect_anomalies(b, [row("cam01", 9, 200)])
    assert via_ts[0].status == "high", via_ts

    # Naive timestamps are tolerated and read as UTC.
    naive = learn([{"camera_id": "cam04", "total": 7,
                    "bucket_start": datetime(2026, 9, 1, 14, 0)}])
    assert ("cam04", 14) in naive

    print("baseline self-check passed")


if __name__ == "__main__":
    _self_check()
