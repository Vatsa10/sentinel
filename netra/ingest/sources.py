"""Source adapters.

A source is anything that can be opened as a sequence of timestamped frames.
The platform onboards cameras through adapters so that supporting a new kind of
source - another protocol, another vendor SDK, a file, an HLS endpoint - means
adding an adapter, not changing the ingest, inference or matching layers.

Three are implemented:

  RtspSource  the Sentinel grid and any ONVIF/RTSP camera
  HlsSource   the same grid over HLS, for networks where 8554 is blocked
  FileSource  local video, used to demonstrate the platform on footage the
              participant supplies

FileSource matters beyond convenience: the government grid is composed of
wide-area night overview cameras where plate recognition is not achievable, so
end-to-end plate recognition has to be demonstrated on footage where plates are
actually resolvable.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
import cv2  # noqa: E402

from netra import config  # noqa: E402

log = logging.getLogger(__name__)


@dataclass
class SourceSpec:
    """How to reach one source, independent of what kind it is."""
    camera_id: str
    kind: str                 # rtsp | hls | file
    uri: str
    #: replay a file endlessly, mirroring how the grid loops its recordings
    loop: bool = False


class Source:
    """Common interface every adapter presents to the ingest layer."""

    def __init__(self, spec: SourceSpec):
        self.spec = spec
        self.cap: cv2.VideoCapture | None = None

    def open(self) -> None:
        self.cap = cv2.VideoCapture(self.spec.uri, cv2.CAP_FFMPEG)
        if not self.cap.isOpened():
            raise ConnectionError(f"could not open {self.spec.kind} source")

    def read(self):
        """Return (ok, frame, pts_ms)."""
        ok, frame = self.cap.read()
        if not ok:
            return False, None, 0.0
        return True, frame, float(self.cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)

    def release(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class RtspSource(Source):
    """Live RTSP, forced over TCP.

    UDP is accepted by the gateway but fails across NAT and most corporate
    firewalls, and partial UDP delivery produces corrupt frames that are easily
    mistaken for model faults.
    """


class HlsSource(Source):
    """HLS fallback for networks where the RTSP port is blocked.

    Higher latency than RTSP, but it traverses the CDN and works anywhere the
    portal session works.
    """


class FileSource(Source):
    """Local video file, optionally looped.

    Looping deliberately reproduces the discontinuity behaviour of the live
    grid, so the same reset logic is exercised in both cases.
    """

    def read(self):
        ok, frame = self.cap.read()
        if not ok and self.spec.loop:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.cap.read()
        if not ok:
            return False, None, 0.0
        return True, frame, float(self.cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)


ADAPTERS = {"rtsp": RtspSource, "hls": HlsSource, "file": FileSource}


def build(spec: SourceSpec) -> Source:
    adapter = ADAPTERS.get(spec.kind)
    if adapter is None:
        raise ValueError(f"no adapter registered for source kind '{spec.kind}'")
    return adapter(spec)


def spec_for_camera(camera_id: str, kind: str | None = None,
                    uri: str | None = None) -> SourceSpec:
    """Resolve how to reach a registered camera.

    Stream URLs are derived from the camera id rather than stored, because the
    grid documentation is explicit that the catalogue is the contract and the
    URL pattern is not.
    """
    kind = kind or os.getenv("NETRA_SOURCE_KIND", "rtsp")
    if uri:
        return SourceSpec(camera_id, kind, uri, loop=(kind == "file"))
    if kind == "hls":
        return SourceSpec(camera_id, "hls", config.hls_url(camera_id))
    return SourceSpec(camera_id, "rtsp", config.rtsp_url(camera_id))
