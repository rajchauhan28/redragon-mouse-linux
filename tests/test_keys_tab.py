#!/usr/bin/env python3
"""Phase 4 checks: the button catalogue, the combo region, and the Keys tab."""

import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from m693 import keys as K  # noqa: E402
from m693 import profile as P  # noqa: E402
from m693 import transfer  # noqa: E402
from m693gui.backend import FakeDevice  # noqa: E402
from m693gui.model import ProfileModel  # noqa: E402
from m693gui.tabs.keys import ROWS, KeysTab  # noqa: E402
from m693gui.widgets.mouseview import CALLOUTS  # noqa: E402

_app = QApplication.instance() or QApplication([])
_fake = FakeDevice(flaky=False)
STOCK = _fake.read(0x00, P.PROFILE_LEN)
STOCK_COMBO = _fake.read(K.ADDR_COMBO, K.COMBO_REGION_LEN)


def build():
    model = ProfileModel()
    tab = KeysTab(model)
    model.load(STOCK)
    model.load_combo(STOCK_COMBO)
    return model, tab


def test_stock_keymap_decodes_to_named_functions():
    """Every slot the mouse ships with must be something we can name.

    The stock image is the ground truth for the type table; an unrecognised
    entry means a type byte was decoded wrong.
    """
    profile = P.Profile(STOCK)
    expected = [
        "left", "right", "middle", "back", "forward",
        "dpi-loop", "fire", "rgb-toggle", "polling-switch",
        "dpi-up", "dpi-down",
    ]
    for slot, want in enumerate(expected):
        got, _params = K.identify(profile.keymap[slot])
        assert got == want, f"slot {slot}: expected {want}, decoded {got}"
    for slot in range(11, P.KEY_SLOTS):
        assert K.identify(profile.keymap[slot])[0] == "disable"


def test_dpi_action_numbering():
    """p1=1 is the loop, not DPI+. Both conversions in OemDrv.exe agree."""
    assert K.DPI_ACTIONS[1] == "dpi-loop"
    assert K.DPI_ACTIONS[2] == "dpi-up"
    assert K.DPI_ACTIONS[3] == "dpi-down"


def test_every_catalogue_entry_can_be_built():
    for fn in K.FUNCTIONS:
        binding = K.build(fn.id) if not fn.param else K.build(
            fn.id, interval=10, shots=3, stage=1, slot=0, modifiers=K.MOD_CTRL,
            keys=["c"],
        )
        assert 0 <= binding.ktype <= 0xFF


def test_bindings_round_trip_through_identify():
    cases = [
        ("left", {}), ("middle", {}), ("dpi-up", {}), ("dpi-loop", {}),
        ("double-click", {}), ("triple-click", {}), ("rgb-toggle", {}),
        ("polling-switch", {}), ("profile-switch", {}), ("disable", {}),
        ("fire", {"interval": 25, "shots": 7}),
        ("dpi-lock", {"stage": 3}),
        ("macro", {"slot": 4}),
        ("play-pause", {}), ("mute", {}), ("web-refresh", {}), ("calculator", {}),
        ("key-combo", {"modifiers": K.MOD_CTRL | K.MOD_ALT, "keys": ["delete"]}),
    ]
    for function_id, params in cases:
        binding = K.build(function_id, **params)
        got_id, got_params = K.identify(binding.entry, binding.combo or b"")
        assert got_id == function_id, f"{function_id} came back as {got_id}"
        for key, value in params.items():
            if key == "keys":
                value = [K.KEYBOARD_USAGES[k] for k in value]
            assert got_params[key] == value, (function_id, key)


def test_combo_records_checksum_like_every_other_group():
    for record in (
        K.encode_combo(0, [], K.CONSUMER_USAGES["mute"]),
        K.encode_combo(K.MOD_CTRL, [K.KEYBOARD_USAGES["c"]]),
        K.encode_combo(
            K.MOD_CTRL | K.MOD_SHIFT | K.MOD_ALT, [K.KEYBOARD_USAGES["f5"]]
        ),
    ):
        assert K.combo_is_valid(record), record.hex(" ")
        assert sum(record[: K.combo_length(record)]) & 0xFF == 0x55


