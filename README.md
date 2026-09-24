# m693ctl — Redragon M693-RGB on Linux

> **Disclaimer:** This project is not from the actual Redragon company. Use it at your own risk. This is an open-source project created only to make life easier for Redragon mouse users on Linux. For other mouse types, I'll be happy to discuss further if anyone with another Redragon mouse is willing to participate in testing.

<img width="1860" height="1195" alt="screenshot_20260925_000601" src="https://github.com/user-attachments/assets/1741298d-3df3-442f-bdd2-e0054c6f812c" />
<img width="1857" height="1191" alt="screenshot_20260925_000611" src="https://github.com/user-attachments/assets/ba84686b-25f2-47a2-9bd8-7f6d23ebc52c" />
<img width="1860" height="1200" alt="screenshot_20260925_000622" src="https://github.com/user-attachments/assets/7c06d9f3-7f34-440a-b7b5-358986093ff0" />
<img width="1857" height="1193" alt="screenshot_20260925_000629" src="https://github.com/user-attachments/assets/10156695-a7d2-43c2-a8d6-e6d0e838dafa" />



A userspace driver for the Redragon M693-RGB gaming mouse, reimplementing what
the Windows-only `OemDrv.exe` configuration tool does. No kernel module, no
Wine — it speaks the vendor HID protocol directly over `hidraw`.

See [PROTOCOL.md](PROTOCOL.md) for how the protocol was recovered and what
every byte of the mouse's configuration memory means.

Pure Python 3.9+, no third-party dependencies.

## Install

On Arch and derivatives, build the package:

```sh
makepkg -si                      # driver, CLI, GUI, udev rule, desktop entry
```

`pyside6` is an optional dependency, so `m693ctl` works on a machine without
Qt; install it if you want `m693-gui`.

Anywhere else:

```sh
tools/install.sh                 # udev rule, input group, desktop entry
pip install --user '.[gui]'      # optional: puts m693ctl and m693-gui on PATH
```

Re-plug the receiver afterwards, then verify with:

```sh
m693ctl list                     # or ./m693ctl list, straight from a checkout
```

`install.sh` only does three things and prints each of them, so read it first if
you would rather do them by hand: it copies `99-redragon-m693.rules` into
`/etc/udev/rules.d/`, adds you to the `input` group, and drops a `.desktop`
entry and icon under `~/.local/share`.

Everything works from a checkout without installing anything — `./m693ctl` and
`./m693-gui` are runnable in place. The driver and CLI have **no dependencies**;
PySide6 is needed only for the GUI, which is why it is an extra.

## Usage

```sh
./m693ctl info                       # everything the mouse currently holds
./m693ctl battery                    # charge, and whether it is charging
./m693ctl battery -q                 # just the number, for a status bar

./m693ctl rate 1000                  # polling rate: 125 / 250 / 500 / 1000 Hz
./m693ctl dpi 800 1600 3200          # replace the DPI stages
./m693ctl dpi 1600x800               # independent X/Y
./m693ctl dpi --active 2             # switch to stage 2
./m693ctl dpi --color 1 00ff00       # stage 1 indicator colour

./m693ctl led --mode breathing --brightness 200 --speed 5 --color ff0080
./m693ctl led --zone wheel --mode steady --color 00ff00   # just the wheel
./m693ctl led --zone all --mode streaming --speed 8       # both zones

./m693ctl preset list                # built-in and saved lighting looks
./m693ctl preset apply Ember
./m693ctl preset save "my look"      # captures every zone as it stands now
./m693ctl set --debounce 4 --lod 2 --angle-snap off
./m693ctl set --motion-sync on --ripple off
./m693ctl set --dormancy 300         # sleep timer, seconds

./m693ctl button 5 dpi-loop          # remap a button slot
./m693ctl button 5 mute              # any multimedia or web function
./m693ctl button 5 key:ctrl+shift+t  # a keyboard shortcut
./m693ctl button 5 fire:10,3         # fire key: 10 ms apart, 3 shots
./m693ctl button 5 dpi-lock:2        # hold at DPI stage 3 while pressed
./m693ctl button 5 disable

./m693ctl export my-profile.json     # everything, readable and restorable
./m693ctl import my-profile.json

./m693ctl macro 9                    # what a button's macro contains
./m693ctl macro 9 --load gg.jmm --repeat 1
./m693ctl macro 9 --clear
```

`button` with an unknown action prints the whole catalogue. Slots are 0-based;
`info` lists what each currently holds.

LED modes: `steady`, `breathing`, `streaming`, `neon`, `single-color-flow`,
`colorful-breathing`, `off`. The cycling modes (`streaming`, `neon`,
`colorful-breathing`) ignore the stored colour, so set a static mode as well if
you want `--color` to be visible.

