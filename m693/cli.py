"""Command line front-end for the Redragon M693."""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import __version__
from . import keys as K
from . import lighting as L
from . import macro as MACRO
from . import presets as PRESETS
from . import profile as P
from . import transfer
from .device import Device, DeviceError, find_devices


def _color(text: str) -> tuple[int, int, int]:
    text = text.lstrip("#")
    if len(text) != 6:
        raise argparse.ArgumentTypeError("colour must be RRGGBB hex")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def _wait(text: str) -> float | None:
    """Seconds to keep retrying, or None for indefinitely."""
    if text.lower() in ("forever", "inf", "infinite"):
        return None
    try:
        seconds = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("--wait takes seconds or 'forever'")
    if seconds < 0:
        raise argparse.ArgumentTypeError("--wait cannot be negative")
    return seconds


def _onoff(text: str) -> bool:
    if text.lower() in ("on", "true", "1", "yes"):
        return True
    if text.lower() in ("off", "false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError("expected on/off")


# --------------------------------------------------------------- subcommands
def cmd_list(args) -> int:
    nodes = find_devices()
    if not nodes:
        print("No Redragon M693 found.")
        return 1
    for node in nodes:
        print(node)
    return 0


def _battery(dev: Device) -> tuple[int, bool]:
    """Charge, refusing a reading the receiver cannot vouch for.

    A receiver with no mouse linked still answers GetPower, with the last
    figure it saw - indistinguishable from a live one, and wrong.  A charge is
    only meaningful when the mouse itself is on the other end.
    """
    if not dev.online():
        raise DeviceError(
            "the mouse is not connected, so its charge would be a stale "
            "reading from the receiver",
            retryable=True,
        )
    return dev.power()


def _battery_text(dev: Device) -> str:
    """Battery as one line, or why it could not be read."""
    try:
        percent, charging = _battery(dev)
    except DeviceError as exc:
        return f"unavailable ({exc})"
    return f"{percent}%" + (" (charging)" if charging else "")


def cmd_battery(args, dev: Device) -> int:
    percent, charging = _battery(dev)
    if args.quiet:
        print(percent)
    else:
        print(f"{percent}%" + (" (charging)" if charging else ""))
    return 0


def cmd_info(args, dev: Device) -> int:
    prof = P.Profile.read_from(dev)
    print(f"device            {dev.path}")
    print(f"link              {'online' if dev.online() else 'offline'}")
    print(f"current profile   {dev.current_profile()}")
    print(f"battery           {_battery_text(dev)}")
    print(f"polling rate      {prof.polling_rate} Hz")
    print(f"lift-off distance {prof.lod}")
    print(f"debounce          {prof.debounce_ms} ms")
    print(f"dormancy          {prof.dormancy_s} s")
    print(f"angle snapping    {'on' if prof.angle_snapping else 'off'}")
    print(f"motion sync       {'on' if prof.motion_sync else 'off'}")
    print(f"ripple control    {'on' if prof.ripple_control else 'off'}")
    print(f"light off moving  {'on' if prof.light_off_when_moving else 'off'}")
    print()
    print(f"DPI stages        {prof.dpi_count} (active: stage {prof.dpi_active + 1})")
    for i, ((x, y), (r, g, b)) in enumerate(zip(prof.dpi_stages, prof.dpi_colors)):
        marker = "*" if i == prof.dpi_active else " "
        value = f"{x}" if x == y else f"{x}x{y}"
        print(f"  {marker} stage {i + 1}: {value:>9} DPI   #{r:02x}{g:02x}{b:02x}")
    print()
    for zone in L.distinct(L.ZONES):
        state = L.read(prof, zone)
        r, g, b = state.color
        print(f"{L.group_label(zone)}")
        print(f"  effect          {state.mode_name}")
        print(f"  colour          #{r:02x}{g:02x}{b:02x}")
        print(f"  brightness      {state.brightness}")
        print(f"  speed           {L.speed_from_raw(state.speed)}  (1 slow .. 10 fast)")
    print("  (the wheel's colour is the active DPI stage's)")
    print()
    print("buttons")
    combo = _read_combo(dev)
    for i, entry in enumerate(prof.keymap):
        if entry == (0, 0, 0) and i >= 11:
            continue
        record = combo[K.COMBO_STRIDE * i : K.COMBO_STRIDE * (i + 1)]
        print(f"  slot {i:2d}: {K.describe(entry, record)}")
    bad = prof.bad_groups()
    if bad:
        print()
        print("warning: checksum mismatch at " + ", ".join(f"{a:#04x}" for a in bad))
    return 0


def cmd_dump(args, dev: Device) -> int:
    length = args.length
    data = dev.read(args.addr, length)
    if args.output:
        with open(args.output, "wb") as fh:
            fh.write(data)
        print(f"wrote {len(data)} bytes to {args.output}")
        return 0
    for off in range(0, len(data), 16):
        row = data[off : off + 16]
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        print(f"{args.addr + off:04x}  {' '.join(f'{b:02x}' for b in row):<47}  {text}")
    return 0


def cmd_restore(args, dev: Device) -> int:
    with open(args.input, "rb") as fh:
        data = fh.read()
    dev.write(args.addr, data)
    print(f"restored {len(data)} bytes at {args.addr:#04x}")
    return 0


def cmd_rate(args, dev: Device) -> int:
    P.set_polling_rate(dev, args.hz)
    print(f"polling rate set to {args.hz} Hz")
    return 0


def cmd_dpi(args, dev: Device) -> int:
    if args.stages:
        stages = []
        for item in args.stages:
            if "x" in item:
                x, y = item.split("x", 1)
                stages.append((int(x), int(y)))
            else:
                stages.append(int(item))
        P.set_dpi_stages(dev, stages)
        print(f"set {len(stages)} DPI stages")
    if args.active is not None:
        P.set_dpi_active(dev, args.active - 1)
        print(f"active DPI stage set to {args.active}")
    if args.color:
        index, color = args.color
        P.set_dpi_color(dev, int(index) - 1, _color(color))
        print(f"stage {index} colour set to #{color.lstrip('#')}")
    if not (args.stages or args.active is not None or args.color):
        prof = P.Profile.read_from(dev)
        for i, (x, y) in enumerate(prof.dpi_stages):
            marker = "*" if i == prof.dpi_active else " "
            print(f"{marker} stage {i + 1}: {x if x == y else f'{x}x{y}'} DPI")
    return 0


def _zones(name: str) -> list[str]:
    """Zones a --zone value names, one entry per independent channel."""
    return L.distinct(L.ZONES if name == "all" else (name,))


def cmd_led(args, dev: Device) -> int:
    targets = _zones(args.zone)
    if args.mode:
        mode = P.LED_MODE_NAMES.get(args.mode)
        if mode is None:
            raise SystemExit(f"unknown LED mode; pick one of {sorted(P.LED_MODE_NAMES)}")
    else:
        mode = None
    if not any((mode is not None, args.brightness is not None,
                args.speed is not None, args.color)):
        prof = P.Profile.read_from(dev)
        for zone in targets:
            state = L.read(prof, zone)
            r, g, b = state.color
            print(f"{L.group_label(zone)}")
            print(f"  mode       {state.mode_name}")
            print(f"  brightness {state.brightness}")
            print(f"  speed      {L.speed_from_raw(state.speed)}")
            print(f"  colour     #{r:02x}{g:02x}{b:02x}")
        return 0

    # Build each zone's new state from what it currently holds, so a partial
    # command leaves the fields it did not name alone.
    prof = P.Profile.read_from(dev)
    for zone in targets:
        state = L.read(prof, zone)
        if mode is not None:
            state.mode = mode
        if args.brightness is not None:
            state.brightness = args.brightness
        if args.speed is not None:
            # The device stores a delay; --speed counts the way people do.
            state.speed = L.speed_to_raw(args.speed)
        if args.color:
            state.color = _color(args.color)
        L.apply(dev, zone, state, color=bool(args.color))
    if args.color and L.ZONE_WHEEL in targets:
        print(
            "note: the wheel's colour is the DPI stage colour table, so every "
            "stage is now that colour",
            file=sys.stderr,
        )
    return 0


def cmd_preset(args, dev: Device) -> int:
    if args.action != "list" and not args.name:
        raise SystemExit(f"preset {args.action} needs a name")
    if args.action == "list":
        for preset in PRESETS.load_all():
            tag = " (built-in)" if PRESETS.is_builtin(preset.name) else ""
            zones = ", ".join(
                f"{L.ZONE_LABELS[z]}: {preset.zones[z].mode_name}"
                for z in L.distinct(preset.zones)
            )
            print(f"{preset.name}{tag} - {zones}")
        return 0
    if args.action == "save":
        if PRESETS.is_builtin(args.name):
            raise SystemExit(f"{args.name!r} is a built-in preset name")
        PRESETS.store(L.Preset.capture(args.name, P.Profile.read_from(dev)))
        print(f"saved {args.name!r} to {PRESETS.PRESET_PATH}")
        return 0
    if args.action == "delete":
        if not PRESETS.remove(args.name):
            raise SystemExit(f"no saved preset called {args.name!r}")
        print(f"deleted {args.name!r}")
        return 0
    preset = PRESETS.find(args.name)
    if preset is None:
        raise SystemExit(f"no preset called {args.name!r} (try: m693ctl preset list)")
    preset.apply(dev, color=not args.keep_dpi_colors)
    print(f"applied {preset.name!r}")
    return 0


def cmd_set(args, dev: Device) -> int:
    if args.lod is not None:
        P.set_lod(dev, args.lod)
    if args.debounce is not None:
        P.set_debounce(dev, args.debounce)
    if args.dormancy is not None:
        P.set_dormancy(dev, args.dormancy)
    if args.angle_snap is not None:
        P.set_flag(dev, P.ADDR_ANGLE_SNAP, args.angle_snap)
    if args.motion_sync is not None:
        P.set_flag(dev, P.ADDR_MOTION_SYNC, args.motion_sync)
    if args.ripple is not None:
        P.set_flag(dev, P.ADDR_RIPPLE, args.ripple)
    if args.light_off_moving is not None:
        P.set_flag(dev, P.ADDR_LIGHT_OFF_MOVE, args.light_off_moving)
    print("applied")
    return 0


def _read_combo(dev: Device) -> bytes:
    try:
        return dev.read(K.ADDR_COMBO, K.COMBO_REGION_LEN)
    except DeviceError:
        return bytes(K.COMBO_REGION_LEN)


def _parse_action(action: str) -> K.Binding:
    """Turn a CLI action word into a binding.

    Accepts a catalogue id ("play-pause"), a parameterised form
    ("fire:10,3", "dpi-lock:2", "macro:0"), a key combination
    ("key:ctrl+shift+t"), or "raw:type,p1,p2" as an escape hatch.
    """
    if action.startswith("raw:"):
        parts = [int(x, 0) for x in action[4:].split(",")]
        return K.Binding(*(parts + [0, 0])[:3])
    if action.startswith("key:"):
        parts = action[4:].lower().split("+")
        modifiers = sum(K.MODIFIER_VALUES[p] for p in parts if p in K.MODIFIER_VALUES)
        names = [p for p in parts if p not in K.MODIFIER_VALUES]
        for name in names:
            if name not in K.KEYBOARD_USAGES:
                raise SystemExit(
                    f"unknown key {name!r}; try one of: "
                    + ", ".join(sorted(K.KEYBOARD_USAGES))
                )
        return K.build("key-combo", modifiers=modifiers, keys=names)
    name, _, argument = action.partition(":")
    if name not in K.FUNCTIONS_BY_ID:
        raise SystemExit(
            "unknown action; try one of:\n  "
            + "\n  ".join(
                f"{fn.id:<16} {fn.label}" for fn in K.FUNCTIONS
            )
            + "\n  key:ctrl+shift+t\n  raw:type,p1,p2"
        )
    fn = K.FUNCTIONS_BY_ID[name]
    if not fn.param:
        return K.build(name)
    values = [int(x, 0) for x in argument.split(",")] if argument else []
    if fn.param == "fire":
        interval, shots = (values + [10, 3])[:2]
        return K.build(name, interval=interval, shots=shots)
    if fn.param == "dpi-stage":
        return K.build(name, stage=(values + [0])[0])
    if fn.param == "macro":
        return K.build(name, slot=(values + [0])[0])
    raise SystemExit(f"{name} needs an argument, e.g. {name}:1")


def cmd_button(args, dev: Device) -> int:
    binding = _parse_action(args.action)
    P.set_binding(dev, args.slot, binding)
    record = binding.combo or b""
    print(f"slot {args.slot} set to {K.describe(binding.entry, record)}")
    return 0


def cmd_macro(args, dev: Device) -> int:
    slot = args.slot
    if args.clear:
        MACRO.clear_macro(dev, slot)
        print(f"slot {slot} macro cleared")
        return 0
    if args.load:
        with open(args.load, "rb") as fh:
            data = fh.read()
        if args.load.lower().endswith(".jmm"):
            macro = MACRO.from_jmm(data)
        else:
            macro = MACRO.Macro.from_json(json.loads(data.decode()))
        MACRO.write_macro(dev, slot, macro)
        if args.repeat:
            P.set_key(dev, slot, K.KEY_TYPE_MACRO, slot, args.repeat)
        print(
            f"slot {slot}: {macro.name!r}, {len(macro.events)} steps"
            + (f", bound to play {MACRO.repeat_label(args.repeat)}" if args.repeat else "")
        )
        return 0
    macro = MACRO.read_macro(dev, slot)
    if macro.is_empty:
        print(f"slot {slot}: empty")
        return 0
    print(f"slot {slot}: {macro.name!r}, {len(macro.events)} steps, "
          f"{macro.duration_ms()} ms")
    for index, event in enumerate(macro.events):
        print(f"  {index + 1:2d}. {event.label():<24} +{event.delay_ms} ms")
    return 0


def cmd_export(args, dev: Device) -> int:
    document = transfer.read_device(dev)
    with open(args.path, "w") as fh:
        json.dump(document, fh, indent=2)
        fh.write("\n")
    print(f"wrote {args.path}")
    return 0


def cmd_import(args, dev: Device) -> int:
    with open(args.path) as fh:
        document = json.load(fh)
    image, combo = transfer.from_json(document)
    transfer.write_device(dev, image, combo)
    print(f"restored {args.path}")
    return 0


def cmd_raw(args, dev: Device) -> int:
    data = bytes(int(x, 0) for x in args.bytes)
    dev.write(args.addr, data)
    print(f"wrote {len(data)} bytes at {args.addr:#04x}")
    return 0


# ---------------------------------------------------------------- entrypoint
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="m693ctl", description="Configure a Redragon M693 mouse on Linux"
    )
    ap.add_argument("--device", help="hidraw node to use (default: autodetect)")
    ap.add_argument(
        "--wait",
        type=_wait,
        default=None,
        metavar="SECONDS",
        help="how long to keep retrying while the mouse is asleep: a number of "
        "seconds, 'forever' (the default), or 0 to fail immediately",
    )
    ap.add_argument(
        "--version", action="version", version=f"m693ctl {__version__}"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list detected M693 control interfaces")
    sub.add_parser("info", help="show the full current configuration")

    p = sub.add_parser("battery", help="show the battery charge")
    p.add_argument(
        "-q", "--quiet", action="store_true",
        help="print just the number, for status bars and scripts",
    )

    p = sub.add_parser("dump", help="hex-dump on-board configuration memory")
    p.add_argument("--addr", type=lambda x: int(x, 0), default=0x00)
    p.add_argument("--length", type=lambda x: int(x, 0), default=P.PROFILE_LEN)
    p.add_argument("-o", "--output", help="write raw bytes to a file instead")

    p = sub.add_parser("restore", help="write a previously dumped image back")
    p.add_argument("input")
    p.add_argument("--addr", type=lambda x: int(x, 0), default=0x00)

    p = sub.add_parser("rate", help="set the USB polling rate")
    p.add_argument("hz", type=int, choices=sorted(P.POLL_DIVISORS))

    p = sub.add_parser("dpi", help="inspect or change DPI stages")
    p.add_argument("stages", nargs="*", help="e.g. 800 1600 3200, or 800x400")
    p.add_argument("--active", type=int, help="1-based stage to activate")
    p.add_argument("--color", nargs=2, metavar=("STAGE", "RRGGBB"))

    p = sub.add_parser("led", help="inspect or change lighting")
    p.add_argument(
        "--zone",
        choices=(*L.ZONES, "all"),
        default=L.ZONE_STRIPS,
        help=f"{L.ZONE_STRIPS} (default) and {L.ZONE_LOGO} are one channel on "
        f"this model - either name moves both; {L.ZONE_WHEEL} = scroll wheel; "
        "all = every zone",
    )
    p.add_argument("--mode", choices=sorted(P.LED_MODE_NAMES))
    p.add_argument("--brightness", type=int)
    p.add_argument(
        "--speed", type=int,
        help=f"{L.SPEED_MIN} (slowest) to {L.SPEED_MAX} (fastest)",
    )
    p.add_argument("--color", help="RRGGBB")

    p = sub.add_parser("preset", help="saved lighting looks")
    p.add_argument("action", choices=("list", "apply", "save", "delete"))
    p.add_argument("name", nargs="?", default="")
    p.add_argument(
        "--keep-dpi-colors",
        action="store_true",
        help="apply the preset's effects but leave the DPI stage colours "
        "(and so the wheel's colour) alone",
    )

    p = sub.add_parser("set", help="change sensor and power settings")
    p.add_argument("--lod", type=int)
    p.add_argument("--debounce", type=int, metavar="MS")
    p.add_argument("--dormancy", type=int, metavar="SECONDS")
    p.add_argument("--angle-snap", type=_onoff)
    p.add_argument("--motion-sync", type=_onoff)
    p.add_argument("--ripple", type=_onoff, help="ripple control")
    p.add_argument("--light-off-moving", type=_onoff)

    p = sub.add_parser("button", help="remap a button slot")
    p.add_argument("slot", type=int)
    p.add_argument(
        "action",
        help="function id, fire:MS,SHOTS, dpi-lock:STAGE, "
        "key:ctrl+shift+t, or raw:type,p1,p2",
    )

    p = sub.add_parser("macro", help="inspect or load a button's macro")
    p.add_argument("slot", type=int)
    p.add_argument("--load", metavar="FILE", help="a .jmm or .json macro to write")
    p.add_argument(
        "--repeat", type=int, metavar="N",
        help="also bind the button: N times, 254 to toggle, 255 while held",
    )
    p.add_argument("--clear", action="store_true", help="empty the slot")

    p = sub.add_parser("export", help="save the whole configuration as JSON")
    p.add_argument("path")

    p = sub.add_parser("import", help="restore a configuration saved with export")
    p.add_argument("path")

    p = sub.add_parser("raw", help="write raw bytes (checksums NOT added)")
    p.add_argument("addr", type=lambda x: int(x, 0))
    p.add_argument("bytes", nargs="+")

    return ap


HANDLERS = {
    "info": cmd_info,
    "battery": cmd_battery,
    "dump": cmd_dump,
    "restore": cmd_restore,
    "rate": cmd_rate,
    "dpi": cmd_dpi,
    "led": cmd_led,
    "preset": cmd_preset,
    "set": cmd_set,
    "button": cmd_button,
    "macro": cmd_macro,
    "export": cmd_export,
    "import": cmd_import,
    "raw": cmd_raw,
}


def _asleep_notice() -> None:
    print(
        "m693ctl: the mouse is asleep - move or click it and this will "
        "continue on its own (Ctrl-C to give up)",
        file=sys.stderr,
    )


def _run(args, dev: Device, deadline: float | None) -> int:
    """Run the subcommand, resuming if the mouse dozes off part-way through.

    Every subcommand is idempotent - each one sets a field to a stated value -
    so re-running one after a half-finished write is safe and converges on the
    right result.  It is only worth retrying when the device went quiet;
    an explicit refusal is reported straight away.
    """
    while True:
        try:
            return HANDLERS[args.cmd](args, dev)
        except DeviceError as exc:
            if not exc.retryable:
                raise
            if deadline is not None and time.monotonic() >= deadline:
                raise
            remaining = None if deadline is None else max(
                0.0, deadline - time.monotonic()
            )
            if not dev.wait_online(remaining, notify=_asleep_notice):
                raise
            print("m693ctl: mouse is back, retrying", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "list":
        return cmd_list(args)
    # Managing the preset file touches no hardware, so do not make the user
    # wake the mouse to list or delete one.
    if args.cmd == "preset" and args.action in ("list", "delete"):
        return cmd_preset(args, None)
    wait = args.wait if hasattr(args, "wait") else None
    deadline = None if wait is None else time.monotonic() + wait
    try:
        with Device(args.device) as dev:
            dev.set_driver_status(True)
            if not dev.wait_online(wait, notify=_asleep_notice):
                print(
                    "error: the mouse is not responding - it is asleep, powered "
                    "off, or out of range. Move or click it, then retry.",
                    file=sys.stderr,
                )
                return 1
            try:
                return _run(args, dev, deadline)
            finally:
                dev.set_driver_status(False)
    except DeviceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nm693ctl: gave up waiting", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
