# Redragon M693-RGB — configuration protocol

Reverse engineered from `REDRAGON M693-RGB Setup v1.0 20221013(2910-B TX V3
CX52850+P3104).exe`, and verified against real hardware (`25a7:fa7c`).

## 1. Unpacking the installer

The `.exe` is an **Inno Setup 5.3.3** installer (the `.itext` PE section plus
~2.7 MB of appended payload is the giveaway; `7z` cannot open it).

```sh
innoextract -e -d out "REDRAGON M693-RGB Setup ….exe"
```

The interesting artefacts are:

| File | Role |
| --- | --- |
| `app/OemDrv.exe` | the configuration GUI — 2.2 MB, PE32, MFC |
| `app/Cfg.ini` | per-model description: USB ids, DPI tables, defaults |
| `app/en/text.xml` | UI strings — the catalogue of features and LED modes |

`Cfg.ini` alone gives up the identity and most of the tunables:

```ini
VID=0x25A7        PID=0xFA7B       PID2=0xFA7C
CID=0x33  MID=0x01  Sensor=0x3104
DM=5      DPI=500,1000,2000,3000,8000
DPIHW=0e,10,12,15,17,1a,…,69
DC=255,0,0, 0,0,255, 0,255,0, 255,0,255, 255,255,0
DEFLEVEL=1   Debounce=8   DR=0x125
```

`OemDrv.exe` imports `SetupAPI` + `HID.DLL`, and of the HID entry points only
**`HidD_SetFeature`** is used for output; input comes back through plain
`ReadFile` on a second handle. So: feature reports out, input reports in.

## 2. Transport

The receiver exposes two USB interfaces. Interface 0 is the ordinary mouse and
must be left alone. **Interface 1** carries the vendor collections:

| Usage page | Report | Direction | Size |
| --- | --- | --- | --- |
| `0xFF02` | `0x08` | Feature (host → device) | 16 bytes + id |
| `0xFF01` | `0x09` | Input (device → host) | 16 bytes + id |
| `0xFF04` | `0x06` | Feature | 7 bytes + id (unused) |

