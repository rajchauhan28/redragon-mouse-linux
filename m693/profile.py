"""On-board configuration memory map for the Redragon M693.

Every field in the low region is stored as a group of value bytes followed by
one checksum byte, where the whole group sums to 0x55.  Scalars are 1+1,
colours and DPI entries and key bindings are 3+1.

Addresses below were recovered from the debug strings and the profile decoder
in OemDrv.exe, then confirmed byte-for-byte against a real device whose
Cfg.ini-declared defaults were known.
"""

from __future__ import annotations

from . import keys
from .device import Device, checksum

# ---------------------------------------------------------------- addresses
ADDR_POLL_RATE = 0x00  # divisor: 1/2/4/8
ADDR_DPI_COUNT = 0x02  # number of active DPI stages (1..8)
ADDR_DPI_ACTIVE = 0x04  # active stage, 0-based
# Present and checksummed on every device seen, but the Windows tool never
# reads or writes either - it dumps 0x00-0x0B for debugging and uses only
# 0x00, 0x02, 0x04 and 0x0A out of it.  Both are zero here.
ADDR_UNUSED_06 = 0x06
ADDR_UNUSED_08 = 0x08
ADDR_LOD = 0x0A  # lift-off distance
ADDR_DPI_TABLE = 0x0C  # 8 x (x, y, mul, crc)
ADDR_DPI_COLORS = 0x2C  # 8 x (r, g, b, crc)
# The driver's own debug output labels 0x4C-0x53 "DPI Effect" - this is the
# lighting of the DPI indicator, which on this model is the scroll wheel.
# Confirmed by writing mode 4 here: the wheel goes dark while the strips stay
# lit.  Its colour is not stored here; it comes from the DPI colour table at
# 0x2C, which is what makes it an indicator.  See lighting.py.
ADDR_DPI_EFFECT = 0x4C  # (mode, crc) (brightness, crc) (speed, crc) (spare, crc)
ADDR_WHEEL_MODE = ADDR_DPI_EFFECT
ADDR_WHEEL_BRIGHTNESS = 0x4E
ADDR_WHEEL_SPEED = 0x50
ADDR_WHEEL_SPARE = 0x52
# Second lighting block, laid out exactly like the wheel block above and
# decoded by EEPROM_To_LogoRGB (0x410070), which is handed a pointer to 0x54
# and reads mode at +4, brightness at +6 and speed at +8.
#
# It does not own an LED on this model, but it is not inert either: the colour
# bytes at 0x54 are ignored, while the mode byte at 0x58 reaches the *wheel* -
# writing 2 there makes it breathe, and 0x4C = off blanks it regardless.  So it
# is a second knob on a channel that already has one, and nothing exposes it.
ADDR_LOGO_COLOR = 0x54  # (r, g, b, crc)
ADDR_LOGO_MODE = 0x58
ADDR_LOGO_BRIGHTNESS = 0x5A
ADDR_LOGO_SPEED = 0x5C
ADDR_LOGO_SPARE = 0x5E
ADDR_LED_COLOR = ADDR_LOGO_COLOR  # old name, kept for callers
ADDR_LED_LOGO = ADDR_LOGO_MODE
ADDR_KEYMAP = 0x60  # 16 x (type, p1, p2, crc)
# Main RGB zone (the side strips).  EEPROM_To_MainRGB reads this 7-byte group;
# confirmed on hardware - writing mode 2 with FF 00 00 gives solid red.
ADDR_LED_MAIN = 0xA0  # (mode, r, g, b, speed, brightness, crc)
ADDR_UNUSED_A7 = 0xA7  # zero here; the stock tool never touches it
ADDR_DEBOUNCE = 0xA9  # milliseconds
ADDR_MOTION_SYNC = 0xAB  # "Motion sync" in the Windows UI
ADDR_DORMANCY = 0xAD  # sleep timer, stored in units of 10
ADDR_ANGLE_SNAP = 0xAF  # "FixLine" in the driver's own debug output
ADDR_RIPPLE = 0xB1  # "Ripple control" in the Windows UI
ADDR_LIGHT_OFF_MOVE = 0xB3  # turn off light while moving
# Read and writable by the stock tool (defaults 1 and 6, a two-state and a
# value picked from a list) but unwritten on this model - both read 0xFF and
# form no valid group, so we leave them alone rather than guess.
ADDR_UNUSED_B5 = 0xB5
ADDR_UNUSED_B7 = 0xB7
# The stock tool reads 0x00-0xBD: 19 commands of 10 bytes, overshooting the
# 0xB5 bytes it actually asks for.
PROFILE_READ_LEN = 0xBE