### Back up before experimenting

```sh
./m693ctl dump -o m693-backup.bin    # 192 bytes of configuration memory
./m693ctl restore m693-backup.bin
```

`dump` with no `-o` hex-dumps instead, and `--addr` / `--length` let you look
anywhere (e.g. `--addr 0x100` for the macro area). `raw` writes arbitrary bytes
without adding checksums, for protocol work.

## Graphical interface

```sh
./m693-gui                       # against the real mouse
./m693-gui --fake                # simulated mouse, no hardware needed
./m693-gui --native-decorations  # use your WM's title bar
```

It is fully keyboard-drivable, which matters more here than in most settings
dialogs: this is the window you use when a remap has gone wrong and the mouse
is the one input device you cannot trust.

| Key | Action |
| --- | --- |
| `Ctrl+1` … `Ctrl+4` | jump to a section |
| `Ctrl+Tab` / `Ctrl+Shift+Tab`, or ← → on the nav bar | next/previous section |
| `Tab` | next control; focus is always visibly ringed |
| `Ctrl+R` | re-read everything from the mouse |
| `F5` | wake a sleeping mouse |
| `Ctrl+E` / `Ctrl+I` | export / import a profile |
| `Ctrl+W`, `Ctrl+Q` | quit |

Optionally pull in the mouse renders used for the button callouts and the
lighting preview. They are Redragon's artwork and are not shipped with this
repository, so supply your own copy of the installer:

```sh
tools/extract_assets.py "REDRAGON M693-RGB Setup ….exe"
```

They go into the checkout if you are working in one, otherwise into
`~/.local/share/m693/device/` — which is where they need to be for an
installed package, since site-packages belongs to root.

**Status: complete.** All four tabs — **Key Settings**, **DPI Settings**,
**Macro** and **Lighting** — are implemented, and the app is packaged and
keyboard-navigable. See [PLAN.md](PLAN.md).

The Lighting tab has two sync modes, as radio buttons: **Side strips + palm
logo** leaves the wheel with its own effect and the drop-down picks which zone
you are editing; **All three zones together** drives the lot from one set of
controls. There is no third option because the strips and the logo are one
channel — they are always in step, whichever mode you are in. Presets store every zone's
effect, colour, brightness and speed under a name in
`~/.config/m693/presets.json`, shared with `m693ctl preset`. There is no
programmable effect engine on this mouse and no live-lighting command, so a
custom animation would mean writing to its configuration flash tens of times a
second; presets combine the seven firmware effects instead, and cost nothing to
leave applied.

The Macro tab keeps a library in `~/.config/m693/macros.json`, records
keystrokes with their real timing, and lets you add mouse-button and
multimedia steps by hand. Macros exported from the Windows tool import
directly: `.jmm` is a documented format now.

Key Settings gives each of the seven buttons the stock tool exposes a full
function menu — mouse buttons, DPI actions, multimedia and web keys, keyboard
shortcuts, fire keys, DPI lock — with the numbered callouts landing on the
leader lines already drawn in Redragon's own render. Profiles export to JSON
and import back.

The DPI sliders snap to the sensor's real ladder — 100 DPI steps to 4000, 200
above — so the number on screen is always a value the mouse can actually store.
The stock Windows tool lets you pick values it then silently quantises.

Edits never write straight through to the mouse. They mutate an in-memory copy
of its configuration, and a 250 ms timer merges the changed checksummed groups
into as few write commands as the protocol allows, on a background thread.
Dragging a slider therefore costs one write, not one per pixel. If the mouse
falls asleep mid-edit the changes are held and applied when it wakes.

```sh
python3 tests/test_model.py       # no hardware required
python3 tests/test_dpi_tab.py
python3 tests/test_lighting_tab.py
python3 tests/test_keys_tab.py
python3 tests/test_shell.py
python3 tests/test_macro_tab.py
python3 tests/test_unknowns.py
python3 tests/test_battery.py     # protocol half runs without Qt
```

## As a library

```python
from m693 import Device, Profile
from m693 import profile as P

with Device() as dev:
    dev.set_driver_status(True)
    dev.wait_online()

    prof = Profile.read_from(dev)
    print(prof.polling_rate, prof.dpi_stages, prof.dpi_colors)

    P.set_dpi_stages(dev, [400, 800, 1600, 3200])
    P.set_led_color(dev, (0xFF, 0x00, 0x80))
```

## Notes and caveats

* **Changes apply instantly** and persist across re-plugs — the settings live
  in the mouse, not in a background daemon. There is no apply step.
