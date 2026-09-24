"""Where the mouse renders come from.

The renders are Redragon's, so they are neither committed to this repository
nor shipped in the package; the GUI falls back to a schematic without them.
A user who supplies their own copy of the installer gets them extracted into
their data directory - which is also the only writable place once the package
is installed system-wide - so that is searched first, then the package itself
for the case where you are running from a checkout.
"""

from __future__ import annotations

import os

PACKAGE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "assets", "device"
)

_DATA_HOME = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
USER_DIR = os.path.join(_DATA_HOME, "m693", "device")

SEARCH_PATH = (USER_DIR, PACKAGE_DIR)


def find(name: str) -> str | None:
    """Absolute path to a render, or None if it was never extracted."""
    for directory in SEARCH_PATH:
        path = os.path.join(directory, name)
        if os.path.exists(path):
            return path
    return None


def install_dir() -> str:
    """Where extract_assets.py should put them.

    A checkout keeps them in the tree so the tests and a `git status` see them;
    anything else goes to the user's data directory.
    """
    if os.path.isdir(PACKAGE_DIR) and os.access(PACKAGE_DIR, os.W_OK):
        return PACKAGE_DIR
    parent = os.path.dirname(PACKAGE_DIR)
    if os.path.isdir(parent) and os.access(parent, os.W_OK):
        return PACKAGE_DIR
    return USER_DIR
