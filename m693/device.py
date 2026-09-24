"""Transport layer for the Redragon M693 configuration protocol.

Reverse engineered from ``OemDrv.exe`` (REDRAGON M693-RGB Setup v1.0 20221013).
See PROTOCOL.md for the derivation.

Wire format
-----------
Host -> device is HID *feature report 0x08* on the vendor collection
(usage page 0xFF02), 17 bytes total::

    [0]     0x08                report id
    [1]     command
    [2]     addr bits 16..23    (always 0 on this device)
    [3]     addr bits 8..15
    [4]     length / sub-arg
    [5..15] payload
    [16]    checksum = (0x55 - sum(bytes[0..15])) & 0xFF

Device -> host is HID *input report 0x09* on the same interface
(usage page 0xFF01), 17 bytes, with the report id stripped by
:meth:`Device._recv`::

    [0]     command echo
    [1]     status (0 == OK)
    [2..3]  address
    [4]     length
    [5..14] payload
    [15]    checksum
"""

from __future__ import annotations

import errno
import fcntl
import glob
import os
import time

VENDOR_ID = 0x25A7
PID_WIRED = 0xFA7B  # the mouse itself, on its cable
PID_RECEIVER = 0xFA7C  # the 2.4 GHz receiver
PRODUCT_IDS = (PID_WIRED, PID_RECEIVER)

REPORT_OUT = 0x08
REPORT_IN = 0x09
PACKET_LEN = 17
MAX_PAYLOAD = 10  # bytes of data per EEPROM read/write command

# Commands recovered from OemDrv.exe.
CMD_DRIVER_STATUS = 0x02  # TellDrvStatus(on)
CMD_CONNECT_STATUS = 0x03  # GetConnectStatus
CMD_GET_POWER = 0x04  # GetPower - battery charge and charging flag
CMD_WRITE = 0x07  # WriteEEPROM
CMD_READ = 0x08  # ReadEEPROM
CMD_CUR_PROFILE = 0x0E  # GetCurProfile
CMD_QUERY_0F = 0x0F
CMD_SET_PROFILE = 0x10

# Marker for the vendor feature collection (usage page 0xFF02, report id 8)
# inside the HID report descriptor.  Interface 1 carries it; interface 0 is
# the plain mouse and must not be touched.
_FEATURE_COLLECTION = b"\x06\x02\xff\x09\x02\xa1\x01\x85\x08"


def _HIDIOCSFEATURE(length: int) -> int:
    # _IOC(_IOC_WRITE|_IOC_READ, 'H', 0x06, length)
    return 0xC0000000 | (length << 16) | (ord("H") << 8) | 0x06


class DeviceError(Exception):
    """Device refused a command, or did not answer in time.

    ``retryable`` separates the two: silence usually means the mouse has
    dozed off and the same command will work once it wakes, whereas an
    explicit non-zero status is a refusal that will not change on its own.
    """

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def checksum(data: bytes) -> int:
    """The device's additive checksum: everything must sum to 0x55."""
    return (0x55 - sum(data)) & 0xFF


def find_devices() -> list[str]:
    """Vendor-control interfaces present, the most likely to answer first.

    Plugging the cable in puts the mouse into wired mode: it enumerates in its
    own right as ``PID_WIRED`` while the receiver stays on the bus with nothing
    linked to it.  That receiver still answers - it reports the mouse offline
    and hands back the *last* battery reading it saw - so picking it over a
    mouse that is sitting right there leaves everything stuck on "asleep".
    A directly connected mouse is certainly present, so it goes first.
    """
    found = []
    for sysfs in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            uevent = open(os.path.join(sysfs, "device", "uevent")).read()
        except OSError:
            continue
        hid_id = ""
        for line in uevent.splitlines():
            if line.startswith("HID_ID="):
                hid_id = line.split("=", 1)[1].upper()
        if not hid_id:
            continue
        parts = hid_id.split(":")
        if len(parts) != 3:
            continue
        vid, pid = int(parts[1], 16), int(parts[2], 16)
        if vid != VENDOR_ID or pid not in PRODUCT_IDS:
            continue
        try:
            descriptor = open(
                os.path.join(sysfs, "device", "report_descriptor"), "rb"
            ).read()
        except OSError:
            continue
        if _FEATURE_COLLECTION in descriptor:
            found.append((pid != PID_WIRED, "/dev/" + os.path.basename(sysfs)))
    return [path for _receiver, path in sorted(found)]


