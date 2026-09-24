"""Colour wheel and the animated mouse preview."""

from __future__ import annotations

import math
import os

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGridLayout, QSizePolicy, QWidget

from m693 import profile as P

from .. import artwork

# The 14 chips the stock tool offers below the wheel.
PREDEFINED = [
    (0xFF, 0x00, 0x00), (0x00, 0xE5, 0xE5), (0x22, 0xB1, 0x4C), (0xE0, 0x2C, 0xE0),
    (0xF2, 0xE0, 0x1C), (0xF0, 0x82, 0x1E), (0x1B, 0x3F, 0xD8),
    (0xE0, 0x3C, 0x9E), (0x4F, 0x9B, 0xD8), (0x6B, 0x2F, 0xA8), (0xFF, 0xFF, 0xFF),
    (0xA8, 0xC0, 0x3C), (0xE8, 0x9B, 0x2A), (0xE0, 0x8C, 0xC8),
]


class ColorWheel(QWidget):
    """HSV disc: hue around, saturation outward."""

    color_changed = Signal(tuple)
    editing_finished = Signal()

    def __init__(self, diameter: int = 180):
        super().__init__()
        self._diameter = diameter
        self.setFixedSize(diameter, diameter)
        self.setCursor(Qt.CrossCursor)
        self._color = QColor(255, 0, 0)
        self._wheel: QImage | None = None

    # -- painting ----------------------------------------------------------
    def _build_wheel(self, size: int) -> QImage:
        image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        radius = size / 2.0
        for y in range(size):
            dy = y - radius + 0.5
            for x in range(size):
                dx = x - radius + 0.5
                distance = math.hypot(dx, dy)
                if distance > radius:
                    continue
                hue = (math.degrees(math.atan2(-dy, dx)) + 360.0) % 360.0
                saturation = min(1.0, distance / radius)
                colour = QColor.fromHsvF(hue / 360.0, saturation, 1.0)
                # Feather the rim so the disc does not look jagged.
                if distance > radius - 1.0:
                    colour.setAlphaF(max(0.0, radius - distance))
                image.setPixelColor(x, y, colour)
        return image

    def paintEvent(self, event):
        size = min(self.width(), self.height())
        if self._wheel is None or self._wheel.width() != size:
            self._wheel = self._build_wheel(size)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.drawImage(0, 0, self._wheel)

        # Marker at the current hue/saturation.
        radius = size / 2.0
        hue, saturation, _, _ = self._color.getHsvF()
        hue = max(0.0, hue)
        angle = math.radians(hue * 360.0)
        distance = saturation * radius
        point = QPointF(radius + math.cos(angle) * distance,
                        radius - math.sin(angle) * distance)
        painter.setPen(QPen(QColor("#101010"), 2))
        painter.setBrush(self._color)
        painter.drawEllipse(point, 6, 6)
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.drawEllipse(point, 7, 7)

    # -- interaction -------------------------------------------------------
    def _pick(self, position) -> None:
        size = min(self.width(), self.height())
        radius = size / 2.0
        dx = position.x() - radius
        dy = position.y() - radius
        distance = math.hypot(dx, dy)
        hue = (math.degrees(math.atan2(-dy, dx)) + 360.0) % 360.0
        saturation = min(1.0, distance / radius)
        self._color = QColor.fromHsvF(hue / 360.0, saturation, 1.0)
        self.update()
        self.color_changed.emit(self.color())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pick(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._pick(event.position())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.editing_finished.emit()

    # -- state -------------------------------------------------------------
    def color(self) -> tuple[int, int, int]:
        return (self._color.red(), self._color.green(), self._color.blue())

    def set_color(self, rgb: tuple[int, int, int]) -> None:
        colour = QColor(*rgb)
        if colour != self._color:
            self._color = colour
            self.update()


class PredefinedColors(QWidget):
    """The fixed palette below the wheel."""

    color_picked = Signal(tuple)

    def __init__(self, columns: int = 7):
        super().__init__()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for index, rgb in enumerate(PREDEFINED):
            chip = _Chip(rgb)
            chip.clicked.connect(self.color_picked)
            layout.addWidget(chip, index // columns, index % columns)


class _Chip(QWidget):
    clicked = Signal(tuple)

    def __init__(self, rgb: tuple[int, int, int]):
        super().__init__()
        self._rgb = rgb
        self.setFixedSize(30, 24)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(*self._rgb))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._rgb)


