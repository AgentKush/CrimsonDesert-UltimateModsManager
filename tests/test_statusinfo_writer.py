"""statusinfo ``stat_level_data`` writer (DIRECT SPEED .cdmod stat mods).

The always-active stat presets (DIRECT MOVEMENT SPEED, DIRECT ATTACK SPEED,
...) set ``stat_level_data[0..15]`` on ``statusinfo.pabgb`` rate records.
These tests prove the writer applies them byte-exact, decodes the field the
way the game does, and refuses anything it cannot represent exactly.

Ground truth (real 1.13 statusinfo, committed fixture): the four rate stats
-- MoveSpeedRate (1000011), AttackSpeedRate (1000010), CriticalRate
(1000007), DHIT (1000004) -- carry ``stat_level_data`` as 16 uint64s at tail
offset 68, in 24-bit fixed point. The other 71 stats have an 84-byte tail
with no such array.

Why the offset gets a test of its own
-------------------------------------
It has been wrong twice. 80 shipped first and overran the array into an
8-byte constant at tail+204; the tests could not catch it because their
containment bound came from the same +80. 76 was the natural correction and
is also wrong -- it partitions the 212-byte tail just as exactly, so no
boundary argument separates it from 68.

What separates them is the data. At 68 every one of the 64 vanilla elements
decodes to an exact whole number on a monotonic ramp; the ramps below are
transcribed from the fixture and would not survive a shift of the offset by
one slot in either direction. ``test_trailer_and_terminator_are_never_touched``
pins the specific defect 80 had.
"""
from __future__ import annotations

import json
import sqlite3
import struct
import zipfile

import pytest

from cdumm.engine.format3_handler import Format3Intent, validate_intents
from cdumm.engine.statusinfo_writer import (
    build_statusinfo_changes,
    decode_element,
)
from cdumm.semantic.parser import parse_pabgh_index
from tests.fixture_loaders import has_vanilla113, load_vanilla113

FIXTURE = "statusinfo.pabgb"
DHIT = 1000004
CRITICAL = 1000007
ATTACK_SPEED = 1000010
MOVE_SPEED = 1000011
RATE_KEYS = {MOVE_SPEED, ATTACK_SPEED, CRITICAL, DHIT}

SLD_OFFSET = 68          # inside the tail
SLD_COUNT = 16
TAIL_LEN = 212

#: Vanilla per-level ramps, decoded. Transcribed from the committed 1.13
#: fixture -- these are what pin the offset (see the module docstring).
VANILLA_RAMPS = {
    DHIT: [0, 2000, 4000, 6000, 8000, 10000, 12000, 14000,
           16000, 18000, 20000, 21000, 22000, 23000, 24000, 25000],
    CRITICAL: [0, 29000000, 55000000, 78000000, 99000000, 118000000,
               136000000, 152000000, 167000000, 181000000, 194000000,
               206000000, 218000000, 229000000, 240000000, 250000000],
    ATTACK_SPEED: [0, 29000000, 55000000, 78000000, 99000000, 118000000,
                   136000000, 152000000, 167000000, 181000000, 194000000,
                   206000000, 218000000, 229000000, 240000000, 250000000],
    MOVE_SPEED: [0, 20000000, 40000000, 60000000, 80000000, 100000000,
                 120000000, 139000000, 157000000, 174000000, 190000000,
                 204000000, 217000000, 229000000, 240000000, 250000000],
}

pytestmark = pytest.mark.skipif(
    not has_vanilla113(FIXTURE),
    reason="1.13 statusinfo fixture not present")


def _intent(key: int, idx: int, val: object,
            entry: str = "MoveSpeedRate") -> Format3Intent:
    return Format3Intent(entry=entry, key=key,
                         field=f"stat_level_data[{idx}]", op="set", new=val)


def _apply(body: bytes, changes: list[dict]) -> bytes:
    out = bytearray(body)
    for c in changes:
        off = c["offset"]
        orig = bytes.fromhex(c["original"])
        assert out[off:off + len(orig)] == orig, "change 'original' must anchor"
        out[off:off + len(orig)] = bytes.fromhex(c["patched"])
    return bytes(out)


