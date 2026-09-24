#!/usr/bin/env python3
"""The CLI has to survive the mouse dozing off mid-command."""

import io
import os
import sys
import time
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from m693 import cli  # noqa: E402
from m693 import profile as P  # noqa: E402
from m693.device import DeviceError  # noqa: E402
from m693gui.backend import FakeDevice  # noqa: E402


class Args:
    def __init__(self, cmd="info", **kw):
        self.cmd = cmd
        self.__dict__.update(kw)


class Napping(FakeDevice):
    """Answers only after it has been asked `wakes_after` times."""

    def __init__(self, wakes_after: int):
        super().__init__(flaky=False)
        self.wakes_after = wakes_after
        self.probes = 0

    def online(self) -> bool:
        self.probes += 1
        return self.probes > self.wakes_after

    def wait_online(self, timeout=20.0, notify=None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        told = False
        while True:
            if self.online():
                return True
            if notify is not None and not told:
                notify()
                told = True
            if deadline is not None and time.monotonic() >= deadline:
                return False


def test_wait_argument_parses_the_three_shapes():
    parser = cli.build_parser()
    assert parser.parse_args(["info"]).wait is None  # forever, by default
    assert parser.parse_args(["--wait", "forever", "info"]).wait is None
    assert parser.parse_args(["--wait", "0", "info"]).wait == 0
    assert parser.parse_args(["--wait", "2.5", "info"]).wait == 2.5


def test_a_sleeping_mouse_is_retried_until_it_answers():
    calls = []

    def handler(args, dev):
        calls.append(1)
        if len(calls) < 3:
            raise DeviceError("no reply to command 0x07", retryable=True)
        return 0

    device = Napping(wakes_after=0)
    original, cli.HANDLERS["info"] = cli.HANDLERS["info"], handler
    try:
        with redirect_stderr(io.StringIO()) as err:
            assert cli._run(Args(), device, None) == 0
    finally:
        cli.HANDLERS["info"] = original
    assert len(calls) == 3, calls
    assert "retrying" in err.getvalue()


def test_a_refusal_is_not_retried():
    """A non-zero status is the device saying no; repeating it is pointless."""
    calls = []

    def handler(args, dev):
        calls.append(1)
        raise DeviceError("command 0x07 rejected by device (status 0x02)")

    device = Napping(wakes_after=0)
    original, cli.HANDLERS["info"] = cli.HANDLERS["info"], handler
    try:
        cli._run(Args(), device, None)
    except DeviceError:
        pass
    else:
        raise AssertionError("a refusal was swallowed")
    finally:
        cli.HANDLERS["info"] = original
    assert len(calls) == 1, "a refusal was retried"


def test_the_deadline_is_honoured():
    def handler(args, dev):
        raise DeviceError("no reply", retryable=True)

    device = Napping(wakes_after=0)
    original, cli.HANDLERS["info"] = cli.HANDLERS["info"], handler
    started = time.monotonic()
    try:
        with redirect_stderr(io.StringIO()):
            cli._run(Args(), device, deadline=time.monotonic() + 0.3)
    except DeviceError:
        pass
    else:
        raise AssertionError("--wait was ignored")
    finally:
        cli.HANDLERS["info"] = original
    assert time.monotonic() - started < 5, "the deadline did not stop the loop"


def test_wait_online_can_wait_indefinitely():
    """timeout=None must not fall out of the loop on its own."""
    device = Napping(wakes_after=4)
    messages = []
    assert device.wait_online(None, notify=lambda: messages.append(1)) is True
    assert device.probes > 4
    assert len(messages) == 1, "the 'mouse is asleep' notice repeated"


def test_retrying_a_command_converges():
    """Re-running a half-applied command has to land on the right result.

    Every subcommand states a target value rather than nudging the current
    one, which is what makes blind retries safe.
    """
    device = FakeDevice(flaky=False)
    args = Args("rate", hz=1000)
    cli.HANDLERS["rate"](args, device)
    cli.HANDLERS["rate"](args, device)  # as if the first half-failed
    assert P.Profile(device.read(0, P.PROFILE_LEN)).polling_rate == 1000


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