LED_MODE_OFS, LED_R_OFS, LED_G_OFS, LED_B_OFS = 0, 1, 2, 3
LED_SPEED_OFS, LED_BRIGHT_OFS = 4, 5
LED_GROUP_LEN = 7

PROFILE_LEN = 0xC0
MAX_DPI_STAGES = 8
KEY_SLOTS = 16

# ------------------------------------------------------------- enumerations
POLL_RATES = {1: 1000, 2: 500, 4: 250, 8: 125}
POLL_DIVISORS = {hz: div for div, hz in POLL_RATES.items()}

# On-device numbering, recovered from the Windows UI's remap table at
# 0x4110cf (UI 1->2, 2->1, 3->0, 4->3, 5->5, 6->7, off->4) and confirmed on
# hardware.  The stock tool renumbers these for display; we write them raw.
LED_MODES = {
    0: "streaming",
    1: "breathing",
    2: "steady",
    3: "neon",
    4: "off",
    5: "single-color-flow",
    7: "colorful-breathing",
}
LED_MODE_NAMES = {name: value for value, name in LED_MODES.items()}

# Button function types and the selectable-function catalogue live in keys.py,
# which is where the two conversion routines in OemDrv.exe were decoded.
from .keys import (  # noqa: E402,F401  (re-exported for convenience)
    ADDR_COMBO,
    COMBO_REGION_LEN,
    COMBO_SLOTS,
    COMBO_STRIDE,
    DPI_ACTION_NAMES,
    DPI_ACTIONS,
    KEY_TYPE_COMBO,
    KEY_TYPE_DISABLED,
    KEY_TYPE_DPI,
    KEY_TYPE_DPI_LOCK,
    KEY_TYPE_FIRE,
    KEY_TYPE_MACRO,
    KEY_TYPE_MOUSE,
    KEY_TYPE_PROFILE_SW,
    KEY_TYPE_REPORT_SW,
    KEY_TYPE_RGB_TOGGLE,
    MOUSE_BUTTON_NAMES,
    MOUSE_BUTTONS,
    Binding,
)
from .keys import describe as describe_key  # noqa: E402,F401

# ------------------------------------------------------------- DPI encoding
# From Cfg.ini's DPISET/DPIHW pair.  Up to 4000 the sensor register is a direct
# lookup; above that the same register is reused with the doubling flag set.
_DPI_STEPS = list(range(500, 4001, 100)) + list(range(4200, 8001, 200))
_DPI_REGS = [
    0x0E, 0x10, 0x12, 0x15, 0x17, 0x1A, 0x1D, 0x1F, 0x22, 0x25, 0x27, 0x2A,
    0x2D, 0x2F, 0x32, 0x34, 0x37, 0x3A, 0x3C, 0x3F, 0x42, 0x44, 0x47, 0x4A,
    0x4C, 0x4F, 0x52, 0x54, 0x57, 0x59, 0x5C, 0x5F, 0x61, 0x64, 0x67, 0x69,
    0x37, 0x3A, 0x3C, 0x3F, 0x42, 0x44, 0x47, 0x4A, 0x4C, 0x4F, 0x52, 0x54,
    0x57, 0x59, 0x5C, 0x5F, 0x61, 0x64, 0x67, 0x69,
]
assert len(_DPI_STEPS) == len(_DPI_REGS)