def _record(body: bytes, header: bytes, key: int) -> tuple[int, bytes]:
    _, offsets = parse_pabgh_index(header, "statusinfo")
    starts = sorted(offsets.values())
    o = offsets[key]
    i = starts.index(o)
    e = starts[i + 1] if i + 1 < len(starts) else len(body)
    return o, body[o:e]


def _tail_start(rec: bytes) -> int:
    return 8 + struct.unpack_from("<I", rec, 4)[0]


def _raw_elements(rec: bytes) -> list[int]:
    """The 16 elements as stored, full 64 bits -- no truncation."""
    blk = _tail_start(rec) + SLD_OFFSET
    return [struct.unpack_from("<Q", rec, blk + i * 8)[0]
            for i in range(SLD_COUNT)]


def _values(rec: bytes) -> list[float]:
    """The 16 elements as the game reads them."""
    return [decode_element(r) for r in _raw_elements(rec)]


# ------------------------------------------------------------ the layout

def test_vanilla_decodes_to_exact_whole_ramps():
    """The offset+encoding pin. Every rate record's 16 elements decode to
    exact whole numbers on a monotonic ramp -- 64 values, no rounding. A
    wrong offset or a wrong scale breaks this immediately."""
    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")
    for key, expected in VANILLA_RAMPS.items():
        _, rec = _record(body, header, key)
        vals = _values(rec)
        assert all(v == int(v) for v in vals), (key, vals)
        assert [int(v) for v in vals] == expected, key
        assert vals == sorted(vals), f"{key} ramp must be non-decreasing"


def test_only_the_four_rate_records_carry_the_array():
    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")
    _, offsets = parse_pabgh_index(header, "statusinfo")
    starts = sorted(offsets.values())
    rate = set()
    for key, o in offsets.items():
        i = starts.index(o)
        e = starts[i + 1] if i + 1 < len(starts) else len(body)
        rec = body[o:e]
        if len(rec) - _tail_start(rec) == TAIL_LEN:
            rate.add(key)
    assert rate == RATE_KEYS


# ------------------------------------------------------------ the writes

def test_direct_speed_applies_and_is_length_preserving():
    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")

    intents = [_intent(MOVE_SPEED, i, 2_500_000_000) for i in range(16)]
    changes, dropped = build_statusinfo_changes(body, header, intents)
    assert not dropped, dropped
    assert len(changes) == 1

    modified = _apply(body, changes)

    # Length-preserving: the table stays the same size, so the companion
    # .pabgh offsets remain valid without any rebuild.
    assert len(modified) == len(body)

    # Every changed byte falls inside MoveSpeedRate's 128-byte block.
    start, rec = _record(body, header, MOVE_SPEED)
    blk0 = start + _tail_start(rec) + SLD_OFFSET
    diff = [j for j in range(len(body)) if body[j] != modified[j]]
    assert diff, "the mod must change something"
    assert all(blk0 <= j < blk0 + 128 for j in diff), (
        "changes must be confined to the stat_level_data block")

    _, new_rec = _record(modified, header, MOVE_SPEED)
    assert _values(new_rec) == [2_500_000_000] * 16


def test_written_bytes_are_the_fixed_point_encoding():
    """Not just 'reads back the same' -- the bytes on disk are the value
    shifted left 24, which is what the game reads."""
    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")
    changes, dropped = build_statusinfo_changes(
        body, header, [_intent(ATTACK_SPEED, 1, 58_000_000,
                               entry="AttackSpeedRate")])
    assert not dropped, dropped
    modified = _apply(body, changes)
    _, rec = _record(modified, header, ATTACK_SPEED)
    assert _raw_elements(rec)[1] == 58_000_000 << 24


def test_the_2x_preset_really_is_two_times_vanilla():
    """The semantics the mod's name claims. 2xAtkSpd sets level 1 to
    58,000,000 against a vanilla 29,000,000 -- exactly double once decoded.
    Under a wrong scale the two numbers would not relate."""
    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")
    _, vanilla_rec = _record(body, header, ATTACK_SPEED)
    assert _values(vanilla_rec)[1] == 29_000_000

    changes, _ = build_statusinfo_changes(
        body, header, [_intent(ATTACK_SPEED, 1, 58_000_000,
                               entry="AttackSpeedRate")])
    _, rec = _record(_apply(body, changes), header, ATTACK_SPEED)
    assert _values(rec)[1] == 2 * _values(vanilla_rec)[1]


