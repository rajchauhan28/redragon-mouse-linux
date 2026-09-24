#!/usr/bin/env python3
"""Phase 6 checks: the macro record, .jmm parsing, the library, and the tab."""

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

from PySide6.QtWidgets import QApplication  # noqa: E402

from m693 import keys as K  # noqa: E402
from m693 import macro as M  # noqa: E402
from m693 import profile as P  # noqa: E402
from m693gui.backend import FakeDevice  # noqa: E402
from m693gui.library import MacroLibrary  # noqa: E402
from m693gui.model import ProfileModel  # noqa: E402
from m693gui.tabs.macro import MacroTab  # noqa: E402

_app = QApplication.instance() or QApplication([])
_fake = FakeDevice(flaky=False)
STOCK = _fake.read(0x00, P.PROFILE_LEN)


def build():
    model = ProfileModel()
    tab = MacroTab(model)
    tab.library.path = os.path.join(tempfile.mkdtemp(), "macros.json")
    tab.library.macros = []
    model.load(STOCK)
    return model, tab


def sample() -> M.Macro:
    return M.Macro("test", M.type_text("hi"))


def test_record_round_trips():
    macro = sample()
    back = M.Macro.from_record(macro.to_record())
    assert back.name == macro.name
    assert back.events == macro.events


def test_record_header_matches_the_layout():
    """Name length at 0, UTF-16 name at 1, event count at 0x1F."""
    macro = M.Macro("ab", [M.key_event("a", True, 5)])
    record = macro.to_record()
    assert record[0] == 4  # two UTF-16 characters
    assert record[M.NAME_OFFSET : M.NAME_OFFSET + 4] == "ab".encode("utf-16-le")
    assert record[M.COUNT_OFFSET] == 1
    assert record[M.EVENTS_OFFSET : M.EVENTS_OFFSET + 5] == bytes(
        [0x81, K.KEYBOARD_USAGES["a"], 0x00, 0x00, 0x05]
    )


def test_checksum_excludes_the_name():
    """The stock encoder checksums before writing the name; we must match it.

    Verified end to end: a record built this way plays back on the mouse. A
    checksum that also covered the name would be a different, untested byte.
    """
    macro = M.Macro("some name", M.type_text("ab"))
    record = macro.to_record()
    end = M.EVENTS_OFFSET + M.EVENT_LEN * len(macro.events)
    without_name = bytearray(record[: end + 1])
    without_name[0 : M.EVENTS_OFFSET - 1] = bytes(M.EVENTS_OFFSET - 1)
    assert sum(without_name) & 0xFF == 0x55
    # And the naive whole-record sum does *not* validate, which is the point.
    assert sum(record[: end + 1]) & 0xFF != 0x55


def test_delays_are_big_endian():
    """Hardware stores the delay high byte first (hw+3), unlike everything else."""
    record = M.Macro("x", [M.key_event("a", True, 0x1234)]).to_record()
    event = record[M.EVENTS_OFFSET : M.EVENTS_OFFSET + 5]
    assert event[3] == 0x12 and event[4] == 0x34
    assert M.Event.decode(event).delay_ms == 0x1234


def test_event_kinds_encode_as_the_firmware_expects():
    cases = [
        (M.Event(M.KIND_MODIFIER, K.MOD_CTRL, True), 0x80),
        (M.Event(M.KIND_MODIFIER, K.MOD_CTRL, False), 0x40),
        (M.Event(M.KIND_KEY, 0x04, True), 0x81),
        (M.Event(M.KIND_KEY, 0x04, False), 0x41),
        (M.Event(M.KIND_MOUSE, 0x01, True), 0x84),
        (M.Event(M.KIND_MOUSE, 0x01, False), 0x44),
    ]
    for event, flag in cases:
        assert event.encode()[0] == flag, event.label()
    # A consumer event is a single tap: the flag is a bare 5, with no
    # press/release bit, and the usage is 16 bits.
    media = M.Event(M.KIND_CONSUMER, K.CONSUMER_USAGES["media-player"], True)
    raw = media.encode()
    assert raw[0] == M.KIND_CONSUMER
    assert raw[1] | (raw[2] << 8) == 0x183
    assert M.Event.decode(raw) == media


