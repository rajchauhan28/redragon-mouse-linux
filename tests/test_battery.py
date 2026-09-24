#!/usr/bin/env python3
"""Battery readout: command 0x04, its decoding, and the status-bar label.

The command and the field offsets come from OemDrv.exe's own GetPower, whose
debug print names both fields; the reply layout below is the one captured from
hardware, charging and not.  No hardware required.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from m693.device import CMD_GET_POWER, Device, DeviceError  # noqa: E402

# The protocol half of this file must run on a machine with no Qt, the same
# split PKGBUILD's check() makes: the driver has no GUI dependency and its
# tests must not acquire one.
try:
    from PySide6.QtWidgets import QApplication

    from m693gui.backend import FakeDevice

    _app = QApplication.instance() or QApplication([])
    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the machine, not the code
    HAVE_QT = False


class ScriptedDevice(Device):
    """A Device whose command() answers from a canned reply, no fd involved."""

    def __init__(self, reply: bytes):
        self._reply = reply
        self.sent = []
        self.fd = None
        self.path = "<scripted>"
        self.retries = 1

    def command(self, payload: bytes, expect_reply: bool = True, match=None):
        self.sent.append(payload)
        return self._reply

    def online(self) -> bool:
        return True


def _reply(percent: int, charging: int) -> bytes:
    """A GetPower reply: command echo, status, then percent at 5, flag at 6."""
    body = bytearray(16)
    body[0] = CMD_GET_POWER
    body[1] = 0x00  # status ok
    body[4] = 0x02
    body[5] = percent
    body[6] = charging
    return bytes(body)


def test_power_uses_command_04():
    dev = ScriptedDevice(_reply(35, 0))
    dev.power()
    assert dev.sent == [bytes([0x04])], dev.sent


def test_power_decodes_percent_and_flag():
    # Exactly the bytes the mouse returned while discharging, then charging.
    assert ScriptedDevice(_reply(35, 0)).power() == (35, False)
    assert ScriptedDevice(_reply(35, 1)).power() == (35, True)
    assert ScriptedDevice(_reply(100, 0)).power() == (100, False)
    assert ScriptedDevice(_reply(0, 0)).power() == (0, False)


def test_implausible_reading_is_rejected_not_clamped():
    """The stock tool drops a reading above 100 rather than pinning it to 100."""
    try:
        ScriptedDevice(_reply(0xFF, 0)).power()
    except DeviceError as exc:
        assert exc.retryable, "a bad reading should be worth retrying"
    else:
        raise AssertionError("101+ should not be reported as a charge level")


def test_a_wired_mouse_is_preferred_over_an_idle_receiver():
    """Both enumerate at once; only the mouse is certainly there.

    Plugging the cable in leaves the receiver on the bus with nothing linked,
    and it still answers - so choosing it strands the UI on "asleep".
    """
    import glob as globmod
    import tempfile

    from m693 import device as D

    with tempfile.TemporaryDirectory() as root:
        made = []
        # Deliberately created receiver-first, and named so that sorting by
        # path alone would keep the receiver in front.
        for name, pid in (("hidraw1", D.PID_RECEIVER), ("hidraw4", D.PID_WIRED)):
            node = os.path.join(root, name, "device")
            os.makedirs(node)
            with open(os.path.join(node, "uevent"), "w") as fh:
                fh.write(f"HID_ID=0003:0000{D.VENDOR_ID:04X}:0000{pid:04X}\n")
            with open(os.path.join(node, "report_descriptor"), "wb") as fh:
                fh.write(b"\x00" + D._FEATURE_COLLECTION + b"\x00")
            made.append(os.path.join(root, name))

        real = globmod.glob
        D.glob.glob = lambda _pattern: sorted(made)
        try:
            found = D.find_devices()
        finally:
            D.glob.glob = real

    assert found == ["/dev/hidraw4", "/dev/hidraw1"], found


def test_a_charge_from_an_unlinked_receiver_is_refused():
    """The receiver answers GetPower with the last figure it saw."""
    from m693 import cli

    class Unlinked(ScriptedDevice):
        def online(self):
            return False

    dev = Unlinked(_reply(35, 0))
    try:
        cli._battery(dev)
    except DeviceError as exc:
        assert "stale" in str(exc), exc
        assert exc.retryable
    else:
        raise AssertionError("a reading from an unlinked receiver is not a charge")

    assert "unavailable" in cli._battery_text(dev)


def test_a_closed_device_refuses_rather_than_crashing():
    """Shutting down mid-read must not tear a file descriptor out from under it."""
    from m693.device import Device

    dev = Device.__new__(Device)
    dev.fd = None
    dev.retries = 3
    try:
        dev.command(bytes([0x04]))
    except DeviceError as exc:
        assert "closed" in str(exc), exc
        assert not exc.retryable, "a closed device will not answer on a retry"
    else:
        raise AssertionError("a closed device should refuse the command")


def test_cli_battery_prints_charge_and_flag():
    import argparse
    import io
    from contextlib import redirect_stdout

    from m693 import cli

    out = io.StringIO()
    with redirect_stdout(out):
        cli.cmd_battery(argparse.Namespace(quiet=False), ScriptedDevice(_reply(35, 1)))
    assert out.getvalue().strip() == "35% (charging)", out.getvalue()

    out = io.StringIO()
    with redirect_stdout(out):
        cli.cmd_battery(argparse.Namespace(quiet=False), ScriptedDevice(_reply(35, 0)))
    assert out.getvalue().strip() == "35%", out.getvalue()

    # -q is meant to be pasted into a status bar, so it is the bare number.
    out = io.StringIO()
    with redirect_stdout(out):
        cli.cmd_battery(argparse.Namespace(quiet=True), ScriptedDevice(_reply(7, 0)))
    assert out.getvalue().strip() == "7", out.getvalue()


def test_info_line_survives_an_unreadable_battery():
    """`info` must still print the rest of the configuration if this fails."""
    from m693 import cli

    class Refusing(ScriptedDevice):
        def command(self, payload, expect_reply=True, match=None):
            raise DeviceError("no reply", retryable=True)

    text = cli._battery_text(Refusing(b""))
    assert text.startswith("unavailable"), text


def test_fake_device_reports_a_plausible_charge():
    if not HAVE_QT:
        print("    (skipped: PySide6 not installed)")
        return
    percent, charging = FakeDevice(flaky=False).power()
    assert 0 < percent <= 100, percent
    assert charging is False


def test_status_bar_shows_and_flags_low_charge():
    if not HAVE_QT:
        print("    (skipped: PySide6 not installed)")
        return
    from m693gui.app import MainWindow

    win = MainWindow(use_fake=True)
    try:
        win._on_battery(64, False)
        assert "64%" in win.battery_label.text()
        assert win.battery_label.property("low") is False

        win._on_battery(11, False)
        assert win.battery_label.property("low") is True, "11% should read as low"

        # Charging is not low, however little is in it.
        win._on_battery(11, True)
        assert win.battery_label.property("low") is False
        assert "11%" in win.battery_label.text()
    finally:
        win.worker.stop()
        win.thread.quit()
        win.thread.wait(2000)
        win.close()


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
