"""Macro: a local library of macros, and assigning one to a button."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from m693 import keys as K
from m693 import macro as M

from ..library import MacroLibrary
from ..model import ProfileModel
from ..widgets.recorder import Recorder
from .keys import ROWS

REPEAT_CHOICES = [
    ("Once", 1),
    ("Twice", 2),
    ("5 times", 5),
    ("10 times", 10),
    ("Repeat while held", M.REPEAT_WHILE_HELD),
    ("Toggle on/off", M.REPEAT_TOGGLE),
]

MOUSE_STEPS = [
    ("Left button", 0x01),
    ("Right button", 0x02),
    ("Middle button", 0x04),
    ("Back button", 0x08),
    ("Forward button", 0x10),
]


class StepDialog(QDialog):
    """Adds a step the keyboard recorder cannot produce."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Insert step")

        self.kind = QComboBox()
        self.kind.addItem("Mouse button", M.KIND_MOUSE)
        self.kind.addItem("Multimedia key", M.KIND_CONSUMER)
        self.kind.addItem("Pause only", -1)
        self.kind.currentIndexChanged.connect(self._on_kind)

        self.what = QComboBox()
        self.action = QComboBox()
        self.action.addItem("Press and release", None)
        self.action.addItem("Press", True)
        self.action.addItem("Release", False)
        self.delay = QSpinBox()
        self.delay.setRange(0, M.MAX_DELAY_MS)
        self.delay.setSuffix(" ms")
        self.delay.setValue(30)

        form = QFormLayout()
        form.addRow("Kind", self.kind)
        form.addRow("Button or key", self.what)
        form.addRow("Action", self.action)
        form.addRow("Pause after", self.delay)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._on_kind()

    def _on_kind(self) -> None:
        kind = self.kind.currentData()
        self.what.clear()
        if kind == M.KIND_MOUSE:
            for label, mask in MOUSE_STEPS:
                self.what.addItem(label, mask)
        elif kind == M.KIND_CONSUMER:
            for name, usage in sorted(K.CONSUMER_USAGES.items()):
                self.what.addItem(name.replace("-", " ").capitalize(), usage)
        self.what.setEnabled(kind != -1)
        # A consumer usage is a single tap in the firmware; it has no halves.
        self.action.setEnabled(kind == M.KIND_MOUSE)

    def steps(self) -> list[M.Event]:
        kind = self.kind.currentData()
        delay = self.delay.value()
        if kind == -1:
            return [M.Event(M.KIND_KEY, 0, True, delay)] if False else []
        code = self.what.currentData()
        if kind == M.KIND_CONSUMER:
            return [M.Event(M.KIND_CONSUMER, code, True, delay)]
        action = self.action.currentData()
        if action is None:
            return [
                M.Event(M.KIND_MOUSE, code, True, min(delay, 30)),
                M.Event(M.KIND_MOUSE, code, False, delay),
            ]
        return [M.Event(M.KIND_MOUSE, code, action, delay)]


