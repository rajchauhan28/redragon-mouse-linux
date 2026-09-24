"""Key Settings: what each button does, plus debounce and profile transfer."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from m693 import keys as K
from m693 import macro as M
from m693 import profile as P

from ..model import ProfileModel
from ..widgets.mouseview import MouseView

# Which memory slot each visible row drives, and what to call it.  Both come
# from the stock tool's own key list (Cfg.ini K1_1..K7_1, whose fourth field is
# the 1-based slot); the artwork has exactly these seven callouts.
ROWS = [
    (0, "Left button"),
    (1, "Right button"),
    (2, "Wheel click"),
    (4, "Forward (thumb)"),
    (3, "Back (thumb)"),
    (9, "Top button, front"),
    (10, "Top button, rear"),
]

# Functions that need an argument before they can be written.
PARAMETRIC = {"fire", "dpi-lock", "macro", "key-combo"}


class ParameterDialog(QDialog):
    """Collects the argument for fire keys, DPI lock and key combinations."""

    def __init__(self, function_id: str, params: dict, parent=None):
        super().__init__(parent)
        self.function_id = function_id
        self.setWindowTitle(K.FUNCTIONS_BY_ID[function_id].label)
        form = QFormLayout()
        self._widgets: dict[str, QWidget] = {}

        if function_id == "fire":
            interval = QSpinBox()
            interval.setRange(1, 255)
            interval.setSuffix(" ms")
            interval.setValue(int(params.get("interval", 10)))
            shots = QSpinBox()
            shots.setRange(1, 255)
            shots.setValue(int(params.get("shots", 3)))
            form.addRow("Interval", interval)
            form.addRow("Clicks", shots)
            self._widgets = {"interval": interval, "shots": shots}
        elif function_id == "dpi-lock":
            stage = QSpinBox()
            stage.setRange(1, P.MAX_DPI_STAGES)
            stage.setValue(int(params.get("stage", 0)) + 1)
            form.addRow("Hold at DPI stage", stage)
            self._widgets = {"stage": stage}
        elif function_id == "key-combo":
            modifiers = int(params.get("modifiers", 0))
            boxes = QHBoxLayout()
            self._mods = {}
            for bit in (K.MOD_CTRL, K.MOD_SHIFT, K.MOD_ALT, K.MOD_SUPER):
                box = QCheckBox(K.MODIFIER_LABELS[bit])
                box.setChecked(bool(modifiers & bit))
                boxes.addWidget(box)
                self._mods[bit] = box
            holder = QWidget()
            holder.setLayout(boxes)
            form.addRow("Modifiers", holder)

            existing = [K.USAGE_KEYS.get(u, "") for u in params.get("keys", [])]
            self._keys = []
            # Three key slots: the firmware's record holds at most six events,
            # which is three presses plus their releases.
            for i in range(3):
                combo = QComboBox()
                combo.addItem("(none)", "")
                for name in sorted(K.KEYBOARD_USAGES):
                    combo.addItem(name.upper(), name)
                if i < len(existing) and existing[i]:
                    combo.setCurrentIndex(combo.findData(existing[i]))
                form.addRow(f"Key {i + 1}" if i else "Key", combo)
                self._keys.append(combo)
        elif function_id == "macro":
            # The slot is not a choice: the mouse stores one macro per button,
            # at the address matching that button's own slot. Only how often it
            # repeats is up to the user.
            repeat = QSpinBox()
            repeat.setRange(1, M.REPEAT_MAX)
            repeat.setValue(min(int(params.get("repeat", 1)), M.REPEAT_MAX))
            mode = QComboBox()
            mode.addItem("A fixed number of times", 0)
            mode.addItem("Repeat while held", M.REPEAT_WHILE_HELD)
            mode.addItem("Toggle on/off", M.REPEAT_TOGGLE)
            stored = int(params.get("repeat", 1))
            if stored in (M.REPEAT_WHILE_HELD, M.REPEAT_TOGGLE):
                mode.setCurrentIndex(mode.findData(stored))
            mode.currentIndexChanged.connect(
                lambda _: repeat.setEnabled(mode.currentData() == 0)
            )
            repeat.setEnabled(mode.currentData() == 0)
            form.addRow("Play", mode)
            form.addRow("Times", repeat)
            note = QLabel("Record the macro itself on the Macro tab.")
            note.setObjectName("Hint")
            note.setWordWrap(True)
            form.addRow(note)
            self._widgets = {"repeat": repeat, "mode": mode}

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def parameters(self) -> dict:
        if self.function_id == "fire":
            return {
                "interval": self._widgets["interval"].value(),
                "shots": self._widgets["shots"].value(),
            }
        if self.function_id == "dpi-lock":
            return {"stage": self._widgets["stage"].value() - 1}
        if self.function_id == "macro":
            mode = self._widgets["mode"].currentData()
            return {"repeat": mode or self._widgets["repeat"].value()}
        if self.function_id == "key-combo":
            modifiers = sum(bit for bit, box in self._mods.items() if box.isChecked())
            chosen = [c.currentData() for c in self._keys if c.currentData()]
            return {"modifiers": modifiers, "keys": chosen}
        return {}


class KeyRow(QWidget):
    """One button: its number, its name, and the function it performs."""

    changed = Signal(int, str)  # row, function id
    focused = Signal(int)

    def __init__(self, row: int, slot: int, name: str):
        super().__init__()
        self.row = row
        self.slot = slot

        badge = QLabel(str(row + 1))
        badge.setObjectName("Callout")
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(24, 24)

        caption = QLabel(name)
        caption.setFixedWidth(126)

        self.combo = QComboBox()
        self._populate()
        self.combo.currentIndexChanged.connect(
            lambda _: self.changed.emit(self.row, self.combo.currentData())
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(10)
        layout.addWidget(badge)
        layout.addWidget(caption)
        layout.addWidget(self.combo, 1)

    def _populate(self) -> None:
        for group in K.GROUP_ORDER:
            members = [fn for fn in K.FUNCTIONS if fn.group == group]
            if not members:
                continue
            self.combo.insertSeparator(self.combo.count())
            for fn in members:
                label = fn.label + ("…" if fn.id in PARAMETRIC else "")
                self.combo.addItem(label, fn.id)
                if fn.id == "macro":
                    self.combo.setItemData(
                        self.combo.count() - 1,
                        "Record and assign macros on the Macro tab",
                        Qt.ToolTipRole,
                    )

    def show_binding(self, function_id: str, summary: str) -> None:
        index = self.combo.findData(function_id)
        blocked = self.combo.blockSignals(True)
        self.combo.setCurrentIndex(index if index >= 0 else -1)
        self.combo.blockSignals(blocked)
        self.combo.setToolTip(summary)

    def enterEvent(self, event):
        self.focused.emit(self.row)


class KeysTab(QWidget):
    """Button assignments, debounce, and profile export/import."""

    export_requested = Signal(str)
    import_requested = Signal(str)
    restore_requested = Signal()

    def __init__(self, model: ProfileModel):
        super().__init__()
        self.model = model
        self._syncing = False
        self._current = 0

        buttons_panel = QFrame()
        buttons_panel.setObjectName("Panel")
        left = QVBoxLayout(buttons_panel)
        left.setContentsMargins(20, 16, 20, 16)
        left.setSpacing(6)
        heading = QLabel("Button assignments")
        heading.setObjectName("PanelTitle")
        left.addWidget(heading)

        self.rows: list[KeyRow] = []
        for index, (slot, name) in enumerate(ROWS):
            row = KeyRow(index, slot, name)
            row.changed.connect(self._on_function_chosen)
            row.focused.connect(self._set_current)
            left.addWidget(row)
            self.rows.append(row)
        left.addStretch(1)

        self.debounce = QSpinBox()
        self.debounce.setRange(0, 30)
        self.debounce.setSuffix(" ms")
        self.debounce.setToolTip(
            "How long the firmware waits before believing a second click. "
            "Raise it if a worn switch double-fires."
        )
        self.debounce.valueChanged.connect(self._on_debounce)
        debounce_row = QHBoxLayout()
        debounce_row.addWidget(QLabel("Click debounce"))
        debounce_row.addWidget(self.debounce)
        debounce_row.addStretch(1)
        left.addLayout(debounce_row)

        view_panel = QFrame()
        view_panel.setObjectName("Panel")
        right = QVBoxLayout(view_panel)
        right.setContentsMargins(12, 12, 12, 12)
        self.view = MouseView()
        self.view.selected.connect(self._set_current)
        right.addWidget(self.view, 1)
        self.caption = QLabel()
        self.caption.setObjectName("Hint")
        self.caption.setAlignment(Qt.AlignCenter)
        self.caption.setWordWrap(True)
        right.addWidget(self.caption)

        transfer = QHBoxLayout()
        transfer.setSpacing(8)
        self.restore_button = QPushButton("Restore defaults")
        self.restore_button.clicked.connect(self.restore_requested)
        self.export_button = QPushButton("Export profile…")
        self.export_button.clicked.connect(self._on_export)
        self.import_button = QPushButton("Import profile…")
        self.import_button.clicked.connect(self._on_import)
        transfer.addStretch(1)
        for button in (self.restore_button, self.export_button, self.import_button):
            transfer.addWidget(button)

        grid = QGridLayout(self)
        grid.setContentsMargins(18, 8, 18, 18)
        grid.setSpacing(14)
        grid.addWidget(buttons_panel, 0, 0)
        grid.addWidget(view_panel, 0, 1)
        grid.addLayout(transfer, 1, 0, 1, 2)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 4)

        model.loaded.connect(self.refresh)
        model.changed.connect(self._on_model_changed)

    # -- model -> view -----------------------------------------------------
    def _on_model_changed(self, field: str) -> None:
        if field in ("keymap", "debounce"):
            self.refresh()

    def refresh(self) -> None:
        if not self.model.is_loaded:
            return
        self._syncing = True
        profile = self.model.profile()
        for row in self.rows:
            entry = profile.keymap[row.slot]
            record = self.model.combo_record(row.slot)
            function_id, _params = K.identify(entry, record)
            row.show_binding(function_id, K.describe(entry, record))
        self.debounce.setValue(profile.debounce_ms)
        self._syncing = False
        self._update_caption()

    def _set_current(self, row: int) -> None:
        self._current = row
        self.view.set_current(row)
        self._update_caption()

    def _update_caption(self) -> None:
        if not self.model.is_loaded:
            self.caption.setText("")
            return
        slot, name = ROWS[self._current]
        entry = self.model.profile().keymap[slot]
        summary = K.describe(entry, self.model.combo_record(slot))
        self.caption.setText(f"{self._current + 1}. {name} — {summary}")

    # -- view -> model -----------------------------------------------------
    def _on_debounce(self, value: int) -> None:
        if not self._syncing:
            self.model.set_debounce(value)

    def _on_function_chosen(self, row: int, function_id: str) -> None:
        if self._syncing or not function_id:
            return
        slot = ROWS[row][0]
        self._set_current(row)
        params: dict = {}
        if function_id in PARAMETRIC:
            _current_id, current_params = self.model.binding(slot)
            dialog = ParameterDialog(function_id, current_params, self)
            if dialog.exec() != QDialog.Accepted:
                self.refresh()  # put the combo back to what the mouse holds
                return
            params = dialog.parameters()
        if function_id == "macro":
            params["slot"] = slot
        try:
            binding = K.build(function_id, **params)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot assign that", str(exc))
            self.refresh()
            return
        self.model.set_binding(slot, binding)
        self._update_caption()

    # -- profile transfer --------------------------------------------------
    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export profile", "m693-profile.json", "Profile (*.json)"
        )
        if path:
            self.export_requested.emit(path)

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import profile", "", "Profile (*.json)"
        )
        if path:
            self.import_requested.emit(path)


def export_profile(model: ProfileModel, path: str) -> None:
    """Write a readable profile plus the raw images it came from."""
    from m693.transfer import to_json

    with open(path, "w") as fh:
        json.dump(to_json(model.image, model.combo_image), fh, indent=2)
        fh.write("\n")


def import_profile(model: ProfileModel, path: str) -> None:
    from m693.transfer import from_json

    with open(path) as fh:
        image, combo = from_json(json.load(fh))
    model.apply_image(image, combo)
