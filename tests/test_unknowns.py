#!/usr/bin/env python3
"""What the previously-unidentified bytes turned out to be.

Each of these was settled either by the driver's own debug output, by the
UI wiring in OemDrv.exe, or by an experiment on real hardware.  The tests
record which, so a later change that "tidies" one of them has to argue with
the evidence.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from m693 import profile as P  # noqa: E402

STOCK = open(
    os.path.join(os.path.dirname(P.__file__), "stock-profile.bin"), "rb"
).read()


def test_flag_addresses_match_the_drivers_own_names():
    """OemDrv.exe prints each byte it parses with a name.

    Those prints (at 0x40f7a5 onward, indexing a buffer based at esp+0x3a0)
    are the author's own labels and outrank any guess:

        [0x0A] "LOD"      [0xAF] "FixLine"    [0xB1] "Ripple"
        [0xA9] "Debounce" [0xAD] "Sleep Time" [0xB3] "Turn OFF Light On Moving"
    """
    assert P.ADDR_LOD == 0x0A
    assert P.ADDR_ANGLE_SNAP == 0xAF  # "FixLine"
    assert P.ADDR_RIPPLE == 0xB1  # "Ripple"
    assert P.ADDR_DEBOUNCE == 0xA9
    assert P.ADDR_DORMANCY == 0xAD
    assert P.ADDR_LIGHT_OFF_MOVE == 0xB3


def test_motion_sync_is_0xab():
    """0xAB is the third checkbox: Motion sync.

    Three chains agree. The settings writer (0x411660) maps struct field
    +0x768 to 0xB1, +0x76c to 0xAF and +0x77c to 0xAB. The dialog binds those
    same fields to the controls whose rows are labelled, in order, "Ripple
    control", "Angle snapping" and "Motion sync". And the first two of those
    match the debug names above, which is what makes the third trustworthy.
    """
    assert P.ADDR_MOTION_SYNC == 0xAB
    assert P.GROUP_SIZES[P.ADDR_MOTION_SYNC] == P.GROUP_SCALAR
    assert P.is_writable(P.ADDR_MOTION_SYNC)


def test_ripple_and_motion_sync_are_separate_settings():
    """They were one field before; the stock UI has both."""
    assert P.ADDR_RIPPLE != P.ADDR_MOTION_SYNC
    profile = P.Profile(STOCK)
    profile.ripple_control  # both must decode
    profile.motion_sync


def test_second_lighting_zone_decodes_like_the_first():
    """EEPROM_To_LogoRGB (0x410070) takes 0x54 and reads +4, +6, +8.

    So the zone is laid out exactly like the DPI-effect block: colour, then
    mode, brightness and speed as 2-byte scalars.
    """
    assert (P.ADDR_LOGO_MODE, P.ADDR_LOGO_BRIGHTNESS, P.ADDR_LOGO_SPEED) == (
        0x58,
        0x5A,
        0x5C,
    )
    profile = P.Profile(STOCK)
    assert profile.logo_color == (0xFF, 0x00, 0xFF)
    assert profile.logo_brightness == 0x80
    assert profile.logo_speed == 3
    # Every one of them is a valid group, which is why it decoded at all.
    for addr in (P.ADDR_LOGO_COLOR, P.ADDR_LOGO_MODE, P.ADDR_LOGO_BRIGHTNESS,
                 P.ADDR_LOGO_SPEED, P.ADDR_LOGO_SPARE):
        size = P.GROUP_SIZES[addr]
        assert sum(STOCK[addr : addr + size]) & 0xFF == 0x55, hex(addr)


def test_the_spare_bytes_are_left_alone():
    """0x06, 0x08, 0x52, 0x5E, 0xA7: valid, zero, and never read.

    The stock tool dumps 0x00-0x0B for debugging but only ever uses 0x00,
    0x02, 0x04 and 0x0A out of it; 0x52 and 0x5E are the fourth slot of two
    groups whose decoder reads only three.
    """
    for addr in (P.ADDR_UNUSED_06, P.ADDR_UNUSED_08, P.ADDR_UNUSED_A7,
                 P.ADDR_LOGO_SPARE, P.ADDR_DPI_EFFECT + 6):
        assert STOCK[addr] == 0, hex(addr)
        assert sum(STOCK[addr : addr + 2]) & 0xFF == 0x55, hex(addr)


def test_the_tail_is_unwritten_and_stays_that_way():
    """0xB5 and 0xB7 are real fields the stock tool can write.

    Its defaults are 1 and 6 and it drives them from a two-state control and
    a value list. On this model they have never been written: they read 0xFF
    and form no valid group, so we do not touch them rather than guess what
    they would do.
    """
    assert STOCK[P.ADDR_UNUSED_B5] == 0xFF
    assert STOCK[P.ADDR_UNUSED_B7] == 0xFF
    assert not P.is_writable(P.ADDR_UNUSED_B5)
    assert not P.is_writable(P.ADDR_UNUSED_B7)
    assert all(byte == 0xFF for byte in STOCK[0xB5:P.PROFILE_LEN])


def test_the_read_length_covers_everything_the_stock_tool_reads():
    """It asks for 0xB5 bytes in 10-byte commands, so it gets 0xBE."""
    assert P.PROFILE_READ_LEN == 0xBE
    assert P.PROFILE_LEN >= P.PROFILE_READ_LEN


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
