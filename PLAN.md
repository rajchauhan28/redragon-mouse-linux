# m693-gui — implementation plan

A PySide6 desktop app reproducing the Windows `OemDrv.exe` interface on Linux,
built on the existing `m693/` driver.

Decisions taken: **PySide6/Qt**, **reuse the extracted Redragon artwork**,
**Macro tab deferred** to a later phase.

---

## 1. The constraint that drives the design

This is not a normal settings dialog. Three properties of the hardware dictate
the architecture:

1. **Every write is a slow, lossy round-trip.** A command is relayed over
   2.4 GHz to the mouse and retried up to 9 times; a failing one costs seconds.
   A slider drag naively bound to a write would emit hundreds of them.
2. **The mouse sleeps constantly.** It stops answering configuration commands
   when idle, while the receiver keeps answering status queries. The UI must
   treat "asleep" as a normal, recoverable state — a banner, not an error
   dialog.
3. **Writes apply instantly and persist.** There is no commit step, so there is
   no natural moment to batch on. We have to invent one.

The answer to all three is a **shadow model with dirty-group flushing on a
worker thread**.

---

## 2. Architecture

```
┌──────────────────────────────────────────────┐
│ Qt widgets  (main thread, never blocks)      │
│   nav bar · KeyTab · DpiTab · MacroTab · LedTab
└───────────────┬──────────────────────────────┘
                │ Qt signals/slots
┌───────────────┴──────────────────────────────┐
│ ProfileModel   (main thread)                 │
│   192-byte shadow image + dirty group set    │
│   flush timer (250 ms coalescing)            │
└───────────────┬──────────────────────────────┘
                │ queued signals across threads
┌───────────────┴──────────────────────────────┐
│ DeviceWorker   (QThread — owns the fd)       │
│   serialises all I/O · link state machine    │
└───────────────┬──────────────────────────────┘
                │
┌───────────────┴──────────────────────────────┐
│ m693/  device.py · profile.py   (unchanged)  │
└──────────────────────────────────────────────┘
```

The existing library stays pure and GUI-agnostic; `m693ctl` keeps working.

### 2.1 ProfileModel — shadow state and dirty groups

The mouse's memory is a sequence of **checksummed groups** (2-byte scalars,
4-byte colours/DPI entries/button bindings), each independently satisfying
`sum == 0x55`. That is the natural unit of change.

```python
class ProfileModel(QObject):
    changed = Signal(str)          # field name, for cross-widget updates
    dirty_changed = Signal(bool)

    _image: bytearray              # 192-byte shadow of device memory
    _dirty: set[int]               # start addresses of modified groups
```

* Widgets call `model.set_dpi_stage(2, 1600)`; the model rewrites the group in
  `_image`, recomputes its checksum, adds the address to `_dirty`, restarts the
  flush timer, and emits `changed`.
* On timer expiry, `_dirty` is sorted and **adjacent groups are merged into
  runs of ≤10 bytes** — the device's maximum payload — so a "set all 5 DPI
  stages" edit becomes 2 writes rather than 5.
* The write job is handed to the worker. `_dirty` is only cleared once the
  worker confirms; on failure the addresses stay dirty and are retried on the
  next tick.

This gives free correctness properties: checksums can never drift, and a
full-profile reload is just `_image = worker.read()` plus a repaint.

### 2.2 DeviceWorker — the only thread touching the fd

Lives on a `QThread` with its own event loop. Exposes queued slots
`read_profile()`, `write_runs(list[tuple[int, bytes]])`, `set_active_dpi(i)`,
and emits `profile_read`, `write_failed`, `link_state_changed`.

Because it is the sole owner of the file descriptor, no locking is needed
anywhere else.

### 2.3 Link state machine

Polled every 2 s with `GetConnectStatus` (cheap — the receiver answers it even
while the mouse dozes, and the *payload byte* carries the real state):

| State | Trigger | UI |
| --- | --- | --- |
| `DISCONNECTED` | no hidraw node | Full-window overlay: "Connect your M693" |
| `ASLEEP` | node present, `connect_status() == 0` | Amber banner: "Move the mouse to wake it" — controls stay visible but disabled, pending edits held |
| `ONLINE` | status byte non-zero | Normal |

Transitions to `ONLINE` automatically flush anything queued while asleep. A
`QSocketNotifier`/udev watch on `/dev/hidraw*` handles hot-plug so the app
recovers without a restart.

---

## 3. Screens

Navigation is the four-icon segmented bar from the original: Key Settings, DPI
Settings, Macro, Lighting — plus the language/settings/minimise/close chrome.

### 3.1 Key Settings — *done in phase 4*

* Left column: 7 numbered rows, each a styled combo box of assignable
  functions, with the profile selector above and the debounce stepper below.
* Right: `mouse_nr.png` with numbered callout lines drawn as an overlay; the
  callouts and the list highlight together on hover.
* Bottom: Restore / Export Profile / Import Profile.

**Ready now:** disable, mouse buttons, DPI+/−/loop, fire key, macro slot,
existing media bindings — all via `KEY_TYPE_*` in `profile.py`.

**Still to reverse:** the keyboard-key binding (`type 0x03`, modifier mask +
HID usage) and the multimedia usage codes (`type 0x08`). Both are small,
well-scoped jobs against `keyinfo_to_hardware_code` in the binary. Until then
those entries are shown greyed with a tooltip, and `raw:type,p1,p2` remains
available from the CLI.

