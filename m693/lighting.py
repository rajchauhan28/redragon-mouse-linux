"""Per-zone lighting.

The M693 lights three places - the two side strips, the logo in the palm rest,
and the scroll wheel - but they are not three independently addressable zones.
Verified on hardware by driving each block and looking at the mouse:

* ``0xA0`` (the main RGB group) drives **the strips and the logo together**.
  Setting it to solid blue turns both blue; there is no way to split them.
* ``0x4C`` drives **the wheel**, with its own mode, brightness and speed.
  Mode 4 there blanks the wheel while the strips stay lit, so it really is a
  separate channel and not just a copy of the main group.

So there are two controllable zones, not three.  The wheel's *colour* is not
stored with its effect: it comes from the DPI stage colour table at ``0x2C``,
which is what makes the wheel a DPI indicator.  Recolouring the wheel therefore
means rewriting that table, and :func:`apply` says so by taking an explicit
decision about how many stages to touch.

``0x54``/``0x58`` was previously recorded as a dead second zone.  That was
wrong, and only looked true because the earlier test changed its *colour*: the
colour bytes are ignored, but the mode byte at ``0x58`` does reach the wheel -
writing 2 there makes it breathe.  It is gated off entirely by ``0x4C`` = off,
so it modulates the same LED rather than owning one.  Nothing in the UI exposes
it: it is a second knob on a channel that already has one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import profile as P
from .device import Device

ZONE_STRIPS = "strips"
ZONE_LOGO = "logo"
ZONE_WHEEL = "wheel"
ZONES = (ZONE_STRIPS, ZONE_LOGO, ZONE_WHEEL)

ZONE_MAIN = ZONE_STRIPS  # old name, kept for callers

# The strips and the logo are listed separately because that is how the mouse
# looks, but they are one channel: both entries read and write 0xA0, so editing
# either moves both.  This mapping is the only place that is decided, and every
# caller that writes deduplicates through it rather than issuing the same write
# twice.  Both front-ends say the two are linked - a picker that implied
# otherwise would just be a slower way of getting the same result.
BLOCK_MAIN, BLOCK_WHEEL = "main", "wheel"
ZONE_BLOCKS = {
    ZONE_STRIPS: BLOCK_MAIN,
    ZONE_LOGO: BLOCK_MAIN,
    ZONE_WHEEL: BLOCK_WHEEL,
}

ZONE_LABELS = {
    ZONE_STRIPS: "Side strips",
    ZONE_LOGO: "Palm logo",
    ZONE_WHEEL: "Scroll wheel",
}

_LINKED = (
    "Linked to the {other} on this model - one channel drives both, so this "
    "changes that too."
)
ZONE_NOTES = {
    ZONE_STRIPS: _LINKED.format(other="palm logo"),
    ZONE_LOGO: _LINKED.format(other="side strips"),
    ZONE_WHEEL: (
        "The wheel shows the active DPI stage's colour, so setting a colour "
        "here rewrites the DPI stage colours."
    ),
}


def linked_to(zone: str) -> tuple[str, ...]:
    """Other zones that move when this one does."""
    block = ZONE_BLOCKS[zone]
    return tuple(z for z in ZONES if z != zone and ZONE_BLOCKS[z] == block)


def group_label(zone: str) -> str:
    """Label naming every zone this one drives, for listings."""
    others = linked_to(zone)
    if not others:
        return ZONE_LABELS[zone]
    return ZONE_LABELS[zone] + " + " + " + ".join(
        ZONE_LABELS[other].lower() for other in others
    )


def distinct(zones) -> list[str]:
    """Drop zones that share a channel with one already in the list."""
    seen, kept = set(), []
    for zone in zones:
        block = ZONE_BLOCKS[zone]
        if block not in seen:
            seen.add(block)
            kept.append(zone)
    return kept


# The stored speed byte is a *delay*, not a rate: verified on hardware, an
# effect at 10 is visibly slower than the same effect at 2.  Everything
# user-facing counts the other way round, so the two are converted at the
# boundary rather than leaving a slider whose "Speed" label means the opposite
# of what it says.
#
# Cfg.ini's LEDParam caps the stock tool's slider at 10, but the firmware takes
# the whole byte and keeps slowing down smoothly all the way to 255 - checked
# by eye at 10, 60 and 255.  The stock range is the fast third of what the
# hardware can do, so the slider covers the lot.  The steps are geometric
# because that is how the change reads: 3 -> 4 is obvious, 200 -> 201 is not.
SPEED_MIN, SPEED_MAX = 1, 10  # 1 slowest, 10 fastest
RAW_SPEED_MIN, RAW_SPEED_MAX = 1, 255

_STEPS = SPEED_MAX - SPEED_MIN
RAW_SPEEDS = [
    max(RAW_SPEED_MIN, round(RAW_SPEED_MAX ** (1.0 - i / _STEPS)))
    for i in range(SPEED_MAX)
]  # [255, 139, 76, 42, 23, 12, 7, 4, 2, 1]


def speed_to_raw(display: int) -> int:
    """Displayed speed (1 slow .. 10 fast) to the stored delay byte."""
    return RAW_SPEEDS[max(SPEED_MIN, min(SPEED_MAX, int(display))) - SPEED_MIN]


def speed_from_raw(raw: int) -> int:
    """Nearest displayed speed for a stored delay byte.

    Any byte can turn up here - a profile written by the stock tool, or one of
    ours from before the slider covered the full range - so this rounds to the
    closest step rather than expecting an exact match.
    """
    raw = max(RAW_SPEED_MIN, min(RAW_SPEED_MAX, int(raw)))
    best = min(range(len(RAW_SPEEDS)), key=lambda i: abs(RAW_SPEEDS[i] - raw))
    return best + SPEED_MIN


@dataclass
class ZoneState:
    """What one zone should be showing.

    ``speed`` is the raw stored byte; run it through :func:`speed_from_raw` to get
    the number a person should see.
    """

    mode: int = 2  # steady
    color: tuple[int, int, int] = (255, 0, 0)
    brightness: int = 255
    speed: int = 3

    @property
    def mode_name(self) -> str:
        return P.LED_MODES.get(self.mode, "steady")

    def to_json(self) -> dict:
        return {
            "mode": self.mode_name,
            "color": "%02x%02x%02x" % tuple(self.color),
            "brightness": self.brightness,
            # Saved the way a person reads it, since this file is editable.
            "speed": speed_from_raw(self.speed),
        }

    @classmethod
    def from_json(cls, data: dict) -> "ZoneState":
        mode = data.get("mode", "steady")
        if isinstance(mode, str):
            if mode not in P.LED_MODE_NAMES:
                raise ValueError(f"unknown lighting mode {mode!r}")
            mode = P.LED_MODE_NAMES[mode]
        raw = str(data.get("color", "ff0000"))
        if len(raw) != 6:
            raise ValueError(f"colour must be RRGGBB, got {raw!r}")
        color = tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))
        return cls(
            mode=int(mode),
            color=color,  # type: ignore[arg-type]
            brightness=max(0, min(255, int(data.get("brightness", 255)))),
            speed=speed_to_raw(int(data.get("speed", 8))),
        )


def read(profile: P.Profile, zone: str) -> ZoneState:
    """Current state of one zone, decoded from a profile image."""
    block = ZONE_BLOCKS.get(zone)
    if block == BLOCK_MAIN:
        return ZoneState(
            mode=profile.led_mode,
            color=profile.led_color,
            brightness=profile.led_brightness,
            speed=profile.led_speed,
        )
    if block == BLOCK_WHEEL:
        stage = min(profile.dpi_active, P.MAX_DPI_STAGES - 1)
        return ZoneState(
            mode=profile.wheel_mode,
            color=profile.dpi_color(stage),
            brightness=profile.wheel_brightness,
            speed=profile.wheel_speed,
        )
    raise ValueError(f"unknown zone {zone!r}")


def apply(dev: Device, zone: str, state: ZoneState, *, color: bool = True) -> None:
    """Push a zone's state to the mouse.

    ``color=False`` leaves the colour alone, which is how the wheel is set
    without flattening a DPI stage table the user set up deliberately.
    """
    block = ZONE_BLOCKS.get(zone)
    if block == BLOCK_MAIN:
        P.set_led(dev, state.mode, state.color, state.speed, state.brightness)
        return
    if block == BLOCK_WHEEL:
        P.set_wheel(dev, state.mode, state.brightness, state.speed)
        if color:
            P.set_all_dpi_colors(dev, state.color)
        return
    raise ValueError(f"unknown zone {zone!r}")


# ------------------------------------------------------------------- presets
@dataclass
class Preset:
    """A named lighting look: one :class:`ZoneState` per zone.

    ``dpi_colors``, when present, is the full stage colour table as it was when
    the preset was captured.  Without it the wheel's single colour is painted
    onto every stage, which is what a hand-written or shipped preset means; with
    it, capturing and re-applying a preset gives back exactly the mouse you had,
    stage-by-stage colour coding included.
    """

    name: str
    zones: dict[str, ZoneState] = field(default_factory=dict)
    dpi_colors: list[tuple[int, int, int]] | None = None

    def to_json(self) -> dict:
        data = {
            "name": self.name,
            "zones": {z: s.to_json() for z, s in self.zones.items()},
        }
        if self.dpi_colors is not None:
            data["dpi_colors"] = ["%02x%02x%02x" % tuple(c) for c in self.dpi_colors]
        return data

    @classmethod
    def from_json(cls, data: dict) -> "Preset":
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("preset has no name")
        zones = {}
        for zone, state in (data.get("zones") or {}).items():
            if zone not in ZONES:
                continue  # a preset written by a newer version; ignore the rest
            zones[zone] = ZoneState.from_json(state)
        if not zones:
            raise ValueError(f"preset {name!r} sets no zone we understand")
        stage_colors = data.get("dpi_colors")
        if stage_colors is not None:
            stage_colors = [
                tuple(int(str(c)[i : i + 2], 16) for i in (0, 2, 4))
                for c in stage_colors
            ]
        return cls(name=name, zones=zones, dpi_colors=stage_colors)

    @classmethod
    def capture(cls, name: str, profile: P.Profile) -> "Preset":
        return cls(
            name=name,
            zones={z: read(profile, z) for z in ZONES},
            dpi_colors=[
                profile.dpi_color(i) for i in range(P.MAX_DPI_STAGES)
            ],
        )

    def apply(self, dev: Device, *, color: bool = True) -> None:
        # distinct() keeps the linked strips/logo pair from writing 0xA0 twice.
        for zone in distinct(self.zones):
            state = self.zones[zone]
            # The stage table is written separately below when we have one, so
            # the wheel's flat colour must not overwrite it first.
            flat = color and not (zone == ZONE_WHEEL and self.dpi_colors)
            apply(dev, zone, state, color=flat)
        if color and self.dpi_colors and ZONE_WHEEL in self.zones:
            for index, rgb in enumerate(self.dpi_colors[: P.MAX_DPI_STAGES]):
                P.set_dpi_color(dev, index, rgb)


def _state(mode: str, color: tuple[int, int, int], brightness=255, speed=8) -> ZoneState:
    """`speed` here is the displayed one - 10 is fastest."""
    return ZoneState(P.LED_MODE_NAMES[mode], color, brightness, speed_to_raw(speed))


# Shipped looks, so the preset list is not empty on a fresh install.  These are
# only combinations of the seven firmware effects: the mouse has no programmable
# effect engine, and driving one from the host would mean writing to its
# configuration flash many times a second.
BUILTIN_PRESETS = [
    Preset("Rainbow wave", {
        ZONE_MAIN: _state("streaming", (255, 0, 0), 255, 7),
        ZONE_WHEEL: _state("streaming", (255, 0, 0), 128, 7),
    }),
    Preset("Solid white", {
        ZONE_MAIN: _state("steady", (255, 255, 255), 255),
        ZONE_WHEEL: _state("steady", (255, 255, 255), 128),
    }),
    Preset("Ember", {
        ZONE_MAIN: _state("breathing", (255, 60, 0), 255, 2),
        ZONE_WHEEL: _state("steady", (255, 120, 0), 128),
    }),
    Preset("Ice", {
        ZONE_MAIN: _state("single-color-flow", (0, 140, 255), 255, 5),
        ZONE_WHEEL: _state("breathing", (0, 200, 255), 128, 4),
    }),
    Preset("Stealth", {
        ZONE_MAIN: _state("off", (0, 0, 0), 0),
        ZONE_WHEEL: _state("off", (0, 0, 0), 0),
    }),
]