DPI_STEPS = tuple(_DPI_STEPS)
DPI_MIN, DPI_MAX = _DPI_STEPS[0], _DPI_STEPS[-1]
MUL_FLAG = 0x11  # low nibble doubles X, high nibble doubles Y


def dpi_to_reg(dpi: int) -> tuple[int, int]:
    """Quantise a DPI value to (sensor register, multiplier byte)."""
    if not DPI_MIN <= dpi <= DPI_MAX:
        raise ValueError(f"DPI must be between {DPI_MIN} and {DPI_MAX}")
    index = min(range(len(_DPI_STEPS)), key=lambda i: abs(_DPI_STEPS[i] - dpi))
    return _DPI_REGS[index], (MUL_FLAG if _DPI_STEPS[index] > 4000 else 0)


def reg_to_dpi(reg: int, mul: int) -> int:
    """Inverse of :func:`dpi_to_reg`; returns the nominal DPI."""
    limit = 36  # entries at or below 4000 DPI
    table = _DPI_REGS[limit:] if mul else _DPI_REGS[:limit]
    steps = _DPI_STEPS[limit:] if mul else _DPI_STEPS[:limit]
    if reg in table:
        return steps[table.index(reg)]
    # Unknown register: fall back to the linear fit of the table.
    approx = round(reg * 4000 / 0x69 / 100) * 100
    return approx * 2 if mul else approx


# --------------------------------------------------------------- group map
# Configuration memory is a sequence of checksummed groups: 2-byte scalars and
# 4-byte records, each independently summing to 0x55.  That is the smallest
# unit that can be written without corrupting a checksum, so it is the unit the
# GUI tracks as dirty.  Regions we have not decoded are listed as opaque and
# are never written back.

GROUP_SCALAR = 2
GROUP_RECORD = 4


def _build_groups() -> list[tuple[int, int]]:
    groups: list[tuple[int, int]] = []
    for addr in range(0x00, 0x0C, 2):  # rate, stages, active, ?, ?, lod
        groups.append((addr, GROUP_SCALAR))
    for i in range(MAX_DPI_STAGES):  # DPI table
        groups.append((ADDR_DPI_TABLE + 4 * i, GROUP_RECORD))
    for i in range(MAX_DPI_STAGES):  # DPI colours
        groups.append((ADDR_DPI_COLORS + 4 * i, GROUP_RECORD))
    for addr in range(ADDR_DPI_EFFECT, ADDR_LED_COLOR, 2):
        groups.append((addr, GROUP_SCALAR))
    groups.append((ADDR_LED_COLOR, GROUP_RECORD))
    for addr in range(ADDR_LED_LOGO, ADDR_KEYMAP, 2):
        groups.append((addr, GROUP_SCALAR))
    for i in range(KEY_SLOTS):  # button map
        groups.append((ADDR_KEYMAP + 4 * i, GROUP_RECORD))
    groups.append((ADDR_LED_MAIN, LED_GROUP_LEN))
    groups.append((ADDR_UNUSED_A7, GROUP_SCALAR))
    for addr in range(ADDR_DEBOUNCE, 0xB5, 2):
        groups.append((addr, GROUP_SCALAR))
    return sorted(groups)


GROUPS = _build_groups()
GROUP_SIZES = dict(GROUPS)

# Readable, never written.  The stock tool does write 0xB5 and 0xB7 on models
# that have those features; on this one they read as 0xFF and form no valid
# group, so we leave them alone.
OPAQUE_RANGES = ((0xB5, PROFILE_LEN),)


def is_writable(addr: int) -> bool:
    return not any(lo <= addr < hi for lo, hi in OPAQUE_RANGES)


def merge_runs(addresses: set[int], limit: int = 10) -> list[tuple[int, int]]:
    """Coalesce dirty group addresses into (start, length) write runs.

    Adjacent groups are merged so long as the run stays within one command's
    payload, turning e.g. five changed DPI stages into two writes.
    """
    runs: list[tuple[int, int]] = []
    for addr in sorted(a for a in addresses if is_writable(a)):
        size = GROUP_SIZES.get(addr)
        if size is None:
            continue
        if runs:
            start, length = runs[-1]
            if start + length == addr and length + size <= limit:
                runs[-1] = (start, length + size)
                continue
        runs.append((addr, size))
    return runs


