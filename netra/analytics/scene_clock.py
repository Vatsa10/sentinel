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


# A parsed date must be a real recording date, not merely a valid datetime.
# Without this, an OCR misread like "0921-05-16" is accepted and silently
# corrupts every downstream correlation - observed on cam04 at confidence 0.02.
MIN_PLAUSIBLE_YEAR = 2015
MAX_PLAUSIBLE_YEAR = 2035

#: OCR readings below this confidence are discarded. No scene time is better
#: than a wrong one: an incorrect anchor mis-times every sighting on a camera.
MIN_OCR_CONFIDENCE = 0.25


def is_plausible(when: datetime | None) -> bool:
    return bool(when) and MIN_PLAUSIBLE_YEAR <= when.year <= MAX_PLAUSIBLE_YEAR


#: Fraction of frame height searched at the top and bottom edges.
BAND_FRACTION = 0.14
#: Overlay glyphs are only a few pixels tall at source scale; upscaling before
#: OCR is what makes them legible at all.
UPSCALE = 4.0

# EasyOCR's defaults are tuned for document text. Overlay text is thin,
# low-contrast and short, so detection thresholds are lowered. Measured over
# the 30 grid cameras, this tuning together with the binarised variants below
# raised successful anchoring from 2 cameras to 15.
_OCR_PARAMS = dict(allowlist="0123456789:/-APM ", detail=1, paragraph=False,
                   text_threshold=0.5, low_text=0.3, link_threshold=0.3,
                   mag_ratio=2.0)


def _preprocess(image):
    """Yield the variants of one crop that OCR is tried against.

    Overlays on this grid appear as both light-on-dark and dark-on-light, and
    are usually near-white or near-black against a mid-tone scene. Isolating
    those extremes recovers text that fails on the greyscale image alone.
    """
    import cv2
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    yield grey
    yield cv2.bitwise_not(grey)
    yield cv2.threshold(grey, 200, 255, cv2.THRESH_BINARY)[1]
    yield cv2.threshold(grey, 60, 255, cv2.THRESH_BINARY_INV)[1]


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

    def age_s(self, pts_ms: float) -> float:
        """How far, in stream seconds, this anchor is being extrapolated.

        Extrapolation is only as good as the decoder's timing. Small errors in
        PTS accumulate, so an anchor read hours ago is quietly less trustworthy
        than one read a minute ago; callers use this to decide when to re-read
        the overlay rather than extrapolating from one reading forever.
        """
        return (pts_ms - self.pts_ms) / 1000.0


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
    best: tuple[datetime, float] | None = None

    for band in (frame[0:int(h * BAND_FRACTION), :],
                 frame[int(h * (1 - BAND_FRACTION)):h, :]):
        if band.size == 0:
            continue
        bh, bw = band.shape[:2]
        # Overlays sit in a corner, so the halves are searched separately
        # rather than passing a 1920px-wide strip through OCR.
        for piece in (band[:, 0:int(bw * 0.55)], band[:, int(bw * 0.45):bw]):
            if piece.size == 0:
                continue
            big = cv2.resize(piece, None, fx=UPSCALE, fy=UPSCALE,
                             interpolation=cv2.INTER_CUBIC)
            for variant in _preprocess(big):
                try:
                    results = ocr.readtext(variant, **_OCR_PARAMS)
                except Exception:
                    continue
                for _box, text, conf in results:
                    conf = float(conf)
                    if conf < MIN_OCR_CONFIDENCE:
                        continue
                    parsed = parse_overlay(text)
                    if is_plausible(parsed) and (best is None or conf > best[1]):
                        best = (parsed, conf)

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

    # A syntactically valid but impossible recording date must be rejected.
    # cam04 produced exactly this at confidence 0.02 and it would otherwise
    # have mis-timed every sighting on that camera.
    assert not is_plausible(parse_overlay("16-05-0921 20:11:34"))
    assert is_plausible(parse_overlay("13-06-2026 23:22:47"))
    assert not is_plausible(None)

    # PTS carries the clock forward from the anchor.
    anchor = ClockAnchor("cam04", datetime(2026, 6, 13, 23, 22, 47,
                                           tzinfo=timezone.utc), 1000.0, 0.9)
    assert anchor.at(61000.0) == datetime(2026, 6, 13, 23, 23, 47,
                                          tzinfo=timezone.utc), anchor.at(61000.0)
    assert anchor.at(1000.0) == anchor.scene_time

    # Anchor age is measured in stream time, from the frame it was read on.
    assert anchor.age_s(1000.0) == 0.0, anchor.age_s(1000.0)
    assert anchor.age_s(61000.0) == 60.0, anchor.age_s(61000.0)
    # A stream that has rewound (a loop cut before reset) must not read as old.
    assert anchor.age_s(0.0) == -1.0, anchor.age_s(0.0)

    print("scene clock self-check passed")


if __name__ == "__main__":
    _self_check()