* **A sleeping mouse ignores configuration commands.** It dozes off within
  about a minute of being left alone. `m693ctl` handles this itself: it waits
  for the mouse indefinitely, resumes a command that was interrupted part-way,
  and says so on stderr while it waits. `--wait SECONDS` puts a limit on that,
  and `--wait 0` fails immediately instead. Only silence is retried — an
  explicit refusal from the device is reported straight away.

  Re-announcing the driver usually wakes the mouse on its own, so in practice
  the wait is a second or two rather than "until you touch it". Note that the
  2.4 GHz receiver keeps answering status queries even while the mouse is
  asleep, so "the dongle is present" is not the same as "the mouse is
  reachable".
* **The wireless link is lossy**; every command is retried automatically.
* **The mouse reports its own charge** over command `0x04`, as a whole
  percentage plus a charging flag — the same `GetPower` the Windows tool's
  battery icon uses. It has to be polled: the mouse pushes no notification
  when the charge changes, and it advertises no HID battery usage, so your
  desktop's power applet will never list it. The GUI polls it about every
  30 seconds and shows it in the status bar.
* Both the wired product id (`25a7:fa7b`) and the 2.4 GHz receiver
  (`25a7:fa7c`) are recognised, and both have now been exercised.
  **Plugging the cable in switches the mouse to wired mode**: it enumerates in
  its own right as `fa7b` while the receiver stays on the bus with nothing
  linked to it. That idle receiver still answers — it reports the mouse
  offline and hands back the *last battery figure it saw*, which looks exactly
  like a live one. So a directly connected mouse is always preferred, and a
  charge is refused outright unless the link says the mouse is really there.
* **The mouse holds one macro per button**, not a library — a macro lives at
  the address matching the button it is bound to. The library on disk is ours,
  not the mouse's. Up to 70 steps per macro.
* Macro recording captures **keystrokes only**. Capturing mouse buttons would
  mean swallowing the clicks you need to stop the recording, so they are added
  from the step list instead — which is also the only way to write a press
  without its release.
* `0x06`, `0x08`, `0x52`, `0x5E` and `0xA7` are spare: zero, correctly
  checksummed, and never read by the stock tool either.
* `0xB5` and `0xB7` are real settings the Windows tool can write, but this
  model has never written them — they read `0xFF` and form no valid group — so
  they are left alone rather than guessed at.
* The mouse lights three places but has only **two** channels: `0xA0` drives
  the side strips *and* the palm logo together, `0x4C` drives the scroll wheel.
  All three are listed separately, because that is how the mouse looks, but
  the strips and the logo are one channel — either entry moves both, and both
  say so. There is no way to split them on this model.
* The wheel's colour is the **DPI stage colour table**, not a field of its own,
  so giving the wheel a colour overwrites the per-stage colour coding. The CLI
  says so and the GUI asks first.
* `0x54`–`0x5F` is a second effect block whose colour bytes are ignored; its
  mode byte at `0x58` reaches the wheel, which `0x4C` gates. Nothing exposes it.
* In the travelling effects the two side strips are **offset in time** — they
  are one LED chain running down one side and back up the other. There is no
  phase or direction control: the stock tool has none, and writing to every
  spare byte in the map changes nothing.
* The stored **speed byte is a delay** — higher is slower — and the firmware
  accepts the whole byte, well past the stock tool's limit of 10. The 1–10
  slider is mapped onto that full range, so 1 is far slower than the Windows
  tool could go. The two zones do not run at the same rate for the same value.
* The mouse **silently ignores** any field group whose checksum does not
  validate — the write is acknowledged and reads back, but nothing changes.
  `raw` bypasses checksum handling, so use it deliberately.
* Verified against firmware `2910-B TX V3 CX52850+P3104`. Other M693 revisions
  very likely share this protocol — it is a common Areson/Compx design — but
  take a backup first.

## Layout

```
m693ctl                    CLI entry point
m693/device.py             discovery, framing, checksums, retries
m693/profile.py            memory map, encoders, DPI tables
m693/keys.py               button catalogue, combo-key records
m693/transfer.py           profile export/import
m693/macro.py              macro records and .jmm files
m693/cli.py                argument parsing and subcommands
m693/stock-profile.bin     factory configuration, for Restore and the simulator
m693gui/                   the Qt front-end (app, model, worker, tabs, widgets)
99-redragon-m693.rules     udev rule for non-root access
packaging/                 .desktop entry
tools/install.sh           udev rule + desktop entry
tools/extract_assets.py    pulls the mouse renders out of the Windows installer
PROTOCOL.md                the reverse-engineering write-up
```