On Windows these are separate device interfaces (`hCmd` / `hNotify` in the
driver's debug log). On Linux both live on one `hidraw` node — the one whose
report descriptor contains `06 02 FF 09 02 A1 01 85 08`.

### Packet format

Both directions use the same 17-byte frame. From `SendCommand` at `0x451420`:

```
[0]     report id (0x08 out, 0x09 in)
[1]     command
[2]     address bits 15..8
[3]     address bits 7..0
[4]     length / sub-argument
[5..15] payload
[16]    checksum
```

Note the address is **big-endian**, which the disassembly makes explicit:

```asm
mov ecx, eax
sar ecx, 8
mov byte [esp+0x4e], cl     ; payload[2] = addr >> 8
mov byte [esp+0x4f], al     ; payload[3] = addr & 0xFF
mov byte [esp+0x50], bl     ; payload[4] = length
```

### Checksum

The trailing byte makes the frame sum to `0x55`:

```asm
mov al, 0x55
sub al, byte [esp+7]        ; sum of odd-indexed bytes
sub al, bl                  ; sum of even-indexed bytes
sub al, cl
mov byte [edi+0x10], al
```

that is `checksum = (0x55 - sum(bytes[0..15])) & 0xFF`. The **same rule applies
to every stored field group** in configuration memory, which is what lets you
validate a dump offline.

### Replies

Replies arrive as input report `0x09` and mirror the request:

```
[0] command echo   [1] status (0 = OK)   [2..3] address
[4] length         [5..14] data          [15] checksum
```

`WaitNotify` (`0x44f910`) blocks until an input report whose first byte matches
the command it issued, and callers then test `resp[1]` for the error flag.

## 3. Command set

| Cmd | Name in the binary | Notes |
| --- | --- | --- |
| `0x02` | `TellDrvStatus` | `payload[4]=1, payload[5]=on`. Announces the config tool. |
| `0x03` | `GetConnectStatus` | `resp[5]` = 1 when the mouse is linked and awake. |
| `0x04` | `GetPower` | battery. `resp[5]` = percent, `resp[6]` = charging. |
| `0x07` | `WriteEEPROM` | ≤10 data bytes per command. |
| `0x08` | `ReadEEPROM` | ≤10 data bytes per command. |
| `0x0E` | `GetCurProfile` | |
| `0x0F` | — | boolean query; answers `01 01`, unchanging. |
| `0x10` | — | `payload[4]=1, payload[5]=arg`; profile select. |

Sweeping the command byte space turned up more that answer — `0x05`, `0x06`,
`0x09` and `0x0D` all return a well-formed reply — but only `0x04` has a known
meaning. **`0x0D` is not safe to send.** Probing it reset the receiver: the
device re-enumerated on the USB bus and came back on a *different* on-board
profile, which rewrote the live lighting and DPI stage colours from another
bank. Nothing was damaged and a `restore` put it back, but treat `0x0D` as
destructive and take a backup before going near the unknown commands.

There is a second profile image at **`0x1B00`**, immediately after the macro
region ends (`0x300 + 16 x 0x180`). Its first 44 bytes matched the live
profile byte-for-byte while the rest — the DPI colour table, the main RGB
group — differed, which is what a second bank looks like. `GetCurProfile`
reported `0` throughout, so how the banks are selected is still unknown.
Nothing in the CLI or GUI exposes them.

`0x03` is answered by the **receiver**, not the mouse, so a reply alone does not
mean the mouse is awake — you must look at `resp[5]`. Reads and writes are
relayed over the air and simply time out when the mouse is dozing.

### Battery — `0x04`

The mouse reports its own charge, and the stock tool shows it: `skins/power1`
…`power4.png` and `power_charging.png` are the four level icons and the
charging one. The command takes no arguments and is answered by the mouse,
not the receiver.

`GetPower` is at `0x451310` in `OemDrv.exe`, and its own debug print names
both fields:

```
GetPower: nPower=0x%x, bCharging=0x%x
```

Its caller reads the two results back from the reply buffer at `+9` and `+10`,
and the receive helper (`0x44f910`) copies the report body — the 16 bytes
after the report id — to that buffer at `+4`. So in a reply indexed the way
`device.py` indexes one, with `resp[0]` the echoed command:

| Byte | Meaning |
| --- | --- |
| `resp[5]` | charge, as a whole percentage |
| `resp[6]` | 1 while charging, 0 otherwise |

Confirmed on hardware: `04 00 00 00 02 23 00` while discharging and
`04 00 00 00 02 23 01` with the cable plugged in — 35%, the flag following the
cable. `resp[4]` is `0x02` in every reading taken so far and is not decoded.

The caller **discards a reading above 100 rather than clamping it**
(`cmp $0x64,%edi; ja ...`), so `m693ctl` treats one as a retryable fault too:
the mouse answers this command in states where the figure is not yet good.

An idle receiver answers this command too, with the last reading it took
before the mouse left the 2.4 GHz link — a plausible-looking figure that is
simply out of date. Observed directly: with the mouse on its cable the
receiver still said 35% while the mouse itself reported 85%. Treat a charge as
meaningful only when `GetConnectStatus` says the mouse is actually linked.

There is no unsolicited notification — nothing is pushed when the charge
changes, and the device advertises no HID Battery Strength usage, so the
kernel creates no `power_supply` entry for it. Polling this command is the
only way to see the charge.

## 4. Configuration memory map

Recovered from the driver's own debug strings —

```
GetProfile (0x0C~0x2B) (DPI)(x y mul crc):
GetProfile (0x2C~0x4B) (DPI Color):
GetProfile (0x4C~0x53) (DPI Effect):
GetProfile (0x60~0x9F) (KeyMatrix):
GetProfile (0xA0~0xA7):
GetProfile (0xA9~0xB4):
GetComboKeyData (0x100~0x%x):
```

— and pinned to exact addresses by mapping the decoder's stack offsets back
through the hexdump calls (`eeprom = stack_offset - 0x3A0`).

| Address | Field | Encoding |
| --- | --- | --- |
| `0x00` | polling rate | divisor: 1→1000 Hz, 2→500, 4→250, 8→125 |
| `0x02` | DPI stage count | 1–8 |
| `0x04` | active DPI stage | 0-based |
| `0x06`, `0x08` | spare | zero, valid, never read by the stock tool |
| `0x0A` | lift-off distance | |
| `0x0C`–`0x2B` | DPI table | 8 × (xReg, yReg, mul, crc) |
| `0x2C`–`0x4B` | DPI stage colours | 8 × (R, G, B, crc) |
| `0x4C`–`0x53` | **scroll-wheel effect** | mode, brightness, speed, spare |
| `0x54`–`0x57` | second-zone colour | (R, G, B, crc) — ignored by this model |
| `0x58`–`0x5F` | second-zone mode, brightness, speed, spare | 4 × (value, crc); `0x58` modulates the wheel |
| `0x60`–`0x9F` | button map | 16 × (type, p1, p2, crc) |
| `0xA0`–`0xA6` | **main RGB zone** | (mode, R, G, B, speed, brightness, crc) |
| `0xA7` | spare | zero, valid, never read |
| `0xA9` | debounce (ms) | "Debounce" |
| `0xAB` | **motion sync** | "Motion sync" |
| `0xAD` | dormancy timer | "Sleep Time", stored ÷ 10 |
| `0xAF` | **angle snapping** | "FixLine" |
| `0xB1` | **ripple control** | "Ripple" |
| `0xB3` | turn light off while moving | "Turn OFF Light On Moving" |
| `0xB5`, `0xB7` | real fields, unwritten on this model | read `0xFF` |
| `0x100`–`0x2FF` | combo-key records | 16 × 32 bytes, one per button slot |
| `0x300`–`0x1AFF` | macro records | 16 × 0x180, one per button slot |

The stock tool asks the device for `0xB5` bytes and reads them ten at a time,
so it actually pulls `0x00`–`0xBD`. The names in the table above are its own:
it prints each byte it parses with a label (`LOD=%d`, `Ripple=%d`,
`FixLine=%d`, `Debounce=%d`, `Sleep Time=%d`, …) from a buffer based at
`esp+0x3a0` in the parser at `0x40f4e0`. Anything it does not name was worked
out from the settings writer at `0x411660`, which maps struct fields to
addresses one at a time, and from the dialog code that binds those same fields
to labelled controls.

That chain is what settles `0xAB`. The writer maps field `+0x768` to `0xB1`,
`+0x76c` to `0xAF` and `+0x77c` to `0xAB`; the dialog binds those three fields
to the rows labelled **Ripple control**, **Angle snapping** and **Motion
sync**. The first two agree with the debug names, which is what makes the third
trustworthy — "Ripple control" and "Motion sync" are two separate settings in
the stock UI, not one.

Scalars are stored as `(value, 0x55 - value)`; colours, DPI entries and button
bindings as three bytes plus a checksum; the main RGB zone is a single seven
byte group. Every group independently satisfies `sum == 0x55`.

**The device silently ignores any group whose checksum does not validate.** It
returns success for the write, and the bytes read back, but the setting never
takes effect. This makes partial writes actively dangerous: writing one field
of the seven-byte lighting group as a two-byte scalar puts a checksum byte
where the red channel lives *and* invalidates the group. Every field of a
multi-value group must be read-modify-written together.

### DPI encoding

The sensor (PixArt-style, `Sensor=0x3104`) takes a register value, not a DPI
number. `Cfg.ini`'s `DPISET`/`DPIHW` pair is the lookup table: 500–4000 DPI in
100 steps map to registers `0x0E`–`0x69`, and 4200–8000 reuse the *same*
registers with the doubling byte `0x11` set (low nibble doubles X, high nibble
doubles Y).

A stock device therefore reads back as:

```
0c  0e 0e 00 39   ->  500 DPI
10  1a 1a 00 21   -> 1000 DPI
14  34 34 00 ed   -> 2000 DPI
18  4f 4f 00 b7   -> 3000 DPI
1c  69 69 11 72   -> 4000 x2 = 8000 DPI
```

which is exactly `Cfg.ini`'s `DPI=500,1000,2000,3000,8000`.

### Button map

Two routines in the binary carry the whole mapping, and they agree with each
other, which is what makes the table below trustworthy:

* `keyinfo_to_hardware_code` (`0x410190`) — UI selection → keymap entry
* the unnamed inverse at `0x40ef70` — keymap entry → UI selection

Both are jump-table dispatches on a *keyinfo* dword: a class in the top byte
and a code in the low 24 bits (`0x01000011` = class 1, code 0x11 = left
button). `Cfg.ini`'s `K1_1=0x01,0x11,0x00,0x01` is the same pair plus the
1-based slot it belongs to. `text.xml` supplies the labels.

| Type | Meaning | Parameters |
| --- | --- | --- |
| `0x00` | disabled | |
| `0x01` | mouse button | p1 = mask: 1 L, 2 R, 4 M, 8 back, 0x10 forward |
| `0x02` | DPI action | p1: **1 = DPI loop, 2 = DPI+, 3 = DPI−** |
| `0x04` | fire key | p1 = interval ms, p2 = shot count |
| `0x05` | keyboard / multimedia | payload in the combo region (below) |
| `0x06` | macro | p2 = macro slot |
| `0x07` | polling-rate switch | |
| `0x08` | lighting on/off | |
| `0x09` | profile switch | |
| `0x0A` | DPI lock ("sniper") | p1 = DPI stage index, active while held |

Double and triple click are not their own type: they are fire keys with a
fixed 50 ms interval and 2 or 3 shots (`0x023204` / `0x033204`).

The DPI numbering is the easy thing to get wrong. `tc_dpiadd` ("DPI +") maps to
`0x202`, i.e. **p1 = 2**; p1 = 1 is the loop. Guessing 1/2/3 = up/down/loop
gives a plausible-looking but wrong result.

### Combo-key records — keyboard and multimedia bindings

The keymap entry has no room for a modifier mask plus keys, so anything of that
shape is stored as type `0x05` with the payload in a second region: **16 slots
of 32 bytes from `0x100`**, indexed by the same slot number as the keymap.
The writer computes the address as `(slot + 8) << 5`.

A record is a short event list:

```
[0]        event count
[1+3n..]   flag, code, arg
[last]     checksum — the whole record sums to 0x55, like every other group
```

`flag` is `0x80` for press and `0x40` for release, plus the event kind in its
low three bits:

| Kind | Meaning | `code`, `arg` |
| --- | --- | --- |
| `0` | modifier | HID modifier bit (1 Ctrl, 2 Shift, 4 Alt, 8 Win), 0 |
| `1` | keyboard key | HID usage (page 0x07), 0 |
| `2` | consumer control | usage low byte, usage high byte |

Presses come first, then modifier releases, then key releases in reverse.
Mute is therefore `02 82 e2 00 42 e2 00 cb`, and Shift+A is
`04 80 02 00 81 04 00 40 02 00 41 04 00 c3`. Both were written to a real mouse
and produce exactly those keystrokes.

The multimedia entries in the stock UI are just fixed consumer usages, from the
switch at `0x410340`:

| Function | Usage | Function | Usage |
| --- | --- | --- | --- |
| Media Player | `0x183` | Email | `0x18A` |
| Play/Pause | `0x0CD` | Calculator | `0x192` |
| Stop play | `0x0B7` | Explorer | `0x194` |
| Previous | `0x0B6` | Search | `0x221` |
| Next | `0x0B5` | Home page | `0x223` |
| Volume up | `0x0E9` | Back / Forward (web) | `0x224` / `0x225` |
| Volume down | `0x0EA` | Stop web | `0x226` |
| Mute | `0x0E2` | Refresh / Favorites | `0x227` / `0x22A` |

The keyboard usages come from a VK→usage table at `0x5b2e98` (104 two-byte
entries); it is the standard HID keyboard page, so nothing surprising lives
there.

One asymmetry worth knowing: the stock tool *writes* up to the full 32-byte
slot but only ever *reads back* the first 20 bytes of each, so a combination
longer than six events is one it can set and then no longer display. This
implementation reads the whole slot.

### Lighting zones

The mouse lights three places — the two side strips, the logo in the palm
rest, and the scroll wheel — but they are **two** channels, not three. Each
claim below was checked by driving the block and looking at the mouse:

| Block | Drives | Evidence |
| --- | --- | --- |
| `0xA0` | side strips **and** logo, together | set to solid blue; both went blue |
| `0x4C` | scroll wheel | mode `4` there blanks the wheel while the strips stay lit |

There is no way to split the logo from the strips on this model. Both are
offered as separate zones anyway, since that is how the mouse looks, but
they resolve to the same block and every writer deduplicates through
`lighting.ZONE_BLOCKS` so one edit is one write.

The wheel's **colour is not stored with its effect**: it comes from the DPI
stage colour table at `0x2C`, which is what makes it a DPI indicator. With the
strips solid blue and stage 1 red, the wheel shows red. Giving the wheel a
colour of its own therefore means overwriting that table — both front-ends say
so before they do it.

#### `0x54`–`0x5F`, the "second zone"

`EEPROM_To_LogoRGB` (`0x410070`) is handed a pointer to `0x54` and reads mode
at `+4`, brightness at `+6` and speed at `+8` — the same shape as the wheel
block at `0x4C`. A stock device holds `#FF00FF`, brightness `0x80`, speed `3`.

An earlier revision of this document called it dead. That was wrong, and only
looked true because the test changed its *colour*: the colour bytes at `0x54`
really are ignored, but the mode byte at `0x58` reaches the **wheel** — writing
`2` there makes it breathe. Setting `0x4C` to mode `4` blanks the wheel anyway,
so `0x58` modulates a channel `0x4C` owns rather than owning one itself.
Nothing in the CLI or GUI exposes it.

### The main group

`EEPROM_To_MainRGB` (`0x40fe00`) is called with a pointer to `0xA0`; this is
what the stock tool's Lighting page edits.

The main group's layout comes from the decompiled debug print —
`mode=%x, speed=%x, light=%x, color=%x,%x,%x` reading `[0]`, `[4]`, `[5]` and
`[1..3]` — and was confirmed on hardware by writing mode 2 with `FF 00 00` and
observing solid red.

#### Speed is a delay, and the stock range is not the hardware range

The speed byte counts the *wrong way round*: breathing at `10` is visibly
slower than breathing at `2`. `Cfg.ini`'s `LEDParam=3,10,8,255,0,255` caps the
stock tool's slider at 10, but the firmware accepts the whole byte and keeps
slowing down smoothly — checked by eye at `10`, `60` and `255`, the last still
a clean, very slow fade. So the stock slider covers roughly the fast third of
what the mouse can do.

This implementation exposes the full range, mapping its 1–10 slider onto
`[255, 138, 74, 40, 22, 12, 6, 3, 2, 1]` — geometric, because that is how the
change reads: 3 → 4 is obvious, 200 → 201 is not.

The two zones do **not** run at the same rate for the same byte: with both set
to breathing at the same speed the wheel is visibly slower than the strips. No
attempt is made to correct for it, so "sync" matches the stored setting rather
than the visible rate.

#### The two side strips are one chain

In the travelling effects (`single-color-flow`, `streaming`) the right strip
lags the left by a fixed offset — same direction, same rate, shifted in time.
That is the LEDs being wired as **one chain** that runs down one side and back
up the other, so the wave reaching the far side late is the effect working, not
a fault.

There is no phase or direction control to correct it with. The driver has no
string for one, the stock tool exposes no such setting, and writing `1` to
every spare byte in the map (`0x06`, `0x08`, `0x52`, `0x5E`, `0xA7`) changes
nothing about the flow. Evening the two sides up would take host-driven
lighting, which this protocol has no command for — see the note on flash wear
in the README.

The mode numbering is *not* the order in `text.xml`. The Windows UI remaps its
combo index before storing (`0x4110cf`): 1→2, 2→1, 3→0, 4→3, 5→5, 6→7, and
off→4. Inverting that gives the on-device values, which is what this
implementation writes:

| Value | Effect |
| --- | --- |
| `0` | Streaming (rainbow flow) |
| `1` | Breathing |
| `2` | Steady |
| `3` | Neon |
| `4` | Off |
| `5` | Single colour flow |
| `7` | Colourful breathing |

`6` is unused. A stock device ships on mode `0` with colour `FF00FF`, which is
the cycling rainbow it shows out of the box — note that the cycling modes
ignore the stored colour entirely, so changing the colour appears to do
nothing until the mode is also changed.

### Macros

Like combo records, macros are stored **per button slot**: record *n* lives at
`0x300 + n * 0x180` and belongs to key slot *n*. There is no macro table and no
id — the mouse holds exactly as many macros as it has buttons, and the stock
tool keeps its real library in `.jmm` files on disk, copying one into a slot
when you bind it.

```
+0x00        name length in bytes (2 × characters, max 30)
+0x01..0x1E  name, UTF-16LE, up to 15 characters
+0x1F        event count (max 70)
+0x20..      events, 5 bytes each
+0x20+5n     checksum, then three zero bytes
```

Each event is `flag, code, code_high, delay_high, delay_low`. The delay is the
pause *after* the event, in milliseconds, and is the one **big-endian** field
in the whole protocol. `flag` is `0x80` press / `0x40` release plus the kind:

| Kind | Meaning | `code` |
| --- | --- | --- |
| `0` | keyboard modifier | HID modifier bit |
| `1` | keyboard key | HID usage, page 0x07 |
| `4` | mouse button | 1 L, 2 R, 4 M, 8 back, 0x10 forward |
| `5` | multimedia | 16-bit consumer usage in `code`/`code_high` |

Kind 5 is the exception: its flag byte is a bare `0x05` with no press/release
bit, because the firmware treats a consumer key as a single tap.

**The checksum does not cover the name.** `StMacro_To_HdMacro` (`0x410960`)
builds the record with the name area still zeroed, the caller checksums that,
and only *then* writes the name over the front of it. That looks like a bug in
their encoder, but it is the only thing the firmware has ever been fed, so it
is what this implementation reproduces — and a macro built this way does play
back correctly on a real mouse.

How often a macro repeats is not in the record at all. It is the `p2` byte of
the button's keymap entry, which is `(0x06, slot, repeat)`:

| `p2` | Behaviour |
| --- | --- |
| `1`–`0xFD` | play that many times |
| `0xFE` | toggle: start on press, stop on the next press |
| `0xFF` | repeat while the button is held |

Confirmed on hardware — `p2 = 9` played a two-keystroke macro nine times, and
`p2 = 1` played it once.

### `.jmm` files

The Windows tool's exported macros are a straight dump of its in-memory macro
struct: exactly **834 bytes**, starting with the magic dword `0xFACB99A0`
(anything else gets "This is not a valid macro file"). The name is a wide
string at `0x14`; events start at `0x6A` and are 8 bytes each —
`uint16 virtual_key, uint8 flags, uint8 pad, uint32 delay_ms` — terminated by
an all-zero entry. `flags` bit 0 is press, bit 1 marks the code as a consumer
usage rather than a virtual key. `m693ctl macro --load` reads these directly.

## 5. Behaviour notes

* Writes take effect **immediately** — there is no separate apply or commit
  step, and the change survives a re-plug.
* The 2.4 GHz link drops packets; every command needs retrying. The stock
  driver retries too (`WriteEEPROM Failed / nRetry=%d`).
* **A reply is not identified by its command byte alone.** With retries in
  play, a late reply to an earlier read can arrive while a second read is
  outstanding and be mistaken for its answer, silently returning another
  address's contents. Replies must also be matched on the echoed address and
  length in `resp[2..4]`.
* An idle mouse stops relaying configuration commands until it is moved. The
  stock tool shows "The mouse is now offline, please move or power on the
  mouse." for exactly this case.
