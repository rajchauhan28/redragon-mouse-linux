#!/usr/bin/env python3
"""Per-zone lighting and saved presets.

The zone split is a hardware finding, not a guess: the main group at 0xA0
drives the strips and the logo together, and 0x4C drives the scroll wheel -
writing mode 4 there blanks the wheel while the strips stay lit.  The wheel's
colour is the DPI stage colour table, which is the awkward part these tests
exist to pin down.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Presets must never touch the real config file while testing.
_TMP = tempfile.mkdtemp(prefix="m693-tests-")
os.environ["XDG_CONFIG_HOME"] = _TMP

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from m693 import lighting as L  # noqa: E402
from m693 import presets as PRESETS  # noqa: E402
from m693 import profile as P  # noqa: E402
from m693gui.backend import FakeDevice  # noqa: E402
from m693gui.model import ProfileModel  # noqa: E402
from m693gui.tabs.lighting import LightingTab  # noqa: E402

_app = QApplication.instance() or QApplication([])
STOCK = FakeDevice(flaky=False).read(0x00, P.PROFILE_LEN)


def build():
    model = ProfileModel()
    tab = LightingTab(model)
    model.load(STOCK)
    return model, tab


# -- addressing -------------------------------------------------------------
def test_three_zones_over_two_channels():
    """The strips and the logo are listed apart but are one channel."""
    assert P.ADDR_WHEEL_MODE == 0x4C
    assert P.ADDR_LED_MAIN == 0xA0
    assert set(L.ZONES) == {L.ZONE_STRIPS, L.ZONE_LOGO, L.ZONE_WHEEL}
    assert L.ZONE_BLOCKS[L.ZONE_STRIPS] == L.ZONE_BLOCKS[L.ZONE_LOGO]
    assert L.ZONE_BLOCKS[L.ZONE_WHEEL] != L.ZONE_BLOCKS[L.ZONE_STRIPS]
    assert len(L.distinct(L.ZONES)) == 2


def test_the_linked_pair_is_declared_both_ways():
    assert L.linked_to(L.ZONE_STRIPS) == (L.ZONE_LOGO,)
    assert L.linked_to(L.ZONE_LOGO) == (L.ZONE_STRIPS,)
    assert L.linked_to(L.ZONE_WHEEL) == ()


def test_editing_the_logo_moves_the_strips():
    """Hardware: 0xA0 lights both, so the UI must not pretend otherwise."""
    dev = FakeDevice(flaky=False)
    L.apply(dev, L.ZONE_LOGO,
            L.ZoneState(P.LED_MODE_NAMES["steady"], (0x21, 0x22, 0x23), 200, 4))
    profile = P.Profile(dev.read(0x00, P.PROFILE_LEN))
    assert L.read(profile, L.ZONE_STRIPS).color == (0x21, 0x22, 0x23)
    assert L.read(profile, L.ZONE_LOGO) == L.read(profile, L.ZONE_STRIPS)


def test_the_linked_pair_is_written_once():
    """Both names must not cost two writes of the same group."""
    model, tab = build()
    tab._confirm_sync = lambda: True
    tab.sync_all.setChecked(True)
    model._dirty.clear()
    tab._on_brightness(123)
    assert P.merge_runs(model._dirty) == [
        (P.ADDR_WHEEL_BRIGHTNESS, P.GROUP_SCALAR),
        (P.ADDR_LED_MAIN, P.LED_GROUP_LEN),
    ]


def test_wheel_block_is_three_separate_scalars():
    """Unlike the main group, each wheel field carries its own checksum."""
    for addr in (P.ADDR_WHEEL_MODE, P.ADDR_WHEEL_BRIGHTNESS, P.ADDR_WHEEL_SPEED):
        assert P.GROUP_SIZES[addr] == P.GROUP_SCALAR, hex(addr)


def test_wheel_colour_reads_from_the_active_dpi_stage():
    profile = P.Profile(STOCK)
    state = L.read(profile, L.ZONE_WHEEL)
    assert state.color == profile.dpi_color(profile.dpi_active)


def test_zones_read_back_independently():
    profile = P.Profile(STOCK)
    main = L.read(profile, L.ZONE_MAIN)
    wheel = L.read(profile, L.ZONE_WHEEL)
    assert main.mode == profile.led_mode
    assert wheel.mode == profile.wheel_mode


# -- writing ----------------------------------------------------------------
def test_setting_the_wheel_leaves_the_strips_alone():
    dev = FakeDevice(flaky=False)
    before = P.Profile(dev.read(0x00, P.PROFILE_LEN)).led_group
    L.apply(dev, L.ZONE_WHEEL,
            L.ZoneState(P.LED_MODE_NAMES["breathing"], (1, 2, 3), 90, 5))
    after = P.Profile(dev.read(0x00, P.PROFILE_LEN))
    assert after.led_group == before, "wheel edit disturbed the main zone"
    assert after.wheel_mode == P.LED_MODE_NAMES["breathing"]
    assert after.wheel_brightness == 90
    assert after.wheel_speed == 5


def test_setting_the_strips_leaves_the_wheel_alone():
    dev = FakeDevice(flaky=False)
    before = P.Profile(dev.read(0x00, P.PROFILE_LEN))
    L.apply(dev, L.ZONE_MAIN,
            L.ZoneState(P.LED_MODE_NAMES["steady"], (9, 8, 7), 200, 2))
    after = P.Profile(dev.read(0x00, P.PROFILE_LEN))
    assert after.wheel_mode == before.wheel_mode
    assert after.dpi_colors == before.dpi_colors
    assert after.led_color == (9, 8, 7)


def test_wheel_colour_repaints_every_dpi_stage():
    dev = FakeDevice(flaky=False)
    L.apply(dev, L.ZONE_WHEEL, L.ZoneState(2, (0x11, 0x22, 0x33), 128, 3))
    profile = P.Profile(dev.read(0x00, P.PROFILE_LEN))
    for index in range(P.MAX_DPI_STAGES):
        assert profile.dpi_color(index) == (0x11, 0x22, 0x33), index


def test_wheel_colour_can_be_left_alone():
    """Applying an effect must not have to cost the DPI colour coding."""
    dev = FakeDevice(flaky=False)
    before = P.Profile(dev.read(0x00, P.PROFILE_LEN)).dpi_colors
    L.apply(dev, L.ZONE_WHEEL, L.ZoneState(1, (0, 0, 0), 64, 8), color=False)
    after = P.Profile(dev.read(0x00, P.PROFILE_LEN))
    assert after.dpi_colors == before
    assert after.wheel_brightness == 64


def test_every_written_group_still_checksums():
    dev = FakeDevice(flaky=False)
    for zone in L.ZONES:
        for name in P.LED_MODE_NAMES:
            L.apply(dev, zone, L.ZoneState(P.LED_MODE_NAMES[name], (7, 7, 7), 5, 9))
    profile = P.Profile(dev.read(0x00, P.PROFILE_LEN))
    assert profile.bad_groups() == []


def test_set_led_writes_the_whole_group_in_one_command():
    """The batched writer must agree with the field-by-field ones."""
    batched, stepwise = FakeDevice(flaky=False), FakeDevice(flaky=False)
    P.set_led(batched, P.LED_MODE_NAMES["neon"], (3, 4, 5), 6, 7)
    P.set_led_mode(stepwise, P.LED_MODE_NAMES["neon"])
    P.set_led_color(stepwise, (3, 4, 5))
    P.set_led_speed(stepwise, 6)
    P.set_led_brightness(stepwise, 7)
    assert (batched.read(P.ADDR_LED_MAIN, P.LED_GROUP_LEN)
            == stepwise.read(P.ADDR_LED_MAIN, P.LED_GROUP_LEN))


# -- speed ------------------------------------------------------------------
def test_every_slider_position_round_trips():
    for display in range(L.SPEED_MIN, L.SPEED_MAX + 1):
        assert L.speed_from_raw(L.speed_to_raw(display)) == display


def test_higher_displayed_speed_stores_a_smaller_delay():
    """Hardware: an effect at a stored 10 is visibly slower than at 2."""
    raws = [L.speed_to_raw(d) for d in range(L.SPEED_MIN, L.SPEED_MAX + 1)]
    assert raws == sorted(raws, reverse=True), raws
    assert len(set(raws)) == len(raws), f"two slider steps collide: {raws}"


def test_the_slider_covers_the_whole_byte():
    """The stock tool stops at 10; the firmware keeps slowing to 255."""
    assert L.speed_to_raw(L.SPEED_MIN) == L.RAW_SPEED_MAX
    assert L.speed_to_raw(L.SPEED_MAX) == L.RAW_SPEED_MIN


def test_any_stored_byte_maps_to_a_slider_position():
    """Profiles written by the stock tool hold values we never write."""
    for raw in range(0, 256):
        assert L.SPEED_MIN <= L.speed_from_raw(raw) <= L.SPEED_MAX, raw


def test_speed_slider_runs_the_right_way_round():
    model, tab = build()
    tab.sync_pair.setChecked(True)
    tab.zone.setCurrentIndex(tab.zone.findData(L.ZONE_MAIN))
    tab._on_speed(L.SPEED_MAX)  # user asks for fastest
    fastest = model.profile().led_speed
    tab._on_speed(L.SPEED_MIN)  # user asks for slowest
    assert model.profile().led_speed > fastest, "the Speed slider is inverted"


def test_preset_files_store_the_displayed_speed():
    state = L.ZoneState(2, (1, 2, 3), 100, L.speed_to_raw(9))
    assert state.to_json()["speed"] == 9
    assert L.ZoneState.from_json(state.to_json()).speed == state.speed


# -- the tab ----------------------------------------------------------------
def test_tab_edits_only_the_selected_zone():
    model, tab = build()
    tab.sync_pair.setChecked(True)
    tab.zone.setCurrentIndex(tab.zone.findData(L.ZONE_WHEEL))
    before = model.profile().led_group
    tab.speed.set_value(9)
    tab._on_speed(9)
    assert model.profile().wheel_speed == L.speed_to_raw(9)
    assert model.profile().led_group == before


def test_tab_sync_writes_both_zones(monkeypatched=None):
    model, tab = build()
    tab._confirm_sync = lambda: True
    tab.sync_all.setChecked(True)
    tab._on_mode(0)  # whatever the combo currently holds
    tab.mode.setCurrentIndex(tab.mode.findData(P.LED_MODE_NAMES["breathing"]))
    profile = model.profile()
    assert profile.led_mode == P.LED_MODE_NAMES["breathing"]
    assert profile.wheel_mode == P.LED_MODE_NAMES["breathing"]


def test_sync_offers_the_two_states_that_exist():
    """Strips+logo is one channel, so the only choice is whether the wheel
    comes along. A third radio would duplicate one of these."""
    model, tab = build()
    assert len(tab.sync_group.buttons()) == 2
    tab.sync_pair.setChecked(True)
    assert not tab.sync_all.isChecked(), "the radios are not exclusive"
    tab._confirm_sync = lambda: True
    tab.sync_all.setChecked(True)
    assert not tab.sync_pair.isChecked()


def test_pair_mode_leaves_the_wheel_alone():
    model, tab = build()
    tab.sync_pair.setChecked(True)
    tab.zone.setCurrentIndex(tab.zone.findData(L.ZONE_LOGO))
    before = model.profile().wheel_mode
    tab._on_mode(0)
    tab.mode.setCurrentIndex(tab.mode.findData(P.LED_MODE_NAMES["neon"]))
    profile = model.profile()
    assert profile.led_mode == P.LED_MODE_NAMES["neon"]
    assert profile.wheel_mode == before, "strips+logo mode dragged the wheel"


def test_zone_picker_is_only_live_when_not_all_synced():
    model, tab = build()
    tab.sync_pair.setChecked(True)
    tab.sync()
    assert tab.zone.isEnabled()
    tab._confirm_sync = lambda: True
    tab.sync_all.setChecked(True)
    tab.sync()
    assert not tab.zone.isEnabled()


def test_declining_the_sync_warning_leaves_the_box_clear():
    """Syncing flattens the DPI colours, so a refused prompt must not sync."""
    model, tab = build()
    before = model.profile().dpi_colors
    tab._confirm_sync = lambda: False
    tab.sync_all.setChecked(True)
    assert not tab.sync_all.isChecked()
    assert tab.sync_pair.isChecked()
    assert model.profile().dpi_colors == before


def test_zone_note_explains_the_dpi_coupling():
    model, tab = build()
    tab.sync_pair.setChecked(True)
    tab.zone.setCurrentIndex(tab.zone.findData(L.ZONE_WHEEL))
    assert "DPI" in tab.zone_note.text()


def test_switching_zone_does_not_write():
    model, tab = build()
    tab.sync_pair.setChecked(True)
    for index in range(tab.zone.count()):
        tab.zone.setCurrentIndex(index)
    assert not model.is_dirty, "merely looking at a zone wrote to the mouse"


# -- presets ----------------------------------------------------------------
def test_builtin_presets_all_decode():
    for preset in L.BUILTIN_PRESETS:
        round_tripped = L.Preset.from_json(preset.to_json())
        assert round_tripped.zones == preset.zones, preset.name


def test_builtin_presets_cover_every_channel():
    """One entry per independent channel; the linked pair needs only one."""
    for preset in L.BUILTIN_PRESETS:
        blocks = {L.ZONE_BLOCKS[z] for z in preset.zones}
        assert blocks == set(L.ZONE_BLOCKS.values()), preset.name


def test_capture_then_apply_is_a_no_op():
    dev = FakeDevice(flaky=False)
    before = dev.read(0x00, P.PROFILE_LEN)
    preset = L.Preset.capture("snapshot", P.Profile(before))
    preset.apply(dev)
    assert dev.read(0x00, P.PROFILE_LEN) == before


def test_user_presets_survive_a_round_trip():
    PRESETS.save_user([])
    preset = L.Preset.capture("mine", P.Profile(STOCK))
    PRESETS.store(preset)
    found = PRESETS.find("mine")
    assert found is not None
    assert found.zones == preset.zones
    assert PRESETS.remove("mine")
    assert PRESETS.find("mine") is None


def test_a_user_preset_shadows_a_builtin_of_the_same_name():
    PRESETS.save_user([])
    name = L.BUILTIN_PRESETS[0].name
    mine = L.Preset(name, {L.ZONE_MAIN: L.ZoneState(2, (1, 1, 1), 10, 1)})
    PRESETS.store(mine)
    names = [p.name for p in PRESETS.load_all()]
    assert names.count(name) == 1
    assert PRESETS.find(name).zones[L.ZONE_MAIN].color == (1, 1, 1)
    PRESETS.remove(name)


def test_a_damaged_preset_file_is_reported_not_ignored():
    PRESETS.save_user([])
    with open(PRESETS.PRESET_PATH, "w") as handle:
        handle.write("{ not json")
    try:
        PRESETS.load_user()
    except ValueError:
        pass
    else:
        raise AssertionError("a corrupt preset file loaded silently")
    PRESETS.save_user([])


def test_one_bad_entry_does_not_lose_the_rest():
    PRESETS.save_user([])
    good = L.Preset("good", {L.ZONE_MAIN: L.ZoneState(2, (5, 5, 5), 10, 1)})
    PRESETS.store(good)
    import json

    with open(PRESETS.PRESET_PATH) as handle:
        data = json.load(handle)
    data["presets"].insert(0, {"name": "broken", "zones": {"strips": {"mode": "nope"}}})
    with open(PRESETS.PRESET_PATH, "w") as handle:
        json.dump(data, handle)
    names = [p.name for p in PRESETS.load_user()]
    assert names == ["good"], names
    PRESETS.save_user([])


def test_tab_lists_builtins_and_disables_deleting_them():
    PRESETS.save_user([])
    model, tab = build()
    tab._reload_presets()
    listed = [tab.presets.itemData(i) for i in range(tab.presets.count())]
    for preset in L.BUILTIN_PRESETS:
        assert preset.name in listed, preset.name
    tab.presets.setCurrentIndex(listed.index(L.BUILTIN_PRESETS[0].name))
    assert not tab.delete_preset.isEnabled()


def test_applying_a_preset_reaches_both_zones():
    model, tab = build()
    tab._confirm_preset_colors = lambda: True
    preset = L.Preset("test", {
        L.ZONE_MAIN: L.ZoneState(P.LED_MODE_NAMES["steady"], (1, 2, 3), 111, 4),
        L.ZONE_WHEEL: L.ZoneState(P.LED_MODE_NAMES["neon"], (4, 5, 6), 77, 8),
    })
    model.apply_preset(preset)
    profile = model.profile()
    assert profile.led_color == (1, 2, 3)
    assert profile.led_brightness == 111
    assert profile.wheel_mode == P.LED_MODE_NAMES["neon"]
    assert profile.dpi_color(0) == (4, 5, 6)
    assert profile.bad_groups() == []


def test_preset_json_rejects_a_bad_colour():
    for bad in ({"color": "xyz"}, {"mode": "disco"}):
        try:
            L.ZoneState.from_json(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")


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
