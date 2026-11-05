#!/usr/bin/env python3
"""2.4 GHz receiver pairing (0x05/0x06) and TellDrvStatus / fd teardown checks."""

import argparse
import io
import os
import sys
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from m693 import cli  # noqa: E402
from m693 import device as D  # noqa: E402
from m693.device import (  # noqa: E402
    CMD_ENTER_PAIR,
    CMD_PAIR_STATUS,
    PAIR_IDLE,
    PAIR_LISTENING,
    PAIR_TIMEOUT,
    Device,
    DeviceError,
)

try:
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


def _pair_reply(state: int, secs: int) -> bytes:
    body = bytearray(16)
    body[0] = CMD_PAIR_STATUS
    body[1] = 0x00
    body[4] = 0x02
    body[5] = state
    body[6] = secs
    return bytes(body)


def test_find_receivers_ignores_wired_mouse():
    import glob as globmod
    import tempfile

    with tempfile.TemporaryDirectory() as root:
        made = []
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
            receivers = D.find_receivers()
        finally:
            D.glob.glob = real

    assert receivers == ["/dev/hidraw1"], receivers


def test_enter_pair_and_pair_status_encoding():
    pkt = Device.build(bytes([CMD_ENTER_PAIR]))
    assert pkt[0] == 0x08 and pkt[1] == 0x05 and pkt[16] == 0x48, pkt.hex(" ")

    sent = []

    class Dummy(Device):
        def __init__(self):
            self.fd = None
            self.path = "/dev/hidraw1"
            self.retries = 1

        def command(self, payload: bytes, expect_reply: bool = True, match=None):
            sent.append(payload)
            if payload[0] == CMD_ENTER_PAIR:
                return bytes([CMD_ENTER_PAIR]) + bytes(15)
            return _pair_reply(PAIR_LISTENING, 23)

    dev = Dummy()
    dev.enter_pair()
    state, secs = dev.pair_status()
    assert sent == [bytes([0x05]), bytes([0x06])], sent
    assert (state, secs) == (PAIR_LISTENING, 23)


def test_pair_skips_when_already_linked_unless_forced():
    class LinkedReceiver:
        path = "/dev/hidraw1"

        def __init__(self):
            self.entered = 0
            self.polls = 0

        def online(self) -> bool:
            self.polls += 1
            # On --force, simulate the link dropping briefly then re-linking.
            return self.entered == 0 or self.polls >= 3

        def pair_status(self) -> tuple[int, int]:
            if self.entered and self.polls < 3:
                return (PAIR_LISTENING, 22)
            return (PAIR_IDLE, 0)

        def enter_pair(self) -> None:
            self.entered += 1

    dev = LinkedReceiver()
    out = io.StringIO()
    with redirect_stdout(out):
        rc = cli.cmd_pair(argparse.Namespace(force=False, timeout=1.0), dev)
    assert rc == 0 and dev.entered == 0, (rc, dev.entered)
    assert "Already linked" in out.getvalue()

    real_sleep = cli.time.sleep
    cli.time.sleep = lambda _s: None
    try:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = cli.cmd_pair(argparse.Namespace(force=True, timeout=2.0), dev)
    finally:
        cli.time.sleep = real_sleep
    assert rc == 0 and dev.entered == 1, (rc, dev.entered)
    assert "Paired on" in out.getvalue()


def test_pair_rearms_expired_window_until_mouse_links():
    # Sequence of pair_status() states: initial idle -> listening ->listening ->
    # window expired (timeout) -> listening -> paired!
    states = [
        (PAIR_IDLE, 0),
        (PAIR_LISTENING, 20),
        (PAIR_LISTENING, 10),
        (PAIR_TIMEOUT, 0),
        (PAIR_LISTENING, 22),
        (PAIR_IDLE, 0),
    ]

    class UnlinkedReceiver:
        path = "/dev/hidraw1"

        def __init__(self):
            self.entered = 0
            self.idx = 0

        def online(self) -> bool:
            return self.idx >= len(states) - 1

        def pair_status(self) -> tuple[int, int]:
            st = states[min(self.idx, len(states) - 1)]
            self.idx += 1
            return st

        def enter_pair(self) -> None:
            self.entered += 1

    dev = UnlinkedReceiver()
    real_sleep = cli.time.sleep
    cli.time.sleep = lambda _s: None
    try:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.cmd_pair(argparse.Namespace(force=False, timeout=5.0), dev)
    finally:
        cli.time.sleep = real_sleep

    # Armed once initially (idle) and once after PAIR_TIMEOUT — never while listening.
    assert rc == 0
    assert dev.entered == 2, dev.entered
    assert "Paired on /dev/hidraw1" in out.getvalue()


def test_pair_refuses_wired_device_cleanly():
    class WiredMouse:
        path = "/dev/hidraw4"

        def online(self) -> bool:
            return False

        def pair_status(self) -> tuple[int, int]:
            raise DeviceError("command 0x06 rejected by device (status 0x01)")

    try:
        cli.cmd_pair(argparse.Namespace(force=False, timeout=1.0), WiredMouse())
    except DeviceError as exc:
        assert "2.4 GHz receiver" in str(exc), exc
    else:
        raise AssertionError("wired device rejection was not translated")


def test_cli_releases_driver_status_on_timeout_and_interrupt():
    events = []

    class AsleepDevice(Device):
        def __init__(self, path=None, retries=9):
            self.fd = None
            self.path = "/dev/hidraw1"
            self.closed = False

        def set_driver_status(self, active: bool) -> None:
            events.append(("status", active))
            if not active:
                # Even if the mouse went completely silent before teardown,
                # release() must swallow it and preserve the CLI's exit code.
                raise DeviceError("no reply", retryable=True)

        def wait_online(self, timeout=20.0, notify=None) -> bool:
            if events[-1] == ("interrupt_next", True):
                raise KeyboardInterrupt
            return False

        def close(self) -> None:
            self.closed = True
            events.append(("close", True))

    real_dev = cli.Device
    cli.Device = AsleepDevice
    try:
        with redirect_stderr(io.StringIO()):
            rc = cli.main(["--wait", "0", "info"])
        assert rc == 1, rc
        assert events == [("status", True), ("status", False), ("close", True)], events

        events.clear()

        class InterruptDevice(AsleepDevice):
            def wait_online(self, timeout=20.0, notify=None) -> bool:
                raise KeyboardInterrupt

        cli.Device = InterruptDevice
        with redirect_stderr(io.StringIO()):
            rc = cli.main(["info"])
        assert rc == 130, rc
        assert events == [("status", True), ("status", False), ("close", True)], events
    finally:
        cli.Device = real_dev


def test_worker_closes_fd_when_driver_status_fails_on_open():
    if not HAVE_QT:
        print("    (skipped: PySide6 not installed)")
        return
    from m693gui import worker as W

    closed = []

    class BrokenDevice:
        def set_driver_status(self, active: bool) -> None:
            raise DeviceError("rejected")

        def close(self) -> None:
            closed.append(True)

    real_present, real_open = W.device_present, W.open_backend
    W.device_present = lambda: True
    W.open_backend = lambda _fake, _path: BrokenDevice()
    try:
        worker = W.DeviceWorker(use_fake=False)
        assert worker._ensure_open() is False
        assert worker._dev is None
        assert closed == [True], "fd leaked when set_driver_status(True) failed"
    finally:
        W.device_present, W.open_backend = real_present, real_open


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
