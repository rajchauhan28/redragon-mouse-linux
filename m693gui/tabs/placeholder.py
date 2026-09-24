"""Phase 1 stand-ins.

Each tab shows what it will contain and, where relevant, why it is not built
yet.  They subscribe to the model so the wiring is exercised end to end before
the real controls land.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from m693 import profile as P
from ..model import ProfileModel


class TabBase(QWidget):
    title = "Tab"
    blurb = ""

    def __init__(self, model: ProfileModel):
        super().__init__()
        self.model = model

        panel = QFrame()
        panel.setObjectName("Panel")
        inner = QVBoxLayout(panel)
        inner.setContentsMargins(22, 18, 22, 18)
        inner.setSpacing(12)

        heading = QLabel(self.title)
        heading.setObjectName("PanelTitle")
        inner.addWidget(heading)

        if self.blurb:
            blurb = QLabel(self.blurb)
            blurb.setObjectName("Hint")
            blurb.setWordWrap(True)
            inner.addWidget(blurb)

        self.summary = QLabel("Waiting for the mouse…")
        self.summary.setTextFormat(Qt.RichText)
        self.summary.setWordWrap(True)
        inner.addWidget(self.summary)
        inner.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 8, 18, 18)
        layout.addWidget(panel)

        model.loaded.connect(self.refresh)
        model.changed.connect(lambda _: self.refresh())

    def refresh(self) -> None:
        if not self.model.is_loaded:
            return
        self.summary.setText(self.describe(self.model.profile()))

    def describe(self, profile: P.Profile) -> str:
        return ""


class MacroTab(TabBase):
    title = "Macro"
    blurb = (
        "Not available yet. The mouse stores macros in the region from 0x300 "
        "(16 slots of 384 bytes), which has not been decoded — so recording "
        "one here could not be played back by the mouse. Deferred to phase 6."
    )

    def describe(self, profile: P.Profile) -> str:
        return ""