def test_combo_rejects_what_the_slot_cannot_hold():
    """A record is one 32-byte slot: count byte, 3 bytes per event, checksum."""
    assert K.COMBO_MAX_EVENTS == 10
    try:
        K.encode_combo(0xF, [4, 5, 6])  # 4 modifiers + 3 keys = 14 events
    except ValueError:
        pass
    else:
        raise AssertionError("oversized combo was accepted")
    # Three modifiers plus one key is 8 events and does fit.
    assert K.combo_is_valid(K.encode_combo(0x7, [K.KEYBOARD_USAGES["f5"]]))


def test_consumer_usages_are_the_ones_the_firmware_maps():
    """Spot-checked against the code->usage switch at 0x410340."""
    assert K.CONSUMER_USAGES["play-pause"] == 0x0CD
    assert K.CONSUMER_USAGES["next"] == 0x0B5
    assert K.CONSUMER_USAGES["previous"] == 0x0B6
    assert K.CONSUMER_USAGES["mute"] == 0x0E2
    assert K.CONSUMER_USAGES["volume-up"] == 0x0E9
    assert K.CONSUMER_USAGES["web-favorites"] == 0x22A


def test_rows_cover_the_slots_the_stock_tool_offers():
    """Cfg.ini's K1_1..K7_1, whose fourth field is the 1-based slot."""
    assert [slot for slot, _name in ROWS] == [0, 1, 2, 4, 3, 9, 10]
    assert len(CALLOUTS) == len(ROWS)


def test_view_reflects_the_device():
    model, tab = build()
    for row in tab.rows:
        function_id, _ = K.identify(
            model.profile().keymap[row.slot], model.combo_record(row.slot)
        )
        assert row.combo.currentData() == function_id
    assert tab.debounce.value() == model.profile().debounce_ms


def test_sync_from_model_does_not_write_back():
    model, tab = build()
    model.load(STOCK)
    assert not model.is_dirty, "populating the view dirtied the model"


def test_assigning_a_combo_queues_the_record_before_the_keymap():
    """The keymap entry is meaningless until its record is in place."""
    model, tab = build()
    order = []
    model.combo_flush_requested.connect(lambda blocks: order.append(("combo", blocks)))
    model.flush_requested.connect(lambda runs, image: order.append(("keymap", runs)))

    model.set_binding(2, K.build("play-pause"))
    model.flush()

    assert [kind for kind, _ in order] == ["combo", "keymap"]
    (_, blocks), (_, runs) = order
    assert blocks[0][0] == K.ADDR_COMBO + K.COMBO_STRIDE * 2
    assert K.combo_is_valid(blocks[0][1])
    assert (P.ADDR_KEYMAP + 8, 4) in runs


def test_non_combo_binding_leaves_the_region_alone():
    model, tab = build()
    before = model.combo_image
    model.set_binding(0, K.build("dpi-up"))
    assert model.combo_image == before
    assert model.profile().keymap[0] == (K.KEY_TYPE_DPI, 2, 0)


def test_export_import_round_trip():
    model, tab = build()
    model.set_binding(9, K.build("key-combo", modifiers=K.MOD_SUPER, keys=["d"]))
    document = transfer.to_json(model.image, model.combo_image)
    image, combo = transfer.from_json(document)
    assert image == model.image
    assert combo == model.combo_image
    assert "Win + D" in document["settings"]["buttons"][9]["function"]


def test_import_refuses_a_corrupt_image():
    document = transfer.to_json(STOCK, STOCK_COMBO)
    broken = bytearray(base64.b64decode(document["raw"]["profile"]))
    broken[P.ADDR_DEBOUNCE] ^= 0xFF  # breaks that group's checksum
    document["raw"]["profile"] = base64.b64encode(bytes(broken)).decode()
    try:
        transfer.from_json(document)
    except ValueError as exc:
        assert "corrupt" in str(exc)
    else:
        raise AssertionError("a corrupt image was imported")


def test_import_marks_everything_dirty():
    model, tab = build()
    model.confirm(P.merge_runs(model._dirty))
    edited = bytearray(model.image)
    document = transfer.to_json(bytes(edited), model.combo_image)
    image, combo = transfer.from_json(document)
    model.apply_image(image, combo)
    assert model.is_dirty
    assert len(P.merge_runs(model._dirty)) > 1


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