# --------------------------------------------------------------- primitives
def pack_scalar(value: int) -> bytes:
    return bytes([value & 0xFF, checksum([value & 0xFF])])


def pack_triple(a: int, b: int, c: int) -> bytes:
    body = bytes([a & 0xFF, b & 0xFF, c & 0xFF])
    return body + bytes([checksum(body)])


def group_ok(group: bytes) -> bool:
    return sum(group) & 0xFF == 0x55


# ------------------------------------------------------------------ profile
class Profile:
    """Decoded view of the mouse's configuration memory."""

    def __init__(self, raw: bytes):
        if len(raw) < PROFILE_LEN:
            raise ValueError("profile image too short")
        self.raw = bytes(raw)

    @classmethod
    def read_from(cls, dev: Device) -> "Profile":
        return cls(dev.read(0x00, PROFILE_LEN))

    def _scalar(self, addr: int) -> int:
        return self.raw[addr]

    # -- simple settings ---------------------------------------------------
    @property
    def polling_rate(self) -> int:
        return POLL_RATES.get(self._scalar(ADDR_POLL_RATE), 0)

    @property
    def lod(self) -> int:
        return self._scalar(ADDR_LOD)

    @property
    def debounce_ms(self) -> int:
        return self._scalar(ADDR_DEBOUNCE)

    @property
    def dormancy_s(self) -> int:
        return self._scalar(ADDR_DORMANCY) * 10

    @property
    def angle_snapping(self) -> bool:
        return bool(self._scalar(ADDR_ANGLE_SNAP))

    @property
    def motion_sync(self) -> bool:
        return bool(self._scalar(ADDR_MOTION_SYNC))

    @property
    def ripple_control(self) -> bool:
        return bool(self._scalar(ADDR_RIPPLE))

    @property
    def light_off_when_moving(self) -> bool:
        return bool(self._scalar(ADDR_LIGHT_OFF_MOVE))

    # -- DPI ---------------------------------------------------------------
    @property
    def dpi_count(self) -> int:
        return self._scalar(ADDR_DPI_COUNT)

    @property
    def dpi_active(self) -> int:
        return self._scalar(ADDR_DPI_ACTIVE)

    def dpi_stage(self, index: int) -> tuple[int, int]:
        """(x_dpi, y_dpi) for any of the 8 slots, populated or not."""
        base = ADDR_DPI_TABLE + 4 * index
        x, y, mul, _ = self.raw[base : base + 4]
        return reg_to_dpi(x, mul & 0x0F), reg_to_dpi(y, mul & 0xF0)

    def dpi_color(self, index: int) -> tuple[int, int, int]:
        base = ADDR_DPI_COLORS + 4 * index
        r, g, b, _ = self.raw[base : base + 4]
        return (r, g, b)

    @property
    def dpi_stages(self) -> list[tuple[int, int]]:
        """[(x_dpi, y_dpi)] for every populated stage."""
        return [self.dpi_stage(i) for i in range(self.dpi_count)]

    @property
    def dpi_colors(self) -> list[tuple[int, int, int]]:
        return [self.dpi_color(i) for i in range(self.dpi_count)]

    # -- lighting ----------------------------------------------------------
    @property
    def led_group(self) -> bytes:
        return self.raw[ADDR_LED_MAIN : ADDR_LED_MAIN + LED_GROUP_LEN]

    @property
    def led_mode(self) -> int:
        return self.raw[ADDR_LED_MAIN + LED_MODE_OFS]

    @property
    def led_color(self) -> tuple[int, int, int]:
        base = ADDR_LED_MAIN
        return (
            self.raw[base + LED_R_OFS],
            self.raw[base + LED_G_OFS],
            self.raw[base + LED_B_OFS],
        )

    @property
    def led_speed(self) -> int:
        return self.raw[ADDR_LED_MAIN + LED_SPEED_OFS]

    @property
    def led_brightness(self) -> int:
        return self.raw[ADDR_LED_MAIN + LED_BRIGHT_OFS]

    @property
    def logo_color(self) -> tuple[int, int, int]:
        r, g, b, _ = self.raw[ADDR_LOGO_COLOR : ADDR_LOGO_COLOR + 4]
        return (r, g, b)

    @property
    def logo_mode(self) -> int:
        return self._scalar(ADDR_LOGO_MODE)

    @property
    def logo_brightness(self) -> int:
        return self._scalar(ADDR_LOGO_BRIGHTNESS)

    @property
    def logo_speed(self) -> int:
        return self._scalar(ADDR_LOGO_SPEED)

    @property
    def dpi_effect_mode(self) -> int:
        return self._scalar(ADDR_DPI_EFFECT)

    @property
    def dpi_effect_brightness(self) -> int:
        return self._scalar(ADDR_DPI_EFFECT + 2)

    @property
    def dpi_effect_speed(self) -> int:
        return self._scalar(ADDR_DPI_EFFECT + 4)

    # The DPI-effect block is the wheel; these are the names the UI uses.
    wheel_mode = dpi_effect_mode
    wheel_brightness = dpi_effect_brightness
    wheel_speed = dpi_effect_speed

    # -- buttons -----------------------------------------------------------
    @property
    def keymap(self) -> list[tuple[int, int, int]]:
        keys = []
        for i in range(KEY_SLOTS):
            t, p1, p2, _ = self.raw[ADDR_KEYMAP + 4 * i : ADDR_KEYMAP + 4 * i + 4]
            keys.append((t, p1, p2))
        return keys

    # -- integrity ---------------------------------------------------------
    def bad_groups(self) -> list[int]:
        """Addresses of groups whose checksum does not validate."""
        return [
            addr
            for addr, size in GROUPS
            if sum(self.raw[addr : addr + size]) & 0xFF != 0x55
        ]