def test_trailer_and_terminator_are_never_touched():
    """The defect offset 80 had. The array ends at tail+196; a zero
    terminator slot follows, then an 8-byte constant that is byte-identical
    on all four rate records. Writing all 16 elements must leave both
    intact."""
    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")

    # the constant really is shared across the rate records
    trailers = set()
    for key in RATE_KEYS:
        _, rec = _record(body, header, key)
        t = _tail_start(rec)
        trailers.add(bytes(rec[t + 204:t + 212]))
        assert rec[t + 196:t + 204] == b"\x00" * 8, key
    assert len(trailers) == 1, "the trailer is structural, not per-stat data"
    assert trailers != {b"\x00" * 8}

    intents = [_intent(MOVE_SPEED, i, 2_500_000_000) for i in range(16)]
    changes, _ = build_statusinfo_changes(body, header, intents)
    _, rec = _record(_apply(body, changes), header, MOVE_SPEED)
    t = _tail_start(rec)
    assert rec[t + 196:t + 204] == b"\x00" * 8
    assert bytes(rec[t + 204:t + 212]) == trailers.pop()


def test_every_other_record_is_byte_identical():
    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")
    intents = [_intent(MOVE_SPEED, i, 2_500_000_000) for i in range(16)]
    changes, _dropped = build_statusinfo_changes(body, header, intents)
    modified = _apply(body, changes)

    _, offsets = parse_pabgh_index(header, "statusinfo")
    starts = sorted(offsets.values())
    for key, o in offsets.items():
        if key == MOVE_SPEED:
            continue
        i = starts.index(o)
        e = starts[i + 1] if i + 1 < len(starts) else len(body)
        assert body[o:e] == modified[o:e], (
            f"record {key} must be untouched")


def test_large_value_round_trips_whole():
    """A value far above 32 bits must survive intact. The previous writer
    stored the number raw and a 32-bit read gave 10,000,000,000 back as
    1,410,065,408 with nothing reported as dropped."""
    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")
    changes, dropped = build_statusinfo_changes(
        body, header, [_intent(MOVE_SPEED, 0, 10_000_000_000)])
    assert not dropped, dropped
    _, rec = _record(_apply(body, changes), header, MOVE_SPEED)
    assert _values(rec)[0] == 10_000_000_000


# ----------------------------------------------------------- the refusals

def test_refuses_non_rate_record():
    """A regular stat (84-byte tail) has no stat_level_data; writing into its
    tail would corrupt it, so the writer must refuse, not write."""
    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")
    _, offsets = parse_pabgh_index(header, "statusinfo")
    regular = next(k for k in offsets if k not in RATE_KEYS)

    changes, dropped = build_statusinfo_changes(
        body, header, [_intent(regular, 0, 2_500_000_000)])
    assert changes == []
    assert len(dropped) == 1
    assert "not a rate stat" in dropped[0][1]


def test_refuses_out_of_range_index():
    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")
    changes, dropped = build_statusinfo_changes(
        body, header, [_intent(MOVE_SPEED, 16, 1)])
    assert changes == []
    assert "out of range" in dropped[0][1]


def test_refuses_missing_key():
    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")
    changes, dropped = build_statusinfo_changes(
        body, header, [_intent(99_999_999, 0, 1)])
    assert changes == []
    assert "no record" in dropped[0][1]


@pytest.mark.parametrize("bad", [
    2 ** 40,            # overflows the field once shifted
    -1,                 # the encoding is unsigned; no evidence for negatives
    1.5,                # not a whole number
    2500000000.0,       # a float, even a whole-valued one
    True,               # bool is an int subclass -- must not become 1
    "2500000000",
    None,
])
def test_refuses_values_it_cannot_encode_exactly(bad):
    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")
    changes, dropped = build_statusinfo_changes(
        body, header, [_intent(MOVE_SPEED, 0, bad)])
    assert changes == [], f"{bad!r} must not be written"
    assert len(dropped) == 1
    assert "stat_level_data element" in dropped[0][1]


def test_largest_representable_value_is_accepted():
    """The refusal must sit exactly at the field boundary, not below it."""
    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")
    biggest = (2 ** 64 - 1) >> 24
    changes, dropped = build_statusinfo_changes(
        body, header, [_intent(MOVE_SPEED, 0, biggest)])
    assert not dropped, dropped
    assert len(changes) == 1


