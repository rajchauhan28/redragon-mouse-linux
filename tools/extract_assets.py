#!/usr/bin/env python3
"""Pull the mouse artwork out of the official Windows installer.

The renders used for the button callouts and the lighting preview belong to
Redragon, so they are deliberately *not* committed to this repository.  Point
this script at your own copy of the installer and it will place the handful of
images the GUI wants where the GUI looks for them: the checkout if you are
working in one, otherwise your XDG data directory (which is what you want for
an installed package, since site-packages is root-owned).

    tools/extract_assets.py "REDRAGON M693-RGB Setup v1.0 ….exe"

Requires innoextract (the installer is Inno Setup 5.3.3).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from m693gui import artwork  # noqa: E402

# source path inside the installer -> name the GUI looks for
WANTED = {
    "app/skins/3301/mouse_nr.png": "mouse_body.png",
    "app/skins/3301/mouse_led.png": "mouse_led.png",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("installer", help="path to the Redragon M693 setup .exe")
    ap.add_argument(
        "--into",
        default=None,
        metavar="DIR",
        help="where to put them (default: the checkout if writable, otherwise "
        f"{artwork.USER_DIR})",
    )
    args = ap.parse_args(argv)
    asset_dir = args.into or artwork.install_dir()

    if shutil.which("innoextract") is None:
        print("error: innoextract is not installed", file=sys.stderr)
        return 1
    if not os.path.isfile(args.installer):
        print(f"error: {args.installer} not found", file=sys.stderr)
        return 1

    os.makedirs(asset_dir, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            ["innoextract", "-s", "-e", "-d", tmp, args.installer],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(result.stderr.strip() or "innoextract failed", file=sys.stderr)
            return 1

        missing = []
        for source, target in WANTED.items():
            path = os.path.join(tmp, source)
            if not os.path.exists(path):
                missing.append(source)
                continue
            shutil.copy2(path, os.path.join(asset_dir, target))
            print(f"  {target}")

    if missing:
        print(
            "warning: not found in this installer: " + ", ".join(missing),
            file=sys.stderr,
        )
        print("The GUI will fall back to its schematic view.", file=sys.stderr)
    print(f"\nAssets written to {asset_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