# ------------------------------------------------------------------ writers
def set_polling_rate(dev: Device, hz: int) -> None:
    if hz not in POLL_DIVISORS:
        raise ValueError(f"polling rate must be one of {sorted(POLL_DIVISORS)}")
    dev.write(ADDR_POLL_RATE, pack_scalar(POLL_DIVISORS[hz]))


def set_lod(dev: Device, value: int) -> None:
    dev.write(ADDR_LOD, pack_scalar(value))


def set_debounce(dev: Device, ms: int) -> None:
    if not 0 <= ms <= 30:
        raise ValueError("debounce must be 0-30 ms")
    dev.write(ADDR_DEBOUNCE, pack_scalar(ms))


def set_dormancy(dev: Device, seconds: int) -> None:
    dev.write(ADDR_DORMANCY, pack_scalar(round(seconds / 10)))


def set_flag(dev: Device, addr: int, on: bool) -> None:
    dev.write(addr, pack_scalar(1 if on else 0))


def set_dpi_stages(dev: Device, stages: list[int | tuple[int, int]]) -> None:
    """Replace the DPI stage table. Accepts ints or (x, y) pairs."""
    if not 1 <= len(stages) <= MAX_DPI_STAGES:
        raise ValueError(f"between 1 and {MAX_DPI_STAGES} DPI stages required")
    blob = bytearray()
    for stage in stages:
        x, y = stage if isinstance(stage, tuple) else (stage, stage)
        xr, xm = dpi_to_reg(x)
        yr, ym = dpi_to_reg(y)
        mul = (xm & 0x0F) | (ym & 0xF0)
        blob += pack_triple(xr, yr, mul)
    # Pad unused slots with a copy of the last stage, as the stock tool does.
    while len(blob) < 4 * MAX_DPI_STAGES:
        blob += blob[-4:]
    dev.write(ADDR_DPI_TABLE, bytes(blob))
    dev.write(ADDR_DPI_COUNT, pack_scalar(len(stages)))


