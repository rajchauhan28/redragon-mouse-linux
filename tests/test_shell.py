#!/usr/bin/env python3
"""Phase 5 checks: keyboard reachability, packaging, and the desktop entry."""

import configparser
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractSlider,
    QApplication,
    QComboBox,
    QPushButton,
    QScrollBar,
    QSpinBox,
)

from m693gui import app as gui_app  # noqa: E402
from m693gui.backend import STOCK_IMAGE  # noqa: E402

_app = QApplication.instance() or QApplication([])
_window = None


def window():
    """One window for the whole run: starting a worker thread per test is slow."""
    global _window
    if _window is None:
        _window = gui_app.MainWindow(use_fake=True, frameless=False)
        _window.show()
        _app.processEvents()
    return _window


def test_the_stock_image_ships_with_the_package():
    """Restore-to-stock and the simulator both read it, so it must be installed."""
    assert os.path.exists(STOCK_IMAGE), STOCK_IMAGE
    assert os.path.dirname(STOCK_IMAGE).endswith("m693")
    assert os.path.getsize(STOCK_IMAGE) >= 0xC0


def test_focus_starts_somewhere_useful():
    win = window()
    focus = win.focusWidget()
    assert focus is win.nav or win.nav.isAncestorOf(focus), focus


def test_arrow_keys_walk_the_sections():
    win = window()
    win.nav.select(0)
    win.nav.step(1)
    assert win.stack.currentIndex() == 1
    win.nav.step(-1)
    assert win.stack.currentIndex() == 0
    win.nav.step(-1)  # wraps
    assert win.stack.currentIndex() == len(win.nav.SECTIONS) - 1


def test_every_section_has_a_shortcut():
    win = window()
    have = {s.key().toString() for s in win._shortcuts}
    for index in range(len(win.nav.SECTIONS)):
        assert f"Ctrl+{index + 1}" in have
    for expected in ("Ctrl+R", "Ctrl+E", "Ctrl+I", "F5", "Ctrl+Q"):
        assert QKeySequence(expected).toString() in have, expected


def test_shortcuts_actually_change_section():
    win = window()
    win.nav.select(0)
    for shortcut in win._shortcuts:
        if shortcut.key().toString() == "Ctrl+4":
            shortcut.activated.emit()
    assert win.stack.currentIndex() == 3
    win.nav.select(0)


def test_no_interactive_control_is_unreachable_by_keyboard():
    """Every control that changes a setting must accept focus.

    This window is how you reconfigure the mouse; if a remap goes wrong, the
    keyboard is the only way back.
    """
    win = window()
    unreachable = []
    for page_index in range(win.stack.count()):
        page = win.stack.widget(page_index)
        for kind in (QComboBox, QSpinBox, QAbstractSlider):
            for widget in page.findChildren(kind):
                # Scroll bars come from combo-box popups and are deliberately
                # not in the tab order; they are not settings.
                if isinstance(widget, QScrollBar):
                    continue
                if widget.focusPolicy() == Qt.NoFocus:
                    unreachable.append(
                        f"{type(page).__name__}.{type(widget).__name__}"
                    )
    assert not unreachable, unreachable


def test_action_buttons_are_reachable():
    win = window()
    for button in win.keys_tab.findChildren(QPushButton):
        assert button.focusPolicy() != Qt.NoFocus, button.text()


def test_focus_is_visible_in_the_theme():
    """A focus ring nobody can see is the same as no keyboard support."""
    with open(os.path.join(gui_app.ASSETS, "theme.qss")) as fh:
        qss = fh.read()
    for selector in ("#NavButton:focus", "QComboBox:focus", "QPushButton:focus"):
        assert selector in qss, selector


def test_desktop_entry_is_valid():
    path = os.path.join(ROOT, "packaging", "m693-gui.desktop")
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read(path)
    entry = parser["Desktop Entry"]
    assert entry["Type"] == "Application"
    assert entry["Exec"] == "m693-gui"
    assert entry["Icon"] == "m693-gui"
    assert os.path.exists(os.path.join(gui_app.ASSETS, "m693.svg"))
    validator = "desktop-file-validate"
    try:
        done = subprocess.run([validator, path], capture_output=True, text=True)
    except FileNotFoundError:
        return  # optional tool; the parse above is the portable part
    assert done.returncode == 0, done.stdout + done.stderr


def test_pyproject_exposes_both_entry_points():
    import tomllib

    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
        project = tomllib.load(fh)["project"]
    assert project["scripts"]["m693ctl"] == "m693.cli:main"
    assert project["gui-scripts"]["m693-gui"] == "m693gui.app:main"
    # The driver must stay importable without Qt installed.
    assert project["dependencies"] == []
    assert "PySide6" in project["optional-dependencies"]["gui"][0]


def test_the_driver_does_not_import_qt():
    """`pip install m693ctl` with no extras has to give a working CLI."""
    source = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.modules['PySide6'] = None; "
            "import m693, m693.cli, m693.keys, m693.transfer; print('ok')",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert source.returncode == 0, source.stderr
    assert "ok" in source.stdout


def test_both_front_ends_report_a_version():
    for module, flag in (("m693.cli", "m693ctl"), ("m693gui.app", "m693-gui")):
        done = subprocess.run(
            [sys.executable, "-c",
             f"import sys; from {module} import main; sys.exit(main(['--version']))"],
            capture_output=True, text=True, cwd=ROOT,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        assert flag in done.stdout, (module, done.stdout, done.stderr)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    try:
        for test in tests:
            try:
                test()
                print(f"  ok    {test.__name__}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL  {test.__name__}: {exc}")
    finally:
        if _window is not None:
            _window.close()  # stops the worker thread; Qt aborts if it leaks
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
