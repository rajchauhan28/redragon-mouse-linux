"""Lighting — per-zone effect, colour, brightness/speed, presets, and preview."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from m693 import lighting as L
from m693 import presets as PRESETS
from m693 import profile as P

from ..model import ProfileModel
from ..widgets.controls import SliderRow
from ..widgets.lighting import ColorWheel, MousePreview, PredefinedColors

DORMANCY_CHOICES = [
    ("30 Sec", 30), ("1 Min", 60), ("2 Min", 120), ("5 Min", 300),
    ("10 Min", 600), ("30 Min", 1800), ("Never", 0),
]

# Order shown to the user; matches the stock tool's list.
MODE_ORDER = ["steady", "breathing", "streaming", "neon",
              "single-color-flow", "colorful-breathing", "off"]


def _panel() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 14, 18, 14)
    layout.setSpacing(10)
    return frame, layout


class LightingTab(QWidget):
    def __init__(self, model: ProfileModel):
        super().__init__()
        self.model = model
        self._syncing = False

        row = QHBoxLayout(self)
        row.setContentsMargins(18, 8, 18, 14)
        row.setSpacing(12)
        row.addWidget(self._build_left(), 0)

        right = QVBoxLayout()
        right.setSpacing(12)
        self.preview = MousePreview()
        right.addWidget(self.preview, 1)
        right.addWidget(self._build_presets(), 0)
        right.addWidget(self._build_options(), 0)
        row.addLayout(right, 1)

        model.loaded.connect(self._on_loaded)
        model.changed.connect(lambda _: self.sync())

    # -- construction ------------------------------------------------------
    def _build_left(self) -> QFrame:
        frame, layout = _panel()
        frame.setFixedWidth(430)

        title = QLabel("Lighting")
        title.setObjectName("PanelTitle")
        layout.addWidget(title)

        layout.addWidget(self._build_sync())

        zone_row = QHBoxLayout()
        zone_row.addWidget(QLabel("Zone"))
        zone_row.addSpacing(14)
        self.zone = QComboBox()
        for name in L.ZONES:
            self.zone.addItem(L.ZONE_LABELS[name], name)
        self.zone.currentIndexChanged.connect(self._on_zone)
        zone_row.addWidget(self.zone, 1)
        layout.addLayout(zone_row)

        self.zone_note = QLabel()
        self.zone_note.setObjectName("Hint")
        self.zone_note.setWordWrap(True)
        layout.addWidget(self.zone_note)
        layout.addSpacing(6)

        effect = QHBoxLayout()
        effect.addWidget(QLabel("LED Effect"))
        effect.addSpacing(14)
        self.mode = QComboBox()
        for name in MODE_ORDER:
            self.mode.addItem(name.replace("-", " ").title(), P.LED_MODE_NAMES[name])
        self.mode.currentIndexChanged.connect(self._on_mode)
        effect.addWidget(self.mode, 1)
        layout.addLayout(effect)
        layout.addSpacing(10)

        self.wheel = ColorWheel(190)
        self.wheel.color_changed.connect(self._on_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(1)
        wheel_row.addWidget(self.wheel)
        wheel_row.addStretch(1)
        layout.addLayout(wheel_row)
        layout.addSpacing(10)

        caption = QLabel("Predefined Color")
        caption.setObjectName("Hint")
        layout.addWidget(caption)

        self.palette = PredefinedColors()
        self.palette.color_picked.connect(self._on_color)
        layout.addWidget(self.palette, 0, Qt.AlignHCenter)
        layout.addStretch(1)
        return frame

    def _build_sync(self) -> QWidget:
        """The two meaningful sync states, as radio buttons.

        There is no third option to offer.  The strips and the logo are one
        channel, so "strips and logo together" is not something that can be
        switched on or off - it is simply true - and the only real choice is
        whether the wheel comes along.  Both are spelled out rather than left
        implicit, so the state is readable at a glance.
        """
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)

        self.sync_pair = QRadioButton("Side strips + palm logo")
        self.sync_pair.setToolTip(
            "The wheel keeps its own effect. Pick which zone the controls "
            "below apply to with the Zone drop-down."
        )
        self.sync_all = QRadioButton("All three zones together")
        self.sync_all.setToolTip(
            "Strips, logo and wheel all follow the controls below."
        )

        self.sync_group = QButtonGroup(self)
        self.sync_group.addButton(self.sync_pair, 0)
        self.sync_group.addButton(self.sync_all, 1)
        self.sync_group.idToggled.connect(self._on_sync_toggled)

        column.addWidget(self.sync_pair)
        column.addWidget(self.sync_all)
        return box

    def _build_presets(self) -> QFrame:
        frame, layout = _panel()

        title = QLabel("Presets")
        title.setObjectName("PanelTitle")
        layout.addWidget(title)

        row = QHBoxLayout()
        self.presets = QComboBox()
        row.addWidget(self.presets, 1)

        self.apply_preset = QPushButton("Apply")
        self.apply_preset.clicked.connect(self._on_apply_preset)
        row.addWidget(self.apply_preset)

        self.save_preset = QPushButton("Save current…")
        self.save_preset.clicked.connect(self._on_save_preset)
        row.addWidget(self.save_preset)

        self.delete_preset = QPushButton("Delete")
        self.delete_preset.clicked.connect(self._on_delete_preset)
        row.addWidget(self.delete_preset)
        layout.addLayout(row)

        hint = QLabel(
            "A preset stores every zone's effect, colour, brightness and speed. "
            "The mouse has no programmable effect engine, so these are "
            "combinations of its seven built-in effects."
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._reload_presets()
        self.presets.currentIndexChanged.connect(self._update_preset_buttons)
        return frame

    def _build_options(self) -> QFrame:
        frame, layout = _panel()

        self.brightness = SliderRow("Brightness", 0, 255)
        self.brightness.value_changed.connect(self._on_brightness)
        layout.addWidget(self.brightness)

        # The device stores a delay, so the slider is flipped: see
        # lighting.speed_to_raw.  Dragging right must make the effect faster.
        self.speed = SliderRow("Speed", L.SPEED_MIN, L.SPEED_MAX)
        self.speed.value_changed.connect(self._on_speed)
        layout.addWidget(self.speed)

        self.light_off = QCheckBox("Turn off light when moving")
        self.light_off.toggled.connect(self._on_light_off)
        layout.addWidget(self.light_off)

        dormancy = QHBoxLayout()
        self.dormancy = QComboBox()
        for text, seconds in DORMANCY_CHOICES:
            self.dormancy.addItem(text, seconds)
        self.dormancy.setFixedWidth(110)
        self.dormancy.currentIndexChanged.connect(self._on_dormancy)
        dormancy.addWidget(self.dormancy)
        dormancy.addWidget(QLabel("Dormancy time"))
        dormancy.addStretch(1)
        layout.addLayout(dormancy)
        return frame

    # -- zone plumbing -----------------------------------------------------
    def current_zone(self) -> str:
        return self.zone.currentData() or L.ZONE_MAIN

    def all_synced(self) -> bool:
        return self.sync_all.isChecked()

    def _targets(self) -> tuple[str, ...]:
        """Zones an edit applies to."""
        return L.ZONES if self.all_synced() else (self.current_zone(),)

    def _zones_match(self) -> bool:
        # Only the independent channels are worth comparing: the strips and the
        # logo are one channel and so always agree.
        states = [self.model.zone_state(z) for z in L.distinct(L.ZONES)]
        return all(state == states[0] for state in states)

    def _on_loaded(self) -> None:
        # Adopt whatever the mouse already holds: if the wheel already matches
        # the strips, start in all-three mode, otherwise leave it on its own.
        self._syncing = True
        try:
            button = self.sync_all if self._zones_match() else self.sync_pair
            button.setChecked(True)
        finally:
            self._syncing = False
        self.sync()

    # -- model -> view -----------------------------------------------------
    def sync(self) -> None:
        if not self.model.is_loaded:
            return
        profile = self.model.profile()
        zone = self.current_zone()
        state = self.model.zone_state(zone)
        self._syncing = True
        try:
            self.zone.setEnabled(not self.all_synced())
            self.zone_note.setText(
                "All three zones follow these controls."
                if self.all_synced()
                else L.ZONE_NOTES[zone]
            )
            index = self.mode.findData(state.mode)
            if index >= 0:
                self.mode.setCurrentIndex(index)
            self.wheel.set_color(state.color)
            self.brightness.set_value(state.brightness)
            self.speed.set_value(L.speed_from_raw(state.speed))
            self.light_off.setChecked(profile.light_off_when_moving)

            seconds = profile.dormancy_s
            closest = min(
                range(self.dormancy.count()),
                key=lambda i: abs(self.dormancy.itemData(i) - seconds),
            )
            self.dormancy.setCurrentIndex(closest)

            # The preview animates at the rate the user sees, not the delay
            # the device stores.
            self.preview.set_state(
                state.mode, state.color, L.speed_from_raw(state.speed), state.brightness
            )
        finally:
            self._syncing = False

    # -- view -> model -----------------------------------------------------
    def _apply(self, field: str, value) -> None:
        if self._syncing:
            return
        # distinct() collapses the linked strips/logo pair, so editing either
        # issues one write rather than the same one twice.
        for zone in L.distinct(self._targets()):
            if L.ZONE_BLOCKS[zone] == L.BLOCK_MAIN:
                getattr(self.model, "set_led_" + field)(value)
            else:
                getattr(self.model, "set_wheel_" + field)(value)

    def _on_zone(self, _index: int) -> None:
        if not self._syncing:
            self.sync()

    def _on_sync_toggled(self, button_id: int, on: bool) -> None:
        # idToggled fires twice per change, once for each button; only the one
        # being switched on is a decision.
        if self._syncing or not on:
            return
        all_zones = button_id == 1
        if all_zones and not self._confirm_sync():
            self._syncing = True
            try:
                self.sync_pair.setChecked(True)
            finally:
                self._syncing = False
            self.sync()
            return
        if all_zones:
            # Make it true rather than merely claiming it: push the zone the
            # user was last looking at onto the others.
            state = self.model.zone_state(self.current_zone())
            self.model.pause()
            try:
                for zone in L.distinct(L.ZONES):
                    self.model.set_zone(zone, state)
            finally:
                self.model.resume()
        self.sync()

    def _confirm_sync(self) -> bool:
        """The wheel's colour is the DPI stage table, so syncing costs it."""
        answer = QMessageBox.question(
            self,
            "Sync all zones",
            "The scroll wheel takes its colour from the DPI stage colours, so "
            "syncing the zones will set every DPI stage to the same colour and "
            "the wheel will no longer indicate which stage is active.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        return answer == QMessageBox.Yes

    def _on_mode(self, _index: int) -> None:
        self._apply("mode", self.mode.currentData())

    def _on_color(self, rgb: tuple) -> None:
        if self._syncing:
            return
        self.wheel.set_color(rgb)
        self._apply("color", rgb)

    def _on_brightness(self, value: int) -> None:
        self._apply("brightness", value)

    def _on_speed(self, value: int) -> None:
        self._apply("speed", L.speed_to_raw(value))

    def _on_light_off(self, on: bool) -> None:
        if not self._syncing:
            self.model.set_light_off_when_moving(on)

    def _on_dormancy(self, _index: int) -> None:
        if not self._syncing:
            self.model.set_dormancy(self.dormancy.currentData())

    # -- presets -----------------------------------------------------------
    def _reload_presets(self, select: str = "") -> None:
        # Rebuilding the list fires currentIndexChanged repeatedly; the button
        # state is refreshed explicitly at the end instead.
        blocked = self.presets.blockSignals(True)
        self.presets.clear()
        try:
            available = PRESETS.load_all()
        except ValueError as exc:
            QMessageBox.warning(self, "Presets", str(exc))
            available = list(PRESETS.BUILTIN_PRESETS)
        for preset in available:
            label = preset.name
            if PRESETS.is_builtin(preset.name):
                label += "  (built-in)"
            self.presets.addItem(label, preset.name)
        if select:
            index = self.presets.findData(select)
            if index >= 0:
                self.presets.setCurrentIndex(index)
        self.presets.blockSignals(blocked)
        self._update_preset_buttons()

    def _update_preset_buttons(self) -> None:
        name = self.presets.currentData()
        # Built-ins are shipped with the package; deleting one would only come
        # back on the next launch, so the button is off rather than lying.
        self.delete_preset.setEnabled(bool(name) and not PRESETS.is_builtin(name))

    def _on_apply_preset(self) -> None:
        name = self.presets.currentData()
        preset = PRESETS.find(name) if name else None
        if preset is None:
            return
        # A preset that carries a captured stage table restores it exactly, so
        # there is nothing to warn about; only a flat wheel colour costs the
        # DPI colour coding.
        if (
            L.ZONE_WHEEL in preset.zones
            and not preset.dpi_colors
            and not self._confirm_preset_colors()
        ):
            return
        self.model.apply_preset(preset)
        self._on_loaded()

    def _confirm_preset_colors(self) -> bool:
        answer = QMessageBox.question(
            self,
            "Apply preset",
            "This preset sets a wheel colour, which is stored as the DPI stage "
            "colours - applying it will replace them.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        return answer == QMessageBox.Yes

    def _on_save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save preset", "Name")
        name = name.strip()
        if not ok or not name:
            return
        if PRESETS.is_builtin(name):
            QMessageBox.warning(
                self, "Save preset",
                f"{name!r} is a built-in preset name; pick another.",
            )
            return
        preset = L.Preset.capture(name, self.model.profile())
        try:
            PRESETS.store(preset)
        except OSError as exc:
            QMessageBox.warning(self, "Save preset", f"Could not save: {exc}")
            return
        self._reload_presets(select=name)

    def _on_delete_preset(self) -> None:
        name = self.presets.currentData()
        if not name or PRESETS.is_builtin(name):
            return
        if QMessageBox.question(
            self, "Delete preset", f"Delete {name!r}?"
        ) != QMessageBox.Yes:
            return
        PRESETS.remove(name)
        self._reload_presets()
