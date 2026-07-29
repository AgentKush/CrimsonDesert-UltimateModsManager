"""interactioninfo pivot range: the locator, and why it is aligned.

"Fast Pickup - Increase Range" sets ``interaction_pivot_list[0].raw_a``
and ``.raw_b`` on five records and applied zero bytes:
``_interactionPivotList`` is field #26 of ``InteractionInfo`` with type
``None`` in the PABGB schema, so the generic walker has no descriptor for
it and ``parse_records`` returns no records for this table at all.

These tests re-run the derivation in CI against the committed vanilla
1.15 table rather than trusting the offsets:

* the mod's values are the raw bits of f32 5.0 and 3.0;
* the 16-zero frame (``_interactionUpperHeight`` 0.0 +
  ``_targetGotoOffset`` (0,0,0)) locates the pair on all five records;
* scanning on the run's 4-byte grid is what makes that locator stable --
  an unaligned scan is unique on vanilla but goes ambiguous the moment
  the mod is applied, because 3.0 beside 3.0 reads as 2.0 one byte early;
* ambiguous records are refused, not guessed at.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from cdumm.engine.interactioninfo_writer import (
    _MAX_RANGE,
    _MIN_RANGE,
    _record_bounds,
    build_interactioninfo_changes,
    locate_pivot_pair,
)

# Loaded directly rather than through fixture_loaders so this file stays
# independent of any other in-flight branch that touches that module.
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vanilla115"

_needs = pytest.mark.skipif(
    not (_FIXTURES / "interactioninfo.pabgb.zlib").exists(),
    reason="vanilla115 interactioninfo fixture absent")

#: The five records the mod targets, and the vanilla range pair each one
#: holds. Game data, taken from the live table.
VANILLA = {
    1000004: ("Gimmick_PickUp", 2.5, 2.5),
    1000058: ("SmallAnimal_Skin", 1.5, 2.0),
    1000098: ("Animal_Skin", 1.7000000476837158, 2.0),
    10028: ("Gimmick_Collect", 2.5, 3.0),
    1000035: ("Insect_Catch", 2.5, 2.5999999046325684),
}

RAW_5_0 = 1084227584          # 0x40A00000
RAW_3_0 = 1077936128          # 0x40400000


class _Intent:
    def __init__(self, key, field="interaction_pivot_list[0].raw_a",
                 new=RAW_3_0, op="set", entry=""):
        self.key, self.field, self.new = key, field, new
        self.op, self.entry = op, entry


def _load(name: str) -> bytes:
    return zlib.decompress((_FIXTURES / (name + ".zlib")).read_bytes())


def _table():
    return _load("interactioninfo.pabgb"), _load("interactioninfo.pabgh")


def test_the_mods_values_are_float_bit_patterns():
    """What identified the field in the first place."""
    assert struct.unpack("<f", struct.pack("<I", RAW_5_0))[0] == 5.0
    assert struct.unpack("<f", struct.pack("<I", RAW_3_0))[0] == 3.0


@_needs
def test_locator_finds_the_pair_on_every_targeted_record():
    body, header = _table()
    bounds = _record_bounds(header, body)
    for key, (name, want_a, want_b) in VANILLA.items():
        lo, hi = bounds[key]
        pos = locate_pivot_pair(body, lo, hi)
        assert pos is not None, name
        a = struct.unpack_from("<f", body, pos)[0]
        b = struct.unpack_from("<f", body, pos + 4)[0]
        assert (a, b) == (want_a, want_b), (name, a, b)


@_needs
def test_the_frame_before_the_pair_is_sixteen_zero_bytes():
    """_interactionUpperHeight (4) + _targetGotoOffset (12), per the
    InteractionPivotOverrideData field order."""
    body, header = _table()
    bounds = _record_bounds(header, body)
    for key, (name, _a, _b) in VANILLA.items():
        lo, hi = bounds[key]
        pos = locate_pivot_pair(body, lo, hi)
        assert body[pos - 16:pos] == b"\x00" * 16, name


@_needs
def test_locator_survives_its_own_write():
    """The alignment rule earns its place here. With an unaligned scan
    this is where it breaks: 3.0 is 00 00 40 40, so once the pair holds
    3.0 twice, a read one byte early yields 00 00 00 40 = 2.0, which
    passes the range check and makes the record look ambiguous. The mod
    would apply once and be refused on every re-apply."""
    body, header = _table()
    intents = [_Intent(k, f, RAW_3_0)
               for k in VANILLA
               for f in ("interaction_pivot_list[0].raw_a",
                         "interaction_pivot_list[0].raw_b")]
    changes, dropped = build_interactioninfo_changes(body, header, intents)
    assert dropped == []
    patched = bytearray(body)
    for c in changes:
        patched[c["offset"]:c["offset"] + 4] = bytes.fromhex(c["patched"])

    bounds = _record_bounds(header, bytes(patched))
    for key, (name, _a, _b) in VANILLA.items():
        lo, hi = bounds[key]
        pos = locate_pivot_pair(bytes(patched), lo, hi)
        assert pos is not None, f"{name}: lost the pair after writing"
        assert struct.unpack_from("<I", patched, pos)[0] == RAW_3_0, name
        assert struct.unpack_from("<I", patched, pos + 4)[0] == RAW_3_0, name


@_needs
def test_writer_applies_the_real_mod_values():
    body, header = _table()
    intents = [
        _Intent(1000004, "interaction_pivot_list[0].raw_a", RAW_5_0),
        _Intent(1000004, "interaction_pivot_list[0].raw_b", RAW_5_0),
        _Intent(1000058, "interaction_pivot_list[0].raw_a", RAW_3_0),
        _Intent(1000058, "interaction_pivot_list[0].raw_b", RAW_3_0),
    ]
    changes, dropped = build_interactioninfo_changes(body, header, intents)
    assert dropped == []
    assert len(changes) == 4
    patched = bytearray(body)
    for c in changes:
        assert len(bytes.fromhex(c["patched"])) == 4
        patched[c["offset"]:c["offset"] + 4] = bytes.fromhex(c["patched"])
    assert len(patched) == len(body)
    assert sum(1 for a, b in zip(body, patched) if a != b) <= 4 * 4


@_needs
def test_writer_no_ops_when_the_range_already_matches():
    """Gimmick_Collect's raw_b is already 3.0 in vanilla."""
    body, header = _table()
    changes, dropped = build_interactioninfo_changes(
        body, header,
        [_Intent(10028, "interaction_pivot_list[0].raw_b", RAW_3_0)])
    assert (changes, dropped) == ([], [])


