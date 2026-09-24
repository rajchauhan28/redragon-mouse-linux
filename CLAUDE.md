# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A userspace driver, CLI and Qt GUI for the Redragon M693-RGB mouse, reimplementing the
Windows-only `OemDrv.exe`. It speaks the vendor HID protocol over `hidraw` — no kernel
module, no daemon. `m693/` (driver + CLI) has **zero third-party dependencies**; PySide6 is
an extra, needed only by `m693gui/`. Keep it that way: a dependency added to `m693/` breaks
the headless-install promise the packaging is built around.

`PROTOCOL.md` documents every byte of the device's configuration memory and how it was
recovered. Read it before changing anything in `m693/profile.py`, `keys.py`, `macro.py` or
`lighting.py` — the addresses and encodings there are findings, not choices. `PLAN.md`
records the GUI architecture and why.

## Commands

```sh
./m693ctl list                  # runnable straight from a checkout, no install
./m693-gui --fake               # simulated mouse; no hardware needed
./m693-gui --native-decorations # the window is frameless by default

QT_QPA_PLATFORM=offscreen python3 tests/test_model.py   # one test file
for t in tests/test_*.py; do QT_QPA_PLATFORM=offscreen python3 "$t" || break; done
makepkg -si                     # Arch package (builds from this working tree via git+file://)
```

Tests are plain scripts with a `__main__` runner that collects `test_*` globals; they also
work under pytest. Each sets `QT_QPA_PLATFORM=offscreen` itself and inserts the repo root on
`sys.path`, so `python3 tests/test_x.py` from anywhere is the normal way to run one. There is
no lint or type-check configuration. `test_unknowns.py`, `test_retry.py` and the protocol half
of `test_battery.py` are the only ones that do not need PySide6 — `PKGBUILD`'s `check()` runs those unconditionally and the rest only
if Qt is importable.

`tests/` uses `m693gui.backend.FakeDevice`, an in-memory device seeded from
`m693/stock-profile.bin` that also simulates sleep and dropped packets.

## Architecture

**Layers.** `m693/device.py` is transport only: discovery, 17-byte packet framing, the
`0x55` checksum, reply matching, retries, and the sleep/wake dance. Everything above it
addresses configuration memory: `profile.py` (memory map, DPI tables, group map),
`keys.py` (button catalogue + combo-key records at `0x100`), `macro.py` (macro records at
`0x300`, `.jmm` import), `lighting.py` (per-zone state and presets), `transfer.py` (JSON
export/import), `presets.py` (`~/.config/m693/presets.json`, shared with the GUI).
`cli.py` sits on top of all of them.

**Checksummed groups are the unit of everything.** Configuration memory is a sequence of
2-byte scalars and 4-byte records, each summing to `0x55`. That is the smallest thing that
can be written without corrupting a checksum, so it is the unit `profile.GROUPS` enumerates,
the unit the GUI marks dirty, and the unit `merge_runs()` coalesces into ≤10-byte write
commands. **The mouse silently ignores a group whose checksum does not validate** — the
write is acknowledged and reads back unchanged. Any new field must be added to `_build_groups()`
or it will never be written. `0xB5`+ is listed in `OPAQUE_RANGES` and never written back.

**The GUI never writes through.** `m693gui/model.py` (`ProfileModel`) holds a shadow copy of
the image; edits mutate it, mark groups dirty, and restart a 250 ms single-shot timer. On
fire, `flush()` emits write runs — macros and combo records **first**, because a keymap entry
of type `0x05`/`0x06` is meaningless until the record it points at is in place. Groups leave
the dirty set only when the worker confirms them (`confirm`/`confirm_blocks`); a failed flush
calls `retry_later()` and the edits survive. Use `pause()`/`resume()` to batch a multi-field
edit into one flush.

**One thread touches the fd.** `m693gui/worker.py` (`DeviceWorker`) owns the device and the
`DISCONNECTED`/`ASLEEP`/`ONLINE` state machine, lives on its own `QThread`, and is reached
only through signals wired up in `app.py`. Nothing else locks because nothing else has the
fd. `backend.py` chooses between `Device` and `FakeDevice`.

**Battery is a command, not a memory field.** `Device.power()` sends command
`0x04` (`GetPower` in the stock tool) and decodes `reply[5]` as a percentage and
`reply[6]` as a charging flag; a reading above 100 is rejected as retryable
rather than clamped, matching what `OemDrv.exe` does. Nothing is pushed when
the charge changes and the device advertises no HID battery usage, so it must
be polled — `DeviceWorker` does that on every fifteenth liveness tick and emits
`battery(percent, charging)`. **Command `0x0D` resets the device** and can
switch the active profile bank; do not send unknown command bytes without a
backup.

**Sleep is normal, not an error.** The mouse dozes off in about a minute and ignores
configuration commands; the 2.4 GHz receiver keeps answering status queries while it does, so
"dongle present" ≠ "mouse reachable". `DeviceError.retryable` distinguishes silence (retry,
wait) from an explicit refusal (report immediately) — preserve that distinction in new device
code. The link is lossy, so every command is already retried.

**Hardware facts that constrain the UI**, all expanded on in `PROTOCOL.md`: three lit areas
but only two channels (`0xA0` drives side strips *and* palm logo together; `0x4C` the wheel);
the wheel's colour *is* the DPI stage colour table, so setting it overwrites per-stage colour
coding; the stored speed byte is a *delay* (higher is slower); one macro per button, addressed
by button slot, 70 steps max; no live-lighting command, so custom animations are impossible
and presets combine the seven firmware effects instead.

## Conventions

- `m693/` stays dependency-free and importable without Qt. GUI-only logic belongs in `m693gui/`;
  anything the CLI could also want (lighting zones, presets, macro decoding) belongs in `m693/`.
- Address constants (`ADDR_*`) live in the module that owns the region and are referenced by
  name, not repeated as literals.
- Old constant names are kept as aliases (`ADDR_LED_COLOR`, `ZONE_MAIN`, `confirm_combo`) rather
  than renamed away.
- Comments explain *why the device forces this*, not what the code does. Match that.
- Version lives in `m693/__init__.py` and is read from there by `PKGBUILD`'s `pkgver()`;
  `pyproject.toml` and `PKGBUILD` carry it too, so bump all three together.
