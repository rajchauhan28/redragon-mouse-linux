"""DPI Settings — stages, polling rate, DPI-indicator effect, sensor options."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from m693 import lighting as L
from m693 import profile as P

from ..model import ProfileModel
from ..widgets.controls import DpiStageRow, SliderRow


def _panel(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 14, 18, 14)
    layout.setSpacing(8)
    if title:
        label = QLabel(title)
        label.setObjectName("PanelTitle")
        layout.addWidget(label)
    return frame, layout


class DpiTab(QWidget):
    def __init__(self, model: ProfileModel):
        super().__init__()
        self.model = model
        self._syncing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 8, 18, 14)
        outer.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(self._build_stages(), 1)
        top.addWidget(self._build_polling())
        outer.addLayout(top, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        bottom.addWidget(self._build_effect(), 1)
        bottom.addWidget(self._build_sensor(), 1)
        outer.addLayout(bottom)

        model.loaded.connect(self.sync)
        model.changed.connect(lambda _: self.sync())

    # -- construction ------------------------------------------------------
    def _build_stages(self) -> QFrame:
        frame, layout = _panel()

        header = QHBoxLayout()
        title = QLabel("DPI Settings")
        title.setObjectName("PanelTitle")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(QLabel("DPI Stages"))
        self.stage_count = QComboBox()
        self.stage_count.addItems([str(i) for i in range(1, P.MAX_DPI_STAGES + 1)])
        self.stage_count.setFixedWidth(72)
        self.stage_count.currentIndexChanged.connect(self._on_count)
        header.addWidget(self.stage_count)
        layout.addLayout(header)
        layout.addSpacing(4)

        self.rows: list[DpiStageRow] = []
        for i in range(P.MAX_DPI_STAGES):
            row = DpiStageRow(i)
            row.dpi_changed.connect(self._on_dpi)
            row.color_changed.connect(self._on_color)
            row.activated.connect(self._on_activate)
            self.rows.append(row)
            layout.addWidget(row)
        layout.addStretch(1)
        return frame

    def _build_polling(self) -> QFrame:
        frame, layout = _panel("Polling Rate")
        frame.setFixedWidth(150)
        self.poll_group = QButtonGroup(self)
        layout.addSpacing(6)
        for hz in (125, 250, 500, 1000):
            button = QRadioButton(f"{hz} Hz")
            self.poll_group.addButton(button, hz)
            layout.addWidget(button, 0, Qt.AlignHCenter)
            layout.addSpacing(10)
        layout.addStretch(1)
        self.poll_group.idToggled.connect(self._on_poll)
        return frame

    def _build_effect(self) -> QFrame:
        frame, layout = _panel()

        header = QHBoxLayout()
        # The same zone the Lighting tab calls "Scroll wheel"; it lives here
        # too because this is where its colours (the stage table) are set.
        title = QLabel("Wheel effect")
        title.setObjectName("PanelTitle")
        header.addWidget(title)
        self.effect_mode = QComboBox()
        for value, name in sorted(P.LED_MODES.items()):
            self.effect_mode.addItem(name.replace("-", " ").title(), value)
        self.effect_mode.currentIndexChanged.connect(self._on_effect_mode)
        header.addWidget(self.effect_mode, 1)
        layout.addLayout(header)

        self.brightness = SliderRow("Brightness", 0, 255)
        self.brightness.value_changed.connect(self.model.set_dpi_effect_brightness)
        layout.addWidget(self.brightness)

        # Flipped: the stored byte is a delay (see lighting.speed_to_raw).
        self.speed = SliderRow("Speed", L.SPEED_MIN, L.SPEED_MAX)
        self.speed.value_changed.connect(
            lambda value: self.model.set_dpi_effect_speed(L.speed_to_raw(value))
        )
        layout.addWidget(self.speed)
        layout.addStretch(1)
        return frame

    def _build_sensor(self) -> QFrame:
        frame, layout = _panel()
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(10)

        title = QLabel("LOD")
        title.setObjectName("PanelTitle")
        grid.addWidget(title, 0, 0, 1, 2)

        self.lod_group = QButtonGroup(self)
        lod_row = QHBoxLayout()
        lod_row.setSpacing(24)
        for value, text in ((1, "1 mm"), (2, "2 mm")):
            button = QRadioButton(text)
            self.lod_group.addButton(button, value)
            lod_row.addWidget(button)
        lod_row.addStretch(1)
        grid.addLayout(lod_row, 1, 0)
        self.lod_group.idToggled.connect(self._on_lod)

        self.ripple = QCheckBox("Ripple control")
        self.ripple.toggled.connect(self._on_ripple)
        grid.addWidget(self.ripple, 0, 1)

        self.angle = QCheckBox("Angle snapping")
        self.angle.toggled.connect(self._on_angle)
        grid.addWidget(self.angle, 1, 1)

        self.motion_sync = QCheckBox("Motion sync")
        self.motion_sync.setToolTip(
            "Aligns sensor readout with the USB polling interval."
        )
        self.motion_sync.toggled.connect(self._on_motion_sync)
        grid.addWidget(self.motion_sync, 2, 1)

        layout.addLayout(grid)
        layout.addStretch(1)
        return frame

    # -- model -> view -----------------------------------------------------
    def sync(self) -> None:
        if not self.model.is_loaded:
            return
        profile = self.model.profile()
        self._syncing = True
        try:
            count = max(1, min(P.MAX_DPI_STAGES, profile.dpi_count))
            self.stage_count.setCurrentIndex(count - 1)
            for i, row in enumerate(self.rows):
                row.setVisible(i < count)
                if i < count:
                    row.set_state(
                        profile.dpi_stage(i)[0],
                        profile.dpi_color(i),
                        i == profile.dpi_active,
                    )

            button = self.poll_group.button(profile.polling_rate)
            if button:
                button.setChecked(True)

            self.effect_mode.setCurrentIndex(
                max(0, self.effect_mode.findData(profile.dpi_effect_mode))
            )
            self.brightness.set_value(profile.dpi_effect_brightness)
            self.speed.set_value(L.speed_from_raw(profile.dpi_effect_speed))

            lod = self.lod_group.button(profile.lod)
            if lod:
                lod.setChecked(True)
            self.ripple.setChecked(profile.ripple_control)
            self.motion_sync.setChecked(profile.motion_sync)
            self.angle.setChecked(profile.angle_snapping)
        finally:
            self._syncing = False

    # -- view -> model -----------------------------------------------------
    def _on_count(self, index: int) -> None:
        if not self._syncing:
            self.model.set_dpi_count(index + 1)

    def _on_dpi(self, index: int, dpi: int) -> None:
        if not self._syncing:
            self.model.set_dpi_stage(index, dpi)

    def _on_color(self, index: int, rgb: tuple) -> None:
        if not self._syncing:
            self.model.set_dpi_color(index, rgb)

    def _on_activate(self, index: int) -> None:
        if not self._syncing:
            self.model.set_dpi_active(index)

    def _on_poll(self, hz: int, checked: bool) -> None:
        if checked and not self._syncing:
            self.model.set_polling_rate(hz)

    def _on_effect_mode(self, _index: int) -> None:
        if not self._syncing:
            self.model.set_dpi_effect_mode(self.effect_mode.currentData())

    def _on_lod(self, value: int, checked: bool) -> None:
        if checked and not self._syncing:
            self.model.set_lod(value)

    def _on_motion_sync(self, on: bool) -> None:
        if not self._syncing:
            self.model.set_motion_sync(on)

    def _on_ripple(self, on: bool) -> None:
        if not self._syncing:
            self.model.set_ripple_control(on)

    def _on_angle(self, on: bool) -> None:
        if not self._syncing:
            self.model.set_angle_snapping(on)
