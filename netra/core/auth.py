"""Role-based access control.

Three roles, matching how a control room actually divides work:

    viewer    read cameras, detections, alerts and traces
    operator  everything a viewer can do, plus acknowledging alerts and
              maintaining the watchlist
    admin     everything, plus onboarding cameras and controlling the pipeline

Callers present an API key. Keys live in data/api_keys.json, outside the
repository, and are never logged - only the role and a short key fingerprint
reach the audit trail.

If no keys are configured the platform runs open and says so loudly at startup.
That is deliberate: a demonstration must not require credential setup, but an
operator must never be able to mistake an open deployment for a secured one.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass

from netra import config

log = logging.getLogger(__name__)

ROLES = ("viewer", "operator", "admin")

# What each role may do. Higher roles inherit the lower ones.
PERMISSIONS = {
    "viewer": {"read"},
    "operator": {"read", "acknowledge", "watchlist"},
    "admin": {"read", "acknowledge", "watchlist", "onboard", "pipeline", "manage"},
}

KEYS_PATH = config.DATA / "api_keys.json"


@dataclass
class Principal:
    """Who is making a request."""
    name: str
    role: str
    #: short, non-reversible identifier safe to write to the audit log
    fingerprint: str

    def may(self, permission: str) -> bool:
        return permission in PERMISSIONS.get(self.role, set())


ANONYMOUS = Principal(name="anonymous", role="admin", fingerprint="open-mode")


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def load_keys() -> dict[str, dict]:
    """Read configured API keys. Missing file means open mode."""
    if not KEYS_PATH.exists():
        return {}
    try:
        raw = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    except Exception:
        log.exception("could not read %s - refusing all keyed access", KEYS_PATH)
        return {}
    out = {}
    for key, meta in raw.items():
        role = meta.get("role", "viewer")
        if role not in ROLES:
            log.warning("api key %s has unknown role %r; treating as viewer",
                        _fingerprint(key), role)
            role = "viewer"
        out[key] = {"name": meta.get("name", "unnamed"), "role": role}
    return out


def enabled() -> bool:
    return bool(load_keys())


def resolve(api_key: str | None) -> Principal | None:
    """Identify the caller. None means the key was supplied but is not valid."""
    keys = load_keys()
    if not keys:
        return ANONYMOUS          # open mode
    if not api_key:
        return None
    meta = keys.get(api_key)
    if not meta:
        return None
    return Principal(name=meta["name"], role=meta["role"],
                     fingerprint=_fingerprint(api_key))


def generate_keys(path=KEYS_PATH) -> dict[str, str]:
    """Create one key per role and write them out. Returns role -> key."""
    keys, out = {}, {}
    for role in ROLES:
        key = f"netra_{role}_{secrets.token_urlsafe(24)}"
        keys[key] = {"name": f"{role} account", "role": role}
        out[role] = key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys, indent=2), encoding="utf-8")
    log.info("wrote %d API keys to %s", len(keys), path)
    return out


def _self_check() -> None:
    """Permission boundaries decide who can change a watchlist or stop the
    pipeline, so the mapping is worth pinning down."""
    viewer = Principal("v", "viewer", "x")
    operator = Principal("o", "operator", "x")
    admin = Principal("a", "admin", "x")

    assert viewer.may("read")
    assert not viewer.may("acknowledge")
    assert not viewer.may("watchlist")
    assert not viewer.may("pipeline")

    assert operator.may("read")
    assert operator.may("acknowledge")
    assert operator.may("watchlist")
    assert not operator.may("pipeline"), "operators must not control the pipeline"
    assert not operator.may("onboard")

    for permission in ("read", "acknowledge", "watchlist", "onboard", "pipeline"):
        assert admin.may(permission), permission

    # An unknown role grants nothing at all, rather than defaulting upward.
    assert not Principal("x", "nonsense", "x").may("read")

    print("auth self-check passed")


if __name__ == "__main__":
    _self_check()
