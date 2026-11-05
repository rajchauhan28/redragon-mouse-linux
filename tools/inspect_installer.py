#!/usr/bin/env python3
"""Inspect a Redragon Windows installer (.exe) or Cfg.ini for multi-model support.

Most Compx / Areson wireless Redragon mice ship an Inno Setup installer packing
``OemDrv.exe`` and ``app/Cfg.ini``.  ``Cfg.ini`` declares the USB VID/PIDs,
CID/MID identity, sensor ID, DPI ladder and register table, button count, and
lighting flags — everything needed to gauge protocol compatibility without
running Windows.

Usage::

    python3 tools/inspect_installer.py "REDRAGON_Mxxx_Setup.exe"
    python3 tools/inspect_installer.py path/to/Cfg.ini
"""

from __future__ import annotations

import argparse
import configparser
import os
import shutil
import subprocess
import sys
import tempfile

OPT_KEYS = (
    "Title",
    "VID",
    "PID",
    "PID2",
    "CID",
    "MID",
    "Sensor",
    "KM",
    "DM",
    "DefLevel",
    "Debounce",
    "DR",
    "ShowMainEffect",
    "ShowDpiEffect",
    "ShowLogoEffect",
    "ShowDpiColor",
    "LEDParam",
)


def _find_cfg_ini(root: str) -> str | None:
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.lower() == "cfg.ini":
                return os.path.join(dirpath, name)
    return None


def parse_cfg_text(text: str) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str  # preserve original casing for readability
    cp.read_string(text)
    return cp


def format_report(cp: configparser.ConfigParser, source_name: str) -> str:
    lines = [f"### Installer / `Cfg.ini` report (`{source_name}`)", ""]
    opt = next((cp[s] for s in cp.sections() if s.upper() == "OPT"), None)
    if opt is not None:
        lines.append("| Key | Value |")
        lines.append("| --- | --- |")
        wanted = {k.lower(): k for k in OPT_KEYS}
        actual = {k.lower(): (k, v) for k, v in opt.items()}
        for low in wanted:
            if low in actual:
                orig_k, val = actual[low]
                lines.append(f"| `{orig_k}` | `{val.split(';')[0].strip()}` |")
        lines.append("")

    for sec in cp.sections():
        up = sec.upper()
        if up.startswith("SENSOR") or up in ("DPI", "MS"):
            lines.append(f"#### `[{sec}]`")
            lines.append("```ini")
            for k, v in cp[sec].items():
                lines.append(f"{k}={v}")
            lines.append("```")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", help="path to Setup.exe or Cfg.ini")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.target):
        print(f"error: {args.target} not found", file=sys.stderr)
        return 1

    if args.target.lower().endswith(".ini"):
        with open(args.target, encoding="utf-8", errors="replace") as fh:
            cp = parse_cfg_text(fh.read())
        print(format_report(cp, os.path.basename(args.target)), end="")
        return 0

    if shutil.which("innoextract") is None:
        print(
            "error: innoextract is required to unpack .exe installers "
            "(or pass an already-extracted Cfg.ini)",
            file=sys.stderr,
        )
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        res = subprocess.run(
            ["innoextract", "-s", "-e", "-d", tmp, args.target],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            print(
                res.stderr.strip() or "error: innoextract could not unpack file",
                file=sys.stderr,
            )
            return 1
        cfg_path = _find_cfg_ini(tmp)
        if cfg_path is None:
            print(
                "error: unpacked installer does not contain a Cfg.ini "
                "(may use a different OEM toolkit)",
                file=sys.stderr,
            )
            return 1
        with open(cfg_path, encoding="utf-8", errors="replace") as fh:
            cp = parse_cfg_text(fh.read())
        print(format_report(cp, os.path.basename(args.target)), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
