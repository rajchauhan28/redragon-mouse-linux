"""Shadow copy of the mouse's configuration memory, with dirty-group flushing.

Widgets never write to the device.  They mutate this model, which rewrites the
affected checksummed group, marks it dirty, and lets a short timer coalesce
everything into as few write commands as possible.  Dragging a slider therefore
costs one write run rather than one write per pixel.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from m693 import keys as K
from m693 import lighting as L
from m693 import macro as MACRO
from m693 import profile as P
from m693.device import checksum

FLUSH_DELAY_MS = 250


class ProfileModel(QObject):
    """The single source of truth for what the mouse should contain."""

    loaded = Signal()  # a fresh image arrived from the device
    changed = Signal(str)  # a named field changed (for cross-widget sync)
    dirty_changed = Signal(bool)
    flush_requested = Signal(list, bytes)  # runs, image  -> DeviceWorker
    combo_flush_requested = Signal(list)  # [(addr, bytes)] -> DeviceWorker
    macro_flush_requested = Signal(list)

    def __init__(self):
        super().__init__()
        self._image = bytearray(P.PROFILE_LEN)
        self._combo = bytearray(K.COMBO_REGION_LEN)
        self._combo_dirty: set[int] = set()
        # Macros are held per slot rather than as one image: the region is 6 kB,
        # which is far too much to mirror over a link that moves ten bytes at a
        # time, and nothing needs the slots we have not touched.
        self._macros: dict[int, MACRO.Macro] = {}
        self._macro_dirty: set[int] = set()
        self._dirty: set[int] = set()
        self._loaded = False
        self._paused = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(FLUSH_DELAY_MS)
        self._timer.timeout.connect(self.flush)

    # -- state -------------------------------------------------------------
    @property
    def image(self) -> bytes:
        return bytes(self._image)

    @property
    def combo_image(self) -> bytes:
        return bytes(self._combo)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def is_dirty(self) -> bool:
        return bool(self._dirty or self._combo_dirty or self._macro_dirty)

    def profile(self) -> P.Profile:
        return P.Profile(bytes(self._image))

    def load(self, image: bytes) -> None:
        """Adopt a freshly read image.

        Anything still dirty is a local edit that has not landed yet, so it is
        replayed on top rather than being silently discarded.
        """
        pending = {addr: self._group_bytes(addr) for addr in self._dirty}
        self._image = bytearray(image[: P.PROFILE_LEN].ljust(P.PROFILE_LEN, b"\x00"))
        for addr, data in pending.items():
            self._image[addr : addr + len(data)] = data
        self._loaded = True
        self.loaded.emit()
        if self._dirty:
            self._timer.start()

    def apply_image(self, image: bytes, combo: bytes = b"") -> None:
        """Adopt an image from elsewhere (an import) and write all of it back.

        Unlike :meth:`load`, which mirrors what the mouse already holds, this
        marks every decoded group dirty so the whole profile is pushed out.
        """
        self._image = bytearray(image[: P.PROFILE_LEN].ljust(P.PROFILE_LEN, b"\x00"))
        self._dirty = {addr for addr, _ in P.GROUPS if P.is_writable(addr)}
        if combo:
            self._combo = bytearray(
                combo[: K.COMBO_REGION_LEN].ljust(K.COMBO_REGION_LEN, b"\x00")
            )
            self._combo_dirty = {
                slot
                for slot in range(K.COMBO_SLOTS)
                if K.combo_is_valid(self.combo_record(slot))
            }
        self._loaded = True
        self.loaded.emit()
        self.dirty_changed.emit(True)
        self._timer.start()

    def _group_bytes(self, addr: int) -> bytes:
        size = P.GROUP_SIZES.get(addr, 0)
        return bytes(self._image[addr : addr + size])

    # -- mutation ----------------------------------------------------------
    def set_group(self, addr: int, values: list[int] | tuple[int, ...], field: str = "") -> None:
        """Rewrite one checksummed group and mark it dirty."""
        size = P.GROUP_SIZES.get(addr)
        if size is None:
            raise KeyError(f"no group at {addr:#04x}")
        if not P.is_writable(addr):
            raise ValueError(f"{addr:#04x} is in an undecoded region")
        body = bytes(v & 0xFF for v in values)
        if len(body) != size - 1:
            raise ValueError(f"group at {addr:#04x} takes {size - 1} value bytes")
        block = body + bytes([checksum(body)])
        if bytes(self._image[addr : addr + size]) == block:
            return
        self._image[addr : addr + size] = block
        was_dirty = bool(self._dirty)
        self._dirty.add(addr)
        if field:
            self.changed.emit(field)
        if not was_dirty:
            self.dirty_changed.emit(True)
        if not self._paused:
            self._timer.start()

    def set_scalar(self, addr: int, value: int, field: str = "") -> None:
        self.set_group(addr, [value], field)

    def set_record(self, addr: int, a: int, b: int, c: int, field: str = "") -> None:
        self.set_group(addr, [a, b, c], field)

    # -- typed setters -----------------------------------------------------
    # Tabs go through these rather than poking addresses, so the encoding of a
    # field lives in exactly one place.

    def set_polling_rate(self, hz: int) -> None:
        self.set_scalar(P.ADDR_POLL_RATE, P.POLL_DIVISORS[hz], "polling_rate")

    def set_lod(self, value: int) -> None:
        self.set_scalar(P.ADDR_LOD, value, "lod")

    def set_debounce(self, ms: int) -> None:
        self.set_scalar(P.ADDR_DEBOUNCE, ms, "debounce")

    def set_dormancy(self, seconds: int) -> None:
        self.set_scalar(P.ADDR_DORMANCY, round(seconds / 10), "dormancy")

    def set_angle_snapping(self, on: bool) -> None:
        self.set_scalar(P.ADDR_ANGLE_SNAP, int(on), "angle_snapping")

    def set_motion_sync(self, on: bool) -> None:
        self.set_scalar(P.ADDR_MOTION_SYNC, int(on), "motion_sync")

    def set_ripple_control(self, on: bool) -> None:
        self.set_scalar(P.ADDR_RIPPLE, int(on), "ripple_control")

    def set_light_off_when_moving(self, on: bool) -> None:
        self.set_scalar(P.ADDR_LIGHT_OFF_MOVE, int(on), "light_off_when_moving")

    def set_dpi_count(self, count: int) -> None:
        count = max(1, min(P.MAX_DPI_STAGES, count))
        self.set_scalar(P.ADDR_DPI_COUNT, count, "dpi_count")
        if self.profile().dpi_active >= count:
            self.set_dpi_active(count - 1)

    def set_dpi_active(self, index: int) -> None:
        self.set_scalar(P.ADDR_DPI_ACTIVE, index, "dpi_active")

    def set_dpi_stage(self, index: int, x_dpi: int, y_dpi: int | None = None) -> None:
        y_dpi = x_dpi if y_dpi is None else y_dpi
        x_reg, x_mul = P.dpi_to_reg(x_dpi)
        y_reg, y_mul = P.dpi_to_reg(y_dpi)
        self.set_record(
            P.ADDR_DPI_TABLE + 4 * index,
            x_reg,
            y_reg,
            (x_mul & 0x0F) | (y_mul & 0xF0),
            "dpi_stage",
        )

    def set_dpi_color(self, index: int, rgb: tuple[int, int, int]) -> None:
        self.set_record(P.ADDR_DPI_COLORS + 4 * index, *rgb, field="dpi_color")

    def _set_led_fields(self, updates: dict[int, int]) -> None:
        base = P.ADDR_LED_MAIN
        body = list(self._image[base : base + 6])
        for offset, value in updates.items():
            body[offset] = value & 0xFF
        self.set_group(base, body, "led")

    def set_led_mode(self, mode: int) -> None:
        self._set_led_fields({P.LED_MODE_OFS: mode})

    def set_led_color(self, rgb: tuple[int, int, int]) -> None:
        r, g, b = rgb
        self._set_led_fields({P.LED_R_OFS: r, P.LED_G_OFS: g, P.LED_B_OFS: b})

    def set_led_speed(self, value: int) -> None:
        self._set_led_fields({P.LED_SPEED_OFS: value})

    def set_led_brightness(self, value: int) -> None:
        self._set_led_fields({P.LED_BRIGHT_OFS: value})

    def set_dpi_effect_mode(self, mode: int) -> None:
        self.set_scalar(P.ADDR_DPI_EFFECT, mode, "dpi_effect")

    def set_dpi_effect_brightness(self, value: int) -> None:
        self.set_scalar(P.ADDR_DPI_EFFECT + 2, value, "dpi_effect")

    def set_dpi_effect_speed(self, value: int) -> None:
        self.set_scalar(P.ADDR_DPI_EFFECT + 4, value, "dpi_effect")

    # -- zones -------------------------------------------------------------
    # The DPI-effect block is the wheel's lighting; these are the names the
    # lighting tab uses, so it does not have to know that.
    set_wheel_mode = set_dpi_effect_mode
    set_wheel_brightness = set_dpi_effect_brightness
    set_wheel_speed = set_dpi_effect_speed

    def set_wheel_color(self, rgb: tuple[int, int, int]) -> None:
        """The wheel's colour is the DPI stage table, so all of it moves."""
        for index in range(P.MAX_DPI_STAGES):
            self.set_dpi_color(index, rgb)

    def zone_state(self, zone: str) -> L.ZoneState:
        return L.read(self.profile(), zone)

    def set_zone(self, zone: str, state: L.ZoneState, *, color: bool = True) -> None:
        """Apply a whole zone in one flush, rather than field by field."""
        self.pause()
        try:
            block = L.ZONE_BLOCKS.get(zone)
            if block == L.BLOCK_MAIN:
                self._set_led_fields({
                    P.LED_MODE_OFS: state.mode,
                    P.LED_R_OFS: state.color[0],
                    P.LED_G_OFS: state.color[1],
                    P.LED_B_OFS: state.color[2],
                    P.LED_SPEED_OFS: state.speed,
                    P.LED_BRIGHT_OFS: state.brightness,
                })
            elif block == L.BLOCK_WHEEL:
                self.set_wheel_mode(state.mode)
                self.set_wheel_brightness(state.brightness)
                self.set_wheel_speed(state.speed)
                if color:
                    self.set_wheel_color(state.color)
            else:
                raise ValueError(f"unknown zone {zone!r}")
        finally:
            self.resume()

    def apply_preset(self, preset: L.Preset, *, color: bool = True) -> None:
        self.pause()
        try:
            # distinct() keeps the linked strips/logo pair from writing the
            # same group twice.
            for zone in L.distinct(preset.zones):
                state = preset.zones[zone]
                # A captured stage table is written below and must not be
                # flattened by the wheel's single colour on the way past.
                flat = color and not (zone == L.ZONE_WHEEL and preset.dpi_colors)
                self.set_zone(zone, state, color=flat)
            if color and preset.dpi_colors and L.ZONE_WHEEL in preset.zones:
                for index, rgb in enumerate(preset.dpi_colors[: P.MAX_DPI_STAGES]):
                    self.set_dpi_color(index, rgb)
        finally:
            self.resume()

    # -- buttons -----------------------------------------------------------
    def combo_record(self, slot: int) -> bytes:
        base = K.COMBO_STRIDE * slot
        return bytes(self._combo[base : base + K.COMBO_STRIDE])

    def binding(self, slot: int) -> tuple[str, dict]:
        return K.identify(self.profile().keymap[slot], self.combo_record(slot))

    def load_combo(self, data: bytes) -> None:
        """Adopt the combo region, keeping records that have not landed yet."""
        pending = {slot: self.combo_record(slot) for slot in self._combo_dirty}
        self._combo = bytearray(
            data[: K.COMBO_REGION_LEN].ljust(K.COMBO_REGION_LEN, b"\x00")
        )
        for slot, record in pending.items():
            base = K.COMBO_STRIDE * slot
            self._combo[base : base + K.COMBO_STRIDE] = record
        self.changed.emit("keymap")
        if self._combo_dirty:
            self._timer.start()

    def macro(self, slot: int) -> MACRO.Macro | None:
        return self._macros.get(slot)

    def set_macro(self, slot: int, macro: MACRO.Macro, repeat: int = 1) -> None:
        """Store a macro in a button's slot and point the button at it.

        The record has to land before the keymap entry, for the same reason a
        combo record does: an entry pointing at a slot that has not been
        written yet is a button that does nothing.
        """
        macro.to_record()  # validates length before anything is queued
        self._macros[slot] = macro
        was_dirty = self.is_dirty
        self._macro_dirty.add(slot)
        if not was_dirty:
            self.dirty_changed.emit(True)
        self.set_binding(slot, K.build("macro", slot=slot, repeat=repeat))

    def set_binding(self, slot: int, binding: K.Binding) -> None:
        """Assign a button.

        A combo binding has to reach the mouse before the keymap entry that
        points at it, so the payload is queued first and the two are flushed
        in that order.
        """
        if not 0 <= slot < P.KEY_SLOTS:
            raise ValueError("key slot out of range")
        if binding.combo is not None:
            base = K.COMBO_STRIDE * slot
            record = bytes(binding.combo).ljust(K.COMBO_STRIDE, b"\x00")
            if bytes(self._combo[base : base + K.COMBO_STRIDE]) != record:
                self._combo[base : base + K.COMBO_STRIDE] = record
                was_dirty = self.is_dirty
                self._combo_dirty.add(slot)
                if not was_dirty:
                    self.dirty_changed.emit(True)
                if not self._paused:
                    self._timer.start()
        self.set_record(
            P.ADDR_KEYMAP + 4 * slot, *binding.entry, field="keymap"
        )

    # -- flushing ----------------------------------------------------------
    def pause(self) -> None:
        """Batch several edits into one flush (e.g. rewriting all DPI stages)."""
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        if self.is_dirty:
            self._timer.start()

    def flush(self) -> None:
        # Payload regions first: a keymap entry of type 0x05 or 0x06 is
        # meaningless until the record it refers to is in place.
        if self._macro_dirty:
            blocks = [
                (MACRO.ADDR_MACROS + MACRO.MACRO_STRIDE * slot,
                 self._macros[slot].to_record())
                for slot in sorted(self._macro_dirty)
                if slot in self._macros
            ]
            if blocks:
                self.macro_flush_requested.emit(blocks)
        if self._combo_dirty:
            blocks = []
            for slot in sorted(self._combo_dirty):
                record = self.combo_record(slot)
                blocks.append(
                    (K.ADDR_COMBO + K.COMBO_STRIDE * slot,
                     record[: K.combo_length(record)])
                )
            self.combo_flush_requested.emit(blocks)
        if not self._dirty:
            return
        runs = P.merge_runs(self._dirty)
        if runs:
            self.flush_requested.emit(runs, bytes(self._image))

    def confirm(self, runs: list) -> None:
        """Groups the device acknowledged; drop them from the dirty set."""
        for addr, length in runs:
            for group_addr, size in P.GROUPS:
                if addr <= group_addr < addr + length:
                    self._dirty.discard(group_addr)
        if not self.is_dirty:
            self.dirty_changed.emit(False)

    def confirm_blocks(self, blocks: list) -> None:
        """Raw blocks the device acknowledged, from either payload region."""
        for addr, _data in blocks:
            if addr >= MACRO.ADDR_MACROS:
                self._macro_dirty.discard(
                    (addr - MACRO.ADDR_MACROS) // MACRO.MACRO_STRIDE
                )
            else:
                self._combo_dirty.discard((addr - K.ADDR_COMBO) // K.COMBO_STRIDE)
        if not self.is_dirty:
            self.dirty_changed.emit(False)

    # Kept for callers that only ever deal with combo records.
    confirm_combo = confirm_blocks

    def retry_later(self) -> None:
        """A flush failed; keep the edits and try again shortly."""
        if self.is_dirty:
            self._timer.start(FLUSH_DELAY_MS * 4)
