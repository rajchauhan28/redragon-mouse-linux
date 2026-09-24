"""Window chrome: title bar, section navigation, and the link-state banner."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)


class TitleBar(QWidget):
    """Frameless-window title bar; also draggable."""

    minimise_requested = Signal()
    close_requested = Signal()

    def __init__(self, window: QWidget, draggable: bool = True):
        super().__init__()
        self.setObjectName("TitleBar")
        self._window = window
        self._draggable = draggable
        self._press: QPoint | None = None

        wordmark = QLabel("REDRAGON")
        wordmark.setObjectName("Wordmark")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 10, 10, 4)
        layout.setSpacing(6)
        layout.addWidget(wordmark)
        layout.addStretch(1)

        if draggable:
            for text, name, signal in (
                ("–", "TitleButton", self.minimise_requested),
                ("×", "CloseButton", self.close_requested),
            ):
                button = QPushButton(text)
                button.setObjectName(name)
                button.setFocusPolicy(Qt.NoFocus)
                button.setCursor(Qt.PointingHandCursor)
                button.clicked.connect(signal)
                layout.addWidget(button)

    # -- dragging ----------------------------------------------------------
    def mousePressEvent(self, event):
        if self._draggable and event.button() == Qt.LeftButton:
            self._press = event.globalPosition().toPoint() - self._window.pos()

    def mouseMoveEvent(self, event):
        if self._press is not None and event.buttons() & Qt.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._press)

    def mouseReleaseEvent(self, event):
        self._press = None


class SectionNav(QWidget):
    """The four-icon segmented selector.

    Reachable from the keyboard: Tab lands on it, Left/Right move between
    sections, and Ctrl+1..4 jump straight to one from anywhere in the window.
    """

    selected = Signal(int)

    SECTIONS = (
        ("⚙", "Key Settings"),
        ("≡", "DPI Settings"),
        ("＋", "Macro"),
        ("●", "Lighting"),
    )

    def __init__(self):
        super().__init__()
        self.setObjectName("Nav")
        # Arrow keys are handled here, so the container itself takes focus.
        self.setFocusPolicy(Qt.StrongFocus)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 4, 18, 10)
        layout.setSpacing(10)

        for index, (glyph, tip) in enumerate(self.SECTIONS):
            button = QPushButton(glyph)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setToolTip(f"{tip}\tCtrl+{index + 1}")
            button.setAccessibleName(tip)
            button.setFocusPolicy(Qt.StrongFocus)
            button.setCursor(Qt.PointingHandCursor)
            self._group.addButton(button, index)
            layout.addWidget(button)
        layout.addStretch(1)

        self._group.idClicked.connect(self.selected)
        self._group.button(0).setChecked(True)

    def set_enabled_section(self, index: int, enabled: bool) -> None:
        self._group.button(index).setEnabled(enabled)

    def current(self) -> int:
        return self._group.checkedId()

    def select(self, index: int) -> None:
        button = self._group.button(index)
        if button is None or not button.isEnabled():
            return
        button.setChecked(True)
        self.selected.emit(index)

    def step(self, delta: int) -> None:
        """Move to the next enabled section, wrapping at the ends."""
        count = len(self.SECTIONS)
        index = self.current()
        for _ in range(count):
            index = (index + delta) % count
            button = self._group.button(index)
            if button is not None and button.isEnabled():
                button.setFocus(Qt.TabFocusReason)
                self.select(index)
                return

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Left, Qt.Key_Up):
            self.step(-1)
        elif event.key() in (Qt.Key_Right, Qt.Key_Down):
            self.step(1)
        else:
            super().keyPressEvent(event)


class StatusBanner(QWidget):
    """Explains link state in place, instead of throwing error dialogs.

    The mouse sleeping is a routine condition, not a failure, so it gets a
    banner with a wake button rather than a modal.
    """

    action_triggered = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("Banner")
        # A plain QWidget ignores stylesheet backgrounds without this.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._label = QLabel()
        self._label.setObjectName("BannerText")
        self._label.setWordWrap(True)
        self._action = QPushButton()
        self._action.setObjectName("BannerAction")
        self._action.setFocusPolicy(Qt.StrongFocus)
        self._action.setCursor(Qt.PointingHandCursor)
        self._action.clicked.connect(self.action_triggered)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.addWidget(self._label)
        layout.addStretch(1)
        layout.addWidget(self._action)
        self.hide()

    def show_message(
        self, text: str, severity: str = "info", action: str | None = None
    ) -> None:
        self._label.setText(text)
        self.setProperty("severity", severity)
        self._action.setText(action or "")
        self._action.setVisible(bool(action))
        # Re-evaluate the stylesheet so the severity colour applies.  Children
        # must be repolished too: Qt does not re-resolve descendant selectors
        # when only the parent's dynamic property changed.
        for widget in (self, self._label, self._action):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.show()

    def clear(self) -> None:
        self.hide()
