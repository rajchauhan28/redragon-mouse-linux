#!/usr/bin/env python3
"""Checks for tools/inspect_installer.py (multi-model Cfg.ini reporting)."""

from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import inspect_installer as II  # noqa: E402

SAMPLE_CFG = """\
[OPT]
Title=REDRAGON M693-RGB Gaming Mouse
VID=0x25A7 ; Areson
PID=0xFA7C ; 2.4G receiver
PID2=0xFA7B ; USB-C wired
CID=0x33
MID=0x01
Sensor=1
KM=7
DM=5
DefLevel=2
Debounce=4
ShowMainEffect=1
ShowDpiEffect=1
ShowLogoEffect=1
ShowDpiColor=1
IgnoredKey=should_not_appear

[Sensor0]
Name=PAW3104
MaxDPI=8000

[DPI]
D1=400
D2=800
D3=1600
"""


def test_parse_and_format_report_extracts_opt_and_sensor_blocks():
    cp = II.parse_cfg_text(SAMPLE_CFG)
    report = II.format_report(cp, "Cfg.ini")
    assert "| `VID` | `0x25A7` |" in report, report
    assert "| `PID` | `0xFA7C` |" in report, report
    assert "| `PID2` | `0xFA7B` |" in report, report
    assert "| `CID` | `0x33` |" in report, report
    assert "IgnoredKey" not in report
    assert "#### `[Sensor0]`" in report
    assert "MaxDPI=8000" in report
    assert "#### `[DPI]`" in report


def test_cli_reads_ini_file_and_rejects_missing_path():
    with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as fh:
        fh.write(SAMPLE_CFG)
        ini_path = fh.name
    try:
        out = io.StringIO()
        with redirect_stdout(out):
            rc = II.main([ini_path])
        assert rc == 0
        assert "REDRAGON M693-RGB Gaming Mouse" in out.getvalue()
    finally:
        os.unlink(ini_path)

    err = io.StringIO()
    with redirect_stderr(err):
        rc_missing = II.main(["/nonexistent/path/Setup.exe"])
    assert rc_missing == 1
    assert "not found" in err.getvalue()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {test.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
