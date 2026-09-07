"""Generate API keys for the demo and write the credentials hand-out.

    python tools/make_keys.py

Writes data/api_keys.json (read by the server) and
data/submission-credentials.md (paste into the submission form). Both are
gitignored. Re-running rotates every key.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from netra import config          # noqa: E402
from netra.core import auth       # noqa: E402


def main() -> None:
    keys = auth.generate_keys()
    out = config.DATA / "submission-credentials.md"
    out.write_text(
        "# NETRA test credentials for the screening committee\n\n"
        "Console opens read-only without a key. Paste a key via Sign in "
        "(top right) to unlock the role.\n\n"
        f"- Operator key (recommended for judges): `{keys['operator']}`\n"
        f"- Admin key: `{keys['admin']}`\n"
        f"- Viewer key (not needed): `{keys['viewer']}`\n",
        encoding="utf-8")
    print(f"keys written to {auth.KEYS_PATH}\nhand-out written to {out}")


if __name__ == "__main__":
    main()