def set_dpi_active(dev: Device, index: int) -> None:
    dev.write(ADDR_DPI_ACTIVE, pack_scalar(index))


def set_dpi_color(dev: Device, index: int, rgb: tuple[int, int, int]) -> None:
    if not 0 <= index < MAX_DPI_STAGES:
        raise ValueError("DPI stage index out of range")
    dev.write(ADDR_DPI_COLORS + 4 * index, pack_triple(*rgb))


def _set_led_fields(dev: Device, updates: dict[int, int]) -> None:
    """Read-modify-write the 7-byte main RGB group.

    Mode, colour, speed and brightness share a single checksum, so none of them
    can be written on its own: a 2-byte scalar write here would overwrite the
    neighbouring value with a checksum byte and leave the group invalid, which
    the device then ignores.
    """
    current = dev.read(ADDR_LED_MAIN, LED_GROUP_LEN)
    if sum(current) & 0xFF != 0x55:
        raise IOError(
            f"lighting block read back corrupt ({current.hex(' ')}); "
            "refusing to build on it - retry, or restore from a backup"
        )
    body = bytearray(current[:6])
    for offset, value in updates.items():
        body[offset] = value & 0xFF
    dev.write(ADDR_LED_MAIN, bytes(body) + bytes([checksum(body)]))


def set_led_mode(dev: Device, mode: int) -> None:
    _set_led_fields(dev, {LED_MODE_OFS: mode})


def set_led_brightness(dev: Device, value: int) -> None:
    _set_led_fields(dev, {LED_BRIGHT_OFS: value})


def set_led_speed(dev: Device, value: int) -> None:
    _set_led_fields(dev, {LED_SPEED_OFS: value})


def set_led_color(dev: Device, rgb: tuple[int, int, int]) -> None:
    r, g, b = rgb
    _set_led_fields(dev, {LED_R_OFS: r, LED_G_OFS: g, LED_B_OFS: b})


def set_led(
    dev: Device,
    mode: int,
    rgb: tuple[int, int, int],
    speed: int,
    brightness: int,
) -> None:
    """Write the whole main group at once.

    Setting a zone field by field costs one read-modify-write per field, since
    they share a checksum; a caller that knows all four values skips that.
    """
    r, g, b = rgb
    body = bytes([mode & 0xFF, r & 0xFF, g & 0xFF, b & 0xFF,
                  speed & 0xFF, brightness & 0xFF])
    dev.write(ADDR_LED_MAIN, body + bytes([checksum(body)]))


def set_wheel(dev: Device, mode: int, brightness: int, speed: int) -> None:
    """Write the wheel's effect block.  Its colour lives in the DPI table."""
    dev.write(
        ADDR_WHEEL_MODE,
        pack_scalar(mode) + pack_scalar(brightness) + pack_scalar(speed),
    )


def set_all_dpi_colors(dev: Device, rgb: tuple[int, int, int]) -> None:
    """Paint every DPI stage the same colour.

    This is how the wheel gets a colour of its own choosing, at the cost of the
    stage-by-stage colour coding - there is only the one table.
    """
    dev.write(ADDR_DPI_COLORS, pack_triple(*rgb) * MAX_DPI_STAGES)


def set_key(dev: Device, slot: int, ktype: int, p1: int = 0, p2: int = 0) -> None:
    if not 0 <= slot < KEY_SLOTS:
        raise ValueError("key slot out of range")
    dev.write(ADDR_KEYMAP + 4 * slot, pack_triple(ktype, p1, p2))


def set_binding(dev: Device, slot: int, binding: Binding) -> None:
    """Apply a binding, writing its combo record first if it has one.

    Order matters: the keymap entry is what makes the mouse look at the combo
    region, so the payload has to be in place before the entry points at it.
    """
    if binding.combo is not None:
        keys.write_combo(dev, slot, binding.combo)
    set_key(dev, slot, *binding.entry)
