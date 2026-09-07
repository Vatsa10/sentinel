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

#: Enforced mode caller with no key. Read-only; the screening committee opens
#: the console without a credential and signs in only to change things.
ANONYMOUS_VIEWER = Principal(name="anonymous", role="viewer", fingerprint="anon")


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


#: Sentinel returned by load_keys() when the file exists but could not be
#: parsed. Distinct from {} (no file, open mode): a file that is present but
#: unreadable must never be treated the same as no file at all, or a
#: corrupted keys file would silently reopen the platform to every caller as
#: admin. resolve() checks for this sentinel and refuses every caller until
#: the file is fixed.
_UNREADABLE = object()

_cache_mtime: float | None = None
_cache_keys: dict[str, dict] | object = {}
_warned_unreadable = False


def _parse_keys(raw: dict) -> dict[str, dict]:
    out = {}
    for key, meta in raw.items():
        role = meta.get("role", "viewer")
        if role not in ROLES:
            log.warning("api key %s has unknown role %r; treating as viewer",
                        _fingerprint(key), role)
            role = "viewer"
        out[key] = {"name": meta.get("name", "unnamed"), "role": role}
    return out


def load_keys() -> dict[str, dict]:
    """Read configured API keys. Missing file means open mode.

    Cached and re-read only when the file's mtime changes, since every
    request that resolves a principal calls this - re-reading and
    re-parsing the file on each one is pure overhead once keys are stable.
    """
    global _cache_mtime, _cache_keys, _warned_unreadable
    if not KEYS_PATH.exists():
        _cache_mtime, _cache_keys, _warned_unreadable = None, {}, False
        return {}
    try:
        mtime = KEYS_PATH.stat().st_mtime
    except OSError:
        mtime = None
    if mtime is not None and mtime == _cache_mtime and _cache_keys is not None:
        return {} if _cache_keys is _UNREADABLE else _cache_keys
    try:
        raw = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
        out = _parse_keys(raw)
        _cache_mtime, _cache_keys, _warned_unreadable = mtime, out, False
        return out
    except Exception:
        if not _warned_unreadable:
            log.exception("could not read %s - refusing all keyed access "
                          "until this is fixed", KEYS_PATH)
            _warned_unreadable = True
        _cache_mtime, _cache_keys = mtime, _UNREADABLE
        return {}


def enabled() -> bool:
    return bool(load_keys())


def resolve(api_key: str | None) -> Principal | None:
    """Identify the caller. None means the caller must be refused (401).

    An unreadable-but-present keys file is not the same as no file: it must
    never fall back to open-mode admin, so every caller is refused until the
    file is fixed, whether or not they presented a key.
    """
    keys = load_keys()
    if KEYS_PATH.exists() and _cache_keys is _UNREADABLE:
        return None
    if not keys:
        return ANONYMOUS          # open mode
    if not api_key:
        return ANONYMOUS_VIEWER   # enforced mode, read-only without a key
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

    # Enforced mode: no key is a viewer, wrong key is refused.
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp()) / "keys.json"
    tmp.write_text(json.dumps({"k1": {"name": "op", "role": "operator"}}))
    global KEYS_PATH, _cache_mtime, _cache_keys, _warned_unreadable
    saved, KEYS_PATH = KEYS_PATH, tmp
    _cache_mtime, _cache_keys, _warned_unreadable = None, {}, False
    try:
        assert resolve(None).role == "viewer", "no key must be anonymous viewer"
        assert resolve("bogus") is None, "unknown key must be refused"
        assert resolve("k1").role == "operator"

        # mtime cache: editing the file without a mtime change must not be
        # required for correctness under normal use, but a real edit (which
        # does bump mtime) must be picked up rather than served stale.
        import time
        time.sleep(0.01)
        tmp.write_text(json.dumps({"k2": {"name": "v", "role": "viewer"}}))
        assert resolve("k1") is None, "old key must stop working after rewrite"
        assert resolve("k2").role == "viewer"

        # An unreadable-but-present file must refuse every caller, not fall
        # back to open-mode admin - the whole point of B2.
        time.sleep(0.01)
        tmp.write_text("{ not json")
        _cache_mtime = None   # force a re-read past the mtime cache
        assert resolve(None) is None, \
            "unreadable keys file must refuse every caller, not open admin"
        assert resolve("k2") is None
    finally:
        KEYS_PATH = saved
        _cache_mtime, _cache_keys, _warned_unreadable = None, {}, False
    print("auth self-check ok")

    print("auth self-check passed")


if __name__ == "__main__":
    _self_check()