def test_unwritten_slot_decodes_as_empty():
    """A fresh slot reads back as 0xFF fill, not zeros."""
    assert M.Macro.from_record(bytes([0xFF] * 0x40)).is_empty


def test_record_refuses_to_overflow_its_slot():
    too_long = M.Macro("x", [M.key_event("a") for _ in range(M.MAX_EVENTS + 1)])
    try:
        too_long.to_record()
    except ValueError as exc:
        assert str(M.MAX_EVENTS) in str(exc)
    else:
        raise AssertionError("an oversized macro was encoded")


def test_reading_a_slot_only_fetches_the_events_it_declares():
    """A full sweep of the region would be hundreds of round-trips."""
    device = FakeDevice(flaky=False)
    M.write_macro(device, 2, sample())

    reads = []
    real_read = device.read

    def counting_read(addr, length):
        reads.append((addr, length))
        return real_read(addr, length)

    device.read = counting_read
    macro = M.read_macro(device, 2)
    device.read = real_read

    assert macro.events == sample().events
    assert sum(length for _addr, length in reads) < 0x80, reads


def test_jmm_round_trip():
    """Build a .jmm the way the Windows tool does, then parse it back."""
    data = bytearray(M.JMM_SIZE)
    struct.pack_into("<I", data, 0, M.JMM_MAGIC)
    name = "from windows".encode("utf-16-le")
    data[M.JMM_NAME_OFFSET : M.JMM_NAME_OFFSET + len(name)] = name
    events = [
        (0x41, 0x01, 30),   # 'A' down, 30 ms   (VK_A)
        (0x41, 0x00, 45),   # 'A' up
        (0x11, 0x01, 10),   # VK_CONTROL down
        (0x11, 0x00, 10),   # VK_CONTROL up
        (0xF0, 0x01, 15),   # left mouse down
        (0x00CD, 0x02, 20),  # play/pause, multimedia flag
    ]
    for index, (code, flags, delay) in enumerate(events):
        struct.pack_into(
            "<HBBI", data, M.JMM_EVENTS_OFFSET + 8 * index, code, flags, 0, delay
        )
    macro = M.from_jmm(bytes(data))
    assert macro.name == "from windows"
    assert [(e.kind, e.code, e.press, e.delay_ms) for e in macro.events] == [
        (M.KIND_KEY, K.KEYBOARD_USAGES["a"], True, 30),
        (M.KIND_KEY, K.KEYBOARD_USAGES["a"], False, 45),
        (M.KIND_MODIFIER, K.MOD_CTRL, True, 10),
        (M.KIND_MODIFIER, K.MOD_CTRL, False, 10),
        (M.KIND_MOUSE, 0x01, True, 15),
        (M.KIND_CONSUMER, 0x0CD, True, 20),
    ]
    # And it survives the trip onto the mouse and back.
    assert M.Macro.from_record(macro.to_record()).events == macro.events


def test_jmm_rejects_a_foreign_file():
    for data in (b"", b"not a macro at all" * 8):
        try:
            M.from_jmm(data)
        except M.JmmError:
            continue
        raise AssertionError("a non-macro file was accepted")


def test_repeat_is_carried_by_the_keymap_entry():
    """Verified on hardware: p2=9 played nine times, p2=1 played once."""
    binding = K.build("macro", slot=9, repeat=9)
    assert binding.entry == (K.KEY_TYPE_MACRO, 9, 9)
    assert K.identify(binding.entry) == ("macro", {"slot": 9, "repeat": 9})
    assert M.repeat_label(1) == "once"
    assert M.repeat_label(M.REPEAT_WHILE_HELD) == "repeat while held"


def test_assigning_writes_the_record_before_the_keymap():
    model, tab = build()
    order = []
    model.macro_flush_requested.connect(lambda blocks: order.append(("macro", blocks)))
    model.flush_requested.connect(lambda runs, image: order.append(("keymap", runs)))

    model.set_macro(4, sample(), repeat=3)
    model.flush()

    assert [kind for kind, _ in order] == ["macro", "keymap"]
    (_, blocks), (_, runs) = order
    assert blocks[0][0] == M.ADDR_MACROS + M.MACRO_STRIDE * 4
    assert (P.ADDR_KEYMAP + 16, 4) in runs
    assert model.profile().keymap[4] == (K.KEY_TYPE_MACRO, 4, 3)