class MousePreview(QWidget):
    """Live render: the body plus a tinted, animated lighting overlay.

    Falls back to a schematic if the artwork has not been extracted from the
    Windows installer (see tools/extract_assets.py).
    """

    FPS = 30

    # A pixel counts as "lit" if it is vivid; the shell is near-greyscale, the
    # strips and logo are saturated.  This is how we separate the light
    # channels from a render that has the rainbow baked in.
    SATURATION_CUT = 0.35
    VALUE_CUT = 60

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 280)
        self._lit = self._load("mouse_led.png")  # 3/4 view, lights on
        self._unlit: QPixmap | None = None
        self._mask: QPixmap | None = None
        self._color = QColor(255, 0, 255)
        self._mode = 0
        self._speed = 3
        self._brightness = 255
        self._phase = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(1000 // self.FPS)
        self._timer.timeout.connect(self._advance)
        self._timer.start()

    @staticmethod
    def _load(name: str) -> QPixmap | None:
        path = artwork.find(name)
        if path is None:
            return None
        pixmap = QPixmap(path)
        return None if pixmap.isNull() else pixmap

    @property
    def has_artwork(self) -> bool:
        return self._lit is not None

    def _prepare(self) -> None:
        """Split the render into an unlit shell and a light-only mask.

        Done once and cached; the source is only ~90k pixels.
        """
        source = self._lit.toImage().convertToFormat(QImage.Format_ARGB32)
        width, height = source.width(), source.height()
        pixels = bytearray(source.constBits().tobytes())  # BGRA, little-endian
        unlit = bytearray(pixels)
        mask = bytearray(len(pixels))

        for i in range(0, len(pixels), 4):
            if pixels[i + 3] == 0:
                continue
            blue, green, red = pixels[i], pixels[i + 1], pixels[i + 2]
            high, low = max(red, green, blue), min(red, green, blue)
            saturation = 0.0 if high == 0 else (high - low) / high
            if saturation <= self.SATURATION_CUT or high <= self.VALUE_CUT:
                continue
            # Dim the lit pixel down to unlit plastic...
            shade = int(high * 0.22)
            unlit[i] = unlit[i + 1] = unlit[i + 2] = shade
            # ...and record its intensity in the mask's alpha channel.
            mask[i] = mask[i + 1] = mask[i + 2] = 0xFF
            mask[i + 3] = int(high * min(1.0, saturation * 1.2))

        self._unlit = QPixmap.fromImage(
            QImage(bytes(unlit), width, height, QImage.Format_ARGB32).copy()
        )
        self._mask = QPixmap.fromImage(
            QImage(bytes(mask), width, height, QImage.Format_ARGB32).copy()
        )

    def set_state(self, mode: int, rgb, speed: int, brightness: int) -> None:
        self._mode = mode
        self._color = QColor(*rgb)
        self._speed = max(1, speed)
        self._brightness = brightness
        self.update()

    # Effects whose colour comes from the sensor's own cycle rather than the
    # stored colour; the shipped render already shows exactly this.
    RAINBOW_MODES = ("streaming", "neon", "colorful-breathing")

    def _advance(self) -> None:
        name = P.LED_MODES.get(self._mode, "steady")
        if name in ("steady", "off") or name in self.RAINBOW_MODES:
            return  # nothing animates; save the repaint
        self._phase = (self._phase + self._speed / (self.FPS * 4.0)) % 1.0
        self.update()

    # -- effect simulation -------------------------------------------------
    def _effective(self) -> tuple[QColor, float]:
        """Colour and opacity of the light layer for this frame."""
        name = P.LED_MODES.get(self._mode, "steady")
        alpha = max(0.15, self._brightness / 255.0)
        if name == "off":
            return self._color, 0.0
        if name in ("breathing", "single-color-flow"):
            eased = (1.0 - math.cos(self._phase * 2 * math.pi)) / 2.0
            return self._color, alpha * (0.12 + 0.88 * eased)
        return self._color, alpha

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)

        if not self.has_artwork:
            self._paint_schematic(painter)
            return
        if self._unlit is None:
            self._prepare()

        name = P.LED_MODES.get(self._mode, "steady")
        colour, opacity = self._effective()

        # The stock artwork is a rainbow render, so for the cycling effects it
        # already is the right picture - just show it.
        base = self._lit if name in self.RAINBOW_MODES else self._unlit
        scaled = base.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2

        if name in self.RAINBOW_MODES:
            painter.setOpacity(max(0.35, self._brightness / 255.0))
            painter.drawPixmap(x, y, scaled)
            painter.setOpacity(1.0)
            return

        painter.drawPixmap(x, y, scaled)
        if opacity <= 0.0 or self._mask is None:
            return

        # Tint the light-only mask: SourceIn keeps its alpha, replaces its RGB.
        layer = self._mask.scaled(
            scaled.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation
        )
        tint = QPainter(layer)
        tint.setCompositionMode(QPainter.CompositionMode_SourceIn)
        tint.fillRect(layer.rect(), colour)
        tint.end()

        painter.setOpacity(opacity)
        painter.drawPixmap(x, y, layer)
        painter.setOpacity(1.0)

    def _paint_schematic(self, painter: QPainter) -> None:
        colour, opacity = self._effective()
        rect = QRectF(self.rect()).adjusted(40, 30, -40, -30)
        painter.setPen(QPen(QColor("#3a3a3a"), 2))
        painter.setBrush(QColor("#1a1a1a"))
        painter.drawRoundedRect(rect, rect.width() / 2.4, rect.height() / 6)
        painter.setOpacity(opacity)
        painter.setPen(QPen(colour, 6))
        for side in (rect.left() + 10, rect.right() - 10):
            painter.drawLine(
                QPointF(side, rect.top() + rect.height() * 0.35),
                QPointF(side, rect.bottom() - rect.height() * 0.15),
            )
        painter.setOpacity(1.0)
        painter.setPen(QColor("#7d7d7d"))
        painter.drawText(
            self.rect().adjusted(0, 0, 0, -8),
            Qt.AlignHCenter | Qt.AlignBottom,
            "run tools/extract_assets.py for the full render",
        )
