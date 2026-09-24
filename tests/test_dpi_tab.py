#!/usr/bin/env python3
"""Phase 2 checks: the DPI tab's two-way binding and quantisation."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from m693 import lighting as L  # noqa: E402
from m693 import profile as P  # noqa: E402
from m693gui.backend import FakeDevice  # noqa: E402
from m693gui.model import ProfileModel  # noqa: E402
from m693gui.tabs.dpi import DpiTab  # noqa: E402

_app = QApplication.instance() or QApplication([])
STOCK = FakeDevice(flaky=False).read(0x00, P.PROFILE_LEN)


def build():
    model = ProfileModel()
    tab = DpiTab(model)
    model.load(STOCK)
    return model, tab


def test_view_reflects_device_state():
    model, tab = build()
    profile = model.profile()
    assert tab.stage_count.currentText() == str(profile.dpi_count)
    assert tab.rows[0].value.text() == "500"
    assert tab.rows[4].value.text() == "8000"
    assert tab.poll_group.checkedId() == profile.polling_rate
    assert tab.lod_group.checkedId() == profile.lod
    assert tab.ripple.isChecked() == profile.ripple_control
    assert tab.motion_sync.isChecked() == profile.motion_sync
    # The slider shows speed; the device stores the inverse, a delay.
    assert tab.speed.slider.value() == L.speed_from_raw(profile.dpi_effect_speed)


def test_only_active_stages_are_visible():
    model, tab = build()
    model.set_dpi_count(3)
    # isVisible() is False while the top-level window is unshown; isHidden()
    # reflects the explicit hide we care about.
    assert [not r.isHidden() for r in tab.rows] == [True] * 3 + [False] * 5


def test_syncing_from_model_does_not_write_back():
    """The guard against feedback loops: a reload must not dirty the model."""
    model, tab = build()
    assert not model.is_dirty
    model.load(STOCK)  # a fresh device read repopulates every widget
    assert not model.is_dirty, "view sync wrote back into the model"


def test_slider_edit_reaches_the_model():
    model, tab = build()
    index = P.DPI_STEPS.index(1600)
    tab.rows[0].slider.setValue(index)
    assert model.profile().dpi_stage(0)[0] == 1600
    assert P.ADDR_DPI_TABLE in model._dirty


def test_slider_cannot_express_an_unstorable_dpi():
    """Every reachable slider position round-trips through the sensor table."""
    model, tab = build()
    row = tab.rows[0]
    for index in range(row.slider.minimum(), row.slider.maximum() + 1):
        row.slider.setValue(index)
        shown = int(row.value.text())
        assert model.profile().dpi_stage(0)[0] == shown, (
            f"slider shows {shown} but the mouse would store something else"
        )


def test_polling_radio_writes_divisor():
    model, tab = build()
    tab.poll_group.button(1000).setChecked(True)
    assert model.profile().polling_rate == 1000
    assert model.image[P.ADDR_POLL_RATE] == 1


def test_checkboxes_write_flags():
    model, tab = build()
    tab.angle.setChecked(True)
    tab.ripple.setChecked(True)
    tab.motion_sync.setChecked(True)
    profile = model.profile()
    assert profile.angle_snapping and profile.ripple_control and profile.motion_sync


def test_reducing_stage_count_clamps_active_stage():
    model, tab = build()
    model.set_dpi_active(4)
    model.set_dpi_count(2)
    assert model.profile().dpi_active == 1, "active stage left pointing past the end"


def test_effect_combo_uses_on_device_numbering():
    model, tab = build()
    tab.effect_mode.setCurrentIndex(tab.effect_mode.findData(P.LED_MODE_NAMES["off"]))
    assert model.image[P.ADDR_DPI_EFFECT] == P.LED_MODE_NAMES["off"]


def test_edits_stay_within_writable_regions():
    model, tab = build()
    tab.rows[0].slider.setValue(10)
    tab.poll_group.button(500).setChecked(True)
    tab.angle.setChecked(True)
    assert all(P.is_writable(addr) for addr in model._dirty)


def test_whole_tab_edit_produces_few_writes():
    """Retuning all five stages costs 3 writes, not one per stage.

    4-byte groups pack two to a 10-byte command, so 20 bytes is 3 runs - the
    optimum, and well under the 5 an uncoalesced implementation would send.
    """
    model, tab = build()
    for i in range(5):
        tab.rows[i].slider.setValue(P.DPI_STEPS.index(800 + i * 400))
    runs = P.merge_runs(model._dirty)
    assert runs == [(0x0C, 8), (0x14, 8), (0x1C, 4)], runs
    assert sum(length for _, length in runs) == 20


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {test.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