@_needs
@pytest.mark.parametrize("intent,fragment", [
    (_Intent(1000004, field="interaction_distance"), "not a supported"),
    (_Intent(1000004, op="scale"), "only 'set'"),
    (_Intent(1000004, new=-1), "not a u32"),
    (_Intent(1000004, new=0), "outside the plausible interaction range"),
    (_Intent(99999999), "no interactioninfo record"),
    (_Intent(1000004, entry="Gimmick_Wrong"), "but the intent names"),
])
def test_writer_refuses_rather_than_guesses(intent, fragment):
    body, header = _table()
    changes, dropped = build_interactioninfo_changes(body, header, [intent])
    assert changes == []
    assert len(dropped) == 1 and fragment in dropped[0][1]


@pytest.mark.parametrize("field,supported", [
    ("interaction_pivot_list[0].raw_a", True),
    ("interaction_pivot_list[0].raw_b", True),
    ("interaction_pivot_list[1].raw_a", False),
    ("interaction_pivot_list[7].raw_b", False),
])
def test_only_element_zero_is_accepted(field, supported):
    """The locator frames ONE pair per record, so there is nothing for a
    higher index to resolve to. #317 review: the regex used to match any
    index. Accepting [1] would either write it to element 0, or -- once
    apply routes only the writer's own SUPPORTED_FIELDS -- drop it into a
    generic walker that has no descriptor for _interactionPivotList and
    applies zero bytes silently. Rejecting at validation is the loudest
    of the three, so pin which spellings are accepted."""
    from cdumm.engine.format3_handler import Format3Intent, validate_intents
    from cdumm.engine.interactioninfo_writer import SUPPORTED_FIELDS

    i = Format3Intent(entry="Gimmick_PickUp", key=1000004, field=field,
                      op="set", new=RAW_3_0)
    v = validate_intents("interactioninfo.pabgb", [i])
    assert bool(v.supported) is supported, (field, v.skipped)
    assert bool(v.skipped) is not supported, (field, v.skipped)
    # and the apply dispatch's accept-set must agree with validation
    assert (field in SUPPORTED_FIELDS) is supported, field


