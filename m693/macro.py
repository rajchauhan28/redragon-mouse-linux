"""Macros: the on-board record format, and the Windows tool's ``.jmm`` files.

Recovered from ``OemDrv.exe``:

``StMacro_To_HdMacro`` (0x410960)
    editor macro -> the bytes stored in the mouse
``HdMacro_To_StMacro`` (0x4106c0)
    the inverse, used when reading a profile back
the caller at 0x410d90
    where the record is placed, and how its checksum is built

Storage is **per button slot**, not a free list: record *n* belongs to key slot
*n*, at ``0x300 + n * 0x180``.  The stock tool keeps its macro library on disk
and copies the chosen one into the slot of whichever button you bind it to;
this module does the same.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .device import Device, checksum
from .keys import (
    CONSUMER_USAGES,
    KEYBOARD_USAGES,
    MODIFIER_LABELS,
    MOUSE_BUTTONS,
    USAGE_KEYS,
)

ADDR_MACROS = 0x300
MACRO_STRIDE = 0x180
MACRO_SLOTS = 16
MACRO_REGION_LEN = MACRO_STRIDE * MACRO_SLOTS  # 0x300 .. 0x1B00

NAME_OFFSET = 0x01
NAME_MAX_BYTES = 0x1E  # 15 UTF-16 characters
COUNT_OFFSET = 0x1F
EVENTS_OFFSET = 0x20
EVENT_LEN = 5
MAX_EVENTS = 70  # the encoder's own ceiling (`cmp edx, 0x46`)
MAX_DELAY_MS = 0xFFFF

# Event kinds, in the low three bits of the flag byte.
KIND_MODIFIER = 0
KIND_KEY = 1
KIND_MOUSE = 4
KIND_CONSUMER = 5

PRESS, RELEASE = 0x80, 0x40

# How often a macro repeats is not stored with the macro - it lives in the
# button's keymap entry (keyinfo_to_hardware_code, 0x4102a9), which is
# (0x06, slot, repeat):
#
#   repeat = 1..0xFD   play that many times   (verified on hardware: 9 gave
#                                              nine repetitions, 1 gave one)
#   repeat = 0xFF      repeat while held
#   repeat = 0xFE      toggle: start on press, stop on the next press
#
# The stock tool's three "cycle" radio buttons are exactly these.
REPEAT_MAX = 0xFD
REPEAT_WHILE_HELD = 0xFF
REPEAT_TOGGLE = 0xFE
REPEAT_LABELS = {
    REPEAT_WHILE_HELD: "repeat while held",
    REPEAT_TOGGLE: "toggle on/off",
}


def repeat_label(repeat: int) -> str:
    if repeat in REPEAT_LABELS:
        return REPEAT_LABELS[repeat]
    return "once" if repeat == 1 else f"{repeat}x"


@dataclass(frozen=True)
class Event:
    """One step of a macro.

    ``delay_ms`` is the pause *after* the event.  Consumer events have no
    press/release halves - the firmware treats them as a single tap - which is
    why ``press`` is meaningless for them and always reads back True.
    """

    kind: int
    code: int
    press: bool = True
    delay_ms: int = 0

    def encode(self) -> bytes:
        delay = max(0, min(MAX_DELAY_MS, int(self.delay_ms)))
        if self.kind == KIND_CONSUMER:
            flag = KIND_CONSUMER
            low, high = self.code & 0xFF, (self.code >> 8) & 0xFF
        else:
            flag = (PRESS if self.press else RELEASE) | self.kind
            low, high = self.code & 0xFF, 0
        return bytes([flag, low, high, (delay >> 8) & 0xFF, delay & 0xFF])

    @classmethod
    def decode(cls, raw: bytes) -> "Event":
        flag, low, high, delay_hi, delay_lo = raw[:EVENT_LEN]
        kind = flag & 0x07
        code = low | (high << 8) if kind == KIND_CONSUMER else low
        return cls(kind, code, bool(flag & PRESS) or kind == KIND_CONSUMER,
                   (delay_hi << 8) | delay_lo)

    def label(self) -> str:
        if self.kind == KIND_CONSUMER:
            name = _CONSUMER_NAMES.get(self.code, f"usage {self.code:#05x}")
            return f"{name} (media)"
        action = "press" if self.press else "release"
        if self.kind == KIND_MODIFIER:
            what = MODIFIER_LABELS.get(self.code, f"modifier {self.code:#04x}")
        elif self.kind == KIND_MOUSE:
            what = MOUSE_BUTTONS.get(self.code, f"button {self.code:#04x}")
            what = f"{what} button"
        elif self.kind == KIND_KEY:
            what = USAGE_KEYS.get(self.code, f"usage {self.code:#04x}").upper()
        else:
            return f"kind {self.kind} code {self.code:#04x} {action}"
        return f"{what} {action}"


_CONSUMER_NAMES = {usage: name for name, usage in CONSUMER_USAGES.items()}


@dataclass
class Macro:
    name: str = ""
    events: list[Event] = None

    def __post_init__(self):
        if self.events is None:
            self.events = []

    @property
    def is_empty(self) -> bool:
        return not self.events

    def duration_ms(self) -> int:
        return sum(event.delay_ms for event in self.events)

    def encoded_name(self) -> bytes:
        raw = self.name.encode("utf-16-le")[:NAME_MAX_BYTES]
        if len(raw) % 2:  # never split a surrogate pair in half
            raw = raw[:-1]
        return raw

    # -- the on-board record ----------------------------------------------
    def to_record(self) -> bytes:
        """Build the bytes written to ``0x300 + slot * 0x180``.

        Note where the checksum comes from. The stock tool computes it over a
        buffer whose name area is still zero, and only *then* writes the name
        in, so the stored checksum does not cover the name. That looks like a
        bug in their encoder, but it is what the firmware has always been fed,
        so it is what we reproduce - a "corrected" checksum is an untested one.
        """
        if len(self.events) > MAX_EVENTS:
            raise ValueError(
                f"{len(self.events)} events is more than the {MAX_EVENTS} a slot holds"
            )
        body = bytearray(EVENTS_OFFSET)
        body[COUNT_OFFSET] = len(self.events)
        for event in self.events:
            body += event.encode()
        record = bytearray(body)
        record.append(checksum(body))
        record += b"\x00\x00\x00"
        name = self.encoded_name()
        record[0] = len(name)
        record[NAME_OFFSET : NAME_OFFSET + len(name)] = name
        if len(record) > MACRO_STRIDE:
            raise ValueError("macro does not fit its slot")
        return bytes(record)

    @classmethod
    def from_record(cls, record: bytes) -> "Macro":
        if len(record) < EVENTS_OFFSET:
            return cls()
        length = min(record[0], NAME_MAX_BYTES) & ~1
        name = record[NAME_OFFSET : NAME_OFFSET + length].decode(
            "utf-16-le", "replace"
        )
        count = record[COUNT_OFFSET]
        if count > MAX_EVENTS:
            return cls(name)  # unwritten slot (0xFF fill) or foreign data
        events = []
        for i in range(count):
            base = EVENTS_OFFSET + EVENT_LEN * i
            chunk = record[base : base + EVENT_LEN]
            if len(chunk) < EVENT_LEN:
                break
            events.append(Event.decode(chunk))
        return cls(name.rstrip("\x00"), events)

    # -- interchange -------------------------------------------------------
    def to_json(self) -> dict:
        return {
            "name": self.name,
            "events": [
                {
                    "kind": event.kind,
                    "code": event.code,
                    "press": event.press,
                    "delay_ms": event.delay_ms,
                    "label": event.label(),  # for humans; ignored on the way back
                }
                for event in self.events
            ],
        }

    @classmethod
    def from_json(cls, document: dict) -> "Macro":
        events = [
            Event(
                int(item["kind"]),
                int(item["code"]),
                bool(item.get("press", True)),
                int(item.get("delay_ms", 0)),
            )
            for item in document.get("events", [])
        ]
        return cls(str(document.get("name", "")), events)


# ------------------------------------------------------------------- helpers
def key_event(name: str, press: bool = True, delay_ms: int = 0) -> Event:
    """A keyboard event by key name, e.g. ``key_event("a")``."""
    if name not in KEYBOARD_USAGES:
        raise ValueError(f"unknown key {name!r}")
    return Event(KIND_KEY, KEYBOARD_USAGES[name], press, delay_ms)


def tap(name: str, hold_ms: int = 20, gap_ms: int = 30) -> list[Event]:
    return [key_event(name, True, hold_ms), key_event(name, False, gap_ms)]


def type_text(text: str, hold_ms: int = 15, gap_ms: int = 25) -> list[Event]:
    """Best-effort ASCII typing; unshifted characters only."""
    events: list[Event] = []
    for character in text.lower():
        name = {" ": "space", "\n": "enter", "\t": "tab"}.get(character, character)
        if name in KEYBOARD_USAGES:
            events += tap(name, hold_ms, gap_ms)
    return events


# -------------------------------------------------------------------- device
def read_record(dev: Device, slot: int) -> bytes:
    """Read a slot's record, reading only as far as its event count says.

    The stock tool reads all 353 usable bytes of every slot at startup. Over a
    lossy 2.4 GHz link that is ten bytes per command, so a full sweep of the
    macro region is some six hundred round-trips - most of them for bytes that
    are padding. Reading the 32-byte header first and then exactly the events
    it declares turns a typical slot into a handful of commands.
    """
    base = ADDR_MACROS + MACRO_STRIDE * slot
    header = dev.read(base, EVENTS_OFFSET)
    count = header[COUNT_OFFSET]
    if count == 0 or count > MAX_EVENTS:
        return bytes(header)  # empty, or never written (0xFF fill)
    return bytes(header) + dev.read(base + EVENTS_OFFSET, EVENT_LEN * count + 1)


def read_macro(dev: Device, slot: int) -> Macro:
    return Macro.from_record(read_record(dev, slot))


def clear_macro(dev: Device, slot: int) -> None:
    """Blank a slot: an event count of zero is all the firmware looks at."""
    write_macro(dev, slot, Macro())


def write_macro(dev: Device, slot: int, macro: Macro) -> None:
    dev.write(ADDR_MACROS + MACRO_STRIDE * slot, macro.to_record())


# ---------------------------------------------------------------- .jmm files
JMM_MAGIC = 0xFACB99A0
JMM_SIZE = 0x342
JMM_NAME_OFFSET = 0x14
JMM_NAME_BYTES = 0x54  # 0x14 .. 0x68
JMM_EVENTS_OFFSET = 0x6A
JMM_EVENT_LEN = 8

# Windows virtual-key codes the tool stores, and what they mean to us.  Only
# the ones that are not plain keyboard usages need translating; the rest go
# through the same VK->HID table the firmware uses (0x5b2e98).
_VK_MODIFIERS = {
    0x10: 0x02, 0xA0: 0x02,  # Shift / LShift  -> LShift
    0x11: 0x01, 0xA2: 0x01,  # Control / LCtrl -> LCtrl
    0x12: 0x04, 0xA4: 0x04,  # Alt / LAlt      -> LAlt
    0x5B: 0x08, 0x5C: 0x08,  # LWin / RWin     -> LGui
    0xA1: 0x20, 0xA3: 0x10, 0xA5: 0x40,  # RShift / RCtrl / RAlt
}
_VK_MOUSE = {0xF0: 0x01, 0xF1: 0x02, 0xF2: 0x04, 0xF3: 0x10, 0xF4: 0x08}
_VK_KEYS = {
    0x1B: "esc", 0x09: "tab", 0x14: "capslock", 0x0D: "enter",
    0x08: "backspace", 0x20: "space", 0xDB: "leftbracket",
    0xDD: "rightbracket", 0xDC: "backslash", 0xBA: "semicolon",
    0xDE: "apostrophe", 0xBC: "comma", 0xBE: "period", 0xBF: "slash",
    0xC0: "grave", 0xBB: "equal", 0xBD: "minus", 0x2C: "printscreen",
    0x91: "scrolllock", 0x13: "pause", 0x2D: "insert", 0x24: "home",
    0x21: "pageup", 0x2E: "delete", 0x23: "end", 0x22: "pagedown",
    0x26: "up", 0x28: "down", 0x25: "left", 0x27: "right", 0x90: "numlock",
    0x5D: "menu",
}
for _vk in range(0x41, 0x5B):
    _VK_KEYS[_vk] = chr(_vk).lower()
for _vk in range(0x30, 0x3A):
    _VK_KEYS[_vk] = chr(_vk)
for _n in range(1, 13):
    _VK_KEYS[0x6F + _n] = f"f{_n}"
for _n, _name in ((0x60, "num0"), (0x61, "num1"), (0x62, "num2"), (0x63, "num3"),
                  (0x64, "num4"), (0x65, "num5"), (0x66, "num6"), (0x67, "num7"),
                  (0x68, "num8"), (0x69, "num9"), (0x6A, "num*"), (0x6B, "num+"),
                  (0x6D, "num-"), (0x6E, "num."), (0x6F, "num/")):
    _VK_KEYS[_n] = _name


class JmmError(ValueError):
    """The file is not a macro the Windows tool wrote."""


def from_jmm(data: bytes) -> Macro:
    """Parse a ``.jmm`` file exported by the Windows tool.

    The file is a straight dump of the tool's in-memory macro struct: a magic
    dword, a wide name at 0x14, and a zero-terminated list of 8-byte events
    from 0x6A.
    """
    if len(data) < JMM_EVENTS_OFFSET:
        raise JmmError("file is too short to be a macro")
    (magic,) = struct.unpack_from("<I", data, 0)
    if magic != JMM_MAGIC:
        raise JmmError(
            f"bad magic {magic:#010x} - this is not a valid macro file"
        )
    raw_name = data[JMM_NAME_OFFSET : JMM_NAME_OFFSET + JMM_NAME_BYTES]
    name = raw_name.decode("utf-16-le", "replace").split("\x00")[0]

    events: list[Event] = []
    offset = JMM_EVENTS_OFFSET
    while offset + JMM_EVENT_LEN <= len(data) and len(events) < MAX_EVENTS:
        code, flags, _pad, delay = struct.unpack_from("<HBBI", data, offset)
        offset += JMM_EVENT_LEN
        if code == 0 and delay == 0:
            break  # the list is zero-terminated
        delay = min(delay, MAX_DELAY_MS)
        if flags & 0x02:  # multimedia: the code is already a consumer usage
            events.append(Event(KIND_CONSUMER, code, True, delay))
            continue
        press = bool(flags & 0x01)
        if code in _VK_MOUSE:
            events.append(Event(KIND_MOUSE, _VK_MOUSE[code], press, delay))
        elif code in _VK_MODIFIERS:
            events.append(Event(KIND_MODIFIER, _VK_MODIFIERS[code], press, delay))
        elif code in _VK_KEYS:
            events.append(
                Event(KIND_KEY, KEYBOARD_USAGES[_VK_KEYS[code]], press, delay)
            )
        else:
            raise JmmError(
                f"this macro contains a key we cannot map (virtual key {code:#04x})"
            )
    return Macro(name, events)