Export/Import writes JSON (readable, diffable) with the raw 192-byte image
embedded as a base64 fallback.

### 3.2 DPI Settings — *complete today*

Everything on this screen maps to a decoded byte:

| Control | Backing |
| --- | --- |
| DPI Stages combo (1–8) | `ADDR_DPI_COUNT` |
| 5× slider + colour swatch | `ADDR_DPI_TABLE` / `ADDR_DPI_COLORS` |
| Polling Rate radios | `ADDR_POLL_RATE` |
| DPI Effect + brightness/speed | `ADDR_LED_MAIN` |
| LOD 1 mm / 2 mm | `ADDR_LOD` |
| Ripple control, Angle snapping | `ADDR_MOTION_SYNC`, `ADDR_ANGLE_SNAP` |

The sliders must **snap to the sensor's real steps** (100 DPI up to 4000, 200
above) using `dpi_to_reg`, so the number shown is the number the mouse will
actually use — the stock tool lets you pick values it silently quantises.

Clicking a stage's swatch opens the shared colour picker (§3.4).

### 3.3 Macro — *done in phase 6*

The tab ships with the real layout (macro list, key list, record controls,
cycle options) and a "not yet supported on Linux" state, so the shape is there
when the format lands.

The RE work, when we get to it: 16 slots of `0x180` bytes from `0x300`, each
with a 30-byte name at offset +1 (recovered from the read loop at `0x40f6bb`),
plus `HdMacro_To_StMacro` for the event encoding and the `.jmm` on-disk format
for interop with Windows-exported macros.

### 3.4 Lighting — *complete today*

* LED Effect combo — the seven modes, written in **on-device** numbering
  (`profile.LED_MODES`), not the UI's reshuffled order.
* **Colour wheel**: custom `QWidget` painting an HSV disc into a cached
  `QImage`, with a draggable marker. Below it the 14 predefined swatches.
* **Live preview**: `mouse_nr.png` with `mouse_led.png` tinted to the current
  colour and composited on top — exactly what the Windows app does. A
  `QTimer` drives per-mode animation (breathing = eased alpha, streaming =
  hue rotation, off = overlay hidden) so the preview shows what the mouse is
  doing.
* Brightness / Speed sliders, "Turn off light when moving", Dormancy combo.

---

## 4. Look and feel

* **Qt Style Sheet** (`assets/theme.qss`) carries the whole dark/red identity —
  `#1e1e1e` panels on `#141414`, `#c8102e` accents, 1 px red borders on
  grouped panels. Widgets stay standard `QComboBox`/`QSlider`/`QCheckBox`;
  only genuinely novel things become custom classes.
* **Frameless window** with a custom title bar to match the original, with a
  `--native-decorations` flag as an escape hatch (frameless windows and
  Hyprland tiling can disagree).
* **Custom widgets:** `ColorWheel`, `MousePreview`, `DpiStageRow`,
  `SegmentedNav`, `Callout` overlay.
* **HiDPI:** the source art is fixed-size raster, so the preview is scaled with
  smooth transforms and the layout uses `Qt.AA_EnableHighDpiScaling`.

### Asset pipeline

`tools/extract_assets.py` runs `innoextract` against the original `.exe` and
copies the needed PNGs into `assets/`. Keeping it a script rather than
committing the images means **the artwork is never redistributed with the
source** — a user supplies their own installer copy, which is the right posture
for third-party assets. `assets/` goes in `.gitignore`, and the app falls back
to the schematic view if the extraction has not been run.

---

## 5. Phases

| Phase | Deliverable |
| --- | --- |
| **1 — Skeleton** | Window, nav bar, theme, `DeviceWorker`, `ProfileModel`, link-state banner, hot-plug. Proves the threading and offline handling before any real UI. |
| **2 — DPI tab** | The whole screen; nothing new to reverse. First fully usable tab. |
| **3 — Lighting tab** | Colour wheel, swatches, animated live preview. |
| **4 — Key Settings** | Layout, callouts, profile export/import, plus the small RE job for keyboard and media bindings. |
| **5 — Polish** | Restore-to-stock from the bundled `m693/stock-profile.bin`, keyboard navigation, `.desktop` entry, packaging. |
| **6 — Macros** | Decode `0x300`–`0x1B00` and `.jmm`, then enable the tab. |

All six phases are done.

---

## 6. Risks

* **Write amplification** — mitigated by the coalescing flush; worth asserting
  in tests that a full slider drag produces one write run, not many.
* **Sleep during a flush** — the dirty set is only cleared on confirmed
  success, so a sleeping mouse defers rather than loses edits.
* **Artwork licensing** — the extract-at-build-time approach keeps Redragon's
  assets out of the repository.
* **Frameless window vs. Hyprland** — escape hatch flag, above.
* **Testing without hardware** — a `FakeDevice` backed by the 192-byte stock
  dump lets the whole UI be developed and tested with the mouse unplugged.

---

## 7. Proposed layout

```
m693-linux/
├── m693/                    existing driver (unchanged)
├── m693gui/
│   ├── app.py               entry point, window, nav
│   ├── worker.py            DeviceWorker (QThread)
│   ├── model.py             ProfileModel, dirty-group flushing
│   ├── widgets/             ColorWheel, MousePreview, DpiStageRow, …
│   ├── tabs/                keys.py, dpi.py, macro.py, lighting.py
│   └── assets/theme.qss
├── tools/extract_assets.py
└── m693-gui                 launcher
```