class MacroTab(QWidget):
    """Left: the library. Middle: the selected macro's steps. Right: assign."""

    assign_requested = Signal(int, object, int)  # slot, Macro, repeat

    def __init__(self, model: ProfileModel):
        super().__init__()
        self.model = model
        self.library = MacroLibrary()
        self._syncing = False

        # -- library ------------------------------------------------------
        library_panel = QFrame()
        library_panel.setObjectName("Panel")
        left = QVBoxLayout(library_panel)
        left.setContentsMargins(18, 16, 18, 16)
        title = QLabel("Macros")
        title.setObjectName("PanelTitle")
        left.addWidget(title)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_select)
        left.addWidget(self.list, 1)

        row = QHBoxLayout()
        for label, handler in (
            ("New", self._on_new),
            ("Rename", self._on_rename),
            ("Delete", self._on_delete),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            row.addWidget(button)
        left.addLayout(row)

        transfer = QHBoxLayout()
        for label, handler in (
            ("Import…", self._on_import),
            ("Export…", self._on_export),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            transfer.addWidget(button)
        left.addLayout(transfer)

        # -- steps --------------------------------------------------------
        steps_panel = QFrame()
        steps_panel.setObjectName("Panel")
        middle = QVBoxLayout(steps_panel)
        middle.setContentsMargins(18, 16, 18, 16)
        heading = QLabel("Steps")
        heading.setObjectName("PanelTitle")
        middle.addWidget(heading)

        self.steps = QTreeWidget()
        self.steps.setColumnCount(3)
        self.steps.setHeaderLabels(["#", "Action", "Pause"])
        self.steps.setRootIsDecorated(False)
        self.steps.setColumnWidth(0, 34)
        self.steps.setColumnWidth(1, 210)
        middle.addWidget(self.steps, 1)

        self.recorder = Recorder()
        self.recorder.event_captured.connect(self._on_captured)
        self.recorder.unsupported.connect(self._on_unsupported)
        self.recorder.finished.connect(self._on_record_finished)

        controls = QHBoxLayout()
        self.record_button = QPushButton("Record")
        self.record_button.setCheckable(True)
        self.record_button.clicked.connect(self._on_record)
        controls.addWidget(self.record_button)
        for label, handler in (
            ("Insert step…", self._on_insert),
            ("Delete step", self._on_delete_step),
            ("Clear", self._on_clear),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            controls.addWidget(button)
        middle.addLayout(controls)
        middle.addWidget(self.recorder)

        self.hint = QLabel()
        self.hint.setObjectName("Hint")
        self.hint.setWordWrap(True)
        middle.addWidget(self.hint)

        # -- assignment ---------------------------------------------------
        assign_panel = QFrame()
        assign_panel.setObjectName("Panel")
        right = QVBoxLayout(assign_panel)
        right.setContentsMargins(18, 16, 18, 16)
        assign_title = QLabel("Assign to a button")
        assign_title.setObjectName("PanelTitle")
        right.addWidget(assign_title)

        self.button_choice = QComboBox()
        for index, (slot, name) in enumerate(ROWS):
            self.button_choice.addItem(f"{index + 1}. {name}", slot)
        self.repeat_choice = QComboBox()
        for label, value in REPEAT_CHOICES:
            self.repeat_choice.addItem(label, value)

        form = QFormLayout()
        form.addRow("Button", self.button_choice)
        form.addRow("Play", self.repeat_choice)
        right.addLayout(form)

        self.assign_button = QPushButton("Assign")
        self.assign_button.clicked.connect(self._on_assign)
        right.addWidget(self.assign_button)

        self.assigned = QLabel()
        self.assigned.setObjectName("Hint")
        self.assigned.setWordWrap(True)
        right.addWidget(self.assigned)
        right.addStretch(1)

        grid = QGridLayout(self)
        grid.setContentsMargins(18, 8, 18, 18)
        grid.setSpacing(14)
        grid.addWidget(library_panel, 0, 0)
        grid.addWidget(steps_panel, 0, 1)
        grid.addWidget(assign_panel, 0, 2)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 5)
        grid.setColumnStretch(2, 3)

        model.changed.connect(self._on_model_changed)
        self._reload_list()
        if self.library.error:
            self.hint.setText(
                f"Could not read your macro library ({self.library.error}). "
                "Nothing will be saved until that file is fixed or moved."
            )

    # -- library -----------------------------------------------------------
    def current(self) -> M.Macro | None:
        index = self.list.currentRow()
        if 0 <= index < len(self.library.macros):
            return self.library.macros[index]
        return None

    def _reload_list(self, select: int = -1) -> None:
        self._syncing = True
        self.list.clear()
        for macro in self.library.macros:
            steps = len(macro.events)
            self.list.addItem(f"{macro.name}  ({steps} step{'s' if steps != 1 else ''})")
        self._syncing = False
        if self.library.macros:
            self.list.setCurrentRow(
                select if 0 <= select < len(self.library.macros) else 0
            )
        else:
            self._refresh_steps()

    def _on_select(self, _row: int) -> None:
        if not self._syncing:
            self._refresh_steps()

    def _on_new(self) -> None:
        index = self.library.add(M.Macro("New macro"))
        self._reload_list(index)

    def _on_rename(self) -> None:
        macro = self.current()
        if macro is None:
            return
        name, ok = QInputDialog.getText(self, "Rename macro", "Name", text=macro.name)
        if not ok or not name.strip():
            return
        macro.name = name.strip()
        self.library.save()
        self._reload_list(self.list.currentRow())

    def _on_delete(self) -> None:
        index = self.list.currentRow()
        if index < 0:
            return
        self.library.remove(index)
        self._reload_list(min(index, len(self.library.macros) - 1))

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import macro", "", "Macros (*.jmm *.json);;All files (*)"
        )
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                with open(path) as fh:
                    document = json.load(fh)
                macros = [M.Macro.from_json(item) for item in document["macros"]]
            else:
                with open(path, "rb") as fh:
                    macros = [M.from_jmm(fh.read())]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, "Could not import", str(exc))
            return
        index = self.list.currentRow()
        for macro in macros:
            index = self.library.add(macro)
        self._reload_list(index)

    def _on_export(self) -> None:
        macro = self.current()
        if macro is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export macro", f"{macro.name}.json", "Macros (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "w") as fh:
                json.dump({"format": "m693-macros", "version": 1,
                           "macros": [macro.to_json()]}, fh, indent=2)
                fh.write("\n")
        except OSError as exc:
            QMessageBox.warning(self, "Could not export", str(exc))

    # -- steps -------------------------------------------------------------
    def _refresh_steps(self) -> None:
        self.steps.clear()
        macro = self.current()
        if macro is None:
            self.hint.setText("Create a macro to start recording one.")
            self.assign_button.setEnabled(False)
            return
        for index, event in enumerate(macro.events):
            item = QTreeWidgetItem(
                [str(index + 1), event.label(), f"{event.delay_ms} ms"]
            )
            self.steps.addTopLevelItem(item)
        self.assign_button.setEnabled(bool(macro.events))
        over = len(macro.events) - M.MAX_EVENTS
        if over > 0:
            self.hint.setText(
                f"This macro has {len(macro.events)} steps; the mouse holds "
                f"{M.MAX_EVENTS}. Remove {over} before assigning it."
            )
            self.assign_button.setEnabled(False)
        else:
            self.hint.setText(
                f"{len(macro.events)}/{M.MAX_EVENTS} steps · "
                f"{macro.duration_ms()} ms total. Recording captures keystrokes "
                "and their real timing; mouse buttons and media keys are added "
                "with Insert step."
            )

    def _on_record(self, checked: bool) -> None:
        if self.current() is None:
            self.record_button.setChecked(False)
            return
        if checked:
            self.record_button.setText("Stop")
            self.recorder.start()
        else:
            self.recorder.stop()

    def _on_record_finished(self) -> None:
        self.record_button.setChecked(False)
        self.record_button.setText("Record")
        self.library.save()
        self._reload_list(self.list.currentRow())

    def _on_captured(self, event: M.Event) -> None:
        macro = self.current()
        if macro is None:
            return
        if len(macro.events) >= M.MAX_EVENTS:
            self.recorder.stop()
            self.hint.setText(f"Stopped: a macro holds at most {M.MAX_EVENTS} steps.")
            return
        macro.events.append(event)
        self._refresh_steps()

    def _on_unsupported(self, name: str) -> None:
        self.hint.setText(f"The mouse cannot send {name}; that keystroke was skipped.")

    def _on_insert(self) -> None:
        macro = self.current()
        if macro is None:
            return
        dialog = StepDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        at = self.steps.indexOfTopLevelItem(self.steps.currentItem())
        at = len(macro.events) if at < 0 else at + 1
        macro.events[at:at] = dialog.steps()
        self.library.save()
        self._refresh_steps()

    def _on_delete_step(self) -> None:
        macro = self.current()
        index = self.steps.indexOfTopLevelItem(self.steps.currentItem())
        if macro is None or index < 0:
            return
        del macro.events[index]
        self.library.save()
        self._refresh_steps()

    def _on_clear(self) -> None:
        macro = self.current()
        if macro is None:
            return
        macro.events.clear()
        self.library.save()
        self._refresh_steps()

    # -- assignment --------------------------------------------------------
    def _on_assign(self) -> None:
        macro = self.current()
        if macro is None or not macro.events:
            return
        slot = self.button_choice.currentData()
        repeat = self.repeat_choice.currentData()
        try:
            macro.to_record()
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot assign", str(exc))
            return
        self.assign_requested.emit(slot, macro, repeat)

    def _on_model_changed(self, field: str) -> None:
        if field == "keymap":
            self._refresh_assigned()

    def _refresh_assigned(self) -> None:
        if not self.model.is_loaded:
            return
        keymap = self.model.profile().keymap
        lines = []
        for index, (slot, name) in enumerate(ROWS):
            entry = keymap[slot]
            if entry[0] == K.KEY_TYPE_MACRO:
                lines.append(
                    f"{index + 1}. {name} — {M.repeat_label(entry[2])}"
                )
        self.assigned.setText(
            "Buttons playing a macro:\n" + "\n".join(lines)
            if lines
            else "No button is currently bound to a macro."
        )
