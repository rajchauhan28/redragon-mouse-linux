"""Keyboard capture for macro recording.

Qt only sees key events, and only while it has focus, so what this records is
keystrokes and their real timings.  Mouse buttons are deliberately *not*
captured: the recorder would swallow the clicks you need to stop it, and the
grab would fight the window manager.  They are added from the step list
instead, which is also the only way to express a press without its release.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QWidget

from m693 import macro as M
from m693.keys import KEYBOARD_USAGES, MOD_ALT, MOD_CTRL, MOD_SHIFT, MOD_SUPER

# Qt key -> our key name.  Letters and digits fall through to their text.
_SPECIAL = {
    Qt.Key_Escape: "esc", Qt.Key_Tab: "tab", Qt.Key_Backspace: "backspace",
    Qt.Key_Return: "enter", Qt.Key_Enter: "numenter", Qt.Key_Space: "space",
    Qt.Key_Insert: "insert", Qt.Key_Delete: "delete", Qt.Key_Home: "home",
    Qt.Key_End: "end", Qt.Key_PageUp: "pageup", Qt.Key_PageDown: "pagedown",
    Qt.Key_Left: "left", Qt.Key_Right: "right", Qt.Key_Up: "up",
    Qt.Key_Down: "down", Qt.Key_CapsLock: "capslock",
    Qt.Key_NumLock: "numlock", Qt.Key_ScrollLock: "scrolllock",
    Qt.Key_Pause: "pause", Qt.Key_Print: "printscreen", Qt.Key_Menu: "menu",
    Qt.Key_Minus: "minus", Qt.Key_Equal: "equal", Qt.Key_BracketLeft: "leftbracket",
    Qt.Key_BracketRight: "rightbracket", Qt.Key_Backslash: "backslash",
    Qt.Key_Semicolon: "semicolon", Qt.Key_Apostrophe: "apostrophe",
    Qt.Key_QuoteLeft: "grave", Qt.Key_Comma: "comma", Qt.Key_Period: "period",
    Qt.Key_Slash: "slash",
}
for _n in range(1, 13):
    _SPECIAL[getattr(Qt, f"Key_F{_n}")] = f"f{_n}"

_MODIFIERS = {
    Qt.Key_Control: MOD_CTRL,
    Qt.Key_Shift: MOD_SHIFT,
    Qt.Key_Alt: MOD_ALT,
    Qt.Key_Meta: MOD_SUPER,
}


def key_to_event(key: int, text: str) -> tuple[int, int] | None:
    """(kind, code) for a Qt key, or None if the mouse cannot express it."""
    if key in _MODIFIERS:
        return (M.KIND_MODIFIER, _MODIFIERS[key])
    name = _SPECIAL.get(key)
    if name is None and text:
        candidate = text.lower()
        if candidate in KEYBOARD_USAGES:
            name = candidate
    if name is None:
        return None
    return (M.KIND_KEY, KEYBOARD_USAGES[name])


class Recorder(QWidget):
    """Grabs the keyboard while armed and emits the events it sees."""

    event_captured = Signal(object)  # m693.macro.Event
    unsupported = Signal(str)
    finished = Signal()

    def __init__(self, min_delay_ms: int = 1):
        super().__init__()
        self.min_delay_ms = min_delay_ms
        self.recording = False
        self._last = 0.0
        self._pending = None
        self.setFocusPolicy(Qt.StrongFocus)

    def start(self) -> None:
        self.recording = True
        self._last = time.monotonic()
        self._pending = None
        self.setFocus(Qt.OtherFocusReason)
        self.grabKeyboard()

    def stop(self) -> None:
        if not self.recording:
            return
        self.recording = False
        self.releaseKeyboard()
        self._flush(0)
        self.finished.emit()

    def _elapsed(self) -> int:
        now = time.monotonic()
        gap = int((now - self._last) * 1000)
        self._last = now
        return max(self.min_delay_ms, min(M.MAX_DELAY_MS, gap))

    def _flush(self, delay: int) -> None:
        """Emit the previous event once we know how long it lasted.

        A macro event's delay is the pause *after* it, so an event cannot be
        emitted until the next one arrives to measure against.
        """
        if self._pending is None:
            return
        kind, code, press = self._pending
        self._pending = None
        self.event_captured.emit(M.Event(kind, code, press, delay))

    def _capture(self, event, press: bool) -> None:
        if event.isAutoRepeat():
            return
        resolved = key_to_event(event.key(), event.text())
        if resolved is None:
            self.unsupported.emit(
                QKeySequence(event.key()).toString() or "that key"
            )
            return
        self._flush(self._elapsed())
        self._pending = (resolved[0], resolved[1], press)

    def keyPressEvent(self, event):
        if not self.recording:
            super().keyPressEvent(event)
            return
        self._capture(event, True)
        event.accept()

    def keyReleaseEvent(self, event):
        if not self.recording:
            super().keyReleaseEvent(event)
            return
        self._capture(event, False)
        event.accept()
