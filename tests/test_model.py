#!/usr/bin/env python3
"""Phase 1 checks: group map, run coalescing, and the model's flush behaviour.

Run directly (`python3 tests/test_model.py`) or under pytest.  No hardware
required - the simulated backend stands in.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from m693 import profile as P  # noqa: E402
from m693.device import checksum  # noqa: E402
from m693gui.backend import FakeDevice  # noqa: E402
from m693gui.model import ProfileModel  # noqa: E402

_app = QCoreApplication.instance() or QCoreApplication([])
STOCK = FakeDevice(flaky=False).read(0x00, P.PROFILE_LEN)


def test_group_map_covers_known_fields():
    """Every documented field address must be a known group."""
    for addr in (
        P.ADDR_POLL_RATE, P.ADDR_DPI_COUNT, P.ADDR_DPI_ACTIVE, P.ADDR_LOD,
        P.ADDR_DPI_TABLE, P.ADDR_DPI_COLORS, P.ADDR_LED_MAIN, P.ADDR_LED_COLOR,
        P.ADDR_KEYMAP, P.ADDR_DEBOUNCE, P.ADDR_DORMANCY, P.ADDR_ANGLE_SNAP,
        P.ADDR_MOTION_SYNC, P.ADDR_RIPPLE, P.ADDR_LIGHT_OFF_MOVE,
        P.ADDR_LOGO_COLOR, P.ADDR_LOGO_MODE, P.ADDR_LOGO_BRIGHTNESS, P.ADDR_LOGO_SPEED,
    ):
        assert addr in P.GROUP_SIZES, f"{addr:#04x} missing from the group map"


def test_groups_do_not_overlap():
    end = 0
    for addr, size in P.GROUPS:
        assert addr >= end, f"group at {addr:#04x} overlaps the previous one"
        end = addr + size


def test_stock_image_checksums_validate():
    """The group map must agree with what a real device actually stores."""
    for addr, size in P.GROUPS:
        group = STOCK[addr : addr + size]
        assert sum(group) & 0xFF == 0x55, (
            f"group at {addr:#04x} does not sum to 0x55 - the map is wrong"
        )


def test_opaque_regions_are_not_writable():
    assert not P.is_writable(0xB5)
    assert not P.is_writable(0xB7)
    assert P.is_writable(P.ADDR_DEBOUNCE)
    assert P.is_writable(P.ADDR_LED_MAIN)


def test_merge_runs_coalesces_adjacent_groups():
    """Five changed DPI stages (20 bytes) must become two writes, not five."""
    dirty = {P.ADDR_DPI_TABLE + 4 * i for i in range(5)}
    runs = P.merge_runs(dirty)
    assert runs == [(0x0C, 8), (0x14, 8), (0x1C, 4)], runs
    assert all(length <= 10 for _, length in runs)


def test_merge_runs_keeps_gaps_separate():
    runs = P.merge_runs({P.ADDR_POLL_RATE, P.ADDR_DEBOUNCE})
    assert runs == [(0x00, 2), (0xA9, 2)]


def test_merge_runs_skips_opaque():
    assert P.merge_runs({0xB5}) == []


def test_main_led_is_a_single_seven_byte_group():
    """All six LED values share one checksum, so they write as one group."""
    assert P.GROUP_SIZES[P.ADDR_LED_MAIN] == 7
    assert sum(STOCK[P.ADDR_LED_MAIN : P.ADDR_LED_MAIN + 7]) & 0xFF == 0x55


def test_led_field_edit_preserves_siblings():
    """Changing the colour must not clobber mode, speed or brightness."""
    model = ProfileModel()
    model.load(STOCK)
    before = model.profile()
    model.set_led_color((0x11, 0x22, 0x33))
    after = model.profile()
    assert after.led_color == (0x11, 0x22, 0x33)
    assert after.led_mode == before.led_mode
    assert after.led_speed == before.led_speed
    assert after.led_brightness == before.led_brightness
    group = after.led_group
    assert sum(group) & 0xFF == 0x55, "checksum not recomputed for the group"


def test_slider_drag_costs_one_flush():
    """The core claim of the design: repeated edits coalesce into one write."""
    model = ProfileModel()
    model.load(STOCK)
    flushes = []
    model.flush_requested.connect(lambda runs, img: flushes.append(runs))

    for dpi in range(800, 1600, 100):  # simulate dragging a slider
        reg, mul = P.dpi_to_reg(dpi)
        model.set_record(P.ADDR_DPI_TABLE, reg, reg, mul)

    assert flushes == [], "must not write before the coalescing timer fires"
    model.flush()
    assert len(flushes) == 1, f"expected one flush, got {len(flushes)}"
    assert flushes[0] == [(P.ADDR_DPI_TABLE, 4)]


def test_no_op_write_does_not_dirty():
    model = ProfileModel()
    model.load(STOCK)
    model.set_scalar(P.ADDR_POLL_RATE, STOCK[P.ADDR_POLL_RATE])
    assert not model.is_dirty


def test_checksums_are_maintained_on_edit():
    model = ProfileModel()
    model.load(STOCK)
    model.set_record(P.ADDR_LED_COLOR, 0x12, 0x34, 0x56)
    group = model.image[P.ADDR_LED_COLOR : P.ADDR_LED_COLOR + 4]
    assert group[3] == checksum(group[:3])
    assert sum(group) & 0xFF == 0x55


def test_pending_edits_survive_a_reload():
    """A re-read while an edit is in flight must not silently revert it."""
    model = ProfileModel()
    model.load(STOCK)
    model.set_scalar(P.ADDR_DEBOUNCE, 4)
    model.load(STOCK)  # device re-read arrives before the write landed
    assert model.profile().debounce_ms == 4
    assert model.is_dirty


def test_confirm_clears_only_written_groups():
    model = ProfileModel()
    model.load(STOCK)
    model.set_scalar(P.ADDR_DEBOUNCE, 4)
    model.set_scalar(P.ADDR_POLL_RATE, 1)
    model.confirm([(P.ADDR_POLL_RATE, 2)])
    assert model.is_dirty
    model.confirm([(P.ADDR_DEBOUNCE, 2)])
    assert not model.is_dirty


def test_round_trip_through_the_fake_device():
    dev = FakeDevice(flaky=False)
    model = ProfileModel()
    model.load(dev.read(0x00, P.PROFILE_LEN))
    model.set_scalar(P.ADDR_POLL_RATE, P.POLL_DIVISORS[1000])
    for addr, length in P.merge_runs({P.ADDR_POLL_RATE}):
        dev.write(addr, model.image[addr : addr + length])
    assert P.Profile(dev.read(0x00, P.PROFILE_LEN)).polling_rate == 1000


def test_dpi_quantisation_round_trips():
    for dpi in (500, 800, 1600, 3200, 4000, 6000, 8000):
        reg, mul = P.dpi_to_reg(dpi)
        assert P.reg_to_dpi(reg, mul) == dpi, dpi


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
