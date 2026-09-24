"""Device backends: the real mouse, or a simulator for offline development."""

from __future__ import annotations

import os
import random
import time

import m693
from m693.device import Device, DeviceError, find_devices
from m693.profile import PROFILE_LEN

# Shipped with the driver so the simulator and "Restore defaults" both work
# from an installed package, not just a checkout.
STOCK_IMAGE = os.path.join(os.path.dirname(m693.__file__), "stock-profile.bin")


class FakeDevice:
    """In-memory stand-in so the UI can be built with no mouse attached.

    It reproduces the two behaviours that actually shape the interface: the
    link falling asleep, and commands occasionally being dropped.
    """

    path = "<simulated>"

    def __init__(self, image: bytes | None = None, flaky: bool = True):
        if image is None:
            try:
                with open(STOCK_IMAGE, "rb") as fh:
                    image = fh.read()
            except OSError:
                image = bytes(PROFILE_LEN)
        # 0x00-0xBF profile, 0x100-0x2FF combo-key records, 0x300-0x1AFF macros.
        # The payload regions start unwritten, which on a real device means
        # 0xFF, not zeros - decoders have to cope with that.
        self._image = bytearray(image[:PROFILE_LEN].ljust(PROFILE_LEN, b"\xff"))
        self._image += b"\xff" * (0x1B00 - len(self._image))
        self._flaky = flaky
        self._born = time.monotonic()
        self._awake_until = time.monotonic() + 30.0

    # -- link simulation ---------------------------------------------------
    def _asleep(self) -> bool:
        return time.monotonic() > self._awake_until

    def wake(self) -> None:
        self._awake_until = time.monotonic() + 30.0

    def _check(self) -> None:
        if self._asleep():
            raise DeviceError("simulated: mouse asleep")
        if self._flaky and random.random() < 0.02:
            raise DeviceError("simulated: dropped packet")

    # -- Device API --------------------------------------------------------
    def set_driver_status(self, active: bool) -> None:
        pass

    def connect_status(self) -> int:
        return 0 if self._asleep() else 1

    def online(self) -> bool:
        return not self._asleep()

    def current_profile(self) -> int:
        return 0

    def power(self) -> tuple[int, bool]:
        """A plausible, slowly-draining charge so the indicator can be seen."""
        self._check()
        elapsed = time.monotonic() - self._born
        return max(1, 87 - int(elapsed // 20) % 87), False

    def wait_online(self, timeout: float = 20.0) -> bool:
        return self.online()

    def read(self, addr: int, length: int) -> bytes:
        self._check()
        return bytes(self._image[addr : addr + length])

    def write(self, addr: int, data: bytes) -> None:
        self._check()
        self._image[addr : addr + len(data)] = data

    def close(self) -> None:
        pass


def open_backend(prefer_fake: bool = False, path: str | None = None):
    """Return a device object, falling back to the simulator when asked."""
    if prefer_fake:
        return FakeDevice()
    return Device(path)


def device_present() -> bool:
    return bool(find_devices())
