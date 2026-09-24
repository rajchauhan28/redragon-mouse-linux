"""Reusable controls shared across the tabs."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QLabel,
    QSlider,
    QWidget,
)

from m693 import profile as P


class Swatch(QWidget):
    """A clickable colour chip."""

    color_picked = Signal(tuple)

    def __init__(self, size: QSize = QSize(18, 18), interactive: bool = True):
        super().__init__()
        self._color = QColor("#ff0000")
        self._size = size
        self._interactive = interactive
        self.setFixedSize(size)
        if interactive:
            self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return self._size

    def color(self) -> tuple[int, int, int]:
        return (self._color.red(), self._color.green(), self._color.blue())

    def set_color(self, rgb: tuple[int, int, int]) -> None:
        self._color = QColor(*rgb)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#000000"), 1))
        painter.setBrush(self._color)
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

    def mousePressEvent(self, event):
        if not self._interactive or event.button() != Qt.LeftButton:
            return
        chosen = QColorDialog.getColor(self._color, self, "Choose colour")
        if chosen.isValid():
            self.set_color((chosen.red(), chosen.green(), chosen.blue()))
            self.color_picked.emit(self.color())


class DpiStageRow(QWidget):
    """One DPI stage: name, colour chip, snapped slider, value.

    The slider indexes the sensor's real DPI ladder rather than spanning raw
    numbers, so the figure shown is always a value the mouse can actually
    store - the stock tool lets you pick values it then silently quantises.
    """

    dpi_changed = Signal(int, int)  # stage index, dpi
    color_changed = Signal(int, tuple)
    activated = Signal(int)

    def __init__(self, index: int):
        super().__init__()
        self.index = index

        self.name = QLabel(f"DPI {index + 1}")
        self.name.setFixedWidth(52)
        self.name.setCursor(Qt.PointingHandCursor)
        self.name.setToolTip("Click to make this the active stage")

        self.swatch = Swatch()
        self.swatch.color_picked.connect(
            lambda rgb: self.color_changed.emit(self.index, rgb)
        )

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, len(P.DPI_STEPS) - 1)
        self.slider.setPageStep(5)
        self.slider.valueChanged.connect(self._on_slider)

        self.value = QLabel()
        self.value.setFixedWidth(52)
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(12)
        layout.addWidget(self.name)
        layout.addWidget(self.swatch)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value)

    def _on_slider(self, index: int) -> None:
        dpi = P.DPI_STEPS[index]
        self.value.setText(str(dpi))
        if not self.signalsBlocked():
            self.dpi_changed.emit(self.index, dpi)

    def set_state(self, dpi: int, rgb: tuple[int, int, int], active: bool) -> None:
        closest = min(range(len(P.DPI_STEPS)), key=lambda i: abs(P.DPI_STEPS[i] - dpi))
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(closest)
        self.slider.blockSignals(blocked)
        self.value.setText(str(P.DPI_STEPS[closest]))
        self.swatch.set_color(rgb)
        self.name.setStyleSheet(
            "color: #ffffff; font-weight: 700;" if active else "color: #b0b0b0;"
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.index)


class SliderRow(QWidget):
    """Label + slider + numeric readout."""

    value_changed = Signal(int)

    def __init__(self, label: str, minimum: int, maximum: int, width: int = 88):
        super().__init__()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.readout = QLabel()
        self.readout.setFixedWidth(30)
        self.readout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        caption = QLabel(label)
        caption.setFixedWidth(width)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(10)
        layout.addWidget(caption)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.readout)

        self.slider.valueChanged.connect(self._on_change)

    def _on_change(self, value: int) -> None:
        self.readout.setText(str(value))
        if not self.signalsBlocked():
            self.value_changed.emit(value)

    def set_value(self, value: int) -> None:
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(blocked)
        self.readout.setText(str(self.slider.value()))