def test_confirm_clears_the_right_region():
    model, tab = build()
    model.set_macro(4, sample())
    model.set_binding(2, K.build("play-pause"))
    assert model.is_dirty
    model.confirm_blocks([(M.ADDR_MACROS + M.MACRO_STRIDE * 4, b"")])
    assert 4 not in model._macro_dirty
    assert 2 in model._combo_dirty, "a macro ack cleared a combo record"


def test_macro_survives_a_write_to_the_device():
    device = FakeDevice(flaky=False)
    model, tab = build()
    model.set_macro(6, sample(), repeat=M.REPEAT_WHILE_HELD)
    for addr, data in [
        (M.ADDR_MACROS + M.MACRO_STRIDE * slot, model._macros[slot].to_record())
        for slot in model._macro_dirty
    ]:
        device.write(addr, data)
    assert M.read_macro(device, 6).events == sample().events


def test_library_persists_and_reloads():
    path = os.path.join(tempfile.mkdtemp(), "macros.json")
    library = MacroLibrary(path)
    library.add(sample())
    library.add(M.Macro("second", M.type_text("x")))
    again = MacroLibrary(path)
    assert [m.name for m in again.macros] == ["test", "second"]
    assert again.macros[0].events == sample().events


def test_library_refuses_to_overwrite_what_it_cannot_read():
    """A corrupt library must not be silently replaced with an empty one."""
    path = os.path.join(tempfile.mkdtemp(), "macros.json")
    with open(path, "w") as fh:
        fh.write("{ this is not json")
    library = MacroLibrary(path)
    assert library.error
    library.add(sample())
    with open(path) as fh:
        assert fh.read() == "{ this is not json"


def test_library_names_stay_unique():
    library = MacroLibrary(os.path.join(tempfile.mkdtemp(), "macros.json"))
    for _ in range(3):
        library.add(M.Macro("Reload"))
    assert [m.name for m in library.macros] == ["Reload", "Reload 2", "Reload 3"]


def test_tab_lists_and_edits():
    model, tab = build()
    tab.library.add(sample())
    tab._reload_list(0)
    assert tab.list.count() == 1
    assert tab.steps.topLevelItemCount() == 4
    assert tab.assign_button.isEnabled()

    tab.steps.setCurrentItem(tab.steps.topLevelItem(0))
    tab._on_delete_step()
    assert tab.steps.topLevelItemCount() == 3

    tab._on_clear()
    assert tab.steps.topLevelItemCount() == 0
    assert not tab.assign_button.isEnabled(), "an empty macro can be assigned"


def test_tab_assign_emits_for_the_chosen_button():
    model, tab = build()
    tab.library.add(sample())
    tab._reload_list(0)
    seen = []
    tab.assign_requested.connect(lambda slot, macro, repeat: seen.append((slot, repeat)))
    tab.button_choice.setCurrentIndex(5)  # "Top button, front" -> slot 9
    tab.repeat_choice.setCurrentIndex(tab.repeat_choice.findData(M.REPEAT_TOGGLE))
    tab._on_assign()
    assert seen == [(9, M.REPEAT_TOGGLE)]


def test_recorder_maps_keys_the_mouse_can_send():
    from PySide6.QtCore import Qt

    from m693gui.widgets.recorder import key_to_event

    assert key_to_event(Qt.Key_A, "a") == (M.KIND_KEY, K.KEYBOARD_USAGES["a"])
    assert key_to_event(Qt.Key_Control, "") == (M.KIND_MODIFIER, K.MOD_CTRL)
    assert key_to_event(Qt.Key_F5, "") == (M.KIND_KEY, K.KEYBOARD_USAGES["f5"])
    assert key_to_event(Qt.Key_Space, " ") == (M.KIND_KEY, K.KEYBOARD_USAGES["space"])
    # Nothing the firmware has no usage for should be invented.
    assert key_to_event(Qt.Key_Massyo, "") is None


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
