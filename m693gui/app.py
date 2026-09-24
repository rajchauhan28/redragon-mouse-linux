"""Application shell: window, threading, and link-state presentation."""

from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtCore import Qt, QMetaObject, QThread, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .model import ProfileModel
from .tabs.dpi import DpiTab
from .tabs.keys import KeysTab, export_profile, import_profile
from .tabs.lighting import LightingTab
from .tabs.macro import MacroTab
from .widgets.chrome import SectionNav, StatusBanner, TitleBar
from .backend import STOCK_IMAGE
from .worker import ASLEEP, DISCONNECTED, ONLINE, DeviceWorker

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
MACRO_SECTION = 2


class MainWindow(QMainWindow):
    wake_requested = Signal()
    read_requested = Signal()

    def __init__(self, use_fake: bool = False, frameless: bool = True):
        super().__init__()
        self.setWindowTitle("Redragon M693")
        icon = os.path.join(ASSETS, "m693.svg")
        if os.path.exists(icon):
            self.setWindowIcon(QIcon(icon))
        self.resize(1030, 706)
        if frameless:
            self.setWindowFlag(Qt.FramelessWindowHint, True)

        self.model = ProfileModel()
        self._link_state = DISCONNECTED

        # -- worker thread -------------------------------------------------
        self.thread = QThread(self)
        self.worker = DeviceWorker(use_fake=use_fake)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.worker.link_state.connect(self._on_link_state)
        self.worker.profile_read.connect(self.model.load)
        self.worker.write_succeeded.connect(self.model.confirm)
        self.worker.combo_read.connect(self.model.load_combo)
        self.worker.combo_write_succeeded.connect(self.model.confirm_blocks)
        self.worker.write_failed.connect(self._on_write_failed)
        self.worker.battery.connect(self._on_battery)
        self.worker.error.connect(self._on_error)
        self.model.flush_requested.connect(self.worker.write_runs)
        self.model.combo_flush_requested.connect(self.worker.write_blocks)
        self.model.macro_flush_requested.connect(self.worker.write_blocks)
        self.model.dirty_changed.connect(self._on_dirty)
        self.wake_requested.connect(self.worker.nudge)
        self.read_requested.connect(self.worker.read_profile)
        self.thread.start()

        # -- layout --------------------------------------------------------
        root = QWidget()
        root.setObjectName("Root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        self.titlebar = TitleBar(self, draggable=frameless)
        self.titlebar.minimise_requested.connect(self.showMinimized)
        self.titlebar.close_requested.connect(self.close)
        layout.addWidget(self.titlebar)

        self.nav = SectionNav()
        self.nav.selected.connect(self._on_section)
        layout.addWidget(self.nav)

        self.banner = StatusBanner()
        self.banner.action_triggered.connect(self.wake_requested)
        layout.addWidget(self.banner)

        self.stack = QStackedWidget()
        self.keys_tab = KeysTab(self.model)
        self.keys_tab.export_requested.connect(self._on_export)
        self.keys_tab.import_requested.connect(self._on_import)
        self.keys_tab.restore_requested.connect(self._on_restore)
        self.macro_tab = MacroTab(self.model)
        self.macro_tab.assign_requested.connect(self._on_assign_macro)
        for tab in (self.keys_tab, DpiTab(self.model), self.macro_tab,
                    LightingTab(self.model)):
            self.stack.addWidget(tab)
        layout.addWidget(self.stack, 1)

        self.setCentralWidget(root)

        self.status = QStatusBar()
        self.status_label = QLabel("Starting…")
        self.status.addWidget(self.status_label)
        self.battery_label = QLabel("")
        self.battery_label.setObjectName("Battery")
        self.status.addPermanentWidget(self.battery_label)
        self.sync_label = QLabel("")
        self.sync_label.setToolTip(
            "Edits are held for a moment and written in as few commands as the "
            "protocol allows, so a slider drag costs one write, not hundreds."
        )
        self.status.addPermanentWidget(self.sync_label)
        hint = QLabel("Ctrl+1…4 sections · Ctrl+R reload · Ctrl+E/I profile")
        hint.setObjectName("Hint")
        self.status.addPermanentWidget(hint)
        self.setStatusBar(self.status)

        # Macro is deliberately reachable so its explanation is discoverable.
        self.nav.set_enabled_section(MACRO_SECTION, True)
        self._install_shortcuts()
        self.nav.setFocus(Qt.OtherFocusReason)

    # -- keyboard ----------------------------------------------------------
    def _install_shortcuts(self) -> None:
        """Everything the toolbar does, without reaching for the mouse.

        Configuring a mouse from the keyboard sounds perverse, but it is the
        one input device you cannot rely on while you are remapping it.
        """
        bindings = [
            ("Ctrl+1", lambda: self.nav.select(0)),
            ("Ctrl+2", lambda: self.nav.select(1)),
            ("Ctrl+3", lambda: self.nav.select(MACRO_SECTION)),
            ("Ctrl+4", lambda: self.nav.select(3)),
            ("Ctrl+Tab", lambda: self.nav.step(1)),
            ("Ctrl+Shift+Tab", lambda: self.nav.step(-1)),
            ("Ctrl+R", self.read_requested.emit),
            ("F5", self.wake_requested.emit),
            ("Ctrl+E", self.keys_tab._on_export),
            ("Ctrl+I", self.keys_tab._on_import),
            ("Ctrl+W", self.close),
            ("Ctrl+Q", self.close),
        ]
        for sequence, handler in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(handler)
            self._shortcuts = getattr(self, "_shortcuts", [])
            self._shortcuts.append(shortcut)

    # -- link state --------------------------------------------------------
    def _on_link_state(self, state: str) -> None:
        previous, self._link_state = self._link_state, state
        if state == DISCONNECTED:
            self.banner.show_message(
                "No M693 detected. Plug in the receiver, or check the udev rule.",
                "error",
                "Retry",
            )
            self.status_label.setText("Disconnected")
            self.battery_label.setText("")
        elif state == ASLEEP:
            self.banner.show_message(
                "The mouse is asleep. Move or click it to reconnect — "
                "any changes you make will be applied when it wakes.",
                "warn",
                "Wake",
            )
            self.status_label.setText("Asleep")
        else:
            self.banner.clear()
            self.status_label.setText("Connected")
            if previous != ONLINE or not self.model.is_loaded:
                self.read_requested.emit()
            if self.model.is_dirty:
                self.model.flush()

    def _on_battery(self, percent: int, charging: bool) -> None:
        """The mouse reports whole percent; 0x04 is the stock tool's GetPower."""
        self.battery_label.setText(
            f"Battery {percent}%" + (" ⚡" if charging else "")
        )
        state = "charging" if charging else "on battery"
        self.battery_label.setToolTip(f"{percent}%, {state}")
        # Only the mouse itself shows a low-battery warning, on the wheel LED,
        # and only once it is already blinking red - so say so a little sooner.
        self.battery_label.setProperty("low", percent <= 20 and not charging)
        self.battery_label.style().unpolish(self.battery_label)
        self.battery_label.style().polish(self.battery_label)

    def _on_dirty(self, dirty: bool) -> None:
        self.sync_label.setText("Saving…" if dirty else "Saved")

    def _on_write_failed(self, runs: list, message: str) -> None:
        self.model.retry_later()
        self.sync_label.setText("Retrying…")
        self.status_label.setText(f"Write deferred: {message}")

    def _on_error(self, message: str) -> None:
        self.banner.show_message(message, "error", "Retry")

    def _on_assign_macro(self, slot: int, macro, repeat: int) -> None:
        try:
            self.model.set_macro(slot, macro, repeat)
        except ValueError as exc:
            self.banner.show_message(str(exc), "error")
            return
        self.status_label.setText(f"Assigned \u201c{macro.name}\u201d to slot {slot + 1}")

    # -- profile transfer --------------------------------------------------
    def _on_export(self, path: str) -> None:
        if not self.model.is_loaded:
            self.banner.show_message("Nothing to export yet — waiting for the mouse.", "warn")
            return
        try:
            export_profile(self.model, path)
        except OSError as exc:
            self.banner.show_message(f"Could not write {path}: {exc}", "error")
            return
        self.status_label.setText(f"Exported to {path}")

    def _on_import(self, path: str) -> None:
        try:
            import_profile(self.model, path)
        except (OSError, ValueError) as exc:
            self.banner.show_message(f"Could not import {path}: {exc}", "error")
            return
        self.status_label.setText(f"Imported {path}")

    def _on_restore(self) -> None:
        answer = QMessageBox.question(
            self,
            "Restore defaults",
            "Put every button, DPI stage and lighting setting back to the "
            "factory values this mouse shipped with?",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            with open(STOCK_IMAGE, "rb") as fh:
                image = fh.read()
        except OSError as exc:
            self.banner.show_message(f"No stock image to restore from: {exc}", "error")
            return
        self.model.apply_image(image)
        self.status_label.setText("Restoring factory settings…")

    def _on_section(self, index: int) -> None:
        self.stack.setCurrentIndex(index)

    # -- shutdown ----------------------------------------------------------
    def closeEvent(self, event):
        self.model.flush()
        # stop() must run *on* the worker thread: called directly it would
        # close the file descriptor from under a read still in flight there.
        # Only worth marshalling while that thread is alive, though - a
        # blocking call into a thread with no event loop never returns.
        if self.thread.isRunning():
            QMetaObject.invokeMethod(self.worker, "stop", Qt.BlockingQueuedConnection)
        else:
            self.worker.stop()
        self.thread.quit()
        self.thread.wait(3000)
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="m693-gui")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="run against a simulated mouse (no hardware needed)",
    )
    parser.add_argument(
        "--version", action="version", version=f"m693-gui {__version__}"
    )
    parser.add_argument(
        "--native-decorations",
        action="store_true",
        help="use the window manager's title bar instead of the custom one",
    )
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("m693-gui")
    app.setApplicationDisplayName("Redragon M693")
    app.setDesktopFileName("m693-gui")
    icon = os.path.join(ASSETS, "m693.svg")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    qss = os.path.join(ASSETS, "theme.qss")
    if os.path.exists(qss):
        with open(qss) as fh:
            # Stylesheet url()s resolve against the working directory, so the
            # asset path has to be baked in rather than left relative.
            app.setStyleSheet(fh.read().replace("@ASSETS@", ASSETS))

    window = MainWindow(use_fake=args.fake, frameless=not args.native_decorations)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
