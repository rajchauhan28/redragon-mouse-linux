"""The top-down mouse render with numbered callouts.

The artwork already carries the leader lines - thin light strokes running out
to the left and right edges - so the numbers only have to be dropped at their
ends.  Those ends were measured off the image rather than guessed:
``tools/extract_assets.py`` copies it verbatim from the installer.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from .. import artwork

ART_W, ART_H = 550, 450
BADGE_R = 13

# (x, y) in artwork pixels for each callout badge, in row order.  The x values
# sit just past where the baked-in leader lines stop (61 on the left,
# 487 on the right).
CALLOUTS = [
    (44, 77),    # 1  left button
    (504, 133),  # 2  right button
    (504, 92),   # 3  wheel click
    (44, 162),   # 4  forward (upper thumb button)
    (44, 229),   # 5  back (lower thumb button)
    (504, 174),  # 6  upper top button
    (504, 225),  # 7  lower top button
]


class MouseView(QWidget):
    """Renders the mouse and its callouts; clicking a badge selects that row."""

    selected = Signal(int)

    def __init__(self):
        super().__init__()
        render = artwork.find("mouse_body.png")
        self._pixmap = QPixmap(render) if render else QPixmap()
        self._current = 0
        self._hover = -1
        self.setMinimumSize(300, 250)
        self.setMouseTracking(True)

    @property
    def has_artwork(self) -> bool:
        return not self._pixmap.isNull()

    def set_current(self, row: int) -> None:
        if row != self._current:
            self._current = row
            self.update()

    # -- geometry ----------------------------------------------------------
    def _frame(self) -> QRectF:
        """Where the artwork sits inside the widget, preserving its aspect."""
        scale = min(self.width() / ART_W, self.height() / ART_H)
        w, h = ART_W * scale, ART_H * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _badge_centre(self, index: int) -> QPointF:
        frame = self._frame()
        x, y = CALLOUTS[index]
        return QPointF(
            frame.x() + x * frame.width() / ART_W,
            frame.y() + y * frame.height() / ART_H,
        )

    def _badge_radius(self) -> float:
        return max(9.0, BADGE_R * self._frame().width() / ART_W)

    def _hit(self, pos) -> int:
        radius = self._badge_radius() + 3
        for index in range(len(CALLOUTS)):
            delta = self._badge_centre(index) - QPointF(pos)
            if delta.x() ** 2 + delta.y() ** 2 <= radius ** 2:
                return index
        return -1

    # -- events ------------------------------------------------------------
    def mouseMoveEvent(self, event):
        hover = self._hit(event.position())
        self.setCursor(Qt.PointingHandCursor if hover >= 0 else Qt.ArrowCursor)
        if hover != self._hover:
            self._hover = hover
            self.update()

    def leaveEvent(self, event):
        self._hover = -1
        self.update()

    def mousePressEvent(self, event):
        index = self._hit(event.position())
        if index >= 0:
            self.selected.emit(index)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        frame = self._frame()
        if self.has_artwork:
            painter.drawPixmap(frame.toRect(), self._pixmap)
        else:
            painter.setPen(QPen(QColor("#3a3a3a"), 1))
            painter.setBrush(QColor("#242424"))
            painter.drawRoundedRect(frame.adjusted(60, 20, -60, -20), 60, 60)

        radius = self._badge_radius()
        font = QFont(self.font())
        font.setPixelSize(int(radius * 1.2))
        font.setBold(True)
        painter.setFont(font)
        for index in range(len(CALLOUTS)):
            centre = self._badge_centre(index)
            active = index == self._current
            hover = index == self._hover
            if active:
                fill, edge, text = QColor("#c8102e"), QColor("#ff4d6a"), Qt.white
            elif hover:
                fill, edge, text = QColor("#3a3a3a"), QColor("#c8102e"), Qt.white
            else:
                fill, edge, text = QColor("#242424"), QColor("#6a6a6a"), QColor("#cfcfcf")
            painter.setPen(QPen(edge, 1.5))
            painter.setBrush(QBrush(fill))
            painter.drawEllipse(centre, radius, radius)
            painter.setPen(QPen(text))
            box = QRectF(
                centre.x() - radius, centre.y() - radius, radius * 2, radius * 2
            )
            painter.drawText(box, Qt.AlignCenter, str(index + 1))
