"""Button functions: the catalogue, the encodings, and the combo-key region.

Recovered from ``OemDrv.exe``.  Two functions carry the whole mapping:

``keyinfo_to_hardware_code`` (0x410190)
    UI selection -> the 3-byte keymap entry at 0x60 + 4*slot.
``hardware_code_to_keyinfo`` (0x40ef70)
    the inverse, used when the tool reads a profile back.

They agree with each other, which is why the tables below can be trusted even
though only a handful of the bindings have been exercised on hardware.

Anything the mouse cannot express as a single keymap entry - a keyboard
shortcut, a multimedia key - is stored as keymap type 0x05 plus a *combo
record* in a second memory region at 0x100; see :func:`encode_combo`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .device import Device, checksum

# ------------------------------------------------------------- keymap types
KEY_TYPE_DISABLED = 0x00
KEY_TYPE_MOUSE = 0x01  # p1 = HID button mask
KEY_TYPE_DPI = 0x02  # p1 = 1 loop, 2 up, 3 down
KEY_TYPE_FIRE = 0x04  # p1 = interval ms, p2 = shot count
KEY_TYPE_COMBO = 0x05  # payload lives in the combo region at 0x100
KEY_TYPE_MACRO = 0x06  # p1 = macro slot, p2 = repeat count / mode
KEY_TYPE_REPORT_SW = 0x07  # cycle the polling rate
KEY_TYPE_RGB_TOGGLE = 0x08  # lighting on/off
KEY_TYPE_PROFILE_SW = 0x09  # cycle the on-board profile
KEY_TYPE_DPI_LOCK = 0x0A  # p1 = DPI stage index, held ("sniper")

MOUSE_BUTTONS = {
    0x01: "left",
    0x02: "right",
    0x04: "middle",
    0x08: "back",
    0x10: "forward",
}
MOUSE_BUTTON_NAMES = {name: mask for mask, name in MOUSE_BUTTONS.items()}

# Note the ordering: the stock UI's "DPI +" is p1=2, not p1=1.  p1=1 is the
# loop.  Both directions of the conversion in the binary agree on this.
DPI_ACTIONS = {1: "dpi-loop", 2: "dpi-up", 3: "dpi-down"}
DPI_ACTION_NAMES = {name: value for value, name in DPI_ACTIONS.items()}

CLICK_INTERVAL_MS = 0x32  # what the stock tool uses for double/triple click

# ------------------------------------------------------------- combo region
ADDR_COMBO = 0x100
COMBO_STRIDE = 0x20  # 32 bytes reserved per key slot
COMBO_SLOTS = 16
COMBO_REGION_LEN = COMBO_STRIDE * COMBO_SLOTS  # 0x100 .. 0x300
# 1 count byte + N*3 event bytes + 1 checksum has to fit the 32-byte slot.
COMBO_MAX_EVENTS = (COMBO_STRIDE - 2) // 3  # 10

# Event kinds, low three bits of the flag byte; 0x80 = press, 0x40 = release.
EV_MODIFIER = 0
EV_KEY = 1
EV_CONSUMER = 2

MOD_CTRL, MOD_SHIFT, MOD_ALT, MOD_SUPER = 0x01, 0x02, 0x04, 0x08
MODIFIER_NAMES = {
    MOD_CTRL: "ctrl",
    MOD_SHIFT: "shift",
    MOD_ALT: "alt",
    MOD_SUPER: "super",
}
MODIFIER_LABELS = {
    MOD_CTRL: "Ctrl",
    MOD_SHIFT: "Shift",
    MOD_ALT: "Alt",
    MOD_SUPER: "Win",
}
MODIFIER_VALUES = {name: bit for bit, name in MODIFIER_NAMES.items()}

# HID keyboard usages the mouse's firmware accepts, from the VK->usage table
# at 0x5b2e98.  Names are ours; the usages are the standard page 0x07 codes.
KEYBOARD_USAGES: dict[str, int] = {}
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    KEYBOARD_USAGES[_c] = 0x04 + _i
for _i, _c in enumerate("1234567890"):
    KEYBOARD_USAGES[_c] = 0x1E + _i
KEYBOARD_USAGES.update(
    {
        "enter": 0x28, "esc": 0x29, "backspace": 0x2A, "tab": 0x2B, "space": 0x2C,
        "minus": 0x2D, "equal": 0x2E, "leftbracket": 0x2F, "rightbracket": 0x30,
        "backslash": 0x31, "semicolon": 0x33, "apostrophe": 0x34, "grave": 0x35,
        "comma": 0x36, "period": 0x37, "slash": 0x38, "capslock": 0x39,
        "printscreen": 0x46, "scrolllock": 0x47, "pause": 0x48, "insert": 0x49,
        "home": 0x4A, "pageup": 0x4B, "delete": 0x4C, "end": 0x4D,
        "pagedown": 0x4E, "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52,
        "numlock": 0x53, "menu": 0x65,
    }
)
for _i in range(1, 13):
    KEYBOARD_USAGES[f"f{_i}"] = 0x39 + _i
for _n, _u in ((1, 0x59), (2, 0x5A), (3, 0x5B), (4, 0x5C), (5, 0x5D), (6, 0x5E),
               (7, 0x5F), (8, 0x60), (9, 0x61), (0, 0x62)):
    KEYBOARD_USAGES[f"num{_n}"] = _u
KEYBOARD_USAGES.update(
    {"num/": 0x54, "num*": 0x55, "num-": 0x56, "num+": 0x57,
     "numenter": 0x58, "num.": 0x63}
)
USAGE_KEYS = {usage: name for name, usage in KEYBOARD_USAGES.items()}

# HID consumer-page usages, from the code->usage switch at 0x410340.
CONSUMER_USAGES = {
    "media-player": 0x183,
    "play-pause": 0x0CD,
    "stop": 0x0B7,
    "previous": 0x0B6,
    "next": 0x0B5,
    "volume-up": 0x0E9,
    "volume-down": 0x0EA,
    "mute": 0x0E2,
    "email": 0x18A,
    "calculator": 0x192,
    "explorer": 0x194,
    "web-search": 0x221,
    "web-home": 0x223,
    "web-back": 0x224,
    "web-forward": 0x225,
    "web-stop": 0x226,
    "web-refresh": 0x227,
    "web-favorites": 0x22A,
}


# ------------------------------------------------------------------ bindings
@dataclass(frozen=True)
class Binding:
    """What one button slot holds.

    ``combo`` is the record for the 0x100 region.  ``None`` means this binding
    does not use one, and the existing record is left untouched - the mouse
    ignores it unless the keymap entry says type 0x05.
    """

    ktype: int
    p1: int = 0
    p2: int = 0
    combo: bytes | None = None

    @property
    def entry(self) -> tuple[int, int, int]:
        return (self.ktype, self.p1, self.p2)


@dataclass(frozen=True)
class Function:
    """One selectable entry in the button menu."""

    id: str
    label: str
    group: str
    binding: Binding | None = None  # None => needs a parameter, see build()
    param: str = ""  # "", "dpi-stage", "macro", "fire", "keys"


def _consumer(name: str) -> Binding:
    return Binding(KEY_TYPE_COMBO, combo=encode_combo(0, [], CONSUMER_USAGES[name]))


def encode_combo(
    modifiers: int, usages: list[int], consumer: int | None = None
) -> bytes:
    """Build a combo record: press events, then release events, then a checksum.

    Byte 0 is the event count; each event is ``flag, code, arg``.  The whole
    record sums to 0x55 like every other stored group.  Release order matches
    the stock tool's: modifiers first, then keys in reverse.
    """
    events: list[tuple[int, int, int]] = []
    if consumer is not None:
        events.append((0x80 | EV_CONSUMER, consumer & 0xFF, (consumer >> 8) & 0xFF))
        events.append((0x40 | EV_CONSUMER, consumer & 0xFF, (consumer >> 8) & 0xFF))
    else:
        bits = [b for b in (MOD_CTRL, MOD_SHIFT, MOD_ALT, MOD_SUPER) if modifiers & b]
        events += [(0x80 | EV_MODIFIER, bit, 0) for bit in bits]
        events += [(0x80 | EV_KEY, usage, 0) for usage in usages]
        events += [(0x40 | EV_MODIFIER, bit, 0) for bit in bits]
        events += [(0x40 | EV_KEY, usage, 0) for usage in reversed(usages)]
    if not events:
        raise ValueError("a key combination needs at least one key")
    if len(events) > COMBO_MAX_EVENTS:
        raise ValueError(
            f"{len(events)} events is more than the {COMBO_MAX_EVENTS} a slot holds"
        )
    body = bytearray([len(events)])
    for flag, code, arg in events:
        body += bytes([flag, code, arg])
    return bytes(body) + bytes([checksum(body)])


def decode_combo(record: bytes) -> tuple[int, list[int], int | None]:
    """Inverse of :func:`encode_combo`: (modifier mask, usages, consumer)."""
    if not record or not record[0] or record[0] > COMBO_MAX_EVENTS:
        return (0, [], None)
    modifiers, usages, consumer = 0, [], None
    for i in range(record[0]):
        base = 1 + 3 * i
        if base + 2 >= len(record):
            break
        flag, code, arg = record[base : base + 3]
        if not flag & 0x80:  # only the press half describes the binding
            continue
        kind = flag & 0x07
        if kind == EV_MODIFIER:
            modifiers |= code
        elif kind == EV_KEY:
            usages.append(code)
        elif kind == EV_CONSUMER:
            consumer = code | (arg << 8)
    return (modifiers, usages, consumer)


def combo_is_valid(record: bytes) -> bool:
    if not record or not record[0] or record[0] > COMBO_MAX_EVENTS:
        return False
    length = 2 + 3 * record[0]
    return len(record) >= length and sum(record[:length]) & 0xFF == 0x55


def combo_length(record: bytes) -> int:
    """Stored length of a record, including its checksum byte."""
    return 2 + 3 * (record[0] if record else 0)


# ------------------------------------------------------------------ catalogue
FUNCTIONS: list[Function] = [
    Function("left", "Left button", "Mouse", Binding(KEY_TYPE_MOUSE, 0x01)),
    Function("right", "Right button", "Mouse", Binding(KEY_TYPE_MOUSE, 0x02)),
    Function("middle", "Middle button", "Mouse", Binding(KEY_TYPE_MOUSE, 0x04)),
    Function("forward", "Forward", "Mouse", Binding(KEY_TYPE_MOUSE, 0x10)),
    Function("back", "Back", "Mouse", Binding(KEY_TYPE_MOUSE, 0x08)),
    Function(
        "double-click", "Double click", "Mouse",
        Binding(KEY_TYPE_FIRE, CLICK_INTERVAL_MS, 2),
    ),
    Function(
        "triple-click", "Three click", "Mouse",
        Binding(KEY_TYPE_FIRE, CLICK_INTERVAL_MS, 3),
    ),
    Function("fire", "Fire key", "Mouse", None, param="fire"),

    Function("dpi-up", "DPI +", "DPI", Binding(KEY_TYPE_DPI, 2)),
    Function("dpi-down", "DPI -", "DPI", Binding(KEY_TYPE_DPI, 3)),
    Function("dpi-loop", "DPI Loop", "DPI", Binding(KEY_TYPE_DPI, 1)),
    Function("dpi-lock", "DPI Lock", "DPI", None, param="dpi-stage"),

    Function("play-pause", "Play/Pause", "Multimedia", _consumer("play-pause")),
    Function("stop", "Stop play", "Multimedia", _consumer("stop")),
    Function("previous", "Previous", "Multimedia", _consumer("previous")),
    Function("next", "Next", "Multimedia", _consumer("next")),
    Function("volume-up", "Volume up", "Multimedia", _consumer("volume-up")),
    Function("volume-down", "Volume down", "Multimedia", _consumer("volume-down")),
    Function("mute", "Mute", "Multimedia", _consumer("mute")),
    Function("media-player", "Media Player", "Multimedia", _consumer("media-player")),

    Function("web-home", "Home page", "Web", _consumer("web-home")),
    Function("web-back", "Back (web)", "Web", _consumer("web-back")),
    Function("web-forward", "Forward (web)", "Web", _consumer("web-forward")),
    Function("web-refresh", "Refresh", "Web", _consumer("web-refresh")),
    Function("web-stop", "Stop web", "Web", _consumer("web-stop")),
    Function("web-search", "Search", "Web", _consumer("web-search")),
    Function("web-favorites", "Favorites", "Web", _consumer("web-favorites")),
    Function("email", "Email", "Web", _consumer("email")),
    Function("calculator", "Calculator", "Web", _consumer("calculator")),
    Function("explorer", "Explorer", "Web", _consumer("explorer")),

    Function("key-combo", "Key combination", "Keyboard", None, param="keys"),

    Function("rgb-toggle", "RGB ON/OFF", "Device", Binding(KEY_TYPE_RGB_TOGGLE)),
    Function(
        "polling-switch", "Polling rate switch", "Device",
        Binding(KEY_TYPE_REPORT_SW),
    ),
    Function("profile-switch", "Profile Switch", "Device", Binding(KEY_TYPE_PROFILE_SW)),

    Function("macro", "Macro", "Other", None, param="macro"),
    Function("disable", "Disable", "Other", Binding(KEY_TYPE_DISABLED)),
]

FUNCTIONS_BY_ID = {fn.id: fn for fn in FUNCTIONS}
GROUP_ORDER = ["Mouse", "DPI", "Multimedia", "Web", "Keyboard", "Device", "Other"]

_CONSUMER_TO_ID = {
    CONSUMER_USAGES[fn.id]: fn.id for fn in FUNCTIONS if fn.id in CONSUMER_USAGES
}


def build(function_id: str, **params) -> Binding:
    """Turn a catalogue id plus any parameters into a :class:`Binding`."""
    fn = FUNCTIONS_BY_ID.get(function_id)
    if fn is None:
        raise ValueError(f"unknown button function {function_id!r}")
    if fn.binding is not None:
        return fn.binding
    if fn.param == "fire":
        interval = int(params.get("interval", 10))
        shots = int(params.get("shots", 3))
        if not 1 <= interval <= 255 or not 1 <= shots <= 255:
            raise ValueError("fire interval and shot count must be 1-255")
        return Binding(KEY_TYPE_FIRE, interval, shots)
    if fn.param == "dpi-stage":
        stage = int(params.get("stage", 0))
        if not 0 <= stage <= 7:
            raise ValueError("DPI stage must be 0-7")
        return Binding(KEY_TYPE_DPI_LOCK, stage)
    if fn.param == "macro":
        slot = int(params.get("slot", 0))
        repeat = int(params.get("repeat", 1))
        if not 0 <= slot < COMBO_SLOTS:
            raise ValueError(f"macro slot must be 0-{COMBO_SLOTS - 1}")
        if not 1 <= repeat <= 0xFF:
            raise ValueError("macro repeat must be 1-253, or 254/255 for the modes")
        return Binding(KEY_TYPE_MACRO, slot, repeat)
    if fn.param == "keys":
        modifiers = params.get("modifiers", 0)
        if isinstance(modifiers, (list, tuple, set)):
            modifiers = sum(MODIFIER_VALUES[m] for m in modifiers)
        usages = [
            KEYBOARD_USAGES[k] if isinstance(k, str) else int(k)
            for k in params.get("keys", [])
        ]
        return Binding(KEY_TYPE_COMBO, combo=encode_combo(int(modifiers), usages))
    raise ValueError(f"{function_id!r} takes parameters that were not supplied")


def identify(entry: tuple[int, int, int], combo: bytes = b"") -> tuple[str, dict]:
    """Recognise a stored binding: (catalogue id, parameters).

    Returns ``("raw", ...)`` for anything the catalogue cannot name, so the
    caller can still show and preserve it.
    """
    ktype, p1, p2 = entry
    if ktype == KEY_TYPE_DISABLED:
        return ("disable", {})
    if ktype == KEY_TYPE_MOUSE and p1 in MOUSE_BUTTONS:
        return (MOUSE_BUTTONS[p1], {})
    if ktype == KEY_TYPE_DPI and p1 in DPI_ACTIONS:
        return (DPI_ACTIONS[p1], {})
    if ktype == KEY_TYPE_DPI_LOCK:
        return ("dpi-lock", {"stage": p1})
    if ktype == KEY_TYPE_FIRE:
        if p1 == CLICK_INTERVAL_MS and p2 == 2:
            return ("double-click", {})
        if p1 == CLICK_INTERVAL_MS and p2 == 3:
            return ("triple-click", {})
        return ("fire", {"interval": p1, "shots": p2})
    if ktype == KEY_TYPE_MACRO:
        return ("macro", {"slot": p1, "repeat": p2})
    if ktype == KEY_TYPE_REPORT_SW:
        return ("polling-switch", {})
    if ktype == KEY_TYPE_RGB_TOGGLE:
        return ("rgb-toggle", {})
    if ktype == KEY_TYPE_PROFILE_SW:
        return ("profile-switch", {})
    if ktype == KEY_TYPE_COMBO:
        modifiers, usages, consumer = decode_combo(combo)
        if consumer is not None:
            return (_CONSUMER_TO_ID.get(consumer, "raw"), {"consumer": consumer})
        if usages or modifiers:
            return ("key-combo", {"modifiers": modifiers, "keys": usages})
        return ("raw", {"entry": entry})
    return ("raw", {"entry": entry})


def describe(entry: tuple[int, int, int], combo: bytes = b"") -> str:
    """Human-readable summary of a stored binding."""
    fid, params = identify(entry, combo)
    if fid == "raw":
        ktype, p1, p2 = entry
        return f"type {ktype:#04x} p1={p1:#04x} p2={p2:#04x}"
    label = FUNCTIONS_BY_ID[fid].label
    if fid == "fire":
        return f"{label} ({params['interval']} ms, {params['shots']} shots)"
    if fid == "dpi-lock":
        return f"{label} (stage {params['stage'] + 1})"
    if fid == "macro":
        from .macro import repeat_label

        return f"{label} {params['slot'] + 1} ({repeat_label(params['repeat'])})"
    if fid == "key-combo":
        return f"{label}: {combo_label(params['modifiers'], params['keys'])}"
    return label


def combo_label(modifiers: int, usages: list[int]) -> str:
    parts = [
        MODIFIER_LABELS[bit]
        for bit in (MOD_CTRL, MOD_SHIFT, MOD_ALT, MOD_SUPER)
        if modifiers & bit
    ]
    parts += [USAGE_KEYS.get(u, f"0x{u:02x}").upper() for u in usages]
    return " + ".join(parts) if parts else "(nothing)"


# ------------------------------------------------------------------- device
def read_combo(dev: Device, slot: int) -> bytes:
    """Read one slot's combo record.

    The stock tool only reads the first 20 bytes of each 32-byte slot, so a
    combination longer than six events is one it writes but cannot read back.
    We read the whole slot.
    """
    return dev.read(ADDR_COMBO + COMBO_STRIDE * slot, COMBO_STRIDE)


def write_combo(dev: Device, slot: int, record: bytes) -> None:
    dev.write(ADDR_COMBO + COMBO_STRIDE * slot, record)
