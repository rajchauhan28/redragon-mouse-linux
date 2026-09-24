"""Profile export and import.

The file is JSON so it can be read, diffed and hand-edited, but the raw memory
images ride along base64-encoded.  Import prefers the raw images - they are
exactly what the mouse held - and uses the readable fields only to report what
is being restored.  That way a profile written by a newer version, with fields
we have since decoded differently, still restores byte-for-byte.
"""

from __future__ import annotations

import base64

from . import keys as K
from . import profile as P

FORMAT = "m693-profile"
VERSION = 1


def _readable(profile: P.Profile, combo: bytes) -> dict:
    return {
        "polling_rate_hz": profile.polling_rate,
        "dpi_stages": [list(stage) for stage in profile.dpi_stages],
        "dpi_colors": ["%02x%02x%02x" % rgb for rgb in profile.dpi_colors],
        "dpi_active": profile.dpi_active,
        "lift_off_distance": profile.lod,
        "debounce_ms": profile.debounce_ms,
        "dormancy_s": profile.dormancy_s,
        "angle_snapping": profile.angle_snapping,
        "motion_sync": profile.motion_sync,
        "ripple_control": profile.ripple_control,
        "light_off_when_moving": profile.light_off_when_moving,
        "lighting": {
            "mode": P.LED_MODES.get(profile.led_mode, profile.led_mode),
            "color": "%02x%02x%02x" % profile.led_color,
            "brightness": profile.led_brightness,
            "speed": profile.led_speed,
        },
        "buttons": [
            {
                "slot": slot,
                "function": K.describe(
                    entry,
                    combo[K.COMBO_STRIDE * slot : K.COMBO_STRIDE * (slot + 1)],
                ),
            }
            for slot, entry in enumerate(profile.keymap)
        ],
    }


def to_json(image: bytes, combo: bytes = b"") -> dict:
    profile = P.Profile(image)
    combo = bytes(combo).ljust(K.COMBO_REGION_LEN, b"\x00")
    return {
        "format": FORMAT,
        "version": VERSION,
        "settings": _readable(profile, combo),
        "raw": {
            "profile": base64.b64encode(bytes(image[: P.PROFILE_LEN])).decode(),
            "combo": base64.b64encode(combo).decode(),
        },
    }


def from_json(document: dict) -> tuple[bytes, bytes]:
    """Return (profile image, combo image). Raises ValueError on junk."""
    if document.get("format") != FORMAT:
        raise ValueError("not an m693 profile")
    raw = document.get("raw") or {}
    try:
        image = base64.b64decode(raw["profile"], validate=True)
        combo = base64.b64decode(raw.get("combo", ""), validate=True)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"profile is missing its raw image ({exc})") from exc
    if len(image) < P.PROFILE_LEN:
        raise ValueError("profile image is truncated")
    bad = P.Profile(image).bad_groups()
    if bad:
        raise ValueError(
            "profile image is corrupt at "
            + ", ".join(f"{addr:#04x}" for addr in bad[:6])
        )
    return (
        bytes(image[: P.PROFILE_LEN]),
        bytes(combo).ljust(K.COMBO_REGION_LEN, b"\x00")[: K.COMBO_REGION_LEN],
    )


def read_device(dev) -> dict:
    return to_json(dev.read(0x00, P.PROFILE_LEN), dev.read(K.ADDR_COMBO, K.COMBO_REGION_LEN))


def write_device(dev, image: bytes, combo: bytes) -> None:
    """Restore both regions, combo records first (keymap entries point at them)."""
    for slot in range(K.COMBO_SLOTS):
        record = combo[K.COMBO_STRIDE * slot : K.COMBO_STRIDE * (slot + 1)]
        if K.combo_is_valid(record):
            K.write_combo(dev, slot, record[: K.combo_length(record)])
    for addr, size in P.GROUPS:
        if P.is_writable(addr):
            dev.write(addr, image[addr : addr + size])