@_needs
def test_ambiguous_records_are_refused_not_guessed():
    """The frame only holds when upper height and goto offset are zero.
    Where it doesn't, the scan would find some other pair of floats, so
    the writer must return None rather than pick one."""
    body, header = _table()
    bounds = _record_bounds(header, body)
    located = sum(1 for lo, hi in bounds.values()
                  if locate_pivot_pair(body, lo, hi) is not None)
    # Measured on the live 1.15 table: a clear majority resolve, and the
    # rest refuse. Pinned so a change to the rule shows up as a number.
    assert (located, len(bounds)) == (295, 393)


#: A record the locator refuses (98 of 393 do). Used to exercise the
#: builder's refusal branch, not just locate_pivot_pair in isolation.
REFUSED_KEY = 1000021           # Gimmick_PickUp_Climb


@pytest.mark.parametrize("lo,hi,why", [
    (0, 8, "record shorter than the envelope"),
    (0, 400, "name_len nonsense"),
    (0, 20, "name runs past the record end"),
])
def test_locator_guards_return_none_rather_than_reading_past(lo, hi, why):
    """The three early-outs in locate_pivot_pair. A malformed or
    truncated record must refuse, not index into whatever follows."""
    body = bytearray(4096)
    if why == "name_len nonsense":
        struct.pack_into("<I", body, 4, 9999)        # > 250
    elif why == "name runs past the record end":
        struct.pack_into("<I", body, 4, 200)         # 8 + 200 > hi
    assert locate_pivot_pair(bytes(body), lo, hi) is None, why


@_needs
def test_builder_refuses_records_the_locator_cannot_frame():
    """test_ambiguous_records_are_refused_not_guessed covers the locator;
    this covers the builder acting on that refusal -- an intent aimed at
    one of the 98 unframeable records must be dropped with a reason, not
    written at a guessed offset."""
    body, header = _table()
    changes, dropped = build_interactioninfo_changes(
        body, header, [_Intent(REFUSED_KEY, entry="")])
    assert changes == []
    assert len(dropped) == 1
    assert "could not be located unambiguously" in dropped[0][1]


def _is_multiple_of(value: float, step: float = 0.05) -> bool:
    return abs(round(value / step) - value / step) < 1e-4


@_needs
def test_located_pairs_are_designer_authored_not_arbitrary_floats():
    """Independent evidence that the locator lands on a real range field.

    ``locate_pivot_pair`` filters on ``[0.01, 100.0]`` and says nothing
    about quantisation, so "every located value is a multiple of 0.05"
    is not implied by the rule -- it is a property of the data. Measured
    on the live 1.15 table:

    * located pairs: **295 / 295** are multiples of 0.05;
    * every OTHER aligned float pair in the same records that passes the
      same ``[0.01, 100.0]`` filter: only **54 / 89** (60.7%).

    A locator landing on arbitrary bytes would look like the control
    group. Asserting the range instead would be circular -- the rule
    guarantees it -- which is what this test used to do (#317 review).
    """
    body, header = _table()
    bounds = _record_bounds(header, body)
    located = 0
    for lo, hi in bounds.values():
        pos = locate_pivot_pair(body, lo, hi)
        if pos is None:
            continue
        located += 1
        for off in (0, 4):
            v = struct.unpack_from("<f", body, pos + off)[0]
            assert _is_multiple_of(v), (pos + off, v)
    assert located == 295


@_needs
def test_quantisation_is_not_implied_by_the_range_filter():
    """The control arm of the test above: without it, 100% quantisation
    could just mean every float in the region is quantised."""
    body, header = _table()
    bounds = _record_bounds(header, body)
    total = quantised = 0
    for lo, hi in bounds.values():
        pos = locate_pivot_pair(body, lo, hi)
        for off in range(lo, hi - 8, 4):
            if off == pos:
                continue
            a = struct.unpack_from("<f", body, off)[0]
            b = struct.unpack_from("<f", body, off + 4)[0]
            if _MIN_RANGE <= a <= _MAX_RANGE and _MIN_RANGE <= b <= _MAX_RANGE:
                total += 1
                quantised += _is_multiple_of(a) and _is_multiple_of(b)
    assert (quantised, total) == (54, 89)
