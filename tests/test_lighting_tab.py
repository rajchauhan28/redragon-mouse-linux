#!/usr/bin/env python3
"""Phase 3 checks: the main RGB group, the mode table, and the Lighting tab."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from m693 import lighting as L  # noqa: E402
from m693 import profile as P  # noqa: E402
from m693gui.backend import FakeDevice  # noqa: E402
from m693gui.model import ProfileModel  # noqa: E402
from m693gui.tabs.lighting import MODE_ORDER, LightingTab  # noqa: E402
from m693gui.widgets.lighting import PREDEFINED, ColorWheel  # noqa: E402

_app = QApplication.instance() or QApplication([])
STOCK = FakeDevice(flaky=False).read(0x00, P.PROFILE_LEN)


def build():
    model = ProfileModel()
    tab = LightingTab(model)
    model.load(STOCK)
    return model, tab


def test_mode_table_matches_the_remap():
    """Values recovered from the UI->device remap at 0x4110cf."""
    assert P.LED_MODES[0] == "streaming"
    assert P.LED_MODES[1] == "breathing"
    assert P.LED_MODES[2] == "steady"     # verified on hardware
    assert P.LED_MODES[3] == "neon"
    assert P.LED_MODES[4] == "off"
    assert 6 not in P.LED_MODES           # the firmware skips 6
    assert P.LED_MODES[7] == "colorful-breathing"


def test_every_offered_mode_is_writable():
    for name in MODE_ORDER:
        assert name in P.LED_MODE_NAMES, name


def test_stock_device_is_streaming_magenta():
    profile = P.Profile(STOCK)
    assert P.LED_MODES[profile.led_mode] == "streaming"
    assert profile.led_color == (0xFF, 0x00, 0xFF)


def test_view_reflects_device_state():
    model, tab = build()
    profile = model.profile()
    assert tab.mode.currentData() == profile.led_mode
    assert tab.wheel.color() == profile.led_color
    assert tab.brightness.slider.value() == profile.led_brightness
    # The slider shows speed; the device stores the inverse, a delay.
    assert tab.speed.slider.value() == L.speed_from_raw(profile.led_speed)


def test_syncing_from_model_does_not_write_back():
    model, tab = build()
    model.load(STOCK)
    assert not model.is_dirty, "view sync wrote back into the model"


def test_mode_change_preserves_colour_and_speed():
    """Mode shares a group with colour, speed and brightness."""
    model, tab = build()
    before = model.profile()
    tab.mode.setCurrentIndex(tab.mode.findData(P.LED_MODE_NAMES["breathing"]))
    after = model.profile()
    assert after.led_mode == P.LED_MODE_NAMES["breathing"]
    assert after.led_color == before.led_color
    assert after.led_speed == before.led_speed
    assert after.led_brightness == before.led_brightness


def test_colour_edit_writes_one_group():
    model, tab = build()
    tab.wheel.set_color((0x10, 0x20, 0x30))
    model.set_led_color((0x10, 0x20, 0x30))
    assert model.profile().led_color == (0x10, 0x20, 0x30)
    assert P.merge_runs(model._dirty) == [(P.ADDR_LED_MAIN, P.LED_GROUP_LEN)]


def test_led_group_always_checksums():
    model, tab = build()
    for name in MODE_ORDER:
        model.set_led_mode(P.LED_MODE_NAMES[name])
        for rgb in PREDEFINED:
            model.set_led_color(rgb)
            group = model.profile().led_group
            assert sum(group) & 0xFF == 0x55, (name, rgb)


def test_predefined_colours_round_trip():
    model, tab = build()
    for rgb in PREDEFINED:
        model.set_led_color(rgb)
        assert model.profile().led_color == rgb


def test_library_led_setters_write_whole_groups():
    """Regression: a 2-byte scalar write at 0xA0 corrupts the group.

    Mode/colour/speed/brightness share one checksum, so each setter must
    read-modify-write all six values.  Writing a scalar would put a checksum
    byte where the red channel lives and leave the group invalid.
    """
    from m693 import profile as prof

    dev = FakeDevice(flaky=False)
    for call in (
        lambda: prof.set_led_mode(dev, prof.LED_MODE_NAMES["breathing"]),
        lambda: prof.set_led_speed(dev, 7),
        lambda: prof.set_led_brightness(dev, 120),
        lambda: prof.set_led_color(dev, (0x12, 0x34, 0x56)),
    ):
        call()
        group = dev.read(prof.ADDR_LED_MAIN, prof.LED_GROUP_LEN)
        assert sum(group) & 0xFF == 0x55, f"setter corrupted the group: {group.hex(' ')}"

    final = P.Profile(dev.read(0x00, P.PROFILE_LEN))
    assert final.led_mode == prof.LED_MODE_NAMES["breathing"]
    assert final.led_speed == 7
    assert final.led_brightness == 120
    assert final.led_color == (0x12, 0x34, 0x56)


def test_dormancy_combo_round_trips():
    model, tab = build()
    for index in range(tab.dormancy.count()):
        seconds = tab.dormancy.itemData(index)
        model.set_dormancy(seconds)
        assert model.profile().dormancy_s == round(seconds / 10) * 10


def test_colour_wheel_maps_angle_to_hue():
    wheel = ColorWheel(180)
    for rgb in ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)):
        wheel.set_color(rgb)
        assert wheel.color() == rgb


def test_preview_survives_missing_artwork():
    """The schematic fallback must not crash when assets are absent."""
    from m693gui.widgets.lighting import MousePreview

    preview = MousePreview()
    preview._lit = None
    preview.set_state(P.LED_MODE_NAMES["steady"], (255, 0, 0), 3, 200)
    assert not preview.has_artwork
    preview.resize(320, 280)
    preview.grab()  # would raise if paintEvent were unsafe


def test_preview_light_mask_excludes_the_shell():
    """The saturation split must find lights without eating the body."""
    from m693gui.widgets.lighting import MousePreview

    preview = MousePreview()
    if not preview.has_artwork:
        return  # artwork not extracted; nothing to check
    preview._prepare()
    mask = preview._mask.toImage()
    lit = sum(
        1
        for y in range(0, mask.height(), 3)
        for x in range(0, mask.width(), 3)
        if mask.pixelColor(x, y).alpha() > 40
    )
    total = (mask.height() // 3) * (mask.width() // 3)
    fraction = lit / total
    assert 0.005 < fraction < 0.25, (
        f"light mask covers {fraction:.1%} of the render - "
        "expected a few percent (strips plus logo)"
    )


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
