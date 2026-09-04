"""Central configuration. Everything tunable lives here."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _local_credentials() -> dict:
    """Read credentials from data/credentials.json if present.

    Kept out of the repository (data/ is gitignored) so the grid password and
    registered email are never committed. Environment variables still win.
    """
    import json
    path = ROOT / "data" / "credentials.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


_CREDS = _local_credentials()


def _setting(env_key: str, cred_key: str, default: str = "") -> str:
    return os.getenv(env_key) or _CREDS.get(cred_key) or default
DATA = ROOT / "data"
EVIDENCE = DATA / "evidence"
MODELS = DATA / "models"
for _d in (DATA, EVIDENCE, MODELS):
    _d.mkdir(parents=True, exist_ok=True)

# --- Sentinel grid -----------------------------------------------------------
GRID_HOST = os.getenv("NETRA_GRID_HOST", "103.250.160.189")
CDN_HOST = os.getenv("NETRA_CDN_HOST", "https://cctv.corp8.cloud")
GRID_PASSWORD = _setting("NETRA_GRID_PASSWORD", "password")
# The portal added an email field mid-challenge; set it if sign-in is required.
GRID_EMAIL = _setting("NETRA_GRID_EMAIL", "email")
CATALOGUE_URL = f"{CDN_HOST}/cameras.json"
#: Fallback used when the credential-gated portal is unreachable.
CATALOGUE_SNAPSHOT = DATA / "cameras.snapshot.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


# The gateway began requiring credentials partway through the challenge.
# Username defaults to the registered email, password to the access key.
RTSP_USER = os.getenv("NETRA_RTSP_USER", "") or GRID_EMAIL
RTSP_PASS = os.getenv("NETRA_RTSP_PASS", "") or GRID_PASSWORD


def _credentials() -> str:
    """`user:pass@` prefix for stream URLs, or empty when the grid is open."""
    if not RTSP_USER:
        return ""
    from urllib.parse import quote
    return f"{quote(RTSP_USER, safe='')}:{quote(RTSP_PASS, safe='')}@"


def rtsp_url(cam_id: str) -> str:
    return f"rtsp://{_credentials()}{GRID_HOST}:8554/stream/{cam_id}"


def whep_url(cam_id: str) -> str:
    return f"http://{GRID_HOST}:8889/stream/{cam_id}/whep"


def hls_url(cam_id: str) -> str:
    return f"{CDN_HOST}/{cam_id}/index.m3u8"


# --- storage -----------------------------------------------------------------
# SQLite by default so the stack runs with zero setup; set NETRA_DB to a
# postgresql+psycopg:// URL to run against PostgreSQL/PostGIS unchanged.
DB_URL = os.getenv("NETRA_DB", f"sqlite:///{DATA / 'netra.db'}")

# --- inference ---------------------------------------------------------------
DEVICE = os.getenv("NETRA_DEVICE", "cuda")
# yolov8m measured on this grid: 131 vehicles across 30 sample frames versus
# yolov8n's 76, at 21ms/frame. The cameras are wide-area night overviews where
# the smaller model simply misses vehicles, and the GPU has the headroom.
VEHICLE_MODEL = os.getenv("NETRA_VEHICLE_MODEL", str(MODELS / "yolov8m.pt"))
PLATE_MODEL = os.getenv("NETRA_PLATE_MODEL", str(MODELS / "plate.pt"))

# Tier-1: every camera scanned at this rate purely to answer "vehicles present?"
TIER1_FPS = float(os.getenv("NETRA_TIER1_FPS", "1.0"))
# Tier-2: cameras with vehicles escalate to this rate for ANPR/ReID
TIER2_FPS = float(os.getenv("NETRA_TIER2_FPS", "5.0"))
# How long a camera stays escalated after its last vehicle sighting (seconds)
ESCALATION_HOLD_S = float(os.getenv("NETRA_ESCALATION_HOLD", "10.0"))

# Detection thresholds. Low confidence is deliberate: these are dark, distant,
# motion-blurred scenes, and a missed vehicle cannot be recovered later while a
# weak detection can still be filtered downstream.
TIER1_IMGSZ = int(os.getenv("NETRA_TIER1_IMGSZ", "640"))
TIER2_IMGSZ = int(os.getenv("NETRA_TIER2_IMGSZ", "960"))
CONF_THRESHOLD = float(os.getenv("NETRA_CONF", "0.20"))

# COCO classes we treat as vehicles
VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

# --- stream handling ---------------------------------------------------------
RECONNECT_BASE_S = 2.0
RECONNECT_MAX_S = 30.0
# A backwards PTS jump larger than this means the loop restarted, not jitter.
LOOP_CUT_THRESHOLD_MS = 2000.0
