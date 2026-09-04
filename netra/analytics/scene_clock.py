"""Recovering true scene time from the burned-in camera overlay.

Correlating a vehicle across cameras requires knowing when each sighting
actually happened. Capture time cannot supply that here: the sandbox replays
recordings, each loop starts whenever a client connects, and two cameras
watched at the same moment are showing scenes recorded at different times.

Every camera on this grid burns a date and time into the frame. Reading it once
per connection anchors the stream to real scene time; from there PTS carries the
clock forward, so the overlay is read rarely rather than every frame.

    scene_time(frame) = anchor_scene_time + (frame.pts_ms - anchor_pts_ms)

That is also why PTS discipline matters elsewhere in the pipeline: it is the
only monotonic clock the stream provides, and here it becomes the bridge
between decoded frames and real time.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# Overlays observed on this grid, in priority order:
#   13-06-2026 23:22:47      dd-mm-yyyy
#   2026-06-13 17:16:01      yyyy-mm-dd
#   14/06/2026 02:47:22 AM   with meridiem
_PATTERNS = [
    (re.compile(r"(\d{2})[-/](\d{2})[-/](\d{4})\D{0,4}(\d{2}):?(\d{2}):?(\d{2})"), "dmy"),
    (re.compile(r"(\d{4})[-/](\d{2})[-/](\d{2})\D{0,4}(\d{2}):?(\d{2}):?(\d{2})"), "ymd"),
]

# OCR frequently loses the separators, producing one run of digits. Two forms
# occur on this grid:
#
#   14 digits  ddmmyyyyHHMMSS          - separators dropped entirely
#   16 digits  dd?mm?yyyyHHMMSS        - each "/" misread as a digit, as in
#                                        "14/06/2026 01:32:03" -> 1410612026013203
#
# The layouts are fixed, so the fields are read by position and then validated;
# anything that does not form a real date is rejected rather than guessed at.
_DIGIT_LAYOUTS = {
    14: {"day": (0, 2), "month": (2, 4), "year": (4, 8),
         "hour": (8, 10), "minute": (10, 12), "second": (12, 14)},
    16: {"day": (0, 2), "month": (3, 5), "year": (6, 10),
         "hour": (10, 12), "minute": (12, 14), "second": (14, 16)},
}


@dataclass
class ClockAnchor:
    """Ties one camera's stream clock to real scene time."""
    camera_id: str
    scene_time: datetime
    pts_ms: float
    confidence: float

    def at(self, pts_ms: float) -> datetime:
        """Scene time for any frame, carried forward by PTS."""
        return self.scene_time + timedelta(milliseconds=pts_ms - self.pts_ms)


def parse_overlay(text: str) -> datetime | None:
    """Interpret one OCR reading of a timestamp overlay.

    Returns None rather than guessing: a wrong scene time would corrupt every
    correlation downstream, which is worse than having none.
    """
    if not text:
        return None
    cleaned = text.strip().upper()

    for pattern, order in _PATTERNS:
        m = pattern.search(cleaned)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        if order == "dmy":
            day, month, year, hh, mm, ss = g
        else:
            year, month, day, hh, mm, ss = g
        if "PM" in cleaned and hh < 12:
            hh += 12
        elif "AM" in cleaned and hh == 12:
            hh = 0
        try:
            return datetime(year, month, day, hh, mm, ss, tzinfo=timezone.utc)
        except ValueError:
            return None

    digits = re.sub(r"\D", "", cleaned)
    layout = _DIGIT_LAYOUTS.get(len(digits))
    if layout:
        f = {name: int(digits[a:b]) for name, (a, b) in layout.items()}
        try:
            return datetime(f["year"], f["month"], f["day"],
                            f["hour"], f["minute"], f["second"],
                            tzinfo=timezone.utc)
        except ValueError:
            return None  # not a real date; do not guess
    return None


def read_scene_time(ocr, frame, pts_ms: float, camera_id: str) -> ClockAnchor | None:
    """Locate and read the timestamp overlay on one frame.

    Only the top and bottom strips are examined - overlays sit in a corner, and
    running OCR over a whole 1080p frame to find a date would be wasteful.
    """
    import cv2

    if frame is None or getattr(frame, "size", 0) == 0:
        return None
    h, w = frame.shape[:2]
    strips = [frame[0:int(h * 0.10), :], frame[int(h * 0.90):h, :]]

    best: tuple[datetime, float] | None = None
    for strip in strips:
        if strip.size == 0:
            continue
        # Overlay glyphs are small; upscaling materially improves the read.
        scale = max(1.0, 90 / max(strip.shape[0], 1))
        big = cv2.resize(strip, None, fx=scale, fy=scale,
                         interpolation=cv2.INTER_CUBIC)
        grey = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
        try:
            results = ocr.readtext(grey, allowlist="0123456789:/-APM ",
                                   detail=1, paragraph=False)
        except Exception:
            continue
        for _box, text, conf in results:
            parsed = parse_overlay(text)
            if parsed and (best is None or conf > best[1]):
                best = (parsed, float(conf))

    if best is None:
        return None
    log.info("%s scene clock anchored to %s (confidence %.2f)",
             camera_id, best[0].isoformat(), best[1])
    return ClockAnchor(camera_id=camera_id, scene_time=best[0],
                       pts_ms=pts_ms, confidence=best[1])


def _self_check() -> None:
    """Overlay parsing decides scene time, which decides every correlation."""
    d = parse_overlay("13-06-2026 23:22:47")
    assert d == datetime(2026, 6, 13, 23, 22, 47, tzinfo=timezone.utc), d

    d = parse_overlay("2026-06-13 17:16:01")
    assert d == datetime(2026, 6, 13, 17, 16, 1, tzinfo=timezone.utc), d

    d = parse_overlay("14/06/2026 02:47:22 AM")
    assert d == datetime(2026, 6, 14, 2, 47, 22, tzinfo=timezone.utc), d

    d = parse_overlay("14/06/2026 02:47:22 PM")
    assert d == datetime(2026, 6, 14, 14, 47, 22, tzinfo=timezone.utc), d

    # Separator-less forms OCR actually produces on this grid.
    # 16 digits: each "/" in 14/06/2026 01:32:03 misread as a digit.
    d = parse_overlay("1410612026013203")
    assert d == datetime(2026, 6, 14, 1, 32, 3, tzinfo=timezone.utc), d
    # 14 digits: separators dropped entirely.
    d = parse_overlay("13062026232247")
    assert d == datetime(2026, 6, 13, 23, 22, 47, tzinfo=timezone.utc), d

    # Nonsense must return None, never a plausible-looking guess.
    assert parse_overlay("") is None
    assert parse_overlay("DELIGHT P1 RLVD") is None
    assert parse_overlay("99-99-2026 99:99:99") is None

    # PTS carries the clock forward from the anchor.
    anchor = ClockAnchor("cam04", datetime(2026, 6, 13, 23, 22, 47,
                                           tzinfo=timezone.utc), 1000.0, 0.9)
    assert anchor.at(61000.0) == datetime(2026, 6, 13, 23, 23, 47,
                                          tzinfo=timezone.utc), anchor.at(61000.0)
    assert anchor.at(1000.0) == anchor.scene_time

    print("scene clock self-check passed")


if __name__ == "__main__":
    _self_check()
