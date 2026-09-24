"""The one thread allowed to touch the device.

All HID traffic is serialised here so nothing else needs locking, and so the UI
thread never blocks on a link that can stall for seconds at a time.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from m693.device import DeviceError
from m693.keys import ADDR_COMBO, COMBO_REGION_LEN
from m693.profile import PROFILE_LEN

from .backend import FakeDevice, device_present, open_backend

DISCONNECTED = "disconnected"
ASLEEP = "asleep"
ONLINE = "online"

POLL_INTERVAL_MS = 2000
# Charge moves far slower than link state, and every query costs a round trip
# over a lossy link, so it rides the liveness tick rather than having its own.
BATTERY_EVERY = 15  # ticks, i.e. about every 30 s


class DeviceWorker(QObject):
    """Owns the file descriptor and the link state machine."""

    link_state = Signal(str)
    profile_read = Signal(bytes)
    combo_read = Signal(bytes)
    write_succeeded = Signal(list)  # group addresses that landed
    combo_write_succeeded = Signal(list)
    write_failed = Signal(list, str)
    battery = Signal(int, bool)  # percent, charging
    error = Signal(str)

    def __init__(self, use_fake: bool = False, path: str | None = None):
        super().__init__()
        self._use_fake = use_fake
        self._path = path
        self._dev = None
        self._state = DISCONNECTED
        self._timer: QTimer | None = None
        self._ticks = 0

    # -- lifecycle ---------------------------------------------------------
    @Slot()
    def start(self) -> None:
        """Runs once the worker thread's event loop is up."""
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._poll()

    @Slot()
    def stop(self) -> None:
        if self._timer:
            self._timer.stop()
        self._close()

    def _close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.set_driver_status(False)
            except (DeviceError, OSError):
                pass
            try:
                self._dev.close()
            except OSError:
                pass
            self._dev = None

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.link_state.emit(state)

    # -- connection --------------------------------------------------------
    def _ensure_open(self) -> bool:
        if self._dev is not None:
            return True
        if not self._use_fake and not device_present():
            return False
        try:
            self._dev = open_backend(self._use_fake, self._path)
            self._dev.set_driver_status(True)
            return True
        except DeviceError as exc:
            self._dev = None
            self.error.emit(str(exc))
            return False

    @Slot()
    def _poll(self) -> None:
        """Cheap liveness tick.

        GetConnectStatus is answered by the 2.4 GHz receiver even while the
        mouse is asleep, so it is the payload byte - not the fact that a reply
        arrived - that tells us whether the mouse is reachable.
        """
        if not self._ensure_open():
            self._set_state(DISCONNECTED)
            return
        try:
            awake = self._dev.online()
        except (DeviceError, OSError):
            self._close()
            self._set_state(DISCONNECTED)
            return
        self._set_state(ONLINE if awake else ASLEEP)
        self._ticks += 1
        if awake and self._ticks % BATTERY_EVERY == 1:
            self._read_battery()

    def _read_battery(self) -> None:
        """Best effort: a charge reading is never worth disturbing the UI for."""
        if self._dev is None:
            return
        try:
            percent, charging = self._dev.power()
        except (DeviceError, OSError):
            return
        self.battery.emit(percent, charging)

    # -- operations --------------------------------------------------------
    @Slot()
    def read_profile(self) -> None:
        if not self._ensure_open():
            self._set_state(DISCONNECTED)
            return
        try:
            image = self._dev.read(0x00, PROFILE_LEN)
        except DeviceError:
            self._set_state(ASLEEP)
            return
        except OSError as exc:
            self._close()
            self._set_state(DISCONNECTED)
            self.error.emit(str(exc))
            return
        self._set_state(ONLINE)
        self.profile_read.emit(bytes(image))
        self._read_battery()
        try:
            combo = self._dev.read(ADDR_COMBO, COMBO_REGION_LEN)
        except (DeviceError, OSError):
            return  # button bindings stay as last known; the profile still landed
        self.combo_read.emit(bytes(combo))

    @Slot(list)
    def write_blocks(self, blocks: list) -> None:
        """Write raw pre-checksummed blocks, e.g. combo-key records."""
        if not self._ensure_open():
            self._set_state(DISCONNECTED)
            self.write_failed.emit([], "device not connected")
            return
        done = []
        for addr, data in blocks:
            try:
                self._dev.write(addr, bytes(data))
                done.append((addr, bytes(data)))
            except DeviceError as exc:
                self._set_state(ASLEEP)
                self.write_failed.emit([], str(exc))
                break
            except OSError as exc:
                self._close()
                self._set_state(DISCONNECTED)
                self.write_failed.emit([], str(exc))
                break
        if done:
            self.combo_write_succeeded.emit(done)

    @Slot(list, bytes)
    def write_runs(self, runs: list, image: bytes) -> None:
        """Write coalesced runs. `runs` is [(addr, length), ...].

        Addresses are reported back individually so the model can clear exactly
        what landed and keep the rest dirty for the next flush.
        """
        if not self._ensure_open():
            self._set_state(DISCONNECTED)
            self.write_failed.emit(list(runs), "device not connected")
            return
        done, failed, message = [], [], ""
        for addr, length in runs:
            try:
                self._dev.write(addr, image[addr : addr + length])
                done.append((addr, length))
            except DeviceError as exc:
                failed.append((addr, length))
                message = str(exc)
            except OSError as exc:
                self._close()
                self._set_state(DISCONNECTED)
                self.write_failed.emit(failed + [(addr, length)], str(exc))
                return
        if done:
            self._set_state(ONLINE)
            self.write_succeeded.emit(done)
        if failed:
            self._set_state(ASLEEP)
            self.write_failed.emit(failed, message)

    @Slot()
    def nudge(self) -> None:
        """User asked us to try waking the mouse."""
        if isinstance(self._dev, FakeDevice):
            self._dev.wake()
        elif self._dev is not None:
            try:
                self._dev.wait_online(3.0)
            except (DeviceError, OSError):
                pass
        self._poll()
