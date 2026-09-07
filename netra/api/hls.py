"""On-demand HLS relay: one ffmpeg per watched camera, capped, self-cleaning.

The console's default tile is MJPEG with overlays. When an operator wants
smooth full-rate video for one camera they toggle "Smooth", which starts a
relay here. Relays die 60 s after the last playlist fetch, and at most
HLS_MAX_CONCURRENT run at once so the tunnel is never saturated.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

from netra import config

log = logging.getLogger(__name__)
HLS_DIR = config.DATA / "hls"


class _Relay:
    def __init__(self, camera_id: str, proc: subprocess.Popen, log_fh):
        self.camera_id = camera_id
        self.proc = proc
        self.log_fh = log_fh
        self.started = time.time()
        self.last_touch = time.time()


class HlsManager:
    def __init__(self, max_concurrent: int = None, idle_s: float = None):
        self.max_concurrent = max_concurrent or config.HLS_MAX_CONCURRENT
        self.idle_s = idle_s or config.HLS_IDLE_S
        self._relays: dict[str, _Relay] = {}
        self._lock = threading.Lock()
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True,
                                        name="hls-reaper")
        self._reaper.start()

    # -- command -------------------------------------------------------------
    @staticmethod
    def command(source: str, out_dir: Path, hevc: bool) -> list[str]:
        video = (["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                  "-g", "50"] if hevc else ["-c:v", "copy"])
        return ["ffmpeg", "-v", "error", "-nostdin",
                *(["-rtsp_transport", "tcp"] if source.startswith("rtsp") else
                  ["-re", "-stream_loop", "-1"]),
                "-i", source, *video, "-an",
                "-f", "hls", "-hls_time", "2", "-hls_list_size", "6",
                "-hls_flags", "delete_segments+omit_endlist",
                str(out_dir / "index.m3u8")]

    @staticmethod
    def _close(relay: "_Relay") -> None:
        """Terminate the process, close its log handle, remove its output
        directory. The one place any of that happens, so every path that
        retires a relay - a clean stop, a restart replacing a dead one, or
        a failed Popen - releases the same handles the same way.
        """
        if relay.proc.poll() is None:
            relay.proc.terminate()
            try:
                relay.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                relay.proc.kill()
        try:
            relay.log_fh.close()
        except Exception:
            pass
        shutil.rmtree(HLS_DIR / relay.camera_id, ignore_errors=True)

    def start(self, camera_id: str, source: str, hevc: bool) -> dict:
        with self._lock:
            live = self._relays.get(camera_id)
            if live and live.proc.poll() is None:
                live.last_touch = time.time()
                already_running = True
            else:
                already_running = False
                if live is not None:
                    # The previous relay for this camera exited on its own
                    # (crash, source drop) without anyone calling stop(): its
                    # log handle is still open and its directory still on
                    # disk. Release both before starting the replacement, or
                    # every restart leaks one file handle and directory.
                    self._close(live)
                    self._relays.pop(camera_id, None)
                running = [r for r in self._relays.values() if r.proc.poll() is None]
                if len(running) >= self.max_concurrent:
                    return {"camera_id": camera_id, "running": False,
                            "error": f"at most {self.max_concurrent} smooth streams",
                            "concurrent": len(running)}
                out_dir = HLS_DIR / camera_id
                shutil.rmtree(out_dir, ignore_errors=True)
                out_dir.mkdir(parents=True, exist_ok=True)
                log_fh = open(out_dir / "ffmpeg.log", "wb")
                try:
                    proc = subprocess.Popen(self.command(source, out_dir, hevc),
                                            stdout=subprocess.DEVNULL,
                                            stderr=log_fh)
                except Exception:
                    # Popen itself failed (e.g. ffmpeg missing/unresolvable
                    # path): the handle we just opened and the directory we
                    # just created must not be left behind.
                    try:
                        log_fh.close()
                    except Exception:
                        pass
                    shutil.rmtree(out_dir, ignore_errors=True)
                    raise
                self._relays[camera_id] = _Relay(camera_id, proc, log_fh)
        return self.status(camera_id)

    def stop(self, camera_id: str) -> bool:
        with self._lock:
            r = self._relays.pop(camera_id, None)
        if not r:
            return False
        self._close(r)
        return True

    def touch(self, camera_id: str) -> None:
        with self._lock:
            r = self._relays.get(camera_id)
            if r:
                r.last_touch = time.time()

    def status(self, camera_id: str) -> dict:
        with self._lock:
            relays = dict(self._relays)
        r = relays.get(camera_id)
        running = bool(r and r.proc.poll() is None)
        playlist = HLS_DIR / camera_id / "index.m3u8"
        return {"camera_id": camera_id, "running": running,
                "ready": running and playlist.exists(),
                "url": f"/hls/{camera_id}/index.m3u8",
                "age_s": round(time.time() - r.started, 1) if r else None,
                "concurrent": sum(1 for x in relays.values()
                                  if x.proc.poll() is None),
                "max_concurrent": self.max_concurrent}

    def stop_all(self) -> None:
        for cid in list(self._relays):
            self.stop(cid)

    # -- reaper --------------------------------------------------------------
    def _reap_loop(self) -> None:
        while True:
            time.sleep(5)
            now = time.time()
            for cid, r in list(self._relays.items()):
                dead = r.proc.poll() is not None
                idle = now - r.last_touch > self.idle_s
                if dead or idle:
                    if dead:
                        try:
                            err = (HLS_DIR / cid / "ffmpeg.log").read_bytes()[-300:]
                            err = err.decode(errors="replace")
                        except OSError:
                            err = ""
                        log.warning("hls relay %s exited: %s", cid, err.strip())
                    self.stop(cid)


HLS = HlsManager()


def _self_check() -> None:
    import tempfile
    assert shutil.which("ffmpeg"), "ffmpeg not on PATH"
    # Synthetic source: 3 s of colour bars as a file, relayed as HLS.
    tmp = Path(tempfile.mkdtemp())
    src = tmp / "bars.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=10", "-t", "3", "-pix_fmt",
                    "yuv420p", str(src)], check=True)
    m = HlsManager(max_concurrent=1, idle_s=4)
    st = m.start("selfcheck", str(src), hevc=False)
    assert st["running"], st
    for _ in range(40):
        if m.status("selfcheck")["ready"]:
            break
        time.sleep(0.25)
    assert m.status("selfcheck")["ready"], "playlist never appeared"
    denied = m.start("second", str(src), hevc=False)
    assert not denied["running"] and "at most" in denied["error"]
    time.sleep(10)          # idle > 4 s: reaper must have stopped it
    assert not m.status("selfcheck")["running"], "reaper did not stop idle relay"
    assert not (HLS_DIR / "selfcheck").exists()

    # A Popen that raises (bogus ffmpeg path) must leave no handle or
    # directory behind - the leak this self-check exists to catch.
    import netra.api.hls as hls_mod
    m2 = HlsManager(max_concurrent=1, idle_s=4)
    real_popen = subprocess.Popen

    def _bad_popen(*a, **kw):
        raise FileNotFoundError("bogus ffmpeg path for this self-check")

    subprocess.Popen = _bad_popen
    try:
        raised = False
        try:
            m2.start("badpath", str(src), hevc=False)
        except FileNotFoundError:
            raised = True
    finally:
        subprocess.Popen = real_popen
    assert raised, "Popen failure should propagate, not be swallowed"
    assert "badpath" not in m2._relays, "no relay should be registered"
    assert not (HLS_DIR / "badpath").exists(), \
        "output directory must be cleaned up after a failed Popen"

    print("hls self-check ok")


if __name__ == "__main__":
    _self_check()