class Device:
    """A connected M693. Use as a context manager."""

    def __init__(self, path: str | None = None, retries: int = 9):
        if path is None:
            nodes = find_devices()
            if not nodes:
                raise DeviceError(
                    "No Redragon M693 found. Check that it is plugged in "
                    "(lsusb should show 25a7:fa7b or 25a7:fa7c)."
                )
            path = nodes[0]
        self.path = path
        self.retries = retries
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError as exc:
            raise DeviceError(
                f"No permission to open {path}. Install the bundled udev rule "
                f"(99-redragon-m693.rules) and re-plug the mouse, or run as root."
            ) from exc
        except OSError as exc:
            raise DeviceError(f"Cannot open {path}: {exc}") from exc

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> "Device":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if getattr(self, "fd", None) is not None:
            os.close(self.fd)
            self.fd = None

    # -- framing -----------------------------------------------------------
    @staticmethod
    def build(payload: bytes) -> bytes:
        if len(payload) > 15:
            raise ValueError("payload longer than 15 bytes")
        buf = bytearray(PACKET_LEN)
        buf[0] = REPORT_OUT
        buf[1 : 1 + len(payload)] = payload
        buf[16] = checksum(buf[0:16])
        return bytes(buf)

    def _drain(self) -> None:
        """Discard stale input reports so a reply cannot be mistaken."""
        while True:
            try:
                os.read(self.fd, 64)
            except BlockingIOError:
                return
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    return
                raise

    def _send(self, payload: bytes) -> None:
        fcntl.ioctl(self.fd, _HIDIOCSFEATURE(PACKET_LEN), bytearray(self.build(payload)))

    def _recv(self, command: int, timeout: float = 0.5, match=None) -> bytes | None:
        """Wait for a reply to `command`.

        `match` further qualifies the reply.  This matters: the command byte
        alone does not identify which request a packet answers, so on a lossy
        link a late reply to an *earlier* read can otherwise be mistaken for
        the answer to the current one - silently returning another address's
        contents.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data = os.read(self.fd, PACKET_LEN)
            except BlockingIOError:
                time.sleep(0.001)
                continue
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    time.sleep(0.001)
                    continue
                raise
            if len(data) != PACKET_LEN or data[0] != REPORT_IN or data[1] != command:
                continue
            reply = data[1:]
            if match is not None and not match(reply):
                continue  # stale reply to a different request
            return reply
        return None

    @staticmethod
    def _addressed(addr: int, length: int):
        """Matcher requiring the reply to echo the address and length we asked for."""

        def check(reply: bytes) -> bool:
            return (
                reply[2] == (addr >> 8) & 0xFF
                and reply[3] == addr & 0xFF
                and reply[4] == length
            )

        return check

    def command(self, payload: bytes, expect_reply: bool = True, match=None) -> bytes | None:
        """Send one command, returning the 16-byte response body.

        Commands are relayed over the 2.4 GHz link to the mouse itself, which
        drops packets and can idle out between operations.  The Windows driver
        retries each command; we do the same and additionally re-assert the
        driver-attached state, which is what brings a dozing mouse back.
        """
        if self.fd is None:
            # Closing the device under an in-flight command is a shutdown race,
            # not a link fault: report it as a refusal so callers unwind rather
            # than retrying against a file descriptor that no longer exists.
            raise DeviceError("the device has been closed")
        last = None
        for attempt in range(self.retries):
            if attempt and attempt % 3 == 0 and payload[0] != CMD_DRIVER_STATUS:
                self._nudge()
            self._drain()
            self._send(payload)
            if not expect_reply:
                return None
            reply = self._recv(payload[0], match=match)
            if reply is not None:
                if reply[1] == 0:
                    return reply
                last = reply[1]
            time.sleep(0.02 * (attempt + 1))
        if last is not None:
            raise DeviceError(
                f"command {payload[0]:#04x} rejected by device (status {last:#04x})"
            )
        raise DeviceError(
            f"no reply to command {payload[0]:#04x} - the mouse may be asleep "
            f"or out of range; move it and try again",
            retryable=True,
        )

    def _nudge(self) -> None:
        """Best-effort wake: re-announce the driver and ping the link."""
        for probe in (
            bytes([CMD_DRIVER_STATUS, 0, 0, 0, 1, 1]),
            bytes([CMD_CONNECT_STATUS]),
        ):
            try:
                self._drain()
                self._send(probe)
                self._recv(probe[0], timeout=0.25)
            except OSError:
                pass

    def online(self) -> bool:
        """True when the mouse is linked and awake.

        ``GetConnectStatus`` is answered by the 2.4 GHz receiver even while the
        mouse is asleep, so the reply arriving proves nothing - it is the
        payload byte that carries the link state.
        """
        try:
            return self.connect_status() != 0
        except DeviceError:
            return False

    def wait_online(self, timeout: float | None = 20.0, notify=None) -> bool:
        """Block until the mouse is awake and relaying configuration commands.

        ``timeout=None`` waits indefinitely, which is what the CLI does by
        default: a sleeping mouse is the normal resting state, not a fault, so
        the useful behaviour is to hold the command until someone touches the
        mouse rather than to fail and make them run it again.

        ``notify`` is called once, the first time we find the mouse asleep, so
        a caller can explain the wait without printing on the common path.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        told = False
        while True:
            if self.online():
                # Confirm with a read, which must be relayed to the mouse.
                self._drain()
                self._send(bytes([CMD_READ, 0, 0, 0, 1]))
                if self._recv(CMD_READ, timeout=0.4) is not None:
                    return True
            if notify is not None and not told:
                notify()
                told = True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            self._nudge()
            time.sleep(0.2)

    # -- high level primitives --------------------------------------------
    def set_driver_status(self, active: bool) -> None:
        """TellDrvStatus - tells the mouse a configuration tool is attached."""
        self.command(bytes([CMD_DRIVER_STATUS, 0, 0, 0, 1, 1 if active else 0]))

    def connect_status(self) -> int:
        return self.command(bytes([CMD_CONNECT_STATUS]))[5]

    def current_profile(self) -> int:
        return self.command(bytes([CMD_CUR_PROFILE]))[5]

    def power(self) -> tuple[int, bool]:
        """Battery charge as a percentage, and whether it is charging.

        ``GetPower`` in the stock tool, whose debug print
        ``GetPower: nPower=0x%x, bCharging=0x%x`` names both fields.  It
        discards a reading above 100 rather than clamping it, and so do we:
        the mouse answers this command even in states where the figure is
        not yet meaningful.
        """
        reply = self.command(bytes([CMD_GET_POWER]))
        percent, charging = reply[5], reply[6]
        if percent > 100:
            raise DeviceError(
                f"implausible battery reading {percent}", retryable=True
            )
        return percent, bool(charging)

    def read(self, addr: int, length: int) -> bytes:
        """Read `length` bytes of on-board configuration memory."""
        out = bytearray()
        while length > 0:
            n = min(MAX_PAYLOAD, length)
            reply = self.command(
                bytes([CMD_READ, 0, (addr >> 8) & 0xFF, addr & 0xFF, n]),
                match=self._addressed(addr, n),
            )
            out += reply[5 : 5 + n]
            addr += n
            length -= n
            time.sleep(0.004)
        return bytes(out)

    def write(self, addr: int, data: bytes) -> None:
        """Write configuration memory. Data must already carry its checksums."""
        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + MAX_PAYLOAD]
            self.command(
                bytes([CMD_WRITE, 0, (addr >> 8) & 0xFF, addr & 0xFF, len(chunk)])
                + chunk,
                match=self._addressed(addr, len(chunk)),
            )
            addr += len(chunk)
            offset += len(chunk)
            time.sleep(0.005)