def test_refuses_unsupported_op():
    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")
    i = Format3Intent(entry="MoveSpeedRate", key=MOVE_SPEED,
                      field="stat_level_data[0]", op="scale", new=2)
    changes, dropped = build_statusinfo_changes(body, header, [i])
    assert changes == []
    assert "not supported" in dropped[0][1]


def test_setting_to_vanilla_value_is_a_noop():
    """Writing an element back to its current value must produce no change --
    the writer only emits a change when bytes actually differ."""
    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")
    _, rec = _record(body, header, MOVE_SPEED)
    current = _values(rec)
    intents = [_intent(MOVE_SPEED, i, int(current[i])) for i in range(16)]
    changes, dropped = build_statusinfo_changes(body, header, intents)
    assert changes == []
    assert not dropped


# ------------------------------------------------------------- the wiring

def test_validate_intents_accepts_stat_level_data():
    """The early-accept must classify a stat_level_data set as supported, so
    it reaches the writer instead of being skipped as schema-less
    (statusinfo has no CDUMM PABGB schema)."""
    intents = [Format3Intent(entry="MoveSpeedRate", key=MOVE_SPEED,
                             field=f"stat_level_data[{i}]", op="set",
                             new=2_500_000_000) for i in range(16)]
    v = validate_intents("statusinfo.pabgb", intents)
    assert len(v.supported) == 16, v
    assert not v.skipped, v


def test_end_to_end_cdmod_to_byte_change(tmp_path):
    """The full user path: a DIRECT SPEED ``.cdmod`` (built here with the
    exact shape of the real Nexus package) -> cdmod_to_format3 -> validate ->
    whole-table dispatch -> statusinfo writer -> a single byte-exact change
    that sets all 16 MoveSpeedRate levels to 2,500,000,000,
    length-preserving.
    """
    # .cdmod import is a fork-only feature (#288); skip cleanly where it is
    # absent so the core statusinfo writer stays portable to upstream.
    ch = pytest.importorskip("cdumm.engine.cdmod_handler")
    cdmod_to_format3 = ch.cdmod_to_format3
    from cdumm.engine.format3_apply import expand_format3_into_aggregated

    body = load_vanilla113("statusinfo.pabgb")
    header = load_vanilla113("statusinfo.pabgh")

    manifest = {
        "format": "crimson-mod-package", "format_version": 1,
        "name": "DIRECT MOVEMENT SPEED - 10X", "version": "1.13.01",
        "components": [{"type": "semantic-patch",
                        "path": "patches/semantic.json"}],
    }
    semantic = {
        "schema": 1,
        "targets": [{
            "file": "statusinfo.pabgb",
            "operations": [
                {"op": "set", "conversion": "conservative",
                 "path": f"stat_level_data[{i}]",
                 "selector": {"key": MOVE_SPEED,
                              "string_key": "MoveSpeedRate"},
                 "value": 2_500_000_000}
                for i in range(16)
            ],
        }],
    }
    cdmod = tmp_path / "direct_speed.cdmod"
    with zipfile.ZipFile(cdmod, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("patches/semantic.json", json.dumps(semantic))

    doc = cdmod_to_format3(cdmod)
    jp = tmp_path / "direct_speed.json"
    jp.write_text(json.dumps(doc), encoding="utf-8")

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE mods (id INTEGER PRIMARY KEY, name TEXT, enabled "
        "INTEGER, json_source TEXT, priority INTEGER, mod_type TEXT)")
    conn.execute(
        "CREATE TABLE mod_config (mod_id INTEGER, custom_values TEXT)")
    conn.execute(
        "INSERT INTO mods VALUES (1, 'DirectSpeed', 1, ?, 5, 'paz')",
        (str(jp),))
    conn.commit()
    db = type("DB", (), {"connection": conn})()

    aggregated: dict = {}
    signatures: dict = {}
    expand_format3_into_aggregated(
        aggregated, signatures, db,
        vanilla_extractor=lambda gf: (body, header)
        if gf == "statusinfo.pabgb" else None)

    changes = aggregated.get("statusinfo.pabgb") or []
    assert len(changes) == 1, aggregated
    modified = _apply(body, changes)
    assert len(modified) == len(body)
    _, rec = _record(modified, header, MOVE_SPEED)
    assert _values(rec) == [2_500_000_000] * 16
